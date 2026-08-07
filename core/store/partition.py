# -*- coding: utf-8 -*-
"""Partitionnement mensuel d'un parquet — fonctions PURES (aucune I/O, aucun
effet de bord), cœur du chantier « sortie de git » (docs/DESIGN_sortie_git.md).

Un flux est découpé en une partition par mois de sa colonne temporelle de
référence (`run_date` pour la base d'ensemble : un run = une partition entière,
`persist()` déduplique par (run_date, model) donc toujours au sein d'une même
partition, jamais à cheval — cf. DESIGN §5). Chaque partition est stockée comme
un asset `<prefix>_<YYYY-MM>.parquet`.

Invariant ABSOLU (intégrité des données, CLAUDE.md) : le découpage ne perd ni
n'invente aucune ligne. `same_rows(concat_partitions(split_by_month(df)), df)`
est TOUJOURS vrai — testé, et rejoué comme preuve à l'amorçage (Phase 1)."""

import re

import pandas as pd

# <prefix>_<YYYY-MM>.parquet — le préfixe peut contenir des « _ » (ex.
# database_paris_observations) : le `.+` glouton ne laisse que le dernier
# segment mois-année à la clé.
_PART_RE = re.compile(r"^(?P<prefix>.+)_(?P<key>\d{4}-\d{2})\.parquet$")


def month_key(ts) -> str:
    """Timestamp → clé de partition « YYYY-MM » (mois calendaire)."""
    return pd.Timestamp(ts).strftime("%Y-%m")


def partition_name(prefix: str, key: str) -> str:
    """(prefix, clé mois) → nom d'asset canonique."""
    return f"{prefix}_{key}.parquet"


def parse_partition_name(name: str):
    """Nom d'asset → (prefix, clé mois), ou None si le nom ne suit pas le
    schéma (permet d'ignorer sans bruit un asset étranger sur le release)."""
    m = _PART_RE.match(name)
    return (m.group("prefix"), m.group("key")) if m else None


def split_by_month(df: pd.DataFrame, time_col: str) -> dict:
    """DataFrame → {clé mois: sous-DataFrame}, découpé sur le mois de `time_col`.

    Aucune ligne n'est perdue : un NaT dans `time_col` ne peut pas être classé,
    c'est une anomalie amont → ValueError explicite (jamais un abandon silencieux
    qui perdrait la ligne). DataFrame vide → dict vide. Les sous-DataFrames ne
    portent aucune colonne technique ajoutée et gardent l'ordre d'origine."""
    if df.empty:
        return {}
    ts = pd.to_datetime(df[time_col])
    if ts.isna().any():
        raise ValueError(
            f"split_by_month : {int(ts.isna().sum())} ligne(s) sans « {time_col} » "
            "valide — impossible de partitionner sans risquer une perte.")
    keys = ts.dt.strftime("%Y-%m")
    return {k: sub.drop(columns=[]).reset_index(drop=True)
            for k, sub in df.groupby(keys, sort=True)}


def concat_partitions(frames) -> pd.DataFrame:
    """Liste/itérable de DataFrames de partitions → un seul DataFrame (l'inverse
    de split_by_month). Liste vide → DataFrame vide. N'ordonne pas : le tri
    éventuel appartient aux couches au-dessus (le pipeline conserve son propre
    ordre via persist)."""
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _canonical(df: pd.DataFrame) -> pd.DataFrame:
    """Forme canonique pour comparaison ensembliste (mêmes lignes, quel que soit
    l'ordre) — dédup pleine ligne, tri sur toutes les colonnes, index remis à
    zéro. Même approche que core/pipeline/hot_cold._canonical."""
    cols = sorted(df.columns)
    df = df[cols]
    return (df.sort_values(cols, na_position="last")
              .reset_index(drop=True))


def same_rows(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """True si `a` et `b` portent exactement les mêmes lignes (mêmes colonnes,
    mêmes valeurs, ordre indifférent — NaN == NaN). Sert de PREUVE de
    non-régression au round-trip split → concat (tests + amorçage Phase 1)."""
    if sorted(a.columns) != sorted(b.columns):
        return False
    return _canonical(a).equals(_canonical(b))
