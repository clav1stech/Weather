# -*- coding: utf-8 -*-
"""Écriture atomique d'un parquet — fichier .tmp puis os.replace : le fichier
final est remplacé d'un coup, jamais d'état partiel sur le disque (invariant
d'intégrité des données, cf. CLAUDE.md).

Implémentation GÉNÉRIQUE destinée aux pipelines des nouvelles apps
(apps/snow/pipeline/…). Le pipeline canicule (Forecast.py et les fetch_* de la
racine) conserve volontairement sa propre séquence inline, identique : la
partie critique de collecte ne se modifie pas à l'occasion d'un chantier de
mutualisation — duplication du motif assumée, pas un oubli."""

import os


def atomic_write_parquet(df, path: str) -> None:
    """Écrit `df` dans `path` (parquet, sans index) via un .tmp adjacent puis
    remplacement atomique. Le .tmp vit dans le même dossier que la cible :
    os.replace n'est atomique qu'au sein d'un même système de fichiers."""
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)  # remplacement atomique — jamais d'écriture partielle
