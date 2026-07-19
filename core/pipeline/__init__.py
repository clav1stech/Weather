# -*- coding: utf-8 -*-
"""Mécanique GÉNÉRIQUE de pipeline d'ensemble — mutualisée entre apps.

Version paramétrée (config-agnostique) des mécanismes éprouvés du pipeline
canicule (Forecast.py) : fraîcheur empirique échéance par échéance, portée
réelle contiguë, persistance conditionnée à la complétude, garde
anti-régression, fusion dédupliquée par (run_date, modèle) et écriture
atomique. Le pipeline canicule conserve volontairement ses implémentations
inline (partie critique, jamais remaniée à l'occasion d'une mutualisation —
duplication du motif assumée, cf. core/io/atomic.py) ; ce package est destiné
aux pipelines des NOUVELLES apps (apps/snow/pipeline/…)."""
