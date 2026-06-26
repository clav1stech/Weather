# -*- coding: utf-8 -*-
"""
Dashboard météo — Prévisions d'ensemble (Paris)
================================================
Application Streamlit pour :
  • Lancer un nouveau run (exécute Forecast.py pour le run 0Z ou 12Z)
  • Consulter tous les runs déjà produits dans le dossier Forecasts/
  • Visualiser tableaux + graphiques Température vs Temps :
      - panache (fan chart) de dispersion du super-ensemble
      - spaghetti des membres par modèle
      - comparaison des médianes ECMWF / AIFS / GEFS
      - convergence run-après-run pour une date cible
      - heatmaps d'évolution et de divergence
"""

import os
import re
import sys
import glob
import shutil
import tempfile
import subprocess
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --------------------------------------------------------------------------- #
#  Configuration générale
# --------------------------------------------------------------------------- #
# Version de l'app — à incrémenter manuellement à chaque évolution notable.
APP_VERSION = "1.0.5"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORECASTS_DIR = os.path.join(BASE_DIR, "Forecasts")
FORECAST_SCRIPT = os.path.join(BASE_DIR, "Forecast.py")

# Lancer un run n'a de sens qu'en local (sur le Cloud le conteneur est éphémère,
# le .xlsx produit serait perdu). On masque donc l'option sur Streamlit Cloud.
def _detect_local():
    """True si l'app tourne en local (PC), False sur Streamlit Community Cloud.

    Forçage explicite possible via WEATHER_LOCAL=1 / =0. Sinon heuristique : le Cloud
    tourne sous Linux et monte le dépôt dans /mount/src ; en local on autorise le run."""
    forced = os.environ.get("WEATHER_LOCAL")
    if forced in ("0", "1"):
        return forced == "1"
    base = BASE_DIR.replace("\\", "/")
    on_cloud = base.startswith("/mount/src") or \
        os.environ.get("HOSTNAME", "").startswith("streamlit")
    return not on_cloud


IS_LOCAL = _detect_local()

# Palette alignée sur les couleurs du fichier Excel généré par Forecast.py
MODEL_COLORS = {"ECMWF": "#1F618D", "AIFS": "#1E8449", "GEFS": "#B9770E"}
DET_LABEL = {"ECMWF": "DET", "AIFS": "DET", "GEFS": "GFS"}
DET_COL_NAMES = {"DET", "GFS"}

# Seuils à 850 hPa (~1500 m). Heuristique plaine France l'été : T_sol_max ≈ T850 + 15 °C.
#   • SEUIL_CHALEUR  : 15 °C @850 ≈ 30 °C au sol — ligne de repère « chaleur notable ».
#   • SEUIL_CANICULE : 20 °C @850 ≈ 35 °C au sol — seuil de canicule exceptionnelle ;
#     c'est lui qui pilote le risque (probabilité de dépassement → dégradé de couleur).
# À 850 hPa le cycle diurne est négligeable : on agrège la journée par mise en commun.
SEUIL_CHALEUR_850 = 14.0   # °C — ligne de repère sur le graphique
SEUIL_CANICULE_850 = 18.0  # °C — seuil de canicule exceptionnelle (pilote le risque)
NORMALE_CLIM_850 = 10.0    # °C — normale climatique de saison à 850 hPa (référence d'anomalie)

