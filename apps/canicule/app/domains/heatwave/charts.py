# -*- coding: utf-8 -*-
"""Rendus propres au domaine canicule : ligne de flottaison (seuils +
normale), cases du calendrier du risque, heatmap de tendance, barres de
confiance. Le calendrier n'est plus une figure Plotly mais une grille de
cartes (cf. cases_calendrier_risques)."""

import pandas as pd
import plotly.graph_objects as go

from app.stats.climato import clim_normal
from app.ui.theme import _ink, _rgba
from app.domains.heatwave.logic import (
    PROB_CANICULE_QUASI, PROB_RISQUE_MARQUE, PROB_RISQUE_MODERE, TREND_STRONG_C,
    _canicule_label, _confiance_label, _tendance_label, incertitude_txtn)
from core.ui.plotly_theme import apply_layout


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
                  annotation_text=f"Canicule — {seuil_canicule:.0f} °C",
                  annotation_position="top left", annotation_font=dict(color="#C0392B", size=12))
    return apply_layout(fig, title=titre, height="tall",
                        y_title="Température à 850 hPa (°C)")


def _niveau_risque(prob):
    """Probabilité de canicule → niveau 0-3 de l'échelle de risque partagée
    (core/ui/tokens). Mêmes paliers que les libellés de `_canicule_label` : la
    couleur et le mot dits d'une case ne peuvent pas diverger."""
    if prob >= PROB_CANICULE_QUASI:
        return 3
    if prob >= PROB_RISQUE_MARQUE:
        return 2
    if prob >= PROB_RISQUE_MODERE:
        return 1
    return 0


def cases_calendrier_risques(jours, seuil, txtn=None):
    """Cases du calendrier du risque, prêtes pour core/ui/components.risk_calendar.

    La COULEUR d'une case est pilotée par la probabilité T850 UNIQUEMENT
    (invariant). `txtn` (DataFrame _TXTN_COLS, cf. app/data/t2m.py) est un
    simple appui d'affichage : la Tx haute résolution en chiffre dans les cases
    couvertes (J → J+6), rien sur les autres — jamais un critère de risque, et
    la fiabilité passe par un glyphe (`≈`) plus l'infobulle, jamais par la
    teinte. txtn None/vide → calendrier strictement identique à l'affichage
    sans ce flux (absence = cas normal).

    Grille de cartes plutôt qu'une heatmap d'une seule ligne : les cases se
    replient en écran étroit au lieu de se comprimer à ~20 px, ce qui laisse
    enfin la place d'écrire une valeur ET un libellé dans chacune."""
    by_day = ({pd.Timestamp(r.date).normalize(): r for r in txtn.itertuples()}
              if txtn is not None and not txtn.empty else {})
    cases = []
    for d, prob, med, p90 in zip(jours["date"], jours["prob"],
                                 jours["Médiane"], jours["P90"]):
        aide = (f"{d:%a %d %b} — {_canicule_label(prob)}"
                f" · Médiane {med:.1f} °C · P90 {p90:.1f} °C"
                f" · P(≥ {seuil:.0f} °C) {prob * 100:.0f} %")
        case = {"date": f"{d:%a %d %b}", "niveau": _niveau_risque(prob),
                "sub": f"{prob * 100:.0f} % de risque"}
        r = by_day.get(pd.Timestamp(d).normalize())
        if r is not None:
            glyphe, reserve, fiab_phrase = incertitude_txtn(
                r.ecart_tx, r.ecart_tn, r.solo, r.model, r.model_alt)
            if pd.notna(r.tx):
                case["valeur"] = f"{r.tx:.0f} °C"
            sol = " · ".join(part for part in (
                f"max {r.tx:.1f} °C" if pd.notna(r.tx) else "",
                f"min {r.tn:.1f} °C" if pd.notna(r.tn) else "") if part)
            if sol:
                aide += (f" · Au sol : {sol} ({r.model}, haute résolution)"
                         f" · Fiabilité : {fiab_phrase}")
            # Réserve marquée par un glyphe, jamais par la couleur de la case
            # (glyphe non vide = source unique ou forte divergence).
            if glyphe:
                case["flag"] = f"{glyphe} {reserve}"
        case["aide"] = aide
        cases.append(case)
    return cases


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
    return apply_layout(fig, height="strip", legend=None, hovermode="closest",
                        xaxis=dict(title=None, tickformat="%a %d/%m", type="date"),
                        yaxis=dict(visible=False))


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
    return apply_layout(fig, height="standard", y_title="Température à 850 hPa (°C)",
                        barmode="overlay",
                        xaxis=dict(title=None, tickformat="%a %d/%m", type="date"))
