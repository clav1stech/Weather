# -*- coding: utf-8 -*-
"""Package du dashboard Streamlit — voir docs/CODEMAP.md pour la carte complète.

Couches (dépendances du bas vers le haut, jamais l'inverse) :
  runtime   → contexte d'exécution (local/cloud, fuseaux, variable principale)
  data/     → accès à la base parquet et sélections de runs (AUCUN calcul métier)
  stats/    → statistiques d'ensemble génériques, tolérantes NaN (aucun Streamlit
              hormis @st.cache_data / session_state pour la climato ajustable)
  ui/       → thème, composants et graphiques Plotly génériques (multi-domaines)
  domains/  → un sous-package par phénomène métier (canicule aujourd'hui ;
              neige/ski, nowcasting demain) : seuils, graphiques et pages propres
  pages/    → pages transverses du dashboard (vue d'ensemble, exploration,
              convergence, diagnostic, pipeline)

Le pipeline de collecte (Forecast.py, Forecast_legacy.py, validate_cross_pipeline.py,
run_dual.py, migrate.py) vit à la RACINE du projet et ne dépend pas de ce package.
"""
