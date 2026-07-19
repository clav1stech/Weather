# -*- coding: utf-8 -*-
"""Composants Streamlit réutilisables (multi-pages) : carte KPI HTML, légende
des runs retenus."""

import pandas as pd

import config as C
from app.data.db import run_label_text
from app.stats.climato import clim_normal


def complete_runs_caption(sources):
    """Légende « Modèle cycle » listant, par modèle, le run retenu (ordre config)."""
    parts = [f"{label} {run_label_text(sources[label])}"
             for label in C.MODEL_LABELS if label in sources]
    return " · ".join(parts)


def _kpi_card(label, value, help_txt="", value_point=None, valid_time=None, sub=""):
    """Carte KPI ; si value_point + valid_time fournis, affiche l'anomalie vs la
    normale climatique saisonnière (cosinus) à cette date. `sub` : ligne de
    détail visible sous la valeur (date, probabilité…) — contrairement à
    help_txt qui n'apparaît qu'au survol."""
    anomalie_html = ""
    if value_point is not None and valid_time is not None:
        delta = value_point - float(clim_normal(pd.Timestamp(valid_time)))
        if delta >= 0.05:
            couleur, signe = "#C0392B", "+"
        elif delta <= -0.05:
            couleur, signe = "#2980B9", "−"
        else:
            couleur, signe = "#7F8C8D", "±"
        anomalie_html = (f"<span style='color:{couleur};font-size:0.95rem;font-weight:600;"
                         f"margin-left:8px;white-space:nowrap;'>"
                         f"({signe}{abs(delta):.1f} °C norm.)</span>")
    title_attr = f' title="{help_txt}"' if help_txt else ""
    sub_html = (f"<div style='font-size:0.78rem;opacity:0.65;margin-top:2px;"
                f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>{sub}</div>"
                if sub else "")
    return (f"<div{title_attr} style='background:rgba(128,138,157,0.10);"
            "border:1px solid rgba(128,138,157,0.25);"
            "border-radius:12px;padding:12px 16px;height:100%;'>"
            f"<div style='font-size:0.8rem;opacity:0.7;'>{label}</div>"
            f"<div style='font-size:1.85rem;font-weight:600;color:inherit;line-height:1.3;'>"
            f"{value}{anomalie_html}</div>{sub_html}</div>")
