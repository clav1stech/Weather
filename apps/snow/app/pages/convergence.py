# -*- coding: utf-8 -*-
"""Page « Convergence des runs » — révisions run-à-run sur le flux _MEAN.

Support = la MOYENNE d'ensemble (runs *_MEAN, rétention API longue), pas les
membres (retenus ~3 j seulement par Open-Meteo) : une seule série par run,
directement comparable de run en run — c'est la base du futur bilan de
fiabilité par modèle en fin de saison (les écarts inter-runs affichés ici
sont exactement la matière de ce bilan).

Comparaison PAR MODÈLE (chaque famille à ses propres cycles), échéances
communes uniquement."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from apps.snow import snow_config as SC
from ..data.db import run_label_text
from ..data.runsets import mean_runs, mean_runs_all
from ..ui.theme import _ink, _plotly_template, _rgba

N_RUNS = 8   # valeur initiale du nombre de runs mean superposés / comparés
ALL_MODELS = "Tous modèles"
ALL_MODELS_COLOR = "#34495E"

# Variables suivies en convergence : (col, site, libellé, unité).
_VARS = [
    ("t850", "village", "T850 (masse d'air)", "°C"),
    ("epaisseur", "village", "Épaisseur 1000-500", "m"),
    ("iso0", "village", "Iso 0 °C", "m"),
    ("pmsl", "village", "Pression mer", "hPa"),
]


def _runs_pivot(sub, site, col):
    """index=valid_time, une colonne par run_date (moyenne d'ensemble)."""
    s = sub[(sub["site"] == site) & sub[col].notna()]
    if s.empty:
        return None
    piv = s.pivot_table(index="valid_time", columns="run_date", values=col)
    return piv.sort_index() if piv.notna().any(axis=None) else None


def _all_models_pivots(sub, site, col):
    """Consensus multi-modèles à composition stable.

    Renvoie ``(consensus, minimum, maximum, modèles_communs)``. Chaque modèle
    `_MEAN` a le même poids, quelle que soit la taille de son ensemble natif :
    la convergence mesure ainsi le déplacement du scénario inter-modèles, pas
    une variation artificielle du nombre de membres. Min/Max sont les bornes
    des moyennes modèles, jamais les extrêmes des membres individuels.
    """
    s = sub[(sub["site"] == site) & sub[col].notna()]
    if s.empty:
        return None, None, None, []
    present = s.groupby("run_date")["model"].agg(set)
    common = sorted(set.intersection(*present.tolist())) if len(present) else []
    if len(common) < 2:
        return None, None, None, common
    stable = s[s["model"].isin(common)]
    kwargs = dict(index="valid_time", columns="run_date", values=col)
    consensus = stable.pivot_table(**kwargs, aggfunc="mean").sort_index()
    lower = stable.pivot_table(**kwargs, aggfunc="min").sort_index()
    upper = stable.pivot_table(**kwargs, aggfunc="max").sort_index()
    if not consensus.notna().any(axis=None):
        return None, None, None, common
    return consensus, lower, upper, common


def _convergence_chart(piv, base_color, unit, spread_piv=None,
                       envelope=None):
    """Un trait par run, avec mean ± spread sur le run le plus récent.

    Le flux ``spread`` porte l'écart-type des membres. Sa bande rend visible
    l'amplitude intra-ensemble sans revenir au parquet membres à rétention
    courte, donc sans casser la comparabilité historique de la page.
    """
    fig = go.Figure()
    run_dates = sorted(piv.columns)
    for i, rd in enumerate(run_dates):
        recent = i == len(run_dates) - 1
        alpha = 0.25 + 0.75 * (i + 1) / len(run_dates)
        if recent and envelope is not None:
            lower_piv, upper_piv, envelope_label = envelope
            if rd in lower_piv.columns and rd in upper_piv.columns:
                aligned = pd.concat([lower_piv[rd].rename("lower"),
                                     upper_piv[rd].rename("upper")], axis=1).dropna()
                if not aligned.empty:
                    fig.add_scatter(x=aligned.index, y=aligned["upper"], mode="lines",
                                    line=dict(width=0), showlegend=False,
                                    hoverinfo="skip")
                    fig.add_scatter(x=aligned.index, y=aligned["lower"], mode="lines",
                                    line=dict(width=0), fill="tonexty",
                                    fillcolor=_rgba(base_color, 0.16),
                                    name=envelope_label,
                                    hovertemplate=("%{x|%d %b %Hh}<br>borne basse "
                                                   "%{y:.1f}<extra></extra>"))
        elif recent and spread_piv is not None and rd in spread_piv.columns:
            aligned = pd.concat([piv[rd].rename("mean"),
                                 spread_piv[rd].rename("spread")], axis=1).dropna()
            if not aligned.empty:
                upper = aligned["mean"] + aligned["spread"]
                lower = aligned["mean"] - aligned["spread"]
                fig.add_scatter(x=aligned.index, y=upper, mode="lines",
                                line=dict(width=0), showlegend=False,
                                hoverinfo="skip")
                fig.add_scatter(x=aligned.index, y=lower, mode="lines",
                                line=dict(width=0), fill="tonexty",
                                fillcolor=_rgba(base_color, 0.16),
                                name="Run récent ± 1 écart-type",
                                hovertemplate=("%{x|%d %b %Hh}<br>borne basse "
                                               "%{y:.1f}<extra></extra>"))
        fig.add_scatter(
            x=piv.index, y=piv[rd], name=run_label_text(rd),
            line=dict(color=_rgba(base_color, alpha),
                      width=2.4 if recent else 1.2))
    fig.update_layout(template=_plotly_template(), height=380,
                      margin=dict(l=10, r=10, t=30, b=10),
                      yaxis=dict(title=unit),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig


def _revision_pivot(piv, *, freq="6h", now=None):
    """Révisions run-à-run sur les échéances futures, grille lisible de 6 h.

    Le diff est calculé APRÈS alignement des runs sur la même grille : chaque
    cellule reste bien la révision d'une échéance entre deux runs successifs.
    ``now`` est injectable pour les tests et les bilans rétrospectifs futurs.
    """
    if piv is None or piv.empty:
        return None
    now = pd.Timestamp.now() if now is None else pd.Timestamp(now)
    ordered = piv.reindex(columns=sorted(piv.columns)).sort_index()
    future = ordered[ordered.index >= now.floor(freq)]
    if future.empty:
        return None
    sampled = future.resample(freq).mean()
    delta = sampled.diff(axis=1).dropna(axis=0, how="all").dropna(axis=1, how="all")
    return delta if not delta.empty else None


def _revision_heatmap(delta, unit):
    """Heatmap générique échéance × run des révisions de la variable choisie."""
    abs_max = max(float(delta.abs().max().max()), 0.1)
    fig = go.Figure(go.Heatmap(
        z=delta.values,
        x=[run_label_text(rd) for rd in delta.columns],
        y=[pd.Timestamp(vt).strftime("%d %b %Hh") for vt in delta.index],
        colorscale="RdBu_r", zmid=0, zmin=-abs_max, zmax=abs_max,
        colorbar=dict(title=f"Révision<br>({unit})"),
        hovertemplate=("Run %{x}<br>Échéance %{y}<br>Révision : "
                       f"%{{z:+.2f}} {unit}<extra></extra>"),
    ))
    fig.update_layout(
        template=_plotly_template(),
        height=min(850, max(360, 16 * len(delta.index) + 140)),
        margin=dict(l=10, r=10, t=20, b=10),
        xaxis_title="Run", yaxis_title="Échéance (pas de 6 h)",
        yaxis=dict(autorange="reversed"),
    )
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

    idx = st.selectbox("Variable pivot", range(len(_VARS)),
                       format_func=lambda i: _VARS[i][2])
    col, site, label, unit = _VARS[idx]
    base = st.radio("Modèle", [ALL_MODELS] + SC.ENS_LABELS, horizontal=True)
    n_runs = st.slider("Nombre de runs superposés", min_value=2, max_value=16,
                       value=N_RUNS, step=1)

    common_models = []
    lower_piv = upper_piv = spread_piv = None
    if base == ALL_MODELS:
        sub = mean_runs_all(sig, n_runs)
        piv, lower_piv, upper_piv, common_models = \
            _all_models_pivots(sub, site, col) if not sub.empty \
            else (None, None, None, [])
    else:
        sub = mean_runs(sig, base, n_runs)
        piv = _runs_pivot(sub, site, col) if not sub.empty else None
    if piv is None:
        st.info("Pas encore de runs mean exploitables pour cette variable / ce "
                "modèle (cas normal : flux mean sans iso 0°, GEFS_MEAN sans "
                "niveaux de pression, ou historique trop court).")
        return

    if base != ALL_MODELS:
        spread_sub = mean_runs(sig, base, n_runs, kind="spread")
        spread_piv = _runs_pivot(spread_sub, site, col) if not spread_sub.empty else None
    st.subheader(f"Superposition des {len(piv.columns)} derniers runs")
    if base == ALL_MODELS:
        bases = [m.removesuffix("_MEAN") for m in common_models]
        st.caption("Consensus à poids égal, sur une composition stable : "
                   f"**{', '.join(bases)}**. Le trait est la moyenne des modèles ; "
                   "la bande du run récent va de la moyenne modèle minimale à "
                   "la maximale.")
    else:
        st.caption("Le trait le plus soutenu est le run le plus récent. Sa bande "
                   "montre ± 1 écart-type entre membres lorsque le flux spread "
                   "publie cette variable.")
    envelope = ((lower_piv, upper_piv, "Amplitude des moyennes modèles")
                if base == ALL_MODELS else None)
    st.plotly_chart(_convergence_chart(
        piv, ALL_MODELS_COLOR if base == ALL_MODELS
        else SC.COLOR_BY_LABEL.get(base, _ink()), unit, spread_piv, envelope),
        use_container_width=True)

    table = _revisions_table(piv)
    if table is not None:
        st.subheader("Révisions run-à-run (échéances à venir communes)")
        st.dataframe(table, use_container_width=True)

    st.markdown("---")
    st.subheader("🗺️ Heatmap des révisions successives")
    st.caption("Chaque cellule compare un run au précédent pour la même "
               "échéance : rouge = révision à la hausse, bleu = baisse, blanc "
               "= stabilité. La grille est agrégée par pas de 6 h pour garder "
               "les 15 jours lisibles.")
    delta = _revision_pivot(piv)
    if delta is None:
        st.info("Pas assez d'échéances communes entre deux runs pour construire "
                "la heatmap.")
    else:
        st.plotly_chart(_revision_heatmap(delta, unit), use_container_width=True)
