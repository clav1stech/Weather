# -*- coding: utf-8 -*-
"""Adaptateur canicule du magasin de données externe (« sortie de git ») —
lie config.py à core/store/ (règle CLAUDE.md : core/ reste config-agnostique,
c'est l'adaptateur app/ qui apporte les réglages).

Magasin PRIMAIRE EN LIGNE, git en BACKUP : le déployé lit le magasin par
défaut (le parquet git n'y est plus qu'un filet, cf. app/data/db.py), tandis
que le local reste sur le parquet — dev, tests et harnais de non-régression
n'ont ainsi jamais besoin du réseau. Surchargeable dans les deux sens par
WEATHER_STORE. Backend choisi par WEATHER_STORE_BACKEND (github_http par
défaut, local pour tests/dev)."""

import os
import re

import pandas as pd
import streamlit as st

import config as C
from app.runtime import IS_LOCAL
from core.store import (
    GitHubReleaseStore,
    LocalDirStore,
    concat_partitions,
    parse_partition_name,
)
from core.store.github_http import GitHubReleaseHttpStore


def _reglage(nom):
    """Valeur d'un réglage, cherchée dans st.secrets PUIS dans l'environnement
    — st.secrets est le SEUL canal de configuration de Streamlit Cloud (les
    variables d'environnement n'y sont pas exposées), l'environnement reste
    celui du local et des tests. Absent des deux → None.

    La lecture de st.secrets appartient à l'adaptateur : core/ n'y touche
    jamais. Elle lève quand aucun fichier de secrets n'existe (cas normal en
    local) — d'où le repli silencieux."""
    try:
        valeur = st.secrets.get(nom)
    except Exception:  # noqa: BLE001 — aucun secrets.toml (local), pas une anomalie
        valeur = None
    if valeur is None:
        valeur = os.environ.get(nom)
    return None if valeur is None else str(valeur).strip()


def store_active() -> bool:
    """True si le dashboard doit lire depuis le magasin externe plutôt que le
    parquet git.

    Par défaut : actif EN LIGNE, inactif en local. Le parquet git n'est plus
    qu'un repli automatique côté cloud (db.py / load_flux), alors qu'il reste
    la source normale en local — un harnais de non-régression doit comparer des
    calculs sur la base locale, pas sur un magasin qui bouge toutes les 2 h.

    WEATHER_STORE (secret Streamlit ou variable d'environnement) tranche dans
    les deux sens : « 0 »/« false » force le parquet git, toute autre valeur
    non vide force le magasin."""
    valeur = _reglage("WEATHER_STORE")
    if valeur:
        return valeur.lower() not in ("0", "false")
    return not IS_LOCAL


def get_store():
    """Construit le magasin de LECTURE selon WEATHER_STORE_BACKEND :
      • « github_http » (défaut) : API REST + HTTPS, sans `gh` ni token — le
        seul backend utilisable sur Streamlit Cloud (pas de CLI gh) ;
      • « github » : via la CLI `gh` (dev local disposant de gh) ;
      • « local » : un dossier (WEATHER_STORE_DIR), pour les tests.
    Le reste du code ne voit qu'une interface DataStore, jamais le backend.
    L'ÉCRITURE (double écriture pipeline) ne passe pas par ici : elle est inline
    dans Forecast.py via gh."""
    backend = (_reglage("WEATHER_STORE_BACKEND") or "github_http").lower()
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
    est actif ET joignable, sinon mtime du parquet git (None si absent) — même
    repli magasin→git que le flux principal (db.py), git reste le backup
    automatique derrière le magasin."""
    if store_active():
        try:
            sig = store_signature(prefix=flux_prefix(db_path))
        except Exception:  # noqa: BLE001 — magasin injoignable, repli sur git
            sig = None
        if sig is not None:
            return sig
    try:
        return os.path.getmtime(db_path)
    except OSError:
        return None


def load_flux(db_path, schema):
    """Base complète d'un flux annexe : magasin PRIMAIRE si actif, avec repli
    automatique sur le parquet git (BACKUP) au moindre échec ou résultat vide —
    puis absent / illisible des deux côtés → DataFrame vide au schéma :
    l'appelant se comporte exactement comme sans ce flux (dégradation
    silencieuse, invariant annexes)."""
    if store_active():
        try:
            df = load_store_df(prefix=flux_prefix(db_path))
        except Exception:  # noqa: BLE001 — magasin injoignable, repli sur git
            df = None
        if df is not None and not df.empty:
            return df
    try:
        if os.path.exists(db_path):
            df = pd.read_parquet(db_path)
            if df is not None and not df.empty:
                return df
    except Exception:  # noqa: BLE001 — parquet git illisible/corrompu
        pass
    return pd.DataFrame(columns=schema)
