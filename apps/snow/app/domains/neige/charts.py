# -*- coding: utf-8 -*-
"""Graphiques du domaine neige (Plotly, thème via app/ui/theme)."""

import plotly.graph_objects as go

from apps.snow import snow_config as SC
from ...ui.theme import _ink, _plotly_template, _rgba
from core.stats.ensemble import model_medians, super_ensemble


def daily_snow_chart(daily, site_code):
    """Cumuls journaliers attendus (barres, cm) + probabilité d'un jour à
    ≥ 1 cm (ligne, axe droit). `daily` = logic.daily_snowfall (à venir)."""
    color = SC.SITE_COLORS[site_code]
    fig = go.Figure()
    fig.add_bar(x=daily["date"], y=daily["attendu"], name="Cumul attendu (cm)",
                marker_color=_rgba(color, 0.75),
                hovertemplate="%{x|%a %d %b}<br>attendu %{y:.1f} cm<extra></extra>")
    fig.add_scatter(x=daily["date"], y=daily["P90"], name="P90 (cm)",
                    mode="markers", marker=dict(color=color, symbol="line-ew-open", size=14))
    fig.add_scatter(x=daily["date"], y=daily["prob"] * 100, name="Proba ≥ 1 cm (%)",
                    yaxis="y2", mode="lines+markers",
                    line=dict(color=_ink(), width=1.5, dash="dot"))
    fig.update_layout(
        template=_plotly_template(), height=320,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(title="cm / jour", rangemode="tozero"),
        yaxis2=dict(title="%", overlaying="y", side="right", range=[0, 100],
                    showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def lpn_chart(lpn):
    """Limite pluie-neige (médiane + bande P10-P90) vs altitude des deux
    points. `lpn` = logic.lpn_series (échéances à venir)."""
    fig = go.Figure()
    band_color = _rgba("#5DADE2", 0.18)
    fig.add_scatter(x=lpn["valid_time"], y=lpn["lpn_p90"], line=dict(width=0),
                    showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=lpn["valid_time"], y=lpn["lpn_p10"], fill="tonexty",
                    fillcolor=band_color, line=dict(width=0),
                    name="LPN P10–P90")
    fig.add_scatter(x=lpn["valid_time"], y=lpn["lpn"], name="Limite pluie-neige (médiane)",
                    line=dict(color="#2E86C1", width=2))
    for site in SC.SITES:
        fig.add_hline(y=site["alt"], line_dash="dash",
                      line_color=SC.SITE_COLORS[site["code"]],
                      annotation_text=f"{site['nom']} ({site['alt']} m)",
                      annotation_position="top left")
    fig.update_layout(template=_plotly_template(), height=340,
                      margin=dict(l=10, r=10, t=30, b=10),
                      yaxis=dict(title="altitude (m)"),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig


def medians_chart(sub, var, title, unit, seuils_h=None):
    """Médianes par modèle d'une variable (lignes, couleurs de config) —
    utilisée pour t850 / pmsl / épaisseur. `seuils_h` : lignes horizontales
    de repère {label: y}. None si variable absente (l'appelant n'affiche rien)."""
    meds = model_medians(sub, var, SC.ENS_LABELS)
    if meds is None or not meds.notna().any(axis=None):
        return None
    fig = go.Figure()
    for model in meds.columns:
        fig.add_scatter(x=meds.index, y=meds[model], name=model,
                        line=dict(color=SC.COLOR_BY_LABEL.get(model), width=1.8))
    for label, y in (seuils_h or {}).items():
        fig.add_hline(y=y, line_dash="dot", line_color=_ink(),
                      annotation_text=label, annotation_position="bottom right")
    fig.update_layout(template=_plotly_template(), height=300, title=title,
                      margin=dict(l=10, r=10, t=40, b=10),
                      yaxis=dict(title=unit),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig


def fan_chart(sub, var, title, unit, seuil=0.0):
    """Panache du super-ensemble (médiane, P25-P75, P10-P90) pour une variable
    au pool donné — tolérant NaN (l'horizon lointain reste tracé)."""
    se = super_ensemble(sub, var, seuil)
    if se is None or se.empty:
        return None
    fig = go.Figure()
    for lo, hi, alpha in (("P10", "P90", 0.14), ("P25", "P75", 0.22)):
        fig.add_scatter(x=se["valid_time"], y=se[hi], line=dict(width=0),
                        showlegend=False, hoverinfo="skip")
        fig.add_scatter(x=se["valid_time"], y=se[lo], fill="tonexty",
                        fillcolor=_rgba("#5DADE2", alpha), line=dict(width=0),
                        name=f"{lo}–{hi}")
    fig.add_scatter(x=se["valid_time"], y=se["Médiane"], name="Médiane",
                    line=dict(color="#2E86C1", width=2.2))
    fig.update_layout(template=_plotly_template(), height=340, title=title,
                      margin=dict(l=10, r=10, t=40, b=10),
                      yaxis=dict(title=unit),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig
