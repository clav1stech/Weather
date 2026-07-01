# -*- coding: utf-8 -*-
"""
Dashboard météo — Prévisions d'ensemble (Paris)
================================================
Lit la base plate unique produite par Forecast.py
(data/database_paris.parquet : [run_date, model, member, valid_time, t850…]).

Toutes les statistiques (super-ensemble, médianes par modèle, divergence, risque
canicule, convergence run-après-run) sont recalculées à la volée depuis la matrice
globale des membres, avec des opérations tolérantes aux NaN — l'horizon 16 j
s'affiche proprement même quand le nombre de membres actifs chute après ~7,5 j.

Config-driven : modèles, variables, climatologie et seuils vivent dans config.py.
"""

import os
import re
import sys
import glob
import shutil
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import config as C
import run_dual
import Forecast as F  # persist() : fusion validée, anti-régression, écriture atomique
import validate_cross_pipeline as V  # helpers de lecture des xlsx legacy (Météociel)

APP_VERSION = "2.1.1"
LOCAL_TZ = ZoneInfo("Europe/Paris")
VAR = C.PRIMARY_VAR  # variable principale affichée (t850)

# --------------------------------------------------------------------------- #
#  Détection local / cloud (le bouton « run » n'a de sens qu'en local)
# --------------------------------------------------------------------------- #
def _detect_local():
    forced = os.environ.get("WEATHER_LOCAL")
    if forced in ("0", "1"):
        return forced == "1"
    base = C.BASE_DIR.replace("\\", "/")
    on_cloud = base.startswith("/mount/src") or \
        os.environ.get("HOSTNAME", "").startswith("streamlit")
    return not on_cloud


IS_LOCAL = _detect_local()

