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
    GitHubReleaseStore, LocalDirStore, concat_partitions, parse_partition_name,
    partition_name, same_rows, split_by_month,
)


def _prefix(db_path):
    return os.path.splitext(os.path.basename(db_path))[0]


def seed_one(store, db_path, time_col, dry_run):
    """Amorce UN flux : découpe son parquet par mois de `time_col`, uploade les
    partitions, puis prouve concat(assets) == parquet. Renvoie True si OK/skip,
    False si vérification échouée."""
    prefix = _prefix(db_path)
    if not os.path.exists(db_path):
        print(f"• {prefix} : parquet absent, ignoré (flux pas encore amorcé).")
        return True
    df = pd.read_parquet(db_path)  # LECTURE SEULE
    parts = split_by_month(df, time_col)
    print(f"• {prefix} : {len(df):,} lignes → {len(parts)} partition(s) "
          f"(par {time_col})")
    for key in sorted(parts):
        print(f"    {partition_name(prefix, key)} : {len(parts[key]):,} lignes")
    if dry_run:
        return True

    with tempfile.TemporaryDirectory() as tmp:
        for key in sorted(parts):
            name = partition_name(prefix, key)
            local = os.path.join(tmp, name)
            parts[key].to_parquet(local, index=False)
            print(f"    ↑ upload {name}…")
            store.upload(name, local)

    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for a in sorted(store.list(prefix), key=lambda x: x.name):
            parsed = parse_partition_name(a.name)
            if not parsed or parsed[0] != prefix:  # préfixe EXACT
                continue
            d = os.path.join(tmp, a.name)
            store.download(a.name, d)
            frames.append(pd.read_parquet(d))
    rebuilt = concat_partitions(frames)
    if not same_rows(rebuilt, df):
        print(f"    ❌ VÉRIFICATION ÉCHOUÉE : concat(assets) ≠ parquet {prefix}.")
        return False
    print(f"    ✅ vérifié : {len(rebuilt):,} lignes identiques.")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="affiche le découpage sans rien uploader")
    ap.add_argument("--local", metavar="DIR",
                    help="amorce un magasin local (dossier) au lieu du release GitHub")
    args = ap.parse_args()

    store = LocalDirStore(args.local) if args.local else \
        GitHubReleaseStore(repo=C.STORE_REPO, tag=C.STORE_TAG)

    ok = True
    for db_path, time_col in C.STORE_FLUX:
        ok = seed_one(store, db_path, time_col, args.dry_run) and ok
    if not ok:
        sys.exit("❌ Amorçage NON validé sur au moins un flux — ne pas se fier au store.")
    print("DRY-RUN — aucun upload." if args.dry_run else "✅ Tous les flux amorcés et vérifiés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
