# -*- coding: utf-8 -*-
"""Graphiques propres au domaine observations : comparaison de température
entre les 4 stations (bandes nocturnes pour faire ressortir l'ICU), série de
l'écart ICU urbain − aéré, confrontation Tx/Tn prévus vs observés."""

import pandas as pd
import plotly.graph_objects as go

import config as C
from app.ui.theme import _ink, _plotly_template, _rgba
from app.domains.observations.logic import NUIT_DEBUT_H, NUIT_FIN_H, lisser_prevision


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


def comparaison_stations(dfw, titre, complement_6m=None):
    """Température observée des 4 stations sur la fenêtre (couleurs config,
    bandes grises = nuits). Une station sans données sur la fenêtre est
    simplement absente (jamais de trace vide trompeuse).

    `complement_6m` (optionnel) : mesures 6 min POSTÉRIEURES à la dernière
    heure consolidée du flux horaire (cf. obs_6m_depuis) — tracées en POINTILLÉ
    dans la couleur de leur station, raccordées au dernier point horaire, pour
    prolonger la lecture jusqu'aux relevés les plus frais. Marquage volontaire :
    le trait plein est la donnée horaire consolidée (celle des calculs), le
    pointillé un aperçu 6 min qui ne couvre que les stations RADOME — deux
    courbes sur quatre s'arrêtant plus tôt est l'état normal, pas un trou."""
    fig = go.Figure()
    comp = complement_6m if complement_6m is not None else pd.DataFrame()
    t_max_global = dfw["valid_time"].max()
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
        if comp.empty:
            continue
        g6 = comp[(comp["station_nom"] == station["nom"]) & comp["t"].notna()] \
            .sort_values("valid_time")
        if g6.empty:
            continue
        # Raccord visuel : le pointillé démarre au dernier point horaire de la
        # station (sinon un trait flottant suggérerait une rupture de mesure).
        dernier = g.sort_values("valid_time").iloc[-1]
        xs = pd.concat([pd.Series([dernier["valid_time"]]), g6["valid_time"]])
        ys = pd.concat([pd.Series([dernier["t"]]), g6["t"]])
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", name=f"{station['nom']} (6 min)",
            showlegend=False,
            line=dict(color=station["color"], width=1.6, dash="dot"),
            hovertemplate=f"{station['nom']} · 6 min · %{{x|%a %d %b %Hh%M}}<br>"
                          "%{y:.1f} °C<extra></extra>"))
        t_max_global = max(t_max_global, g6["valid_time"].max())
    if fig.data:
        _bandes_nocturnes(fig, dfw["valid_time"].min(), t_max_global)
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


# Teinte unique des courbes de prévision (ambre), distincte du bleu Montsouris
# de l'observé : les leads se différencient par l'OPACITÉ (lead récent = opaque,
# lead ancien = pâle) et le style de trait, pas par la couleur — on lit d'un
# coup « les prévisions convergent vers l'observé à mesure que le lead diminue ».
_VINTAGE_HEX = "#E67E22"

# Le lissage des courbes de prévision vit dans logic (lisser_prevision) : il
# est partagé avec le tableau d'écarts, qui doit chiffrer exactement la même
# courbe que celle tracée ici.


def vintage_comparison_chart(obs_df, vintage_series_df, titre):
    """Convergence de la prévision à Montsouris : température OBSERVÉE (6 min,
    trait plein épais, couleur de référence Montsouris) surchargée des courbes
    de prévision émises à divers reculs (lead 0 = dernière prévision, puis J-6h,
    J-12h… en ambre de plus en plus pâle et pointillé). `vintage_series_df` au
    format long [valid_time, lead_h, temperature] (cf. logic.vintage_comparison_series).

    Une série vide (lead sans données — historique trop court pour ce recul)
    n'est jamais tracée : pas de trace fantôme, comme comparaison_stations pour
    une station muette."""
    ref = next((s for s in C.OBS_STATIONS if s.get("reference")), C.OBS_STATIONS[0])
    fig = go.Figure()

    obs = obs_df[obs_df["t"].notna()] if obs_df is not None and not obs_df.empty \
        else pd.DataFrame(columns=["valid_time", "t"])
    if not obs.empty:
        fig.add_trace(go.Scatter(
            x=obs["valid_time"], y=obs["t"], mode="lines",
            name=f"Observé ({ref['nom']})",
            line=dict(color=ref["color"], width=3.5),
            hovertemplate="Observé · %{x|%a %d %b %Hh%M}<br>%{y:.1f} °C<extra></extra>"))

    leads = (sorted(vintage_series_df["lead_h"].unique())
             if vintage_series_df is not None and not vintage_series_df.empty else [])
    lead_max = max(leads) if leads and max(leads) > 0 else 1
    for h in leads:
        g = vintage_series_df[(vintage_series_df["lead_h"] == h)
                              & vintage_series_df["temperature"].notna()] \
            .sort_values("valid_time")
        if g.empty:
            continue
        # Opacité dégressive avec le recul (lead 0 = plein, J-24h = pâle).
        alpha = 1.0 - 0.6 * (h / lead_max)
        label = "Prévision (dernière)" if h == 0 else f"Prévision J−{h}h"
        fig.add_trace(go.Scatter(
            x=g["valid_time"], y=lisser_prevision(g["temperature"]), mode="lines", name=label,
            line=dict(color=_rgba(_VINTAGE_HEX, alpha),
                      width=2.5 if h == 0 else 1.6,
                      dash="solid" if h == 0 else "dot"),
            hovertemplate=f"{label} · %{{x|%a %d %b %Hh%M}}<br>%{{y:.1f}} °C<extra></extra>"))

    if fig.data:
        xs = []
        if not obs.empty:
            xs += [obs["valid_time"].min(), obs["valid_time"].max()]
        if leads:
            xs += [vintage_series_df["valid_time"].min(),
                   vintage_series_df["valid_time"].max()]
        _bandes_nocturnes(fig, min(xs), max(xs))
    fig.update_layout(title=titre, height=430, hovermode="x unified",
                      template=_plotly_template(), xaxis_title=None,
                      yaxis_title="Température (°C)",
                      legend=dict(orientation="h", y=1.08),
                      margin=dict(t=70, l=10, r=10, b=10))
    return fig
