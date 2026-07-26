# -*- coding: utf-8 -*-
"""Magasin de données externe (« sortie de git ») — cf. docs/DESIGN_sortie_git.md.

Abstraction DataStore (base) + implémentations (local, github) + fonctions
pures de partitionnement mensuel. Rien n'est branché sur le pipeline ni le
dashboard à ce stade (Phase 0) : abstraction et briques testables seulement."""

from core.store.base import Asset, DataStore
from core.store.github import GitHubReleaseStore
from core.store.github_http import GitHubReleaseHttpStore
from core.store.local import LocalDirStore
from core.store.partition import (
    concat_partitions,
    month_key,
    parse_partition_name,
    partition_name,
    same_rows,
    split_by_month,
)

__all__ = [
    "Asset", "DataStore", "GitHubReleaseStore", "GitHubReleaseHttpStore",
    "LocalDirStore", "concat_partitions", "month_key", "parse_partition_name",
    "partition_name", "same_rows", "split_by_month",
]
