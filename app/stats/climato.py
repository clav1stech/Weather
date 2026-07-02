# -*- coding: utf-8 -*-
"""Climatologie (normale saisonnière en cosinus) & anomalie.

Les 3 paramètres par défaut (config.CLIM_MEAN/AMPLITUDE/PEAK_DOY) sont une
ESTIMATION, pas une normale officielle issue d'une série climatologique réelle
— ajustables via la page Indicateur de canicule → Réglages avancés, stockés en
session pour s'appliquer partout (KPI, graphiques) tant que l'appli reste
ouverte."""

import numpy as np
import pandas as pd
import streamlit as st

import config as C


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
    doy = pd.to_datetime(when)
    doy = doy.dt.dayofyear if hasattr(doy, "dt") else doy.dayofyear
    return mean + amplitude * np.cos(2 * np.pi * (doy - peak_doy) / 365.25)
