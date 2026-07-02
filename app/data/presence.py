# -*- coding: utf-8 -*-
"""Contrôle de présence des modèles — diagnostic du pipeline (Open-Meteo vs legacy).

Objectif : pendant la phase de double run (Open-Meteo + Météociel), voir d'un
coup d'œil QUEL modèle est présent sur CHAQUE run, JUSQU'À QUELLE échéance, et
avec combien de membres — des DEUX côtés — pour repérer une incohérence (modèle
absent, run tronqué anormalement, cycles désalignés, horizon divergent). Vue
purement DIAGNOSTIQUE : elle n'écrit rien et ne pilote aucune persistance —
l'horizon n'est comparé au nominal (`horizon_h`) que comme repère informatif,
jamais comme une troncature (cf. invariants CLAUDE.md)."""

import glob
import os
import re

import numpy as np
import pandas as pd
import streamlit as st

import config as C
import validate_cross_pipeline as V  # helpers de lecture des xlsx legacy (Météociel)
from app.runtime import VAR
from app.data.db import load_db, _run_utc_naive

_CYCLES_BY_LABEL = {m["label"]: m["cycles"] for m in C.MODELS}
_LEGACY_FILE_RE = re.compile(r"Forecast-(\d{8})-(.+)\.xlsx$", re.IGNORECASE)


def openmeteo_presence(sig):
    """Une ligne par (run_date, modèle) présent dans le parquet Open-Meteo :
    nb de membres, première/dernière échéance RÉELLE (valeur non-NaN), horizon
    (lead, en heures) et cycle synoptique. `expected` = ce modèle publie-t-il à ce
    cycle (config `cycles`) — sert à distinguer une absence anormale d'un cycle où
    le modèle ne tourne simplement pas (ex. GEM à 6Z/18Z)."""
    df = load_db(sig)
    cols = ["run_date", "model", "n_members", "first_vt", "last_vt",
            "lead_h", "run_utc", "cycle_h", "expected"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    v = df.dropna(subset=[VAR])
    g = v.groupby(["run_date", "model"], as_index=False).agg(
        n_members=("member", "nunique"),
        first_vt=("valid_time", "min"),
        last_vt=("valid_time", "max"))
    # lead_h = horizon mesuré uniquement sur les échéances post-cycle (valid_time ≥ run_date).
    # Les données antérieures (rebouchage API depuis 00h local) ne reflètent pas l'horizon
    # réel du cycle. NaN si aucune échéance post-cycle valide → affiché comme ✗ absent.
    v_fut = v[v["valid_time"] >= v["run_date"]]
    g_fut = (v_fut.groupby(["run_date", "model"])["valid_time"]
             .max().rename("last_vt_fut").reset_index())
    g = g.merge(g_fut, on=["run_date", "model"], how="left")
    g["lead_h"] = (g["last_vt_fut"] - g["run_date"]).dt.total_seconds() / 3600
    g = g.drop(columns=["last_vt_fut"])
    g["run_utc"] = g["run_date"].map(_run_utc_naive)
    g["cycle_h"] = g["run_utc"].map(lambda t: t.hour)
    g["expected"] = g.apply(
        lambda r: r["cycle_h"] in _CYCLES_BY_LABEL.get(r["model"], []), axis=1)
    return g


def legacy_signature():
    """Signature (nom, mtime) des xlsx legacy — invalide le cache dès qu'un fichier
    change ou qu'un nouveau scrape apparaît."""
    out = []
    for f in sorted(glob.glob(os.path.join(C.LEGACY_FORECASTS_DIR, "Forecast-*.xlsx"))):
        try:
            out.append((os.path.basename(f), os.path.getmtime(f)))
        except OSError:
            continue
    return tuple(out)


@st.cache_data(show_spinner=False)
def legacy_presence(_sig):
    """Présence/horizon côté Météociel (legacy), une ligne par (fichier, modèle).

    run_date = celui déclaré par Météociel dans l'en-tête du xlsx (source fiable,
    pas la date du nom de fichier qui est la date de scrape) ; last_vt = dernière
    échéance réellement renseignée (≥ 1 membre non-NaN) ; n_members = membres
    d'ensemble effectivement remplis. Réutilise les helpers de
    validate_cross_pipeline pour ne pas dupliquer le parsing legacy."""
    cols = ["run_label", "scrape_date", "file", "model", "run_date",
            "n_members", "last_vt", "lead_h", "n_ech"]
    rows = []
    for fname, _ in _sig:
        m = _LEGACY_FILE_RE.search(fname)
        if not m:
            continue
        scrape_date = pd.to_datetime(m.group(1), format="%d%m%Y", errors="coerce")
        run_label = m.group(2)
        path = os.path.join(C.LEGACY_FORECASTS_DIR, fname)
        for label, sheet in C.LEGACY_MODELS.items():
            df, _det, member_cols = V._read_legacy_sheet(path, sheet)
            if df is None or not member_cols:
                continue
            run_date = V._parse_legacy_run_date(path, sheet)
            members = df[member_cols].apply(pd.to_numeric, errors="coerce")
            valid = df[members.notna().any(axis=1)]
            if valid.empty:
                continue
            last_vt = valid["valid_time"].max()
            lead_h = ((last_vt - run_date).total_seconds() / 3600
                      if run_date is not None else np.nan)
            rows.append({
                "run_label": run_label, "scrape_date": scrape_date, "file": fname,
                "model": label, "run_date": run_date,
                "n_members": int(members.notna().any(axis=0).sum()),
                "last_vt": last_vt, "lead_h": lead_h, "n_ech": len(valid)})
    return pd.DataFrame(rows, columns=cols)


def _missing_by_run(om):
    """{run_date -> set des modèles attendus à ce cycle mais ABSENTS du run}.

    Un modèle n'est « attendu » qu'à partir de sa PREMIÈRE apparition réelle dans la
    base (min run_utc), et seulement aux cycles où il publie (config `cycles`). Cela
    cale automatiquement l'attente sur le go-live de chaque modèle — ex. GEM n'est
    jamais signalé « absent » avant sa première collecte (30/06) — sans date en dur."""
    first_seen = om.groupby("model")["run_utc"].min().to_dict()
    # Un modèle avec lead_h NaN ou ≤ 0 (données uniquement pré-cycle, pur rebouchage)
    # est équivalent à une absence : ses données post-cycle seront prises ailleurs.
    _has_fut = om[om["lead_h"].fillna(0) > 0]
    present_by_rd = (_has_fut.groupby("run_date")["model"].agg(set)
                     if not _has_fut.empty else pd.Series(dtype=object))
    out = {}
    for rd in om["run_date"].unique():
        ru = _run_utc_naive(rd)
        pres = present_by_rd.get(rd, set())
        out[rd] = {m for m in C.MODEL_LABELS
                   if ru.hour in C.EXPECTED_CYCLES_BY_LABEL.get(m, [])
                   and m in first_seen and ru >= first_seen[m]
                   and m not in pres}
    return out


def _nearest_om_run(om, model, ref_utc, window_h=12):
    """Ligne de présence Open-Meteo du modèle dont le cycle UTC est le plus proche
    de `ref_utc` (run legacy), dans une fenêtre de ±window_h. None si aucun."""
    sub = om[om["model"] == model]
    if sub.empty:
        return None
    gap = sub["run_utc"].map(lambda t: abs((t - ref_utc).total_seconds()) / 3600)
    cand = sub[gap <= window_h]
    if cand.empty:
        return None
    return sub.loc[gap.idxmin()]
