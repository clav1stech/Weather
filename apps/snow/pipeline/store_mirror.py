# -*- coding: utf-8 -*-
"""Écriture des flux neige dans le magasin externe (« sortie de git »).

Point d'entrée unique des pipelines neige : lie snow_config à la mécanique
générique core/store/mirror.py.

Contrairement au canicule (double écriture, git source de vérité), la neige
écrit DIRECTEMENT dans le magasin, qui en est la seule source de vérité : la
CI ne committe plus les parquets neige. Ceux restés dans git sont FIGÉS à la
date de la bascule et ne servent plus qu'au repli de lecture du dashboard
(cf. apps/snow/app/data/store.py) — jamais au pipeline.

D'où la règle critique de `load_existing` ci-dessous : en CI (clone neuf), le
parquet sur disque est la copie gelée ; repartir de lui tronquerait
l'historique à chaque run. L'existant se lit donc TOUJOURS dans le magasin dès
que celui-ci est actif.

Activé uniquement si WEATHER_STORE_WRITE est positionné (CI) ; absent →
pipelines strictement inchangés (lecture et écriture sur le parquet local)."""

import os
import tempfile

import pandas as pd

from apps.snow import snow_config as SC
from core.store import (
    GitHubReleaseStore, concat_partitions, mirror_to_store, parse_partition_name,
)


def store_write_active() -> bool:
    """True si le magasin est la cible d'écriture (et la source de l'existant)."""
    return os.environ.get("WEATHER_STORE_WRITE", "").strip().lower() \
        not in ("", "0", "false")


def _store():
    """Magasin d'ÉCRITURE via la CLI `gh` (les pipelines tournent en CI, où gh
    est disponible et authentifié)."""
    return GitHubReleaseStore(repo=SC.STORE_REPO, tag=SC.STORE_TAG)


def load_existing(db_path, schema, fallback):
    """Base existante d'un flux, SOURCE DE VÉRITÉ comprise : depuis le magasin
    s'il est actif, sinon via `fallback` (lecture du parquet local, comportement
    de dev inchangé).

    Un magasin injoignable lève : contrairement à la lecture du dashboard, un
    pipeline ne doit JAMAIS se rabattre sur le parquet gelé de git — il
    réécrirait ensuite le magasin à partir d'un historique tronqué."""
    if not store_write_active():
        return fallback(db_path, schema)
    prefix = os.path.splitext(os.path.basename(db_path))[0]
    store = _store()
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for a in sorted(store.list(prefix), key=lambda x: x.name):
            parsed = parse_partition_name(a.name)
            if not parsed or parsed[0] != prefix:  # préfixe EXACT
                continue
            dest = os.path.join(tmp, a.name)
            store.download(a.name, dest)
            frames.append(pd.read_parquet(dest))
    df = concat_partitions(frames)
    if df.empty:
        return pd.DataFrame(columns=schema)
    for col in schema:
        if col not in df.columns:
            df[col] = float("nan")
    return df[schema]


def mirror(existing, combined, db_path, time_col):
    """Écrit dans le magasin les partitions modifiées d'un flux neige (collecte :
    rien n'est jamais retiré). No-op si le magasin n'est pas actif."""
    if not store_write_active():
        return []
    return mirror_to_store(_store(), existing, combined, db_path, time_col)

