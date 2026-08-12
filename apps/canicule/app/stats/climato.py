# -*- coding: utf-8 -*-
"""Climatologie (normale saisonnière T850) & anomalie — adaptateur canicule
de core/stats/climato.py.

Par défaut, la normale est lue directement dans CLIM_T850_DAILY_NORMAL (table
de 365 valeurs réelles ERA5 1991-2020, lissée une fois pour toutes — un simple
indexage, jamais recalculée à l'affichage). CLIM_MEAN/AMPLITUDE/PEAK_DOY (fit
cosinus sur cette même table) ne servent que de repli manuel : dès que
l'utilisateur modifie un des 3 réglages avancés (page Indicateur de canicule
→ Réglages avancés), stockés en session, le calcul bascule sur le modèle
cosinus ajustable pour refléter son choix. La gestion de session vit ici, pas
dans core/ : c'est un choix propre à cette app (core/ n'importe ni config ni
état Streamlit)."""

import streamlit as st

import config as C
from core.stats.climato import cosine_normal, daily_table_normal


def clim_params():
    """(moyenne, amplitude, jour du pic) effectifs — session si ajustés, sinon config.py."""
    return (
        st.session_state.get("clim_mean", C.CLIM_MEAN),
        st.session_state.get("clim_amplitude", C.CLIM_AMPLITUDE),
        st.session_state.get("clim_peak_doy", C.CLIM_PEAK_DOY),
    )


def clim_normal(when):
    """Normale climatique T850. `when` : Timestamp ou Series. Table journalière
    réelle par défaut ; cosinus ajustable dès qu'un réglage avancé est modifié.

    Le widget « jour du pic » est un number_input ENTIER (UX en jours) : sa
    valeur en session est un int dès qu'il a été rendu au moins une fois, mais
    tant que la page n'a jamais été visitée, `clim_params()` retombe sur
    C.CLIM_PEAK_DOY tel quel — un float (213.5, précision du fit). Comparer
    en int des deux côtés couvre les deux cas sans détecter à tort une
    modification."""
    mean, amplitude, peak_doy = clim_params()
    is_default = (
        mean == C.CLIM_MEAN
        and amplitude == C.CLIM_AMPLITUDE
        and int(peak_doy) == int(C.CLIM_PEAK_DOY)
    )
    if is_default:
        return daily_table_normal(when, C.CLIM_T850_DAILY_NORMAL)
    return cosine_normal(when, mean, amplitude, peak_doy)


def clim_z500_normal(when):
    """Normale climatique Z500 saisonnière (cosinus, config.CLIM_Z500_*), en
    mètres géopotentiels. Sert à convertir la médiane Z500 en ANOMALIE — seule
    lecture interprétable du géopotentiel. Pas de réglage session (contrairement
    à la T850) : Z500 est une variable de contexte, pas un indicateur ajusté."""
    return cosine_normal(when, C.CLIM_Z500_MEAN, C.CLIM_Z500_AMPLITUDE,
                         C.CLIM_Z500_PEAK_DOY)
