# -*- coding: utf-8 -*-
"""Page « Convergence des runs » — révisions run-à-run sur le flux _MEAN.

Support = la MOYENNE d'ensemble (runs *_MEAN, rétention API longue), pas les
membres (retenus ~3 j seulement par Open-Meteo) : une seule série par run,
directement comparable de run en run — c'est la base du futur bilan de
fiabilité par modèle en fin de saison (les écarts inter-runs affichés ici
sont exactement la matière de ce bilan).

Comparaison PAR MODÈLE (chaque famille à ses propres cycles), échéances
communes uniquement."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from apps.snow import snow_config as SC
from ..data.db import run_label_text
from ..data.runsets import mean_runs
from ..ui.theme import _ink, _plotly_template, _rgba

N_RUNS = 8   # nombre de runs mean superposés / comparés

# Variables suivies en convergence : (col, site, libellé, unité).
_VARS = [
    ("t850", "village", "T850 (masse d'air)", "°C"),
    ("iso0", "village", "Iso 0°", "m"),
    ("t2m", "sommet", "Température 2 m sommet", "°C"),
    ("neige", "sommet", "Neige fraîche horaire sommet", "cm"),
    ("pmsl", "village", "Pression mer", "hPa"),
    ("epaisseur", "village", "Épaisseur 1000-500", "m"),
]


def _runs_pivot(sub, site, col):
    """index=valid_time, une colonne par run_date (moyenne d'ensemble)."""
    s = sub[(sub["site"] == site) & sub[col].notna()]
    if s.empty:
        return None
    piv = s.pivot_table(index="valid_time", columns="run_date", values=col)
    return piv.sort_index() if piv.notna().any(axis=None) else None


def _convergence_chart(piv, base_color, unit):
    """Un trait par run, du plus ancien (pâle) au plus récent (plein)."""
    fig = go.Figure()
    run_dates = sorted(piv.columns)
    for i, rd in enumerate(run_dates):
        recent = i == len(run_dates) - 1
        alpha = 0.25 + 0.75 * (i + 1) / len(run_dates)
        fig.add_scatter(
            x=piv.index, y=piv[rd], name=run_label_text(rd),
            line=dict(color=_rgba(base_color, alpha),
                      width=2.4 if recent else 1.2))
    fig.update_layout(template=_plotly_template(), height=380,
                      margin=dict(l=10, r=10, t=30, b=10),
                      yaxis=dict(title=unit),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig


def _revisions_table(piv):
    """Écart moyen absolu entre runs consécutifs (échéances communes À VENIR) :
    ligne = transition run n-1 → run n. C'est la matière première du bilan de
    fiabilité de fin de saison (un modèle qui révise peu converge tôt)."""
    now = pd.Timestamp.now()
    fut = piv[piv.index >= now]
    run_dates = sorted(piv.columns)
    rows = []
    for prev, cur in zip(run_dates, run_dates[1:]):
        common = fut[[prev, cur]].dropna()
        if common.empty:
            continue
        diff = (common[cur] - common[prev])
        rows.append({
            "Transition": f"{run_label_text(prev)} → {run_label_text(cur)}",
            "Écart moyen abs.": round(float(diff.abs().mean()), 2),
            "Biais moyen": round(float(diff.mean()), 2),
            "Échéances comparées": len(common),
        })
    return pd.DataFrame(rows) if rows else None


def page_convergence(runs, sig):
    st.title("🔁 Convergence des runs — Megève")
    st.caption("Moyennes d'ensemble successives (flux mean, rétention longue) : "
               "plus les traits récents se superposent, plus le scénario est "
               "acquis. Les écarts inter-runs ci-dessous alimenteront le bilan "
               "de fiabilité par modèle en fin de saison.")

    idx = st.selectbox("Variable", range(len(_VARS)),
                       format_func=lambda i: _VARS[i][2])
    col, site, label, unit = _VARS[idx]
    base = st.radio("Modèle", SC.ENS_LABELS, horizontal=True)

    sub = mean_runs(sig, base, N_RUNS)
    piv = _runs_pivot(sub, site, col) if not sub.empty else None
    if piv is None:
        st.info("Pas encore de runs mean exploitables pour cette variable / ce "
                "modèle (cas normal : flux mean sans iso 0°, GEFS_MEAN sans "
                "niveaux de pression, ou historique trop court).")
        return

    st.plotly_chart(
        _convergence_chart(piv, SC.COLOR_BY_LABEL.get(base, _ink()), unit),
        use_container_width=True)

    table = _revisions_table(piv)
    if table is not None:
        st.subheader("Révisions run-à-run (échéances à venir communes)")
        st.dataframe(table, use_container_width=True)
