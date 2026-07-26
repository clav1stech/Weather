# -*- coding: utf-8 -*-
"""Couche données du flux ANNEXE observations Météo-France Alpes du Nord
(parquet séparé apps/snow/data/db_obs_alpes.parquet, produit par
apps/snow/pipeline/fetch_observations.py). LECTURE SEULE.

Dégradation silencieuse : parquet absent, vide, corrompu ou partiel →
DataFrame vide, jamais d'exception — l'absence d'observations est un état
NORMAL (flux plus récent que la base, API indisponible, clé non configurée).
Stockage UTC tz-naïf, conversion vers l'heure de Paris dès load_obs (tout le
dashboard est de l'affichage). L'instrumentation varie par station (hauteur de
neige, vent… publiés par un sous-ensemble) : NaN structurel, toute statistique
est tolérante aux NaN. Le parquet COLD éventuel (rollover hot/cold) est relu
et concaténé : l'historique reste entier à l'affichage."""

import os

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from ..runtime import LOCAL_TZ


def obs_signature():
    """Signature (mtimes hot+cold) → invalide le cache à chaque collecte ou
    rollover. None si aucun fichier (état normal, pas une anomalie)."""
    sigs = []
    for path in (SC.DB_OBS_PATH, SC.DB_OBS_COLD_PATH):
        try:
            sigs.append(os.path.getmtime(path))
        except OSError:
            sigs.append(None)
    return None if all(s is None for s in sigs) else tuple(sigs)


def _read(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=SC.OBS_SCHEMA)
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 — fichier corrompu/partiel : on dégrade, jamais de crash
        return pd.DataFrame(columns=SC.OBS_SCHEMA)
    for col in SC.OBS_SCHEMA:
        if col not in df.columns:
            df[col] = pd.NA
    return df[SC.OBS_SCHEMA]


@st.cache_data(show_spinner=False)
def load_obs(_sig):
    """Base observations complète (cold + hot, historique append-only),
    valid_time converti UTC → heure de Paris (naïf). Stations retirées de la
    config après coup : lignes orphelines filtrées (même principe que load_db
    avec les modèles legacy)."""
    if _sig is None:
        return pd.DataFrame(columns=SC.OBS_SCHEMA)
    df = pd.concat([_read(SC.DB_OBS_COLD_PATH), _read(SC.DB_OBS_PATH)],
                   ignore_index=True)
    if df.empty:
        return df
    # Recouvrement hot/cold impossible après un rollover sain, mais la lecture
    # ne doit pas dépendre de cette bonne conduite : dédup défensive.
    df = (df.drop_duplicates(subset=["station_id", "valid_time"], keep="last")
            .reset_index(drop=True))
    df = df[df["station_id"].isin(SC.OBS_STATION_IDS)].reset_index(drop=True)
    s = pd.to_datetime(df["valid_time"])
    df["valid_time"] = s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return df.sort_values(["valid_time", "station_id"]).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def latest_obs(_sig):
    """Dernière observation de CHAQUE station (une ligne par station, dans
    l'ordre de config). Une station sans la moindre ligne en base est
    simplement absente du résultat — l'appelant affiche ce qu'il a."""
    df = load_obs(_sig)
    if df.empty:
        return df
    last = (df.sort_values("valid_time")
              .groupby("station_id", as_index=False).last())
    order = {sid: i for i, sid in enumerate(SC.OBS_STATION_IDS)}
    return (last.assign(_ord=last["station_id"].map(order))
                .sort_values("_ord").drop(columns="_ord").reset_index(drop=True))


def obs_window(_sig, hours):
    """Observations des `hours` dernières heures (depuis la dernière obs en
    base, pas depuis l'horloge — une base qui date de la veille reste lisible)."""
    df = load_obs(_sig)
    if df.empty:
        return df
    end = df["valid_time"].max()
    return df[df["valid_time"] > end - pd.Timedelta(hours=hours)].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def daily_txtn_obs(_sig):
    """Tx/Tn OBSERVÉS par (station, jour civil de Paris) : max des tx horaires /
    min des tn horaires (tx/tn API = extrêmes de l'heure écoulée). `n_heures` =
    nombre d'observations du jour — l'appelant juge la complétude via
    OBS_JOUR_COMPLET_MIN_H (un jour troué donnerait un faux extrême)."""
    df = load_obs(_sig)
    cols = ["station_id", "station_nom", "date", "tx", "tn", "n_heures"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df.assign(date=df["valid_time"].dt.normalize())
    out = (df.groupby(["station_id", "station_nom", "date"], as_index=False)
             .agg(tx=("tx", "max"), tn=("tn", "min"), n_heures=("valid_time", "count")))
    return out[cols].sort_values(["date", "station_id"]).reset_index(drop=True)
