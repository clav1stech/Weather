# -*- coding: utf-8 -*-
"""Composants d'interface partagés par les deux dashboards.

Chaque composant produit du HTML qui ne porte QUE des classes `wx-*` définies
dans core/ui/design.py — aucun style inline : la mise en forme reste modifiable
d'un seul endroit, et une page ne peut pas dériver de la charte sans le vouloir.
Seule exception assumée, une variable CSS posée en `style` quand la valeur est
une DONNÉE et non un choix de charte (couleur d'un jour de calendrier, teinte
d'un bandeau) — la règle de mise en forme, elle, reste dans la feuille.

Config-agnostique comme tout core/ : ni seuil, ni libellé métier, ni couleur de
modèle ici. Les pages passent des valeurs déjà calculées et déjà formatées.

Conventions communes :
  * `into` — conteneur Streamlit de destination (colonne, expander…) ;
    None = le flux principal.
  * les fonctions `*_html` renvoient la chaîne sans rien afficher, pour les
    appelants qui composent eux-mêmes leur balisage.
"""

import html as _html

import streamlit as st

from core.ui.tokens import RISK_KEYS, tokens

# Niveaux sémantiques acceptés par les bandeaux et badges → clé de palette.
# « info » vaut accent : un statut neutre porte la couleur d'action, pas une
# alerte. Un niveau inconnu retombe sur accent plutôt que de lever.
_NIVEAUX = {"info": "accent", "ok": "ok", "warn": "warn", "danger": "danger",
            "neutral": "neutral", "warm": "warm", "cold": "cold"}


def _esc(txt):
    """Échappe le texte destiné au HTML. Les composants reçoivent des libellés
    construits par les pages (dates, noms de modèles) : sans échappement, un
    caractère comme `<` casserait silencieusement le balisage."""
    return _html.escape(str(txt), quote=True)


def _rendu(html, into=None):
    (into or st).markdown(html, unsafe_allow_html=True)


def couleur_niveau(niveau, dark=None):
    """Couleur d'un niveau sémantique ("ok", "warn"…) ou d'un niveau de risque
    entier 0-3 — le point d'entrée unique des composants pour teinter."""
    pal = tokens(dark)
    if isinstance(niveau, (int, float)) and not isinstance(niveau, bool):
        idx = max(0, min(len(RISK_KEYS) - 1, int(niveau)))
        return pal[RISK_KEYS[idx]]
    return pal[_NIVEAUX.get(niveau, "accent")]


# --------------------------------------------------------------------------- #
#  En-tête de page
# --------------------------------------------------------------------------- #
def page_header_html(titre, *, eyebrow=None, sub=None):
    haut = f'<div class="wx-eyebrow">{_esc(eyebrow)}</div>' if eyebrow else ""
    bas = f'<p class="wx-sub">{_esc(sub)}</p>' if sub else ""
    return f'<div class="wx-header">{haut}<h1>{_esc(titre)}</h1>{bas}</div>'


def page_header(titre, *, eyebrow=None, sub=None, into=None):
    """En-tête normalisé d'une page : sur-titre (section de navigation), titre,
    sous-titre d'une phrase. Remplace le couple st.title + st.caption, dont
    l'espacement variait d'une page à l'autre."""
    _rendu(page_header_html(titre, eyebrow=eyebrow, sub=sub), into)


# --------------------------------------------------------------------------- #
#  Bandeau de statut
# --------------------------------------------------------------------------- #
def status_banner_html(titre, *, sub=None, niveau="info"):
    couleur = couleur_niveau(niveau)
    bas = f'<div class="wx-banner-sub">{_esc(sub)}</div>' if sub else ""
    # La teinte est une DONNÉE (niveau du statut) : elle passe par une variable
    # CSS, la règle de dessin restant dans la feuille de style.
    return (f'<div class="wx-banner" style="--wx-level: {couleur};">'
            f'<span class="wx-banner-dot"></span>'
            f'<div class="wx-banner-txt">'
            f'<div class="wx-banner-title">{_esc(titre)}</div>{bas}</div></div>')


