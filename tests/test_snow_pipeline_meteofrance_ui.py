# -*- coding: utf-8 -*-
"""Contrats UI des collecteurs Météo-France du pipeline neige."""

from pathlib import Path

from apps.snow.app.pages.pipeline import ACTIVE_FETCH_ENTRIES


ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_entries_cover_all_active_collectors():
    scripts = [entry[1] for entry in ACTIVE_FETCH_ENTRIES]
    assert scripts == [
        "apps/snow/pipeline/fetch_ensemble.py",
        "apps/snow/pipeline/fetch_pe_arome.py",
        "apps/snow/pipeline/fetch_pe_arpege.py",
        "apps/snow/pipeline/fetch_arome_pi.py",
        "apps/snow/pipeline/fetch_arome_ifs.py",
        "apps/snow/pipeline/fetch_hd.py",
        "apps/snow/pipeline/fetch_observations.py",
    ]


def test_sidebar_names_models_and_data_sources():
    source = (ROOT / "snow_app.py").read_text(encoding="utf-8")
    for label in (
        "Météo-France PNT", "Open-Meteo", "AROME-PI", "PE-AROME",
        "PE-ARPEGE", "AROME-IFS", "AROME France (source MF)", "ICON-D2",
        "ECMWF ENS", "AIFS",
        "GEFS", "API Météo-France",
    ):
        assert label in source
