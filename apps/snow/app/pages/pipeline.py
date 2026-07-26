# -*- coding: utf-8 -*-
"""Page locale de lancement des flux neige et diagnostic du rollover.

Toutes les collectes actives sont relançables séparément ou en séquence. Le rollover
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
MF_FORECAST_ENTRIES = [
    ("PE-AROME 25 membres", "apps/snow/pipeline/fetch_pe_arome.py", 360),
    ("PE-ARPEGE 35 membres", "apps/snow/pipeline/fetch_pe_arpege.py", 600),
    ("AROME-PI H+1 à H+6", "apps/snow/pipeline/fetch_arome_pi.py", 240),
    ("AROME-IFS H+1 à H+45", "apps/snow/pipeline/fetch_arome_ifs.py", 360),
]
ACTIVE_FETCH_ENTRIES = [
    FETCH_ENTRIES[0],
    MF_FORECAST_ENTRIES[0],
    MF_FORECAST_ENTRIES[1],
    MF_FORECAST_ENTRIES[2],
    MF_FORECAST_ENTRIES[3],
    FETCH_ENTRIES[1],
    FETCH_ENTRIES[2],
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
    st.caption("Chaque flux conserve son stockage dédié. Le lancement complet "
               "enchaîne les sept collectes actives sans fusion opaque entre modèles.")
    allowed, remaining = cooldown.can(COOLDOWN_PATH, COOLDOWN_S)
    if not allowed:
        st.caption(f"⏳ Nouveau lancement disponible dans {remaining:.0f} s.")

    sections = [
        ("🏔️ Très courte échéance et maille fine", [
            ("AROME-PI", "Météo-France PNT · horaire H+1 à H+6 · "
             "pluie/neige directe aux sites village et sommet.", 3),
            ("AROME-IFS", "Météo-France PNT · horaire H+1 à H+45 · "
             "AROME initialisé et couplé par IFS/ECMWF.", 4),
            ("AROME HD + ICON-D2", "Open-Meteo · maille fine horaire jusqu'à "
             "48 h · comparaison/repli sans double comptage.", 5),
        ]),
        ("🎯 Ensembles régionaux", [
            ("PE-AROME", "Météo-France PNT · 25 membres · cumuls pluie/"
             "neige H+24 et H+48.", 1),
            ("PE-ARPEGE", "Météo-France PNT · 35 membres · relais "
             "journalier jusqu'à H+96.", 2),
        ]),
        ("🌍 Grande échelle et observations", [
            ("ECMWF ENS · AIFS · GEFS", "Open-Meteo · membres bruts, "
             "moyenne et dispersion · masse d'air jusqu'à J+15.", 0),
            ("Stations Alpes du Nord", "API observations Météo-France · "
             "département 74.", 6),
        ]),
    ]
    card_pos = 0
    for section_title, section_cards in sections:
        st.subheader(section_title)
        columns = st.columns(len(section_cards))
        for container, (title, caption, entry_index) in zip(columns, section_cards):
            with container:
                st.markdown(f"**{title}**")
                st.caption(caption)
                if st.button(f"Lancer {title}", type="secondary",
                             disabled=not allowed,
                             key=f"snow_pipeline_fetch_{card_pos}"):
                    _launch([ACTIVE_FETCH_ENTRIES[entry_index]])
            card_pos += 1

    if st.button("▶️ Lancer les 7 collectes actives", type="primary",
                 disabled=not allowed, key="snow_pipeline_fetch_all"):
        _launch(ACTIVE_FETCH_ENTRIES)

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
