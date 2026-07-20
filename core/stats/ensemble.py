# -*- coding: utf-8 -*-
"""Matrices de membres & statistiques d'ensemble, tolérantes NaN — GÉNÉRIQUES.

Toutes les agrégations ignorent les NaN (pandas, skipna) : l'horizon lointain
doit s'afficher proprement même quand les membres se raréfient. Ces fonctions
reçoivent un sous-ensemble `sub` de la base, quel que soit le pool de runs
choisi — aucune logique de sélection de runs ici, ni de seuil métier.

Module config-agnostique (règle core/) : la variable pivotée (`var`), le seuil
de dépassement (`seuil`) et les listes de modèles (`model_labels`,
`main_labels`) arrivent en paramètres — chaque app les lie à SA config via son
adaptateur (cf. apps/canicule/app/stats/ensemble.py), qui conserve les
signatures historiques."""

import numpy as np
import pandas as pd

_PCT_COLS = ["Min", "P10", "P25", "Médiane", "P75", "P90", "Max"]


def member_matrix(sub, var):
    """Pivot membres : index=valid_time, colonnes=(model, member). Tri temporel.

    Colonne absente de `sub` (parquet antérieur à l'ajout de la variable, run
    legacy) → None : l'absence d'une variable de contexte est un cas normal,
    jamais une erreur."""
    if sub.empty or var not in sub.columns:
        return None
    piv = sub.pivot_table(index="valid_time", columns=["model", "member"], values=var)
    return piv.sort_index()


def var_median(sub, var):
    """Médiane d'ensemble d'une variable SECONDAIRE par échéance (tous membres
    poolés, NaN ignorés) : [valid_time, median, n_membres]. None si la variable
    est absente ou sans la moindre valeur valide — les vues qui l'affichent se
    dégradent alors silencieusement, sans toucher au reste du dashboard."""
    piv = member_matrix(sub, var)
    if piv is None or not piv.notna().any(axis=None):
        return None
    out = pd.DataFrame({"valid_time": piv.index,
                        "median": piv.median(axis=1).values,
                        "n_membres": piv.notna().sum(axis=1).values})
    return out[out["n_membres"] > 0].reset_index(drop=True)


def super_ensemble(sub, var, seuil):
    """Super-ensemble : stats par échéance sur TOUS les membres poolés (multi-modèles).

    Médiane / P10 / P90 / Min / Max / Spread + écart-type, proba de dépassement
    de `seuil`, nb de membres et de modèles actifs. Toutes les agrégations
    ignorent les NaN (pandas, skipna) → l'horizon reste tracé même si les
    membres se raréfient.
    """
    piv = member_matrix(sub, var)
    if piv is None or piv.empty:
        return None
    out = pd.DataFrame({"valid_time": piv.index})
    out["Min"] = piv.min(axis=1).values
    out["P10"] = piv.quantile(0.10, axis=1).values
    out["P25"] = piv.quantile(0.25, axis=1).values
    out["Médiane"] = piv.median(axis=1).values
    out["P75"] = piv.quantile(0.75, axis=1).values
    out["P90"] = piv.quantile(0.90, axis=1).values
    out["Max"] = piv.max(axis=1).values
    out[_PCT_COLS] = out[_PCT_COLS].round(2)
    out["Spread"] = (out["P90"] - out["P10"]).round(2)
    out["Ecart-type"] = piv.std(axis=1).round(2).values
    out["Proba > seuil"] = (piv.gt(seuil).sum(axis=1) / piv.notna().sum(axis=1)).fillna(0).values
    out["n_membres"] = piv.notna().sum(axis=1).values
    # n_models : nb de modèles ayant ≥1 membre valide à cette échéance.
    pres = piv.notna().T.groupby(level="model").any().T
    out["n_models"] = pres.sum(axis=1).values
    return out.reset_index(drop=True)


def model_data(sub, model, var):
    """Données d'UN modèle : (stats, members_df, det_series).

    members_df : index=valid_time, colonnes=membres perturbés (contrôle exclu).
    det_series : membre de contrôle (member 0), ou None.
    stats : valid_time + median/p10/p90 (sur tous les membres, contrôle inclus).
    """
    s = sub[sub["model"] == model]
    if s.empty or var not in s.columns:
        return None
    piv = s.pivot_table(index="valid_time", columns="member", values=var).sort_index()
    if piv.empty:
        return None
    det = piv[0] if 0 in piv.columns else None
    members = piv.drop(columns=[0], errors="ignore")
    stats = pd.DataFrame({"valid_time": piv.index})
    stats["median"] = piv.median(axis=1).values
    stats["p10"] = piv.quantile(0.10, axis=1).values
    stats["p90"] = piv.quantile(0.90, axis=1).values
    return stats, members, det


