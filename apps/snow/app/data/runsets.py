# -*- coding: utf-8 -*-
"""Politiques de sélection de runs du dashboard neige — mêmes principes que le
canicule (CLAUDE.md § Vues combinées), adaptés au schéma à colonne `kind` :

  • latest_complete_run_sub : pour CHAQUE modèle membres, son dernier run à
    HORIZON PLEIN — complétude mesurée EMPIRIQUEMENT sur la portée réelle du
    run stocké (max valid_time − run_date), jamais par une règle d'heure de
    cycle ; aucun run plein → repli sur le dernier non vide, signalé
    « horizon réduit » (vues combinées / Vue d'ensemble neige) ;
  • latest_run_sub : dernier run NON VIDE de chaque modèle, quel que soit son
    horizon (fraîcheur maximale, option « Dernier run » d'Explorer un run) ;
  • previous_runs_sub : run précédent PAR MODÈLE (colonnes Δ des tables
    d'export) — jamais un cycle global partagé ;
  • mean_runs : les N derniers runs mean d'une famille (flux _MEAN, rétention
    API longue) — support de la page Convergence et, à terme, du bilan de
    fiabilité par modèle en fin de saison.
"""

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from .db import list_runs, mean_db, members_db


@st.cache_resource(show_spinner=False, max_entries=1)
def _portees(sig):
    """Index (modèle, run_date) → portée réelle en heures, pour TOUS les runs
    membres en base. Table de quelques dizaines de lignes, calculée une fois.

    Portée réelle d'un run : max valid_time − run_date sur les lignes ayant AU
    MOINS une variable valide (how="all" — une variable secondaire à couverture
    moindre ne raccourcit pas la portée) ; un run sans aucune ligne valide n'y
    figure pas, ce qui vaut « portée indéfinie ».

    Passer par cet index plutôt que par des découpes successives du DataFrame
    est ce qui rend les politiques de pool peu coûteuses : chaque `df[df[…]]`
    intermédiaire recopiait une fraction de la base rien que pour en mesurer
    la portée, avant de la jeter."""
    df = members_db(sig)
    if df.empty:
        return pd.DataFrame(columns=["model", "run_date", "reach_h"])
    valides = df[SC.ENS_VAR_COLS].notna().any(axis=1)
    cols = df.loc[valides, ["model", "run_date", "valid_time"]]
    out = (cols.groupby(["model", "run_date"], as_index=False, observed=True)
               .agg(fin=("valid_time", "max")))
    out["reach_h"] = (out["fin"] - out["run_date"]) / pd.Timedelta(hours=1)
    return out.drop(columns="fin")


def _run_choisi(portees, label, require_full):
    """(run_date retenu, repli ?) pour un modèle : dernier run à horizon plein
    si require_full, sinon dernier run non vide. Aucun run plein → repli sur le
    dernier non vide, signalé. Aucun run du tout → (None, False)."""
    sub = portees[portees["model"] == label].sort_values("run_date",
                                                         ascending=False)
    if sub.empty:
        return None, False
    horizon = SC.HORIZON_BY_LABEL.get(label)
    if require_full and horizon is not None:
        pleins = sub[sub["reach_h"] >= horizon - SC.FULL_HORIZON_TOLERANCE_H]
        if not pleins.empty:
            return pleins.iloc[0]["run_date"], False
        return sub.iloc[0]["run_date"], True
    return sub.iloc[0]["run_date"], False


def _latest_by_policy(sig, labels, require_full):
    """Pool « dernier run par modèle » : dernier run à horizon plein si
    require_full (repli dernier non vide, signalé), sinon dernier non vide."""
    df = members_db(sig)
    portees = _portees(sig)
    choix, flags = {}, {}
    for label in labels:
        run_date, repli = _run_choisi(portees, label, require_full)
        if run_date is None:
            continue
        choix[label] = run_date
        if repli:
            flags[label] = "horizon réduit"
    if not choix:
        return df.iloc[0:0], flags
    # Une seule découpe, sur les seuls (modèle, run) retenus — les lignes
    # restent dans l'ordre des `labels`, comme lorsque chaque modèle était
    # extrait puis concaténé.
    parts = [df[(df["model"] == label) & (df["run_date"] == run_date)]
             for label, run_date in choix.items()]
    return pd.concat(parts, ignore_index=True), flags


