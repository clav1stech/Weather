# -*- coding: utf-8 -*-
"""Tables d'export de l'onglet 🧾 (Explorer un run) — volontairement LARGES,
pensées pour l'export vers une analyse externe (IA), pas pour la lecture à
l'écran : ne pas les « alléger » pour la lisibilité (invariant CLAUDE.md)."""

import config as C
from app.runtime import VAR
from app.stats.ensemble import super_ensemble, _model_median


def enriched_super_table(sub, prev_sub=None):
    """Table d'export du super-ensemble, enrichie par modèle : médiane, contrôle
    (member 0), nb de membres actifs et Δ de médiane vs le run précédent de CE
    modèle (cf. previous_runs_sub). Volontairement large : pensée pour l'export
    vers une analyse externe (IA), pas pour la lecture à l'écran."""
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
        if prev_sub is not None:
            pmed = _model_median(prev_sub, model)
            if pmed is not None:
                out[f"{model} Δ médiane"] = (med - pmed.reindex(out.index)).round(2)
    return out.reset_index()


def model_table(sub, model, prev_sub=None):
    """Table d'export d'UN modèle : mêmes stats d'ensemble que le super-ensemble
    mais restreintes à ses seuls membres, plus le contrôle (member 0) et le Δ de
    médiane vs le run précédent de CE modèle."""
    s = sub[sub["model"] == model]
    se = super_ensemble(s)
    if se is None or se.empty:
        return se
    out = se.drop(columns=["n_models"]).set_index("valid_time")
    piv = s.pivot_table(index="valid_time", columns="member", values=VAR).sort_index()
    if 0 in piv.columns:
        out["Contrôle"] = piv[0].reindex(out.index).round(2)
    if prev_sub is not None:
        pmed = _model_median(prev_sub, model)
        if pmed is not None:
            out["Δ médiane vs préc."] = (out["Médiane"] - pmed.reindex(out.index)).round(2)
    return out.reset_index()
