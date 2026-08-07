# -*- coding: utf-8 -*-
"""Amorçage du magasin de données externe pour les flux NEIGE
(docs/DESIGN_sortie_git.md) : découpe chaque parquet neige en partitions
mensuelles et les publie comme assets du release dédié `data-store`.

LECTURE SEULE de la donnée locale : n'écrit RIEN dans les parquets (seule
action d'écriture = upload d'assets). Prouve avant de rendre la main que
`concat(assets ré-téléchargés) == parquet original` (mêmes lignes) — sinon
échec explicite, on ne se fie jamais à un amorçage non vérifié.

Idempotent : rejouable pour resynchroniser le magasin sur l'état local (le
miroir incrémental des pipelines ne rattrape jamais un retard accumulé).

Usage :
    python apps/snow/pipeline/seed_store.py --dry-run   # découpage seul
    python apps/snow/pipeline/seed_store.py             # upload + vérification
"""

import argparse
import os
import sys
import tempfile

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "..")))

from apps.snow import snow_config as SC  # noqa: E402
from core.store import (  # noqa: E402
    GitHubReleaseStore, LocalDirStore, concat_partitions, parse_partition_name,
    partition_name, same_rows, split_by_month,
)


def seed_one(store, db_path, time_col, dry_run):
    """Amorce UN flux : découpe son parquet par mois de `time_col`, uploade les
    partitions, puis prouve concat(assets) == parquet. True si OK/ignoré."""
    prefix = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.exists(db_path):
        print(f"• {prefix} : parquet absent, ignoré (flux pas encore amorcé).")
        return True
    df = pd.read_parquet(db_path)  # LECTURE SEULE
    if df.empty:
        print(f"• {prefix} : parquet vide, ignoré.")
        return True
    parts = split_by_month(df, time_col)
    print(f"• {prefix} : {len(df):,} lignes → {len(parts)} partition(s) (par {time_col})")
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
            dest = os.path.join(tmp, a.name)
            store.download(a.name, dest)
            frames.append(pd.read_parquet(dest))
    if not same_rows(concat_partitions(frames), df):
        print(f"    ❌ VÉRIFICATION ÉCHOUÉE : concat(assets) ≠ parquet {prefix}.")
        return False
    print(f"    ✅ vérifié : {len(df):,} lignes identiques.")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="affiche le découpage sans rien uploader")
    ap.add_argument("--local", metavar="DIR",
                    help="amorce un magasin local (dossier) au lieu du release")
    args = ap.parse_args()

    store = LocalDirStore(args.local) if args.local else \
        GitHubReleaseStore(repo=SC.STORE_REPO, tag=SC.STORE_TAG)

    ok = True
    for db_path, time_col in SC.STORE_FLUX:
        ok = seed_one(store, db_path, time_col, args.dry_run) and ok
    if not ok:
        sys.exit("❌ Amorçage NON validé sur au moins un flux — ne pas se fier au store.")
    print("DRY-RUN — aucun upload." if args.dry_run
          else "✅ Tous les flux neige amorcés et vérifiés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
