# -*- coding: utf-8 -*-
"""Amorçage one-off du magasin de données externe (Phase 1a de la « sortie de
git », docs/DESIGN_sortie_git.md) : découpe le parquet git courant en partitions
mensuelles et les publie comme assets du release dédié `data-store`.

LECTURE SEULE de la donnée de prod : lit config.DB_PATH, n'écrit RIEN dedans
(seule action d'écriture = upload d'assets sur le release GitHub). Prouve avant
de rendre la main que `concat(assets ré-téléchargés) == parquet original`
(mêmes lignes) — sinon échec explicite, on ne se fie jamais à un amorçage non
vérifié.

Usage :
    python tools/seed_store.py --dry-run   # montre le découpage, n'uploade rien
    python tools/seed_store.py             # crée le release + uploade + vérifie

Backend GitHub par défaut (gh authentifié). --local <dir> vise un dossier
(tests) au lieu du release."""

import argparse
import os
import sys
import tempfile

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "apps", "canicule"))

import config as C  # noqa: E402
from core.store import (  # noqa: E402
    GitHubReleaseStore, LocalDirStore, concat_partitions, partition_name,
    same_rows, split_by_month,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="affiche le découpage sans rien uploader")
    ap.add_argument("--local", metavar="DIR",
                    help="amorce un magasin local (dossier) au lieu du release GitHub")
    args = ap.parse_args()

    if not os.path.exists(C.DB_PATH):
        sys.exit(f"❌ Parquet introuvable : {C.DB_PATH}")

    df = pd.read_parquet(C.DB_PATH)  # LECTURE SEULE
    parts = split_by_month(df, "run_date")
    print(f"Parquet : {len(df):,} lignes → {len(parts)} partition(s) mensuelle(s)")
    for key in sorted(parts):
        print(f"  {partition_name(C.STORE_PREFIX, key)} : {len(parts[key]):,} lignes")

    if args.dry_run:
        print("DRY-RUN — aucun upload.")
        return 0

    store = LocalDirStore(args.local) if args.local else \
        GitHubReleaseStore(repo=C.STORE_REPO, tag=C.STORE_TAG)

    # Upload de chaque partition (ensure_release() est appelé par upload()).
    with tempfile.TemporaryDirectory() as tmp:
        for key in sorted(parts):
            name = partition_name(C.STORE_PREFIX, key)
            local = os.path.join(tmp, name)
            parts[key].to_parquet(local, index=False)
            print(f"  ↑ upload {name}…")
            store.upload(name, local)

    # ------------------------------------------------- PREUVE avant de rendre la main
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for a in sorted(store.list(C.STORE_PREFIX), key=lambda x: x.name):
            d = os.path.join(tmp, a.name)
            store.download(a.name, d)
            frames.append(pd.read_parquet(d))
    rebuilt = concat_partitions(frames)
    if not same_rows(rebuilt, df):
        sys.exit("❌ VÉRIFICATION ÉCHOUÉE : concat(assets) ≠ parquet original. "
                 "Amorçage NON validé — ne pas se fier au store.")
    print(f"✅ Amorçage vérifié : {len(rebuilt):,} lignes identiques au parquet "
          f"({len(frames)} partition(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