def model_medians(sub, var, model_labels):
    """DataFrame index=valid_time, une colonne de médiane par modèle présent."""
    meds = {}
    for model in model_labels:
        s = sub[sub["model"] == model]
        if s.empty:
            continue
        piv = s.pivot_table(index="valid_time", columns="member", values=var).sort_index()
        med = piv.median(axis=1)
        if med.notna().any():
            meds[model] = med
    if not meds:
        return None
    return pd.concat(meds, axis=1).sort_index()


def _model_median(sub, model, var):
    """Médiane d'UN modèle (tous membres, contrôle inclus), indexée valid_time."""
    s = sub[sub["model"] == model]
    if s.empty:
        return None
    return s.pivot_table(index="valid_time", columns="member", values=var).median(axis=1)


def divergence(sub, var, model_labels, main_labels):
    """Divergence inter-modèles = (médiane max − médiane min) entre modèles principaux.

    Sécurité logique : calculée uniquement aux échéances où TOUS les modèles
    principaux présents dans ce run ont une médiane valide. Sur les échéances à
    composition incomplète (un modèle s'est arrêté), la valeur est masquée → pas de
    saut de référence artificiel. On se restreint aux modèles principaux
    (`main_labels`) pour qu'un modèle d'appoint à horizon court ne tronque pas
    l'analyse.
    """
    meds = model_medians(sub, var, model_labels)
    if meds is None:
        return None
    expected = [m for m in main_labels if m in meds.columns]
    if len(expected) < 2:
        return None
    full = meds[expected].dropna(how="any")  # composition complète (modèles principaux)
    if full.empty:
        return None
    div = (full.max(axis=1) - full.min(axis=1)).round(2)
    return pd.DataFrame({"valid_time": full.index, "Divergence": div.values})


def multimodel_cutoff(sub, var, seuil):
    """Dernière échéance où ≥ 2 modèles sont présents (au-delà : modèle isolé)."""
    se = super_ensemble(sub, var, seuil)
    if se is None or se.empty:
        return None
    multi = se.loc[se["n_models"] >= 2, "valid_time"]
    return pd.Timestamp(multi.max()) if not multi.empty else None


def daily_aggregate(se):
    """Super-ensemble infra-journalier → 1 ligne/jour (moyenne du jour, 12h locale)."""
    if se is None or se.empty:
        return se
    se = se.copy()
    se["date"] = pd.to_datetime(se["valid_time"]).dt.normalize()
    num = [c for c in se.columns if c not in ("valid_time", "date")]
    out = se.groupby("date")[num].mean().reset_index()
    out["valid_time"] = out["date"] + pd.Timedelta(hours=12)
    out[_PCT_COLS] = out[_PCT_COLS].round(2)
    return out


def daily_risk(sub, seuil, var):
    """Risque de dépassement/jour : pool des membres de la journée, proba de dépasser seuil.

    `exces` = dépassement attendu E[max(T − seuil, 0)] sur les membres poolés du
    jour — c'est exactement probabilité × sévérité moyenne des dépassements : un
    jour à proba modeste mais à queue très chaude y pèse autant qu'un jour à
    proba forte et dépassement léger (cf. KPI « Jours à risque »)."""
    piv = member_matrix(sub, var)
    if piv is None or piv.empty:
        return None
    dates = pd.to_datetime(piv.index).normalize()
    rows = []
    for date, grp in piv.groupby(dates):
        vals = grp.to_numpy().ravel()
        vals = vals[~np.isnan(vals)]
        if vals.size == 0:
            continue
        rows.append({
            "date": pd.Timestamp(date),
            "Médiane": float(np.median(vals)),
            "P75": float(np.quantile(vals, 0.75)),
            "P90": float(np.quantile(vals, 0.90)),
            "prob": float((vals >= seuil).mean()),
            "exces": float(np.maximum(vals - seuil, 0).mean()),
        })
    return pd.DataFrame(rows)
