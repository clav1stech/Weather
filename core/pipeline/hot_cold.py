# -*- coding: utf-8 -*-
"""Archivage HOT/COLD générique d'un parquet à croissance illimitée — mise en
œuvre du design docs/DESIGN_archivage_pipeline.md (§3), mutualisée dans core/
(config-agnostique : chemins, colonne temporelle et rétention en paramètres).

Principe : le parquet HOT (celui que le pipeline écrit et que le dashboard
charge) ne garde qu'une fenêtre glissante récente ; un job de « rollover »
périodique bascule les lignes plus anciennes vers un parquet COLD append-only.

Invariant ABSOLU (CLAUDE.md — intégrité des données) : aucune opération ne
supprime ni ne dégrade une donnée existante. Garanties d'implémentation :
  • sauvegarde datée des DEUX fichiers avant toute écriture (suffixe
    `.YYYYmmddTHHMMSS.bak` — jamais `.parquet`, pour ne pas matcher les globs
    `git add *.parquet` des jobs CI) ;
  • vérification STRICTE avant écriture : l'union (hot ∪ cold) après rollover
    doit contenir EXACTEMENT les mêmes lignes (mêmes valeurs) que l'union
    avant, et le cold d'origine doit se retrouver intact en tête du nouveau
    cold — sinon abandon (SystemExit), fichiers laissés tels quels ;
  • écritures atomiques (.tmp + os.replace), COLD écrit AVANT le HOT : un
    crash entre les deux laisse au pire des lignes présentes des deux côtés
    (aucune perte) — la dédup pleine ligne du prochain rollover et des
    lecteurs résorbe le recouvrement ;
  • idempotent : rien de plus ancien que la fenêtre → aucun fichier touché ;
  • `dry_run=True` : calcule le rapport complet sans JAMAIS rien écrire
    (ni sauvegarde, ni parquet) — c'est le mode d'analyse préalable.

Le COLD n'est jamais réécrit au sens contenu : chaque rollover ne fait que
lui APPENDRE des lignes (le fichier est certes récrit physiquement — un
parquet ne s'étend pas en place — mais son contenu antérieur est vérifié
intact avant remplacement)."""

import datetime as dt
import os
import shutil

import pandas as pd

from core.io.atomic import atomic_write_parquet


def _read(path, columns=None):
    """Parquet → DataFrame, réaligné sur `columns` si fourni (colonne absente
    → NA, même principe que les load_existing des pipelines). Fichier absent
    → DataFrame vide (au schéma demandé le cas échéant)."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=columns if columns is not None else [])
    df = pd.read_parquet(path)
    if columns is not None:
        for col in columns:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[list(columns)]
    return df


def _canonical(df):
    """Forme canonique pour comparaison : dédup pleine ligne (résorbe un
    recouvrement hot/cold laissé par un crash antérieur), tri sur toutes les
    colonnes, index remis à zéro. Deux DataFrames au même contenu logique ont
    la même forme canonique (`.equals` traite NaN == NaN)."""
    cols = list(df.columns)
    return (df.drop_duplicates()
              .sort_values(cols, na_position="last")
              .reset_index(drop=True))


def split_hot_cold(df, time_col, cutoff):
    """Découpe pure : (lignes à garder en HOT, lignes à basculer en COLD).
    La frontière est STRICTE : bascule si `time_col < cutoff` — une ligne à
    l'instant exact du cutoff reste en hot."""
    old = pd.to_datetime(df[time_col]) < pd.Timestamp(cutoff)
    return (df[~old].reset_index(drop=True), df[old].reset_index(drop=True))


def _backup(path, stamp):
    """Copie datée `<path>.<stamp>.bak` à côté du fichier — extension .bak
    volontaire (jamais committée par les globs *.parquet des jobs CI)."""
    dest = f"{path}.{stamp}.bak"
    shutil.copy2(path, dest)
    return dest


