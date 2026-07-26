# -*- coding: utf-8 -*-
"""Persistance générique des runs d'ensemble — fraîcheur, complétude,
anti-régression, fusion atomique. CONFIG-AGNOSTIQUE : schéma, colonnes de
variables, colonnes d'identité des séries, seuils et chemins arrivent tous en
paramètres.

Invariants portés ici (mêmes garanties que Forecast.py côté canicule,
cf. CLAUDE.md § Historique des runs / Détection des runs partiels) :
  • fraîcheur EMPIRIQUE, échéance par échéance (mask_stale_tail) : jamais de
    table d'horizons codée en dur — l'horizon réel d'un cycle varie ;
  • portée réelle CONTIGUË (contiguous_reach_h) : jamais un simple
    max(valid_time) − run_date, qui laisserait un point parasite isolé simuler
    une portée pleine (run fantôme persisté PUIS bloquant le vrai run via la
    garde anti-régression) ;
  • persistance conditionnée à la complétude (filter_fresh_rows) : un run
    frais trop court (calcul en cours, cycle nativement partiel, réponse
    creuse) est laissé de côté — l'ancien run complet reste en base ;
  • jamais de régression d'un (run_date, modèle) déjà stocké
    (drop_regressions, dernier rempart indépendant du filtre de fraîcheur) ;
  • fusion dédupliquée par couple (run_date, modèle) — jamais par run_date
    seul (chaque modèle a son propre cycle) — et écriture atomique
    (.tmp + os.replace via core.io.atomic).
"""

import os

import numpy as np
import pandas as pd

from core.io.atomic import atomic_write_parquet


# --------------------------------------------------------------------------- #
#  Portée réelle contiguë
# --------------------------------------------------------------------------- #
def contiguous_reach_h(run_date, valid_times, max_gap_h):
    """Portée réelle CONTIGUË (h) : dernière échéance valide atteignable depuis
    `run_date` sans trou > max_gap_h entre échéances valides successives
    (run_date compris comme point de départ). Les échéances antérieures au
    cycle (passé rebouché par l'API depuis 00:00 local) sont ignorées : elles
    ne disent rien de la fraîcheur du run."""
    hours = np.sort(((valid_times[valid_times >= run_date] - run_date)
                     / pd.Timedelta(hours=1)).unique())
    hours = np.concatenate(([0.0], hours))
    breaks = np.flatnonzero(np.diff(hours) > max_gap_h)
    return hours[breaks[0]] if breaks.size else hours[-1]


def _reach_h_by_key(df, var_cols, max_gap_h):
    """{(modèle, run_date) → portée réelle contiguë en heures} sur les lignes à
    valeur valide. how="all" : une ligne compte dès qu'UNE variable est valide
    — indispensable pour que les runs antérieurs à l'ajout d'une variable
    gardent leur vraie portée (sinon la garde anti-régression les croirait
    vides et laisserait un glitch API écraser un run complet)."""
    valid = df.dropna(subset=var_cols, how="all")
    if valid.empty:
        return {}
    return {key: contiguous_reach_h(key[1], g["valid_time"], max_gap_h)
            for key, g in valid.groupby(["model", "run_date"])}