def status_banner(titre, *, sub=None, niveau="info", into=None):
    """Bandeau de tête d'une page métier : le message principal (état du risque,
    fraîcheur des données) en une ligne, son détail en dessous. `niveau` :
    "info"/"ok"/"warn"/"danger" ou un niveau de risque 0-3."""
    _rendu(status_banner_html(titre, sub=sub, niveau=niveau), into)


# --------------------------------------------------------------------------- #
#  Carte KPI
# --------------------------------------------------------------------------- #
def kpi_html(label, valeur, *, unite=None, delta=None, delta_niveau="neutral",
             sub=None, note=None, note_niveau=None, aide=None):
    """Carte KPI en HTML. `delta` est déjà formaté par l'appelant (signe, unité
    comprise) : le composant ne sait rien de la grandeur affichée. `note` = une
    seconde ligne de détail, teintable (horodatage périmé, réserve)."""
    attrs = f' title="{_esc(aide)}"' if aide else ""
    unite_html = f'<span class="wx-kpi-unit">{_esc(unite)}</span>' if unite else ""
    delta_html = ""
    if delta:
        couleur = couleur_niveau(delta_niveau)
        delta_html = (f'<span class="wx-kpi-delta" style="color: {couleur};">'
                      f'{_esc(delta)}</span>')
    sub_html = f'<div class="wx-kpi-sub">{_esc(sub)}</div>' if sub else ""
    note_html = ""
    if note:
        teinte = (f' style="color: {couleur_niveau(note_niveau)};"'
                  if note_niveau else "")
        note_html = f'<div class="wx-kpi-note"{teinte}>{_esc(note)}</div>'
    return (f'<div class="wx-kpi"{attrs}>'
            f'<div class="wx-kpi-label">{_esc(label)}</div>'
            f'<div class="wx-kpi-value">{_esc(valeur)}{unite_html}{delta_html}</div>'
            f'{sub_html}{note_html}</div>')


def kpi_card(label, valeur, *, unite=None, delta=None, delta_niveau="neutral",
             sub=None, note=None, note_niveau=None, aide=None, into=None):
    """Carte KPI — mécanique UNIQUE des deux apps (elle remplace à la fois les
    cartes HTML maison, les st.metric et les st.container(border=True) qui
    coexistaient). `delta` : texte déjà formaté (anomalie, révision…), teinté
    par `delta_niveau` ; `sub`/`note` : lignes de détail visibles ; `aide` :
    infobulle."""
    _rendu(kpi_html(label, valeur, unite=unite, delta=delta,
                    delta_niveau=delta_niveau, sub=sub, note=note,
                    note_niveau=note_niveau, aide=aide), into)


def kpi_row(cartes, into=None):
    """Rangée de cartes KPI de largeur égale. `cartes` : liste de dicts passés
    tels quels à kpi_card (clé `label` et `valeur` obligatoires)."""
    if not cartes:
        return
    cols = (into or st).columns(len(cartes))
    for col, carte in zip(cols, cartes):
        kpi_card(into=col, **carte)


# --------------------------------------------------------------------------- #
#  Badge
# --------------------------------------------------------------------------- #
def badge_html(texte, *, niveau="info", point=True):
    couleur = couleur_niveau(niveau)
    pt = '<span class="wx-badge-dot"></span>' if point else ""
    return (f'<span class="wx-badge" style="color: {couleur};">'
            f'{pt}{_esc(texte)}</span>')


def badge(texte, *, niveau="info", point=True, into=None):
    """Pastille d'état (données partielles, horizon réduit, source unique…)."""
    _rendu(badge_html(texte, niveau=niveau, point=point), into)


def badges(items, into=None):
    """Ligne de plusieurs badges. `items` : liste de (texte, niveau)."""
    if not items:
        return
    _rendu("".join(badge_html(t, niveau=n) for t, n in items), into)