def rollover(hot_path, cold_path, time_col, retention_days,
             now=None, dry_run=False):
    """Bascule vers `cold_path` les lignes de `hot_path` plus anciennes que
    `now − retention_days`. Renvoie un rapport dict (comptes avant/après,
    cutoff, sauvegardes créées, written). En cas d'incohérence détectée à la
    vérification : SystemExit AVANT toute écriture, fichiers intacts."""
    now = pd.Timestamp.now() if now is None else pd.Timestamp(now)
    cutoff = now - pd.Timedelta(days=retention_days)
    report = {"hot_path": hot_path, "cold_path": cold_path, "cutoff": cutoff,
              "dry_run": dry_run, "written": False, "backups": [],
              "moved": 0, "hot_before": 0, "hot_after": 0,
              "cold_before": 0, "cold_after": 0}

    if not os.path.exists(hot_path):
        return report  # rien à faire — pas une erreur (flux pas encore amorcé)

    hot = pd.read_parquet(hot_path)
    cold = _read(cold_path, columns=hot.columns)
    report["hot_before"], report["cold_before"] = len(hot), len(cold)

    new_hot, moved = split_hot_cold(hot, time_col, cutoff)
    report["moved"] = len(moved)
    report["hot_after"] = len(new_hot)
    if moved.empty:
        report["cold_after"] = len(cold)
        return report  # fenêtre déjà respectée — aucun fichier touché

    # Append-only : le cold d'origine reste en tête, les lignes basculées
    # s'ajoutent derrière. Dédup pleine ligne défensive (recouvrement laissé
    # par un crash entre les deux écritures d'un rollover antérieur).
    new_cold = pd.concat([cold, moved], ignore_index=True)
    new_cold = new_cold.drop_duplicates().reset_index(drop=True)
    report["cold_after"] = len(new_cold)

    # ------------------------------------------------- Vérifications bloquantes
    # 1) Aucune ligne perdue ni inventée : union avant == union après.
    before = _canonical(pd.concat([hot, cold], ignore_index=True))
    after = _canonical(pd.concat([new_hot, new_cold], ignore_index=True))
    if not before.equals(after):
        raise SystemExit("❌ Rollover ABANDONNÉ : l'union hot ∪ cold après "
                         "bascule ne reproduit pas exactement l'union avant "
                         "(ligne perdue, altérée ou inventée). Fichiers intacts.")
    # 2) Le cold d'origine est intact en tête du nouveau cold (append pur).
    if not cold.empty and not new_cold.iloc[:len(cold)].reset_index(drop=True).equals(
            cold.reset_index(drop=True)):
        raise SystemExit("❌ Rollover ABANDONNÉ : le contenu antérieur du cold "
                         "ne se retrouve pas intact en tête du nouveau cold. "
                         "Fichiers intacts.")

    if dry_run:
        return report

    # ------------------------------------------- Sauvegardes datées puis écriture
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    report["backups"].append(_backup(hot_path, stamp))
    if os.path.exists(cold_path):
        report["backups"].append(_backup(cold_path, stamp))
    # COLD d'abord (cf. docstring module : un crash entre les deux ne peut que
    # laisser un recouvrement, jamais une perte).
    atomic_write_parquet(new_cold, cold_path)
    atomic_write_parquet(new_hot, hot_path)
    report["written"] = True
    return report


def format_report(report, label=""):
    """Rapport lisible pour les logs CI/console."""
    tag = f"[{label}] " if label else ""
    if report["moved"] == 0:
        return (f"{tag}rien à basculer (hot {report['hot_before']} lignes, "
                f"cutoff {report['cutoff']:%Y-%m-%d %H:%M})")
    action = "DRY-RUN — aucune écriture" if report["dry_run"] else \
             ("écrit" if report["written"] else "NON écrit")
    return (f"{tag}{report['moved']} ligne(s) basculée(s) avant "
            f"{report['cutoff']:%Y-%m-%d %H:%M} · hot {report['hot_before']} → "
            f"{report['hot_after']} · cold {report['cold_before']} → "
            f"{report['cold_after']} · {action}")
