# -*- coding: utf-8 -*-
"""Parsing de ``precip`` et compatibilité avec les anciens schémas neige."""

import os
import sys
import tempfile

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from apps.snow import snow_config as SC  # noqa: E402
from apps.snow.app.data.db import _align_schema  # noqa: E402
from apps.snow.pipeline import fetch_ensemble, fetch_hd  # noqa: E402
from core.pipeline import ensemble_runs as ER  # noqa: E402


TIMES = ["2026-01-10T00:00", "2026-01-10T01:00"]


def _point(hourly):
    return {"utc_offset_seconds": 0, "hourly": {"time": TIMES, **hourly}}


def test_ensemble_parse_precip_uniquement_au_village():
    model = SC.ENS_MODELS[0]
    suffix = model["api"]
    payload = [
        _point({f"temperature_2m_{suffix}": [0.0, 1.0],
                f"precipitation_{suffix}": [0.2, 1.3]}),
        _point({f"temperature_2m_{suffix}": [-2.0, -1.0],
                f"precipitation_{suffix}": [0.4, 2.0]}),
    ]
    run_dates = {m["label"]: pd.Timestamp("2026-01-10") for m in SC.ENS_MODELS}
    frames = fetch_ensemble.parse_members(payload, run_dates)
    parsed = pd.concat(frames, ignore_index=True)
    village = parsed[(parsed["model"] == model["label"])
                     & (parsed["site"] == "village")]
    sommet = parsed[(parsed["model"] == model["label"])
                    & (parsed["site"] == "sommet")]
    assert list(village["precip"]) == [0.2, 1.3]
    assert sommet["precip"].isna().all()


def test_hd_parse_precip_aux_deux_sites():
    keys = {f"precipitation_{m['api']}": [0.2, 1.3] for m in SC.HD_MODELS}
    payload = [_point(keys), _point(keys)]
    parsed = fetch_hd.parse_payload(payload, pd.Timestamp("2026-01-10"))
    assert set(parsed["site"]) == {"village", "sommet"}
    assert parsed["precip"].notna().all()
    assert set(parsed.groupby(["model", "site"])["precip"].sum()) == {1.5}


def test_loaders_alignent_ancien_schema_sans_inventer_de_valeur():
    old_ens_schema = [col for col in SC.ENS_SCHEMA if col != "precip"]
    old = pd.DataFrame([{col: np.nan for col in old_ens_schema}])
    old["run_date"] = pd.Timestamp("2026-01-10")
    old["valid_time"] = pd.Timestamp("2026-01-10")
    old["model"] = SC.ENS_LABELS[0]
    old["kind"] = "member"
    old["member"] = 0
    old["site"] = "village"

    aligned_dashboard = _align_schema(old, SC.ENS_SCHEMA)
    assert list(aligned_dashboard.columns) == SC.ENS_SCHEMA
    assert aligned_dashboard["precip"].isna().all()

    # Le loader du pipeline porte la même garantie sur le parquet historique.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "old.parquet")
        old.to_parquet(path, index=False)
        aligned_pipeline = ER.load_existing(path, SC.ENS_SCHEMA)
    assert list(aligned_pipeline.columns) == SC.ENS_SCHEMA
    assert aligned_pipeline["precip"].isna().all()


def test_schema_precip_est_derive_des_variables_configurees():
    assert "precip" in SC.ENS_VAR_COLS and "precip" in SC.ENS_SCHEMA
    assert "precip" in SC.HD_VAR_COLS and "precip" in SC.HD_SCHEMA
    ens = next(v for v in SC.ENS_VARIABLES if v["col"] == "precip")
    hd = next(v for v in SC.HD_VARIABLES if v["col"] == "precip")
    assert ens["sites"] == ["village"] and ens["spread"] is False
    assert hd["sites"] == ["village", "sommet"]
