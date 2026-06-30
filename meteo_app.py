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
import sys
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

APP_VERSION = "2.0.0"
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


def latest_refresh_status(runs, sig):
    """Heure du dernier rafraîchissement (mtime du parquet) et complétude (tous
    les modèles principaux présents ou non) du dernier run."""
    if runs.empty:
        return None, True, []
    try:
        refreshed_at = datetime.fromtimestamp(os.path.getmtime(C.DB_PATH))
    except OSError:
        refreshed_at = None
    present = set(run_slice(sig, runs.iloc[0]["run_date"])["model"].unique())
    missing = [m for m in C.MAIN_LABELS if m not in present]
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


def completed_pooled_sub(runs, pos, sig, max_lookback=BACKFILL_MAX_LOOKBACK):
    """Lignes du run `pos` (index dans `runs`, trié du plus récent au plus
    ancien), en complétant, échéance par échéance, les NaN des modèles PRINCIPAUX
    avec les runs antérieurs (pos+1 → pos+max_lookback) — priorité au plus frais.

    Retourne (sub_complet, sources) où `sources` mappe chaque modèle principal à
    la liste des run_date effectivement utilisés (le run courant en premier s'il
    a contribué, puis les runs antérieurs ayant comblé des échéances manquantes ;
    liste vide si le modèle est introuvable dans toute la fenêtre)."""
    n = len(runs)
    frames, sources = [], {}
    for model in C.MAIN_LABELS:
        used, covered_vt = [], set()
        for j in range(pos, min(pos + max_lookback + 1, n)):
            cand_run_date = runs.iloc[j]["run_date"]
            cand = run_slice(sig, cand_run_date)
            cand = cand[cand["model"] == model]
            if cand.empty:
                continue
            # Ne garde que les échéances valides ET pas déjà couvertes par un run
            # plus récent (priorité au plus frais, échéance par échéance).
            valid = cand.dropna(subset=[VAR])
            valid = valid[~valid["valid_time"].isin(covered_vt)]
            if valid.empty:
                continue
            frames.append(valid)
            covered_vt.update(valid["valid_time"].unique())
            used.append(cand_run_date)
        sources[model] = used
    # Modèles d'appoint (non principaux) : jamais backfillés, uniquement si présents au run courant.
    current = run_slice(sig, runs.iloc[pos]["run_date"])
    extra = current[~current["model"].isin(C.MAIN_LABELS)]
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
    """Risque canicule/jour : pool des membres de la journée, proba de dépasser seuil."""
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


def _canicule_label(prob):
    if prob >= 0.50:
        return "🔴 Canicule quasi-certaine"
    if prob >= 0.25:
        return "🟠 Risque marqué"
    if prob >= 0.10:
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


def _kpi_card(label, value, help_txt="", value_point=None, valid_time=None):
    """Carte KPI ; si value_point + valid_time fournis, affiche l'anomalie vs la
    normale climatique saisonnière (cosinus) à cette date."""
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
    return (f"<div{title_attr} style='background:rgba(128,138,157,0.10);"
            "border:1px solid rgba(128,138,157,0.25);"
            "border-radius:12px;padding:12px 16px;height:100%;'>"
            f"<div style='font-size:0.8rem;opacity:0.7;'>{label}</div>"
            f"<div style='font-size:1.85rem;font-weight:600;color:inherit;line-height:1.3;'>"
            f"{value}{anomalie_html}</div></div>")