st.set_page_config(
    page_title="Dashboard Météo — Ensembles Paris",
    page_icon="🌡️",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
      h1, h2, h3 {letter-spacing: -0.01em;}
      div[data-testid="stMetric"] {
          background: #f6f8fb; border: 1px solid #e6ebf2;
          border-radius: 12px; padding: 12px 16px;
      }
      .stPlotlyChart {border: 1px solid #eef1f5; border-radius: 12px; padding: 4px;}
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
#  Découverte & lecture des fichiers
# --------------------------------------------------------------------------- #
RUN_HOURS = {"0Z": 0, "12Z": 12}


def parse_file_dt(path):
    """Retourne (datetime, date, run) à partir du nom Forecast-DDMMYYYY-{0Z|12Z}.xlsx."""
    m = re.search(r"Forecast-(\d{8})(?:-(0Z|12Z))?\.xlsx", os.path.basename(path))
    if not m:
        return None
    d = datetime.strptime(m.group(1), "%d%m%Y")
    run = m.group(2) or "12Z"
    return d + timedelta(hours=RUN_HOURS[run]), d.date(), run


@st.cache_data(show_spinner=False)
def list_runs(_mtime_signature):
    """Liste les runs disponibles, du plus récent au plus ancien."""
    rows = []
    for p in glob.glob(os.path.join(FORECASTS_DIR, "Forecast-*.xlsx")):
        if os.path.basename(p).startswith("~$"):
            continue
        parsed = parse_file_dt(p)
        if not parsed:
            continue
        dt, d, run = parsed
        rows.append(
            {
                "path": p,
                "datetime": dt,
                "date": d,
                "run": run,
                "label": f"{d.strftime('%d %b %Y')} — {run}",
            }
        )
    df = pd.DataFrame(rows).sort_values("datetime", ascending=False).reset_index(drop=True)
    return df


def runs_signature():
    """Signature basée sur les dates de modif → invalide le cache quand un run change."""
    sig = []
    for p in glob.glob(os.path.join(FORECASTS_DIR, "Forecast-*.xlsx")):
        try:
            sig.append((p, os.path.getmtime(p)))
        except OSError:
            pass
    return tuple(sorted(sig))


def _safe_read_excel(path, **kwargs):
    """Lit un Excel même s'il est verrouillé (ouvert dans Excel / synchro OneDrive)."""
    try:
        return pd.read_excel(path, **kwargs)
    except PermissionError:
        tmp = os.path.join(tempfile.gettempdir(), "meteo_" + os.path.basename(path))
        shutil.copy2(path, tmp)
        return pd.read_excel(tmp, **kwargs)


def list_sheets(path):
    try:
        return pd.ExcelFile(path).sheet_names
    except PermissionError:
        tmp = os.path.join(tempfile.gettempdir(), "meteo_" + os.path.basename(path))
        shutil.copy2(path, tmp)
        return pd.ExcelFile(tmp).sheet_names


def parse_valid_time(s):
    """'2026-06-23 12Z' → Timestamp; gère aussi un datetime déjà parsé."""
    if isinstance(s, (pd.Timestamp, datetime)):
        return pd.Timestamp(s)
    parts = str(s).split()
    base = pd.to_datetime(parts[0], errors="coerce")
    if pd.isna(base):
        return pd.NaT
    hour = 0
    if len(parts) > 1:
        m = re.search(r"(\d{1,2})Z", parts[1])
        if m:
            hour = int(m.group(1))
    return base + pd.Timedelta(hours=hour)


def _parse_date_column(series, fallback_year):
    """Parse une colonne Date hétérogène : datetime, 'YYYY-MM-DD', ou 'jj/mm'.
    Pour le format 'jj/mm' (sans année), on complète avec fallback_year."""
    out = pd.to_datetime(series, errors="coerce", format="mixed", dayfirst=False)
    miss = out.isna()
    if miss.any():
        # Reparse les jj/mm en injectant l'année du run
        patched = series[miss].astype(str) + f"/{fallback_year}"
        out.loc[miss] = pd.to_datetime(patched, format="%d/%m/%Y", errors="coerce")
    return out


@st.cache_data(show_spinner=False)
def load_synthese(path, _sig):
    """Feuille Synthèse (super-ensemble). Header en ligne Excel 5 → skiprows=4."""
    sheets = list_sheets(path)
    target = next((s for s in sheets if s.strip().lower().startswith("synth")), None)
    if target is None:
        return None
    df = _safe_read_excel(path, sheet_name=target, skiprows=4)
    df = df.dropna(how="all")
    if "Date" not in df.columns:
        return None
    parsed = parse_file_dt(path)
    year = parsed[1].year if parsed else datetime.now().year
    df["valid_time"] = _parse_date_column(df["Date"], year) + pd.Timedelta(hours=12)
    df["Ech"] = pd.to_numeric(df.get("Ech"), errors="coerce")
    df = df.dropna(subset=["valid_time"]).reset_index(drop=True)
    return df


@st.cache_data(show_spinner=False)
def load_comparaison(path, _sig):
    """Feuille 'Comparaison Modèles' si présente. Header ligne Excel 5 → skiprows=4."""
    sheets = list_sheets(path)
    target = next((s for s in sheets if "comparaison" in s.strip().lower()), None)
    if target is None:
        return None
    df = _safe_read_excel(path, sheet_name=target, skiprows=4)
    df = df.dropna(how="all")
    if "Date" not in df.columns:
        return None
    parsed = parse_file_dt(path)
    year = parsed[1].year if parsed else datetime.now().year
    df["valid_time"] = _parse_date_column(df["Date"], year) + pd.Timedelta(hours=12)
    df["Ech"] = pd.to_numeric(df.get("Ech"), errors="coerce")
    return df.dropna(subset=["valid_time"]).reset_index(drop=True)


def available_models(path):
    return [s for s in list_sheets(path) if s in MODEL_COLORS]


@st.cache_data(show_spinner=False)
def load_model(path, sheet, _sig):
    """Feuille modèle (ECMWF/AIFS/GEFS). Header ligne Excel 4 → skiprows=3.
    Retourne (df_long_stats, members_df, det_series)."""
    df = _safe_read_excel(path, sheet_name=sheet, skiprows=3)
    df = df.dropna(how="all")
    if "Date" not in df.columns:
        return None
    df["valid_time"] = df["Date"].apply(parse_valid_time)
    df = df.dropna(subset=["valid_time"]).reset_index(drop=True)

    det_col = next((c for c in df.columns if str(c) in DET_COL_NAMES), None)
    skip = {"Date", "Ech.", "valid_time"}
    if det_col is not None:
        skip.add(det_col)
    member_cols = [c for c in df.columns if c not in skip]

    members = df[member_cols].apply(pd.to_numeric, errors="coerce")
    stats = pd.DataFrame({"valid_time": df["valid_time"]})
    stats["Ech"] = pd.to_numeric(df.get("Ech."), errors="coerce")
    stats["min"] = members.min(axis=1)
    stats["p10"] = members.quantile(0.10, axis=1)
    stats["p25"] = members.quantile(0.25, axis=1)
    stats["median"] = members.median(axis=1)
    stats["p75"] = members.quantile(0.75, axis=1)
    stats["p90"] = members.quantile(0.90, axis=1)
    stats["max"] = members.max(axis=1)
    stats["spread"] = (stats["p90"] - stats["p10"]).round(2)
    stats["n_members"] = members.notna().sum(axis=1)
    det = pd.to_numeric(df[det_col], errors="coerce") if det_col is not None else None
    members.index = df["valid_time"]
    return stats, members, det


_PCT_COLS = ["Min", "P10", "P25", "Médiane", "P75", "P90", "Max"]


@st.cache_data(show_spinner=False)
def _pooled_members(path, _sig, grid_hours=(0, 6, 12, 18), truncate=True):
    """Matrice des membres bruts pooled (ECMWF + AIFS + GEFS) sur la grille commune.

    BASE COMMUNE de tous les calculs de l'interface. Repool tous les membres des
    feuilles modèles, restreint à la grille temporelle (6h par défaut) et, si
    `truncate`, coupe la queue où il ne reste qu'un seul modèle (GEFS au long terme).

    Retourne (allm, n_models, det_series) où :
      • allm : DataFrame index=valid_time, colonnes=membres préfixés « MODELE_x » ;
      • n_models : Series (nb de modèles présents par échéance) ;
      • det_series : {« MODELE DET » : Series indexée valid_time}.
    Insensible au bug historique du déterministe GEFS (« GFS » exclu par load_model).
    """
    models = available_models(path)
    member_frames, presence, det_series = [], [], {}
    for model in models:
        loaded = load_model(path, model, _sig)
        if not loaded:
            continue
        stats, members, det = loaded  # members : index = valid_time, déterministe exclu
        m = members.apply(pd.to_numeric, errors="coerce")
        m.columns = [f"{model}_{c}" for c in m.columns]
        member_frames.append(m)
        presence.append(m.notna().any(axis=1).rename(model))
        if det is not None:
            det_series[f"{model} DET"] = pd.Series(
                pd.to_numeric(det, errors="coerce").values, index=stats["valid_time"])
    if not member_frames:
        return None

    allm = pd.concat(member_frames, axis=1).sort_index()
    pres = pd.concat(presence, axis=1).reindex(allm.index)
    n_models = (pres == True).sum(axis=1)  # NaN (modèle absent) compte comme False
    keep_grid = allm.index.to_series().dt.hour.isin(grid_hours)
    allm, n_models = allm[keep_grid], n_models[keep_grid]
    if allm.empty:
        return None
    if truncate:
        multi = allm.index[n_models.values >= 2]
        if len(multi):
            allm, n_models = allm.loc[:multi[-1]], n_models.loc[:multi[-1]]
    return allm, n_models, det_series


@st.cache_data(show_spinner=False)
def super_ensemble(path, _sig, grid_hours=(0, 6, 12, 18), truncate=True):
    """SOURCE UNIQUE de l'interface : super-ensemble recalculé depuis les membres bruts.

    Recalcule TOUTES les statistiques par échéance (au lieu de relire la feuille
    Synthèse précalculée 12Z). Voir _pooled_members pour la base de calcul.

    Retourne un DataFrame : valid_time, Min/P10/P25/Médiane/P75/P90/Max, Spread,
    Ecart-type, Proba > 20°, n_membres, n_models, et une colonne « <MODELE> DET ».
    """
    pooled = _pooled_members(path, _sig, grid_hours, truncate)
    if pooled is None:
        return None
    allm, n_models, det_series = pooled

    out = pd.DataFrame({"valid_time": allm.index})
    out["Min"] = allm.min(axis=1).values
    out["P10"] = allm.quantile(0.10, axis=1).values
    out["P25"] = allm.quantile(0.25, axis=1).values
    out["Médiane"] = allm.median(axis=1).values
    out["P75"] = allm.quantile(0.75, axis=1).values
    out["P90"] = allm.quantile(0.90, axis=1).values
    out["Max"] = allm.max(axis=1).values
    out[_PCT_COLS] = out[_PCT_COLS].round(2)
    out["Spread"] = (out["P90"] - out["P10"]).round(2)
    out["Ecart-type"] = allm.std(axis=1).round(2).values
    out["Proba > 20°"] = (allm.gt(20).sum(axis=1) / allm.notna().sum(axis=1)).fillna(0).values
    out["n_membres"] = allm.notna().sum(axis=1).values
    out["n_models"] = n_models.astype(int).values
    for name, s in det_series.items():
        out[name] = s.reindex(allm.index).round(1).values
    return out.reset_index(drop=True)


def super_ensemble_daily(path, sig, **kw):
    """Super-ensemble agrégé à 1 ligne/jour (MOYENNE des échéances du jour).

    À 850 hPa le cycle diurne est négligeable : la moyenne journalière est
    l'estimateur le plus robuste et sans biais. `valid_time` est repositionné à 12Z.
    """
    se = super_ensemble(path, sig, **kw)
    if se is None or se.empty:
        return se
    se = se.copy()
    se["date"] = pd.to_datetime(se["valid_time"]).dt.normalize()
    num = [c for c in se.columns if c not in ("valid_time", "date")]
    out = se.groupby("date")[num].mean().reset_index()
    out["valid_time"] = out["date"] + pd.Timedelta(hours=12)
    out[_PCT_COLS] = out[_PCT_COLS].round(2)
    return out


def multimodel_cutoff(path, sig):
    """Dernière échéance où ≥ 2 modèles sont présents (au-delà : GEFS seul)."""
    se = super_ensemble(path, sig, truncate=True)
    if se is None or se.empty:
        return None
    return pd.Timestamp(se["valid_time"].max())


def daily_canicule_risk(path, sig, seuil, grid_hours=(0, 6, 12, 18)):
    """Risque de canicule par jour, basé sur la PROBABILITÉ de dépasser `seuil`.

    Met en commun tous les membres des échéances du jour (cycle diurne négligeable
    à 850 hPa) et calcule, pour chaque journée :
      • Médiane, P75, P90 (sur le pool de membres du jour) ;
      • prob = fraction de membres ≥ seuil (probabilité de canicule exceptionnelle).
    La couleur de la frise est un dégradé continu piloté par `prob` (vert→jaune→
    orange→rouge), conformément à : vert = pas de signal (hors dernier décile),
    rouge = canicule quasi-sûre (médiane au-dessus du seuil).
    """
    pooled = _pooled_members(path, sig, grid_hours, truncate=True)
    if pooled is None:
        return None
    allm = pooled[0]
    dates = pd.to_datetime(allm.index).normalize()
    rows = []
    for date, grp in allm.groupby(dates):
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
        })
    return pd.DataFrame(rows)


