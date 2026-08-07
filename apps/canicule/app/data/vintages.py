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

import pandas as pd
import streamlit as st

import config as C
from app.data.store import flux_signature, load_flux


def vintages_signature():
    """Signature du flux vintages → invalide le cache à chaque collecte. None si
    le flux n'existe pas encore. Bascule mtime git ↔ etags du magasin selon
    WEATHER_STORE, de façon transparente."""
    return flux_signature(C.DB_VINTAGE_PATH)


@st.cache_data(show_spinner=False)
def load_vintages(_sig):
    """Base vintages complète (append-only, bornée par compaction). DataFrame
    vide au schéma VINTAGE_SCHEMA si le flux est absent, illisible ou sans
    colonne attendue. `valid_time`/`fetched_at` normalisés en datetime."""
    if _sig is None:
        return pd.DataFrame(columns=C.VINTAGE_SCHEMA)
    df = load_flux(C.DB_VINTAGE_PATH, C.VINTAGE_SCHEMA)
    if df.empty:
        return df
    for col in C.VINTAGE_SCHEMA:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[C.VINTAGE_SCHEMA].copy()
    df["valid_time"] = pd.to_datetime(df["valid_time"])
    df["fetched_at"] = pd.to_datetime(df["fetched_at"])
    return df
