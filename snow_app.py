# -*- coding: utf-8 -*-
"""
Dashboard neige — Megève (Mont d'Arbois)
=========================================
POINT D'ENTRÉE Streamlit du dashboard neige : configuration de la page,
sidebar et routage UNIQUEMENT — tout le reste vit dans apps/snow/app/.

Monorepo : ce point d'entrée reste à la racine (même convention que
meteo_app.py — Streamlit Cloud et les lanceurs pointent sur la racine). Il
importe le dashboard sous son namespace propre `apps.snow.app`, afin qu'il
puisse cohabiter dans un même processus avec le package canicule `app`. La
racine expose aussi `core` et `apps.snow.snow_config`.
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
from apps.snow.app.ui.theme import GLOBAL_CSS
from core.version import SHARED_VERSION

SNOW_APP_VERSION = SHARED_VERSION

st.set_page_config(page_title="Dashboard Neige — Megève",
                   layout="wide")
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# « Lancer le pipeline » est visible PARTOUT : la page adapte elle-même ses
# capacités à l'environnement — sous-processus en local, déclenchement du job
# CI derrière mot de passe en ligne (cf. apps/snow/app/pages/pipeline.py).
CORE_PAGES = [
    ("Explorer un run", page_explore),
    ("Maille fine Météo-France", page_meteofrance),
    ("Convergence des runs", page_convergence),
    ("Contrôle des runs", page_diagnostic),
    ("Lancer le pipeline", page_run),
]


def main():
    sig = db_signature()
    runs = list_runs(sig)

    renderers = dict(DOMAIN_PAGES + CORE_PAGES)

    st.sidebar.title("Navigation")
    page = st.sidebar.radio("Aller à", list(renderers))
    st.sidebar.markdown("---")
    if not runs.empty:
        st.sidebar.caption(f"Dernière : {runs.iloc[0]['label']}")
        refreshed_at, complete, missing = latest_refresh_status(runs, sig)
        if complete:
            st.sidebar.caption("Tous les modèles attendus à ce run présents")
        else:
            st.sidebar.caption(f"Données partielles — manque : {', '.join(missing)}")
    if st.sidebar.button("Rafraîchir"):
        # Les trois caches, dans cet ordre : le magasin externe (métadonnées du
        # release, mémoïsées avec un TTL taillé pour le rythme automatique), les
        # calculs (cache_data) et la base elle-même (cache_resource, que
        # cache_data.clear() ne touche PAS). En sauter un laisse la page sur les
        # données précédentes jusqu'au redémarrage du process.
        vider_cache_magasin()
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()
    st.sidebar.markdown("---")
    st.sidebar.markdown("<small><b>Modèles et sources</b><br>"
                        "<b>Météo-France PNT</b><br>"
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
                        f"Version {SNOW_APP_VERSION}</small>",
                        unsafe_allow_html=True)

    renderers[page](runs, sig)


if __name__ == "__main__":
    main()