# --------------------------------------------------------------------------- #
#  Pages
# --------------------------------------------------------------------------- #
def page_overview(runs, sig):
    st.title("🌡️ Dashboard Météo — Prévisions d'ensemble (Paris)")
    if runs.empty:
        st.warning("Base vide. Lancez le pipeline `Forecast.py` pour la remplir.")
        return
    latest = runs.iloc[0]
    sub = run_slice(sig, latest["run_date"])
    refreshed_at, complete, missing = latest_refresh_status(runs, sig)
    refresh_txt = (f" · rafraîchi le {refreshed_at.strftime('%d/%m/%Y à %Hh%M')}"
                   if refreshed_at is not None else "")
    statut_txt = ("complet ✅" if complete
                  else f"partiel ⚠️ (manque {', '.join(missing)})")
    st.caption(f"Dernière prévision : **{latest['label']}** · "
               f"{len(runs)} prévisions (runs) archivées · températures à 850 hPa"
               f"{refresh_txt} · {statut_txt}")

    syn = super_ensemble(sub)
    if syn is None or syn.empty:
        st.error("Aucune donnée exploitable pour ce run.")
        return

    c1, c2, c3, c4 = st.columns(4)
    first = syn.iloc[0]
    c1.markdown(_kpi_card("Prochaine échéance (médiane)", f"{first['Médiane']:.1f} °C",
                          "Scénario central pour la première échéance",
                          value_point=first["Médiane"], valid_time=first["valid_time"]),
                unsafe_allow_html=True)
    c2.markdown(_kpi_card("Dispersion moyenne", f"{syn['Spread'].mean():.1f} °C",
                          "Largeur moyenne P90−P10 sur toutes les échéances"),
                unsafe_allow_html=True)
    peak = syn.loc[syn["Médiane"].idxmax()]
    c3.markdown(_kpi_card("Pic de chaleur (médiane)", f"{peak['Médiane']:.1f} °C",
                          f"Maximum du scénario central · {peak['valid_time']:%d %b %Hh}",
                          value_point=peak["Médiane"], valid_time=peak["valid_time"]),
                unsafe_allow_html=True)
    c4.markdown(_kpi_card("Échéances > seuil (médiane)",
                          f"{int((syn['Médiane'] > C.SEUIL_CANICULE_850).sum())}",
                          f"Échéances où la médiane dépasse {C.SEUIL_CANICULE_850:.0f} °C"),
                unsafe_allow_html=True)

    st.caption("**Panache de dispersion** : ligne rouge = médiane ; bandes = part "
               "croissante des scénarios (Min–Max, P10–P90, P25–P75).")
    st.plotly_chart(fan_chart(syn, f"Panache du super-ensemble — {latest['label']}"),
                    width="stretch")

    present = sorted(sub["model"].unique())
    cutoff = multimodel_cutoff(sub)
    st.caption("Trait plein = médiane par modèle, bande = dispersion (P10–P90), "
               "pointillés = run de contrôle.")
    st.plotly_chart(models_median_chart(sub, present, cutoff), width="stretch")


