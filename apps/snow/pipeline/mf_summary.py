# -*- coding: utf-8 -*-
"""Archive compacte des sorties Météo-France ciblées.

Les collecteurs conservent leurs membres bruts dans les parquets HOT/COLD afin
de permettre un recalibrage futur. Ce fichier produit en parallèle une moyenne
par cycle, modèle, site et échéance pour les lectures historiques légères.
La variable catégorielle ``ptype`` est réduite par son mode, jamais par moyenne.
"""

import numpy as np
import pandas as pd

from apps.snow import snow_config as SC
from core.pipeline import ensemble_runs as ER


_GROUP_COLS = ["run_date", "model", "site", "valid_time", "period_h"]


def _numeric_mode(series):
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return np.nan
    modes = values.mode()
    return float(modes.iloc[0]) if not modes.empty else np.nan


def summarize_rows(raw):
    """Réduit des membres/déterministes bruts sans inventer de zéro."""
    empty = pd.DataFrame(columns=SC.MF_SUMMARY_SCHEMA)
    if raw is None or raw.empty:
        return empty
    required = set(_GROUP_COLS) | {"member", "cell_lat", "cell_lon"} \
        | set(SC.MF_LOCAL_VAR_COLS)
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(
            "Colonnes brutes absentes pour la synthèse MF : " + ", ".join(missing))
    rows = []
    for key, group in raw.groupby(_GROUP_COLS, dropna=False):
        row = dict(zip(_GROUP_COLS, key))
        row["n_members"] = int(pd.to_numeric(
            group["member"], errors="coerce").nunique())
        row["cell_lat"] = pd.to_numeric(group["cell_lat"], errors="coerce").mean()
        row["cell_lon"] = pd.to_numeric(group["cell_lon"], errors="coerce").mean()
        for column in SC.MF_LOCAL_VAR_COLS:
            if column == "ptype":
                row[column] = _numeric_mode(group[column])
            else:
                row[column] = pd.to_numeric(
                    group[column], errors="coerce").mean()
        rows.append(row)
    return pd.DataFrame(rows)[SC.MF_SUMMARY_SCHEMA].sort_values(
        _GROUP_COLS).reset_index(drop=True)


def _same_summary(existing, candidate):
    """Vrai si tous les couples (run, modèle) candidats sont déjà exacts."""
    if existing is None or existing.empty or candidate.empty:
        return False
    keys = candidate[["run_date", "model"]].drop_duplicates()
    stored = existing.merge(keys, on=["run_date", "model"], how="inner")
    if len(stored) != len(candidate):
        return False
    sort_cols = _GROUP_COLS
    left = stored[SC.MF_SUMMARY_SCHEMA].sort_values(sort_cols).reset_index(drop=True)
    right = candidate[SC.MF_SUMMARY_SCHEMA].sort_values(sort_cols).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            left, right, check_dtype=False, check_exact=False,
            rtol=1e-12, atol=1e-12)
    except AssertionError:
        return False
    return True


def persist_summary(raw, db_path=None):
    """Synchronise la synthèse append-only ; run inchangé = zéro écriture."""
    db_path = db_path or SC.DB_MF_SUMMARY_PATH
    candidate = summarize_rows(raw)
    if candidate.empty:
        raise ValueError("Aucune ligne brute à synthétiser.")
    existing = ER.load_existing(db_path, SC.MF_SUMMARY_SCHEMA)
    if _same_summary(existing, candidate):
        return existing, False
    combined = ER.persist(
        candidate, db_path, schema=SC.MF_SUMMARY_SCHEMA,
        var_cols=SC.MF_SUMMARY_VAR_COLS,
        sort_cols=_GROUP_COLS, max_gap_h=24, existing=existing)
    return combined, True