def _canicule_label(prob):
    """Niveau de risque canicule (pour le survol)."""
    if prob >= 0.50:
        return "🔴 Canicule quasi-certaine"
    if prob >= 0.25:
        return "🟠 Risque marqué"
    if prob >= 0.10:
        return "🟡 Risque modéré"
    return "🟢 Pas de signal de canicule"


# Dégradé continu du risque canicule (P de dépassement, de 0 à 1).
CANICULE_SCALE = [
    [0.00, "#2ECC71"],  # vert   — aucun signal
    [0.10, "#A9DC76"],  # vert-jaune — limite « dernier décile »
    [0.25, "#F1C40F"],  # jaune  — risque modéré (≈ P75 au seuil)
    [0.40, "#E67E22"],  # orange — risque marqué
    [0.50, "#E74C3C"],  # rouge  — médiane au seuil → quasi-sûr
    [1.00, "#C0392B"],  # rouge foncé — certain
]


# --------------------------------------------------------------------------- #
#  Composants graphiques
# --------------------------------------------------------------------------- #
def _band(fig, x, lo, hi, color, name, opacity=0.18):
    fig.add_trace(go.Scatter(x=x, y=hi, mode="lines", line=dict(width=0),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=x, y=lo, mode="lines", line=dict(width=0), fill="tonexty",
        fillcolor=_rgba(color, opacity), name=name, hoverinfo="skip"))


def _rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def fan_chart(syn, title):
    """Panache de dispersion du super-ensemble : Min–Max, P10–P90, P25–P75, médiane."""
    x = syn["valid_time"]
    fig = go.Figure()
    base = "#2C3E50"
    _band(fig, x, syn["min"] if "min" in syn else syn["Min"],
          syn["max"] if "max" in syn else syn["Max"], base, "Min–Max", 0.08)
    _band(fig, x, syn.get("p10", syn.get("P10")), syn.get("p90", syn.get("P90")),
          base, "P10–P90", 0.16)
    _band(fig, x, syn.get("p25", syn.get("P25")), syn.get("p75", syn.get("P75")),
          base, "P25–P75 (50 %)", 0.28)
    med = syn.get("median", syn.get("Médiane"))
    fig.add_trace(go.Scatter(x=x, y=med, mode="lines+markers", name="Médiane",
                             line=dict(color="#E74C3C", width=3),
                             marker=dict(size=5)))
    fig.update_layout(
        title=title, height=480, hovermode="x unified",
        xaxis_title="Échéance (date de validité)", yaxis_title="Température (°C)",
        legend=dict(orientation="h", y=1.08), template="plotly_white",
        margin=dict(t=70, l=10, r=10, b=10),
    )
    return fig


def spaghetti_chart(members, stats, det, model, det_label):
    """Tous les membres d'ensemble (fins) + médiane + run déterministe."""
    fig = go.Figure()
    color = MODEL_COLORS.get(model, "#888")
    x = members.index
    for col in members.columns:
        fig.add_trace(go.Scatter(
            x=x, y=members[col], mode="lines",
            line=dict(color=_rgba(color, 0.22), width=0.8),
            hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=stats["valid_time"], y=stats["median"], mode="lines",
        name="Médiane", line=dict(color=color, width=3.5)))
    if det is not None:
        fig.add_trace(go.Scatter(
            x=stats["valid_time"], y=det.values, mode="lines",
            name=f"Déterministe ({det_label})",
            line=dict(color="#111", width=2, dash="dash")))
    fig.update_layout(
        title=f"Spaghetti des membres — {model} ({members.shape[1]} scénarios)",
        height=480, hovermode="x unified", template="plotly_white",
        xaxis_title="Échéance (date de validité)", yaxis_title="Température (°C)",
        legend=dict(orientation="h", y=1.08), margin=dict(t=70, l=10, r=10, b=10),
    )
    return fig


def models_median_chart(path, sig, models, cutoff=None):
    """Comparaison des médianes (et enveloppe P10–P90) des modèles.

    `cutoff` : tronque toutes les courbes à cette échéance (graphique multi-modèles
    → on n'affiche pas la queue où seul GEFS subsiste)."""
    fig = go.Figure()
    for model in models:
        loaded = load_model(path, model, sig)
        if not loaded:
            continue
        stats, _, det = loaded
        if cutoff is not None:
            mask = stats["valid_time"] <= cutoff
            stats = stats[mask]
            if det is not None:
                det = det[mask.values]
        c = MODEL_COLORS[model]
        _band(fig, stats["valid_time"], stats["p10"], stats["p90"], c,
              f"{model} P10–P90", 0.12)
        fig.add_trace(go.Scatter(
            x=stats["valid_time"], y=stats["median"], mode="lines",
            name=f"{model} médiane", line=dict(color=c, width=2.8)))
        if det is not None and det.notna().any():
            fig.add_trace(go.Scatter(
                x=stats["valid_time"], y=det.values, mode="lines",
                name=f"{model} déterministe",
                line=dict(color=c, width=1.6, dash="dot")))
    fig.update_layout(
        title="Comparaison des modèles — médiane, dispersion & déterministe",
        height=480, hovermode="x unified", template="plotly_white",
        xaxis_title="Échéance (date de validité)", yaxis_title="Température (°C)",
        legend=dict(orientation="h", y=1.1), margin=dict(t=80, l=10, r=10, b=10),
    )
    return fig


