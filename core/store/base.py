# -*- coding: utf-8 -*-
"""Contrat d'un magasin de données externe (« sortie de git »,
docs/DESIGN_sortie_git.md §4).

Pipeline et dashboard ne parlent JAMAIS directement à `gh` ni à `boto3`, mais à
cette interface fine : changer de backend (GitHub Release assets → Cloudflare
R2…) = remplacer le seul module d'implémentation, aucune autre ligne ne bouge.
Le format parquet et le nommage des partitions étant identiques quel que soit le
backend, migrer la donnée = recopier les fichiers une fois.

Module GÉNÉRIQUE (règle core/) : aucune config, aucun secret ici — dépôt,
release, jeton arrivent en paramètres dans les implémentations."""

from typing import NamedTuple, Protocol, runtime_checkable


class Asset(NamedTuple):
    """Une partition telle que vue par le magasin. `etag` est un jeton de
    changement opaque (updatedAt côté GitHub, hash de contenu en local) : il
    n'a de sens que pour détecter « cet asset a-t-il changé ? » et piloter le
    cache du dashboard — jamais interprété autrement."""
    name: str
    size: int
    etag: str


@runtime_checkable
class DataStore(Protocol):
    """Magasin clé→fichier. Les noms sont des noms d'asset plats
    (`database_paris_2026-07.parquet`) ; le magasin ne connaît ni les mois ni
    les flux, juste des octets adressés par nom."""

    def list(self, prefix: str = "") -> list[Asset]:
        """Assets dont le nom commence par `prefix` (préfixe vide = tous).
        Magasin/flux pas encore amorcé → liste vide (jamais une erreur)."""
        ...

    def download(self, name: str, dest: str) -> None:
        """Copie l'asset `name` vers le chemin local `dest`. Asset absent →
        FileNotFoundError."""
        ...

    def upload(self, name: str, src: str) -> None:
        """Publie le fichier local `src` sous le nom d'asset `name`, en
        REMPLAÇANT un asset homonyme s'il existe (sémantique --clobber)."""
        ...

    def delete(self, name: str) -> None:
        """Supprime l'asset `name` (rollback / nettoyage). Asset absent →
        no-op silencieux (idempotent)."""
        ...
