# -*- coding: utf-8 -*-
"""Magasin de données externe (« sortie de git ») — cf. docs/DESIGN_sortie_git.md.

Abstraction DataStore (base) + implémentations (local, github) + fonctions
pures de partitionnement mensuel + miroir de double écriture. Config-agnostique
comme tout core/ : magasin et réglages arrivent en paramètres, liés par les
adaptateurs app/ de chaque application."""

from core.store.base import Asset, DataStore
from core.store.github import GitHubReleaseStore
from core.store.github_http import GitHubReleaseHttpStore
from core.store.local import LocalDirStore
from core.store.mirror import changed_months, mirror_to_store
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
    "LocalDirStore", "changed_months", "concat_partitions", "mirror_to_store",
    "month_key", "parse_partition_name", "partition_name", "same_rows",
    "split_by_month",
]