st.set_page_config(page_title="Dashboard Météo — Ensembles Paris",
                   page_icon="🌡️", layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
      h1, h2, h3 {letter-spacing: -0.01em;}
      div[data-testid="stMetric"] {
          background: rgba(128,138,157,0.10);
          border: 1px solid rgba(128,138,157,0.25);
          border-radius: 12px; padding: 12px 16px;}
      .stPlotlyChart {border: 1px solid rgba(128,138,157,0.22); border-radius: 12px; padding: 4px;}
    </style>
    """,
    unsafe_allow_html=True,
)

_PCT_COLS = ["Min", "P10", "P25", "Médiane", "P75", "P90", "Max"]


# --------------------------------------------------------------------------- #
#  Thème clair / sombre — adapte graphiques et cartes au thème actif
# --------------------------------------------------------------------------- #
def _is_dark():
    """Le thème sombre est-il actif ? Gère le mode auto/système (Streamlit récent)
    via st.context.theme, avec repli sur la base configurée dans config.toml."""
    try:
        theme = st.context.theme
        if theme is not None and getattr(theme, "type", None):
            return theme.type == "dark"
    except Exception:  # noqa: BLE001
        pass
    try:
        return (st.get_option("theme.base") or "light").lower() == "dark"
    except Exception:  # noqa: BLE001
        return False


def _plotly_template():
    """Template Plotly cohérent avec le thème courant. Template ET couleurs d'encre
    (cf. _ink) partagent _is_dark() : même si la détection ne colle pas exactement à
    la page, le graphique reste lisible car ses fonds et traits restent cohérents."""
    return "plotly_dark" if _is_dark() else "plotly_white"


def _ink(dark=None):
    """Couleur des traits/textes forts (médiane, contrôle, axe zéro), lisible quel
    que soit le thème : ardoise sur fond clair, presque-blanc sur fond sombre."""
    if dark is None:
        dark = _is_dark()
    return "#E6E9EE" if dark else "#2C3E50"


# --------------------------------------------------------------------------- #
#  Couche données : lecture de la base unique
# --------------------------------------------------------------------------- #
def db_signature():
    """Signature (mtime) du fichier → invalide le cache au moindre nouveau run."""
    try:
        return os.path.getmtime(C.DB_PATH)
    except OSError:
        return None


@st.cache_data(show_spinner=False)
def load_db(_sig):
    """Base complète. run_date / valid_time convertis UTC → heure de Paris (naïf).

    Filtre aussi les modèles legacy qui auraient pu rester dans un parquet plus
    ancien (ex. AIGEFS/ICON retirés de config.MODELS) — évite tout crash sur des
    lignes orphelines sans couleur/config déclarée."""
    if _sig is None or not os.path.exists(C.DB_PATH):
        return pd.DataFrame(columns=C.SCHEMA)
    df = pd.read_parquet(C.DB_PATH)
    df = df[df["model"].isin(C.MODEL_LABELS)].reset_index(drop=True)
    for col in ("run_date", "valid_time"):
        s = pd.to_datetime(df[col])
        df[col] = (s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None))
    return df


def utc_cycle(local_run_date):
    """Reconvertit un run_date stocké en heure locale Paris vers son instant UTC
    réel — nécessaire pour retrouver le vrai cycle synoptique (0/6/12/18Z)."""
    return pd.Timestamp(local_run_date).tz_localize(LOCAL_TZ).tz_convert("UTC")


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


# Tolérance (heures) entre la portée RÉELLE d'un run stocké (max valid_time −
# run_date) et l'horizon nominal du modèle (config `horizon_h`), pour juger qu'un
# run atteint « l'horizon plein ». Assez large pour absorber le pas d'échéance et
# une légère avance de coupure, assez serré pour écarter les cycles courts.
FULL_HORIZON_TOLERANCE_H = 24


@st.cache_data(show_spinner=False)
def latest_complete_run_sub(_sig, as_of=None):
    """Pool multi-modèles où CHAQUE modèle est représenté par son dernier run à
    HORIZON PLEIN — base des vues combinées (super-ensemble global).

    `as_of` (run_date local, optionnel) : rejoue la sélection telle qu'elle
    était à ce cycle — seuls les runs `run_date ≤ as_of` sont considérés, la
    logique de complétude reste identique. Sert au sélecteur « Vu depuis » de
    la Vue d'ensemble (versions antérieures du super-ensemble).

    La complétude se mesure EMPIRIQUEMENT sur la portée réelle du run stocké
    (max valid_time − run_date ≥ horizon_h − tolérance), jamais par une règle
    codée en dur sur l'heure de cycle : un 6Z/18Z qui atteint réellement le plein
    horizon est donc éligible, et un 0Z/12Z anormalement court est écarté (cf.
    invariant : l'horizon réel d'un cycle varie d'un jour à l'autre). Chaque
    modèle garde son propre run_date/cycle — aucun cycle global partagé.

    Modèles sans `horizon_h` déclaré (ex. GEM) : complétude non jugeable → on
    retient leur dernier run non vide (à leurs cycles réels, jamais backfillé).
    Repli général : si aucun run n'atteint l'horizon plein, on prend le dernier
    run non vide (le modèle reste présent, signalé « horizon réduit »).

    Retourne (sub, sources, partial) :
      - sub     : lignes poolées (mêmes colonnes que la base) ;
      - sources : {label → run_date retenu} ;
      - partial : modèles principaux sans aucun run à horizon plein récent."""
    df = load_db(_sig)
    if as_of is not None:
        df = df[df["run_date"] <= pd.Timestamp(as_of)]
    if df.empty:
        return df, {}, []
    frames, sources, partial = [], {}, []
    for model in C.MODELS:
        label = model["label"]
        mdf = df[df["model"] == label]
        if mdf.empty:
            continue
        horizon = model.get("horizon_h")
        chosen, fallback = None, None
        for rd in sorted(mdf["run_date"].unique(), reverse=True):
            valid = mdf[(mdf["run_date"] == rd)].dropna(subset=[VAR])
            if valid.empty:
                continue
            if fallback is None:
                fallback = rd  # dernier run non vide, quel que soit son horizon
            if horizon is None:
                chosen = rd   # complétude non jugeable → plus récent non vide
                break
            reach_h = (valid["valid_time"].max() - pd.Timestamp(rd)) / pd.Timedelta(hours=1)
            if reach_h >= horizon - FULL_HORIZON_TOLERANCE_H:
                chosen = rd
                break
        if chosen is None:
            chosen = fallback
            if chosen is not None and horizon is not None:
                partial.append(label)  # aucun run à horizon plein → repli signalé
        if chosen is None:
            continue
        frames.append(mdf[mdf["run_date"] == chosen])
        sources[label] = chosen
    if not frames:
        return df.iloc[0:0], sources, partial
    return pd.concat(frames, ignore_index=True), sources, partial


@st.cache_data(show_spinner=False)
def latest_run_sub(_sig):
    """Pool « dernier run » : pour CHAQUE modèle, son dernier run non vide, quel
    que soit son cycle (0/6/12/18Z) — contrairement aux vues combinées
    (latest_complete_run_sub), AUCUNE exigence d'horizon plein : on montre
    l'information la plus fraîche disponible, même partielle. Chaque modèle
    garde son propre run_date/cycle. Retourne (sub, sources)."""
    df = load_db(_sig)
    if df.empty:
        return df, {}
    frames, sources = [], {}
    for label in C.MODEL_LABELS:
        mdf = df[df["model"] == label]
        valid = mdf.dropna(subset=[VAR])
        if valid.empty:
            continue
        rd = valid["run_date"].max()
        frames.append(mdf[mdf["run_date"] == rd])
        sources[label] = rd
    if not frames:
        return df.iloc[0:0], sources
    return pd.concat(frames, ignore_index=True), sources


def complete_runs_caption(sources):
    """Légende « Modèle cycle » listant, par modèle, le run retenu (ordre config)."""
    parts = [f"{label} {run_label_text(sources[label])}"
             for label in C.MODEL_LABELS if label in sources]
    return " · ".join(parts)


def main_labels_expected_at(run_date):
    """Modèles principaux attendus au cycle synoptique de `run_date`.
    Utilise `expected_cycles` (config) — ex. ECMWF attendu seulement à 0Z/12Z,
    donc absent à 6Z/18Z sans déclencher d'alerte."""
    h = utc_cycle(run_date).hour
    return [m for m in C.MAIN_LABELS if h in C.EXPECTED_CYCLES_BY_LABEL.get(m, [])]


def user_tz():
    """Fuseau horaire du NAVIGATEUR de l'utilisateur (st.context), repli sur
    l'heure de Paris. Sert aux horodatages « temps réel » (rafraîchissement) —
    les données météo, elles, restent affichées en heure de Paris (LOCAL_TZ)."""
    try:
        tz = st.context.timezone
        if tz:
            return ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        pass
    return LOCAL_TZ


def latest_refresh_status(runs, sig):
    """Heure du dernier rafraîchissement (mtime du parquet, dans le fuseau de
    l'utilisateur — jamais l'heure du serveur, qui est UTC sur le cloud) et
    complétude (tous les modèles principaux ATTENDUS À CE CYCLE présents ou
    non) du dernier run."""
    if runs.empty:
        return None, True, []
    try:
        refreshed_at = datetime.fromtimestamp(os.path.getmtime(C.DB_PATH), tz=user_tz())
    except OSError:
        refreshed_at = None
    last_rd = runs.iloc[0]["run_date"]
    present = set(run_slice(sig, last_rd)["model"].unique())
    expected = main_labels_expected_at(last_rd)
    missing = [m for m in expected if m not in present]
    return refreshed_at, not missing, missing


# --------------------------------------------------------------------------- #
#  Climatologie (normale saisonnière en cosinus) & anomalie
# --------------------------------------------------------------------------- #
# Les 3 paramètres par défaut (config.CLIM_MEAN/AMPLITUDE/PEAK_DOY) sont une
# ESTIMATION, pas une normale officielle issue d'une série climatologique réelle
# — ajustables ci-dessous (page Indicateur de canicule → Réglages avancés),
# stockés en session pour s'appliquer partout (KPI, graphiques) tant que l'appli
# reste ouverte.
def clim_params():
    """(moyenne, amplitude, jour du pic) effectifs — session si ajustés, sinon config.py."""
    return (
        st.session_state.get("clim_mean", C.CLIM_MEAN),
        st.session_state.get("clim_amplitude", C.CLIM_AMPLITUDE),
        st.session_state.get("clim_peak_doy", C.CLIM_PEAK_DOY),
    )


def clim_normal(when):
    """Normale climatique T850 saisonnière (cosinus). `when` : Timestamp ou Series."""
    mean, amplitude, peak_doy = clim_params()
    doy = pd.to_datetime(when)
    doy = doy.dt.dayofyear if hasattr(doy, "dt") else doy.dayofyear
    return mean + amplitude * np.cos(2 * np.pi * (doy - peak_doy) / 365.25)


# --------------------------------------------------------------------------- #
#  Matrices de membres & statistiques (tolérantes NaN)
# --------------------------------------------------------------------------- #
def member_matrix(sub):
    """Pivot membres : index=valid_time, colonnes=(model, member). Tri temporel."""
    if sub.empty:
        return None
    piv = sub.pivot_table(index="valid_time", columns=["model", "member"], values=VAR)
    return piv.sort_index()


def super_ensemble(sub):
    """Super-ensemble : stats par échéance sur TOUS les membres poolés (multi-modèles).

    Médiane / P10 / P90 / Min / Max / Spread + écart-type, proba de dépassement,
    nb de membres et de modèles actifs. Toutes les agrégations ignorent les NaN
    (pandas, skipna) → l'horizon 16 j reste tracé même si les membres se raréfient.
    """
    piv = member_matrix(sub)
    if piv is None or piv.empty:
        return None
    out = pd.DataFrame({"valid_time": piv.index})
    out["Min"] = piv.min(axis=1).values
    out["P10"] = piv.quantile(0.10, axis=1).values
    out["P25"] = piv.quantile(0.25, axis=1).values
    out["Médiane"] = piv.median(axis=1).values
    out["P75"] = piv.quantile(0.75, axis=1).values
    out["P90"] = piv.quantile(0.90, axis=1).values
    out["Max"] = piv.max(axis=1).values
    out[_PCT_COLS] = out[_PCT_COLS].round(2)
    out["Spread"] = (out["P90"] - out["P10"]).round(2)
    out["Ecart-type"] = piv.std(axis=1).round(2).values
    seuil = C.SEUIL_CANICULE_850
    out["Proba > seuil"] = (piv.gt(seuil).sum(axis=1) / piv.notna().sum(axis=1)).fillna(0).values
    out["n_membres"] = piv.notna().sum(axis=1).values
    # n_models : nb de modèles ayant ≥1 membre valide à cette échéance.
    pres = piv.notna().T.groupby(level="model").any().T
    out["n_models"] = pres.sum(axis=1).values
    return out.reset_index(drop=True)


def model_data(sub, model):
    """Données d'UN modèle : (stats, members_df, det_series).

    members_df : index=valid_time, colonnes=membres perturbés (contrôle exclu).
    det_series : membre de contrôle (member 0), ou None.
    stats : valid_time + median/p10/p90 (sur tous les membres, contrôle inclus).
    """
    s = sub[sub["model"] == model]
    if s.empty:
        return None
    piv = s.pivot_table(index="valid_time", columns="member", values=VAR).sort_index()
    det = piv[0] if 0 in piv.columns else None
    members = piv.drop(columns=[0], errors="ignore")
    stats = pd.DataFrame({"valid_time": piv.index})
    stats["median"] = piv.median(axis=1).values
    stats["p10"] = piv.quantile(0.10, axis=1).values
    stats["p90"] = piv.quantile(0.90, axis=1).values
    return stats, members, det


def model_medians(sub):
    """DataFrame index=valid_time, une colonne de médiane par modèle présent."""
    meds = {}
    for model in C.MODEL_LABELS:
        s = sub[sub["model"] == model]
        if s.empty:
            continue
        piv = s.pivot_table(index="valid_time", columns="member", values=VAR).sort_index()
        meds[model] = piv.median(axis=1)
    if not meds:
        return None
    return pd.concat(meds, axis=1).sort_index()


def previous_runs_sub(sig, sub):
    """Pool « run précédent » : pour chaque couple (modèle, run) présent dans
    `sub`, les lignes du dernier run STRICTEMENT antérieur de CE modèle — chaque
    modèle recule vers son propre cycle précédent, jamais de cycle global
    partagé (un 6Z peut ainsi être comparé au 0Z pour ECMWF et au 6Z−6h pour
    GEFS). Sert de référence aux colonnes Δ des tableaux d'export. None si
    aucun modèle n'a d'antécédent."""
    df = load_db(sig)
    frames = []
    for (model, rd), _ in sub.groupby(["model", "run_date"]):
        prior = df[(df["model"] == model) & (df["run_date"] < rd)].dropna(subset=[VAR])
        if prior.empty:
            continue
        prev_rd = prior["run_date"].max()
        frames.append(df[(df["model"] == model) & (df["run_date"] == prev_rd)])
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def _model_median(sub, model):
    """Médiane d'UN modèle (tous membres, contrôle inclus), indexée valid_time."""
    s = sub[sub["model"] == model]
    if s.empty:
        return None
    return s.pivot_table(index="valid_time", columns="member", values=VAR).median(axis=1)


def enriched_super_table(sub, prev_sub=None):
    """Table d'export du super-ensemble, enrichie par modèle : médiane, contrôle
    (member 0), nb de membres actifs et Δ de médiane vs le run précédent de CE
    modèle (cf. previous_runs_sub). Volontairement large : pensée pour l'export
    vers une analyse externe (IA), pas pour la lecture à l'écran."""
    se = super_ensemble(sub)
    if se is None or se.empty:
        return se
    out = se.set_index("valid_time")
    if prev_sub is not None:
        prev_se = super_ensemble(prev_sub)
        if prev_se is not None and not prev_se.empty:
            prev_med = prev_se.set_index("valid_time")["Médiane"]
            out["Δ Médiane vs préc."] = (out["Médiane"] - prev_med.reindex(out.index)).round(2)
    for model in C.MODEL_LABELS:
        s = sub[sub["model"] == model]
        if s.empty:
            continue
        piv = s.pivot_table(index="valid_time", columns="member", values=VAR).sort_index()
        med = piv.median(axis=1).reindex(out.index)
        out[f"{model} médiane"] = med.round(2)
        if 0 in piv.columns:
            out[f"{model} contrôle"] = piv[0].reindex(out.index).round(2)
        out[f"{model} n membres"] = (piv.notna().sum(axis=1)
                                     .reindex(out.index).fillna(0).astype(int))
        if prev_sub is not None:
            pmed = _model_median(prev_sub, model)
            if pmed is not None:
                out[f"{model} Δ médiane"] = (med - pmed.reindex(out.index)).round(2)
    return out.reset_index()


def model_table(sub, model, prev_sub=None):
    """Table d'export d'UN modèle : mêmes stats d'ensemble que le super-ensemble
    mais restreintes à ses seuls membres, plus le contrôle (member 0) et le Δ de
    médiane vs le run précédent de CE modèle."""
    s = sub[sub["model"] == model]
    se = super_ensemble(s)
    if se is None or se.empty:
        return se
    out = se.drop(columns=["n_models"]).set_index("valid_time")
    piv = s.pivot_table(index="valid_time", columns="member", values=VAR).sort_index()
    if 0 in piv.columns:
        out["Contrôle"] = piv[0].reindex(out.index).round(2)
    if prev_sub is not None:
        pmed = _model_median(prev_sub, model)
        if pmed is not None:
            out["Δ médiane vs préc."] = (out["Médiane"] - pmed.reindex(out.index)).round(2)
    return out.reset_index()


def divergence(sub):
    """Divergence inter-modèles = (médiane max − médiane min) entre modèles principaux.

    Sécurité logique : calculée uniquement aux échéances où TOUS les modèles
    principaux présents dans ce run ont une médiane valide. Sur les échéances à
    composition incomplète (un modèle s'est arrêté), la valeur est masquée → pas de
    saut de référence artificiel. On se restreint aux modèles principaux (config
    `main`) pour qu'un modèle d'appoint à horizon court ne tronque pas l'analyse.
    """
    meds = model_medians(sub)
    if meds is None:
        return None
    expected = [m for m in C.MAIN_LABELS if m in meds.columns]
    if len(expected) < 2:
        return None
    full = meds[expected].dropna(how="any")  # composition complète (modèles principaux)
    if full.empty:
        return None
    div = (full.max(axis=1) - full.min(axis=1)).round(2)
    return pd.DataFrame({"valid_time": full.index, "Divergence": div.values})


def multimodel_cutoff(sub):
    """Dernière échéance où ≥ 2 modèles sont présents (au-delà : modèle isolé)."""
    se = super_ensemble(sub)
    if se is None or se.empty:
        return None
    multi = se.loc[se["n_models"] >= 2, "valid_time"]
    return pd.Timestamp(multi.max()) if not multi.empty else None


# Backfill inter-runs, ÉCHÉANCE PAR ÉCHÉANCE : pour chaque modèle PRINCIPAL, on
# part du run courant (sa portion réellement fraîche, cf. Forecast.mask_stale_tail
# côté pipeline — au-delà, NaN) et on comble les échéances encore NaN avec celles
# du run antérieur le plus proche qui les couvre, et ainsi de suite jusqu'à
# n-3 (3 runs sautés). Un run partiel à 6Z/18Z se voit donc complété par la
# moitié du run précédent (lui-même éventuellement partiel), puis par celui
# d'avant si besoin — jamais une simple substitution tout-ou-rien par modèle.
#
# Les modèles d'appoint (non principaux, ex. GEM) ne sont JAMAIS backfillés :
# GEM n'existe qu'à 0Z/12Z (cf. config.MODELS `cycles`), le comparer doit donc
# toujours se faire cycle identique à cycle identique — pas de « GEM 6Z » fabriqué.
BACKFILL_MAX_LOOKBACK = 3

# Tolérance (heures) sous l'horizon nominal pour qu'un run CANDIDAT au backfill
# soit jugé valide (portée réelle = dernière échéance − SON PROPRE run_date).
# Plus large que FULL_HORIZON_TOLERANCE_H (vues combinées) : on veut ici juste
# écarter les runs franchement périmés (queue recollée de l'ancien cycle, cf.
# Forecast.mask_stale_tail — portée nulle ou négative), pas exiger un horizon
# quasi complet. Sans ce filtre, un run comme GEFS 12Z entièrement périmé
# (aucune échéance ≥ son propre cycle) est certes ignoré comme source, mais
# silencieusement — et la recherche saute alors un run 18Z pourtant valide
# situé plus loin si celui-ci n'est plus dans la liste examinée.
BACKFILL_HORIZON_TOLERANCE_H = 40
MODEL_HORIZON_H = {m["label"]: m.get("horizon_h") for m in C.MODELS}


def completed_pooled_sub(runs, pos, sig, max_lookback=BACKFILL_MAX_LOOKBACK):
    """Lignes du run `pos` (index dans `runs`, trié du plus récent au plus
    ancien), en complétant, échéance par échéance, les NaN des modèles PRINCIPAUX
    avec les runs antérieurs (pos+1 → pos+max_lookback) — priorité au plus frais.

    Retourne (sub_complet, sources) où `sources` mappe chaque modèle principal à
    la liste des run_date effectivement utilisés (le run courant en premier s'il
    a contribué, puis les runs antérieurs ayant comblé des échéances manquantes ;
    liste vide si le modèle est introuvable dans toute la fenêtre)."""
    n = len(runs)
    run_start = runs.iloc[pos]["run_date"]  # cycle (heure locale) du run analysé
    frames, sources = [], {}
    for model in C.MAIN_LABELS:
        used, covered_vt = [], set()
        horizon = MODEL_HORIZON_H.get(model)
        for j in range(pos, min(pos + max_lookback + 1, n)):
            cand_run_date = runs.iloc[j]["run_date"]
            cand = run_slice(sig, cand_run_date)
            cand = cand[cand["model"] == model]
            if cand.empty:
                continue
            cand_valid = cand.dropna(subset=[VAR])
            if cand_valid.empty:
                continue
            # Run candidat invalide (queue périmée du cycle précédent) : sa portée
            # réelle depuis SON PROPRE run_date n'atteint pas l'horizon nominal
            # (à BACKFILL_HORIZON_TOLERANCE_H près) → on l'écarte explicitement de
            # la recherche, plutôt que de compter sur le fait qu'il ne recouvrira
            # par coïncidence aucune échéance utile du run analysé.
            if horizon is not None:
                reach_h = ((cand_valid["valid_time"].max() - pd.Timestamp(cand_run_date))
                           / pd.Timedelta(hours=1))
                if reach_h < horizon - BACKFILL_HORIZON_TOLERANCE_H:
                    continue
            # On ne considère que les échéances À VENIR (≥ cycle du run) : les heures
            # antérieures au cycle sont du passé, rebouchées par l'API depuis 00:00
            # local (et souvent aussi servies par le run précédent). La convergence
            # les jette de toute façon (filtre target ≥ run) ; les compter ici
            # faisait apparaître QUASI TOUS les runs comme « complétés » par un
            # ancien alors qu'ils sont pleins — bruit massif. On garde ensuite les
            # échéances valides et pas déjà couvertes (priorité au plus frais).
            valid = cand_valid[(cand_valid["valid_time"] >= run_start)
                                & (~cand_valid["valid_time"].isin(covered_vt))]
            if valid.empty:
                continue
            frames.append(valid)
            covered_vt.update(valid["valid_time"].unique())
            used.append(cand_run_date)
        sources[model] = used
    # Modèles d'appoint (non principaux) : jamais backfillés, uniquement si présents
    # au run courant (mêmes échéances à venir, pour rester cohérent avec ci-dessus).
    current = run_slice(sig, run_start)
    extra = current[(~current["model"].isin(C.MAIN_LABELS))
                    & (current["valid_time"] >= run_start)]
    if not extra.empty:
        frames.append(extra)
    if not frames:
        return None, sources
    return pd.concat(frames, ignore_index=True), sources


def completed_super_ensemble_daily(runs, pos, sig, max_lookback=BACKFILL_MAX_LOOKBACK):
    """Super-ensemble journalier du run `pos`, modèles principaux complétés
    échéance par échéance. Retourne (df_daily, sources) — voir completed_pooled_sub."""
    sub_complet, sources = completed_pooled_sub(runs, pos, sig, max_lookback)
    if sub_complet is None:
        return None, sources
    return daily_aggregate(super_ensemble(sub_complet)), sources


def daily_aggregate(se):
    """Super-ensemble infra-journalier → 1 ligne/jour (moyenne du jour, 12h locale)."""
    if se is None or se.empty:
        return se
    se = se.copy()
    se["date"] = pd.to_datetime(se["valid_time"]).dt.normalize()
    num = [c for c in se.columns if c not in ("valid_time", "date")]
    out = se.groupby("date")[num].mean().reset_index()
    out["valid_time"] = out["date"] + pd.Timedelta(hours=12)
    out[_PCT_COLS] = out[_PCT_COLS].round(2)
    return out


def daily_risk(sub, seuil):
    """Risque canicule/jour : pool des membres de la journée, proba de dépasser seuil.

    `exces` = dépassement attendu E[max(T − seuil, 0)] sur les membres poolés du
    jour — c'est exactement probabilité × sévérité moyenne des dépassements : un
    jour à proba modeste mais à queue très chaude y pèse autant qu'un jour à
    proba forte et dépassement léger (cf. KPI « Jours à risque »)."""
    piv = member_matrix(sub)
    if piv is None or piv.empty:
        return None
    dates = pd.to_datetime(piv.index).normalize()
    rows = []
    for date, grp in piv.groupby(dates):
        vals = grp.to_numpy().ravel()
        vals = vals[~np.isnan(vals)]
        if vals.size == 0:
            continue
        rows.append({
            "date": pd.Timestamp(date),
            "Médiane": float(np.median(vals)),
            "P75": float(np.quantile(vals, 0.75)),
            "P90": float(np.quantile(vals, 0.90)),
            "prob": float((vals >= seuil).mean()),
            "exces": float(np.maximum(vals - seuil, 0).mean()),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
#  Composants graphiques
# --------------------------------------------------------------------------- #
def _rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _band(fig, x, lo, hi, color, name, opacity=0.18):
    fig.add_trace(go.Scatter(x=x, y=hi, mode="lines", line=dict(width=0),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=lo, mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor=_rgba(color, opacity), name=name, hoverinfo="skip"))


def fan_chart(syn, title):
    """Panache de dispersion du super-ensemble : Min–Max, P10–P90, P25–P75, médiane."""
    x = syn["valid_time"]
    fig = go.Figure()
    base = _ink()
    _band(fig, x, syn["Min"], syn["Max"], base, "Min–Max", 0.08)
    _band(fig, x, syn["P10"], syn["P90"], base, "P10–P90", 0.16)
    _band(fig, x, syn["P25"], syn["P75"], base, "P25–P75 (50 %)", 0.28)
    fig.add_trace(go.Scatter(x=x, y=syn["Médiane"], mode="lines", name="Médiane",
                             line=dict(color="#E74C3C", width=3)))
    fig.update_layout(title=title, height=480, hovermode="x unified",
                      xaxis_title="Échéance (date de validité)", yaxis_title="Température (°C)",
                      legend=dict(orientation="h", y=1.08), template=_plotly_template(),
                      margin=dict(t=70, l=10, r=10, b=10))
    return fig


def spaghetti_chart(members, stats, det, model):
    """Tous les membres d'ensemble (fins) + médiane + run de contrôle."""
    fig = go.Figure()
    color = C.COLOR_BY_LABEL.get(model, "#888")
    x = members.index
    for col in members.columns:
        fig.add_trace(go.Scatter(x=x, y=members[col], mode="lines",
                                 line=dict(color=_rgba(color, 0.22), width=0.8),
                                 hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=stats["valid_time"], y=stats["median"], mode="lines",
                             name="Médiane", line=dict(color=color, width=3.5)))
    if det is not None:
        fig.add_trace(go.Scatter(x=det.index, y=det.values, mode="lines",
                                 name="Contrôle", line=dict(color=_ink(), width=2, dash="dash")))
    fig.update_layout(
        title=f"Spaghetti des membres — {model} ({members.shape[1]} scénarios)",
        height=480, hovermode="x unified", template=_plotly_template(),
        xaxis_title="Échéance (date de validité)", yaxis_title="Température (°C)",
        legend=dict(orientation="h", y=1.08), margin=dict(t=70, l=10, r=10, b=10))
    return fig


def models_median_chart(sub, models, cutoff=None):
    """Comparaison des médianes (+ enveloppe P10–P90 + contrôle) des modèles."""
    fig = go.Figure()
    for model in models:
        loaded = model_data(sub, model)
        if loaded is None:
            continue
        stats, _, det = loaded
        if cutoff is not None:
            mask = stats["valid_time"] <= cutoff
            stats = stats[mask]
        c = C.COLOR_BY_LABEL[model]
        _band(fig, stats["valid_time"], stats["p10"], stats["p90"], c, f"{model} P10–P90", 0.12)
        fig.add_trace(go.Scatter(x=stats["valid_time"], y=stats["median"], mode="lines",
                                 name=f"{model} médiane", line=dict(color=c, width=2.8)))
        if det is not None and det.notna().any():
            d = det[det.index <= cutoff] if cutoff is not None else det
            fig.add_trace(go.Scatter(x=d.index, y=d.values, mode="lines",
                                     name=f"{model} contrôle",
                                     line=dict(color=c, width=1.6, dash="dot")))
    fig.update_layout(title="Comparaison des modèles — médiane, dispersion & contrôle",
                      height=480, hovermode="x unified", template=_plotly_template(),
                      xaxis_title="Échéance (date de validité)", yaxis_title="Température (°C)",
                      legend=dict(orientation="h", y=1.1), margin=dict(t=80, l=10, r=10, b=10))
    return fig


def divergence_chart(div, cutoff=None):
    """Divergence inter-modèles en fonction de l'échéance (composition complète)."""
    if cutoff is not None:
        div = div[div["valid_time"] <= cutoff]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=div["valid_time"], y=div["Divergence"], mode="lines+markers",
        line=dict(color="#7D3C98", width=2.5), marker=dict(size=4),
        name="Divergence",
        hovertemplate="%{x|%d/%m %Hh}<br>Divergence : %{y:.1f} °C<extra></extra>"))
    fig.add_hline(y=4.0, line=dict(color="#D32F2F", width=1, dash="dot"),
                  annotation_text="forte (≥4 °C)", annotation_position="top left")
    fig.add_hline(y=1.5, line=dict(color="#1976D2", width=1, dash="dot"),
                  annotation_text="faible (≤1.5 °C)", annotation_position="bottom left")
    fig.update_layout(title="Divergence inter-modèles (écart des médianes)",
                      height=340, template=_plotly_template(), hovermode="x unified",
                      xaxis_title="Échéance", yaxis_title="Écart chaud−froid (°C)",
                      margin=dict(t=70, l=10, r=10, b=10))
    return fig


def spread_chart(syn):
    """Incertitude (spread P90-P10 et écart-type) en fonction de l'échéance."""
    fig = go.Figure()
    fig.add_trace(go.Bar(x=syn["valid_time"], y=syn["Spread"], name="Spread (P90−P10)",
                         marker_color=_rgba("#2980B9", 0.55)))
    fig.add_trace(go.Scatter(x=syn["valid_time"], y=syn["Ecart-type"], name="Écart-type",
                             yaxis="y2", line=dict(color="#C0392B", width=2.5)))
    fig.update_layout(title="Incertitude de la prévision selon l'échéance",
                      height=360, template=_plotly_template(), hovermode="x unified",
                      xaxis_title="Échéance", yaxis_title="Spread (°C)",
                      yaxis2=dict(title="Écart-type (°C)", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=1.15), margin=dict(t=70, l=10, r=10, b=10))
    return fig


def ligne_de_flottaison(syn, seuil_chaleur, seuil_canicule, titre):
    """Médiane + zone P10–P90 + normale climatique (cosinus) + deux seuils."""
    x = syn["valid_time"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=syn["P90"], mode="lines", line=dict(width=0),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=syn["P10"], mode="lines", line=dict(width=0),
                             fill="tonexty", fillcolor=_rgba("#E74C3C", 0.10),
                             name="Marge d'incertitude", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=syn["Médiane"], mode="lines", name="Tendance (médiane)",
                             line=dict(color=_ink(), width=3),
                             hovertemplate="%{x|%a %d %b · %Hh}<br>Médiane : %{y:.1f} °C<extra></extra>"))
    # Normale climatique saisonnière (cosinus) — courbe, pas une simple ligne.
    fig.add_trace(go.Scatter(x=x, y=clim_normal(x), mode="lines", name="Normale climatique",
                             line=dict(color="#2980B9", width=2, dash="dot"),
                             hovertemplate="Normale : %{y:.1f} °C<extra></extra>"))
    fig.add_hline(y=seuil_chaleur, line=dict(color="#F39C12", width=2, dash="dash"),
                  annotation_text=f"Chaleur notable — {seuil_chaleur:.0f} °C",
                  annotation_position="top left", annotation_font=dict(color="#E67E22", size=12))
    fig.add_hline(y=seuil_canicule, line=dict(color="#E74C3C", width=2, dash="dash"),
                  annotation_text=f"Canicule exceptionnelle — {seuil_canicule:.0f} °C",
                  annotation_position="top left", annotation_font=dict(color="#C0392B", size=12))
    fig.update_layout(title=titre, height=440, hovermode="x unified", template=_plotly_template(),
                      xaxis_title=None, yaxis_title="Température à 850 hPa (°C)",
                      legend=dict(orientation="h", y=1.08), margin=dict(t=70, l=10, r=10, b=10))
    return fig


