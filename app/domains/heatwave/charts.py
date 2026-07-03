# -*- coding: utf-8 -*-
"""Graphiques propres au domaine canicule : ligne de flottaison (seuils +
normale), calendrier du risque, heatmap de tendance, barres de confiance."""

import plotly.graph_objects as go

from app.stats.climato import clim_normal
from app.ui.theme import _ink, _plotly_template, _rgba
from app.domains.heatwave.logic import (
    TREND_STRONG_C, _canicule_label, _confiance_label, _tendance_label)


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


def tendance_heatmap(tend):
    """Une case par jour à venir : couleur (rouge = revu à la hausse, bleu = à
    la baisse, blanc = stable) + flèche. Lecture en un coup d'œil de la tendance
    récente des modèles sur toute la période — aucune valeur brute affichée."""
    arrows, hovers = [], []
    for _, r in tend.iterrows():
        arrow, lib = _tendance_label(r["delta"])
        arrows.append(arrow)
        hovers.append(f"{r['target']:%a %d %b}<br>Ces derniers jours : {lib}")
    zmax = max(float(tend["delta"].abs().max()), TREND_STRONG_C)
    fig = go.Figure(go.Heatmap(
        x=tend["target"], y=["Tendance récente"], z=[tend["delta"].tolist()],
        colorscale="RdBu_r", zmid=0, zmin=-zmax, zmax=zmax, xgap=3, ygap=0,
        text=[arrows], texttemplate="%{text}", textfont=dict(size=16),
        customdata=[hovers], hovertemplate="%{customdata}<extra></extra>",
        showscale=False))
    fig.update_layout(height=150, template=_plotly_template(),
                      xaxis=dict(title=None, tickformat="%a %d/%m", type="date"),
                      yaxis=dict(visible=False), margin=dict(t=10, l=10, r=10, b=10))
    return fig


def confiance_chart(daily, seuil_chaleur, seuil_canicule):
    """Grand public : fourchette probable (P10–P90) par journée, barre colorée
    selon l'accord des scénarios (spread journalier), + scénario médian en trait
    foncé. Une barre courte et verte = les modèles sont d'accord ; longue et
    orange = le chiffre du jour est à prendre avec des pincettes."""
    labels_colors = [_confiance_label(s) for s in daily["Spread"]]
    texts = [
        f"{d:%a %d %b}<br>Fourchette probable : {p10:.0f} à {p90:.0f} °C"
        f"<br>Scénario médian : {m:.1f} °C<br>Confiance : {lab}"
        for d, p10, p90, m, (lab, _) in zip(daily["date"], daily["P10"], daily["P90"],
                                            daily["Médiane"], labels_colors)
    ]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=daily["date"], y=daily["P90"] - daily["P10"], base=daily["P10"],
        marker_color=[_rgba(c, 0.55) for _, c in labels_colors],
        name="Fourchette probable (P10–P90)",
        customdata=texts, hovertemplate="%{customdata}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=daily["date"], y=daily["Médiane"], mode="lines+markers",
        name="Scénario médian", line=dict(color=_ink(), width=2.5),
        marker=dict(size=6), hoverinfo="skip"))
    fig.add_hline(y=seuil_chaleur, line=dict(color="#F39C12", width=1.5, dash="dash"),
                  annotation_text=f"Chaleur — {seuil_chaleur:.0f} °C",
                  annotation_position="top left", annotation_font=dict(color="#E67E22", size=11))
    fig.add_hline(y=seuil_canicule, line=dict(color="#E74C3C", width=1.5, dash="dash"),
                  annotation_text=f"Canicule — {seuil_canicule:.0f} °C",
                  annotation_position="top left", annotation_font=dict(color="#C0392B", size=11))
    fig.update_layout(height=400, hovermode="x unified", template=_plotly_template(),
                      xaxis=dict(title=None, tickformat="%a %d/%m", type="date"),
                      yaxis_title="Température à 850 hPa (°C)",
                      legend=dict(orientation="h", y=1.12), barmode="overlay",
                      margin=dict(t=40, l=10, r=10, b=10))
    return fig