# --------------------------------------------------------------------------- #
#  Fraîcheur — détection empirique, échéance par échéance
# --------------------------------------------------------------------------- #
def mask_stale_tail(model_label, candidate, existing, *, var_cols, id_cols, eps):
    """NaN-ifie, dans `candidate` (lignes d'UN modèle), les échéances dont les
    valeurs sont quasi identiques à celles du dernier run stocké pour ce
    modèle — signe que l'API ressert la queue de l'ancien cycle, pas une
    donnée recalculée. Comparaison échéance PAR échéance (l'horizon réel d'un
    cycle varie d'un jour à l'autre) : des séries indépendantes ne tombent
    jamais pile sur les mêmes valeurs par hasard, un écart moyen ≤ eps trahit
    fiablement une copie.

    `id_cols` : colonnes identifiant une série au sein d'un modèle (ex.
    ["member"] pour un flux membres, ["kind", "member", "site"] pour un flux
    mêlant membres/moyenne/spread sur plusieurs points).

    Renvoie (candidate masqué, a_du_neuf). a_du_neuf=False si AUCUNE échéance
    n'a changé (modèle pas encore renouvelé → à écarter entièrement)."""
    prior = existing[existing["model"] == model_label]
    if prior.empty:
        return candidate, True
    latest_run = prior["run_date"].max()
    prior_latest = prior[prior["run_date"] == latest_run]

    merged = candidate.merge(
        prior_latest[[*id_cols, "valid_time", *var_cols]],
        on=[*id_cols, "valid_time"], suffixes=("_new", "_old"), how="left")

    abs_diff = pd.Series(0.0, index=merged.index)
    n_comparable = pd.Series(0, index=merged.index)
    has_new_value = pd.Series(False, index=merged.index)
    for col in var_cols:
        new_v, old_v = merged[f"{col}_new"], merged[f"{col}_old"]
        both_valid = new_v.notna() & old_v.notna()
        abs_diff += (new_v - old_v).abs().where(both_valid, 0.0)
        n_comparable += both_valid.astype(int)
        has_new_value |= new_v.notna()

    per_step = pd.DataFrame({"valid_time": merged["valid_time"],
                             "abs_diff": abs_diff, "n": n_comparable,
                             "has_new": has_new_value})

    def _classify(g):
        # n=0 : rien de comparable — couverture inédite si le candidat a une
        # valeur (fraîche), sinon NaN des deux côtés (neutre, déjà NaN).
        if g["n"].sum() == 0:
            return "fresh" if g["has_new"].any() else "empty"
        return "fresh" if (g["abs_diff"].sum() / g["n"].sum()) > eps else "stale"

    step_class = per_step.groupby("valid_time").apply(_classify, include_groups=False)
    stale_steps = step_class[step_class == "stale"].index

    candidate = candidate.copy()
    candidate.loc[candidate["valid_time"].isin(stale_steps), var_cols] = np.nan
    a_du_neuf = bool((step_class == "fresh").any())
    return candidate, a_du_neuf


def _meets_persist_horizon(group, *, var_cols, horizon_h, horizon_tol_h,
                           min_horizon_h, max_gap_h):
    """Le run frais est-il assez avancé pour être persisté ? Trois causes de
    run court, même traitement (on ne l'écrit pas, l'ancien run complet reste
    en base) : calcul encore en cours au moment du poll (se résout au poll
    suivant), cycle nativement plus court par construction (ne sera simplement
    jamais persisté — voulu), réponse creuse de l'API (portée contiguë ≈ 0)."""
    valid = group.dropna(subset=var_cols, how="all")
    if valid.empty:
        return False
    run_date = pd.Timestamp(group["run_date"].iloc[0])
    reach_h = contiguous_reach_h(run_date, valid["valid_time"], max_gap_h)
    threshold = (horizon_h - horizon_tol_h) if horizon_h is not None else min_horizon_h
    return reach_h >= threshold


def filter_fresh_rows(fresh, existing, *, var_cols, id_cols, eps,
                      horizon_h_by_model, horizon_tol_h, min_horizon_h,
                      max_gap_h):
    """Applique mask_stale_tail à chaque modèle puis n'accepte à la
    persistance que les runs frais à portée suffisante. Renvoie
    (rows retenues, modèles inchangés, modèles à portée trop courte)."""
    kept, stale_labels, partial_labels = [], [], []
    for model_label, candidate in fresh.groupby("model"):
        masked, a_du_neuf = mask_stale_tail(
            model_label, candidate, existing,
            var_cols=var_cols, id_cols=id_cols, eps=eps)
        if not a_du_neuf:
            stale_labels.append(model_label)
        elif _meets_persist_horizon(
                masked, var_cols=var_cols,
                horizon_h=horizon_h_by_model.get(model_label),
                horizon_tol_h=horizon_tol_h, min_horizon_h=min_horizon_h,
                max_gap_h=max_gap_h):
            kept.append(masked)
        else:
            partial_labels.append(model_label)
    out = pd.concat(kept, ignore_index=True) if kept else fresh.iloc[0:0]
    return out, stale_labels, partial_labels