def latest_complete_run_sub(sig):
    """Vues combinées : dernier run à horizon plein de chaque modèle membres.
    Renvoie (sub, flags) — flags[label]="horizon réduit" en cas de repli."""
    return _latest_by_policy(sig, SC.ENS_LABELS, require_full=True)


def latest_run_sub(sig):
    """Option « Dernier run » : dernier run non vide de chaque modèle membres,
    sans exigence d'horizon (fraîcheur maximale, même partielle — voulu)."""
    sub, _ = _latest_by_policy(sig, SC.ENS_LABELS, require_full=False)
    return sub


def previous_runs_sub(sig, sub):
    """Pour chaque modèle de `sub`, les lignes de son run STRICTEMENT
    antérieur (dernier run de CE modèle avant celui affiché) — support des
    colonnes Δ. Vide si aucun modèle n'a de run antérieur."""
    df = members_db(sig)
    portees = _portees(sig)
    parts = []
    for label in sub["model"].unique():
        current = sub.loc[sub["model"] == label, "run_date"].max()
        # Le run précédent se cherche dans l'index des runs, pas en découpant
        # la base : seul le run finalement retenu en est extrait.
        anterieurs = portees[(portees["model"] == label)
                             & (portees["run_date"] < current)]
        if anterieurs.empty:
            continue
        run_date = anterieurs["run_date"].max()
        parts.append(df[(df["model"] == label) & (df["run_date"] == run_date)])
    if not parts:
        return df.iloc[0:0]
    return pd.concat(parts, ignore_index=True)


def mean_runs(sig, base_label, n_runs, kind="mean"):
    """Les N derniers runs du flux _MEAN d'une famille (`base_label` ∈
    ENS_LABELS), du plus récent au plus ancien. C'est le support de la
    convergence : rétention API longue, une seule série par run (la moyenne
    d'ensemble), directement comparable de run en run."""
    label = SC.MEAN_LABEL_BY_BASE.get(base_label)
    df = mean_db(sig, kind)
    sub = df[df["model"] == label]
    if sub.empty:
        return sub
    keep = sorted(sub["run_date"].unique(), reverse=True)[:n_runs]
    return sub[sub["run_date"].isin(keep)]


def mean_runs_all(sig, n_runs):
    """Runs `_MEAN` comparables pour le consensus « Tous modèles ».

    Un cycle n'entre dans la sélection que si au moins deux familles y sont
    présentes. La page resserre ensuite sur l'intersection des modèles entre
    ces cycles : la composition reste stable et une arrivée/disparition de
    modèle ne peut pas simuler une révision du scénario.
    """
    df = mean_db(sig, "mean")
    if df.empty:
        return df
    by_run = df.groupby("run_date")["model"].nunique()
    eligible = sorted(by_run[by_run >= 2].index, reverse=True)[:n_runs]
    return df[df["run_date"].isin(eligible)]


def latest_refresh_status(runs, sig):
    """(instant du dernier run, complet ?, modèles manquants) pour le bloc
    fraîcheur de la sidebar — l'attendu se juge sur expected_cycles du cycle
    du dernier run (cycles ≠ expected_cycles, cf. canicule)."""
    if runs.empty:
        return None, True, []
    latest = runs.iloc[0]["run_date"]
    df = members_db(sig)
    present = set(df.loc[df["run_date"] == latest, "model"].unique())
    from .db import utc_cycle
    hour = utc_cycle(latest).hour
    expected = [label for label in SC.ENS_LABELS
                if hour in SC.EXPECTED_CYCLES_BY_LABEL.get(label, [])]
    missing = [m for m in expected if m not in present]
    return latest, not missing, missing
