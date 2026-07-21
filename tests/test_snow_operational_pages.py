# -*- coding: utf-8 -*-
"""Tests des trois pages opérationnelles neige et de leurs helpers purs."""

import os
import sys

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from apps.snow import snow_config as SC  # noqa: E402
from apps.snow.app.data.quality import quality_report  # noqa: E402
from apps.snow.app.pages.pipeline import (  # noqa: E402
    FETCH_ENTRIES, ROLLOVER_ENTRY, ROOT_DIR)
from core.ui.pipeline import run_script  # noqa: E402


def _quality_run(model, run_local, hours, offset):
    """Run membre minimal au schéma complet, en heure d'affichage Paris."""
    valid = pd.date_range(run_local, periods=len(hours), freq="h")
    df = pd.DataFrame({
        "run_date": run_local, "model": model, "kind": "member",
        "member": 0, "site": "village", "valid_time": valid,
    })
    for col in SC.ENS_VAR_COLS:
        df[col] = np.nan
    df["t2m"] = np.arange(len(df), dtype=float) + offset
    return df[SC.ENS_SCHEMA]


def test_pipeline_entries_cover_three_fluxes_and_rollover_is_dry_run_only():
    scripts = [entry[1] for entry in FETCH_ENTRIES]
    assert scripts == [
        "apps/snow/pipeline/fetch_ensemble.py",
        "apps/snow/pipeline/fetch_hd.py",
        "apps/snow/pipeline/fetch_observations.py",
    ]
    assert ROLLOVER_ENTRY[0][1] == "apps/snow/pipeline/rollover.py"
    assert all("--execute" not in str(part)
               for entry in ROLLOVER_ENTRY for part in entry)


def test_generic_run_script_captures_stdout_and_stderr():
    code, output = run_script(
        ROOT_DIR, "-c",
        "import sys; print('neige'); print('diagnostic', file=sys.stderr)")
    assert code == 0
    assert "neige" in output
    assert "[stderr]" in output and "diagnostic" in output


def test_quality_report_uses_expected_cycles_and_contiguous_completeness():
    # Hiver : 00Z = 01h locale, 06Z = 07h locale. Les deux cycles AIFS
    # attendus avant 12Z sont présents et atteignent l'horizon requis.
    df = pd.concat([
        _quality_run("AIFS", pd.Timestamp("2026-01-01 01:00"), range(337), 0),
        _quality_run("AIFS", pd.Timestamp("2026-01-01 07:00"), range(337), 10),
    ], ignore_index=True)
    summary, history, anomalies = quality_report(
        df, now_utc=pd.Timestamp("2026-01-01 12:00"), lookback_days=1)
    aifs = summary[summary["Modèle"] == "AIFS"].iloc[0]
    assert aifs["Complétude"] == "complet"
    assert aifs["Publication"] == "à jour"
    assert aifs["Fraîcheur empirique"] == "renouvelé"
    assert history.loc[history["model"] == "AIFS", "complete"].all()
    assert anomalies[(anomalies["Modèle"] == "AIFS")
                     & (anomalies["Type"] == "cycle manquant")].empty


def test_quality_report_flags_missing_expected_cycle_and_partial_reach():
    df = _quality_run("AIFS", pd.Timestamp("2026-01-01 01:00"), range(101), 0)
    summary, _, anomalies = quality_report(
        df, now_utc=pd.Timestamp("2026-01-01 12:00"), lookback_days=1)
    aifs = summary[summary["Modèle"] == "AIFS"].iloc[0]
    assert aifs["Complétude"] == "partiel"
    assert aifs["Publication"] == "en retard"
    missing = anomalies[(anomalies["Modèle"] == "AIFS")
                        & (anomalies["Type"] == "cycle manquant")]
    assert pd.Timestamp("2026-01-01 06:00") in set(missing["Cycle UTC"])


def test_snow_operational_pages_render_without_exception(monkeypatch):
    monkeypatch.setenv("WEATHER_LOCAL", "1")
    at = AppTest.from_file(os.path.join(_ROOT, "snow_app.py"), default_timeout=60)
    at.run()
    options = at.sidebar.radio[0].options
    assert "Convergence des runs" in options
    assert "Contrôle des runs" in options
    assert "Lancer le pipeline" in options
    for page in ("Convergence des runs", "Contrôle des runs", "Lancer le pipeline"):
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, at.exception)
    assert "Maille fine Météo-France" in options


def test_neige_overview_and_explore_pages_render_without_exception(monkeypatch):
    # La refonte grand public (frise de tendance, tuile « Changement de temps »,
    # expanders) et les repères de seuil ajoutés à Explorer doivent se rendre
    # sans exception sur les données réelles en base, quelle que soit la saison.
    monkeypatch.setenv("WEATHER_LOCAL", "1")
    at = AppTest.from_file(os.path.join(_ROOT, "snow_app.py"), default_timeout=90)
    at.run()
    for page in ("Vue d'ensemble neige", "Explorer un run",
                 "Maille fine Météo-France"):
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, at.exception)
