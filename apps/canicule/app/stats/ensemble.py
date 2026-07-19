# -*- coding: utf-8 -*-
"""Adaptateur canicule des statistiques d'ensemble mutualisées
(core/stats/ensemble.py) : lie la config du projet (variable principale VAR,
seuil canicule, listes de modèles) aux fonctions génériques, en conservant les
SIGNATURES HISTORIQUES — noms et arguments sont le contrat des pages, des
domaines et du harnais de non-régression."""

import config as C
from app.runtime import VAR
from core.stats import ensemble as _core

_PCT_COLS = _core._PCT_COLS


def member_matrix(sub, var=VAR):
    return _core.member_matrix(sub, var)


def var_median(sub, var):
    return _core.var_median(sub, var)


def super_ensemble(sub):
    return _core.super_ensemble(sub, VAR, C.SEUIL_CANICULE_850)


def model_data(sub, model):
    return _core.model_data(sub, model, VAR)


def model_medians(sub):
    return _core.model_medians(sub, VAR, C.MODEL_LABELS)


def _model_median(sub, model):
    return _core._model_median(sub, model, VAR)


def divergence(sub):
    return _core.divergence(sub, VAR, C.MODEL_LABELS, C.MAIN_LABELS)


def multimodel_cutoff(sub):
    return _core.multimodel_cutoff(sub, VAR, C.SEUIL_CANICULE_850)


def daily_aggregate(se):
    return _core.daily_aggregate(se)


def daily_risk(sub, seuil):
    return _core.daily_risk(sub, seuil, VAR)
