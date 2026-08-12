# -*- coding: utf-8 -*-
"""Normale saisonnière en cosinus — formule GÉNÉRIQUE.

Une seule harmonique annuelle : normale(jour) = mean + amplitude ×
cos(2π (doy − peak_doy) / 365.25). Chaque app fournit SES paramètres
(mean/amplitude/peak_doy) via son adaptateur — ici, aucune config importée
(règle core/) ni gestion de session Streamlit (les ajustements en session sont
un choix d'app, cf. apps/canicule/app/stats/climato.py)."""

import numpy as np
import pandas as pd


def cosine_normal(when, mean, amplitude, peak_doy):
    """Normale climatique saisonnière (cosinus). `when` : Timestamp ou Series."""
    doy = pd.to_datetime(when)
    doy = doy.dt.dayofyear if hasattr(doy, "dt") else doy.dayofyear
    return mean + amplitude * np.cos(2 * np.pi * (doy - peak_doy) / 365.25)


def doy365(when):
    """Jour de l'année (1-365) avec le 29 février FUSIONNÉ au 28 (même case
    climatologique) — convention utilisée pour construire et lire toute table
    de normale journalière à 365 valeurs. `when` : Timestamp ou Series."""
    ts = pd.to_datetime(when)
    is_series = hasattr(ts, "dt")
    doy = ts.dt.dayofyear if is_series else ts.dayofyear
    is_leap = ts.dt.is_leap_year if is_series else ts.is_leap_year
    if is_series:
        return doy - ((is_leap) & (doy > 59)).astype(int)
    return doy - 1 if (is_leap and doy > 59) else doy


def daily_table_normal(when, table):
    """Normale climatique lue dans une table de 365 valeurs journalières déjà
    lissées (index 0 = 1er janvier) — un simple indexage, aucun recalcul.
    `when` : Timestamp ou Series ; `table` : séquence de 365 valeurs."""
    table = np.asarray(table)
    doy = doy365(when)
    if hasattr(doy, "values"):
        return pd.Series(table[doy.values - 1], index=doy.index)
    return table[doy - 1]