def divergence_from_raw(path, sig, models, cutoff=None):
    """Divergence inter-modèles recalculée depuis le brut : médiane la + chaude −
    la + froide, par échéance, restreinte aux pas où ≥ 2 modèles sont présents."""
    meds = {}
    for model in models:
        loaded = load_model(path, model, sig)
        if not loaded:
            continue
        stats, _, _ = loaded
        meds[model] = pd.Series(stats["median"].values, index=stats["valid_time"])
    if len(meds) < 2:
        return None
    df = pd.concat(meds, axis=1).sort_index().dropna(thresh=2)  # ≥ 2 modèles comparables
    if cutoff is not None:
        df = df[df.index <= cutoff]
    if df.empty:
        return None
    div = (df.max(axis=1) - df.min(axis=1)).round(2)
    return pd.DataFrame({"valid_time": div.index, "Divergence": div.values})


def spread_chart(syn):
    """Incertitude (spread P90-P10 et écart-type) en fonction de l'échéance."""
    fig = go.Figure()
    if "Spread" in syn:
        fig.add_trace(go.Bar(x=syn["valid_time"], y=syn["Spread"], name="Spread (P90−P10)",
                             marker_color=_rgba("#2980B9", 0.55)))
    if "Ecart-type" in syn:
        fig.add_trace(go.Scatter(x=syn["valid_time"], y=syn["Ecart-type"],
                                 name="Écart-type", yaxis="y2",
                                 line=dict(color="#C0392B", width=2.5)))
    fig.update_layout(
        title="Incertitude de la prévision selon l'échéance",
        height=360, template="plotly_white", hovermode="x unified",
        xaxis_title="Échéance", yaxis_title="Spread (°C)",
        yaxis2=dict(title="Écart-type (°C)", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.15), margin=dict(t=70, l=10, r=10, b=10),
    )
    return fig


# --------------------------------------------------------------------------- #
#  Pages
# --------------------------------------------------------------------------- #
def _kpi_card(label, value, help_txt="", anomalie=None):
    """Carte KPI maison (style proche de st.metric) avec, en option, l'anomalie vs
    normale climatique affichée **à côté** de la valeur (rouge si +, bleu si −)."""
    anomalie_html = ""
    if anomalie is not None:
        delta = anomalie - NORMALE_CLIM_850
        if delta >= 0.05:
            couleur, signe = "#C0392B", "+"
        elif delta <= -0.05:
            couleur, signe = "#2980B9", "−"
        else:
            couleur, signe = "#7F8C8D", "±"
        anomalie_html = (
            f"<span style='color:{couleur};font-size:0.95rem;font-weight:600;"
            f"margin-left:8px;white-space:nowrap;'>"
            f"({signe}{abs(delta):.1f} °C norm.)</span>")
    title_attr = f' title="{help_txt}"' if help_txt else ""
    return (
        f"<div{title_attr} style='background:#f6f8fb;border:1px solid #e6ebf2;"
        "border-radius:12px;padding:12px 16px;height:100%;'>"
        f"<div style='font-size:0.8rem;color:#5b6b7f;'>{label}</div>"
        f"<div style='font-size:1.85rem;font-weight:600;color:#1a2330;line-height:1.3;'>"
        f"{value}{anomalie_html}</div>"
        "</div>")


def page_overview(runs, sig):
    st.title("🌡️ Dashboard Météo — Prévisions d'ensemble (Paris)")
    if runs.empty:
        st.warning("Aucun fichier de prévision trouvé dans le dossier `Forecasts/`.")
        return

    latest = runs.iloc[0]
    st.caption(f"Dernière prévision : **{latest['label']}** · "
               f"{len(runs)} prévisions (runs) archivées · températures à 850 hPa")

    syn = super_ensemble(latest["path"], sig)
    if syn is None or syn.empty:
        st.error("Aucune feuille modèle exploitable pour ce run.")
        return

    # KPI sur la première échéance exploitable
    c1, c2, c3, c4 = st.columns(4)
    med_col = "Médiane"
    today_row = syn.iloc[0]
    c1.markdown(
        _kpi_card("Prochaine échéance (médiane)", f"{today_row[med_col]:.1f} °C",
                  "Scénario central pour la première échéance à venir",
                  anomalie=today_row[med_col]),
        unsafe_allow_html=True)
    c2.markdown(
        _kpi_card("Dispersion moyenne", f"{syn['Spread'].mean():.1f} °C",
                  "Largeur moyenne de la fourchette des scénarios (spread P90−P10) "
                  "sur toutes les échéances — indicateur d'incertitude"),
        unsafe_allow_html=True)
    peak = syn.loc[syn[med_col].idxmax()]
    c3.markdown(
        _kpi_card("Pic de chaleur prévu (médiane)", f"{peak[med_col]:.1f} °C",
                  f"Maximum du scénario central · {peak['valid_time'].strftime('%d %b %Hh')}",
                  anomalie=peak[med_col]),
        unsafe_allow_html=True)
    c4.markdown(
        _kpi_card("Échéances > 20 °C (médiane)", f"{int((syn[med_col] > 20).sum())}",
                  "Nombre d'échéances où le scénario central dépasse 20 °C à 850 hPa"),
        unsafe_allow_html=True)

    st.caption(
        "**Panache de dispersion** : la ligne rouge est le scénario central (médiane) ; "
        "les bandes, du clair au foncé, regroupent une part croissante des scénarios "
        "(extrêmes Min–Max, puis 80 % P10–P90, puis 50 % central P25–P75). "
        "Bandes étroites = prévision sûre ; bandes larges = forte incertitude.")
    st.plotly_chart(fan_chart(syn, f"Panache du super-ensemble — {latest['label']}"),
                    use_container_width=True)

    models = available_models(latest["path"])
    if models:
        cutoff = multimodel_cutoff(latest["path"], sig)
        st.caption(
            "Trait plein = **médiane** de chaque modèle, bande = sa dispersion (P10–P90). "
            "Pointillés = **run déterministe** (prévision unique « haute résolution ») : "
            "utile pour voir si le scénario principal s'écarte du centre de son ensemble.")
        st.plotly_chart(models_median_chart(latest["path"], sig, models, cutoff),
                        use_container_width=True)


