# -*- coding: utf-8 -*-
"""Couche données : lecture de la base plate unique produite par Forecast.py
(data/database_paris.parquet : [run_date, model, member, valid_time, t850…])
et conversions run_date ↔ cycle synoptique UTC.

Invariant : stockage en UTC tz-naïf, conversion vers l'heure de Paris SEULEMENT
à l'affichage (ici, dès load_db, car tout le dashboard est de l'affichage) ;
les cycles réels (0/6/12/18Z) se retrouvent via utc_cycle()."""

import os

import pandas as pd
import streamlit as st

import config as C
from app.runtime import LOCAL_TZ


def db_signature():
    """Signature (mtimes hot + archive) → invalide le cache au moindre nouveau
    run comme après un rollover hot/cold. Tant que l'archive n'existe pas
    (archivage canicule non activé), seule la composante hot varie — clé de
    cache opaque, aucun appelant n'en inspecte le contenu."""
    sigs = []
    for path in (C.DB_PATH, C.DB_ARCHIVE_PATH):
        try:
            sigs.append(os.path.getmtime(path))
        except OSError:
            sigs.append(None)
    return None if sigs[0] is None else tuple(sigs)


@st.cache_data(show_spinner=False)
def load_db(_sig):
    """Base complète. run_date / valid_time convertis UTC → heure de Paris (naïf).

    Si le parquet COLD de l'archivage hot/cold existe (rollover canicule activé
    un jour — cf. config.DB_ARCHIVE_PATH), il est concaténé AVANT le hot : la
    base vue du dashboard reste l'historique ENTIER, aucun run archivé ne
    disparaît d'Explorer/Contrôle et les harnais de non-régression restent
    identiques avant/après un rollover. Archive absente (cas actuel) →
    comportement strictement inchangé. La lecture hot-seul des pages
    interactives (gain mémoire, design §3) est un chantier ultérieur distinct.

    Filtre aussi les modèles legacy qui auraient pu rester dans un parquet plus
    ancien (ex. AIGEFS/ICON retirés de config.MODELS) — évite tout crash sur des
    lignes orphelines sans couleur/config déclarée."""
    if _sig is None or not os.path.exists(C.DB_PATH):
        return pd.DataFrame(columns=C.SCHEMA)
    df = pd.read_parquet(C.DB_PATH)
    if os.path.exists(C.DB_ARCHIVE_PATH):
        archive = pd.read_parquet(C.DB_ARCHIVE_PATH)
        df = pd.concat([archive, df], ignore_index=True)
        # Recouvrement hot/archive impossible après un rollover sain, mais la
        # lecture ne doit pas en dépendre : dédup défensive, hot prioritaire.
        df = df.drop_duplicates(subset=["run_date", "model", "member", "valid_time"],
                                keep="last")
    df = df[df["model"].isin(C.MODEL_LABELS)].reset_index(drop=True)
    for col in ("run_date", "valid_time"):
        s = pd.to_datetime(df[col])
        df[col] = (s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None))
    return df


def utc_cycle(local_run_date):
    """Reconvertit un run_date stocké en heure locale Paris vers son instant UTC
    réel — nécessaire pour retrouver le vrai cycle synoptique (0/6/12/18Z)."""
    return pd.Timestamp(local_run_date).tz_localize(LOCAL_TZ).tz_convert("UTC")


def _run_utc_naive(local_run_date):
    """Cycle synoptique UTC (0/6/12/18Z), tz-naïf — même convention que le
    run_date legacy parsé depuis l'en-tête Météociel, donc directement comparable."""
    return utc_cycle(local_run_date).replace(tzinfo=None)


def run_label_text(local_run_date):
    """Nom du run d'après son vrai cycle UTC, ex. « 30 Jun 2026 — 06Z » — jamais
    l'heure locale, qui ne correspond à aucun cycle synoptique réel."""
    u = utc_cycle(local_run_date)
    return f"{u:%d %b %Y} — {u.hour:02d}Z"


@st.cache_data(show_spinner=False)
def list_runs(_sig):
    """Runs disponibles (run_date distinctes), du plus récent au plus ancien."""
    df = load_db(_sig)
    if df.empty:
        return pd.DataFrame(columns=["run_date", "label"])
    runs = pd.DataFrame({"run_date": sorted(df["run_date"].unique(), reverse=True)})
    runs["label"] = runs["run_date"].apply(run_label_text)
    return runs.reset_index(drop=True)


def run_slice(_sig, run_date):
    df = load_db(_sig)
    return df[df["run_date"] == run_date]
