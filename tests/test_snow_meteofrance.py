# -*- coding: utf-8 -*-
"""Tests de la page « Maille fine Météo-France » : helpers purs de mise en
forme (déterministe, ensembles, ptype) et constructeurs de graphiques.
Exécutable sans pytest : `python tests/test_snow_meteofrance.py`."""

import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from apps.snow.app.domains.neige.charts import (  # noqa: E402
    mf_member_box, mf_meteogram, ptype_strip)
from apps.snow.app.pages.meteofrance import (  # noqa: E402
    _deterministic_series, _exceedance_table, _member_windows, _ptype_frise)

NOW = pd.Timestamp("2026-01-10 00:00")
RUN = pd.Timestamp("2026-01-10 00:00")


def _det_df():
    return pd.DataFrame([
        # passé (filtré), village futur ×2, sommet futur (autre site)
        {"site": "village", "valid_time": NOW - pd.Timedelta(hours=1),
         "precip": 9.0, "neige_eau": 0.0, "t2m": 5.0},
        {"site": "village", "valid_time": NOW + pd.Timedelta(hours=1),
         "precip": 3.0, "neige_eau": 2.0, "t2m": -1.0},
        {"site": "village", "valid_time": NOW + pd.Timedelta(hours=2),
         "precip": 1.0, "neige_eau": 1.5, "t2m": -2.0},   # neige > total → bornée
        {"site": "sommet", "valid_time": NOW + pd.Timedelta(hours=1),
         "precip": 4.0, "neige_eau": 4.0, "t2m": -6.0},
    ])


def test_deterministic_series_borne_neige_et_filtre_futur_par_site():
    series = _deterministic_series(_det_df(), "village", now=NOW)
    assert list(series["valid_time"]) == [NOW + pd.Timedelta(hours=1),
                                          NOW + pd.Timedelta(hours=2)]
    # h+1 : total 3, neige 2 → pluie 1 ; h+2 : neige 1,5 bornée à total 1 → pluie 0.
    assert list(series["neige_mm"].round(2)) == [2.0, 1.0]
    assert list(series["pluie_mm"].round(2)) == [1.0, 0.0]
    assert list(series["t2m_c"]) == [-1.0, -2.0]


def _members_df():
    rows = []
    for member in range(4):
        for end_h, neige in ((24, member * 1.0), (48, member * 2.0)):
            rows.append({
                "run_date": RUN, "site": "village", "member": member,
                "valid_time": RUN + pd.Timedelta(hours=end_h), "period_h": 24,
                "neige_eau": neige, "precip": neige + 0.5,
            })
    # une ligne sommet ignorée par le filtre de site
    rows.append({"run_date": RUN, "site": "sommet", "member": 0,
                 "valid_time": RUN + pd.Timedelta(hours=24), "period_h": 24,
                 "neige_eau": 9.0, "precip": 9.0})
    return pd.DataFrame(rows)


def test_member_windows_libelle_et_cumul_par_fenetre():
    dist = _member_windows(_members_df(), "village")
    assert set(dist["window"]) == {"0–24 h", "24–48 h"}
    assert sorted(dist["window_order"].unique()) == [24, 48]
    w1 = dist[dist["window_order"] == 24]
    assert len(w1) == 4                                   # 4 membres, site village
    assert sorted(w1["neige_cm"]) == [0.0, 1.0, 2.0, 3.0]  # ratio 1 cm/mm


def test_exceedance_table_probabilites_par_palier():
    dist = pd.DataFrame({
        "window": ["0–24 h"] * 4, "window_order": [24] * 4,
        "member": range(4), "neige_cm": [0.5, 2.0, 6.0, 25.0],
        "precip_mm": [1.0, 3.0, 7.0, 26.0],
    })
    table = _exceedance_table(dist).iloc[0]
    assert table["≥ 1 cm"] == "75 %"     # 3/4
    assert table["≥ 5 cm"] == "50 %"     # 2/4
    assert table["≥ 20 cm"] == "25 %"    # 1/4
    assert table["Membres"] == 4


def test_ptype_frise_decode_les_codes_et_filtre_nan_et_passe():
    pi = pd.DataFrame([
        {"site": "village", "valid_time": NOW - pd.Timedelta(hours=1), "ptype": 5.0},
        {"site": "village", "valid_time": NOW + pd.Timedelta(hours=1), "ptype": 0.0},
        {"site": "village", "valid_time": NOW + pd.Timedelta(hours=2), "ptype": 5.0},
        {"site": "village", "valid_time": NOW + pd.Timedelta(hours=3), "ptype": np.nan},
    ])
    frise = _ptype_frise(pi, now=NOW)
    assert list(frise["code"]) == [0.0, 5.0]             # passé + NaN écartés
    assert list(frise["categorie"]) == ["sec", "neige"]


def test_meteogram_empile_pluie_neige_et_trace_t2m_sur_axe_droit():
    series = pd.DataFrame({
        "valid_time": [NOW + pd.Timedelta(hours=h) for h in (1, 2)],
        "pluie_mm": [1.0, 0.0], "neige_mm": [2.0, 1.0], "t2m_c": [-1.0, -2.0],
    })
    fig = mf_meteogram(series, "AROME-PI")
    assert fig.layout.barmode == "stack"
    bars = [t for t in fig.data if t.type == "bar"]
    assert {t.name for t in bars} == {"❄️ Neige", "🌧️ Pluie"}
    t2m = [t for t in fig.data if t.type == "scatter"][0]
    assert t2m.yaxis == "y2"


def test_meteogram_none_si_tout_nan():
    series = pd.DataFrame({
        "valid_time": [NOW], "pluie_mm": [np.nan],
        "neige_mm": [np.nan], "t2m_c": [np.nan]})
    assert mf_meteogram(series, "AROME-IFS") is None


def test_member_box_une_boite_par_fenetre_avec_reperes():
    dist = _member_windows(_members_df(), "village")
    fig = mf_member_box(dist, "neige_cm", "cm", "PE-AROME",
                        seuils_h={"1 cm": 1.0, "5 cm": 5.0})
    box = [t for t in fig.data if t.type == "box"]
    assert len(box) == 1
    assert {ann.text for ann in fig.layout.annotations} == {"1 cm", "5 cm"}


def test_ptype_strip_couleur_par_categorie_et_code_au_survol():
    frise = pd.DataFrame({
        "valid_time": [NOW + pd.Timedelta(hours=h) for h in (1, 2)],
        "code": [0.0, 5.0], "categorie": ["sec", "neige"],
        "label": ["☀️ Pas de précipitation", "❄️ Neige"],
    })
    fig = ptype_strip(frise)
    bar = [t for t in fig.data if t.type == "bar"][0]
    assert list(bar.marker.color) == ["#F4D03F", "#5DADE2"]
    assert "code ptype" in bar.hovertemplate


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("Tous les tests Maille fine Météo-France passent.")