def page_explore(runs, sig):
    st.title("📊 Explorer une prévision (run)")
    st.caption(
        "Vue détaillée d'un même run sous plusieurs angles : panache de dispersion, "
        "scénarios individuels, comparaison des modèles, incertitude et tableaux de données.")
    if runs.empty:
        st.warning("Aucun run disponible.")
        return

    idx = st.selectbox("Choisir un run (date + heure du calcul)", runs.index,
                       format_func=lambda i: runs.loc[i, "label"])
    run = runs.loc[idx]
    path = run["path"]
    if IS_LOCAL:
        st.caption(f"Fichier : `{os.path.basename(path)}`")

    syn = super_ensemble(path, sig)
    models = available_models(path)
    cutoff = multimodel_cutoff(path, sig)

    # Signale les modèles absents de ce run (super-ensemble appauvri → moins fiable)
    manquants = [m for m in MODEL_COLORS if m not in models]
    if manquants:
        st.warning(
            f"⚠️ Modèle(s) absent(s) de ce run : **{', '.join(manquants)}**. "
            "Le super-ensemble repose donc sur moins de modèles : "
            "la prévision est **moins fiable** (dispersion possiblement sous-estimée).")

    tab_fan, tab_spag, tab_cmp, tab_unc, tab_tbl = st.tabs(
        ["📈 Panache", "🍝 Spaghetti", "⚖️ Modèles", "📉 Incertitude", "🧾 Tableaux"]
    )

    with tab_fan:
        if syn is not None and not syn.empty:
            st.plotly_chart(fan_chart(syn, f"Super-ensemble — {run['label']}"),
                            use_container_width=True)
        else:
            st.info("Aucune feuille modèle exploitable dans ce run.")

    with tab_spag:
        if models:
            model = st.radio("Modèle", models, horizontal=True, key="spag_model")
            loaded = load_model(path, model, sig)
            if loaded:
                stats, members, det = loaded
                st.plotly_chart(
                    spaghetti_chart(members, stats, det, model, DET_LABEL.get(model, "DET")),
                    use_container_width=True)
        else:
            st.info("Aucune feuille modèle individuelle dans ce run.")

    with tab_cmp:
        if models:
            st.plotly_chart(models_median_chart(path, sig, models, cutoff),
                            use_container_width=True)
            div = divergence_from_raw(path, sig, models, cutoff)
            if div is not None:
                figd = go.Figure()
                figd.add_trace(go.Bar(
                    x=div["valid_time"], y=div["Divergence"],
                    marker_color=_rgba("#7D3C98", 0.6), name="Divergence"))
                figd.update_layout(
                    title="Divergence inter-modèles (médiane la + chaude − la + froide)",
                    height=320, template="plotly_white",
                    xaxis_title="Échéance", yaxis_title="Écart (°C)",
                    margin=dict(t=60, l=10, r=10, b=10))
                st.plotly_chart(figd, use_container_width=True)
        else:
            st.info("Comparaison indisponible (pas de feuilles modèles).")

    with tab_unc:
        if syn is not None and not syn.empty:
            st.plotly_chart(spread_chart(syn), use_container_width=True)
        else:
            st.info("Pas de données d'incertitude.")

    with tab_tbl:
        # Tables recalculées depuis les membres bruts (priorité) + feuilles modèles brutes.
        # On masque les feuilles Synthèse/Comparaison stockées : superseded par le recalcul.
        recomputed = {
            "Super-ensemble (recalculé, infra-journalier)": lambda: syn,
            "Super-ensemble (recalculé, journalier 12Z)": lambda: super_ensemble_daily(path, sig),
        }
        raw_sheets = [s for s in list_sheets(path) if s in MODEL_COLORS]
        choices = list(recomputed) + raw_sheets
        sheet = st.selectbox("Table", choices, key="tbl_sheet")
        if sheet in recomputed:
            raw = recomputed[sheet]()
            if raw is not None:
                raw = raw.drop(columns=["date"], errors="ignore")
        else:
            raw = _safe_read_excel(path, sheet_name=sheet, skiprows=3).dropna(how="all")
        if raw is None or raw.empty:
            st.info("Table indisponible pour ce run.")
        else:
            styler = raw
            # Coloration douce des colonnes de température numériques
            num_cols = raw.select_dtypes(include="number").columns
            if len(num_cols):
                styler = raw.style.background_gradient(
                    cmap="RdYlBu_r", subset=list(num_cols), axis=None).format(precision=1)
            st.dataframe(styler, use_container_width=True, height=520)
            st.download_button(
                "⬇️ Télécharger la table (CSV)",
                raw.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{os.path.splitext(os.path.basename(path))[0]}_{sheet[:24]}.csv",
                mime="text/csv")


