# -*- coding: utf-8 -*-
"""Adaptateur canicule du magasin de données externe (« sortie de git ») —
lie config.py à core/store/ (règle CLAUDE.md : core/ reste config-agnostique,
c'est l'adaptateur app/ qui apporte les réglages).

INACTIF par défaut : tant que la variable d'environnement WEATHER_STORE n'est
pas positionnée, le dashboard lit le parquet git comme avant (app/data/db.py
ne bascule sur le store que si store_active()). Backend choisi par
WEATHER_STORE_BACKEND (github par défaut, local pour tests/dev)."""

import os
import re

import pandas as pd

import config as C
from core.store import (
    GitHubReleaseStore,
    LocalDirStore,
    concat_partitions,
    parse_partition_name,
)


def store_active() -> bool:
    """True si le dashboard doit lire depuis le magasin externe plutôt que le
    parquet git. Toute valeur non vide/non « 0 »/« false » active."""
    return os.environ.get("WEATHER_STORE", "").strip().lower() not in ("", "0", "false")


def get_store():
    """Construit le magasin selon WEATHER_STORE_BACKEND. « local » (tests/dev)
    lit un dossier (WEATHER_STORE_DIR) ; « github » (prod) lit le release dédié.
    Le reste du code ne voit qu'une interface DataStore, jamais le backend."""
    backend = os.environ.get("WEATHER_STORE_BACKEND", "github").strip().lower()
    if backend == "local":
        return LocalDirStore(os.environ["WEATHER_STORE_DIR"])
    return GitHubReleaseStore(repo=C.STORE_REPO, tag=C.STORE_TAG)


def _partition_assets(store):
    """Assets du flux principal uniquement : préfixe EXACT STORE_PREFIX (un
    simple startswith attraperait database_paris_observations_… — d'où le parse
    et l'égalité stricte du préfixe). Triés par nom (mois croissant)."""
    out = []
    for a in store.list(C.STORE_PREFIX):
        parsed = parse_partition_name(a.name)
        if parsed and parsed[0] == C.STORE_PREFIX:
            out.append(a)
    return sorted(out, key=lambda a: a.name)


def store_signature(store=None):
    """Signature de cache = ((nom, etag)…) de toutes les partitions du flux.
    Change dès qu'une partition change (mois courant re-uploadé) → invalide le
    cache Streamlit au bon moment. Aucune partition → None (magasin pas encore
    amorcé, même sémantique que db_signature git)."""
    store = store or get_store()
    assets = _partition_assets(store)
    return tuple((a.name, a.etag) for a in assets) or None


def _cache_path(asset):
    """Chemin de cache local d'une partition, clé par etag : un mois clos
    (etag stable) n'est téléchargé qu'une fois ; le mois courant (etag qui
    change) re-télécharge sous un nouveau nom. L'etag est neutralisé pour être
    un nom de fichier sûr (les updatedAt GitHub contiennent des « : »)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", asset.etag) or "noetag"
    return os.path.join(C.STORE_CACHE_DIR, f"{safe}__{asset.name}")


def load_store_df(store=None) -> pd.DataFrame:
    """Concatène toutes les partitions du flux principal en un DataFrame brut
    (schéma parquet, AVANT le finalize commun de db.py). Téléchargement mis en
    cache par etag. Magasin vide → DataFrame vide (db.py renvoie alors le schéma
    canonique). Aucune écriture ailleurs que dans le cache local."""
    store = store or get_store()
    os.makedirs(C.STORE_CACHE_DIR, exist_ok=True)
    frames = []
    for a in _partition_assets(store):
        local = _cache_path(a)
        if not os.path.exists(local):
            store.download(a.name, local)
        frames.append(pd.read_parquet(local))
    return concat_partitions(frames)