CANICULE_SCALE = [
    [0.00, "#2ECC71"], [0.10, "#A9DC76"], [0.25, "#F1C40F"],
    [0.40, "#E67E22"], [0.50, "#E74C3C"], [1.00, "#C0392B"],
]


# Paliers de probabilité de canicule PARTAGÉS entre le calendrier du risque
# (_canicule_label) et le KPI « Statut canicule » (statut gradué) — une seule
# échelle pour toute la page, jamais deux jugements différents du même chiffre.
PROB_CANICULE_QUASI = 0.50
PROB_RISQUE_MARQUE = 0.25
PROB_RISQUE_MODERE = 0.10


def _canicule_label(prob):
    if prob >= PROB_CANICULE_QUASI:
        return "🔴 Canicule quasi-certaine"
    if prob >= PROB_RISQUE_MARQUE:
        return "🟠 Risque marqué"
    if prob >= PROB_RISQUE_MODERE:
        return "🟡 Risque modéré"
    return "🟢 Pas de signal de canicule"


def calendrier_risques(jours, seuil):
    texts = [
        f"{d:%a %d %b}<br>{_canicule_label(p)}"
        f"<br>Médiane : {m:.1f} °C · P90 : {p90:.1f} °C"
        f"<br>P(≥ {seuil:.0f} °C) : {p * 100:.0f} %"
        for d, p, m, p90 in zip(jours["date"], jours["prob"], jours["Médiane"], jours["P90"])
    ]
    fig = go.Figure(go.Heatmap(
        x=jours["date"], y=["Risque canicule"], z=[jours["prob"].tolist()],
        colorscale=CANICULE_SCALE, zmin=0.0, zmax=1.0, xgap=3, ygap=0,
        text=[texts], hovertemplate="%{text}<extra></extra>",
        colorbar=dict(title="P(canicule)", tickformat=".0%", thickness=12, len=0.9)))
    fig.update_layout(height=150, template=_plotly_template(),
                      xaxis=dict(title=None, tickformat="%a %d/%m", type="date"),
                      yaxis=dict(visible=False), margin=dict(t=10, l=10, r=10, b=10))
    return fig


# Indice de tendance récente (grand public) : fenêtre de runs considérée et
# seuils (°C) de qualification des révisions. |Δ| < STABLE = stable ;
# ≥ STRONG = révision nette. La fenêtre ~66 h ≈ les runs des 3 derniers jours
# (0Z/12Z + cycles récents), assez large pour lisser un run isolé.
TREND_WINDOW_H = 66
TREND_STABLE_C = 0.5
TREND_STRONG_C = 1.5


def tendance_recente(trend, window_h=TREND_WINDOW_H):
    """Indice de variation PAR JOURNÉE cible : écart entre ce que prévoit le
    dernier run et ce que prévoyait le plus ancien run de la fenêtre (médiane
    journalière du super-ensemble complété, cf. trend_daily_medians). Positif =
    les calculs récents ont réchauffé la prévision pour ce jour. Une journée
    n'est notée que si ≥ 2 runs de la fenêtre la couvrent."""
    if trend.empty:
        return pd.DataFrame(columns=["target", "delta"])
    latest = trend["run_dt"].max()
    win = trend[trend["run_dt"] >= latest - pd.Timedelta(hours=window_h)]
    rows = []
    for target, grp in win.groupby("target"):
        grp = grp.sort_values("run_dt")
        if grp["run_dt"].nunique() < 2:
            continue
        rows.append({"target": pd.Timestamp(target),
                     "delta": float(grp.iloc[-1]["median"] - grp.iloc[0]["median"])})
    return pd.DataFrame(rows)


def _tendance_label(delta):
    """(flèche, libellé vulgarisé) d'une révision — jamais de valeur brute."""
    if delta >= TREND_STRONG_C:
        return "⬆", "nette révision à la hausse"
    if delta >= TREND_STABLE_C:
        return "↗", "légère révision à la hausse"
    if delta <= -TREND_STRONG_C:
        return "⬇", "nette révision à la baisse"
    if delta <= -TREND_STABLE_C:
        return "↘", "légère révision à la baisse"
    return "＝", "prévision stable"


def tendance_heatmap(tend):
    """Une case par jour à venir : couleur (rouge = revu à la hausse, bleu = à
    la baisse, blanc = stable) + flèche. Lecture en un coup d'œil de la tendance
    récente des modèles sur toute la période — aucune valeur brute affichée."""
    arrows, hovers = [], []
    for _, r in tend.iterrows():
        arrow, lib = _tendance_label(r["delta"])
        arrows.append(arrow)
        hovers.append(f"{r['target']:%a %d %b}<br>Ces derniers jours : {lib}")
    zmax = max(float(tend["delta"].abs().max()), TREND_STRONG_C)
    fig = go.Figure(go.Heatmap(
        x=tend["target"], y=["Tendance récente"], z=[tend["delta"].tolist()],
        colorscale="RdBu_r", zmid=0, zmin=-zmax, zmax=zmax, xgap=3, ygap=0,
        text=[arrows], texttemplate="%{text}", textfont=dict(size=16),
        customdata=[hovers], hovertemplate="%{customdata}<extra></extra>",
        showscale=False))
    fig.update_layout(height=150, template=_plotly_template(),
                      xaxis=dict(title=None, tickformat="%a %d/%m", type="date"),
                      yaxis=dict(visible=False), margin=dict(t=10, l=10, r=10, b=10))
    return fig


# Seuils (°C) sur le spread journalier P90−P10 pour le libellé grand public de
# confiance : scénarios groupés / partagés / très dispersés. Ordres de grandeur
# T850 : < 3 °C = bon accord, ≥ 6 °C = fourchette trop large pour trancher.
CONF_SPREAD_BON_C = 3.0
CONF_SPREAD_FAIBLE_C = 6.0


def _confiance_label(spread):
    if spread < CONF_SPREAD_BON_C:
        return "🟢 bonne (scénarios groupés)", "#2ECC71"
    if spread < CONF_SPREAD_FAIBLE_C:
        return "🟡 moyenne (scénarios partagés)", "#F1C40F"
    return "🟠 faible (scénarios très dispersés)", "#E67E22"


def confiance_chart(daily, seuil_chaleur, seuil_canicule):
    """Grand public : fourchette probable (P10–P90) par journée, barre colorée
    selon l'accord des scénarios (spread journalier), + scénario médian en trait
    foncé. Une barre courte et verte = les modèles sont d'accord ; longue et
    orange = le chiffre du jour est à prendre avec des pincettes."""
    labels_colors = [_confiance_label(s) for s in daily["Spread"]]
    texts = [
        f"{d:%a %d %b}<br>Fourchette probable : {p10:.0f} à {p90:.0f} °C"
        f"<br>Scénario médian : {m:.1f} °C<br>Confiance : {lab}"
        for d, p10, p90, m, (lab, _) in zip(daily["date"], daily["P10"], daily["P90"],
                                            daily["Médiane"], labels_colors)
    ]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=daily["date"], y=daily["P90"] - daily["P10"], base=daily["P10"],
        marker_color=[_rgba(c, 0.55) for _, c in labels_colors],
        name="Fourchette probable (P10–P90)",
        customdata=texts, hovertemplate="%{customdata}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=daily["date"], y=daily["Médiane"], mode="lines+markers",
        name="Scénario médian", line=dict(color=_ink(), width=2.5),
        marker=dict(size=6), hoverinfo="skip"))
    fig.add_hline(y=seuil_chaleur, line=dict(color="#F39C12", width=1.5, dash="dash"),
                  annotation_text=f"Chaleur — {seuil_chaleur:.0f} °C",
                  annotation_position="top left", annotation_font=dict(color="#E67E22", size=11))
    fig.add_hline(y=seuil_canicule, line=dict(color="#E74C3C", width=1.5, dash="dash"),
                  annotation_text=f"Canicule — {seuil_canicule:.0f} °C",
                  annotation_position="top left", annotation_font=dict(color="#C0392B", size=11))
    fig.update_layout(height=400, hovermode="x unified", template=_plotly_template(),
                      xaxis=dict(title=None, tickformat="%a %d/%m", type="date"),
                      yaxis_title="Température à 850 hPa (°C)",
                      legend=dict(orientation="h", y=1.12), barmode="overlay",
                      margin=dict(t=40, l=10, r=10, b=10))
    return fig


def _kpi_card(label, value, help_txt="", value_point=None, valid_time=None, sub=""):
    """Carte KPI ; si value_point + valid_time fournis, affiche l'anomalie vs la
    normale climatique saisonnière (cosinus) à cette date. `sub` : ligne de
    détail visible sous la valeur (date, probabilité…) — contrairement à
    help_txt qui n'apparaît qu'au survol."""
    anomalie_html = ""
    if value_point is not None and valid_time is not None:
        delta = value_point - float(clim_normal(pd.Timestamp(valid_time)))
        if delta >= 0.05:
            couleur, signe = "#C0392B", "+"
        elif delta <= -0.05:
            couleur, signe = "#2980B9", "−"
        else:
            couleur, signe = "#7F8C8D", "±"
        anomalie_html = (f"<span style='color:{couleur};font-size:0.95rem;font-weight:600;"
                         f"margin-left:8px;white-space:nowrap;'>"
                         f"({signe}{abs(delta):.1f} °C norm.)</span>")
    title_attr = f' title="{help_txt}"' if help_txt else ""
    sub_html = (f"<div style='font-size:0.78rem;opacity:0.65;margin-top:2px;"
                f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>{sub}</div>"
                if sub else "")
    return (f"<div{title_attr} style='background:rgba(128,138,157,0.10);"
            "border:1px solid rgba(128,138,157,0.25);"
            "border-radius:12px;padding:12px 16px;height:100%;'>"
            f"<div style='font-size:0.8rem;opacity:0.7;'>{label}</div>"
            f"<div style='font-size:1.85rem;font-weight:600;color:inherit;line-height:1.3;'>"
            f"{value}{anomalie_html}</div>{sub_html}</div>")


