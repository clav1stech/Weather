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
