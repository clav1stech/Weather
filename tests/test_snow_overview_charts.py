# -*- coding: utf-8 -*-
"""Tests de lisibilité et d'incertitude des graphiques neige principaux."""

import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from apps.snow.app.domains.neige.charts import (  # noqa: E402
    daily_snow_chart, hourly_vertical_weather_chart, medians_chart,
    weather_type_chart, weather_type_strip_chart)
from apps.snow.app.domains.neige.page import _hd_daily_table  # noqa: E402
from apps.snow.app.pages.explore import _seuils_repere  # noqa: E402


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


def test_hourly_hd_chart_reste_borne_aux_altitudes_et_masque_lpn_iso0():
    rows = []
    for altitude, snow in ((1100, float("nan")), (1300, 1.0),
                           (1600, 3.0), (2000, 4.0)):
        rows.append({
            "valid_time": pd.Timestamp("2026-01-10 06:00"),
            "date": pd.Timestamp("2026-01-10"), "jour": 0,
            "altitude_m": altitude, "neige_cm": snow,
            "phase": "pluie" if altitude == 1100 else "neige", "t2m_c": -1.0,
            "precip_mm": 4.0, "quantite": 4.0,
            "unite": "mm" if altitude == 1100 else "cm",
            "pluie_mm": 4.0 if altitude == 1100 else float("nan"),
            "lpn_m": 1200.0, "iso0_m": 1500.0, "n_modeles": 1,
        })
    fig = hourly_vertical_weather_chart(pd.DataFrame(rows))
    traces = {trace.name: trace for trace in fig.data}
    assert "🌧️ Pluie" in traces and "❄️ Neige" in traces
    assert not any("LPN" in name or "Iso 0" in name for name in traces)
    assert fig.layout.yaxis.range[1] <= 2100
    assert fig.layout.legend.y < 0


def test_hourly_chart_affiche_explicitement_la_phase_mixte_arome_pi():
    row = {
        "valid_time": pd.Timestamp("2026-01-10 06:00"),
        "altitude_m": 1300, "neige_cm": 0.9, "pluie_mm": 1.1,
        "phase": "mixte", "t2m_c": 0.2, "precip_mm": 2.0,
        "quantite": 2.0, "unite": "mm éq. eau", "source": "AROME-PI",
    }
    fig = hourly_vertical_weather_chart(pd.DataFrame([row]))
    assert "🌦️ Pluie/neige" in {trace.name for trace in fig.data}
    assert "Source : AROME-PI" in fig.data[0].hovertext[0]
    assert "❄️ 0.9 cm" in fig.data[0].hovertext[0]
    assert "🌧️ 1.1 mm" in fig.data[0].hovertext[0]


def test_bilan_hd_quotidien_affiche_mm_et_cm_par_altitude():
    summary = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-10")] * 2,
        "altitude_m": [1100, 1600],
        "pluie_mm": [4.2, 0.0], "neige_cm": [0.0, 7.5],
    })
    table = _hd_daily_table(summary)
    assert table.loc["1100 m"].iloc[0] == "🌧️ 4.2 mm"
    assert table.loc["1600 m"].iloc[0] == "❄️ 7.5 cm"


def test_weather_type_chart_est_empile_a_100_pourcent():
    daily = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-14")], "jour": [4],
        "neigeux": [25.0], "pluvieux": [25.0], "sec": [25.0],
        "mixte": [25.0], "n_classes": [40], "n_non_classes": [0],
    })
    hd = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-14")], "categorie": ["sec"],
        "pluie_mm": [0.0], "neige_cm": [0.0], "heures_hd": [24],
        "partiel": [False],
    })
    fig = weather_type_chart(daily, hd)
    assert fig.layout.barmode == "stack"
    bars = [trace for trace in fig.data if trace.type == "bar"]
    assert sum(float(trace.y[0]) for trace in bars) == 100.0
    assert {trace.name for trace in bars} == {
        "❄️ Neigeux", "🌧️ Pluvieux (≥ 2 mm)",
        "☀️ Sec / ensoleillé", "🌦️ Trace / mixte / incertain"}
    assert {trace.text[0] for trace in bars} == {"❄️", "🌧️", "☀️", "🌦️"}
    hd_trace = [trace for trace in fig.data if trace.type == "scatter"][0]
    assert hd_trace.text[0] == "HD ☀️"
    assert "pluie 0.0 mm" in hd_trace.hovertext[0]


def test_weather_type_strip_expose_dominant_couleur_emoji_et_pourcentages():
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-10", "2026-01-11", "2026-01-12"]),
        "jour": [0, 1, 2],
        # J0 accord fort (neige), J1 4-way égal (mixte visuel), J2 sec net.
        "neigeux": [80.0, 25.0, 10.0],
        "pluvieux": [5.0, 25.0, 5.0],
        "sec": [5.0, 25.0, 75.0],
        "mixte": [10.0, 25.0, 10.0],
        "n_classes": [40, 40, 40],
    })
    fig = weather_type_strip_chart(daily)
    bars = [trace for trace in fig.data if trace.type == "bar"]
    assert len(bars) == 1
    bar = bars[0]
    # Catégorie dominante par jour : neige, neige (1er argmax sur égalité), sec.
    assert list(bar.marker.color) == ["#5DADE2", "#5DADE2", "#F4D03F"]
    assert list(bar.text) == ["❄️", "❄️", "☀️"]
    # Accord fort (J0) → tuile plus opaque que l'égalité 25 % (J1).
    assert bar.marker.opacity[0] > bar.marker.opacity[1]
    # Les proportions restent lisibles au survol de chaque tuile.
    assert "%" in bar.hovertemplate
    assert "customdata[1]" in bar.hovertemplate


def test_medians_chart_dessine_les_reperes_de_seuil_transmis():
    rows = [{"model": "AIFS", "member": m,
             "valid_time": pd.Timestamp("2026-01-10"), "t850": v}
            for m, v in enumerate((-4.0, 0.0, 4.0))]
    seuils = _seuils_repere("t850")
    fig = medians_chart(pd.DataFrame(rows), "t850", "T850", "°C",
                        seuils_h=seuils)
    labels = {ann.text for ann in fig.layout.annotations}
    assert labels == set(seuils)


def test_seuils_repere_couvre_t850_et_epaisseur_seulement():
    assert set(_seuils_repere("t850")) == {"neige sommet", "neige village"}
    assert set(_seuils_repere("epaisseur")) == {"repère village", "repère sommet"}
    assert _seuils_repere("pmsl") is None
