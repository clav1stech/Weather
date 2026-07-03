# Conventions de code

Règles courtes pour toute contribution (humaine ou IA). Les invariants métier
sont dans `CLAUDE.md`, la carte des modules dans `docs/CODEMAP.md`.

## Langue & style
- Docstrings et commentaires en **français**, denses : le *pourquoi* (pièges,
  invariants), pas la genèse (« corrigé suite à… » interdit).
- Noms de modules/fonctions en anglais ou français existant — suivre le module
  touché ; ne pas renommer sans nécessité (les noms sont le contrat des harnais
  de non-régression).
- PEP 8 assoupli : lignes ~100 caractères max, comme l'existant.
- Préfixe `_` = helper interne au module (quelques noms historiques préfixés
  restent importés ailleurs — tolérés, ne pas généraliser).

## Où mettre quoi
- **Un réglage variable** (modèle, variable, seuil physique, climato, KPI) →
  `config.py`, jamais en dur dans la logique. Les seuils d'INTERPRÉTATION
  propres à l'affichage d'un domaine (paliers de labels) → `logic.py` du domaine.
- **Accès/sélection de données** → `app/data/` (aucun calcul métier).
- **Statistique générique** (marche pour tout domaine/variable) → `app/stats/`.
- **Graphique générique** → `app/ui/charts.py` ; **propre à un domaine** →
  `app/domains/<nom>/charts.py`.
- **Nouvelle page** : transverse → `app/pages/` + routage `meteo_app.py` ;
  métier → dans son domaine + registre `app/domains/__init__.py`.
- Contrat page : `page_xxx(runs, sig)`.

## Imports & dépendances
- Imports absolus (`from app.stats.ensemble import super_ensemble`).
- Sens unique : `runtime` ← `data/db` ← `stats` ← `data/runsets` ←
  {`ui`, `pages`, `domains`}. Jamais de `pages` → `pages`, `domains` → `domains`,
  ni `stats` → `data`.
- Aucune nouvelle dépendance externe sans justification forte.
- Le dashboard n'importe du pipeline que `Forecast.persist`/`load_existing`,
  `validate_cross_pipeline` (helpers lecture xlsx) et `run_dual` (constantes
  de créneaux) — ne pas élargir cette surface.

## Streamlit & cache
- Fonctions coûteuses : `@st.cache_data(show_spinner=False)` avec la
  signature de fichier en 1er argument `_sig` (cf. `db_signature`,
  `legacy_signature`) — jamais de cache sans clé d'invalidation.
- `st.set_page_config` : uniquement dans `meteo_app.py`, avant tout autre
  appel Streamlit. Aucun module de `app/` n'exécute d'appel Streamlit à
  l'import (décorateurs exceptés).
- Affichage en heure de Paris ; stockage UTC tz-naïf ; cycles via `utc_cycle`.

## Données (rappel bloquant — détail dans CLAUDE.md)
- Parquet et xlsx legacy : lecture seule côté dashboard. Seule exception :
  `app/data/legacy_import.py` (absence avérée, sauvegarde datée, fusion via
  `Forecast.persist`). Ne jamais écrire directement dans le parquet.
- Le pipeline (fichiers racine) ne se modifie pas à l'occasion d'un chantier
  dashboard.

## Non-régression (obligatoire pour tout refactor / factorisation)
1. `python tools/check_non_regression.py capture` puis
   `python tools/ui_snapshot.py capture` AVANT de toucher au code ;
2. modifier ;
3. les deux `… check` doivent être 100 % verts APRÈS (mêmes données, même
   heure « pleine »). Un changement de comportement VOULU se justifie dans le
   message de PR/commit, puis on re-capture.

## Git
- Ne jamais committer sans demande explicite de l'utilisateur.
- `Export/`, `.venv/`, `__pycache__/`, `tools/golden/` ne sont pas versionnés.
