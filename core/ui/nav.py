# -*- coding: utf-8 -*-
"""Navigation et panneau de contexte, partagés par les deux dashboards.

Deux briques indépendantes de toute app :

* `build_navigation` — registre SECTIONNÉ de pages passé à `st.navigation` en
  barre du HAUT. La sidebar cesse ainsi d'être un menu pour devenir un panneau
  de contexte (fraîcheur, sources, version), et les pages gagnent toute la
  largeur.
* `context_panel` — ce panneau, rendu avec les classes `wx-ctx`/`wx-foot`.

Contrat de page INCHANGÉ : les pages restent des `page_xxx(runs, sig)`. C'est
le point d'entrée qui calcule `sig`/`runs` une fois, puis fige les arguments
(`functools.partial`) — `st.Page` n'accepte qu'un callable sans argument.
"""

import functools
import re
import unicodedata

import streamlit as st


def page_slug(label):
    """Chemin d'URL déduit du libellé d'une page : minuscules, sans accent, mots
    reliés par des tirets. Déterministe et stable — c'est l'identité d'une page
    (Streamlit dérive de ce chemin le hash de la page, et le harnais de rendu
    s'en sert pour naviguer). Un renommage de libellé change donc l'URL : c'est
    voulu, mais ça se recapture côté harnais."""
    plat = unicodedata.normalize("NFKD", str(label))
    plat = "".join(c for c in plat if not unicodedata.combining(c))
    plat = re.sub(r"[^a-zA-Z0-9]+", "-", plat).strip("-").lower()
    return plat or "page"


def build_navigation(sections, *, args=(), position="top", defaut=None):
    """Construit la navigation à partir d'un registre sectionné et retourne la
    page courante (l'appelant fait `.run()`).

    sections : dict {libellé de section: [(libellé de page, fonction), …]} —
               l'ordre des sections et des pages est celui du dict.
    args     : arguments communs figés sur chaque page (ici `(runs, sig)`),
               ce qui préserve le contrat historique `page_xxx(runs, sig)`.
    defaut   : libellé de la page ouverte à l'arrivée ; à défaut, la première.

    Les libellés doivent être uniques toutes sections confondues : deux pages de
    même `url_path` partageraient leur identité (Streamlit dérive le hash de ce
    seul chemin) et se recouvriraient silencieusement."""
    vus = set()
    pages = {}
    rang = 0
    for section, entrees in sections.items():
        construites = []
        for libelle, fonction in entrees:
            slug = page_slug(libelle)
            assert slug not in vus, f"Deux pages partagent le chemin « {slug} »."
            vus.add(slug)
            est_defaut = (libelle == defaut) if defaut else (rang == 0)
            rang += 1
            construites.append(st.Page(
                functools.partial(fonction, *args),
                title=libelle, url_path=slug, default=est_defaut))
        if construites:
            pages[section] = construites
    return st.navigation(pages, position=position)


def context_panel(*, titre=None, lignes=(), badges_html=(), pied=None,
                  bouton=None, on_click=None, into=None):
    """Panneau de contexte de la sidebar : ce qui décrit l'ÉTAT des données
    (fraîcheur, complétude, sources, version), jamais la navigation.

    lignes      : liste de (intitulé, valeur) — une ligne par fait.
    badges_html : badges déjà rendus (cf. core/ui/components.badge_html).
    bouton      : libellé d'un bouton d'action (Rafraîchir) ; `on_click` est
                  appelé s'il est pressé.
    pied        : HTML de bas de panneau (sources, version)."""
    zone = into or st.sidebar
    if titre:
        zone.markdown(f"### {titre}")
    if lignes:
        rows = "".join(
            f'<div class="wx-ctx-row"><span>{k}</span><strong>{v}</strong></div>'
            for k, v in lignes)
        zone.markdown(f'<div class="wx-ctx">{rows}</div>', unsafe_allow_html=True)
    if badges_html:
        zone.markdown("".join(badges_html), unsafe_allow_html=True)
    if bouton and zone.button(bouton, width="stretch") and on_click is not None:
        on_click()
    if pied:
        zone.markdown(f'<div class="wx-foot">{pied}</div>', unsafe_allow_html=True)
