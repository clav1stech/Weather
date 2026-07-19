# -*- coding: utf-8 -*-
"""Graphiques du domaine Observations neige (Plotly, thème via app/ui/theme).
Couleurs par station depuis la config ; une station sans la moindre valeur
pour la variable tracée est simplement absente du graphe (NaN structurel)."""

import plotly.graph_objects as go

from apps.snow import snow_config as SC
from ...ui.theme import _plotly_template


def _nom_long(station):
    return f"{station['nom']} ({station['alt']} m)"


def stations_chart(window, var, titre, unit):
    """Comparaison inter-stations d'une variable sur la fenêtre fournie —
    une ligne par station ayant au moins une valeur valide. None si aucune."""
    fig, trace = go.Figure(), False
    for station in SC.OBS_STATIONS:
        g = window[window["station_id"] == station["id"]].dropna(subset=[var])
        if g.empty:
            continue
        trace = True
        fig.add_scatter(x=g["valid_time"], y=g[var], name=_nom_long(station),
                        line=dict(color=station["color"], width=1.8),
                        hovertemplate="%{x|%a %d %b %H:%M}<br>"
                                      f"%{{y:.1f}} {unit}<extra>{station['nom']}</extra>")
    if not trace:
        return None
    if var == "t":
        fig.add_hline(y=0, line_dash="dot", line_color="#5DADE2",
                      annotation_text="0 °C", annotation_position="bottom right")
    fig.update_layout(template=_plotly_template(), height=360, title=titre,
                      margin=dict(l=10, r=10, t=40, b=10),
                      yaxis=dict(title=unit),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig
