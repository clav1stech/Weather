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
from apps.snow.app.domains import DOMAIN_PAGES
from apps.snow.app.pages.convergence import page_convergence
from apps.snow.app.pages.diagnostic import page_diagnostic
from apps.snow.app.pages.explore import page_explore
from apps.snow.app.pages.pipeline import page_run
from apps.snow.app.runtime import IS_LOCAL
from apps.snow.app.ui.theme import GLOBAL_CSS

SNOW_APP_VERSION = "0.4.3"

st.set_page_config(page_title="Dashboard Neige — Megève",
                   page_icon="🏔️", layout="wide")
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

CORE_PAGES = [
    ("Explorer un run", page_explore),
    ("Convergence des runs", page_convergence),
    ("Contrôle des runs", page_diagnostic),
]
LOCAL_PAGES = [
    ("Lancer le pipeline", page_run),
]


def main():
    sig = db_signature()
    runs = list_runs(sig)

    renderers = dict(DOMAIN_PAGES + CORE_PAGES + (LOCAL_PAGES if IS_LOCAL else []))

    st.sidebar.title("🏔️ Navigation")
    page = st.sidebar.radio("Aller à", list(renderers))
    st.sidebar.markdown("---")
    if not runs.empty:
        st.sidebar.caption(f"Dernière : {runs.iloc[0]['label']}")
        refreshed_at, complete, missing = latest_refresh_status(runs, sig)
        if complete:
            st.sidebar.caption("✅ Tous les modèles attendus à ce run présents")
        else:
            st.sidebar.caption(f"⚠️ Données partielles — manque : {', '.join(missing)}")
    if st.sidebar.button("🔄 Rafraîchir"):
        st.cache_data.clear()
        st.rerun()
    st.sidebar.markdown("---")
    st.sidebar.markdown("<small><b>Modèles et sources</b><br>"
                        "🇫🇷 <b>Météo-France PNT</b><br>"
                        "AROME-PI (H+1–H+6)<br>"
                        "PE-AROME · 25 membres (H+48)<br>"
                        "PE-ARPEGE · 35 membres (H+96)<br><br>"
                        "🌐 <b>Open-Meteo</b><br>"
                        "AROME France HD · ICON-D2 (48 h)<br>"
                        "ECMWF ENS · AIFS · GEFS (J+15)<br><br>"
                        "🛰️ <b>Observations</b><br>"
                        "API Météo-France · stations 74<br><br>"
                        "🕐 Actualisation automatique toutes les 2 h<br>"
                        "AROME-IFS 45 h : accès en attente<br>"
                        f"Version {SNOW_APP_VERSION}</small>",
                        unsafe_allow_html=True)

    renderers[page](runs, sig)


if __name__ == "__main__":
    main()
