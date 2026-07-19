# -*- coding: utf-8 -*-
"""Climatologie (normale saisonnière en cosinus) & anomalie — adaptateur
canicule de core/stats/climato.py (formule cosinus mutualisée).

Les 3 paramètres par défaut (config.CLIM_MEAN/AMPLITUDE/PEAK_DOY) sont une
ESTIMATION, pas une normale officielle issue d'une série climatologique réelle
— ajustables via la page Indicateur de canicule → Réglages avancés, stockés en
session pour s'appliquer partout (KPI, graphiques) tant que l'appli reste
ouverte. La gestion de session vit ici, pas dans core/ : c'est un choix propre
à cette app (core/ n'importe ni config ni état Streamlit)."""

import streamlit as st

import config as C
from core.stats.climato import cosine_normal


def clim_params():
    """(moyenne, amplitude, jour du pic) effectifs — session si ajustés, sinon config.py."""
    return (
        st.session_state.get("clim_mean", C.CLIM_MEAN),
        st.session_state.get("clim_amplitude", C.CLIM_AMPLITUDE),
        st.session_state.get("clim_peak_doy", C.CLIM_PEAK_DOY),
    )


def clim_normal(when):
    """Normale climatique T850 saisonnière (cosinus). `when` : Timestamp ou Series."""
    mean, amplitude, peak_doy = clim_params()
    return cosine_normal(when, mean, amplitude, peak_doy)


def clim_z500_normal(when):
    """Normale climatique Z500 saisonnière (cosinus, config.CLIM_Z500_*), en
    mètres géopotentiels. Sert à convertir la médiane Z500 en ANOMALIE — seule
    lecture interprétable du géopotentiel. Pas de réglage session (contrairement
    à la T850) : Z500 est une variable de contexte, pas un indicateur ajusté."""
    return cosine_normal(when, C.CLIM_Z500_MEAN, C.CLIM_Z500_AMPLITUDE,
                         C.CLIM_Z500_PEAK_DOY)
