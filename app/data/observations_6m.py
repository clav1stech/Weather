# -*- coding: utf-8 -*-
"""Couche données du flux ANNEXE d'observations INFRA-HORAIRES 6 min (parquet
séparé data/database_paris_observations_6m.parquet, produit par
fetch_observations_6m.py).

Même contrat que app/data/observations.py : lecture seule, dégradation
silencieuse (parquet absent/vide/corrompu → DataFrame vide, jamais d'exception),
stockage UTC tz-naïf converti vers l'heure de Paris à la lecture. SEUL usage :
fournir la dernière mesure INSTANTANÉE (température, humidité, vent, pression)
des stations RADOME pour rafraîchir les cartes « temps réel » — le flux horaire
(observations.py) reste l'unique source de la comparaison inter-stations et des
Tx/Tn journaliers. L'absence de 6 min est un état NORMAL (stations ETENDU sans
ce produit, flux plus récent que la base, API indisponible)."""

import os

import pandas as pd
import streamlit as st

import config as C
from app.runtime import LOCAL_TZ


def obs_6m_signature():
    """Signature (mtime) du parquet 6 min → invalide le cache à chaque collecte.
    None si le fichier n'existe pas (état normal, pas une anomalie)."""
    try:
        return os.path.getmtime(C.DB_OBS_6M_PATH)
    except OSError:
        return None


@st.cache_data(show_spinner=False)
def load_obs_6m(_sig):
    """Base 6 min complète, valid_time converti UTC → heure de Paris (naïf).
    DataFrame vide au schéma OBS_6M_SCHEMA si le fichier est absent, illisible
    ou sans colonne attendue."""
    if _sig is None or not os.path.exists(C.DB_OBS_6M_PATH):
        return pd.DataFrame(columns=C.OBS_6M_SCHEMA)
    try:
        df = pd.read_parquet(C.DB_OBS_6M_PATH)
    except Exception:  # noqa: BLE001 — fichier corrompu/partiel : on dégrade, jamais de crash
        return pd.DataFrame(columns=C.OBS_6M_SCHEMA)
    for col in C.OBS_6M_SCHEMA:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[C.OBS_6M_SCHEMA].copy()
    df = df[df["station_id"].isin(C.OBS_STATION_IDS)].reset_index(drop=True)
    s = pd.to_datetime(df["valid_time"])
    df["valid_time"] = s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return df


@st.cache_data(show_spinner=False)
def latest_obs_6m(_sig):
    """Dernière mesure 6 min de CHAQUE station présente en base (dans l'ordre de
    config.OBS_STATIONS). Seules les stations RADOME y figurent en pratique ;
    une station sans 6 min est simplement absente du résultat — l'appelant
    retombe alors sur l'observation horaire."""
    df = load_obs_6m(_sig)
    if df.empty:
        return df
    last = (df.sort_values("valid_time")
              .groupby("station_id", as_index=False).last())
    order = {sid: i for i, sid in enumerate(C.OBS_STATION_IDS)}
    return (last.assign(_ord=last["station_id"].map(order))
                .sort_values("_ord").drop(columns="_ord").reset_index(drop=True))
