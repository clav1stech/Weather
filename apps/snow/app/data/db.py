# -*- coding: utf-8 -*-
"""Couche données du dashboard neige : lecture des deux parquets produits par
apps/snow/pipeline/ (flux ensemble db_megeve.parquet, flux maille fine
db_megeve_hd.parquet), en LECTURE SEULE.

Invariants : stockage en UTC tz-naïf, conversion vers l'heure de Paris
SEULEMENT à l'affichage (ici, dès load_db — tout le dashboard est de
l'affichage) ; les cycles réels (0/6/12/18Z) se retrouvent via utc_cycle().
Parquet absent/vide/corrompu → DataFrame vide, dégradation silencieuse
(jamais un crash, jamais une alerte intrusive)."""

import os

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from ..runtime import LOCAL_TZ


def _signature(path):
    """Signature (mtime) d'un parquet → invalide le cache au moindre run."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def db_signature():
    """Signature combinée hot + cold (rollover hot/cold) : le cache s'invalide
    aussi bien après une collecte qu'après une bascule d'archive."""
    return (_signature(SC.DB_ENS_PATH), _signature(SC.DB_ENS_COLD_PATH))


def hd_signature():
    return _signature(SC.DB_HD_PATH)


def _to_paris(df, cols):
    for col in cols:
        s = pd.to_datetime(df[col])
        df[col] = s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return df


def _read_ens(path):
    """Un parquet au schéma ensemble → DataFrame filtré (labels de config) et
    converti en heure de Paris. Absent/corrompu → vide, dégradation silencieuse."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=SC.ENS_SCHEMA)
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 — parquet corrompu = dégradation silencieuse
        return pd.DataFrame(columns=SC.ENS_SCHEMA)
    df = df[df["model"].isin(SC.ENS_LABELS + SC.MEAN_LABELS)].reset_index(drop=True)
    return _to_paris(df, ("run_date", "valid_time"))


@st.cache_data(show_spinner=False)
def load_db(_sig):
    """Base ensemble HOT (membres + mean/spread, colonne `kind`) — le fichier
    que le pipeline écrit, fenêtre récente bornée par le rollover. C'est le
    support des vues interactives ; l'historique archivé se lit via load_cold
    (à la demande, pages qui en ont besoin seulement)."""
    return _read_ens(SC.DB_ENS_PATH)


@st.cache_data(show_spinner=False)
def load_cold(_sig):
    """Base ensemble COLD (archive du rollover hot/cold) — chargée seulement
    par les usages qui remontent loin (historique _MEAN de la convergence).
    Absente = cas normal (rollover jamais encore déclenché)."""
    return _read_ens(SC.DB_ENS_COLD_PATH)


@st.cache_data(show_spinner=False)
def load_hd(_sig):
    """Base maille fine (append-only). fetched_at / target_datetime convertis
    UTC → heure de Paris (naïf)."""
    if _sig is None or not os.path.exists(SC.DB_HD_PATH):
        return pd.DataFrame(columns=SC.HD_SCHEMA)
    try:
        df = pd.read_parquet(SC.DB_HD_PATH)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=SC.HD_SCHEMA)
    return _to_paris(df, ("fetched_at", "target_datetime"))


def members_db(_sig):
    """Lignes membres (flux Ensemble API) — le pool des vues probabilistes."""
    df = load_db(_sig)
    return df[df["kind"] == "member"]


def mean_db(_sig, kind="mean"):
    """Lignes mean (ou spread) du flux Ensemble Mean — rétention API longue,
    support de l'historique/convergence : lit hot + cold (l'historique de
    convergence doit traverser la fenêtre du rollover). Dédup défensive au
    cas où un crash de rollover aurait laissé un recouvrement (hot prioritaire)."""
    df = pd.concat([load_cold(_sig), load_db(_sig)], ignore_index=True)
    df = df.drop_duplicates(subset=["run_date", "model", "kind", "member",
                                    "site", "valid_time"], keep="last")
    return df[df["kind"] == kind]


def utc_cycle(local_run_date):
    """Reconvertit un run_date affiché (heure de Paris) vers son instant UTC
    réel — nécessaire pour retrouver le vrai cycle synoptique (0/6/12/18Z)."""
    return pd.Timestamp(local_run_date).tz_localize(LOCAL_TZ).tz_convert("UTC")


def run_label_text(local_run_date):
    """Nom du run d'après son vrai cycle UTC (jamais l'heure locale)."""
    u = utc_cycle(local_run_date)
    return f"{u:%d %b %Y} — {u.hour:02d}Z"


@st.cache_data(show_spinner=False)
def list_runs(_sig):
    """Runs membres disponibles (run_date distinctes), du plus récent au plus
    ancien — les runs _MEAN ne pilotent pas la navigation (flux d'appui)."""
    df = members_db(_sig)
    if df.empty:
        return pd.DataFrame(columns=["run_date", "label"])
    runs = pd.DataFrame({"run_date": sorted(df["run_date"].unique(), reverse=True)})
    runs["label"] = runs["run_date"].apply(run_label_text)
    return runs.reset_index(drop=True)
