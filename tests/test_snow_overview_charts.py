# -*- coding: utf-8 -*-
"""Tests de lisibilité et d'incertitude des graphiques neige principaux."""

import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from apps.snow.app.domains.neige.charts import (  # noqa: E402
    daily_snow_chart, medians_chart)


def test_model_medians_chart_exposes_member_p10_p90_amplitude():
    rows = []
    for member, value in enumerate((-4.0, 0.0, 4.0)):
        rows.append({"model": "AIFS", "member": member,
                     "valid_time": pd.Timestamp("2026-01-10"), "t850": value})
    fig = medians_chart(pd.DataFrame(rows), "t850", "T850", "°C")
    assert fig is not None
    assert len(fig.data) == 3  # borne haute, bande jusqu'à borne basse, médiane
    assert fig.data[1].fill == "tonexty"
    assert fig.layout.title.y >= 0.95
    assert fig.layout.legend.y < 0


def test_overview_chart_legends_are_below_the_plot():
    daily = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-10")], "attendu": [4.0],
        "P90": [8.0], "prob": [0.4], "n_membres": [100],
    })
    fig = daily_snow_chart(daily, "sommet")
    assert fig.layout.legend.y < 0
    assert fig.layout.margin.b >= 80
