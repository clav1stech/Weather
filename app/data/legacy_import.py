# -*- coding: utf-8 -*-
"""Import ciblé legacy → parquet — comble une absence avérée, jamais d'écrasement.

Même principe que migrate.py (xlsx Météociel → schéma plat), mais restreint à
UN SEUL couple (run, modèle), choisi par l'utilisateur parmi les couples
présents en legacy et SANS AUCUNE donnée valide côté Open-Meteo. Les xlsx
restent en lecture seule (assurance-vie du projet) ; le parquet est sauvegardé
(copie datée) avant toute écriture ; la fusion passe par Forecast.persist
(validation, anti-régression, écriture atomique .tmp + os.replace).

Invariant CLAUDE.md : ne jamais assouplir ce module en outil de « remplacement »
de données Open-Meteo existantes."""

import os
import re
import shutil
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

import config as C
import Forecast as F  # persist() : fusion validée, anti-régression, écriture atomique
import validate_cross_pipeline as V  # helpers de lecture des xlsx legacy (Météociel)
from app.data.presence import legacy_presence


def _member_id_from_col(col_name):
    """'1', '12'… → entier ; None si la colonne n'est pas un membre numéroté.
    (Le contrôle DET/GFS est identifié à part via det_col → membre 0.)"""
    m = re.search(r"(\d+)", str(col_name).strip())
    return int(m.group(1)) if m else None


@st.cache_data(show_spinner=False)
def legacy_import_candidates(_db_sig, _legacy_sig):
    """Couples (run_date, modèle) présents dans les xlsx legacy mais sans la
    moindre valeur valide dans le parquet Open-Meteo — les seuls importables.

    Comparaison en UTC tz-naïf des deux côtés : parquet BRUT (F.load_existing),
    jamais load_db qui convertit run_date en heure de Paris. Si plusieurs xlsx
    couvrent le même run (re-scrapes), on garde le plus complet (last_vt max,
    puis scrape le plus récent)."""
    leg = legacy_presence(_legacy_sig)
    if leg.empty:
        return leg
    leg = leg.dropna(subset=["run_date"])
    leg = leg[leg["model"].isin(C.MODEL_LABELS)]
    if leg.empty:
        return leg

    db = F.load_existing()
    if not db.empty:
        have = db.dropna(subset=C.VAR_COLS, how="all")[["run_date", "model"]] \
                 .drop_duplicates()
        keys_have = {(pd.Timestamp(r.run_date), r.model)
                     for r in have.itertuples(index=False)}
        mask = leg.apply(
            lambda r: (pd.Timestamp(r["run_date"]), r["model"]) not in keys_have,
            axis=1)
        leg = leg[mask]

    if leg.empty:
        return leg
    leg = (leg.sort_values(["last_vt", "scrape_date"])
              .drop_duplicates(subset=["run_date", "model"], keep="last"))
    return leg.sort_values(["run_date", "model"],
                           ascending=[False, True]).reset_index(drop=True)


def import_legacy_run(fname, model_label, expected_run_date):
    """Importe UN run d'UN modèle depuis un xlsx legacy vers le parquet.
    Retourne (ok, message). Garde-fous, dans l'ordre :
      1. relecture du xlsx au moment de l'import (lecture seule) et re-parse du
         run_date d'en-tête : s'il diffère du couple sélectionné (fichier changé
         entre-temps), abandon ;
      2. contrôle d'absence AU MOMENT de l'écriture (le dropdown peut être
         périmé) : la moindre valeur valide déjà en base pour ce couple → refus ;
      3. sauvegarde datée du parquet AVANT toute écriture ;
      4. fusion via Forecast.persist (jamais d'écriture directe)."""
    path = os.path.join(C.LEGACY_FORECASTS_DIR, fname)
    sheet = C.LEGACY_MODELS.get(model_label)
    if sheet is None:
        return False, f"Modèle {model_label} sans feuille legacy déclarée (config)."

    df, det_col, member_cols = V._read_legacy_sheet(path, sheet)
    if df is None or (det_col is None and not member_cols):
        return False, f"Feuille « {sheet} » illisible dans {fname}."
    run_date = V._parse_legacy_run_date(path, sheet)
    if run_date is None or pd.Timestamp(run_date) != pd.Timestamp(expected_run_date):
        return False, ("Le run_date lu dans le xlsx ne correspond plus à la "
                       "sélection (fichier modifié entre-temps ?) — import abandonné.")

    # xlsx large → schéma plat. Le legacy Météociel ne publie que la T850 :
    # les autres variables éventuelles du schéma restent NaN.
    frames = []
    if det_col is not None:
        d = df[["valid_time", det_col]].rename(columns={det_col: "t850"})
        d["member"] = 0
        frames.append(d)
    for col in member_cols:
        mid = _member_id_from_col(col)
        if mid is None:
            continue
        d = df[["valid_time", col]].rename(columns={col: "t850"})
        d["member"] = mid
        frames.append(d)
    if not frames:
        return False, "Aucune colonne membre exploitable dans la feuille."

    tidy = pd.concat(frames, ignore_index=True)
    tidy["t850"] = pd.to_numeric(tidy["t850"], errors="coerce")
    tidy = tidy.dropna(subset=["t850"])
    if tidy.empty:
        return False, "Aucune valeur valide dans le xlsx — rien à importer."
    tidy["run_date"] = pd.Timestamp(run_date)  # UTC tz-naïf, comme le parquet
    tidy["model"] = model_label
    tidy["member"] = tidy["member"].astype(int)
    for c in C.VAR_COLS:
        if c not in tidy.columns:
            tidy[c] = np.nan
    tidy = tidy.drop_duplicates(subset=["run_date", "model", "member", "valid_time"])
    tidy = tidy[C.SCHEMA]

    existing = F.load_existing()
    if not existing.empty:
        pair = existing[(existing["run_date"] == tidy["run_date"].iloc[0])
                        & (existing["model"] == model_label)]
        if not pair.dropna(subset=C.VAR_COLS, how="all").empty:
            return False, ("Ce couple (run, modèle) contient déjà des données "
                           "valides dans le parquet — import refusé (le module "
                           "ne comble que des absences, jamais d'écrasement).")

    backup = None
    if os.path.exists(C.DB_PATH):
        # Copies datées dans data/backups/ (gitignoré) : locales uniquement,
        # jamais ramassées par le `git add data/*.parquet` du workflow.
        backup_dir = os.path.join(C.DATA_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup = os.path.join(
            backup_dir,
            os.path.basename(C.DB_PATH).replace(
                ".parquet", f"_backup_{datetime.now():%Y%m%d_%H%M%S}.parquet"))
        shutil.copy2(C.DB_PATH, backup)

    combined = F.persist(tidy, existing)
    msg = (f"{len(tidy):,} lignes importées — {model_label}, run "
           f"{run_date:%d/%m/%Y} {run_date.hour}Z, {tidy['member'].nunique()} "
           f"membres, dernière échéance {tidy['valid_time'].max():%d/%m %Hh} UTC. "
           f"Base : {len(combined):,} lignes.")
    if backup:
        msg += f" Sauvegarde préalable : {os.path.basename(backup)}."
    return True, msg
