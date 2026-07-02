# -*- coding: utf-8 -*-
"""Graphiques Plotly génériques (multi-domaines) : panache, spaghetti,
comparaison de modèles, divergence, incertitude. Chaque fonction reçoit des
stats déjà calculées (cf. app/stats/ensemble.py) et retourne une figure."""

import plotly.graph_objects as go

import config as C
from app.stats.ensemble import model_data
from app.ui.theme import _ink, _plotly_template, _rgba


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
