# -*- coding: utf-8 -*-
"""Thème clair / sombre — adapte graphiques et cartes au thème actif."""

import streamlit as st

# CSS global injecté une fois par meteo_app (entrée) — cartes KPI et graphiques.
GLOBAL_CSS = """
    <style>
      .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
      h1, h2, h3 {letter-spacing: -0.01em;}
      div[data-testid="stMetric"] {
          background: rgba(128,138,157,0.10);
          border: 1px solid rgba(128,138,157,0.25);
          border-radius: 12px; padding: 12px 16px;}
      .stPlotlyChart {border: 1px solid rgba(128,138,157,0.22); border-radius: 12px; padding: 4px;}
    </style>
    """


def _is_dark():
    """Le thème sombre est-il actif ? Gère le mode auto/système (Streamlit récent)
    via st.context.theme, avec repli sur la base configurée dans config.toml."""
    try:
        theme = st.context.theme
        if theme is not None and getattr(theme, "type", None):
            return theme.type == "dark"
    except Exception:  # noqa: BLE001
        pass
    try:
        return (st.get_option("theme.base") or "light").lower() == "dark"
    except Exception:  # noqa: BLE001
        return False


def _plotly_template():
    """Template Plotly cohérent avec le thème courant. Template ET couleurs d'encre
    (cf. _ink) partagent _is_dark() : même si la détection ne colle pas exactement à
    la page, le graphique reste lisible car ses fonds et traits restent cohérents."""
    return "plotly_dark" if _is_dark() else "plotly_white"


def _ink(dark=None):
    """Couleur des traits/textes forts (médiane, contrôle, axe zéro), lisible quel
    que soit le thème : ardoise sur fond clair, presque-blanc sur fond sombre."""
    if dark is None:
        dark = _is_dark()
    return "#E6E9EE" if dark else "#2C3E50"


def _rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"
