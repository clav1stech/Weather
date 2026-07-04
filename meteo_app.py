# -*- coding: utf-8 -*-
"""
Dashboard météo — Prévisions d'ensemble (Paris)
================================================
POINT D'ENTRÉE Streamlit uniquement : configuration de la page, sidebar et
routage. Tout le reste vit dans le package app/ — carte complète dans
docs/CODEMAP.md :
  app/data/    accès à la base parquet & sélections de runs
  app/stats/   statistiques d'ensemble (tolérantes NaN), climatologie
  app/ui/      thème, composants, graphiques génériques
  app/domains/ un sous-package par phénomène métier (canicule…)
  app/pages/   pages transverses (vue d'ensemble, exploration, convergence…)

Config-driven : modèles, variables, climatologie et seuils vivent dans config.py.
La navigation = pages des domaines (app/domains/__init__.py, registre) puis
pages transverses — ajouter un domaine ne modifie pas ce fichier.
"""

import streamlit as st

from app.runtime import IS_LOCAL
from app.data.db import db_signature, list_runs
from app.data.runsets import latest_refresh_status
from app.domains import DOMAIN_PAGES
from app.pages.convergence import page_convergence
from app.pages.diagnostic import page_diagnostic
from app.pages.explore import page_explore
from app.pages.overview import page_overview
from app.pages.pipeline import page_run
from app.ui.theme import GLOBAL_CSS

APP_VERSION = "2.4.3"

st.set_page_config(page_title="Dashboard Météo — Ensembles Paris",
                   page_icon="🌡️", layout="wide")
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# Pages transverses, après les domaines. « Lancer le pipeline » n'apparaît
# qu'en local (IS_LOCAL) : lancer un sous-processus n'a pas de sens sur le cloud.
CORE_PAGES = [
    ("Vue d'ensemble", page_overview),
    ("Explorer un run", page_explore),
    ("Convergence des runs", page_convergence),
    ("Contrôle des runs", page_diagnostic),
]
LOCAL_PAGES = [
    ("Lancer le pipeline", page_run),
]


# --------------------------------------------------------------------------- #
#  Routage
# --------------------------------------------------------------------------- #
def main():
    sig = db_signature()
    runs = list_runs(sig)

    renderers = dict(DOMAIN_PAGES + CORE_PAGES + (LOCAL_PAGES if IS_LOCAL else []))

    st.sidebar.title("🌦️ Navigation")
    page = st.sidebar.radio("Aller à", list(renderers))
    st.sidebar.markdown("---")
    st.sidebar.metric("Prévisions archivées", len(runs),
                      help="Nombre de runs (calculs) disponibles dans la base")
    if not runs.empty:
        st.sidebar.caption(f"Dernière : {runs.iloc[0]['label']}")
        refreshed_at, complete, missing = latest_refresh_status(runs, sig)
        if refreshed_at is not None:
            st.sidebar.caption(f"🕐 Rafraîchi le {refreshed_at.strftime('%d/%m/%Y à %Hh%M')}")
        if complete:
            st.sidebar.caption("✅ Tous les modèles attendus à ce run présents")
        else:
            st.sidebar.caption(f"⚠️ Données partielles — manque : {', '.join(missing)}")
    if st.sidebar.button("🔄 Rafraîchir"):
        st.cache_data.clear()
        st.rerun()
    st.sidebar.markdown("---")
    st.sidebar.markdown("<small>🕐 **Mise à jour automatique**<br>"
                        "4×/jour via Open-Meteo — runs 0Z/6Z/12Z/18Z "
                        "(GEM : 0Z/12Z uniquement)</small>",
                        unsafe_allow_html=True)
    st.sidebar.markdown("<small>Données : ECMWF · NOAA · ECCC</small>",
                        unsafe_allow_html=True)
    st.sidebar.markdown(f"<small>Version {APP_VERSION}</small>", unsafe_allow_html=True)

    renderers[page](runs, sig)


if __name__ == "__main__":
    main()
