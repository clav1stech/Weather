# -*- coding: utf-8 -*-
"""Tests du mécanisme d'archivage hot/cold (core/pipeline/hot_cold.py).
Chemins temporaires uniquement — ne touche JAMAIS aux vraies bases.
Exécutable sans pytest : `python tests/test_hot_cold.py`."""

import os
import sys
import tempfile

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.pipeline import hot_cold as HC  # noqa: E402

NOW = pd.Timestamp("2026-07-19 12:00")


def _df(days_ago_list):
    """Base synthétique : une ligne par ancienneté (jours avant NOW)."""
    return pd.DataFrame({
        "run_date": [NOW - pd.Timedelta(days=d) for d in days_ago_list],
        "model": "ECMWF",
        "val": [float(d) for d in days_ago_list],
    })


def test_split_boundary_is_strict():
    df = _df([10, 45, 46])
    cutoff = NOW - pd.Timedelta(days=45)
    hot, cold = HC.split_hot_cold(df, "run_date", cutoff)
    # La ligne à exactement 45 j (= cutoff) reste en hot ; seule 46 j bascule.
    assert list(hot["val"]) == [10.0, 45.0]
    assert list(cold["val"]) == [46.0]


def test_rollover_moves_and_preserves_union():
    with tempfile.TemporaryDirectory() as tmp:
        hot_p = os.path.join(tmp, "hot.parquet")
        cold_p = os.path.join(tmp, "cold.parquet")
        original = _df([1, 10, 50, 60])
        original.to_parquet(hot_p, index=False)

        report = HC.rollover(hot_p, cold_p, "run_date", 45, now=NOW)
        assert report["written"] and report["moved"] == 2
        assert len(report["backups"]) == 1  # cold n'existait pas encore

        hot, cold = pd.read_parquet(hot_p), pd.read_parquet(cold_p)
        assert sorted(hot["val"]) == [1.0, 10.0]
        assert sorted(cold["val"]) == [50.0, 60.0]
        # Union intacte (mêmes lignes, mêmes valeurs).
        union = pd.concat([hot, cold]).sort_values("val").reset_index(drop=True)
        assert union.equals(original.sort_values("val").reset_index(drop=True))
        # Sauvegarde datée présente, en .bak (jamais matchée par *.parquet).
        assert report["backups"][0].endswith(".bak")
        assert os.path.exists(report["backups"][0])


def test_rollover_idempotent_and_cold_append_only():
    with tempfile.TemporaryDirectory() as tmp:
        hot_p = os.path.join(tmp, "hot.parquet")
        cold_p = os.path.join(tmp, "cold.parquet")
        _df([1, 50]).to_parquet(hot_p, index=False)
        HC.rollover(hot_p, cold_p, "run_date", 45, now=NOW)

        # Relance immédiate : rien à basculer, aucun fichier touché.
        before = os.path.getmtime(cold_p)
        report = HC.rollover(hot_p, cold_p, "run_date", 45, now=NOW)
        assert report["moved"] == 0 and not report["written"]
        assert os.path.getmtime(cold_p) == before

        # Nouvelle ligne vieillissante : le cold s'APPEND (ancien contenu en tête).
        pd.concat([pd.read_parquet(hot_p), _df([48])]).to_parquet(hot_p, index=False)
        HC.rollover(hot_p, cold_p, "run_date", 45, now=NOW)
        cold = pd.read_parquet(cold_p)
        assert list(cold["val"]) == [50.0, 48.0]  # append derrière, jamais réordonné


def test_rollover_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        hot_p = os.path.join(tmp, "hot.parquet")
        cold_p = os.path.join(tmp, "cold.parquet")
        _df([1, 50]).to_parquet(hot_p, index=False)
        before = os.path.getmtime(hot_p)
        report = HC.rollover(hot_p, cold_p, "run_date", 45, now=NOW, dry_run=True)
        assert report["moved"] == 1 and not report["written"]
        assert not report["backups"]
        assert not os.path.exists(cold_p)
        assert os.path.getmtime(hot_p) == before


def test_rollover_absorbs_prior_overlap():
    # Crash simulé d'un rollover antérieur : une ligne présente à la fois en
    # hot et en cold ne doit ni bloquer ni se dupliquer.
    with tempfile.TemporaryDirectory() as tmp:
        hot_p = os.path.join(tmp, "hot.parquet")
        cold_p = os.path.join(tmp, "cold.parquet")
        _df([1, 50]).to_parquet(hot_p, index=False)
        _df([50]).to_parquet(cold_p, index=False)  # déjà archivée ET encore en hot
        report = HC.rollover(hot_p, cold_p, "run_date", 45, now=NOW)
        assert report["written"]
        cold = pd.read_parquet(cold_p)
        assert list(cold["val"]) == [50.0]  # une seule fois, pas de doublon
        assert list(pd.read_parquet(hot_p)["val"]) == [1.0]


def test_rollover_missing_hot_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        report = HC.rollover(os.path.join(tmp, "absent.parquet"),
                             os.path.join(tmp, "cold.parquet"),
                             "run_date", 45, now=NOW)
        assert report["moved"] == 0 and not report["written"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("Tous les tests hot/cold passent.")