# --------------------------------------------------------------------------- #
#  Pages
# --------------------------------------------------------------------------- #
def page_overview(runs, sig):
    st.title("🌡️ Dashboard Météo — Prévisions d'ensemble (Paris)")
    if runs.empty:
        st.warning("Base vide. Lancez le pipeline `Forecast.py` pour la remplir.")
        return
    # Sélecteur « Vu depuis » : rejoue la page telle qu'elle était après un cycle
    # antérieur (base filtrée run_date ≤ cycle, même logique de complétude par
    # modèle). La carte « Tendance » se compare alors au jeu précédent RELATIF à
    # la version affichée — on peut dérouler l'évolution d'un épisode a posteriori.
    opts = runs["run_date"].tolist()[:C.KPI_MAX_VERSIONS]
    opt_labels = [run_label_text(rd) for rd in opts]
    col_sel, _ = st.columns([1, 3])
    choice = col_sel.selectbox(
        "Vu depuis", ["Dernier état"] + opt_labels,
        help="Rejoue la vue avec les derniers runs complets disponibles à ce cycle.")
    as_of = None if choice == "Dernier état" else opts[opt_labels.index(choice)]

    sub, sources, partial = latest_complete_run_sub(sig, as_of)
    refreshed_at, _, _ = latest_refresh_status(runs, sig)
    missing = [m for m in C.MAIN_LABELS if m not in sources]
    if as_of is not None:
        refresh_txt = f" · vue reconstituée au cycle {run_label_text(as_of)}"
    else:
        refresh_txt = (f" · rafraîchi le {refreshed_at.strftime('%d/%m/%Y à %Hh%M')}"
                       if refreshed_at is not None else "")
    if missing:
        statut_txt = f"partiel ⚠️ (aucun run pour {', '.join(missing)})"
    elif partial:
        statut_txt = (f"horizon réduit ⚠️ pour {', '.join(partial)} "
                      "(pas de run à horizon plein récent)")
    else:
        statut_txt = "complet ✅"
    st.caption(f"Super-ensemble des **derniers runs complets** par modèle · "
               f"{len(runs)} prévisions (runs) archivées · températures à 850 hPa"
               f"{refresh_txt} · {statut_txt}")
    st.caption(f"Runs retenus (horizon plein, par modèle) : {complete_runs_caption(sources)}")

    syn = super_ensemble(sub)
    if syn is None or syn.empty:
        st.error("Aucune donnée exploitable pour ce run.")
        return

    # Référence « présent » des KPI : l'instant réel pour le dernier état, le
    # cycle choisi pour une version reconstituée (l'horloge n'y a aucun sens).
    ref_now = (pd.Timestamp(as_of) if as_of is not None
               else pd.Timestamp(datetime.now(LOCAL_TZ)).tz_localize(None))
    # KPI calculés sur les échéances À VENIR uniquement : les heures passées
    # (rebouchées par l'API depuis 00:00 local) fausseraient prochaine échéance,
    # pic, tendance et anomalie. Les graphiques, eux, gardent le panache complet.
    fut = syn[syn["valid_time"] >= ref_now]
    if fut.empty:
        fut = syn
    first = fut.iloc[0]
    peak = fut.loc[fut["Médiane"].idxmax()]

    # Tendance : Δ de la médiane du super-ensemble vs le pool des runs précédents
    # (recul PAR MODÈLE, cf. previous_runs_sub — jamais de cycle global partagé).
    delta_txt, delta_sub = "—", "aucun run antérieur en base"
    prev = previous_runs_sub(sig, sub)
    if prev is not None:
        syn_prev = super_ensemble(prev)
        if syn_prev is not None and not syn_prev.empty:
            both = fut.merge(syn_prev[["valid_time", "Médiane"]], on="valid_time",
                             suffixes=("", "_prev")).dropna(subset=["Médiane", "Médiane_prev"])
            if not both.empty:
                d = both["Médiane"] - both["Médiane_prev"]
                at_peak = both[both["valid_time"] == peak["valid_time"]]
                pic_txt = (f" · au pic {(at_peak.iloc[0]['Médiane'] - at_peak.iloc[0]['Médiane_prev']):+.1f} °C"
                           if not at_peak.empty else "")
                delta_txt = f"{d.mean():+.1f} °C"
                delta_sub = f"sur {len(both)} échéances communes{pic_txt}"

    # Horizon de confiance : première échéance où le spread P90−P10 dépasse le
    # seuil config — au-delà, le scénario central seul n'est plus exploitable.
    over = fut[fut["Spread"] > C.KPI_SPREAD_CONF_MAX_C]
    if over.empty:
        conf_txt = "Plein horizon"
        conf_sub = f"spread P90−P10 < {C.KPI_SPREAD_CONF_MAX_C:.0f} °C sur toute la fenêtre"
    else:
        t_lim = over.iloc[0]["valid_time"]
        conf_txt = f"J+{max((t_lim - ref_now) / pd.Timedelta(days=1), 0):.0f}"
        conf_sub = (f"spread > {C.KPI_SPREAD_CONF_MAX_C:.0f} °C dès {t_lim:%a %d %b} · "
                    f"au pic {peak['Spread']:.1f} °C")

    # Jours à risque = probabilité × sévérité (cf. daily_risk / seuils KPI_RISK_*) :
    # un jour compte si la proba journalière atteint PROB_MIN OU si le dépassement
    # attendu atteint EXCESS_MIN (queue chaude à proba modeste).
    risk = daily_risk(sub[sub["valid_time"] >= ref_now.normalize()], C.SEUIL_CANICULE_850)
    risk_txt, risk_sub = "0 j", "aucune donnée exploitable"
    if risk is not None and not risk.empty:
        flag = risk[(risk["prob"] >= C.KPI_RISK_PROB_MIN) |
                    (risk["exces"] >= C.KPI_RISK_EXCESS_MIN_C)]
        if flag.empty:
            worst = risk.loc[risk["exces"].idxmax()]
            risk_txt = "0 j"
            risk_sub = (f"max : {worst['date']:%a %d %b} · P {worst['prob']:.0%} · "
                        f"+{worst['exces']:.1f} °C attendu")
        else:
            f0 = flag.iloc[0]
            sev = f0["exces"] / f0["prob"] if f0["prob"] > 0 else 0.0
            risk_txt = f"{len(flag)} j"
            risk_sub = (f"1er : {f0['date']:%a %d %b} · P {f0['prob']:.0%} · "
                        f"+{sev:.1f} °C si dépassé")

    # Anomalie moyenne de la médiane vs la normale cosinus sur la fenêtre courte :
    # caractérise le régime (semaine chaude/froide) indépendamment du pic ponctuel.
    win = fut[fut["valid_time"] <= ref_now + pd.Timedelta(days=C.KPI_ANOMALIE_FENETRE_J)]
    if win.empty:
        win = fut
    anom = (win["Médiane"] - clim_normal(win["valid_time"])).mean()

    r1c1, r1c2, r1c3 = st.columns(3)
    r1c1.markdown(_kpi_card("Prochaine échéance (médiane)", f"{first['Médiane']:.1f} °C",
                            "Scénario central pour la première échéance à venir",
                            value_point=first["Médiane"], valid_time=first["valid_time"],
                            sub=f"{first['valid_time']:%a %d %b %Hh}"),
                  unsafe_allow_html=True)
    r1c2.markdown(_kpi_card("Pic de chaleur (médiane)", f"{peak['Médiane']:.1f} °C",
                            "Maximum du scénario central ; P90 = scénario chaud "
                            "plausible à la même échéance",
                            value_point=peak["Médiane"], valid_time=peak["valid_time"],
                            sub=f"{peak['valid_time']:%a %d %b %Hh} · P90 {peak['P90']:.1f} °C"),
                  unsafe_allow_html=True)
    r1c3.markdown(_kpi_card("Tendance vs runs précédents", delta_txt,
                            "Δ moyen de la médiane du super-ensemble vs le run "
                            "précédent de CHAQUE modèle (échéances communes à venir)",
                            sub=delta_sub),
                  unsafe_allow_html=True)

    r2c1, r2c2, r2c3 = st.columns(3)
    r2c1.markdown(_kpi_card("Horizon de confiance", conf_txt,
                            f"Première échéance où le spread P90−P10 du super-ensemble "
                            f"dépasse {C.KPI_SPREAD_CONF_MAX_C:.0f} °C",
                            sub=conf_sub),
                  unsafe_allow_html=True)
    r2c2.markdown(_kpi_card("Jours à risque canicule", risk_txt,
                            f"Jours où P(≥ {C.SEUIL_CANICULE_850:.0f} °C) ≥ "
                            f"{C.KPI_RISK_PROB_MIN:.0%} OU dépassement attendu ≥ "
                            f"{C.KPI_RISK_EXCESS_MIN_C:.1f} °C (probabilité × sévérité)",
                            sub=risk_sub),
                  unsafe_allow_html=True)
    r2c3.markdown(_kpi_card(f"Anomalie {C.KPI_ANOMALIE_FENETRE_J} j vs normale",
                            f"{anom:+.1f} °C",
                            f"Écart moyen de la médiane à la normale climatique sur "
                            f"les {C.KPI_ANOMALIE_FENETRE_J} prochains jours",
                            sub="médiane du super-ensemble vs normale saisonnière"),
                  unsafe_allow_html=True)

    st.caption("**Panache de dispersion** : ligne rouge = médiane ; bandes = part "
               "croissante des scénarios (Min–Max, P10–P90, P25–P75).")
    st.plotly_chart(fan_chart(syn, "Panache du super-ensemble — derniers runs complets par modèle"),
                    width="stretch")

    present = sorted(sub["model"].unique())
    cutoff = multimodel_cutoff(sub)
    st.caption("Trait plein = médiane par modèle, bande = dispersion (P10–P90), "
               "pointillés = run de contrôle.")
    st.plotly_chart(models_median_chart(sub, present, cutoff), width="stretch")


def page_explore(runs, sig):
    st.title("📊 Explorer une prévision (run)")
    st.caption(
        "Vue détaillée d'un run sous plusieurs angles : panache de dispersion, "
        "scénarios individuels, comparaison des modèles, divergence, incertitude et "
        "tableaux de données. « Dernier run » poole le run le plus récent de chaque "
        "modèle, même à cycles différents.")
    if runs.empty:
        st.warning("Aucun run disponible.")
        return

    # Sentinelle « dernier run » : pool du dernier run de CHAQUE modèle, quel que
    # soit son cycle (cf. latest_run_sub) — les vrais runs restent listés ensuite.
    LATEST = -1
    idx = st.selectbox(
        "Choisir un run", [LATEST] + list(runs.index),
        format_func=lambda i: ("🕐 Dernier run (le plus récent de chaque modèle, "
                               "tous cycles)" if i == LATEST else runs.loc[i, "label"]))
    if idx == LATEST:
        sub, sources = latest_run_sub(sig)
        run_label = "derniers runs par modèle"
        file_tag = "dernier"
        st.caption("Dernier run disponible de chaque modèle, même partiel (aucune "
                   f"exigence d'horizon plein) : {complete_runs_caption(sources)}")
        manquants = [m for m in C.MAIN_LABELS if m not in sources]
    else:
        run = runs.loc[idx]
        sub = run_slice(sig, run["run_date"])
        run_label = run["label"]
        u = utc_cycle(run["run_date"])
        file_tag = f"{u:%Y%m%d}_{u.hour:02d}Z"
        manquants = [m for m in main_labels_expected_at(run["run_date"])
                     if m not in sub["model"].unique()]
    syn = super_ensemble(sub)
    present = sorted(sub["model"].unique())
    cutoff = multimodel_cutoff(sub)

    if manquants:
        st.warning(f"⚠️ Modèle(s) principal(aux) absent(s) : **{', '.join(manquants)}**. "
                   "Super-ensemble appauvri (dispersion possiblement sous-estimée).")

    tab_fan, tab_spag, tab_cmp, tab_unc, tab_tbl = st.tabs(
        ["📈 Panache", "🍝 Spaghetti", "⚖️ Modèles", "📉 Incertitude", "🧾 Tableaux"])

    with tab_fan:
        if syn is not None and not syn.empty:
            st.plotly_chart(fan_chart(syn, f"Super-ensemble — {run_label}"), width="stretch")
        else:
            st.info("Aucune donnée exploitable dans ce run.")

    with tab_spag:
        if present:
            model = st.radio("Modèle", present, horizontal=True, key="spag_model")
            loaded = model_data(sub, model)
            if loaded:
                stats, members, det = loaded
                st.plotly_chart(spaghetti_chart(members, stats, det, model), width="stretch")
        else:
            st.info("Aucun modèle dans ce run.")

    with tab_cmp:
        if present:
            st.plotly_chart(models_median_chart(sub, present, cutoff), width="stretch")
            div = divergence(sub)
            if div is not None and not div.empty:
                st.caption("Divergence calculée uniquement aux échéances à composition "
                           "complète (tous les modèles du run présents).")
                st.plotly_chart(divergence_chart(div, cutoff), width="stretch")
        else:
            st.info("Comparaison indisponible.")

    with tab_unc:
        if syn is not None and not syn.empty:
            st.plotly_chart(spread_chart(syn), width="stretch")
        else:
            st.info("Pas de données d'incertitude.")

    with tab_tbl:
        st.caption("Tableaux larges, pensés pour l'export vers une analyse externe "
                   "(IA) : stats du super-ensemble enrichies, par modèle, de la "
                   "médiane, du contrôle (member 0), du nombre de membres actifs et "
                   "du Δ de médiane vs le run précédent de chaque modèle — plus une "
                   "table détaillée par modèle.")
        prev_sub = previous_runs_sub(sig, sub)
        tables = {
            "Super-ensemble (infra-journalier)":
                lambda: enriched_super_table(sub, prev_sub),
            "Super-ensemble (journalier 12h)":
                lambda: daily_aggregate(enriched_super_table(sub, prev_sub)),
        }
        for m in present:
            tables[f"Modèle — {m}"] = (lambda m=m: model_table(sub, m, prev_sub))
        choice = st.selectbox("Table", list(tables), key="tbl_sheet")
        raw = tables[choice]()
        if raw is not None:
            raw = raw.drop(columns=["date"], errors="ignore").round(2)
        if raw is None or raw.empty:
            st.info("Table indisponible.")
        else:
            num_cols = raw.select_dtypes(include="number").columns
            styler = (raw.style.background_gradient(cmap="RdYlBu_r", subset=list(num_cols),
                                                    axis=None)
                      .set_properties(subset=list(num_cols), color="#1a2330")
                      .format(precision=1)
                      if len(num_cols) else raw)
            st.dataframe(styler, width="stretch", height=520)
            st.download_button("⬇️ Télécharger (CSV)",
                               raw.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"run_{file_tag}_{choice[:20]}.csv",
                               mime="text/csv")


def _convergence_runs(runs):
    """Filtre les runs affichés en convergence : les cycles 6Z/18Z sont partiels
    par construction — ECMWF/AIFS s'arrêtent à mi-période, GEM n'y existe pas
    (cf. config.MODELS) — et pollueraient la carte de lignes à moitié remplies
    sur l'historique. On ne garde donc les cycles 6Z/18Z que pour les DEUX runs
    les plus récents (`runs` est trié du plus récent au plus ancien) ; au-delà,
    seuls 0Z/12Z (tous modèles dispos sur la période complète) sont conservés."""
    runs = runs.reset_index(drop=True)
    recent = runs.index < 2
    main_cycle = runs["run_date"].apply(lambda d: utc_cycle(d).hour in (0, 12))
    return runs[recent | main_cycle].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def trend_daily_medians(_sig, n_runs=8):
    """Médiane/P10/P90 JOURNALIÈRES des `n_runs` derniers runs affichables —
    format long (run_dt / target / median / p10 / p90), pour la section grand
    public « évolution au fil des runs ».

    Mêmes règles de comparabilité que la page Convergence : chaque run est
    recalculé comme super-ensemble COMPLÉTÉ (backfill échéance par échéance des
    modèles principaux, cf. completed_pooled_sub) pour ne jamais comparer
    « 4 modèles vs 1 » ; les cycles 6Z/18Z anciens, partiels par construction,
    sont écartés via _convergence_runs (le backfill, lui, cherche dans TOUS les
    runs). Seules les journées À VENIR de chaque run sont gardées (target ≥ jour
    du cycle) — le passé rebouché par l'API fausserait la comparaison."""
    runs_full = list_runs(_sig).reset_index(drop=True)
    shown = _convergence_runs(runs_full).head(n_runs)
    full_pos = {rd: i for i, rd in enumerate(runs_full["run_date"])}
    records = []
    for rd in shown["run_date"]:
        syn, _ = completed_super_ensemble_daily(runs_full, full_pos[rd], _sig)
        if syn is None or syn.empty:
            continue
        run_day = pd.Timestamp(rd).normalize()
        for _, row in syn.iterrows():
            target = pd.Timestamp(row["valid_time"]).normalize()
            if target < run_day:
                continue
            records.append({"run_dt": rd, "target": target,
                            "median": row.get("Médiane"),
                            "p10": row.get("P10"), "p90": row.get("P90")})
    if not records:
        return pd.DataFrame(columns=["run_dt", "target", "median", "p10", "p90"])
    return pd.DataFrame(records).dropna(subset=["median"]).reset_index(drop=True)


