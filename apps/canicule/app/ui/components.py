# -*- coding: utf-8 -*-
"""Composants propres au canicule : adaptateur de la carte KPI partagée (il y
lie la climatologie de config.py) et légende des runs retenus. Tout le rendu
vit dans core/ui/components.py, commun aux deux apps."""

import pandas as pd

import config as C
from app.data.db import run_label_text
from app.stats.climato import clim_normal
from core.ui.components import kpi_html


def complete_runs_caption(sources):
    """Légende « Modèle cycle » listant, par modèle, le run retenu (ordre config)."""
    parts = [f"{label} {run_label_text(sources[label])}"
             for label in C.MODEL_LABELS if label in sources]
    return " · ".join(parts)


def _kpi_card(label, value, help_txt="", value_point=None, valid_time=None,
              sub="", into=None):
    """ADAPTATEUR de la carte KPI partagée (core/ui/components.kpi_html) : ne
    reste ici que ce qui dépend de la config canicule — l'anomalie vs la normale
    climatique saisonnière (cosinus) à l'échéance affichée, quand value_point et
    valid_time sont fournis. `sub` : ligne de détail visible sous la valeur,
    contrairement à help_txt qui n'apparaît qu'au survol.

    Signature historique conservée (contrat des pages et du harnais de rendu) :
    sans `into`, la fonction RETOURNE le HTML ; avec `into` (colonne, expander),
    elle l'affiche directement — les pages n'ont ainsi plus à manipuler de HTML
    brut."""
    delta = delta_niveau = None
    if value_point is not None and valid_time is not None:
        ecart = value_point - float(clim_normal(pd.Timestamp(valid_time)))
        # Bande morte ±0,05 °C : un écart non significatif s'affiche « ± » et
        # reste neutre, il ne doit pas colorer la carte comme une anomalie.
        if ecart >= 0.05:
            delta_niveau, signe = "warm", "+"
        elif ecart <= -0.05:
            delta_niveau, signe = "cold", "−"
        else:
            delta_niveau, signe = "neutral", "±"
        delta = f"({signe}{abs(ecart):.1f} °C norm.)"
    html = kpi_html(label, value, delta=delta, delta_niveau=delta_niveau or "neutral",
                    sub=sub or None, aide=help_txt or None)
    if into is None:
        return html
    into.markdown(html, unsafe_allow_html=True)
    return None