def page_convergence(runs, sig):
    st.title("🔄 Révisions & convergence des prévisions")
    st.caption(
        "Chaque nouveau calcul (run) corrige le précédent. Cette page montre **comment la "
        "prévision d'une même date a évolué d'un run à l'autre** : si elle se stabilise, "
        "on peut s'y fier ; si elle bouge encore beaucoup, l'incertitude reste forte.")

    if len(runs) < 2:
        st.warning("Il faut au moins 2 runs pour analyser la convergence.")
        return

    # --- Historique commun : médiane/P10/P90 journalières de CHAQUE run (recalcul brut) ---
    records = []
    for _, r in runs.iterrows():
        syn = super_ensemble_daily(r["path"], sig)
        if syn is None or syn.empty:
            continue
        for _, row in syn.iterrows():
            target = pd.Timestamp(row["valid_time"]).normalize()
            days_before = (target - pd.Timestamp(r["datetime"]).normalize()).days
            if days_before < 0:
                continue
            # Délai réel run → échéance (en jours, fractionnaire) : sépare 0Z et 12Z d'un
            # même jour → supprime les dents de scie du graphique de convergence.
            lead = (pd.Timestamp(row["valid_time"]) - pd.Timestamp(r["datetime"])).total_seconds() / 86400
            records.append({
                "run_dt": r["datetime"],
                "days_before": days_before,
                "lead": lead,
                "target": target,
                "median": row.get("Médiane"),
                "p10": row.get("P10"),
                "p90": row.get("P90"),
            })
    long = pd.DataFrame(records).dropna(subset=["median"])
    if long.empty:
        st.warning("Données insuffisantes (aucune feuille modèle exploitable).")
        return

    # Runs réellement présents dans les graphiques et privés d'au moins un modèle.
    # incomplets : {datetime du run -> liste des modèles manquants}
    runs_affiches = set(long["run_dt"].unique())
    incomplets = {
        r["datetime"]: [m for m in MODEL_COLORS if m not in available_models(r["path"])]
        for _, r in runs.iterrows() if r["datetime"] in runs_affiches
    }
    incomplets = {dt: miss for dt, miss in incomplets.items() if miss}
    label_par_dt = {r["datetime"]: r["label"] for _, r in runs.iterrows()}

    def _run_tick(dt):
        """Libellé d'un run pour les graphiques ; italique + astérisque si modèle manquant."""
        s = pd.Timestamp(dt).strftime("%d %b %Hh")
        return f"<i>{s}*</i>" if dt in incomplets else s

    if incomplets:
        detail = " · ".join(f"{label_par_dt[dt]} (manque {', '.join(incomplets[dt])})"
                            for dt in sorted(incomplets, reverse=True))
        st.warning(
            "⚠️ Certains runs affichés n'ont pas tous les modèles, leur super-ensemble est "
            "appauvri : les révisions qui les concernent sont **moins fiables**. "
            "Dans les graphiques, ces runs sont notés en *italique* avec un astérisque (\\*).\n\n"
            f"{detail}")

    # --- Sélection du run de référence pour les révisions ---
    idx = st.selectbox(
        "Run de référence pour les révisions",
        runs.index,
        format_func=lambda i: runs.loc[i, "label"],
        key="conv_run_sel",
    )
    ref_run = runs.loc[idx]

    # ── 1. Révisions : médiane du run de référence − médiane des runs précédents ──
    st.subheader("📐 Révisions de la médiane vs runs précédents")
    st.caption(
        "Chaque barre = combien la médiane de **ce run** a bougé par rapport à un run "
        "antérieur, pour une même date prévue. Rouge = hausse, bleu = baisse."
    )
    ref_med = long[long["run_dt"] == ref_run["datetime"]].set_index("target")["median"]
    prev_runs = [rd for rd in sorted(long["run_dt"].unique(), reverse=True)
                 if rd < ref_run["datetime"]][:5]
    if not ref_med.empty and prev_runs:
        fig_rev = go.Figure()
        for i, pr in enumerate(prev_runs):
            prev_med = long[long["run_dt"] == pr].set_index("target")["median"]
            delta = (ref_med - prev_med).dropna()
            if delta.empty:
                continue
            colors = [_rgba("#E74C3C", 0.8) if v > 0 else _rgba("#2980B9", 0.8)
                      if v < 0 else _rgba("#888888", 0.4) for v in delta.values]
            fig_rev.add_trace(go.Bar(
                x=delta.index, y=delta.values, offsetgroup=i,
                name="Δ vs " + _run_tick(pr),
                marker_color=colors,
            ))
        fig_rev.add_hline(y=0, line_color="#333", line_width=1.5)
        fig_rev.update_layout(
            height=380, template="plotly_white", hovermode="x unified",
            barmode="group",
            xaxis_title="Date prévue", yaxis_title="Révision (°C)",
            legend=dict(orientation="h", y=1.12),
            margin=dict(t=30, l=10, r=10, b=10),
        )
        st.plotly_chart(fig_rev, use_container_width=True)
    else:
        st.info("Pas de run antérieur comparable à ce run de référence.")

    st.markdown("---")

    # ── 2. Convergence : médiane d'une date cible vue J-N jours avant ──
    st.subheader("📈 Comment la prévision a évolué au fil des jours")
    st.caption(
        "**Un panneau = une date cible.** Dans chacun, la courbe montre comment la médiane "
        "prévue a évolué selon l'ancienneté du run ; la bande = l'incertitude P10–P90. "
        "À droite (J-0) = prévision la plus récente, à gauche = prévision lointaine. "
        "Une courbe qui se stabilise vers la droite = le modèle a convergé. "
        "Un panneau qui s'arrête tôt (ex. à J-4) est **normal** : il n'existe pas encore de "
        "run plus proche de cette date (échéance future)."
    )

    targets = sorted(long["target"].unique())
    default_t = (
        [t for t in targets if t >= pd.Timestamp(datetime.now().date())][:5]
        or targets[-5:]
    )
    chosen = st.multiselect(
        "Dates cibles à suivre",
        targets,
        default=default_t,
        format_func=lambda t: pd.Timestamp(t).strftime("%d %b %Y"),
        key="conv_targets",
    )

    if chosen:
        palette = ["#E74C3C", "#2980B9", "#27AE60", "#8E44AD", "#E67E22",
                   "#16A085", "#C0392B", "#2C3E50"]
        chosen_sorted = sorted(chosen)
        n = len(chosen_sorted)
        ncols = min(3, n)
        nrows = (n + ncols - 1) // ncols
        titles = [pd.Timestamp(t).strftime("%d %b") for t in chosen_sorted]

        sub = long[long["target"].isin(chosen_sorted)]
        max_lead = float(sub["lead"].max())
        ymin = float(sub["p10"].min())
        ymax = float(sub["p90"].max())
        marge = max(0.5, (ymax - ymin) * 0.08)

        fig_conv = make_subplots(
            rows=nrows, cols=ncols, subplot_titles=titles,
            horizontal_spacing=0.04, vertical_spacing=0.14)

        for i, t in enumerate(chosen_sorted):
            r, cpos = i // ncols + 1, i % ncols + 1
            d = long[long["target"] == t].sort_values("lead", ascending=False)
            if d.empty:
                continue
            c = palette[i % len(palette)]
            label = titles[i]
            # Enveloppe P10–P90 (bande propre à ce panneau)
            fig_conv.add_trace(go.Scatter(
                x=d["lead"], y=d["p90"], mode="lines", line=dict(width=0),
                hoverinfo="skip", showlegend=False), row=r, col=cpos)
            fig_conv.add_trace(go.Scatter(
                x=d["lead"], y=d["p10"], mode="lines", line=dict(width=0),
                fill="tonexty", fillcolor=_rgba(c, 0.15),
                hoverinfo="skip", showlegend=False), row=r, col=cpos)
            # Médiane
            fig_conv.add_trace(go.Scatter(
                x=d["lead"], y=d["median"], mode="lines+markers",
                line=dict(color=c, width=2.5), marker=dict(size=5),
                showlegend=False, customdata=d[["p10", "p90"]].values,
                hovertemplate=(f"{label} — J-%{{x:.1f}}<br>Médiane : %{{y:.1f}} °C"
                               "<br>P10–P90 : %{customdata[0]:.1f}–%{customdata[1]:.1f} °C"
                               "<extra></extra>")),
                row=r, col=cpos)
            # Repère : prévision médiane la plus récente (plus petit lead)
            derniere = d.iloc[-1]["median"]
            fig_conv.add_hline(y=derniere, line=dict(color=c, width=1, dash="dot"),
                               opacity=0.4, row=r, col=cpos)

        # Axes communs : Y identique partout, X « jours avant » inversé et partagé
        fig_conv.update_yaxes(range=[ymin - marge, ymax + marge])
        fig_conv.update_xaxes(autorange=False, range=[max_lead + 0.3, -0.3], ticksuffix=" j")
        fig_conv.update_yaxes(title_text="Temp. prévue (°C)", col=1)
        for cpos in range(1, ncols + 1):
            fig_conv.update_xaxes(title_text="Jours avant l'échéance", row=nrows, col=cpos)
        fig_conv.update_layout(
            height=240 * nrows, template="plotly_white",
            margin=dict(t=40, l=10, r=10, b=10))
        st.plotly_chart(fig_conv, use_container_width=True)

    st.markdown("---")

    # ── 3. Heatmap des révisions run-à-run ──
    st.subheader("🗺️ Carte des révisions run-à-run")
    st.caption(
        "Rouge = le run a **revu à la hausse** la prévision vs le run précédent. "
        "Bleu = révision à la baisse. Blanc = pas de changement = prévision stable et fiable."
    )
    pivot = long.pivot_table(index="target", columns="run_dt", values="median")
    pivot = pivot.sort_index()
    delta_pivot = pivot.diff(axis=1)  # révisions run-à-run

    if not delta_pivot.empty:
        abs_max = delta_pivot.abs().max().max()
        abs_max = max(abs_max, 0.5)  # évite une échelle à 0
        heat = go.Figure(data=go.Heatmap(
            z=delta_pivot.values,
            x=[_run_tick(c) for c in delta_pivot.columns],
            y=[pd.Timestamp(i).strftime("%d %b") for i in delta_pivot.index],
            colorscale="RdBu_r",
            zmid=0,
            zmin=-abs_max,
            zmax=abs_max,
            colorbar=dict(title="Révision (°C)"),
            hovertemplate="Run %{x}<br>Cible %{y}<br>Révision : %{z:+.1f} °C<extra></extra>",
        ))
        heat.update_layout(
            height=max(300, 26 * len(delta_pivot.index) + 120),
            template="plotly_white",
            xaxis_title="Run",
            yaxis_title="Date prévue",
            margin=dict(t=10, l=10, r=10, b=10),
        )
        st.plotly_chart(heat, use_container_width=True)


