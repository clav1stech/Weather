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

from apps.snow import snow_config as SC
from .db import list_runs, mean_db, members_db


def _reach_h(group):
    """Portée réelle (h) d'un run stocké : max valid_time − run_date sur les
    lignes ayant AU MOINS une variable valide (how="all" — une variable
    secondaire à couverture moindre ne raccourcit pas la portée)."""
    valid = group.dropna(subset=SC.ENS_VAR_COLS, how="all")
    if valid.empty:
        return None
    run_date = pd.Timestamp(group["run_date"].iloc[0])
    return (valid["valid_time"].max() - run_date) / pd.Timedelta(hours=1)


def _latest_by_policy(df, labels, require_full):
    """Pool « dernier run par modèle » : dernier run à horizon plein si
    require_full (repli dernier non vide, signalé), sinon dernier non vide."""
    parts, flags = [], {}
    for label in labels:
        sub = df[df["model"] == label]
        if sub.empty:
            continue
        chosen = None
        horizon = SC.HORIZON_BY_LABEL.get(label)
        for run_date in sorted(sub["run_date"].unique(), reverse=True):
            g = sub[sub["run_date"] == run_date]
            reach = _reach_h(g)
            if reach is None:
                continue
            if not require_full or horizon is None \
                    or reach >= horizon - SC.FULL_HORIZON_TOLERANCE_H:
                chosen = g
                break
        if chosen is None:
            # Aucun run plein : repli sur le dernier non vide, signalé.
            for run_date in sorted(sub["run_date"].unique(), reverse=True):
                g = sub[sub["run_date"] == run_date]
                if _reach_h(g) is not None:
                    chosen, flags[label] = g, "horizon réduit"
                    break
        if chosen is not None:
            parts.append(chosen)
    if not parts:
        return df.iloc[0:0], flags
    return pd.concat(parts, ignore_index=True), flags


def latest_complete_run_sub(_sig):
    """Vues combinées : dernier run à horizon plein de chaque modèle membres.
    Renvoie (sub, flags) — flags[label]="horizon réduit" en cas de repli."""
    return _latest_by_policy(members_db(_sig), SC.ENS_LABELS, require_full=True)


def latest_run_sub(_sig):
    """Option « Dernier run » : dernier run non vide de chaque modèle membres,
    sans exigence d'horizon (fraîcheur maximale, même partielle — voulu)."""
    sub, _ = _latest_by_policy(members_db(_sig), SC.ENS_LABELS, require_full=False)
    return sub


def previous_runs_sub(_sig, sub):
    """Pour chaque modèle de `sub`, les lignes de son run STRICTEMENT
    antérieur (dernier run de CE modèle avant celui affiché) — support des
    colonnes Δ. Vide si aucun modèle n'a de run antérieur."""
    df = members_db(_sig)
    parts = []
    for label in sub["model"].unique():
        current = sub.loc[sub["model"] == label, "run_date"].max()
        prior = df[(df["model"] == label) & (df["run_date"] < current)]
        if prior.empty:
            continue
        parts.append(prior[prior["run_date"] == prior["run_date"].max()])
    if not parts:
        return df.iloc[0:0]
    return pd.concat(parts, ignore_index=True)


def mean_runs(_sig, base_label, n_runs, kind="mean"):
    """Les N derniers runs du flux _MEAN d'une famille (`base_label` ∈
    ENS_LABELS), du plus récent au plus ancien. C'est le support de la
    convergence : rétention API longue, une seule série par run (la moyenne
    d'ensemble), directement comparable de run en run."""
    label = SC.MEAN_LABEL_BY_BASE.get(base_label)
    df = mean_db(_sig, kind)
    sub = df[df["model"] == label]
    if sub.empty:
        return sub
    keep = sorted(sub["run_date"].unique(), reverse=True)[:n_runs]
    return sub[sub["run_date"].isin(keep)]


def mean_runs_all(_sig, n_runs):
    """Runs `_MEAN` comparables pour le consensus « Tous modèles ».

    Un cycle n'entre dans la sélection que si au moins deux familles y sont
    présentes. La page resserre ensuite sur l'intersection des modèles entre
    ces cycles : la composition reste stable et une arrivée/disparition de
    modèle ne peut pas simuler une révision du scénario.
    """
    df = mean_db(_sig, "mean")
    if df.empty:
        return df
    by_run = df.groupby("run_date")["model"].nunique()
    eligible = sorted(by_run[by_run >= 2].index, reverse=True)[:n_runs]
    return df[df["run_date"].isin(eligible)]


def latest_refresh_status(runs, _sig):
    """(instant du dernier run, complet ?, modèles manquants) pour le bloc
    fraîcheur de la sidebar — l'attendu se juge sur expected_cycles du cycle
    du dernier run (cycles ≠ expected_cycles, cf. canicule)."""
    if runs.empty:
        return None, True, []
    latest = runs.iloc[0]["run_date"]
    df = members_db(_sig)
    present = set(df.loc[df["run_date"] == latest, "model"].unique())
    from .db import utc_cycle
    hour = utc_cycle(latest).hour
    expected = [label for label in SC.ENS_LABELS
                if hour in SC.EXPECTED_CYCLES_BY_LABEL.get(label, [])]
    missing = [m for m in expected if m not in present]
    return latest, not missing, missing
