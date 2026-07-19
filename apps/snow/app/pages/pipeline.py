# -*- coding: utf-8 -*-
"""Page locale de lancement des flux neige et diagnostic du rollover.

Les trois collectes sont relançables séparément ou en séquence. Le rollover
est volontairement invoqué SANS ``--execute`` : depuis l'UI il reste donc un
diagnostic dry-run, sans possibilité de mutation de l'archive hot/cold.
"""

import os
import tempfile

import streamlit as st

from apps.snow import snow_config as SC
from apps.snow.app.runtime import IS_LOCAL
from core.services import cooldown
from core.ui.pipeline import execute, render_execution_results

ROOT_DIR = os.path.dirname(os.path.dirname(SC.SNOW_DIR))
RESULTS_KEY = "snow_pipeline_results"
COOLDOWN_S = 5
COOLDOWN_PATH = os.path.join(tempfile.gettempdir(), "weather_snow_pipeline_ui.cooldown")

FETCH_ENTRIES = [
    ("Ensemble membres + mean/spread", "apps/snow/pipeline/fetch_ensemble.py", 300),
    ("Maille fine AROME HD + ICON-D2", "apps/snow/pipeline/fetch_hd.py", 180),
    ("Observations Alpes du Nord", "apps/snow/pipeline/fetch_observations.py", 120),
]
ROLLOVER_ENTRY = [
    ("Diagnostic archivage hot/cold", "apps/snow/pipeline/rollover.py", 120),
]


def _launch(entries):
    allowed, _ = cooldown.can(COOLDOWN_PATH, COOLDOWN_S)
    if not allowed:
        return
    st.session_state[RESULTS_KEY] = execute(entries, base_dir=ROOT_DIR)
    cooldown.record(COOLDOWN_PATH)


def _is_rollover_result(results):
    return bool(results) and results[0][0] == ROLLOVER_ENTRY[0][0]


def page_run(runs, sig):
    st.title("🚀 Lancer le pipeline neige")
    if not IS_LOCAL:
        st.warning("☁️ Exécution cloud détectée : cette page est sans effet. "
                   "Les flux neige sont lancés uniquement par GitHub Actions.")
        return

    st.success("💻 Exécution locale détectée — les boutons lancent les scripts "
               "dans des sous-processus Python locaux.")
    st.caption("Chaque flux conserve son parquet propre. Le bouton groupé les "
               "enchaîne sans fusionner leurs données.")
    allowed, remaining = cooldown.can(COOLDOWN_PATH, COOLDOWN_S)
    if not allowed:
        st.caption(f"⏳ Nouveau lancement disponible dans {remaining:.0f} s.")

    col1, col2, col3, col4 = st.columns(4)
    cards = [
        (col1, "① Ensemble", "`fetch_ensemble.py` : membres, moyenne et spread "
         "ECMWF/AIFS/GEFS vers `db_megeve.parquet`. ~10–30 s.",
         "🌐 Lancer Ensemble", [FETCH_ENTRIES[0]], "secondary"),
        (col2, "② Maille fine", "`fetch_hd.py` : AROME France HD et ICON-D2 "
         "vers `db_megeve_hd.parquet`. ~5–15 s.",
         "🔬 Lancer HD", [FETCH_ENTRIES[1]], "secondary"),
        (col3, "③ Observations", "`fetch_observations.py` : paquet Météo-France "
         "du département 74 vers `db_obs_alpes.parquet`. ~5–15 s.",
         "🛰️ Lancer observations", [FETCH_ENTRIES[2]], "secondary"),
        (col4, "④ Tous les flux", "Enchaîne les trois collectes ci-contre. "
         "Un échec n'empêche pas le diagnostic des flux suivants.",
         "▶️ Tout lancer", FETCH_ENTRIES, "primary"),
    ]
    for pos, (container, title, caption, button, entries, kind) in enumerate(cards):
        with container:
            st.subheader(title)
            st.caption(caption)
            if st.button(button, type=kind, disabled=not allowed,
                         key=f"snow_pipeline_fetch_{pos}"):
                _launch(entries)

    # Le log reste hors des colonnes : une sortie de pipeline doit conserver
    # toute la largeur, notamment pour les tableaux de fraîcheur par modèle.
    results = st.session_state.get(RESULTS_KEY)
    if not _is_rollover_result(results):
        render_execution_results(results)

    st.markdown("---")
    st.subheader("🧊 Diagnostic de l'archivage hot/cold")
    st.caption("`rollover.py` est appelé en **dry-run uniquement** depuis cette "
               "page. Aucun bouton ni argument `--execute` n'est exposé dans "
               "l'interface ; l'archivage réel reste réservé au workflow dédié.")
    if st.button("🔎 Simuler rollover.py (aucune écriture)", type="secondary",
                 disabled=not allowed, key="snow_pipeline_rollover_dry_run"):
        _launch(ROLLOVER_ENTRY)

    results = st.session_state.get(RESULTS_KEY)
    if _is_rollover_result(results):
        render_execution_results(results)