def page_run(sig):
    st.title("🚀 Lancer un nouveau run")
    st.write(
        "Exécute `Forecast.py` : télécharge les tableaux Meteociel (ECMWF/AIFS/GEFS), "
        "construit le super-ensemble et enregistre un fichier dans `Forecasts/`."
    )
    run_choice = st.radio("Run à récupérer", ["0Z", "12Z"], horizontal=True)
    st.info("Le téléchargement nécessite une connexion internet et peut prendre ~30 s.")

    if st.button(f"▶️ Lancer le run {run_choice}", type="primary"):
        log = st.empty()
        with st.spinner(f"Exécution de Forecast.py {run_choice}…"):
            try:
                # Force l'enfant à écrire en UTF-8 (sinon ses emojis plantent en cp1252)
                child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
                proc = subprocess.run(
                    [sys.executable, FORECAST_SCRIPT, run_choice],
                    cwd=BASE_DIR, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=300, env=child_env,
                )
                output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
                log.code(output or "(aucune sortie)")
                if proc.returncode == 0:
                    st.success("✅ Run terminé. Les nouvelles données sont disponibles.")
                    st.cache_data.clear()
                else:
                    st.error(f"❌ Le script s'est terminé avec le code {proc.returncode}.")
            except subprocess.TimeoutExpired:
                st.error("⏱️ Délai dépassé (5 min). Le site est peut-être inaccessible.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Erreur lors du lancement : {e}")


def ligne_de_flottaison(syn, seuil_chaleur, seuil_canicule, titre,
                        normale=NORMALE_CLIM_850):
    """Graphique épuré : médiane + zone d'incertitude P10–P90 + normale + deux seuils."""
    x = syn["valid_time"]
    p10 = syn.get("P10")
    p90 = syn.get("P90")
    med = syn.get("Médiane")

    fig = go.Figure()
    # Zone d'incertitude unique P10–P90 (très douce), sans aucun détail de membre.
    fig.add_trace(go.Scatter(
        x=x, y=p90, mode="lines", line=dict(width=0),
        hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=x, y=p10, mode="lines", line=dict(width=0), fill="tonexty",
        fillcolor=_rgba("#E74C3C", 0.10), name="Marge d'incertitude",
        hoverinfo="skip"))
    # Tendance centrale
    fig.add_trace(go.Scatter(
        x=x, y=med, mode="lines", name="Tendance (médiane)",
        line=dict(color="#2C3E50", width=3),
        hovertemplate="%{x|%a %d %b · %Hh}<br>Médiane : %{y:.1f} °C<extra></extra>"))
    # Normale climatique de saison (bleu) : référence « temps normal »
    fig.add_hline(
        y=normale, line=dict(color="#2980B9", width=2, dash="dot"),
        annotation_text=f"Normale climatique — {normale:.0f} °C",
        annotation_position="bottom left",
        annotation_font=dict(color="#2471A3", size=12))
    # Lignes d'alerte : chaleur (orange) et canicule exceptionnelle (rouge)
    fig.add_hline(
        y=seuil_chaleur, line=dict(color="#F39C12", width=2, dash="dash"),
        annotation_text=f"Chaleur notable — {seuil_chaleur:.0f} °C",
        annotation_position="top left",
        annotation_font=dict(color="#E67E22", size=12))
    fig.add_hline(
        y=seuil_canicule, line=dict(color="#E74C3C", width=2, dash="dash"),
        annotation_text=f"Canicule exceptionnelle — {seuil_canicule:.0f} °C",
        annotation_position="top left",
        annotation_font=dict(color="#C0392B", size=12))
    fig.update_layout(
        title=titre, height=440, hovermode="x unified", template="plotly_white",
        xaxis_title=None, yaxis_title="Température à 850 hPa (°C)",
        legend=dict(orientation="h", y=1.08), margin=dict(t=70, l=10, r=10, b=10))
    return fig


def calendrier_risques(jours, seuil):
    """Frise chronologique en DÉGRADÉ continu : chaque jour est coloré selon la
    probabilité de dépasser le seuil de canicule (vert→jaune→orange→rouge)."""
    texts = [
        f"{d:%a %d %b}<br>{_canicule_label(p)}"
        f"<br>Médiane : {m:.1f} °C · P90 : {p90:.1f} °C"
        f"<br>P(≥ {seuil:.0f} °C) : {p * 100:.0f} %"
        for d, p, m, p90 in zip(jours["date"], jours["prob"],
                                jours["Médiane"], jours["P90"])
    ]
    fig = go.Figure(go.Heatmap(
        x=jours["date"], y=["Risque canicule"], z=[jours["prob"].tolist()],
        colorscale=CANICULE_SCALE, zmin=0.0, zmax=1.0,
        xgap=3, ygap=0,
        text=[texts], hovertemplate="%{text}<extra></extra>",
        colorbar=dict(title="P(canicule)", tickformat=".0%", thickness=12, len=0.9),
    ))
    fig.update_layout(
        height=150, template="plotly_white",
        xaxis=dict(title=None, tickformat="%a %d/%m", type="date"),
        yaxis=dict(visible=False),
        margin=dict(t=10, l=10, r=10, b=10),
    )
    return fig


