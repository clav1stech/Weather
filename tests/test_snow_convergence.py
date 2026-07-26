# -*- coding: utf-8 -*-
"""Tests des vues de convergence neige : révisions, spread et consensus."""

import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from apps.snow.app.pages.convergence import (  # noqa: E402
    _all_models_pivots, _convergence_chart, _revision_heatmap, _revision_pivot)


def test_revision_pivot_aligns_runs_and_keeps_future_only():
    idx = pd.date_range("2026-01-10 00:00", periods=13, freq="h")
    r0, r1, r2 = (pd.Timestamp("2026-01-08 00:00"),
                  pd.Timestamp("2026-01-08 06:00"),
                  pd.Timestamp("2026-01-08 12:00"))
    piv = pd.DataFrame({r0: 0.0, r1: 1.0, r2: 3.0}, index=idx)
    delta = _revision_pivot(piv, now=pd.Timestamp("2026-01-10 00:30"))
    assert delta is not None
    assert list(delta.columns) == [r1, r2]
    assert (delta[r1] == 1.0).all()
    assert (delta[r2] == 2.0).all()
    assert delta.index.min() >= pd.Timestamp("2026-01-10 00:00")
    assert len(_revision_heatmap(delta, "°C").data) == 1


def test_convergence_chart_adds_latest_mean_spread_band():
    idx = pd.date_range("2026-01-10", periods=4, freq="h")
    r0, r1 = pd.Timestamp("2026-01-08"), pd.Timestamp("2026-01-08 06:00")
    means = pd.DataFrame({r0: [0, 1, 2, 3], r1: [1, 2, 3, 4]}, index=idx)
    spreads = pd.DataFrame({r1: [0.5, 0.5, 1.0, 1.0]}, index=idx)
    fig = _convergence_chart(means, "#123456", "°C", spreads)
    names = [trace.name for trace in fig.data]
    assert "Run récent ± 1 écart-type" in names
    assert len(fig.data) == 4  # deux bornes de bande + deux runs mean


def test_all_models_consensus_keeps_stable_composition_and_equal_weights():
    runs = [pd.Timestamp("2026-01-08"), pd.Timestamp("2026-01-08 06:00")]
    rows = []
    for run in runs:
        for model, value in (("ECMWF_MEAN", 0.0), ("AIFS_MEAN", 4.0)):
            rows.append({"run_date": run, "model": model, "site": "village",
                         "valid_time": pd.Timestamp("2026-01-10"),
                         "t850": value + run.hour})
    # GEFS n'existe que sur le run récent : il ne doit pas changer la
    # composition du consensus ni simuler une révision.
    rows.append({"run_date": runs[1], "model": "GEFS_MEAN", "site": "village",
                 "valid_time": pd.Timestamp("2026-01-10"), "t850": 50.0})
    consensus, lower, upper, common = _all_models_pivots(
        pd.DataFrame(rows), "village", "t850")
    assert common == ["AIFS_MEAN", "ECMWF_MEAN"]
    assert consensus.loc[pd.Timestamp("2026-01-10"), runs[0]] == 2.0
    assert consensus.loc[pd.Timestamp("2026-01-10"), runs[1]] == 8.0
    assert lower.loc[pd.Timestamp("2026-01-10"), runs[1]] == 6.0
    assert upper.loc[pd.Timestamp("2026-01-10"), runs[1]] == 10.0


def test_all_models_chart_uses_model_mean_envelope():
    idx = pd.date_range("2026-01-10", periods=2, freq="h")
    run = pd.Timestamp("2026-01-08")
    consensus = pd.DataFrame({run: [2.0, 3.0]}, index=idx)
    lower = pd.DataFrame({run: [0.0, 1.0]}, index=idx)
    upper = pd.DataFrame({run: [4.0, 5.0]}, index=idx)
    fig = _convergence_chart(
        consensus, "#123456", "°C",
        envelope=(lower, upper, "Amplitude des moyennes modèles"))
    assert [trace.name for trace in fig.data].count(
        "Amplitude des moyennes modèles") == 1
