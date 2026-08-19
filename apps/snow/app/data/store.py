# -*- coding: utf-8 -*-
"""Adaptateur neige du magasin de données externe (« sortie de git ») — lie
snow_config.py à core/store/ (règle CLAUDE.md : core/ reste config-agnostique,
c'est l'adaptateur app/ qui apporte les réglages).

Même mécanique que l'adaptateur canicule (apps/canicule/app/data/store.py),
avec les réglages neige : magasin PRIMAIRE en ligne, parquet git en BACKUP
automatique au moindre échec ou résultat vide, lecture git par défaut en local.

L'enjeu est plus fort ici que côté canicule : la neige est SORTIE de git (le
magasin en est l'unique source de vérité, les parquets du dépôt sont gelés à la
bascule) — magasin inactif = dashboard figé à cette date.

Les parquets HOT et leur ARCHIVE (cold) sont des flux DISTINCTS du magasin
(un préfixe d'asset chacun) : la sémantique hot/cold du dashboard est
préservée telle quelle, ce module ne les fusionne jamais."""

import os
import re

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from apps.snow.app.runtime import IS_LOCAL
from core.store import (
    GitHubReleaseStore,
    LocalDirStore,
    concat_partitions,
    parse_partition_name,
)
from core.store.github_http import (
    GitHubReleaseHttpStore,
    reset_release_cache,
)


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

    Par défaut : actif EN LIGNE, inactif en local (le parquet local, lui, est
    alimenté par les collectes de dev). WEATHER_STORE (secret Streamlit ou
    variable d'environnement) tranche dans les deux sens : « 0 »/« false »
    force le parquet git, toute autre valeur non vide force le magasin."""
    valeur = _reglage("WEATHER_STORE")
    if valeur:
        return valeur.lower() not in ("0", "false")
    return not IS_LOCAL


def get_store():
    """Magasin de LECTURE selon WEATHER_STORE_BACKEND — « github_http » par
    défaut (API REST + HTTPS, sans `gh` ni token : le seul backend utilisable
    sur Streamlit Cloud), « github » via la CLI `gh`, « local » pour les tests.
    L'ÉCRITURE (double écriture pipeline) ne passe pas par ici."""
    backend = (_reglage("WEATHER_STORE_BACKEND") or "github_http").lower()
    if backend == "local":
        return LocalDirStore(os.environ["WEATHER_STORE_DIR"])
    if backend == "github":
        return GitHubReleaseStore(repo=SC.STORE_REPO, tag=SC.STORE_TAG)
    # Jeton réutilisé du déclenchement pipeline (portée Actions, mais suffisant
    # pour élever le quota anonyme 60/h → 5000/h sur un dépôt public : la
    # lecture de release n'exige aucune permission particulière côté GitHub).
    # GitHubReleaseHttpStore ne lit lui-même que os.environ (inerte côté
    # Streamlit Cloud) — le jeton doit donc transiter par cet adaptateur.
    return GitHubReleaseHttpStore(repo=SC.STORE_REPO, tag=SC.STORE_TAG,
                                   token=_reglage(SC.GITHUB_DISPATCH_TOKEN_SECRET))


def flux_prefix(db_path):
    """Préfixe de partition d'un flux = basename du parquet sans extension
    (db_megeve_hd.parquet → db_megeve_hd). Un parquet d'archive a donc son
    propre préfixe (db_megeve_archive), distinct de son hot."""
    return os.path.splitext(os.path.basename(db_path))[0]


def _partition_assets(store, prefix):
    """Assets d'UN flux : préfixe EXACT (un simple startswith attraperait
    db_megeve_hd_… sous le préfixe db_megeve — d'où le parse et l'égalité
    stricte). Triés par nom (mois croissant)."""
    out = []
    for a in store.list(prefix):
        parsed = parse_partition_name(a.name)
        if parsed and parsed[0] == prefix:
            out.append(a)
    return sorted(out, key=lambda a: a.name)


def _cache_path(asset):
    """Chemin de cache local d'une partition, clé par etag : un mois clos
    (etag stable) n'est téléchargé qu'une fois ; le mois courant re-télécharge
    sous un nouveau nom. L'etag est neutralisé pour être un nom de fichier sûr."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", asset.etag) or "noetag"
    return os.path.join(SC.STORE_CACHE_DIR, f"{safe}__{asset.name}")


def load_store_df(prefix, store=None) -> pd.DataFrame:
    """Concatène toutes les partitions d'un flux en un DataFrame brut (schéma
    parquet). Téléchargement mis en cache par etag. Aucune écriture ailleurs
    que dans le cache."""
    store = store or get_store()
    os.makedirs(SC.STORE_CACHE_DIR, exist_ok=True)
    frames = []
    for a in _partition_assets(store, prefix):
        local = _cache_path(a)
        if not os.path.exists(local):
            store.download(a.name, local)
        frames.append(pd.read_parquet(local))
    return concat_partitions(frames)


def flux_signature(db_path):
    """Signature de cache d'un flux : etags des partitions si le magasin est
    actif ET joignable, sinon mtime du parquet git (None si absent) — même
    repli magasin→git que la lecture, git restant le backup automatique."""
    if store_active():
        try:
            assets = _partition_assets(get_store(), flux_prefix(db_path))
            sig = tuple((a.name, a.etag) for a in assets) or None
        except Exception:  # noqa: BLE001 — magasin injoignable, repli sur git
            sig = None
        if sig is not None:
            return sig
    try:
        return os.path.getmtime(db_path)
    except OSError:
        return None


def read_flux(db_path):
    """DataFrame BRUT d'un flux (aucune conversion, aucun filtre — l'appelant
    garde son propre post-traitement) : magasin PRIMAIRE si actif, repli
    automatique sur le parquet git en BACKUP au moindre échec ou résultat vide.
    Rien d'exploitable des deux côtés → None, l'appelant se dégradant
    silencieusement comme sans ce flux (invariant du dashboard neige)."""
    if store_active():
        try:
            df = load_store_df(flux_prefix(db_path))
            if df is not None and not df.empty:
                return df
        except Exception:  # noqa: BLE001 — magasin injoignable, repli sur git
            pass
    try:
        if os.path.exists(db_path):
            df = pd.read_parquet(db_path)
            if df is not None and not df.empty:
                return df
    except Exception:  # noqa: BLE001 — parquet corrompu, dégradation silencieuse
        pass
    return None


def vider_cache_magasin():
    """Oublie tout ce qui est mémoïsé du magasin : métadonnées du release (TTL
    processus) — le cache disque des partitions, lui, est clé par etag et se
    renouvelle donc de lui-même dès qu'une partition change. Appelé par le
    bouton « Rafraîchir » : sans cela, un clic dans les secondes qui suivent le
    chargement de la page relirait la MÊME liste d'assets et donc les mêmes
    fichiers, sans voir la collecte qui vient d'aboutir."""
    reset_release_cache()
