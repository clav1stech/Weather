# -*- coding: utf-8 -*-
"""Couche données du flux ANNEXE Tx/Tn haute résolution (parquet séparé
data/database_paris_t2m.parquet, produit par forecast_t2m_hd.py).

Lecture seule, dégradation silencieuse : parquet absent, vide ou partiel →
DataFrame vide, jamais d'exception — l'absence de Tx/Tn est un état NORMAL
(flux plus récent que la base, horizon 4 j dépassé, modèle indisponible), le
calendrier du risque s'affiche alors strictement comme avant.

`target_date` est un jour UTC (l'API daily est requêtée en UTC, comme tout le
stockage) : à l'affichage il est mis tel quel en face des jours du calendrier
(heure de Paris) — pour une valeur JOURNALIÈRE, le jour civil UTC et le jour
civil parisien coïncident, il n'y a pas d'heure à convertir."""

import os

import pandas as pd
import streamlit as st

import config as C


def t2m_signature():
    """Signature (mtime) du parquet T2m → invalide le cache à chaque collecte.
    None si le fichier n'existe pas (état normal, pas une anomalie)."""
    try:
        return os.path.getmtime(C.DB_T2M_PATH)
    except OSError:
        return None


@st.cache_data(show_spinner=False)
def load_t2m(_sig):
    """Base Tx/Tn complète (historique append-only). DataFrame vide au schéma
    T2M_SCHEMA si le fichier est absent, illisible ou sans colonne attendue."""
    if _sig is None or not os.path.exists(C.DB_T2M_PATH):
        return pd.DataFrame(columns=C.T2M_SCHEMA)
    try:
        df = pd.read_parquet(C.DB_T2M_PATH)
    except Exception:  # noqa: BLE001 — fichier corrompu/partiel : on dégrade, jamais de crash
        return pd.DataFrame(columns=C.T2M_SCHEMA)
    for col in C.T2M_SCHEMA:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[C.T2M_SCHEMA].copy()
    df["target_date"] = pd.to_datetime(df["target_date"]).dt.normalize()
    return df


@st.cache_data(show_spinner=False)
def txtn_by_day(_sig):
    """Un SEUL couple Tx/Tn par jour cible, prêt pour l'affichage :
    DataFrame [date, tx, tn, model].

    Pour chaque (modèle, jour), seule la DERNIÈRE collecte compte (l'historique
    ne sert qu'à l'archive). Puis, jour par jour, le premier modèle de
    config.T2M_MODELS ayant au moins une valeur l'emporte (Météo-France
    prioritaire, DWD ICON en secours) — jamais de mélange ni de double
    affichage pour un même jour. Les jours passés sont conservés ici (c'est le
    dernier état connu) ; le filtrage temporel appartient à l'appelant."""
    df = load_t2m(_sig)
    if df.empty:
        return pd.DataFrame(columns=["date", "tx", "tn", "model"])
    latest = (df.dropna(subset=["tx", "tn"], how="all")
                .sort_values("fetched_at")
                .groupby(["model", "target_date"], as_index=False).last())
    if latest.empty:
        return pd.DataFrame(columns=["date", "tx", "tn", "model"])
    # Rang de priorité = position dans T2M_LABELS ; un label inconnu (retiré de
    # la config après coup) est relégué derrière tous les modèles déclarés.
    rank = {label: i for i, label in enumerate(C.T2M_LABELS)}
    latest["_rank"] = latest["model"].map(lambda m: rank.get(m, len(rank)))
    best = (latest.sort_values("_rank")
                  .groupby("target_date", as_index=False).first())
    best = best.rename(columns={"target_date": "date"})
    return best[["date", "tx", "tn", "model"]].sort_values("date").reset_index(drop=True)
