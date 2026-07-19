# -*- coding: utf-8 -*-
"""Page opérationnelle de contrôle des runs neige (lecture seule)."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from apps.snow import snow_config as SC
from apps.snow.app.data.db import load_db
from apps.snow.app.data.quality import quality_report
from apps.snow.app.ui.theme import _plotly_template


def _quality_heatmap(history):
    """Matrice run × modèle : couleur = part de l'horizon contigu atteinte."""
    recent = history.head(6 * 4 * SC.RUN_QUALITY_LOOKBACK_DAYS).copy()
    recent["cycle"] = recent["run_utc"].dt.strftime("%d %b %HZ")
    recent["ratio"] = (recent["reach_h"] / recent["horizon_h"]).clip(0, 1)
    recent["txt"] = recent.apply(
        lambda r: f"{r['reach_h'] / 24:.1f} j<br>{'✓' if r['complete'] else 'partiel'}",
        axis=1)
    order = recent[["cycle", "run_utc"]].drop_duplicates() \
        .sort_values("run_utc", ascending=False)["cycle"].tolist()
    models = SC.ENS_LABELS + SC.MEAN_LABELS
    z = recent.pivot_table(index="cycle", columns="model", values="ratio",
                           aggfunc="max").reindex(index=order, columns=models)
    txt = recent.pivot_table(index="cycle", columns="model", values="txt",
                             aggfunc="first").reindex(index=order, columns=models)
    fig = go.Figure(go.Heatmap(
        z=z.values, x=models, y=order, text=txt.fillna("").values,
        texttemplate="%{text}", colorscale="RdYlGn", zmin=0, zmax=1,
        xgap=3, ygap=3, hoverongaps=False,
        colorbar=dict(title="Part horizon", tickformat=".0%"),
        hovertemplate="Run %{y}<br>Modèle %{x}<br>%{text}<extra></extra>",
    ))
    fig.update_layout(template=_plotly_template(), xaxis=dict(side="top"),
                      yaxis=dict(autorange="reversed"),
                      height=max(360, min(900, 28 * len(order) + 130)),
                      margin=dict(t=80, l=10, r=10, b=10))
    return fig


def page_diagnostic(runs, sig):
    st.title("🩺 Contrôle des runs neige")
    st.caption("État opérationnel des flux membres et mean/spread : fraîcheur "
               "empirique, portée contiguë, complétude et cycles attendus. "
               "Les calculs emploient les mêmes fonctions et seuils que le "
               "pipeline de persistance.")

    summary, history, anomalies = quality_report(load_db(sig))
    if summary.empty:
        st.warning("Aucun run neige exploitable dans le parquet ensemble.")
        return

    late = int((summary["Publication"] == "en retard").sum())
    partial = int((summary["Complétude"] == "partiel").sum())
    missing = int((anomalies["Type"] == "cycle manquant").sum()) if not anomalies.empty else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Flux à jour", f"{len(summary) - late}/{len(summary)}")
    c2.metric("Derniers runs partiels", partial)
    c3.metric(f"Cycles manquants ({SC.RUN_QUALITY_LOOKBACK_DAYS} j)", missing)
    if late or partial or missing:
        st.warning("⚠️ Une ou plusieurs anomalies demandent vérification dans "
                   "les tableaux ci-dessous.")
    else:
        st.success("✅ Tous les flux attendus sont à jour et leurs derniers runs "
                   "ont une portée contiguë complète.")

    st.subheader("Synthèse par modèle et par flux")
    display = summary.copy()
    for col in ("Dernier run UTC", "Dernier cycle attendu UTC"):
        display[col] = pd.to_datetime(display[col]).dt.strftime("%d/%m %HZ")
    styler = display.style.apply(
        lambda row: ["background-color:#fdecea;color:#611a15"
                     if row["Publication"] == "en retard"
                     or row["Complétude"] in ("partiel", "absent") else ""
                     for _ in row], axis=1
    ).format({"Âge (h)": "{:.1f}", "Portée (h)": "{:.0f}"}, na_rep="—")
    st.dataframe(styler, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("Complétude empirique des runs récents")
    st.caption("La portée est la dernière échéance atteignable sans trou de plus "
               f"de {SC.PERSIST_MAX_GAP_H} h. Une valeur isolée en queue ne peut "
               "donc jamais simuler un run complet.")
    if not history.empty:
        st.plotly_chart(_quality_heatmap(history), use_container_width=True)

    st.markdown("---")
    st.subheader("Anomalies de publication")
    st.caption("Une absence n'est signalée que sur `expected_cycles`. Les cycles "
               "6Z/18Z ECMWF, possibles mais nativement courts, ne sont pas "
               "considérés manquants. Aucun contrôle croisé legacy n'existe pour "
               "la neige : la comparaison porte ici sur la publication réelle.")
    if anomalies.empty:
        st.success("✅ Aucun cycle attendu manquant ni cycle hors configuration.")
    else:
        show = anomalies.copy()
        show["Cycle UTC"] = pd.to_datetime(show["Cycle UTC"]).dt.strftime("%d/%m/%Y %HZ")
        st.dataframe(show, use_container_width=True, hide_index=True, height=420)
