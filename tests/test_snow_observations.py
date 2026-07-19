# -*- coding: utf-8 -*-
"""Tests de la mécanique générique d'observations DPPaquetObs
(core/pipeline/observations.py) consommée par apps/snow/pipeline/.
Fonctions pures + chemins temporaires uniquement — ne touche JAMAIS aux
vraies bases. Exécutable sans pytest : `python tests/test_snow_observations.py`."""

import math
import os
import sys
import tempfile

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.pipeline import observations as OBS  # noqa: E402

STATIONS = {"74236002": {"id": "74236002", "nom": "Mont d'Arbois"},
            "74083002": {"id": "74083002", "nom": "Combloux"}}
VARIABLES = [{"api": "t",        "col": "t",      "conv": "kelvin"},
             {"api": "ht_neige", "col": "hneige", "conv": "m_to_cm"},
             {"api": "ff",       "col": "vent_ff", "conv": None}]
VAR_COLS = ["t", "hneige", "vent_ff"]
SCHEMA = ["valid_time", "station_id", "station_nom"] + VAR_COLS


def test_convert_value_units():
    assert OBS.convert_value(273.15, "kelvin") == 0.0
    assert OBS.convert_value(0.42, "m_to_cm") == 42.0
    assert OBS.convert_value(101325, "pa_to_hpa") == 1013.25
    assert OBS.convert_value(3.5, None) == 3.5
    assert math.isnan(OBS.convert_value(None, "kelvin"))   # absence structurelle
    assert math.isnan(OBS.convert_value("n/a", None))


def test_parse_filters_and_converts():
    payload = [
        {"geo_id_insee": "74236002", "validity_time": "2026-01-10T06:00:00Z",
         "t": 268.15, "ht_neige": 0.8, "ff": 5.0},
        {"geo_id_insee": "99999999", "validity_time": "2026-01-10T06:00:00Z",
         "t": 270.0},                                     # station hors config
        {"geo_id_insee": "74083002", "t": 271.0},          # sans validity_time
        {"geo_id_insee": "74083002", "validity_time": "2026-01-10T06:00:00Z",
         "t": None, "ht_neige": None, "ff": None},         # aucune valeur valide
    ]
    df = OBS.parse_observations(payload, STATIONS, VARIABLES, SCHEMA, VAR_COLS)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["station_nom"] == "Mont d'Arbois"
    assert row["t"] == -5.0 and row["hneige"] == 80.0 and row["vent_ff"] == 5.0
    assert row["valid_time"] == pd.Timestamp("2026-01-10 06:00")  # UTC tz-naïf


def test_persist_never_replaces_existing_observation():
    def _obs(hour, t):
        return pd.DataFrame([{
            "valid_time": pd.Timestamp(f"2026-01-10 {hour:02d}:00"),
            "station_id": "74236002", "station_nom": "Mont d'Arbois",
            "t": t, "hneige": 80.0, "vent_ff": 5.0}])[SCHEMA]

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "obs.parquet")
        OBS.persist(_obs(6, t=-5.0), path, SCHEMA)
        # Repasse avec la MÊME obs (valeur différente : glitch) + une nouvelle.
        fresh = pd.concat([_obs(6, t=99.0), _obs(7, t=-4.0)], ignore_index=True)
        combined, n_new = OBS.persist(fresh, path, SCHEMA)
        assert n_new == 1                       # seule 07:00 est nouvelle
        stored = combined.set_index("valid_time")["t"]
        assert stored[pd.Timestamp("2026-01-10 06:00")] == -5.0  # fait acquis, intact


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("Tous les tests observations passent.")