def page_convergence(runs, sig):
    st.title("🔄 Révisions & convergence des prévisions")
    st.caption(
        "Chaque nouveau calcul (run) corrige le précédent. Cette page montre **comment la "
        "prévision d'une même date a évolué d'un run à l'autre** : si elle se stabilise, "
        "on peut s'y fier ; si elle bouge encore beaucoup, l'incertitude reste forte.")

    runs_full = runs.reset_index(drop=True)  # historique complet, AVANT le filtre d'affichage
    runs = _convergence_runs(runs)
    if len(runs) < 2:
        st.warning("Il faut au moins 2 runs pour analyser la convergence.")
        return

    # --- Historique commun : médiane/P10/P90 journalières de CHAQUE run (recalcul brut) ---
    # Un modèle principal absent d'un run est backfillé depuis le run antérieur le plus
    # proche qui le contient (jusqu'à n-3) : on compare ainsi des super-ensembles à
    # modèles comparables, pas « 4 modèles vs 1 ». backfill_src : {run_date -> sources}.
    # La recherche se fait sur `runs_full` (tous les cycles, pas seulement 0Z/12Z) : le
    # filtre d'affichage de `_convergence_runs` allège l'axe des runs tracés mais ne doit
    # pas faire disparaître un run 6Z/18Z par ailleurs valide de la recherche de backfill.
    full_pos = {rd: i for i, rd in enumerate(runs_full["run_date"])}
    records = []
    backfill_src = {}
    for pos in range(len(runs)):
        r = runs.iloc[pos]
        syn, sources = completed_super_ensemble_daily(runs_full, full_pos[r["run_date"]], sig)
        backfill_src[r["run_date"]] = sources
        if syn is None or syn.empty:
            continue
        for _, row in syn.iterrows():
            target = pd.Timestamp(row["valid_time"]).normalize()
            run_dt = pd.Timestamp(r["run_date"])
            if target < run_dt.normalize():
                continue
            # Délai réel run → échéance (en jours, fractionnaire) : sépare les cycles
            # d'un même jour → supprime les dents de scie du graphique de convergence.
            lead = (pd.Timestamp(row["valid_time"]) - run_dt).total_seconds() / 86400
            records.append({"run_dt": r["run_date"], "lead": lead, "target": target,
                            "median": row.get("Médiane"), "p10": row.get("P10"),
                            "p90": row.get("P90")})
    long = pd.DataFrame(records).dropna(subset=["median"])
    if long.empty:
        st.warning("Données insuffisantes.")
        return

    # Runs affichés dont au moins un modèle principal a été partiellement complété
    # (échéances comblées par un run antérieur) ou reste introuvable dans la
    # fenêtre n-3. backfilled / manquants : {run_date -> ...}. `sources[model]`
    # est la liste des run_date utilisés pour CE modèle, du plus récent au plus
    # ancien (plusieurs si un run partiel a été complété par un run antérieur).
    runs_affiches = set(long["run_dt"].unique())
    label_par_dt = {r["run_date"]: r["label"] for _, r in runs.iterrows()}
    # first_seen_utc : garde symétrique avec _missing_by_run — on ne signale « manque »
    # que si le modèle avait déjà été collecté à cette date (évite les faux positifs
    # pour les runs antérieurs à la 1re collecte d'un modèle, ex. ECMWF 17 Jun).
    _om_pres = openmeteo_presence(sig)
    first_seen_utc = (_om_pres.groupby("model")["run_utc"].min().to_dict()
                      if not _om_pres.empty else {})
    backfilled, manquants = {}, {}
    for run_dt, sources in backfill_src.items():
        if run_dt not in runs_affiches:
            continue
        expected_at = set(main_labels_expected_at(run_dt))
        # On ne signale "repris" que pour les modèles attendus à ce cycle ET totalement
        # absents du run courant (srcs[0] != run_dt). Les cas où le run courant a des
        # données mais avec quelques trous comblés silencieusement ne sont PAS alertés :
        # si la donnée a été trouvée pour ce run, le contrôle des modèles le montre, et
        # l'alerte "complété par" serait incohérente avec le fait d'avoir un run présent.
        bf = {m: srcs for m, srcs in sources.items()
              if m in expected_at and srcs and srcs[0] != run_dt}
        run_utc_val = _run_utc_naive(run_dt)
        miss = [m for m in expected_at
                if not sources.get(m)
                and m in first_seen_utc
                and run_utc_val >= first_seen_utc[m]]
        if bf:
            backfilled[run_dt] = bf
        if miss:
            manquants[run_dt] = miss
    imparfaits = set(backfilled) | set(manquants)

    def _run_tick(dt):
        u = utc_cycle(dt)
        s = f"{u:%d %b} {u.hour:02d}Z"
        return f"<i>{s}*</i>" if dt in imparfaits else s

    if imparfaits:
        parts = []
        for run_dt in sorted(imparfaits, reverse=True):
            notes = []
            for m, srcs in backfilled.get(run_dt, {}).items():
                extra = [s for s in srcs if s != run_dt]
                if not extra:
                    continue
                extra_txt = ", ".join(f"{utc_cycle(s):%d %b} {utc_cycle(s).hour:02d}Z" for s in extra)
                notes.append(f"{m} repris par {extra_txt}")
            if run_dt in manquants:
                notes.append(f"manque {', '.join(manquants[run_dt])}")
            parts.append(f"{label_par_dt[run_dt]} ({' ; '.join(notes)})")
        st.warning(
            "⚠️ Certains runs affichés ont un modèle principal **absent** : son dernier run "
            "disponible est repris à sa place (jusqu'à n-3, soit ~1 jour) pour comparer des "
            "super-ensembles à nombre de modèles équivalent. Les modèles d'appoint (ex. GEM) "
            "ne sont jamais ainsi repris — ils n'apparaissent qu'à leurs propres cycles réels "
            "(0Z/12Z). Ces runs sont notés en *italique* avec un astérisque (\\*).\n\n"
            + " · ".join(parts))

    # ── 1. Révisions vs runs précédents ──
    st.subheader("📐 Révisions de la médiane vs runs précédents")
    st.caption("Chaque barre = écart de la médiane de ce run vs un run antérieur, "
               "pour une même date. Rouge = hausse, bleu = baisse.")
    idx = st.selectbox("Run de référence", runs.index,
                       format_func=lambda i: runs.loc[i, "label"], key="conv_run_sel")
    ref_run = runs.loc[idx]
    ref_med = long[long["run_dt"] == ref_run["run_date"]].set_index("target")["median"]
    prev_runs = [rd for rd in sorted(long["run_dt"].unique(), reverse=True)
                 if rd < ref_run["run_date"]][:5]
    if not ref_med.empty and prev_runs:
        fig = go.Figure()
        for i, pr in enumerate(prev_runs):
            prev_med = long[long["run_dt"] == pr].set_index("target")["median"]
            delta = (ref_med - prev_med).dropna()
            if delta.empty:
                continue
            colors = [_rgba("#E74C3C", 0.8) if v > 0 else _rgba("#2980B9", 0.8)
                      if v < 0 else _rgba("#888888", 0.4) for v in delta.values]
            fig.add_trace(go.Bar(x=delta.index, y=delta.values, offsetgroup=i,
                                 name="Δ vs " + _run_tick(pr), marker_color=colors))
        fig.add_hline(y=0, line_color=_ink(), line_width=1.5)
        fig.update_layout(height=380, template=_plotly_template(), hovermode="x unified",
                          barmode="group", xaxis_title="Date prévue", yaxis_title="Révision (°C)",
                          legend=dict(orientation="h", y=1.12), margin=dict(t=30, l=10, r=10, b=10))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Pas de run antérieur comparable.")

    st.markdown("---")

    # ── 2. Convergence par date cible ──
    st.subheader("📈 Comment la prévision a évolué au fil des jours")
    st.caption(
        "**Un panneau = une date cible.** Dans chacun, la courbe montre comment la médiane "
        "prévue a évolué selon l'ancienneté du run ; la bande = l'incertitude P10–P90. "
        "À droite (J-0) = prévision la plus récente, à gauche = prévision lointaine. "
        "Une courbe qui se stabilise vers la droite = le modèle a convergé. "
        "Un panneau qui s'arrête tôt (ex. à J-4) est **normal** : il n'existe pas encore de "
        "run plus proche de cette date (échéance future).")
    today = pd.Timestamp(datetime.now().date())
    targets = sorted(t for t in long["target"].unique() if t >= today)
    chosen = st.multiselect("Dates cibles", targets, default=targets[:5],
                            format_func=lambda t: pd.Timestamp(t).strftime("%d %b %Y"),
                            key="conv_targets")
    if chosen:
        palette = ["#E74C3C", "#2980B9", "#27AE60", "#8E44AD", "#E67E22",
                   "#16A085", "#C0392B", "#2C3E50"]
        chosen_sorted = sorted(chosen)
        n = len(chosen_sorted)
        ncols = min(3, n)
        nrows = (n + ncols - 1) // ncols
        titles = [pd.Timestamp(t).strftime("%d %b") for t in chosen_sorted]
        sub_l = long[long["target"].isin(chosen_sorted)]
        max_lead = float(sub_l["lead"].max())
        ymin, ymax = float(sub_l["p10"].min()), float(sub_l["p90"].max())
        marge = max(0.5, (ymax - ymin) * 0.08)
        fig = make_subplots(rows=nrows, cols=ncols, subplot_titles=titles,
                            horizontal_spacing=0.04, vertical_spacing=0.14)
        for i, t in enumerate(chosen_sorted):
            r, cpos = i // ncols + 1, i % ncols + 1
            d = long[long["target"] == t].sort_values("lead", ascending=False)
            if d.empty:
                continue
            c = palette[i % len(palette)]
            fig.add_trace(go.Scatter(x=d["lead"], y=d["p90"], mode="lines", line=dict(width=0),
                                     hoverinfo="skip", showlegend=False), row=r, col=cpos)
            fig.add_trace(go.Scatter(x=d["lead"], y=d["p10"], mode="lines", line=dict(width=0),
                                     fill="tonexty", fillcolor=_rgba(c, 0.15),
                                     hoverinfo="skip", showlegend=False), row=r, col=cpos)
            fig.add_trace(go.Scatter(x=d["lead"], y=d["median"], mode="lines+markers",
                                     line=dict(color=c, width=2.5), marker=dict(size=5),
                                     showlegend=False, customdata=d[["p10", "p90"]].values,
                                     hovertemplate=(f"{titles[i]} — J-%{{x:.1f}}<br>"
                                                    "Médiane : %{y:.1f} °C<br>P10–P90 : "
                                                    "%{customdata[0]:.1f}–%{customdata[1]:.1f}"
                                                    "<extra></extra>")), row=r, col=cpos)
            fig.add_hline(y=d.iloc[-1]["median"], line=dict(color=c, width=1, dash="dot"),
                          opacity=0.4, row=r, col=cpos)
        fig.update_yaxes(range=[ymin - marge, ymax + marge])
        fig.update_xaxes(autorange=False, range=[max_lead + 0.3, -0.3], ticksuffix=" j")
        fig.update_yaxes(title_text="Temp. prévue (°C)", col=1)
        for cpos in range(1, ncols + 1):
            fig.update_xaxes(title_text="Jours avant l'échéance", row=nrows, col=cpos)
        fig.update_layout(height=240 * nrows, template=_plotly_template(),
                          margin=dict(t=40, l=10, r=10, b=10))
        st.plotly_chart(fig, width="stretch")

    st.markdown("---")

    # ── 3. Heatmap des révisions run-à-run ──
    st.subheader("🗺️ Carte des révisions run-à-run")
    st.caption(
        "Rouge = le run a **revu à la hausse** la prévision vs le run précédent. "
        "Bleu = révision à la baisse. Blanc = pas de changement = prévision stable et fiable.")
    pivot = long.pivot_table(index="target", columns="run_dt", values="median").sort_index()
    delta_pivot = pivot.diff(axis=1)
    if not delta_pivot.empty:
        abs_max = max(delta_pivot.abs().max().max(), 0.5)
        heat = go.Figure(data=go.Heatmap(
            z=delta_pivot.values, x=[_run_tick(c) for c in delta_pivot.columns],
            y=[pd.Timestamp(i).strftime("%d %b") for i in delta_pivot.index],
            colorscale="RdBu_r", zmid=0, zmin=-abs_max, zmax=abs_max,
            colorbar=dict(title="Révision (°C)"),
            hovertemplate="Run %{x}<br>Cible %{y}<br>Révision : %{z:+.1f} °C<extra></extra>"))
        heat.update_layout(height=max(300, 26 * len(delta_pivot.index) + 120),
                           template=_plotly_template(), xaxis_title="Run", yaxis_title="Date prévue",
                           margin=dict(t=10, l=10, r=10, b=10))
        st.plotly_chart(heat, width="stretch")


