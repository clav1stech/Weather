# -*- coding: utf-8 -*-
"""REGISTRE des domaines du dashboard neige — même mécanique que le canicule :
ajouter un domaine = un sous-package + UNE entrée ici, rien d'autre à toucher
(ni les domaines existants, ni app/pages/, ni snow_app.py)."""

from .neige.page import page_neige

DOMAIN_PAGES = [
    ("Vue d'ensemble neige", page_neige),
]
