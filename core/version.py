# -*- coding: utf-8 -*-
"""Version partagée des deux dashboards du monorepo (canicule et neige).

Source de vérité unique depuis v3.0 : les deux apps partagent une part
croissante de code (core/) et sont désormais versionnées comme un seul
produit. `meteo_app.py` (APP_VERSION) et `snow_app.py` (SNOW_APP_VERSION)
importent tous deux `SHARED_VERSION` — ne jamais les faire diverger.
`tools/export_project.py` lit cette constante par regex."""

SHARED_VERSION = "3.1.13"