def page_grand_public(runs, sig):
    st.title("🌞 Indicateur de canicule")
    if runs.empty:
        st.warning("Base vide.")
        return
    sub, sources, _ = latest_complete_run_sub(sig)
    st.caption("Super-ensemble des **derniers runs complets** par modèle · "
               f"{complete_runs_caption(sources)}")

    with st.expander("❓ Comment lire cet indicateur — la température à 850 hPa (T850)"):
        st.markdown(
            "**Pourquoi « 850 hPa » ?** Cet indicateur ne suit pas la température au sol, "
            "mais la **température de l'air vers 1 500 m d'altitude** (niveau de pression "
            "850 hPa, noté *T850*). C'est la référence des météorologues pour juger d'une "
            "vague de chaleur : elle décrit la masse d'air sur toute la région, sans être "
            "faussée par les effets locaux (vent, humidité du sol, chaleur urbaine).\n\n"
            "**Elle ne varie quasiment pas entre le jour et la nuit.** Contrairement au "
            "thermomètre au sol qui grimpe l'après-midi et retombe la nuit, la T850 reste "
            "stable sur 24 h : **une seule valeur résume donc la journée entière.**\n\n"
            "**Repères (plaine, été) — au sol il fait en gros _T850 + 15 °C_ :**\n"
            "- **≈ 14–15 °C à 850 hPa → ~30 °C au sol** : chaleur notable.\n"
            "- **≈ 18–20 °C à 850 hPa → ~35 °C au sol** : canicule exceptionnelle.\n\n"
            "En clair, **au-delà de ~18 °C de T850, le signal doit alerter.**\n\n"
            "**⚠️ Le « +15 °C » n'est qu'un ordre de grandeur.** Pour une même T850, la "
            "température réelle au sol (T2m) peut varier de plusieurs degrés. "
            "Ce qui creuse l'écart :\n"
            "- **Ensoleillement et durée du jour** : ciel clair et journées longues → le sol "
            "chauffe plus fort l'air en surface.\n"
            "- **Sécheresse du sol** : un sol sec n'évapore plus d'eau, donc toute l'énergie "
            "solaire part en chaleur (les canicules s'auto-amplifient avec la sécheresse).\n"
            "- **Subsidence anticyclonique** : l'air qui descend se comprime, se réchauffe et "
            "écrase la couche d'air près du sol.\n"
            "- **Advection / vent** : un flux de sud peut amener de l'air encore plus chaud à "
            "basse altitude.\n\n"
            "À l'inverse, **nuages, sol humide, vent marin ou matinée** réduisent l'écart. "
            "La T850 indique le *potentiel* de chaleur ; ces facteurs décident jusqu'où il "
            "se réalise au sol.")

    with st.expander("📡 D'où viennent ces prévisions ? (modèles, runs, super-ensemble)"):
        n_main = len(C.MAIN_LABELS)
        models_bullets = "\n".join(f"- **{m['label']}** — {m['desc']}." for m in C.MODELS)
        st.markdown(
            f"**{len(C.MODELS)} modèles d'ensemble sont combinés :**\n"
            f"{models_bullets}\n\n"
            "**Pourquoi un « ensemble » ?** Chaque modèle est relancé avec de légères "
            "variations des conditions initiales, produisant des dizaines de **scénarios "
            "(« membres »)**. Leur dispersion = la mesure de l'incertitude.\n\n"
            "**Runs 0Z / 6Z / 12Z / 18Z.** Un *run* est un calcul lancé à heure fixe en "
            "**temps universel (UTC)**. Chaque modèle a son propre rythme : certains "
            f"(dont les {n_main} modèles principaux) cyclent jusqu'à 4 fois par jour, "
            "d'autres (ex. GEM) seulement 2 fois (0Z/12Z) — la base se met à jour modèle "
            "par modèle, pas par fournée unique.\n\n"
            "**Mise à jour automatique.** Le pipeline interroge l'API Open-Meteo 4 fois par "
            "jour et ne retient, pour chaque modèle, que les échéances réellement "
            "renouvelées par rapport au run précédent (comparaison échéance par échéance) — "
            "jamais de queue recopiée de l'ancien cycle sous une étiquette erronée.\n\n"
            "**Super-ensemble.** Plutôt qu'un seul modèle, cette appli **met en commun tous "
            "les scénarios des modèles disponibles** : c'est le *super-ensemble*. Une "
            "prévision partagée par de nombreux scénarios issus de modèles différents est "
            "plus solide.\n\n"
            "**Ce qu'affichent les graphiques :**\n"
            "- *Indicateur de canicule* et *Vue d'ensemble* → le **super-ensemble** "
            "combinant, pour **chaque modèle, son dernier run à horizon plein** "
            "(les cycles trop courts sont écartés) — pas forcément le même cycle "
            "d'un modèle à l'autre.\n"
            "- *Explorer un run* → au choix : le super-ensemble (onglet **Panache**), "
            "**un seul modèle** détaillé scénario par scénario (onglet **Spaghetti**), ou la "
            "**comparaison des médianes** de chaque modèle (onglet **Modèles**).")

    with st.expander("⚙️ Réglages avancés"):
        col_a, col_b = st.columns(2)
        seuil_chaleur = col_a.number_input("Seuil chaleur (°C @850)", 10.0, 25.0,
                                           float(C.SEUIL_CHALEUR_850), 0.5)
        seuil_canicule = col_b.number_input("Seuil canicule (°C @850)", 10.0, 30.0,
                                            float(C.SEUIL_CANICULE_850), 0.5)
        if seuil_canicule <= seuil_chaleur:
            st.warning("Seuil canicule corrigé (doit dépasser le seuil chaleur).")
            seuil_canicule = seuil_chaleur + 0.5

        st.markdown("---")
        st.caption(
            "**Normale climatique (T850).** Modélisée par un cosinus saisonnier "
            "`moyenne + amplitude × cos(2π(jour − pic)/365)` — ce sont des valeurs "
            "**estimées**, pas une normale officielle calculée sur une série d'observations. "
            "Ajustez-les ici si elles ne correspondent pas à votre référence ; le réglage "
            "s'applique à toute l'appli (cartes KPI et graphiques) tant que la session reste "
            "ouverte.")
        col_m, col_amp, col_pic = st.columns(3)
        mean0, amp0, peak0 = clim_params()
        col_m.number_input("Moyenne annuelle (°C @850)", -5.0, 20.0,
                           float(mean0), 0.5, key="clim_mean")
        col_amp.number_input("Amplitude saisonnière (°C)", 0.0, 15.0,
                             float(amp0), 0.5, key="clim_amplitude")
        col_pic.number_input("Jour du pic (1-365, ~17 juil. = 198)",
                             1, 365, int(peak0), 1, key="clim_peak_doy")
        st.caption(f"Normale du jour actuel : {clim_normal(pd.Timestamp(datetime.now().date())):.1f} °C")

    syn = super_ensemble(sub)
    if syn is None or syn.empty:
        st.error("Aucune donnée exploitable.")
        return
    st.caption(f"Synthèse combinant jusqu'à {int(syn['n_membres'].max())} membres "
               f"({', '.join(sorted(sub['model'].unique()))}) par échéance.")

    jours = daily_risk(sub, seuil_canicule)
    if jours is None or jours.empty:
        st.error("Risque non calculable.")
        return

    eleve = jours["prob"] >= PROB_CANICULE_QUASI
    high_dates = jours.loc[eleve, "date"].sort_values().tolist()
    high_set = set(high_dates)
    today = pd.Timestamp(datetime.now().date())
    pic = jours.loc[jours["prob"].idxmax()]
    c1, c2, c3 = st.columns(3)
    if not high_dates:
        # Statut GRADUÉ (mêmes paliers que le calendrier) : « aucune canicule
        # probable » ne veut pas dire « rien à signaler » — un pic à 37 % ou une
        # semaine de chaleur notable doivent apparaître, pas un statut vide.
        avenir = jours[jours["date"] >= today]
        chauds = avenir[avenir["Médiane"] >= seuil_chaleur]
        pic_av = avenir.loc[avenir["prob"].idxmax()] if not avenir.empty else None
        if pic_av is not None and pic_av["prob"] >= PROB_RISQUE_MARQUE:
            c1.metric("Statut canicule", "🟠 Risque à surveiller",
                      help=f"Pas de canicule probable (≥ {PROB_CANICULE_QUASI:.0%}) à ce "
                           f"stade, mais le risque monte à {pic_av['prob']:.0%} "
                           f"le {pic_av['date']:%a %d %b}.")
        elif pic_av is not None and pic_av["prob"] >= PROB_RISQUE_MODERE:
            c1.metric("Statut canicule", "🟡 Signal faible",
                      help=f"Quelques scénarios voient une canicule (jusqu'à "
                           f"{pic_av['prob']:.0%} le {pic_av['date']:%a %d %b}) — "
                           f"minoritaires, à suivre.")
        elif not chauds.empty:
            c1.metric("Statut canicule", "🌡️ Chaleur sans canicule",
                      help=f"Pas de canicule en vue, mais de la chaleur notable "
                           f"(≥ {seuil_chaleur:.0f} °C @850) est prévue autour du "
                           f"{chauds.iloc[0]['date']:%a %d %b}.")
        else:
            c1.metric("Statut canicule", "🟢 Aucune en vue")
        # 2e carte adaptée au niveau d'alerte : jours à surveiller (risque marqué),
        # sinon jours de chaleur notable, sinon rien à quantifier.
        n_watch = int((avenir["prob"] >= PROB_RISQUE_MARQUE).sum()) if not avenir.empty else 0
        if n_watch:
            c2.metric("Jours à surveiller", f"{n_watch} jour{'s' if n_watch > 1 else ''}",
                      help=f"Jours avec au moins {PROB_RISQUE_MARQUE:.0%} de risque de canicule.")
        elif not chauds.empty:
            n_ch = len(chauds)
            c2.metric("Chaleur notable", f"{n_ch} jour{'s' if n_ch > 1 else ''}",
                      help=f"Jours dont la médiane atteint {seuil_chaleur:.0f} °C @850 "
                           f"(≈ 30 °C au sol), sans franchir le seuil canicule.")
        else:
            c2.metric("Durée prévue", "—")
    else:
        duree = 1
        dts = sorted(high_dates)
        for a, b in zip(dts, dts[1:]):
            if (b - a).days == 1:
                duree += 1
            else:
                break
        if today in high_set:
            fin = today
            while fin + pd.Timedelta(days=1) in high_set:
                fin += pd.Timedelta(days=1)
            c1.metric("Statut canicule", "🔴 En cours",
                      help=f"Au moins jusqu'au {fin:%a %d %b}")
        else:
            prochaine = next((d for d in high_dates if d > today), high_dates[0])
            c1.metric("Prochaine canicule", prochaine.strftime("%a %d %b"))
        c2.metric("Durée de l'épisode", f"{duree} jour{'s' if duree > 1 else ''}")
    c3.metric("Pic de risque", f"{pic['prob'] * 100:.0f} %",
              help=f"{pic['date']:%a %d %b} · médiane {pic['Médiane']:.1f} °C")

    st.subheader("📈 Évolution de la chaleur prévue")
    st.caption("Courbe foncée = médiane ; bande rouge = P10–P90 ; pointillés bleus = "
               "normale climatique saisonnière ; orange/rouge = seuils d'alerte.")
    st.plotly_chart(ligne_de_flottaison(syn, seuil_chaleur, seuil_canicule,
                                        "Température à 850 hPa — tendance et incertitude"),
                    width="stretch")

    st.subheader("🗓️ Calendrier du risque de canicule")
    st.caption(f"Chaque case = un jour, coloré selon P(≥ {seuil_canicule:.0f} °C @850).")
    st.plotly_chart(calendrier_risques(jours, seuil_canicule), width="stretch")

    # ── Tendance récente des runs (vulgarisé, en un coup d'œil) ──────────────
    st.subheader("🧭 Les modèles changent-ils d'avis ?")
    st.markdown(
        "Les modèles recalculent la prévision plusieurs fois par jour. La ligne ci-dessous "
        "compare les **calculs des ~3 derniers jours** : pour chaque jour à venir, elle dit "
        "si la prévision a été **revue à la hausse** (🔴, l'épisode se confirme ou "
        "s'intensifie), **à la baisse** (🔵, il se dégonfle) ou si elle est **stable** "
        "(⚪, prévision mûre, plus fiable).")
    trend = trend_daily_medians(sig)  # `today` déjà défini plus haut (KPI)
    tend = tendance_recente(trend)
    tend = tend[tend["target"] >= today] if not tend.empty else tend
    if tend.empty:
        st.info("Pas encore assez de runs en base pour mesurer une tendance.")
    else:
        # Verdict global qualitatif : moyenne des révisions sur la période à venir
        # (seuil plus bas que par jour : une dérive d'ensemble se voit sur la moyenne).
        d_moy = float(tend["delta"].mean())
        if d_moy >= 0.3:
            st.markdown("📈 **Tendance récente : vers plus chaud** — les derniers calculs "
                        "renforcent globalement la chaleur prévue.")
        elif d_moy <= -0.3:
            st.markdown("📉 **Tendance récente : vers moins chaud** — les derniers calculs "
                        "revoient globalement la chaleur à la baisse.")
        else:
            st.markdown("➡️ **Tendance récente : stable** — les derniers calculs confirment "
                        "globalement la prévision.")
        st.plotly_chart(tendance_heatmap(tend), width="stretch")
        st.caption("Pour l'analyse détaillée run par run (avec les valeurs), voir la page "
                   "*Révisions & convergence*.")

    # ── Confiance : la médiane n'est pas une certitude (vulgarisé) ───────────
    st.subheader("🎯 Quelle confiance accorder à ces chiffres ?")
    st.markdown(
        "**La médiane n'est pas une promesse.** C'est simplement le scénario du milieu : "
        "un scénario sur deux est plus chaud, un sur deux plus froid. La vraie information "
        "est la **fourchette** ci-dessous : les modèles jugent très probable (8 chances "
        "sur 10) que la journée tombe dedans. Plus la barre est courte, plus les scénarios "
        "sont d'accord — plus elle s'allonge (c'est inévitable au-delà de quelques jours), "
        "plus il faut lire « ça peut encore bouger », dans un sens comme dans l'autre.")
    daily = daily_aggregate(syn)
    daily = daily[daily["date"] >= today] if daily is not None else None
    if daily is None or daily.empty:
        st.info("Fourchettes journalières non calculables.")
    else:
        st.caption("Couleur de la barre = accord des scénarios : 🟢 groupés (bonne "
                   "confiance) · 🟡 partagés · 🟠 très dispersés (chiffre indicatif). "
                   "Trait foncé = scénario médian.")
        st.plotly_chart(confiance_chart(daily, seuil_chaleur, seuil_canicule),
                        width="stretch")
        # Alertes d'asymétrie, RECALCULÉES à chaque affichage depuis la prévision
        # courante (jamais de contenu figé) : les cas où la médiane, seule,
        # induirait en erreur — queue chaude (minorité de scénarios caniculaires
        # sous une médiane sage) et queue froide (médiane chaude mais rechute
        # possible). Formulation courte, sans valeur brute (« X scénarios sur 10 »).
        info = daily.merge(jours[["date", "prob"]], on="date", how="left")
        chauds, froids = [], []
        for _, r in info.iterrows():
            up, down = r["P90"] - r["Médiane"], r["Médiane"] - r["P10"]
            prob = r.get("prob")
            if pd.notna(prob) and prob >= 0.15 and r["Médiane"] < seuil_canicule:
                sur10 = max(1, round(prob * 10))
                chauds.append((prob, f"**{r['date']:%a %d %b}** : la médiane reste sous le "
                                     f"seuil canicule, mais **{sur10} scénario{'s' if sur10 > 1 else ''} "
                                     f"sur 10 le dépasse{'nt' if sur10 > 1 else ''}** — "
                                     f"le risque n'est pas écarté."))
            elif down - up >= 1.5 and r["Médiane"] >= seuil_chaleur:
                froids.append((down - up, f"**{r['date']:%a %d %b}** : la médiane est élevée, "
                                          f"mais une partie des scénarios reste bien plus "
                                          f"fraîche — la chaleur n'est pas encore acquise."))
        notes = ([n for _, n in sorted(chauds, reverse=True)[:2]]
                 + [n for _, n in sorted(froids, reverse=True)[:2]])[:3]
        if notes:
            st.markdown("**⚖️ À ne pas manquer derrière la médiane** *(recalculé à chaque "
                        "mise à jour)* **:**\n" + "\n".join(f"- {n}" for n in notes))


def _run_script(*args, timeout=300):
    """Lance un script Python du projet en sous-processus, capture stdout/stderr."""
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run([sys.executable, *args], cwd=C.BASE_DIR, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=child_env)
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    return proc.returncode, output


def cross_check_log_signature():
    try:
        return os.path.getmtime(C.CROSS_CHECK_LOG_PATH)
    except OSError:
        return None


@st.cache_data(show_spinner=False)
def load_cross_check_log(_sig):
    if _sig is None or not os.path.exists(C.CROSS_CHECK_LOG_PATH):
        return pd.DataFrame()
    df = pd.read_csv(C.CROSS_CHECK_LOG_PATH, parse_dates=["checked_at", "run_date", "valid_time"])
    return df.sort_values("checked_at", ascending=False).reset_index(drop=True)


def page_run(sig):
    st.title("🚀 Lancer le pipeline")

    now_utc = datetime.now(ZoneInfo("UTC"))
    nearest = run_dual._nearest_cron_hour(now_utc)
    legacy_slot = run_dual.LEGACY_SLOT_BY_CRON_HOUR.get(nearest)
    if nearest is not None:
        poll_info = f"poll de référence le plus proche : **{nearest:02d}:15 UTC**"
    else:
        poll_info = "hors créneau cron (aucun poll dans la fenêtre ±1h30)"
    st.caption(f"Heure UTC actuelle : **{now_utc:%H:%M}** · {poll_info}")

    st.markdown("**Horaires conseillés (cron du workflow) :**")
    rows = []
    for h in run_dual.CRON_HOURS:
        slot = run_dual.LEGACY_SLOT_BY_CRON_HOUR.get(h)
        action = (f"Open-Meteo + scrape Météociel {slot} + contrôle croisé"
                  if slot else "Open-Meteo seul (Météociel pas encore complet à cette heure)")
        marker = " ← maintenant" if h == nearest else ""
        rows.append(f"- **{h:02d}:15 UTC** — {action}{marker}")
    st.markdown("\n".join(rows))

    if legacy_slot:
        st.success(f"✅ Créneau favorable au double run : Météociel a fini de publier le "
                   f"{legacy_slot} (~{'midi' if legacy_slot == '0Z' else 'minuit'} heure de Paris).")
    else:
        st.info("ℹ️ Hors créneau Météociel : le double run fonctionnera, mais le scrape legacy "
               "sera automatiquement sauté (run_dual.py ne le déclenche qu'aux créneaux ci-dessus).")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("① Open-Meteo seul")
        st.caption("`Forecast.py` : interroge l'API, détecte le cycle par modèle, met à jour "
                  "`data/database_paris.parquet`. ~10-30 s.")
        if st.button("▶️ Lancer Forecast.py", type="secondary"):
            with st.spinner("Exécution de Forecast.py…"):
                try:
                    code, output = _run_script(os.path.join(C.BASE_DIR, "Forecast.py"))
                    st.code(output or "(aucune sortie)")
                    if code == 0:
                        st.success("✅ Pipeline Open-Meteo terminé.")
                        st.cache_data.clear()
                    else:
                        st.error(f"❌ Code de sortie {code}.")
                except subprocess.TimeoutExpired:
                    st.error("⏱️ Délai dépassé (5 min).")
                except Exception as e:  # noqa: BLE001
                    st.error(f"Erreur : {e}")

    with col2:
        st.subheader("② Double run + contrôle croisé")
        st.caption("`run_dual.py` : Open-Meteo, puis (si créneau favorable) scrape Météociel "
                  "+ comparaison ECMWF/AIFS/GEFS échéance par échéance. ~30-90 s.")
        if st.button("🔁 Lancer le double run", type="primary"):
            with st.spinner("Exécution de run_dual.py…"):
                try:
                    code, output = _run_script(os.path.join(C.BASE_DIR, "run_dual.py"), timeout=600)
                    st.code(output or "(aucune sortie)")
                    if code == 0:
                        st.success("✅ Double run terminé.")
                        st.cache_data.clear()
                    else:
                        st.error(f"❌ Code de sortie {code}.")
                except subprocess.TimeoutExpired:
                    st.error("⏱️ Délai dépassé (10 min).")
                except Exception as e:  # noqa: BLE001
                    st.error(f"Erreur : {e}")

    st.markdown("---")
    st.subheader("🩹 Import ciblé depuis le legacy")
    st.caption("Comble une **absence avérée** du parquet Open-Meteo depuis un xlsx "
               "Météociel (même principe que `migrate.py`, mais un seul couple "
               "run × modèle à la fois). Ne liste que les couples présents en "
               "legacy et **sans aucune donnée valide** côté Open-Meteo — jamais "
               "d'écrasement. Sauvegarde datée du parquet avant écriture ; les "
               "xlsx restent en lecture seule.")
    cands = legacy_import_candidates(sig, legacy_signature())
    if cands.empty:
        st.info("Aucune absence à combler : tous les runs legacy sont déjà "
                "couverts par des données valides dans le parquet Open-Meteo.")
    else:
        def _cand_label(r):
            reach = "" if pd.isna(r["lead_h"]) else f" · portée {r['lead_h']/24:.1f} j"
            return (f"{r['model']} — run {r['run_date']:%d/%m/%Y} "
                    f"{r['run_date'].hour}Z · {int(r['n_members'])} membres"
                    f"{reach} · {r['file']}")

        choice = st.selectbox("Run legacy à importer (absent du parquet)",
                              cands.to_dict("records"), format_func=_cand_label)
        confirm = st.checkbox("Je confirme l'import de ce run dans le parquet "
                              "(sauvegarde datée créée automatiquement avant écriture).")
        if st.button("📥 Importer ce run", type="primary", disabled=not confirm):
            with st.spinner("Import en cours…"):
                try:
                    ok, msg = import_legacy_run(choice["file"], choice["model"],
                                                choice["run_date"])
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, f"Erreur inattendue : {e}"
            if ok:
                st.success(f"✅ {msg}")
                st.cache_data.clear()
            else:
                st.error(f"❌ {msg}")

    st.markdown("---")
    st.subheader("🔍 Historique du contrôle croisé")
    st.caption("Comparaison **médiane d'ensemble** (ECMWF/AIFS/GEFS) entre Open-Meteo et "
              "Météociel, échéance par échéance. Seuil de signalement (⚠️) élargi avec "
              f"l'échéance, de {C.CROSS_CHECK_TOLERANCE_BASE_C:.1f} à "
              f"{C.CROSS_CHECK_TOLERANCE_CAP_C:.1f} °C (un bug pipeline ressort à courte "
              "échéance ; à longue échéance deux ensembles distincts divergent légitimement).")
    log_sig = cross_check_log_signature()
    log = load_cross_check_log(log_sig)
    if log.empty:
        st.info("Aucun contrôle croisé enregistré pour l'instant. Lance le double run à un "
               "créneau favorable (10:15 ou 22:15 UTC) pour en générer un.")
    else:
        latest_check = log["checked_at"].max()
        latest = log[log["checked_at"] == latest_check]
        st.caption(f"Dernier contrôle : **{latest_check:%d/%m/%Y %Hh%M}** UTC · "
                  f"run **{pd.Timestamp(latest['run_date'].iloc[0]):%d %b %Hh}** UTC")
        summary = latest.groupby(["model", "metric"]).agg(
            n=("diff", "size"), mean_abs=("diff", lambda s: s.abs().mean()),
            max_abs=("diff", lambda s: s.abs().max()), n_flag=("flag", "sum")).reset_index()
        summary.columns = ["Modèle", "Métrique", "N", "Écart moyen abs.", "Écart max abs.", "Flags"]
        st.dataframe(summary.style.format({"Écart moyen abs.": "{:.2f}", "Écart max abs.": "{:.2f}"}),
                    width="stretch", hide_index=True)

        if int(latest["flag"].sum()):
            st.warning(f"⚠️ {int(latest['flag'].sum())} échéance(s) au-delà du seuil sur le "
                      "dernier contrôle — détail ci-dessous.")
        with st.expander("📋 Détail du dernier contrôle"):
            detail_cols = ["model", "metric", "valid_time", "lead_h", "legacy_value",
                           "openmeteo_value", "diff", "tol", "flag"]
            # Rétro-compat : un log antérieur au format lead-aware n'a ni lead_h ni tol.
            show = latest[[c for c in detail_cols if c in latest.columns]].sort_values(
                "diff", key=lambda s: s.abs(), ascending=False)
            styler = show.style.apply(
                lambda r: ["background-color:#fdecea;color:#611a15" if r["flag"] else ""
                           for _ in r], axis=1
            ).format({"legacy_value": "{:.1f}", "openmeteo_value": "{:.1f}",
                      "diff": "{:+.2f}", "tol": "{:.2f}"})
            st.dataframe(styler, width="stretch", height=400, hide_index=True)

        with st.expander("📜 Historique complet (tous contrôles)"):
            st.dataframe(log, width="stretch", height=400, hide_index=True)
            st.download_button("⬇️ Télécharger l'historique (CSV)",
                               log.to_csv(index=False).encode("utf-8-sig"),
                               file_name="cross_check_log.csv", mime="text/csv")


