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
from core.store.github_http import GitHubReleaseHttpStore


def store_active() -> bool:
    """True si le dashboard doit lire depuis le magasin externe plutôt que le
    parquet git. Toute valeur non vide/non « 0 »/« false » active."""
    return os.environ.get("WEATHER_STORE", "").strip().lower() not in ("", "0", "false")


def get_store():
    """Construit le magasin de LECTURE selon WEATHER_STORE_BACKEND :
      • « github_http » (défaut) : API REST + HTTPS, sans `gh` ni token — le
        seul backend utilisable sur Streamlit Cloud (pas de CLI gh) ;
      • « github » : via la CLI `gh` (dev local disposant de gh) ;
      • « local » : un dossier (WEATHER_STORE_DIR), pour les tests.
    Le reste du code ne voit qu'une interface DataStore, jamais le backend.
    L'ÉCRITURE (double écriture pipeline) ne passe pas par ici : elle est inline
    dans Forecast.py via gh."""
    backend = os.environ.get("WEATHER_STORE_BACKEND", "github_http").strip().lower()
    if backend == "local":
        return LocalDirStore(os.environ["WEATHER_STORE_DIR"])
    if backend == "github":
        return GitHubReleaseStore(repo=C.STORE_REPO, tag=C.STORE_TAG)
    return GitHubReleaseHttpStore(repo=C.STORE_REPO, tag=C.STORE_TAG)


def _partition_assets(store, prefix):
    """Assets d'UN flux : préfixe EXACT `prefix` (un simple startswith
    attraperait database_paris_observations_… sous le préfixe database_paris —
    d'où le parse et l'égalité stricte). Triés par nom (mois croissant)."""
    out = []
    for a in store.list(prefix):
        parsed = parse_partition_name(a.name)
        if parsed and parsed[0] == prefix:
            out.append(a)
    return sorted(out, key=lambda a: a.name)


def store_signature(store=None, prefix=None):
    """Signature de cache = ((nom, etag)…) des partitions du flux `prefix`
    (principal par défaut). Change dès qu'une partition change (mois courant
    re-uploadé) → invalide le cache Streamlit au bon moment. Aucune partition →
    None (magasin pas encore amorcé, même sémantique que la signature git)."""
    store = store or get_store()
    assets = _partition_assets(store, prefix or C.STORE_PREFIX)
    return tuple((a.name, a.etag) for a in assets) or None


def _cache_path(asset):
    """Chemin de cache local d'une partition, clé par etag : un mois clos
    (etag stable) n'est téléchargé qu'une fois ; le mois courant (etag qui
    change) re-télécharge sous un nouveau nom. L'etag est neutralisé pour être
    un nom de fichier sûr (les updatedAt GitHub contiennent des « : »)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", asset.etag) or "noetag"
    return os.path.join(C.STORE_CACHE_DIR, f"{safe}__{asset.name}")


def load_store_df(store=None, prefix=None) -> pd.DataFrame:
    """Concatène toutes les partitions du flux `prefix` (principal par défaut) en
    un DataFrame brut (schéma parquet). Téléchargement mis en cache par etag.
    Magasin vide → DataFrame vide. Aucune écriture ailleurs que dans le cache."""
    store = store or get_store()
    os.makedirs(C.STORE_CACHE_DIR, exist_ok=True)
    frames = []
    for a in _partition_assets(store, prefix or C.STORE_PREFIX):
        local = _cache_path(a)
        if not os.path.exists(local):
            store.download(a.name, local)
        frames.append(pd.read_parquet(local))
    return concat_partitions(frames)


# --------------------------------------------------------------------------- #
#  Helpers génériques pour les flux ANNEXES (t2m, observations, vintages…)
# --------------------------------------------------------------------------- #
# Le flux principal (db.py) garde sa propre logique (archive hot/cold, finalize).
# Les flux annexes, eux, ont tous la même forme « signature + lecture parquet » :
# ces deux helpers la factorisent, en respectant store_active() et l'invariant
# de DÉGRADATION SILENCIEUSE (absence/erreur → DataFrame vide au schéma).

def flux_prefix(db_path):
    """Préfixe de partition d'un flux = basename du parquet sans extension
    (database_paris_t2m.parquet → database_paris_t2m). Convention uniforme,
    identique à STORE_PREFIX pour le flux principal."""
    return os.path.splitext(os.path.basename(db_path))[0]


def flux_signature(db_path):
    """Signature de cache d'un flux annexe : etags des partitions si le magasin
    est actif, sinon mtime du parquet git (None si absent)."""
    if store_active():
        return store_signature(prefix=flux_prefix(db_path))
    try:
        return os.path.getmtime(db_path)
    except OSError:
        return None


def load_flux(db_path, schema):
    """Base complète d'un flux annexe, depuis le magasin (si actif) ou le parquet
    git. Absent / vide / illisible / magasin injoignable → DataFrame vide au
    schéma : l'appelant se comporte exactement comme sans ce flux."""
    try:
        if store_active():
            df = load_store_df(prefix=flux_prefix(db_path))
        elif os.path.exists(db_path):
            df = pd.read_parquet(db_path)
        else:
            df = None
        if df is None or df.empty:
            return pd.DataFrame(columns=schema)
        return df
    except Exception:  # noqa: BLE001 — dégradation silencieuse (invariant annexes)
        return pd.DataFrame(columns=schema)