# --------------------------------------------------------------------------- #
#  Persistance
# --------------------------------------------------------------------------- #
def validate(df, schema, var_cols):
    """Jamais de run vide écrit (invariant absolu d'intégrité des données)."""
    if df is None or df.empty:
        raise ValueError("Run frais vide — rien à écrire.")
    missing = [c for c in schema if c not in df.columns]
    if missing:
        raise ValueError(f"Colonnes manquantes dans le run frais : {missing}")
    if not bool(df[var_cols].notna().any(axis=None)):
        raise ValueError("Aucune valeur valide dans le run frais.")


def drop_regressions(fresh, existing, *, var_cols, max_gap_h):
    """Dernier rempart avant écriture, indépendant de filter_fresh_rows :
    retire de `fresh` tout (run_date, modèle) qui RÉGRESSERAIT un run déjà
    persisté sous ce même couple (glitch API, réponse tronquée à un poll).
    Seuls les couples présents des DEUX côtés sont comparés : un run_date
    nouveau n'a rien à régresser."""
    overlap = existing[["model", "run_date"]].drop_duplicates().merge(
        fresh[["model", "run_date"]].drop_duplicates(), on=["model", "run_date"])
    if overlap.empty:
        return fresh

    existing_reach = _reach_h_by_key(existing, var_cols, max_gap_h)
    fresh_reach = _reach_h_by_key(fresh, var_cols, max_gap_h)
    regressed = [
        (row.model, row.run_date) for row in overlap.itertuples()
        if existing_reach.get((row.model, row.run_date), -1)
        > fresh_reach.get((row.model, row.run_date), -1)
    ]
    if not regressed:
        return fresh

    labels = ", ".join(f"{m} {rd:%d %b %HZ}" for m, rd in regressed)
    print(f"   🛡️  Run existant plus complet conservé (fresh régressif ignoré) : {labels}")
    idx = pd.MultiIndex.from_tuples(regressed, names=["model", "run_date"])
    keep = ~fresh.set_index(["model", "run_date"]).index.isin(idx)
    return fresh[keep].reset_index(drop=True)


def load_existing(db_path, schema):
    """Base existante réalignée sur le schéma courant (colonne ajoutée après
    coup → NaN) : l'historique reste lisible quelle que soit l'évolution du
    schéma."""
    if os.path.exists(db_path):
        df = pd.read_parquet(db_path)
        for col in schema:
            if col not in df.columns:
                df[col] = np.nan
        return df[schema]
    return pd.DataFrame(columns=schema)


def persist(fresh, db_path, *, schema, var_cols, sort_cols, max_gap_h,
            existing=None):
    """Fusion atomique : retire, pour chaque (run_date, modèle) présent dans
    `fresh`, les lignes déjà stockées sous ce même couple, puis append. Les
    modèles absents de `fresh` gardent leur run antérieur intact — jamais de
    perte d'historique. Écrit TOUJOURS sur `db_path` (pas de dry-run) :
    ne jamais l'appeler avec des données de test sans rediriger le chemin."""
    validate(fresh, schema, var_cols)

    if existing is None:
        existing = load_existing(db_path, schema)

    if existing.empty:
        combined = fresh.copy()
    else:
        fresh = drop_regressions(fresh, existing,
                                 var_cols=var_cols, max_gap_h=max_gap_h)
        if fresh.empty:
            return existing
        fresh_keys = fresh[["run_date", "model"]].drop_duplicates()
        merged = existing.merge(fresh_keys, on=["run_date", "model"],
                                how="left", indicator=True)
        dup_mask = (merged["_merge"] == "both").to_numpy()
        combined = pd.concat([existing[~dup_mask], fresh], ignore_index=True)

    combined = combined.sort_values(sort_cols).reset_index(drop=True)

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    atomic_write_parquet(combined[schema], db_path)
    return combined