def page_grand_public(runs, sig):
    st.title("🌞 Indicateur de canicule")
    if runs.empty:
        st.warning("Aucun fichier de prévision trouvé dans le dossier `Forecasts/`.")
        return

    latest = runs.iloc[0]
    st.caption(
        f"Dernière prévision : **{latest['label']}** · "
        "synthèse simplifiée des trois modèles d'ensemble (ECMWF + AIFS + GEFS)")

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
        st.markdown(
            "**Trois modèles d'ensemble sont combinés :**\n"
            "- **ECMWF** — modèle *physique* du Centre européen (Reading, Royaume-Uni), "
            "référence mondiale de la prévision à moyenne échéance ; son ensemble compte "
            "une cinquantaine de scénarios.\n"
            "- **AIFS** — le modèle d'**intelligence artificielle** du même Centre européen, "
            "récent, très rapide et désormais très performant.\n"
            "- **GEFS** — l'ensemble américain de la **NOAA** (États-Unis), une trentaine de "
            "scénarios.\n\n"
            "**Pourquoi un « ensemble » ?** Chaque modèle est relancé avec de légères "
            "variations des conditions initiales, produisant des dizaines de **scénarios "
            "(« membres »)**. Leur dispersion = la mesure de l'incertitude.\n\n"
            "**Runs 0Z / 12Z.** Un *run* est un calcul lancé à heure fixe en **temps "
            "universel (UTC)** : le **0Z** part de minuit UTC, le **12Z** de midi UTC "
            "(≈ 14 h à Paris l'été). Chaque jour fournit ainsi de nouveaux runs qui affinent "
            "la prévision au fil des sorties.\n\n"
            "**Mise en ligne (indicatif, heure de Paris).** Les sorties ne sont pas "
            "disponibles tout de suite : il faut le temps du calcul et de la diffusion. "
            "Le GEFS arrive le premier ; l'ECMWF, plus tardif, ferme la marche. En pratique "
            "le **0Z** complet est exploitable en **fin de matinée**, le **12Z** en "
            "**soirée**.\n\n"
            "**Super-ensemble.** Plutôt qu'un seul modèle, cette appli **met en commun tous "
            "les scénarios des trois ensembles** : c'est le *super-ensemble*. Une prévision "
            "partagée par de nombreux scénarios issus de modèles différents est plus solide.\n\n"
            "**Ce qu'affichent les graphiques :**\n"
            "- *Indicateur de canicule* et *Vue d'ensemble* → le **super-ensemble** "
            "(les 3 modèles réunis).\n"
            "- *Explorer un run* → au choix : le super-ensemble (onglet **Panache**), "
            "**un seul modèle** détaillé scénario par scénario (onglet **Spaghetti**), ou la "
            "**comparaison des médianes** de chaque modèle (onglet **Modèles**).")

    # Réglages avancés : normale climatique + deux seuils d'intensité (repliés par défaut)
    with st.expander("⚙️ Réglages avancés"):
        st.caption("Heuristique plaine France l'été : température au sol ≈ T850 + 15 °C.")
        col_n, col_a, col_b = st.columns(3)
        seuil_normale = col_n.number_input(
            "Normale climatique (°C à 850 hPa)",
            min_value=0.0, max_value=20.0, value=float(NORMALE_CLIM_850), step=0.5,
            help="Repère « temps de saison ». Sert de référence d'anomalie (ligne bleue).")
        seuil_chaleur = col_a.number_input(
            "Seuil chaleur — vert → orange (°C à 850 hPa)",
            min_value=10.0, max_value=25.0, value=float(SEUIL_CHALEUR_850), step=0.5,
            help="Début de chaleur notable. ~15 °C @850 ≈ 30 °C au sol.")
        seuil_canicule = col_b.number_input(
            "Seuil canicule — orange → rouge (°C à 850 hPa)",
            min_value=10.0, max_value=30.0, value=float(SEUIL_CANICULE_850), step=0.5,
            help="Canicule vraiment exceptionnelle. ~20 °C @850 ≈ 35 °C au sol.")
        if seuil_canicule <= seuil_chaleur:
            st.warning("Le seuil canicule doit être supérieur au seuil chaleur — "
                       "valeurs corrigées automatiquement.")
            seuil_canicule = seuil_chaleur + 0.5

    # Super-ensemble recalculé depuis les membres bruts (grille 6h, GEFS-seul tronqué).
    syn = super_ensemble(latest["path"], sig)
    if syn is None or syn.empty:
        st.error("Aucune feuille modèle exploitable pour ce run.")
        return
    st.caption(
        f"Synthèse recalculée toutes les 6 h, en combinant jusqu'à "
        f"{int(syn['n_membres'].max())} scénarios (« membres ») ECMWF + AIFS + GEFS "
        "par échéance — plus il y a de scénarios, plus la prévision est robuste.")

    # --- Risque canicule par jour : PROBABILITÉ de dépasser le seuil exceptionnel ---
    # On met en commun les membres de la journée (cycle diurne négligeable à 850 hPa)
    # et on mesure la part de scénarios atteignant la canicule → dégradé de couleur.
    jours = daily_canicule_risk(latest["path"], sig, seuil_canicule)
    if jours is None or jours.empty:
        st.error("Impossible de calculer le risque pour ce run.")
        return

    # --- Bandeau de synthèse : début / durée du risque ÉLEVÉ (quasi-sûr) et pic ---
    def _premier_episode(mask_series):
        """(début, durée) du 1er épisode consécutif où mask est vrai, sinon (None, 0)."""
        dts = jours.loc[mask_series, "date"].sort_values().tolist()
        if not dts:
            return None, 0
        duree = 1
        for a, b in zip(dts, dts[1:]):
            if (b - a).days == 1:
                duree += 1
            else:
                break
        return dts[0], duree

    eleve = jours["prob"] >= 0.50  # canicule quasi-sûre (médiane au-dessus du seuil)
    _, duree = _premier_episode(eleve)
    high_dates = jours.loc[eleve, "date"].sort_values().tolist()
    high_set = set(high_dates)
    today = pd.Timestamp(datetime.now().date())
    pic = jours.loc[jours["prob"].idxmax()]

    c1, c2, c3 = st.columns(3)
    if not high_dates:
        c1.metric("Statut canicule", "Aucune en vue",
                  help="Aucun jour à risque élevé (P ≥ 50 %) sur l'horizon")
        c2.metric("Durée prévue", "—")
    else:
        if today in high_set:
            # Fin du bloc contigu de risque élevé contenant aujourd'hui
            fin = today
            while fin + pd.Timedelta(days=1) in high_set:
                fin += pd.Timedelta(days=1)
            c1.metric("Statut canicule", "🔴 En cours",
                      help=f"Niveau élevé au moins jusqu'au {fin.strftime('%a %d %b')}")
        else:
            prochaine = next((d for d in high_dates if d > today), high_dates[0])
            c1.metric("Prochaine canicule", prochaine.strftime("%a %d %b"),
                      help="Prochaine journée à risque élevé (P ≥ 50 %)")
        c2.metric("Durée restante de l'épisode en cours", f"{duree} jour{'s' if duree > 1 else ''}")
    c3.metric("Pic de risque", f"{pic['prob'] * 100:.0f} %",
              help=f"{pic['date'].strftime('%a %d %b')} · médiane {pic['Médiane']:.1f} °C")

    # --- Graphique 1 : la ligne de flottaison ---
    st.subheader("📈 Évolution de la chaleur prévue")
    st.caption(
        "Courbe foncée = **scénario central** (médiane des modèles). "
        "Bande rouge claire = **fourchette des scénarios** (P10–P90, soit 8 cas sur 10) : "
        "plus elle est large, plus la prévision est incertaine. "
        "Pointillés : la **normale climatique** (bleu, ~temps de saison) et les deux "
        "**seuils d'alerte** (orange/rouge), en température à 850 hPa.")
    st.plotly_chart(
        ligne_de_flottaison(syn, seuil_chaleur, seuil_canicule,
                            "Température à 850 hPa — tendance et incertitude",
                            normale=seuil_normale),
        use_container_width=True)

    # --- Graphique 2 : le calendrier des risques (dégradé) ---
    st.subheader("🗓️ Calendrier du risque de canicule")
    st.caption(
        f"Chaque case = un jour. La couleur indique la **probabilité de dépasser "
        f"{seuil_canicule:.0f} °C à 850 hPa** (canicule exceptionnelle) : "
        "🟢 pas de signal → 🟡🟠 risque croissant → 🔴 canicule quasi-certaine. "
        "Les blocs rouges consécutifs donnent la **durée** de l'épisode, "
        "le retour au vert sa **fin**.")
    st.plotly_chart(calendrier_risques(jours, seuil_canicule), use_container_width=True)


# --------------------------------------------------------------------------- #
#  Routage
# --------------------------------------------------------------------------- #
def main():
    sig = runs_signature()
    runs = list_runs(sig)

    st.sidebar.title("🌦️ Navigation")
    pages = ["Indicateur de canicule", "Vue d'ensemble", "Explorer un run",
             "Convergence des runs"]
    if IS_LOCAL:
        pages.append("Lancer un run")
    page = st.sidebar.radio("Aller à", pages)
    st.sidebar.markdown("---")
    st.sidebar.metric("Prévisions archivées", len(runs),
                      help="Nombre de runs (calculs) disponibles dans Forecasts/")
    if not runs.empty:
        st.sidebar.caption(f"Dernière : {runs.iloc[0]['label']}")
    if st.sidebar.button("🔄 Rafraîchir les données"):
        st.cache_data.clear()
        st.rerun()
    st.sidebar.markdown(
        "<small>Données : Meteociel · Paris · échéances 12Z</small>",
        unsafe_allow_html=True)
    st.sidebar.markdown(
        f"<small>Version {APP_VERSION}</small>",
        unsafe_allow_html=True)

    if page == "Vue d'ensemble":
        page_overview(runs, sig)
    elif page == "Indicateur de canicule":
        page_grand_public(runs, sig)
    elif page == "Explorer un run":
        page_explore(runs, sig)
    elif page == "Convergence des runs":
        page_convergence(runs, sig)
    elif page == "Lancer un run" and IS_LOCAL:
        page_run(sig)


if __name__ == "__main__":
    main()
