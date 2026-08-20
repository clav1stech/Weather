# -*- coding: utf-8 -*-
"""
Dashboard météo — Prévisions d'ensemble (Paris)
================================================
POINT D'ENTRÉE Streamlit uniquement : configuration de la page, feuille de
style, navigation et panneau de contexte. Tout le reste vit dans le package
app/ — carte complète dans docs/CODEMAP.md :
  app/data/    accès à la base parquet & sélections de runs
  app/stats/   statistiques d'ensemble (tolérantes NaN), climatologie
  app/ui/      thème, composants, graphiques génériques
  app/domains/ un sous-package par phénomène métier (canicule…)
  app/pages/   pages transverses (vue d'ensemble, exploration, convergence…)

Config-driven : modèles, variables, climatologie et seuils vivent dans config.py.
La navigation = pages des domaines (app/domains/__init__.py, registre) puis
pages transverses — ajouter un domaine ne modifie pas ce fichier.

Navigation en BARRE DU HAUT (st.navigation position="top") : la sidebar n'est
plus un menu mais un panneau de contexte (fraîcheur des données, rafraîchir,
sources, version). Le contrat des pages ne change pas — elles restent des
`page_xxx(runs, sig)`, `runs`/`sig` étant calculés ici puis figés sur chaque
st.Page (cf. core/ui/nav.build_navigation).
"""

import os
import sys

# Monorepo : le code du dashboard canicule (package app/) vit dans
# apps/canicule/, le code mutualisé dans core/ (racine). Ce point d'entrée
# reste à la racine — Streamlit Cloud, les lanceurs locaux et le harnais UI
# pointent dessus — et expose apps/canicule/ sur sys.path pour que `import app`
# se résolve ; la racine (dossier de ce script) est déjà sur sys.path, ce qui
# résout `import config` et `import core`.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps", "canicule"))

import streamlit as st

from app.data.db import db_signature, list_runs
from app.data.runsets import latest_refresh_status
from app.data.store import vider_cache_magasin
from app.domains import DOMAIN_PAGES
from app.pages.convergence import page_convergence
from app.pages.diagnostic import page_diagnostic
from app.pages.explore import page_explore
from app.pages.overview import page_overview
from app.pages.pipeline import page_run
from app.ui.theme import stylesheet
from core.ui.components import badge_html
from core.ui.nav import build_navigation, context_panel
from core.version import SHARED_VERSION

APP_VERSION = SHARED_VERSION

st.set_page_config(page_title="Dashboard Météo — Ensembles Paris",
                   layout="wide")
st.markdown(stylesheet(), unsafe_allow_html=True)

# Pages transverses, après les domaines, groupées par intention de lecture.
# « Lancer le pipeline » est visible PARTOUT : sa consultation (stock legacy,
# contrôle croisé) est en lecture seule, et la page adapte elle-même ses
# capacités à l'environnement — sous-processus en local, déclenchement du job
# CI derrière mot de passe en ligne, import legacy jamais exposé hors local
# (cf. app/pages/pipeline.py).
ANALYSE_PAGES = [
    ("Vue d'ensemble", page_overview),
    ("Explorer un run", page_explore),
    ("Convergence des runs", page_convergence),
]
DONNEES_PAGES = [
    ("Contrôle des runs", page_diagnostic),
    ("Lancer le pipeline", page_run),
]
# Conservé sous son nom historique : les lanceurs et scripts externes s'y
# réfèrent comme à la liste des pages transverses.
CORE_PAGES = ANALYSE_PAGES + DONNEES_PAGES


def _vider_les_caches():
    """Les trois caches, dans cet ordre : le magasin externe (métadonnées du
    release, mémoïsées avec un TTL taillé pour le rythme automatique), les
    calculs (cache_data) et la base elle-même (cache_resource, que
    cache_data.clear() ne touche PAS). En sauter un laisse la page sur les
    données précédentes jusqu'au redémarrage du process."""
    vider_cache_magasin()
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()


def _panneau_contexte(runs, sig):
    """Sidebar = état des données, jamais la navigation. Bloc fraîcheur réduit à
    l'essentiel — dernier run, heure de collecte, complétude. Pas de compteur
    cumulatif : le nombre total de runs archivés grossit sans fin et ne dit rien
    de la fiabilité des données."""
    lignes, badges = [], []
    if not runs.empty:
        lignes.append(("Dernière prévision", runs.iloc[0]["label"]))
        refreshed_at, complete, missing = latest_refresh_status(runs, sig)
        if refreshed_at is not None:
            lignes.append(("Rafraîchi le",
                           refreshed_at.strftime("%d/%m/%Y à %Hh%M")))
        badges.append(badge_html("Tous les modèles attendus", niveau="ok")
                      if complete else
                      badge_html(f"Manque : {', '.join(missing)}", niveau="warn"))
    context_panel(
        titre="État des données", lignes=lignes, badges_html=badges,
        bouton="Rafraîchir", on_click=_vider_les_caches,
        pied=("<b>Mise à jour automatique</b> 4×/jour via Open-Meteo — "
              "runs 0Z/6Z/12Z/18Z (GEM : 0Z/12Z)<br>"
              "Données : ECMWF · NOAA · ECCC<br>"
              f"Version {APP_VERSION}"))


# --------------------------------------------------------------------------- #
#  Routage
# --------------------------------------------------------------------------- #
def main():
    sig = db_signature()
    runs = list_runs(sig)

    # Registre sectionné : les domaines d'abord (le 1er est la page d'accueil),
    # puis les pages transverses. Ajouter un domaine reste une seule ligne dans
    # app/domains/__init__.py — ce fichier n'en sait rien.
    page = build_navigation(
        {"Suivi": DOMAIN_PAGES,
         "Analyse": ANALYSE_PAGES,
         "Données": DONNEES_PAGES},
        args=(runs, sig))

    _panneau_contexte(runs, sig)
    page.run()


if __name__ == "__main__":
    main()
