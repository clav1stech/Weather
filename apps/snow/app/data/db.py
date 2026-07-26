# -*- coding: utf-8 -*-
"""Couche données du dashboard neige : lecture des parquets produits par
apps/snow/pipeline/ (ensemble global, PNT Météo-France local et maille fine),
en LECTURE SEULE.

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


def mf_local_signature():
    return _signature(SC.DB_MF_LOCAL_PATH)


def mf_regional_signature():
    return _signature(SC.DB_MF_REGIONAL_PATH)


def mf_summary_signature():
    return _signature(SC.DB_MF_SUMMARY_PATH)


def _to_paris(df, cols):
    for col in cols:
        s = pd.to_datetime(df[col])
        df[col] = s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return df


def _align_schema(df, schema):
    """Réaligne une base historique sur le schéma courant sans la réécrire.

    Une variable ajoutée après le début de la collecte reste ``NaN`` sur
    l'historique : absence explicite, jamais zéro inventé ni migration
    rétroactive des parquets.
    """
    df = df.copy()
    for col in schema:
        if col not in df.columns:
            df[col] = float("nan")
    return df[schema]


def _read_ens(path):
    """Un parquet au schéma ensemble → DataFrame filtré (labels de config) et
    converti en heure de Paris. Absent/corrompu → vide, dégradation silencieuse."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=SC.ENS_SCHEMA)
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 — parquet corrompu = dégradation silencieuse
        return pd.DataFrame(columns=SC.ENS_SCHEMA)
    df = _align_schema(df, SC.ENS_SCHEMA)
    df = df[df["model"].isin(SC.ENS_LABELS + SC.MEAN_LABELS)].reset_index(drop=True)
    return _to_paris(df, ("run_date", "valid_time"))


def _read_mf_local(path):
    """Parquet PNT Météo-France local, tolérant au schéma progressif."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=SC.MF_LOCAL_SCHEMA)
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 — absence explicite dans l'UI en aval
        return pd.DataFrame(columns=SC.MF_LOCAL_SCHEMA)
    df = _align_schema(df, SC.MF_LOCAL_SCHEMA)
    return _to_paris(df, ("run_date", "valid_time"))


def _read_mf_regional(path):
    """Parquet PE-ARPEGE dédié, absent tant que le flux n'a pas tourné."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=SC.MF_REGIONAL_SCHEMA)
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=SC.MF_REGIONAL_SCHEMA)
    df = _align_schema(df, SC.MF_REGIONAL_SCHEMA)
    return _to_paris(df, ("run_date", "valid_time"))


def _read_mf_summary(path):
    """Archive moyenne compacte, tolérante aux futures colonnes ajoutées."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=SC.MF_SUMMARY_SCHEMA)
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=SC.MF_SUMMARY_SCHEMA)
    df = _align_schema(df, SC.MF_SUMMARY_SCHEMA)
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
    df = _align_schema(df, SC.HD_SCHEMA)
    return _to_paris(df, ("fetched_at", "target_datetime"))


@st.cache_data(show_spinner=False)
def load_mf_local(_sig):
    """Runs locaux/régionaux HOT ; l'absence du nouveau parquet est normale."""
    return _read_mf_local(SC.DB_MF_LOCAL_PATH)


@st.cache_data(show_spinner=False)
def load_mf_regional(_sig):
    """Runs PE-ARPEGE HOT ; le parquet dédié peut ne pas encore exister."""
    return _read_mf_regional(SC.DB_MF_REGIONAL_PATH)


@st.cache_data(show_spinner=False)
def load_mf_summary(_sig):
    """Historique compact des moyennes PI/IFS/PE-AROME/PE-ARPEGE."""
    return _read_mf_summary(SC.DB_MF_SUMMARY_PATH)


def mf_local_members(_sig):
    """Membres PE-AROME au village, sans les déterministes PI/IFS futurs."""
    df = load_mf_local(_sig)
    return df[(df["kind"] == "member") & (df["model"] == SC.PE_AROME_MODEL)]


def latest_mf_local_members(_sig):
    """Dernier cycle PE-AROME complet stocké, sans mélanger les runs."""
    df = mf_local_members(_sig)
    if df.empty:
        return df
    return df[df["run_date"] == df["run_date"].max()].reset_index(drop=True)


def latest_mf_regional_members(_sig):
    """Dernier cycle PE-ARPEGE complet stocké, sans mélanger les runs."""
    df = load_mf_regional(_sig)
    df = df[(df["kind"] == "member") & (df["model"] == SC.PE_ARPEGE_MODEL)]
    if df.empty:
        return df
    return df[df["run_date"] == df["run_date"].max()].reset_index(drop=True)


def latest_mf_local_deterministic(_sig, model):
    """Dernier cycle d'un modèle local déterministe, sans mélanger les runs.

    Le filtre de fraîcheur météorologique reste à la logique du domaine :
    cette couche de lecture ne transforme jamais un vieux cycle en prévision
    courante et ne substitue jamais silencieusement un autre modèle.
    """
    if model not in SC.MF_LOCAL_MODELS:
        raise ValueError(f"Modèle Météo-France local inconnu : {model}")
    df = load_mf_local(_sig)
    df = df[(df["kind"] == "deterministic") & (df["model"] == model)]
    if df.empty:
        return df
    return df[df["run_date"] == df["run_date"].max()].reset_index(drop=True)


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
