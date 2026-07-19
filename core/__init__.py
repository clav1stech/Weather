# -*- coding: utf-8 -*-
"""Code mutualisé entre les dashboards du monorepo (apps/canicule, apps/snow).

Règle d'architecture : core/ est CONFIG-AGNOSTIQUE — aucun module ici
n'importe `config` ni `app.*` ; tout réglage (variable, seuil, labels,
chemins) arrive en paramètre. C'est ce qui permet à deux apps aux configs
homonymes (`config.py` chacune) de partager ce code sans collision."""
