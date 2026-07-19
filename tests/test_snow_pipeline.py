# -*- coding: utf-8 -*-
"""Tests de la mécanique générique de pipeline d'ensemble (core/pipeline/
ensemble_runs.py) consommée par apps/snow/pipeline/. Fonctions pures + chemins
temporaires uniquement — ne touche JAMAIS aux vraies bases (persist() écrit
toujours sur le chemin qu'on lui passe : ici un dossier temporaire).
Exécutable sans pytest : `python tests/test_snow_pipeline.py` (asserts simples)."""

import os
import sys
import tempfile

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.pipeline import ensemble_runs as ER  # noqa: E402

VAR_COLS = ["t2m", "neige"]
SCHEMA = ["run_date", "model", "kind", "member", "site", "valid_time"] + VAR_COLS
ID_COLS = ["kind", "member", "site"]
RUN0 = pd.Timestamp("2026-01-01 00:00")
RUN1 = pd.Timestamp("2026-01-01 12:00")


def _run(run_date, model, hours, t2m_offset=0.0, member=0, site="village",
         kind="member"):
    """Run synthétique minimal au schéma neige (valeurs déterministes)."""
    valid = [run_date + pd.Timedelta(hours=h) for h in hours]
    return pd.DataFrame({
        "run_date": run_date, "model": model, "kind": kind, "member": member,
        "site": site, "valid_time": valid,
        "t2m": [10.0 + t2m_offset + h / 24 for h in hours],
        "neige": [0.1 * (h % 5) for h in hours],
    })[SCHEMA]


def test_contiguous_reach_ignores_isolated_tail_point():
    # Un point parasite isolé en queue ne doit pas simuler une portée pleine.
    valid = pd.Series([RUN0 + pd.Timedelta(hours=h) for h in (1, 2, 3, 300)])
    assert ER.contiguous_reach_h(RUN0, valid, max_gap_h=24) == 3


def test_mask_stale_tail_detects_copied_cycle():
    prior = _run(RUN0, "ECMWF", range(0, 48))
    # Nouveau poll : mêmes valeurs (queue recollée) sauf les 12 dernières heures.
    cand = _run(RUN1, "ECMWF", range(0, 48))
    cand["valid_time"] = prior["valid_time"]  # même grille d'échéances
    cand.loc[cand.index[-12:], "t2m"] += 2.0
    masked, a_du_neuf = ER.mask_stale_tail(
        "ECMWF", cand, prior, var_cols=VAR_COLS, id_cols=ID_COLS, eps=0.05)
    assert a_du_neuf
    assert masked["t2m"].head(36).isna().all()      # copie → NaN-ifiée
    assert masked["t2m"].tail(12).notna().all()     # renouvelé → conservé


def test_mask_stale_tail_fully_stale():
    prior = _run(RUN0, "ECMWF", range(0, 48))
    _, a_du_neuf = ER.mask_stale_tail(
        "ECMWF", prior.copy(), prior, var_cols=VAR_COLS, id_cols=ID_COLS, eps=0.05)
    assert not a_du_neuf


def test_filter_fresh_rows_holds_back_short_run():
    existing = _run(RUN0, "ECMWF", range(0, 336))
    fresh = _run(RUN1, "ECMWF", range(0, 100), t2m_offset=3.0)  # en cours de calcul
    kept, stale, partial = ER.filter_fresh_rows(
        fresh, existing, var_cols=VAR_COLS, id_cols=ID_COLS, eps=0.05,
        horizon_h_by_model={"ECMWF": 336}, horizon_tol_h=24,
        min_horizon_h=312, max_gap_h=24)
    assert kept.empty and partial == ["ECMWF"] and not stale


def test_persist_dedups_by_run_and_model_and_keeps_history():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "db.parquet")
        ER.persist(_run(RUN0, "ECMWF", range(0, 336)), db, schema=SCHEMA,
                   var_cols=VAR_COLS,
                   sort_cols=["run_date", "model", "valid_time"], max_gap_h=24)
        # Nouveau cycle ECMWF + premier run GEFS : l'ancien run ECMWF intact.
        fresh = pd.concat([_run(RUN1, "ECMWF", range(0, 336), t2m_offset=1.0),
                           _run(RUN1, "GEFS", range(0, 336))], ignore_index=True)
        combined = ER.persist(fresh, db, schema=SCHEMA, var_cols=VAR_COLS,
                              sort_cols=["run_date", "model", "valid_time"],
                              max_gap_h=24)
        keys = set(map(tuple, combined[["run_date", "model"]].drop_duplicates()
                       .itertuples(index=False)))
        assert keys == {(RUN0, "ECMWF"), (RUN1, "ECMWF"), (RUN1, "GEFS")}


def test_persist_refuses_regression():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "db.parquet")
        full = _run(RUN0, "ECMWF", range(0, 336))
        ER.persist(full, db, schema=SCHEMA, var_cols=VAR_COLS,
                   sort_cols=["run_date", "model", "valid_time"], max_gap_h=24)
        # Glitch API : même couple (run_date, modèle) mais portée tronquée.
        truncated = _run(RUN0, "ECMWF", range(0, 48), t2m_offset=0.5)
        combined = ER.persist(truncated, db, schema=SCHEMA, var_cols=VAR_COLS,
                              sort_cols=["run_date", "model", "valid_time"],
                              max_gap_h=24)
        assert len(combined) == len(full)
        expected = full.sort_values(["run_date", "model", "valid_time"]) \
                       .reset_index(drop=True)["t2m"]
        assert combined["t2m"].equals(expected)


def test_validate_refuses_empty_run():
    for bad in (pd.DataFrame(columns=SCHEMA),
                _run(RUN0, "ECMWF", range(0, 4)).assign(t2m=np.nan, neige=np.nan)):
        try:
            ER.validate(bad, SCHEMA, VAR_COLS)
        except ValueError:
            pass
        else:
            raise AssertionError("validate aurait dû refuser un run vide")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("Tous les tests snow pipeline passent.")
