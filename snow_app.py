# -*- coding: utf-8 -*-
"""
Dashboard neige — Megève (Mont d'Arbois)
=========================================
POINT D'ENTRÉE Streamlit du dashboard neige : configuration de la page,
feuille de style, navigation et panneau de contexte UNIQUEMENT — tout le reste
vit dans apps/snow/app/.

Monorepo : ce point d'entrée reste à la racine (même convention que
meteo_app.py — Streamlit Cloud et les lanceurs pointent sur la racine). Il
importe le dashboard sous son namespace propre `apps.snow.app`, afin qu'il
puisse cohabiter dans un même processus avec le package canicule `app`. La
racine expose aussi `core` et `apps.snow.snow_config`.

Navigation en BARRE DU HAUT (st.navigation position="top"), sidebar réduite au
contexte (fraîcheur, rafraîchir, sources, version) — mêmes règles que le
canicule, mêmes briques `core/ui/nav.py`. Le contrat des pages ne change pas :
`page_xxx(runs, sig)`, arguments figés sur chaque st.Page.
"""

import os
import sys

# Racine du repo (→ core et namespace apps, insérée EXPLICITEMENT : sous le
# harnais AppTest le dossier du script n'est pas garanti sur sys.path).
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

import streamlit as st

from apps.snow.app.data.db import db_signature, list_runs
from apps.snow.app.data.runsets import latest_refresh_status
from apps.snow.app.data.store import vider_cache_magasin
from apps.snow.app.domains import DOMAIN_PAGES
from apps.snow.app.pages.convergence import page_convergence
from apps.snow.app.pages.diagnostic import page_diagnostic
from apps.snow.app.pages.explore import page_explore
from apps.snow.app.pages.meteofrance import page_meteofrance
from apps.snow.app.pages.pipeline import page_run
from apps.snow.app.ui.theme import stylesheet
from core.ui.components import badge_html
from core.ui.nav import build_navigation, context_panel
from core.version import SHARED_VERSION

SNOW_APP_VERSION = SHARED_VERSION

st.set_page_config(page_title="Dashboard Neige — Megève",
                   layout="wide")
st.markdown(stylesheet(), unsafe_allow_html=True)

# « Lancer le pipeline » est visible PARTOUT : la page adapte elle-même ses
# capacités à l'environnement — sous-processus en local, déclenchement du job
# CI derrière mot de passe en ligne (cf. apps/snow/app/pages/pipeline.py).
ANALYSE_PAGES = [
    ("Explorer un run", page_explore),
    ("Maille fine Météo-France", page_meteofrance),
    ("Convergence des runs", page_convergence),
]
DONNEES_PAGES = [
    ("Contrôle des runs", page_diagnostic),
    ("Lancer le pipeline", page_run),
]
CORE_PAGES = ANALYSE_PAGES + DONNEES_PAGES

# Sources et modèles : bloc de pied de sidebar, purement descriptif.
_SOURCES = ("<b>Météo-France PNT</b><br>"
            "AROME-PI (H+1–H+6)<br>"
            "AROME-IFS (H+1–H+45)<br>"
            "PE-AROME · 25 membres (H+48)<br>"
            "PE-ARPEGE · 35 membres (H+96)<br><br>"
            "<b>Open-Meteo</b><br>"
            "AROME France (source MF) · ICON-D2 (48 h)<br>"
            "ECMWF ENS · AIFS · GEFS (J+15)<br><br>"
            "<b>Observations</b><br>"
            "API Météo-France · stations 74<br><br>"
            "Actualisation automatique toutes les 2 h<br>"
            f"Version {SNOW_APP_VERSION}")


def _vider_les_caches():
    """Les trois caches, dans cet ordre : le magasin externe (métadonnées du
    release, mémoïsées avec un TTL taillé pour le rythme automatique), les
    calculs (cache_data) et la base elle-même (cache_resource, que
    cache_data.clear() ne touche PAS). En sauter un laisse la page sur les
    données précédentes jusqu'au redémarrage du process."""
    vider_cache_magasin()
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()


def _panneau_contexte(runs, sig):
    """Sidebar = état des données (fraîcheur, complétude), jamais navigation."""
    lignes, badges = [], []
    if not runs.empty:
        lignes.append(("Dernière prévision", runs.iloc[0]["label"]))
        _, complete, missing = latest_refresh_status(runs, sig)
        badges.append(badge_html("Tous les modèles attendus", niveau="ok")
                      if complete else
                      badge_html(f"Manque : {', '.join(missing)}", niveau="warn"))
    context_panel(titre="État des données", lignes=lignes, badges_html=badges,
                  bouton="Rafraîchir", on_click=_vider_les_caches,
                  pied=_SOURCES)


def main():
    sig = db_signature()
    runs = list_runs(sig)

    page = build_navigation(
        {"Suivi": DOMAIN_PAGES,
         "Analyse": ANALYSE_PAGES,
         "Données": DONNEES_PAGES},
        args=(runs, sig))

    _panneau_contexte(runs, sig)
    page.run()


if __name__ == "__main__":
    main()
