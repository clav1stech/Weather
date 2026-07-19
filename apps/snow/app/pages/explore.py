# -*- coding: utf-8 -*-
"""Page « Explorer un run » — inspection d'un pool de runs membres.

Deux options de pool « dernier » volontairement DISTINCTES (mêmes politiques
que le canicule, ne pas les confondre) :
  • « Dernier run (le plus frais) » : dernier run non vide de chaque modèle,
    quel que soit son horizon (fraîcheur maximale, même partielle) ;
  • « Dernier run à horizon plein » : celui des vues combinées.
S'y ajoutent les cycles individuels (tous modèles présents à ce cycle).

Les tableaux de l'onglet 🧾 sont VOLONTAIREMENT LARGES (export pour analyse
par IA) : stats du super-ensemble + par modèle médiane, contrôle (member 0),
nb de membres, Δ médiane vs run précédent DE CE MODÈLE — jamais un cycle
global partagé."""

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from ..data.db import members_db, run_label_text
from ..data.runsets import latest_complete_run_sub, latest_run_sub, previous_runs_sub
from ..domains.neige.charts import fan_chart, medians_chart
from core.stats.ensemble import model_data, super_ensemble

# Variables inspectables par site (libellé, unité) — mêmes colonnes que la
# config ; une variable absente du pool se dégrade en silence.
_VARS = {
    "village": [("t2m", "Température 2 m", "°C"), ("t850", "T850", "°C"),
                ("pmsl", "Pression mer", "hPa"), ("epaisseur", "Épaisseur 1000-500", "m"),
                ("iso0", "Iso 0° (zone)", "m"), ("z500", "Z500 (contexte)", "m"),
                ("t500", "T500 (contexte)", "°C")],
    "sommet": [("t2m", "Température 2 m", "°C"), ("neige", "Neige fraîche horaire", "cm"),
               ("hneige", "Épaisseur de neige au sol", "m"), ("raf", "Rafales", "km/h")],
}


def _run_pool(sig, runs, choice):
    if choice == "Dernier run (le plus frais)":
        return latest_run_sub(sig)
    if choice == "Dernier run à horizon plein":
        return latest_complete_run_sub(sig)[0]
    run_date = runs.loc[runs["label"] == choice, "run_date"].iloc[0]
    df = members_db(sig)
    return df[df["run_date"] == run_date]


def _export_table(sub_site, prev_site, var):
    """Table large par échéance : super-ensemble + détail par modèle."""
    se = super_ensemble(sub_site, var, seuil=0.0)
    if se is None or se.empty:
        return None
    table = se[["valid_time", "Médiane", "P10", "P90", "Spread",
                "n_membres", "n_models"]].set_index("valid_time")
    for model in SC.ENS_LABELS:
        data = model_data(sub_site, model, var)
        if data is None:
            continue
        stats, members, det = data
        stats = stats.set_index("valid_time")
        table[f"{model} médiane"] = stats["median"].round(2)
        if det is not None:
            table[f"{model} contrôle"] = det.round(2)
        table[f"{model} n"] = members.notna().sum(axis=1)
        prev = model_data(prev_site, model, var) if not prev_site.empty else None
        if prev is not None:
            prev_med = prev[0].set_index("valid_time")["median"]
            table[f"{model} Δ vs run préc."] = (stats["median"] - prev_med).round(2)
    return table.reset_index()


def page_explore(runs, sig):
    st.title("🔍 Explorer un run — Megève")
    if runs.empty:
        st.info("Aucun run en base pour l'instant.")
        return

    options = ["Dernier run (le plus frais)", "Dernier run à horizon plein"] \
        + runs["label"].tolist()
    choice = st.selectbox("Run à explorer", options)
    sub = _run_pool(sig, runs, choice)
    if sub.empty:
        st.info("Pool vide pour cette sélection.")
        return
    cycles = ", ".join(f"{m} {run_label_text(rd)}"
                       for m, rd in sub.groupby("model")["run_date"].max().items())
    st.caption(f"Pool : {cycles}")

    site_code = st.radio("Point", SC.SITE_CODES, horizontal=True,
                         format_func=lambda c: SC.SITE_BY_CODE[c]["nom"])
    sub_site = sub[sub["site"] == site_code]
    prev_site = previous_runs_sub(sig, sub)
    prev_site = prev_site[prev_site["site"] == site_code] if not prev_site.empty \
        else prev_site

    var_options = [(col, label, unit) for col, label, unit in _VARS[site_code]
                   if sub_site[col].notna().any()]
    if not var_options:
        st.info("Aucune variable exploitable à ce point dans ce pool.")
        return
    col, label, unit = var_options[st.selectbox(
        "Variable", range(len(var_options)),
        format_func=lambda i: var_options[i][1])]

    tab_graph, tab_tables = st.tabs(["📈 Graphiques", "🧾 Tables d'export"])
    with tab_graph:
        fig = fan_chart(sub_site, col, f"{label} — super-ensemble", unit)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
        fig = medians_chart(sub_site, col, f"{label} — médianes par modèle", unit)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
    with tab_tables:
        table = _export_table(sub_site, prev_site, col)
        if table is None:
            st.caption("Pas de données pour cette variable.")
        else:
            st.dataframe(table, use_container_width=True, height=480)