# --------------------------------------------------------------------------- #
#  Contrôle de présence des modèles — diagnostic du pipeline (Open-Meteo vs legacy)
# --------------------------------------------------------------------------- #
# Objectif : pendant la phase de double run (Open-Meteo + Météociel), voir d'un
# coup d'œil QUEL modèle est présent sur CHAQUE run, JUSQU'À QUELLE échéance, et
# avec combien de membres — des DEUX côtés — pour repérer une incohérence (modèle
# absent, run tronqué anormalement, cycles désalignés, horizon divergent). Vue
# purement DIAGNOSTIQUE : elle n'écrit rien et ne pilote aucune persistance —
# l'horizon n'est comparé au nominal (`horizon_h`) que comme repère informatif,
# jamais comme une troncature (cf. invariants CLAUDE.md).
_CYCLES_BY_LABEL = {m["label"]: m["cycles"] for m in C.MODELS}
_LEGACY_FILE_RE = re.compile(r"Forecast-(\d{8})-(.+)\.xlsx$", re.IGNORECASE)


def _run_utc_naive(local_run_date):
    """Cycle synoptique UTC (0/6/12/18Z), tz-naïf — même convention que le
    run_date legacy parsé depuis l'en-tête Météociel, donc directement comparable."""
    return utc_cycle(local_run_date).replace(tzinfo=None)


