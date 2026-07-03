# -*- coding: utf-8 -*-
"""Graphiques propres au domaine observations : comparaison de température
entre les 4 stations (bandes nocturnes pour faire ressortir l'ICU), série de
l'écart ICU urbain − aéré, confrontation Tx/Tn prévus vs observés."""

import pandas as pd
import plotly.graph_objects as go

import config as C
from app.ui.theme import _ink, _plotly_template, _rgba
from app.domains.observations.logic import NUIT_DEBUT_H, NUIT_FIN_H


def _bandes_nocturnes(fig, t_min, t_max):
    """Grise les fenêtres nocturnes (NUIT_DEBUT_H → NUIT_FIN_H, heure de Paris)
    couvrant [t_min, t_max] — c'est là que l'écart ICU se creuse et que la
    comparaison inter-stations prend son sens. Approximation civile fixe (pas
    d'éphéméride), cohérente avec logic.ecart_icu_series."""
    day = pd.Timestamp(t_min).normalize() - pd.Timedelta(days=1)
    end = pd.Timestamp(t_max)
    while day <= end:
        debut = day + pd.Timedelta(hours=NUIT_DEBUT_H)
        fin = day + pd.Timedelta(days=1, hours=NUIT_FIN_H)
        if fin >= t_min and debut <= end:
            fig.add_vrect(x0=max(debut, pd.Timestamp(t_min)), x1=min(fin, end),
                          fillcolor="rgba(100,110,140,0.13)", line_width=0,
                          layer="below")
        day += pd.Timedelta(days=1)


def comparaison_stations(dfw, titre):
    """Température observée des 4 stations sur la fenêtre (couleurs config,
    bandes grises = nuits). Une station sans données sur la fenêtre est
    simplement absente (jamais de trace vide trompeuse)."""
    fig = go.Figure()
    for station in C.OBS_STATIONS:
        g = dfw[(dfw["station_nom"] == station["nom"]) & dfw["t"].notna()]
        if g.empty:
            continue
        fig.add_trace(go.Scatter(
            x=g["valid_time"], y=g["t"], mode="lines+markers",
            name=station["nom"], line=dict(color=station["color"], width=2),
            marker=dict(size=4),
            hovertemplate=f"{station['nom']} · %{{x|%a %d %b %Hh%M}}<br>"
                          "%{y:.1f} °C<extra></extra>"))
    if fig.data:
        _bandes_nocturnes(fig, dfw["valid_time"].min(), dfw["valid_time"].max())
    fig.update_layout(title=titre, height=430, hovermode="x unified",
                      template=_plotly_template(), xaxis_title=None,
                      yaxis_title="Température observée (°C)",
                      legend=dict(orientation="h", y=1.08),
                      margin=dict(t=70, l=10, r=10, b=10))
    return fig


def ecart_icu_chart(ecarts):
    """Écart ICU (moyenne stations urbaines − moyenne stations aérées) au fil
    des heures : barres rouges quand l'urbain dense est plus chaud, bleues en
    cas d'inversion, nuits grisées — lecture directe du « la ville ne
    refroidit pas la nuit »."""
    colors = [_rgba("#C0392B", 0.75) if e >= 0 else _rgba("#2980B9", 0.75)
              for e in ecarts["ecart"]]
    fig = go.Figure(go.Bar(
        x=ecarts["valid_time"], y=ecarts["ecart"], marker_color=colors,
        hovertemplate="%{x|%a %d %b %Hh%M}<br>Écart urbain − aéré : "
                      "%{y:+.1f} °C<extra></extra>"))
    _bandes_nocturnes(fig, ecarts["valid_time"].min(), ecarts["valid_time"].max())
    fig.add_hline(y=0, line=dict(color=_ink(), width=1))
    fig.update_layout(height=260, template=_plotly_template(), xaxis_title=None,
                      yaxis_title="Écart urbain − aéré (°C)", showlegend=False,
                      margin=dict(t=20, l=10, r=10, b=10))
    return fig


def prevu_vs_observe_chart(cmp_df, quoi):
    """Tx (`quoi`="tx") ou Tn (`quoi`="tn") : prévision HD (une SEULE valeur
    pour tout Paris — trait foncé) vs valeurs observées par station (points
    colorés). L'éparpillement vertical des points autour du trait EST le
    signal : quelle station colle à la prévision générale, laquelle s'en
    écarte (ICU local)."""
    obs_col, prevu_col = f"{quoi}_obs", f"{quoi}_prevu"
    lib = "maximale (Tx)" if quoi == "tx" else "minimale (Tn)"
    prevu = cmp_df.drop_duplicates("date").sort_values("date")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=prevu["date"], y=prevu[prevu_col], mode="lines+markers",
        name="Prévu (HD, point unique Paris)",
        line=dict(color=_ink(), width=2.5, dash="dash"), marker=dict(size=7),
        hovertemplate="%{x|%a %d %b}<br>Prévu : %{y:.1f} °C<extra></extra>"))
    for station in C.OBS_STATIONS:
        g = cmp_df[(cmp_df["station_nom"] == station["nom"])
                   & cmp_df[obs_col].notna()]
        if g.empty:
            continue
        fig.add_trace(go.Scatter(
            x=g["date"], y=g[obs_col], mode="markers", name=station["nom"],
            marker=dict(color=station["color"], size=10,
                        line=dict(color="white", width=1)),
            hovertemplate=f"{station['nom']} · %{{x|%a %d %b}}<br>"
                          "Observé : %{y:.1f} °C<extra></extra>"))
    fig.update_layout(title=f"Température {lib} — prévu vs observé",
                      height=380, template=_plotly_template(),
                      xaxis=dict(title=None, tickformat="%a %d/%m", type="date"),
                      yaxis_title="°C", legend=dict(orientation="h", y=1.12),
                      margin=dict(t=70, l=10, r=10, b=10))
    return fig
