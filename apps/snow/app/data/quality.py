# -*- coding: utf-8 -*-
"""Diagnostic pur de fraîcheur/complétude des runs neige.

La mesure ne réimplémente pas le pipeline : elle appelle directement
``contiguous_reach_h`` et ``mask_stale_tail`` de core, avec les mêmes colonnes
et seuils que la collecte. Elle ne fait qu'observer le parquet chargé.
"""

import pandas as pd

from apps.snow import snow_config as SC
from core.pipeline.ensemble_runs import contiguous_reach_h, mask_stale_tail
from .db import utc_cycle

ID_COLS = ["kind", "member", "site"]


def _utc_naive(local_run_date):
    """Cycle d'affichage Paris → UTC naïf, format canonique du pipeline."""
    return utc_cycle(local_run_date).tz_localize(None)


def _model_specs():
    return SC.ENS_MODELS + SC.MEAN_MODELS


def _stream_name(label):
    return "mean/spread" if label in SC.MEAN_LABELS else "membres"


def _run_reach(group):
    """Portée contiguë avec la fonction canonique du pipeline."""
    valid = group.dropna(subset=SC.ENS_VAR_COLS, how="all")
    if valid.empty:
        return 0.0
    run_date = pd.Timestamp(group["run_date"].iloc[0])
    return float(contiguous_reach_h(
        run_date, valid["valid_time"], SC.PERSIST_MAX_GAP_H))


def _freshness_status(model_df, latest_run):
    """Fraîcheur empirique du dernier run vs le précédent du même flux."""
    dates = sorted(model_df["run_date"].unique(), reverse=True)
    if len(dates) < 2:
        return "premier run"
    candidate = model_df[model_df["run_date"] == latest_run]
    prior = model_df[model_df["run_date"] == dates[1]]
    _, has_fresh = mask_stale_tail(
        str(candidate["model"].iloc[0]), candidate, prior,
        var_cols=SC.ENS_VAR_COLS, id_cols=ID_COLS, eps=SC.FRESHNESS_EPS)
    return "renouvelé" if has_fresh else "identique au précédent"


def expected_cycles(now_utc=None, lookback_days=None):
    """Cycles dont la publication devrait être achevée à ``now_utc``.

    ``expected_cycles`` est utilisé, jamais ``cycles`` : un cycle possible
    mais nativement court (ECMWF 6Z/18Z) n'est pas une absence anormale.
    """
    now = (pd.Timestamp.now(tz="UTC").tz_localize(None) if now_utc is None
           else pd.Timestamp(now_utc).tz_localize(None)
           if pd.Timestamp(now_utc).tzinfo else pd.Timestamp(now_utc))
    days = SC.RUN_QUALITY_LOOKBACK_DAYS if lookback_days is None else lookback_days
    cutoff = now - pd.Timedelta(hours=SC.PUBLICATION_LAG_HOURS)
    start = (cutoff - pd.Timedelta(days=days)).normalize()
    rows = []
    for spec in _model_specs():
        day = start
        while day <= cutoff.normalize():
            for hour in spec.get("expected_cycles", spec["cycles"]):
                cycle = day + pd.Timedelta(hours=hour)
                if start <= cycle <= cutoff:
                    rows.append({"model": spec["label"], "cycle_utc": cycle})
            day += pd.Timedelta(days=1)
    return pd.DataFrame(rows, columns=["model", "cycle_utc"])


def _frames(sources):
    """`sources` = un DataFrame ou plusieurs (un par flux) → liste non vide de
    DataFrames non vides. Accepter plusieurs flux évite au seul appelant réel
    d'en construire d'abord le recollement : les membres pèsent des centaines
    de Mo, et chaque modèle n'est de toute façon lu que dans le flux qui le
    porte."""
    if sources is None:
        return []
    if isinstance(sources, pd.DataFrame):
        sources = [sources]
    return [f for f in sources if f is not None and not f.empty]


def _model_rows(frames, label):
    """Lignes d'un modèle, cherchées dans chaque flux fourni. Un modèle
    n'existe que dans un seul d'entre eux (membres OU mean/spread), mais rien
    n'en dépend : le recollement est fait par modèle, jamais sur toute la base."""
    parts = [f[f["model"] == label] for f in frames]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return frames[0].iloc[0:0]
    return parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)