def openmeteo_presence(sig):
    """Une ligne par (run_date, modèle) présent dans le parquet Open-Meteo :
    nb de membres, première/dernière échéance RÉELLE (valeur non-NaN), horizon
    (lead, en heures) et cycle synoptique. `expected` = ce modèle publie-t-il à ce
    cycle (config `cycles`) — sert à distinguer une absence anormale d'un cycle où
    le modèle ne tourne simplement pas (ex. GEM à 6Z/18Z)."""
    df = load_db(sig)
    cols = ["run_date", "model", "n_members", "first_vt", "last_vt",
            "lead_h", "run_utc", "cycle_h", "expected"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    v = df.dropna(subset=[VAR])
    g = v.groupby(["run_date", "model"], as_index=False).agg(
        n_members=("member", "nunique"),
        first_vt=("valid_time", "min"),
        last_vt=("valid_time", "max"))
    # lead_h = horizon mesuré uniquement sur les échéances post-cycle (valid_time ≥ run_date).
    # Les données antérieures (rebouchage API depuis 00h local) ne reflètent pas l'horizon
    # réel du cycle. NaN si aucune échéance post-cycle valide → affiché comme ✗ absent.
    v_fut = v[v["valid_time"] >= v["run_date"]]
    g_fut = (v_fut.groupby(["run_date", "model"])["valid_time"]
             .max().rename("last_vt_fut").reset_index())
    g = g.merge(g_fut, on=["run_date", "model"], how="left")
    g["lead_h"] = (g["last_vt_fut"] - g["run_date"]).dt.total_seconds() / 3600
    g = g.drop(columns=["last_vt_fut"])
    g["run_utc"] = g["run_date"].map(_run_utc_naive)
    g["cycle_h"] = g["run_utc"].map(lambda t: t.hour)
    g["expected"] = g.apply(
        lambda r: r["cycle_h"] in _CYCLES_BY_LABEL.get(r["model"], []), axis=1)
    return g


def legacy_signature():
    """Signature (nom, mtime) des xlsx legacy — invalide le cache dès qu'un fichier
    change ou qu'un nouveau scrape apparaît."""
    out = []
    for f in sorted(glob.glob(os.path.join(C.LEGACY_FORECASTS_DIR, "Forecast-*.xlsx"))):
        try:
            out.append((os.path.basename(f), os.path.getmtime(f)))
        except OSError:
            continue
    return tuple(out)


@st.cache_data(show_spinner=False)
def legacy_presence(_sig):
    """Présence/horizon côté Météociel (legacy), une ligne par (fichier, modèle).

    run_date = celui déclaré par Météociel dans l'en-tête du xlsx (source fiable,
    pas la date du nom de fichier qui est la date de scrape) ; last_vt = dernière
    échéance réellement renseignée (≥ 1 membre non-NaN) ; n_members = membres
    d'ensemble effectivement remplis. Réutilise les helpers de
    validate_cross_pipeline pour ne pas dupliquer le parsing legacy."""
    cols = ["run_label", "scrape_date", "file", "model", "run_date",
            "n_members", "last_vt", "lead_h", "n_ech"]
    rows = []
    for fname, _ in _sig:
        m = _LEGACY_FILE_RE.search(fname)
        if not m:
            continue
        scrape_date = pd.to_datetime(m.group(1), format="%d%m%Y", errors="coerce")
        run_label = m.group(2)
        path = os.path.join(C.LEGACY_FORECASTS_DIR, fname)
        for label, sheet in C.LEGACY_MODELS.items():
            df, _det, member_cols = V._read_legacy_sheet(path, sheet)
            if df is None or not member_cols:
                continue
            run_date = V._parse_legacy_run_date(path, sheet)
            members = df[member_cols].apply(pd.to_numeric, errors="coerce")
            valid = df[members.notna().any(axis=1)]
            if valid.empty:
                continue
            last_vt = valid["valid_time"].max()
            lead_h = ((last_vt - run_date).total_seconds() / 3600
                      if run_date is not None else np.nan)
            rows.append({
                "run_label": run_label, "scrape_date": scrape_date, "file": fname,
                "model": label, "run_date": run_date,
                "n_members": int(members.notna().any(axis=0).sum()),
                "last_vt": last_vt, "lead_h": lead_h, "n_ech": len(valid)})
    return pd.DataFrame(rows, columns=cols)


# --------------------------------------------------------------------------- #
#  Import ciblé legacy → parquet — comble une absence avérée, jamais d'écrasement
# --------------------------------------------------------------------------- #
# Même principe que migrate.py (xlsx Météociel → schéma plat), mais restreint à
# UN SEUL couple (run, modèle), choisi par l'utilisateur parmi les couples
# présents en legacy et SANS AUCUNE donnée valide côté Open-Meteo. Les xlsx
# restent en lecture seule (assurance-vie du projet) ; le parquet est sauvegardé
# (copie datée) avant toute écriture ; la fusion passe par Forecast.persist
# (validation, anti-régression, écriture atomique .tmp + os.replace).

def _member_id_from_col(col_name):
    """'1', '12'… → entier ; None si la colonne n'est pas un membre numéroté.
    (Le contrôle DET/GFS est identifié à part via det_col → membre 0.)"""
    m = re.search(r"(\d+)", str(col_name).strip())
    return int(m.group(1)) if m else None


@st.cache_data(show_spinner=False)
def legacy_import_candidates(_db_sig, _legacy_sig):
    """Couples (run_date, modèle) présents dans les xlsx legacy mais sans la
    moindre valeur valide dans le parquet Open-Meteo — les seuls importables.

    Comparaison en UTC tz-naïf des deux côtés : parquet BRUT (F.load_existing),
    jamais load_db qui convertit run_date en heure de Paris. Si plusieurs xlsx
    couvrent le même run (re-scrapes), on garde le plus complet (last_vt max,
    puis scrape le plus récent)."""
    leg = legacy_presence(_legacy_sig)
    if leg.empty:
        return leg
    leg = leg.dropna(subset=["run_date"])
    leg = leg[leg["model"].isin(C.MODEL_LABELS)]
    if leg.empty:
        return leg

    db = F.load_existing()
    if not db.empty:
        have = db.dropna(subset=C.VAR_COLS, how="all")[["run_date", "model"]] \
                 .drop_duplicates()
        keys_have = {(pd.Timestamp(r.run_date), r.model)
                     for r in have.itertuples(index=False)}
        mask = leg.apply(
            lambda r: (pd.Timestamp(r["run_date"]), r["model"]) not in keys_have,
            axis=1)
        leg = leg[mask]

    if leg.empty:
        return leg
    leg = (leg.sort_values(["last_vt", "scrape_date"])
              .drop_duplicates(subset=["run_date", "model"], keep="last"))
    return leg.sort_values(["run_date", "model"],
                           ascending=[False, True]).reset_index(drop=True)


def import_legacy_run(fname, model_label, expected_run_date):
    """Importe UN run d'UN modèle depuis un xlsx legacy vers le parquet.
    Retourne (ok, message). Garde-fous, dans l'ordre :
      1. relecture du xlsx au moment de l'import (lecture seule) et re-parse du
         run_date d'en-tête : s'il diffère du couple sélectionné (fichier changé
         entre-temps), abandon ;
      2. contrôle d'absence AU MOMENT de l'écriture (le dropdown peut être
         périmé) : la moindre valeur valide déjà en base pour ce couple → refus ;
      3. sauvegarde datée du parquet AVANT toute écriture ;
      4. fusion via Forecast.persist (jamais d'écriture directe)."""
    path = os.path.join(C.LEGACY_FORECASTS_DIR, fname)
    sheet = C.LEGACY_MODELS.get(model_label)
    if sheet is None:
        return False, f"Modèle {model_label} sans feuille legacy déclarée (config)."

    df, det_col, member_cols = V._read_legacy_sheet(path, sheet)
    if df is None or (det_col is None and not member_cols):
        return False, f"Feuille « {sheet} » illisible dans {fname}."
    run_date = V._parse_legacy_run_date(path, sheet)
    if run_date is None or pd.Timestamp(run_date) != pd.Timestamp(expected_run_date):
        return False, ("Le run_date lu dans le xlsx ne correspond plus à la "
                       "sélection (fichier modifié entre-temps ?) — import abandonné.")

    # xlsx large → schéma plat. Le legacy Météociel ne publie que la T850 :
    # les autres variables éventuelles du schéma restent NaN.
    frames = []
    if det_col is not None:
        d = df[["valid_time", det_col]].rename(columns={det_col: "t850"})
        d["member"] = 0
        frames.append(d)
    for col in member_cols:
        mid = _member_id_from_col(col)
        if mid is None:
            continue
        d = df[["valid_time", col]].rename(columns={col: "t850"})
        d["member"] = mid
        frames.append(d)
    if not frames:
        return False, "Aucune colonne membre exploitable dans la feuille."

    tidy = pd.concat(frames, ignore_index=True)
    tidy["t850"] = pd.to_numeric(tidy["t850"], errors="coerce")
    tidy = tidy.dropna(subset=["t850"])
    if tidy.empty:
        return False, "Aucune valeur valide dans le xlsx — rien à importer."
    tidy["run_date"] = pd.Timestamp(run_date)  # UTC tz-naïf, comme le parquet
    tidy["model"] = model_label
    tidy["member"] = tidy["member"].astype(int)
    for c in C.VAR_COLS:
        if c not in tidy.columns:
            tidy[c] = np.nan
    tidy = tidy.drop_duplicates(subset=["run_date", "model", "member", "valid_time"])
    tidy = tidy[C.SCHEMA]

    existing = F.load_existing()
    if not existing.empty:
        pair = existing[(existing["run_date"] == tidy["run_date"].iloc[0])
                        & (existing["model"] == model_label)]
        if not pair.dropna(subset=C.VAR_COLS, how="all").empty:
            return False, ("Ce couple (run, modèle) contient déjà des données "
                           "valides dans le parquet — import refusé (le module "
                           "ne comble que des absences, jamais d'écrasement).")

    backup = None
    if os.path.exists(C.DB_PATH):
        backup = C.DB_PATH.replace(
            ".parquet", f"_backup_{datetime.now():%Y%m%d_%H%M%S}.parquet")
        shutil.copy2(C.DB_PATH, backup)

    combined = F.persist(tidy, existing)
    msg = (f"{len(tidy):,} lignes importées — {model_label}, run "
           f"{run_date:%d/%m/%Y} {run_date.hour}Z, {tidy['member'].nunique()} "
           f"membres, dernière échéance {tidy['valid_time'].max():%d/%m %Hh} UTC. "
           f"Base : {len(combined):,} lignes.")
    if backup:
        msg += f" Sauvegarde préalable : {os.path.basename(backup)}."
    return True, msg


def _cell_text(lead_days, members):
    """Contenu d'une cellule de matrice de présence : « 13.0 j · 51 m »."""
    if pd.isna(lead_days):
        return ""
    txt = f"{lead_days:.1f} j"
    if not pd.isna(members):
        txt += f"<br>{int(members)} m"
    return txt


def _presence_heatmap(mat, txt, missing, title, height):
    """Heatmap de présence : lignes = runs (plus récent en haut), colonnes =
    modèles, couleur = horizon en jours, texte = « horizon · membres ». Les
    cellules attendues mais absentes (`missing`) sont marquées d'une croix rouge."""
    fig = go.Figure(go.Heatmap(
        z=mat.values.astype(float), x=list(mat.columns), y=list(mat.index),
        text=txt.values, texttemplate="%{text}", textfont=dict(size=11),
        colorscale="YlGnBu", zmin=0, zmax=16.5, xgap=3, ygap=3,
        hoverongaps=False,
        colorbar=dict(title="Horizon<br>(jours)", thickness=12, len=0.9),
        hovertemplate="Run %{y}<br>Modèle %{x}<br>%{text}<extra></extra>"))
    for i, run in enumerate(mat.index):
        for j, model in enumerate(mat.columns):
            if bool(missing.iloc[i, j]):
                fig.add_annotation(x=model, y=run, text="✗", showarrow=False,
                                   font=dict(color="#C0392B", size=16, family="Arial Black"))
    fig.update_layout(title=title, height=height, template=_plotly_template(),
                      xaxis=dict(side="top"), yaxis=dict(autorange="reversed"),
                      margin=dict(t=90, l=10, r=10, b=10))
    return fig


def _missing_by_run(om):
    """{run_date -> set des modèles attendus à ce cycle mais ABSENTS du run}.

    Un modèle n'est « attendu » qu'à partir de sa PREMIÈRE apparition réelle dans la
    base (min run_utc), et seulement aux cycles où il publie (config `cycles`). Cela
    cale automatiquement l'attente sur le go-live de chaque modèle — ex. GEM n'est
    jamais signalé « absent » avant sa première collecte (30/06) — sans date en dur."""
    first_seen = om.groupby("model")["run_utc"].min().to_dict()
    # Un modèle avec lead_h NaN ou ≤ 0 (données uniquement pré-cycle, pur rebouchage)
    # est équivalent à une absence : ses données post-cycle seront prises ailleurs.
    _has_fut = om[om["lead_h"].fillna(0) > 0]
    present_by_rd = (_has_fut.groupby("run_date")["model"].agg(set)
                     if not _has_fut.empty else pd.Series(dtype=object))
    out = {}
    for rd in om["run_date"].unique():
        ru = _run_utc_naive(rd)
        pres = present_by_rd.get(rd, set())
        out[rd] = {m for m in C.MODEL_LABELS
                   if ru.hour in C.EXPECTED_CYCLES_BY_LABEL.get(m, [])
                   and m in first_seen and ru >= first_seen[m]
                   and m not in pres}
    return out


def _build_matrices(pres, run_order, models, missing_by_key):
    """(mat, txt, missing) pour _presence_heatmap, à partir d'une table de présence
    [run_key, model, lead_days, n_members] déjà agrégée. `missing_by_key` : run_key
    → set des modèles attendus mais absents (marqués d'une croix rouge)."""
    mat = pres.pivot_table(index="run_key", columns="model", values="lead_days",
                           aggfunc="max").reindex(index=run_order, columns=models)
    mem = pres.pivot_table(index="run_key", columns="model", values="n_members",
                           aggfunc="max").reindex(index=run_order, columns=models)
    txt = pd.DataFrame("", index=run_order, columns=models)
    missing = pd.DataFrame(False, index=run_order, columns=models)
    for r in run_order:
        miss = missing_by_key.get(r, set())
        for mo in models:
            txt.loc[r, mo] = _cell_text(mat.loc[r, mo], mem.loc[r, mo])
            if mo in miss:
                missing.loc[r, mo] = True
    return mat, txt, missing


def _nearest_om_run(om, model, ref_utc, window_h=12):
    """Ligne de présence Open-Meteo du modèle dont le cycle UTC est le plus proche
    de `ref_utc` (run legacy), dans une fenêtre de ±window_h. None si aucun."""
    sub = om[om["model"] == model]
    if sub.empty:
        return None
    gap = sub["run_utc"].map(lambda t: abs((t - ref_utc).total_seconds()) / 3600)
    cand = sub[gap <= window_h]
    if cand.empty:
        return None
    return sub.loc[gap.idxmin()]


def page_diagnostic(runs, sig):
    st.title("🩺 Contrôle de présence des modèles")
    st.caption(
        "Vue de fiabilisation du **double run** (Open-Meteo vs legacy/Météociel) : "
        "pour chaque run, quel modèle est présent, **jusqu'à quelle échéance** et avec "
        "combien de membres. Une croix rouge ✗ = modèle **attendu à ce cycle** mais "
        "absent. Objectif : repérer d'un coup d'œil les incohérences (modèle manquant, "
        "run anormalement tronqué, cycles désalignés, horizon divergent).")

    om = openmeteo_presence(sig)
    lg_raw = legacy_presence(legacy_signature())

    if om.empty and lg_raw.empty:
        st.warning("Aucune donnée : ni parquet Open-Meteo, ni fichier legacy exploitable.")
        return

    # ── Synthèse des anomalies (Open-Meteo) ────────────────────────────────── #
    st.subheader("⚠️ Anomalies détectées (Open-Meteo)")
    alerts = []
    om_missing = _missing_by_run(om) if not om.empty else {}
    if not om.empty:
        # Absences : à chaque run, un modèle attendu à ce cycle ET collecté à cette
        # époque (cf. _missing_by_run) mais introuvable dans le run.
        for run_date in sorted(om["run_date"].unique(), reverse=True):
            attendus = sorted(om_missing.get(run_date, set()))
            if attendus:
                alerts.append(f"🔴 **{run_label_text(run_date)}** — modèle(s) attendu(s) "
                              f"absent(s) : **{', '.join(attendus)}**.")
        # Horizon quasi nul / négatif : la fenêtre réellement fraîche est vide ou
        # avant le cycle (souvent une queue entièrement NaN-ifiée par mask_stale_tail).
        for _, r in om[om["lead_h"] <= 24].iterrows():
            alerts.append(f"🟠 **{run_label_text(r['run_date'])} · {r['model']}** — horizon "
                          f"anormalement court ({r['lead_h']:.0f} h de données fraîches "
                          "seulement). Run partiel/tronqué ou queue masquée ?")
    if alerts:
        st.markdown("\n\n".join(alerts))
    else:
        st.success("✅ Aucun modèle attendu manquant et aucun horizon anormalement court "
                   "sur les runs Open-Meteo archivés.")

    # ── Matrice Open-Meteo ─────────────────────────────────────────────────── #
    st.markdown("---")
    st.subheader("🛰️ Open-Meteo — présence & horizon par run")
    st.caption(
        "Chaque case : **horizon post-cycle** (dernière échéance non-NaN ≥ cycle − cycle) "
        "en jours et **nombre de membres**. Couleur = horizon. ✗ rouge = attendu mais absent "
        "ou données uniquement pré-cycle (rebouchage) — un modèle n'est attendu qu'à partir "
        "de sa 1re collecte réelle (GEM depuis le "
        f"{pd.Timestamp(C.PIPELINE_LIVE_SINCE):%d/%m}, cycles 6Z/18Z de même). Avant cette "
        "bascule, la base est rétro-remplie depuis les xlsx Météociel (migrate.py).")
    if om.empty:
        st.info("Base Open-Meteo vide.")
    else:
        om_disp = om.copy()
        om_disp["run_key"] = om_disp["run_date"].map(run_label_text)
        om_disp["lead_days"] = om_disp["lead_h"] / 24
        order_df = (om_disp[["run_key", "run_date"]].drop_duplicates()
                    .sort_values("run_date", ascending=False))
        run_order = order_df["run_key"].tolist()
        missing_by_key = {run_label_text(rd): om_missing.get(rd, set())
                          for rd in om_disp["run_date"].unique()}
        mat, txt, missing = _build_matrices(om_disp, run_order, C.MODEL_LABELS, missing_by_key)
        st.plotly_chart(
            _presence_heatmap(mat, txt, missing,
                              "Open-Meteo — horizon (jours) & membres par run",
                              height=max(320, 30 * len(run_order) + 120)),
            width="stretch")

    # ── Matrice legacy / Météociel ─────────────────────────────────────────── #
    st.markdown("---")
    st.subheader("📄 Legacy / Météociel — présence & horizon par run")
    st.caption("Runs scrapés sur Météociel (0Z/12Z uniquement). run_date = celui déclaré "
               "dans l'en-tête du xlsx ; en cas de re-scrape, seul le plus récent est retenu.")
    if lg_raw.empty:
        st.info("Aucun fichier legacy exploitable dans " + C.LEGACY_FORECASTS_DIR + ".")
        lg = lg_raw
    else:
        # Dédup : un même (run_date, modèle) peut avoir été scrapé plusieurs jours →
        # on garde le scrape le plus récent (le plus complet en principe).
        lg = (lg_raw.dropna(subset=["run_date"])
              .sort_values("scrape_date", ascending=False)
              .drop_duplicates(subset=["run_date", "model"], keep="first"))
        lg_disp = lg.copy()
        lg_disp["run_key"] = lg_disp["run_date"].map(
            lambda t: f"{t:%d %b %Y} — {t.hour:02d}Z")
        lg_disp["lead_days"] = lg_disp["lead_h"] / 24
        order_df = (lg_disp[["run_key", "run_date"]].drop_duplicates()
                    .sort_values("run_date", ascending=False))
        run_order = order_df["run_key"].tolist()
        leg_models = list(C.LEGACY_MODELS)
        # Météociel publie 0Z/12Z avec les 3 modèles legacy : tout modèle absent
        # d'un fichier est une anomalie (croix rouge).
        present_by_key = lg_disp.groupby("run_key")["model"].agg(set).to_dict()
        missing_by_key = {rk: set(leg_models) - present_by_key.get(rk, set())
                          for rk in run_order}
        mat, txt, missing = _build_matrices(lg_disp, run_order, leg_models, missing_by_key)
        st.plotly_chart(
            _presence_heatmap(mat, txt, missing,
                              "Météociel — horizon (jours) & membres par run",
                              height=max(320, 30 * len(run_order) + 120)),
            width="stretch")

    # ── Confrontation Open-Meteo ↔ legacy ──────────────────────────────────── #
    st.markdown("---")
    st.subheader("🔀 Confrontation Open-Meteo ↔ legacy (runs alignés)")
    live = pd.Timestamp(C.PIPELINE_LIVE_SINCE)
    st.caption(
        "Pour chaque run legacy, on cherche le run Open-Meteo du **même modèle** au cycle "
        "le plus proche (±12 h) et on confronte cycle, horizon et membres. Un ⚠️ signale "
        "une incohérence à investiguer : cycles désalignés "
        f"(> {C.CROSS_CHECK_RUN_ALIGN_TOL_H} h), écart d'horizon > 1 j, ou run Open-Meteo "
        f"introuvable côté modèle. Limitée aux runs **à partir du {live:%d/%m/%Y}** : avant, "
        "la base Open-Meteo est rétro-remplie depuis les xlsx Météociel (comparaison "
        "circulaire). Météociel ne publiant ni 6Z ni 18Z, ces cycles Open-Meteo n'ont "
        "légitimement aucun équivalent legacy et ne sont pas confrontés.")
    lg_live = lg[lg["run_date"] >= live] if not lg.empty else lg
    if om.empty or lg_live.empty:
        st.info(f"Aucun run legacy à partir du {live:%d/%m/%Y} à confronter "
                "(ou base Open-Meteo vide).")
    else:
        rows = []
        for _, lr in lg_live.iterrows():
            ref = lr["run_date"]
            om_row = _nearest_om_run(om, lr["model"], ref)
            gap_h = (abs((om_row["run_utc"] - ref).total_seconds()) / 3600
                     if om_row is not None else np.nan)
            lead_om = om_row["lead_h"] / 24 if om_row is not None else np.nan
            lead_lg = lr["lead_h"] / 24 if not pd.isna(lr["lead_h"]) else np.nan
            flags = []
            if om_row is None:
                flags.append("OM absent")
            else:
                if gap_h > C.CROSS_CHECK_RUN_ALIGN_TOL_H:
                    flags.append("cycles désalignés")
                if not pd.isna(lead_om) and not pd.isna(lead_lg) and abs(lead_om - lead_lg) > 1:
                    flags.append("horizon divergent")
            rows.append({
                "_sort": ref,
                "Run legacy": f"{ref:%d %b} {ref.hour:02d}Z",
                "Modèle": lr["model"],
                "Cycle OM": (f"{om_row['run_utc']:%d %b %HZ}" if om_row is not None else "—"),
                "Δ cycle (h)": round(gap_h, 1) if not pd.isna(gap_h) else np.nan,
                "Horizon OM (j)": round(lead_om, 1) if not pd.isna(lead_om) else np.nan,
                "Horizon legacy (j)": round(lead_lg, 1) if not pd.isna(lead_lg) else np.nan,
                "Membres OM": (int(om_row["n_members"]) if om_row is not None else np.nan),
                "Membres legacy": int(lr["n_members"]),
                "Alerte": " · ".join(f"⚠️ {f}" for f in flags),
            })
        comp = (pd.DataFrame(rows).sort_values(["_sort", "Modèle"], ascending=[False, True])
                .drop(columns="_sort"))
        n_flag = int((comp["Alerte"] != "").sum())
        if n_flag:
            st.warning(f"⚠️ {n_flag} ligne(s) présentent une incohérence — voir colonne « Alerte ».")
        else:
            st.success("✅ Tous les runs legacy s'alignent proprement sur un run Open-Meteo "
                       "(cycle et horizon cohérents).")
        styler = comp.style.apply(
            lambda r: ["background-color:#fdecea;color:#611a15" if r["Alerte"] else ""
                       for _ in r], axis=1
        ).format({"Δ cycle (h)": "{:.1f}", "Horizon OM (j)": "{:.1f}",
                  "Horizon legacy (j)": "{:.1f}", "Membres OM": "{:.0f}"}, na_rep="—")
        st.dataframe(styler, width="stretch", height=460, hide_index=True)


# --------------------------------------------------------------------------- #
#  Routage
# --------------------------------------------------------------------------- #
def main():
    sig = db_signature()
    runs = list_runs(sig)

    st.sidebar.title("🌦️ Navigation")
    pages = ["Indicateur de canicule", "Vue d'ensemble", "Explorer un run",
             "Convergence des runs", "Contrôle des runs"]
    if IS_LOCAL:
        pages.append("Lancer le pipeline")
    page = st.sidebar.radio("Aller à", pages)
    st.sidebar.markdown("---")
    st.sidebar.metric("Prévisions archivées", len(runs),
                      help="Nombre de runs (calculs) disponibles dans la base")
    if not runs.empty:
        st.sidebar.caption(f"Dernière : {runs.iloc[0]['label']}")
        refreshed_at, complete, missing = latest_refresh_status(runs, sig)
        if refreshed_at is not None:
            st.sidebar.caption(f"🕐 Rafraîchi le {refreshed_at.strftime('%d/%m/%Y à %Hh%M')}")
        if complete:
            st.sidebar.caption("✅ Tous les modèles attendus à ce run présents")
        else:
            st.sidebar.caption(f"⚠️ Données partielles — manque : {', '.join(missing)}")
    if st.sidebar.button("🔄 Rafraîchir"):
        st.cache_data.clear()
        st.rerun()
    st.sidebar.markdown("---")
    st.sidebar.markdown("<small>🕐 **Mise à jour automatique**<br>"
                        "4×/jour via Open-Meteo — runs 0Z/6Z/12Z/18Z "
                        "(GEM : 0Z/12Z uniquement)</small>",
                        unsafe_allow_html=True)
    st.sidebar.markdown("<small>Données : ECMWF · NOAA · ECCC</small>",
                        unsafe_allow_html=True)
    st.sidebar.markdown(f"<small>Version {APP_VERSION}</small>", unsafe_allow_html=True)

    if page == "Vue d'ensemble":
        page_overview(runs, sig)
    elif page == "Indicateur de canicule":
        page_grand_public(runs, sig)
    elif page == "Explorer un run":
        page_explore(runs, sig)
    elif page == "Convergence des runs":
        page_convergence(runs, sig)
    elif page == "Contrôle des runs":
        page_diagnostic(runs, sig)
    elif page == "Lancer le pipeline" and IS_LOCAL:
        page_run(sig)


if __name__ == "__main__":
    main()