def page_explore(runs, sig):
    st.title("📊 Explorer une prévision (run)")
    st.caption(
        "Vue détaillée d'un même run sous plusieurs angles : panache de dispersion, "
        "scénarios individuels, comparaison des modèles, divergence, incertitude et "
        "tableaux de données.")
    if runs.empty:
        st.warning("Aucun run disponible.")
        return

    idx = st.selectbox("Choisir un run", runs.index,
                       format_func=lambda i: runs.loc[i, "label"])
    run = runs.loc[idx]
    sub = run_slice(sig, run["run_date"])
    syn = super_ensemble(sub)
    present = sorted(sub["model"].unique())
    cutoff = multimodel_cutoff(sub)

    manquants = [m for m in C.MAIN_LABELS if m not in present]
    if manquants:
        st.warning(f"⚠️ Modèle(s) principal(aux) absent(s) : **{', '.join(manquants)}**. "
                   "Super-ensemble appauvri (dispersion possiblement sous-estimée).")

    tab_fan, tab_spag, tab_cmp, tab_unc, tab_tbl = st.tabs(
        ["📈 Panache", "🍝 Spaghetti", "⚖️ Modèles", "📉 Incertitude", "🧾 Tableaux"])

    with tab_fan:
        if syn is not None and not syn.empty:
            st.plotly_chart(fan_chart(syn, f"Super-ensemble — {run['label']}"), width="stretch")
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
        tables = {
            "Super-ensemble (infra-journalier)": lambda: syn,
            "Super-ensemble (journalier 12h)": lambda: daily_aggregate(syn),
        }
        choice = st.selectbox("Table", list(tables), key="tbl_sheet")
        raw = tables[choice]()
        if raw is not None:
            raw = raw.drop(columns=["date"], errors="ignore")
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
            u = utc_cycle(run["run_date"])
            st.download_button("⬇️ Télécharger (CSV)",
                               raw.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"run_{u:%Y%m%d}_{u.hour:02d}Z_{choice[:20]}.csv",
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


def page_convergence(runs, sig):
    st.title("🔄 Révisions & convergence des prévisions")
    st.caption(
        "Chaque nouveau calcul (run) corrige le précédent. Cette page montre **comment la "
        "prévision d'une même date a évolué d'un run à l'autre** : si elle se stabilise, "
        "on peut s'y fier ; si elle bouge encore beaucoup, l'incertitude reste forte.")

    runs = _convergence_runs(runs)
    if len(runs) < 2:
        st.warning("Il faut au moins 2 runs pour analyser la convergence.")
        return

    # --- Historique commun : médiane/P10/P90 journalières de CHAQUE run (recalcul brut) ---
    # Un modèle principal absent d'un run est backfillé depuis le run antérieur le plus
    # proche qui le contient (jusqu'à n-3) : on compare ainsi des super-ensembles à
    # modèles comparables, pas « 4 modèles vs 1 ». backfill_src : {run_date -> sources}.
    records = []
    backfill_src = {}
    for pos in range(len(runs)):
        r = runs.iloc[pos]
        syn, sources = completed_super_ensemble_daily(runs, pos, sig)
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
    backfilled, manquants = {}, {}
    for run_dt, sources in backfill_src.items():
        if run_dt not in runs_affiches:
            continue
        bf = {m: srcs for m, srcs in sources.items()
              if srcs and (len(srcs) > 1 or srcs[0] != run_dt)}
        miss = [m for m in C.MAIN_LABELS if not sources.get(m)]
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
                tag = "complété" if run_dt in srcs else "repris"
                notes.append(f"{m} {tag} par {extra_txt}")
            if run_dt in manquants:
                notes.append(f"manque {', '.join(manquants[run_dt])}")
            parts.append(f"{label_par_dt[run_dt]} ({' ; '.join(notes)})")
        st.warning(
            "⚠️ Certains runs affichés n'avaient pas tous les modèles sur toute la période. "
            "Les échéances manquantes (modèle absent, ou run partiel — ex. 6Z/18Z arrêté en "
            "cours de période) sont **complétées, échéance par échéance, par le run antérieur "
            "le plus proche** qui les couvre (jusqu'à n-3, soit ~1 jour) afin de comparer des "
            "super-ensembles équivalents. Quand aucun run récent ne couvre une échéance, elle "
            "reste manquante et le super-ensemble y est appauvri. Les modèles d'appoint (ex. "
            "GEM) ne sont jamais ainsi complétés — ils n'apparaissent qu'à leurs propres "
            "cycles réels (0Z/12Z). "
            "Ces runs sont notés en *italique* avec un astérisque (\\*).\n\n"
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
    latest = runs.iloc[0]
    sub = run_slice(sig, latest["run_date"])
    st.caption(f"Dernière prévision : **{latest['label']}** · super-ensemble multi-modèles")

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
            "(tous les modèles réunis).\n"
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

    eleve = jours["prob"] >= 0.50
    high_dates = jours.loc[eleve, "date"].sort_values().tolist()
    high_set = set(high_dates)
    today = pd.Timestamp(datetime.now().date())
    pic = jours.loc[jours["prob"].idxmax()]
    c1, c2, c3 = st.columns(3)
    if not high_dates:
        c1.metric("Statut canicule", "Aucune en vue")
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
    st.caption(f"Heure UTC actuelle : **{now_utc:%H:%M}** · poll de référence le plus proche : "
               f"**{nearest:02d}:15 UTC**")

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
#  Routage
# --------------------------------------------------------------------------- #
def main():
    sig = db_signature()
    runs = list_runs(sig)

    st.sidebar.title("🌦️ Navigation")
    pages = ["Indicateur de canicule", "Vue d'ensemble", "Explorer un run",
             "Convergence des runs"]
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
            st.sidebar.caption(f"✅ Données complètes ({len(C.MAIN_LABELS)} modèles principaux)")
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
    elif page == "Lancer le pipeline" and IS_LOCAL:
        page_run(sig)


if __name__ == "__main__":
    main()
