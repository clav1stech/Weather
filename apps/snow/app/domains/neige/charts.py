# -*- coding: utf-8 -*-
"""Graphiques du domaine neige (Plotly, thème via app/ui/theme)."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from apps.snow import snow_config as SC
from ...ui.theme import _ink, _plotly_template, _rgba
from core.stats.ensemble import model_data, model_medians, super_ensemble


def hourly_vertical_weather_chart(profile):
    """Phase et intensité horaires aux quatre altitudes sur les prochaines 48 h."""
    fig = go.Figure()
    styles = {
        "neige": ("❄️ Neige", "diamond", "#5DADE2"),
        "pluie": ("🌧️ Pluie", "circle", "#2471A3"),
        "mixte": ("🌦️ Pluie/neige", "square", "#8E44AD"),
        "sec": ("☀️ Sec", "circle-open", "#D4AC0D"),
    }
    for phase_name, (label, symbol, color) in styles.items():
        points = profile[profile["phase"] == phase_name]
        if not points.empty:
            sizes = (np.full(len(points), 7.0) if phase_name == "sec" else
                     np.clip(10.0 + 7.0 * np.sqrt(points["quantite"].clip(lower=0)),
                             10.0, 32.0))
            sources = (points["source"] if "source" in points.columns
                       else pd.Series(["Maille fine HD"] * len(points),
                                      index=points.index))
            hover = []
            snow_values = points.get(
                "neige_cm", pd.Series(np.nan, index=points.index))
            rain_values = points.get(
                "pluie_mm", pd.Series(np.nan, index=points.index))
            for vt, alt, q, unit, temp, source, snow, rain in zip(
                    points["valid_time"], points["altitude_m"],
                    points["quantite"], points["unite"], points["t2m_c"],
                    sources, snow_values, rain_values):
                if phase_name == "sec":
                    amount = "sec"
                elif phase_name == "mixte":
                    amount = f"❄️ {snow:.1f} cm · 🌧️ {rain:.1f} mm"
                else:
                    amount = f"{q:.1f} {unit}"
                    # AROME-PI peut diagnostiquer une phase dominante avec une
                    # faible composante opposée : elle reste visible au survol.
                    if phase_name == "neige" and pd.notna(rain) \
                            and rain >= 0.1:
                        amount += f" · 🌧️ {rain:.1f} mm"
                    if phase_name == "pluie" and pd.notna(snow) \
                            and snow >= 0.1:
                        amount += f" · ❄️ {snow:.1f} cm"
                hover.append(
                    f"<b>{pd.Timestamp(vt):%a %d %b · %Hh}</b><br>"
                    f"{alt:.0f} m · {label}<br>{amount}"
                    f"<br>T2m {temp:+.1f} °C<br>Source : {source}")
            fig.add_scatter(
                x=points["valid_time"], y=points["altitude_m"], mode="markers",
                name=label, marker=dict(symbol=symbol, color=color, size=sizes,
                                        line=dict(color=color, width=1.2)),
                hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
            )

    start = pd.Timestamp(profile["valid_time"].min()).floor("6h")
    end = pd.Timestamp(profile["valid_time"].max()).ceil("6h")
    ticks = pd.date_range(start, end, freq="6h")
    for boundary in pd.date_range(start.normalize() + pd.Timedelta(days=1),
                                  end.normalize(), freq="D"):
        fig.add_vline(x=boundary, line_color=_ink(), line_width=0.8,
                      line_dash="dot", opacity=0.35)
    fig.update_layout(
        template=_plotly_template(), height=360,
        margin=dict(l=10, r=10, t=15, b=90),
        xaxis=dict(tickmode="array", tickvals=ticks,
                   ticktext=[f"{t:%a %d}<br>{t:%Hh}" for t in ticks],
                   range=[profile["valid_time"].min(), profile["valid_time"].max()]),
        yaxis=dict(title="Altitude", tickmode="array",
                   tickvals=sorted(profile["altitude_m"].unique()),
                   ticktext=[f"{a:.0f} m" for a in sorted(profile["altitude_m"].unique())],
                   range=[1020, 2080]),
        legend=dict(orientation="h", yanchor="top", y=-0.20,
                    xanchor="left", x=0),
    )
    return fig


def weather_type_chart(daily, hd_reference=None):
    """Proportions pondérées, contextualisées par le scénario HD à 48 h."""
    styles = {
        "neigeux": ("❄️ Neigeux", "#5DADE2", "❄️"),
        "pluvieux": ("🌧️ Pluvieux (≥ 2 mm)", "#2874A6", "🌧️"),
        "sec": ("☀️ Sec / ensoleillé", "#F4D03F", "☀️"),
        "mixte": ("🌦️ Trace / mixte / incertain", "#AAB7B8", "🌦️"),
    }
    fig = go.Figure()
    for category, (label, color, emoji) in styles.items():
        text = [emoji if value > 0 else "" for value in daily[category]]
        fig.add_bar(
            x=daily["date"], y=daily[category], name=label,
            marker_color=color, text=text, textposition="inside",
            insidetextanchor="middle", textfont=dict(size=17),
            customdata=np.stack([daily["jour"], daily["n_classes"]], axis=-1),
            hovertemplate=("J+%{customdata[0]} · %{x|%a %d %b}<br>"
                           f"{label} %{{y:.0f}} %<br>"
                           "%{customdata[1]} membres classés avant pondération"
                           "<extra></extra>"),
        )
    if hd_reference is not None and not hd_reference.empty:
        hd_styles = {
            "neigeux": ("❄️", "neige"),
            "pluvieux": ("🌧️", "pluie"),
            "sec": ("☀️", "sec"),
            "mixte": ("🌦️", "trace ou phase mixte"),
        }
        text, hover = [], []
        for row in hd_reference.itertuples(index=False):
            emoji, label = hd_styles[row.categorie]
            suffix = "*" if row.partiel else ""
            text.append(f"HD {emoji}{suffix}")
            coverage = (f"couverture partielle ({int(row.heures_hd)} h)"
                        if row.partiel else "journée couverte 24 h")
            hover.append(
                f"<b>Maille fine HD au village : {label}</b><br>"
                f"pluie {row.pluie_mm:.1f} mm · neige {row.neige_cm:.1f} cm<br>"
                f"{coverage}"
            )
        # Axe superposé sans graduations : la référence HD reste au-dessus
        # des barres et ne déforme jamais leur axe probabiliste 0–100 %.
        fig.add_scatter(
            x=hd_reference["date"], y=[1.03] * len(hd_reference), yaxis="y2",
            mode="markers+text", name="Maille fine HD (village)",
            marker=dict(size=5, color=_ink(), opacity=0),
            text=text, textposition="top center", textfont=dict(size=12),
            hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
            showlegend=False,
        )
    ticks = [f"J+{int(j)}<br>{d:%a %d}"
             for d, j in zip(daily["date"], daily["jour"])]
    fig.update_layout(
        template=_plotly_template(), height=420, barmode="stack",
        margin=dict(l=10, r=10, t=25, b=100),
        xaxis=dict(tickmode="array", tickvals=daily["date"], ticktext=ticks),
        yaxis=dict(title="Part des membres (%)", range=[0, 100]),
        yaxis2=dict(overlaying="y", range=[0, 1.12], visible=False,
                    fixedrange=True),
        legend=dict(orientation="h", yanchor="top", y=-0.20,
                    xanchor="left", x=0),
    )
    return fig


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
        template=_plotly_template(), height=380,
        margin=dict(l=10, r=10, t=25, b=90),
        yaxis=dict(title="cm / jour", rangemode="tozero"),
        yaxis2=dict(title="%", overlaying="y", side="right", range=[0, 100],
                    showgrid=False),
        legend=dict(orientation="h", yanchor="top", y=-0.20,
                    xanchor="left", x=0),
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
    fig.update_layout(template=_plotly_template(), height=400,
                      margin=dict(l=10, r=10, t=25, b=90),
                      yaxis=dict(title="altitude (m)"),
                      legend=dict(orientation="h", yanchor="top", y=-0.20,
                                  xanchor="left", x=0))
    return fig


def medians_chart(sub, var, title, unit, seuils_h=None):
    """Médianes par modèle avec amplitude P10–P90 de leurs membres.

    La bande colorée donne la dispersion interne de chaque modèle, la ligne
    garde la lecture comparative des médianes. ``seuils_h`` ajoute les repères
    physiques du domaine sans participer aux calculs.
    """
    meds = model_medians(sub, var, SC.ENS_LABELS)
    if meds is None or not meds.notna().any(axis=None):
        return None
    fig = go.Figure()
    for model in meds.columns:
        loaded = model_data(sub, model, var)
        if loaded is not None:
            stats = loaded[0]
            color = SC.COLOR_BY_LABEL.get(model)
            fig.add_scatter(x=stats["valid_time"], y=stats["p90"],
                            line=dict(width=0), showlegend=False,
                            hoverinfo="skip")
            fig.add_scatter(x=stats["valid_time"], y=stats["p10"],
                            line=dict(width=0), fill="tonexty",
                            fillcolor=_rgba(color, 0.09), showlegend=False,
                            hoverinfo="skip")
        fig.add_scatter(x=meds.index, y=meds[model], name=model,
                        line=dict(color=SC.COLOR_BY_LABEL.get(model), width=1.8))
    for label, y in (seuils_h or {}).items():
        fig.add_hline(y=y, line_dash="dot", line_color=_ink(),
                      annotation_text=label, annotation_position="bottom right")
    fig.update_layout(template=_plotly_template(), height=390,
                      title=dict(text=title, x=0.01, xanchor="left", y=0.98),
                      margin=dict(l=10, r=10, t=75, b=85),
                      yaxis=dict(title=unit),
                      legend=dict(orientation="h", yanchor="top", y=-0.20,
                                  xanchor="left", x=0))
    return fig


def fan_chart(sub, var, title, unit, seuil=0.0):
    """Panache du super-ensemble jusqu'aux extrêmes Min–Max.

    Les bandes P25–P75 et P10–P90 portent le cœur probabiliste ; Min–Max,
    volontairement plus pâle, expose l'amplitude complète entre membres sans
    lui donner le même poids visuel que les quantiles robustes.
    """
    se = super_ensemble(sub, var, seuil)
    if se is None or se.empty:
        return None
    fig = go.Figure()
    for lo, hi, alpha in (("Min", "Max", 0.06), ("P10", "P90", 0.14),
                          ("P25", "P75", 0.22)):
        fig.add_scatter(x=se["valid_time"], y=se[hi], line=dict(width=0),
                        showlegend=False, hoverinfo="skip")
        fig.add_scatter(x=se["valid_time"], y=se[lo], fill="tonexty",
                        fillcolor=_rgba("#5DADE2", alpha), line=dict(width=0),
                        name=f"{lo}–{hi}")
    fig.add_scatter(x=se["valid_time"], y=se["Médiane"], name="Médiane",
                    line=dict(color="#2E86C1", width=2.2))
    fig.update_layout(template=_plotly_template(), height=420,
                      title=dict(text=title, x=0.01, xanchor="left", y=0.98),
                      margin=dict(l=10, r=10, t=75, b=90),
                      yaxis=dict(title=unit),
                      legend=dict(orientation="h", yanchor="top", y=-0.20,
                                  xanchor="left", x=0))
    return fig
