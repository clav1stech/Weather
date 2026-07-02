# -*- coding: utf-8 -*-
"""Registre des domaines métier du dashboard.

Un domaine = un phénomène météo autonome (canicule aujourd'hui ; neige/ski,
nowcasting demain) avec ses seuils, ses graphiques et ses pages, isolé dans
app/domains/<nom>/. Ajouter un domaine ne touche NI aux domaines existants NI
aux pages transverses : créer le sous-package puis l'enregistrer ci-dessous
(une entrée) — checklist complète dans docs/CODEMAP.md § Ajouter un domaine.
"""

from app.domains.heatwave.page import page_grand_public

# (label de navigation, fonction de rendu (runs, sig) -> None), dans l'ordre
# d'affichage souhaité dans la sidebar — les domaines passent AVANT les pages
# transverses (le 1er domaine est la page d'accueil du dashboard).
DOMAIN_PAGES = [
    ("Indicateur de canicule", page_grand_public),
]
