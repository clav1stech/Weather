# -*- coding: utf-8 -*-
"""Couche données du flux ANNEXE d'observations INFRA-HORAIRES 6 min (parquet
séparé data/database_paris_observations_6m.parquet, produit par
fetch_observations_6m.py).

Même contrat que app/data/observations.py : lecture seule, dégradation
silencieuse (parquet absent/vide/corrompu → DataFrame vide, jamais d'exception),
stockage UTC tz-naïf converti vers l'heure de Paris à la lecture. Les 4
stations publient ce flux, avec une instrumentation inégale : RADOME
(Montsouris, Longchamp) publie tout, ETENDU (Lariboisière, Luxembourg) ne
renseigne que température et pluie 6 min (le reste NaN, structurel — cf.
fetch_observations_6m.py). Deux usages, tous deux d'AFFICHAGE : la dernière
mesure INSTANTANÉE (température, humidité, vent, pression — selon
disponibilité par station) pour rafraîchir les cartes « temps réel », et le
prolongement pointillé du graphique inter-stations au-delà de la dernière
heure consolidée du flux horaire (obs_6m_depuis, température seule — publiée
aux 4 stations). Le flux horaire (observations.py) reste la source EXCLUSIVE
de tous les CALCULS : écart ICU, Tx/Tn journaliers, min/max du jour. Un flux
6 min absent/vide/corrompu est un état NORMAL (flux plus récent que la base,
API indisponible)."""

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
def obs_6m_depuis(_sig, debut):
    """Mesures 6 min STRICTEMENT postérieures à `debut` (heure de Paris,
    tz-naïf) : le complément de fraîcheur du graphique inter-stations, au-delà
    de la dernière heure consolidée du flux horaire (qui accuse un délai de
    publication de quelques heures côté API Météo-France). La température,
    seule variable utilisée par ce complément, est publiée aux 4 stations
    (RADOME comme ETENDU) — une station sans point récent y est simplement
    absente, cas normal (poll manqué, station momentanément muette). Vide si
    rien de plus frais."""
    df = load_obs_6m(_sig)
    if df.empty or pd.isna(pd.Timestamp(debut)):
        return df.iloc[0:0]
    return df[df["valid_time"] > pd.Timestamp(debut)].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def latest_obs_6m(_sig):
    """Dernière mesure 6 min de CHAQUE station présente en base (dans l'ordre de
    config.OBS_STATIONS) — les 4 stations y figurent normalement, avec un
    contenu inégal (ETENDU : température/pluie seules, cf. module). Une
    station sans la moindre ligne 6 min est simplement absente du résultat —
    l'appelant retombe alors sur l'observation horaire."""
    df = load_obs_6m(_sig)
    if df.empty:
        return df
    last = (df.sort_values("valid_time")
              .groupby("station_id", as_index=False).last())
    order = {sid: i for i, sid in enumerate(C.OBS_STATION_IDS)}
    return (last.assign(_ord=last["station_id"].map(order))
                .sort_values("_ord").drop(columns="_ord").reset_index(drop=True))