# --------------------------------------------------------------------------- #
#  Calendrier (grille de jours)
# --------------------------------------------------------------------------- #
def risk_calendar_html(jours):
    """Grille de cartes-jour. `jours` : liste de dicts
        date    libellé du jour (déjà formaté)
        valeur  chiffre mis en avant (str) — optionnel
        sub     ligne de détail — optionnel
        flag    glyphe de réserve (fiabilité…) — optionnel, jamais une couleur
        niveau  niveau de risque 0-3 ou nom sémantique → teinte du liseré
        aide    infobulle (le détail complet, comme le survol d'une heatmap)
    La COULEUR ne porte qu'un seul signal (le niveau de risque) : tout autre
    qualificatif passe par `flag` ou l'infobulle, jamais par la teinte."""
    cases = []
    for j in jours:
        couleur = couleur_niveau(j.get("niveau", 0))
        aide = f' title="{_esc(j["aide"])}"' if j.get("aide") else ""
        val = (f'<div class="wx-cal-main">{_esc(j["valeur"])}</div>'
               if j.get("valeur") else "")
        sub = f'<div class="wx-cal-sub">{_esc(j["sub"])}</div>' if j.get("sub") else ""
        flag = f'<div class="wx-cal-flag">{_esc(j["flag"])}</div>' if j.get("flag") else ""
        cases.append(f'<div class="wx-cal-day" style="--wx-day-color: {couleur};"{aide}>'
                     f'<div class="wx-cal-date">{_esc(j.get("date", ""))}</div>'
                     f'{val}{sub}{flag}</div>')
    return f'<div class="wx-cal">{"".join(cases)}</div>'


def risk_calendar(jours, into=None):
    """Calendrier du risque en grille de cartes HTML — remplace une heatmap
    Plotly d'une seule ligne : lisible sur écran étroit (les cases se replient),
    et chaque case peut porter du texte sans que les libellés se touchent."""
    if not jours:
        return
    _rendu(risk_calendar_html(jours), into)


# --------------------------------------------------------------------------- #
#  État vide
# --------------------------------------------------------------------------- #
def empty_state(message, into=None):
    """Encart discret « rien à afficher » — pour un flux annexe absent, cas
    NORMAL du projet : ce n'est ni un avertissement ni une erreur, et ça ne doit
    donc pas prendre la couleur d'une alerte."""
    _rendu(f'<div class="wx-empty">{_esc(message)}</div>', into)


# --------------------------------------------------------------------------- #
#  Tableaux
# --------------------------------------------------------------------------- #
def table_style(df, *, formats=None, na_rep=None, gradient=None,
                cmap="RdYlBu_r", gradient_axis=None, alerte=None,
                precision=1, dark=None):
    """Styler pandas habillé aux couleurs du thème, pour que les tableaux ne
    détonnent pas avec le reste (en-têtes, bordures, aplats d'alerte).

    formats       : dict colonne → format, passé à Styler.format.
    gradient      : colonnes à colorer en dégradé (sous-ensemble numérique).
    gradient_axis : axe du dégradé (None = sur tout le bloc, cf. pandas).
    alerte        : prédicat ligne → bool ; les lignes retenues prennent
                    l'aplat `danger_soft` du thème. C'est le SEUL endroit où se
                    définit une couleur d'alerte de tableau — une page ne code
                    jamais une teinte en dur.

    Retourne un Styler : `st.dataframe` l'accepte directement, et la donnée
    sous-jacente reste intacte (les harnais la comparent telle quelle)."""
    pal = tokens(dark)
    sty = df.style.format(formats, precision=precision, na_rep=na_rep) if formats \
        else df.style.format(precision=precision, na_rep=na_rep)
    if gradient:
        cols = [c for c in gradient if c in df.columns]
        if cols:
            sty = sty.background_gradient(cmap=cmap, subset=cols, axis=gradient_axis)
            # Le dégradé impose ses propres fonds : l'encre est fixée pour
            # rester lisible sur toute la rampe, quel que soit le thème.
            sty = sty.set_properties(subset=cols, color="#1a2330")
    if alerte is not None:
        marque = f"background-color:{pal['danger_soft']};color:{pal['danger']}"
        sty = sty.apply(
            lambda row: [marque if alerte(row) else "" for _ in row], axis=1)
    return sty.set_table_styles([
        {"selector": "th", "props": [("background-color", pal["surface_alt"]),
                                     ("color", pal["ink_soft"]),
                                     ("font-weight", "600"),
                                     ("border-color", pal["border"])]},
        {"selector": "td", "props": [("border-color", pal["border"])]},
    ])
