# -*- coding: utf-8 -*-
"""Couche données du flux ANNEXE prévision Montsouris « vintages » 15 min
(parquet séparé data/database_paris_montsouris_vintages.parquet, produit par
fetch_montsouris_vintages.py).

Lecture seule, dégradation silencieuse : parquet absent, vide ou corrompu →
DataFrame vide au schéma VINTAGE_SCHEMA, jamais d'exception — l'absence de ce
flux est un état NORMAL (flux plus récent que le dashboard, collecte pas encore
lancée), la page qui le consommera s'affiche alors strictement comme sans lui.

Une ligne = un « vintage » : le couple (valid_time, fetched_at). Plusieurs
vintages coexistent pour une même échéance (valid_time) — c'est tout l'intérêt
du flux (comparer les prévisions émises à divers instants). `valid_time` et
`fetched_at` sont des instants UTC tz-naïfs (comme tout le stockage) ; la
conversion vers l'heure de Paris n'a lieu qu'à l'affichage."""

import os

import pandas as pd
import streamlit as st

import config as C


def vintages_signature():
    """Signature (mtime) du parquet vintages → invalide le cache à chaque
    collecte. None si le fichier n'existe pas (état normal, pas une anomalie)."""
    try:
        return os.path.getmtime(C.DB_VINTAGE_PATH)
    except OSError:
        return None


@st.cache_data(show_spinner=False)
def load_vintages(_sig):
    """Base vintages complète (append-only, bornée par compaction). DataFrame
    vide au schéma VINTAGE_SCHEMA si le fichier est absent, illisible ou sans
    colonne attendue. `valid_time`/`fetched_at` normalisés en datetime."""
    if _sig is None or not os.path.exists(C.DB_VINTAGE_PATH):
        return pd.DataFrame(columns=C.VINTAGE_SCHEMA)
    try:
        df = pd.read_parquet(C.DB_VINTAGE_PATH)
    except Exception:  # noqa: BLE001 — fichier corrompu/partiel : on dégrade, jamais de crash
        return pd.DataFrame(columns=C.VINTAGE_SCHEMA)
    for col in C.VINTAGE_SCHEMA:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[C.VINTAGE_SCHEMA].copy()
    df["valid_time"] = pd.to_datetime(df["valid_time"])
    df["fetched_at"] = pd.to_datetime(df["fetched_at"])
    return df
