# -*- coding: utf-8 -*-
"""Tables d'export de l'onglet 🧾 (Explorer un run) — volontairement LARGES,
pensées pour l'export vers une analyse externe (IA), pas pour la lecture à
l'écran : ne pas les « alléger » pour la lisibilité (invariant CLAUDE.md)."""

import config as C
from app.runtime import VAR
from app.stats.ensemble import super_ensemble, _model_median


def enriched_super_table(sub, prev_sub=None, j1_sub=None, j2_sub=None):
    """Table d'export du super-ensemble, enrichie par modèle : médiane, contrôle
    (member 0), nb de membres actifs, médiane Z500 (contexte synoptique, absente
    en silence si la variable n'est pas dans `sub`) et Δ T850 vs le run précédent
    de CE modèle (cf. previous_runs_sub) ainsi que vs le run le plus proche de
    J-1/J-2 (cf. n_days_before_sub, `j1_sub`/`j2_sub`). Volontairement large :
    pensée pour l'export vers une analyse externe (IA), pas pour la lecture à
    l'écran."""
    se = super_ensemble(sub)
    if se is None or se.empty:
        return se
    out = se.set_index("valid_time")
    if prev_sub is not None:
        prev_se = super_ensemble(prev_sub)
        if prev_se is not None and not prev_se.empty:
            prev_med = prev_se.set_index("valid_time")["Médiane"]
            out["Δ Médiane vs préc."] = (out["Médiane"] - prev_med.reindex(out.index)).round(2)
    for model in C.MODEL_LABELS:
        s = sub[sub["model"] == model]
        if s.empty:
            continue
        piv = s.pivot_table(index="valid_time", columns="member", values=VAR).sort_index()
        med = piv.median(axis=1).reindex(out.index)
        out[f"{model} médiane"] = med.round(2)
        if 0 in piv.columns:
            out[f"{model} contrôle"] = piv[0].reindex(out.index).round(2)
        out[f"{model} n membres"] = (piv.notna().sum(axis=1)
                                     .reindex(out.index).fillna(0).astype(int))
        if "z500" in s.columns:
            zpiv = s.pivot_table(index="valid_time", columns="member", values="z500").sort_index()
            if zpiv.notna().any(axis=None):
                out[f"{model} z500"] = zpiv.median(axis=1).reindex(out.index).round(2)
        if prev_sub is not None:
            pmed = _model_median(prev_sub, model)
            if pmed is not None:
                out[f"{model} Δ médiane"] = (med - pmed.reindex(out.index)).round(2)
        if j1_sub is not None:
            pmed = _model_median(j1_sub, model)
            if pmed is not None:
                out[f"{model} Δ J-1"] = (med - pmed.reindex(out.index)).round(2)
        if j2_sub is not None:
            pmed = _model_median(j2_sub, model)
            if pmed is not None:
                out[f"{model} Δ J-2"] = (med - pmed.reindex(out.index)).round(2)
    return out.reset_index()


def model_table(sub, model, prev_sub=None, j1_sub=None, j2_sub=None):
    """Table d'export d'UN modèle : mêmes stats d'ensemble que le super-ensemble
    mais restreintes à ses seuls membres, plus le contrôle (member 0), la
    médiane Z500 (contexte, absente en silence si non disponible) et le Δ T850
    vs le run précédent de CE modèle ainsi que vs le run le plus proche de
    J-1/J-2."""
    s = sub[sub["model"] == model]
    se = super_ensemble(s)
    if se is None or se.empty:
        return se
    out = se.drop(columns=["n_models"]).set_index("valid_time")
    piv = s.pivot_table(index="valid_time", columns="member", values=VAR).sort_index()
    if 0 in piv.columns:
        out["Contrôle"] = piv[0].reindex(out.index).round(2)
    if "z500" in s.columns:
        zpiv = s.pivot_table(index="valid_time", columns="member", values="z500").sort_index()
        if zpiv.notna().any(axis=None):
            out["Z500"] = zpiv.median(axis=1).reindex(out.index).round(2)
    if prev_sub is not None:
        pmed = _model_median(prev_sub, model)
        if pmed is not None:
            out["Δ médiane vs préc."] = (out["Médiane"] - pmed.reindex(out.index)).round(2)
    if j1_sub is not None:
        pmed = _model_median(j1_sub, model)
        if pmed is not None:
            out["Δ J-1"] = (out["Médiane"] - pmed.reindex(out.index)).round(2)
    if j2_sub is not None:
        pmed = _model_median(j2_sub, model)
        if pmed is not None:
            out["Δ J-2"] = (out["Médiane"] - pmed.reindex(out.index)).round(2)
    return out.reset_index()