def quality_report(sources, now_utc=None, lookback_days=None):
    """Renvoie ``(synthèse modèles, historique runs, anomalies cycles)``.

    ``sources`` est un DataFrame au schéma ensemble, ou plusieurs (un par
    flux)."""
    summary_cols = ["Modèle", "Flux", "Dernier run UTC", "Âge (h)",
                    "Portée (h)", "Complétude", "Fraîcheur empirique",
                    "Dernier cycle attendu UTC", "Publication"]
    history_cols = ["model", "stream", "run_date", "run_utc", "reach_h",
                    "horizon_h", "complete"]
    anomaly_cols = ["Modèle", "Flux", "Cycle UTC", "Type", "Détail"]
    frames = _frames(sources)
    if not frames:
        return (pd.DataFrame(columns=summary_cols),
                pd.DataFrame(columns=history_cols),
                pd.DataFrame(columns=anomaly_cols))

    now = (pd.Timestamp.now(tz="UTC").tz_localize(None) if now_utc is None
           else pd.Timestamp(now_utc).tz_convert("UTC").tz_localize(None)
           if pd.Timestamp(now_utc).tzinfo else pd.Timestamp(now_utc))
    expected = expected_cycles(now, lookback_days)
    summaries, histories, anomalies = [], [], []

    for spec in _model_specs():
        label = spec["label"]
        stream = _stream_name(label)
        model_df = _model_rows(frames, label)
        exp = expected[expected["model"] == label]
        latest_expected = exp["cycle_utc"].max() if not exp.empty else pd.NaT

        if model_df.empty:
            summaries.append({
                "Modèle": label, "Flux": stream, "Dernier run UTC": pd.NaT,
                "Âge (h)": None, "Portée (h)": 0.0, "Complétude": "absent",
                "Fraîcheur empirique": "n/d",
                "Dernier cycle attendu UTC": latest_expected,
                "Publication": "en retard" if pd.notna(latest_expected) else "n/d",
            })
            for cycle in exp["cycle_utc"]:
                anomalies.append({"Modèle": label, "Flux": stream,
                                  "Cycle UTC": cycle, "Type": "cycle manquant",
                                  "Détail": "aucun run stocké pour ce flux"})
            continue

        actual_by_local = {rd: _utc_naive(rd) for rd in model_df["run_date"].unique()}
        actual_utc = set(actual_by_local.values())
        first_utc = min(actual_utc)
        for cycle in exp.loc[exp["cycle_utc"] >= first_utc, "cycle_utc"]:
            if cycle not in actual_utc:
                anomalies.append({"Modèle": label, "Flux": stream,
                                  "Cycle UTC": cycle, "Type": "cycle manquant",
                                  "Détail": "attendu selon expected_cycles"})

        allowed = set(spec["cycles"])
        for cycle in sorted(actual_utc):
            if cycle.hour not in allowed:
                anomalies.append({"Modèle": label, "Flux": stream,
                                  "Cycle UTC": cycle, "Type": "cycle inattendu",
                                  "Détail": f"heure {cycle.hour:02d}Z hors cycles déclarés"})

        for run_date, group in model_df.groupby("run_date"):
            reach = _run_reach(group)
            horizon = spec.get("horizon_h")
            complete = horizon is None or reach >= horizon - SC.PERSIST_HORIZON_TOLERANCE_H
            histories.append({
                "model": label, "stream": stream, "run_date": run_date,
                "run_utc": actual_by_local[run_date], "reach_h": reach,
                "horizon_h": horizon, "complete": complete,
            })

        latest_local = max(actual_by_local, key=actual_by_local.get)
        latest_utc = actual_by_local[latest_local]
        latest_hist = next(r for r in histories
                           if r["model"] == label and r["run_date"] == latest_local)
        publication = ("à jour" if pd.isna(latest_expected)
                       or latest_utc >= latest_expected else "en retard")
        summaries.append({
            "Modèle": label, "Flux": stream, "Dernier run UTC": latest_utc,
            "Âge (h)": round(float((now - latest_utc) / pd.Timedelta(hours=1)), 1),
            "Portée (h)": round(latest_hist["reach_h"], 1),
            "Complétude": "complet" if latest_hist["complete"] else "partiel",
            "Fraîcheur empirique": _freshness_status(model_df, latest_local),
            "Dernier cycle attendu UTC": latest_expected,
            "Publication": publication,
        })

    summary = pd.DataFrame(summaries, columns=summary_cols)
    history = pd.DataFrame(histories, columns=history_cols)
    if not history.empty:
        history = history.sort_values(["run_utc", "model"], ascending=[False, True])
    anomaly = pd.DataFrame(anomalies, columns=anomaly_cols)
    if not anomaly.empty:
        anomaly = anomaly.sort_values("Cycle UTC", ascending=False)
    return summary, history, anomaly
