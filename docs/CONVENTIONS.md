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
- **Code réutilisable par une autre app du monorepo** (stat pure, service,
  thème, harnais) → `core/`, en CONFIG-AGNOSTIQUE : jamais d'`import config`
  ni `app.*` dans `core/`, tout réglage arrive en paramètre. Côté app, un
  ADAPTATEUR `app/…` lie la config et **conserve les signatures historiques**
  (contrat des pages et des harnais).
- **Accès/sélection de données** → `app/data/` (aucun calcul métier).
- **Statistique générique** (marche pour tout domaine/variable) → calcul dans
  `core/stats/`, liaison config dans l'adaptateur `app/stats/`.
- **Graphique générique** → `app/ui/charts.py` ; **propre à un domaine** →
  `app/domains/<nom>/charts.py`.
- **Élément d'interface réutilisable** (en-tête, bandeau, carte, badge,
  calendrier, tableau) → `core/ui/components.py` ; **couleur, mesure, hauteur
  de graphique** → `core/ui/tokens.py` ; **règle de mise en forme** →
  `core/ui/design.py`. Une page n'invente ni l'un ni l'autre.
- **Nouvelle page** : transverse → `app/pages/` + routage `meteo_app.py` ;
  métier → dans son domaine + registre `app/domains/__init__.py`.
- Contrat page : `page_xxx(runs, sig)`.

## Imports & dépendances
- Imports absolus (`from app.stats.ensemble import super_ensemble`). Le package
  `app` vit dans `apps/canicule/app/` mais s'importe toujours `app.…` : le
  chemin `apps/canicule/` est exposé sur `sys.path` par les points d'entrée
  (`meteo_app.py`, harnais `core/testing/`, `tests/`).
- Sens unique : `core` ← `runtime` ← `data/db` ← `stats` ← `data/runsets` ←
  {`ui`, `pages`, `domains`}. Jamais de `pages` → `pages`, `domains` → `domains`,
  ni `stats` → `data`. `core/` n'importe JAMAIS `config` ni `app.*` ; le
  pipeline racine n'importe jamais `core/`.
- Aucune nouvelle dépendance externe sans justification forte.
- Le dashboard n'importe du pipeline que `Forecast.persist`/`load_existing`,
  `validate_cross_pipeline` (helpers lecture xlsx) et `run_dual` (constantes
  de créneaux) — ne pas élargir cette surface.

## Interface (design system `core/ui/`)
- **Plus aucun style inline dans les pages ni dans les composants.** Le rendu
  passe par les composants de `core/ui/components.py`, qui ne posent que des
  classes `wx-*` définies dans `core/ui/design.py`. Seule exception admise :
  une variable CSS portant une DONNÉE (teinte d'un niveau de risque, couleur
  d'un jour de calendrier) — jamais une règle de mise en forme.
- **Toute couleur vient des jetons** (`core/ui/tokens.py`, deux jeux clair et
  sombre aux mêmes clés). Aucun `#RRGGBB` d'interface en dur dans une page ;
  les couleurs MÉTIER (modèle, station, altitude) restent, elles, dans
  `config.py` / `snow_config.py` et arrivent en paramètres — `core/` reste
  config-agnostique.
- **Une figure ne fixe ni sa hauteur, ni ses marges, ni sa légende** : elle
  appelle `core.ui.plotly_theme.apply_layout` avec une clé du registre
  `CHART_H` (`strip`/`compact`/`standard`/`tall`). Une hauteur proportionnelle
  au nombre de lignes reste explicite et commentée.
- **Une couleur = un seul signal.** La teinte d'un bandeau ou d'une case de
  calendrier ne porte qu'un critère (probabilité T850, palier d'intensité
  neige) ; toute réserve ou nuance passe par un glyphe, un sous-titre ou
  l'infobulle — jamais par la couleur.
- **Absence de données ≠ alerte** : un flux annexe absent s'affiche avec
  `empty_state` (encart discret), jamais avec `st.warning`/`st.error`. Les
  `st.info` restent réservés aux messages OPÉRATIONNELS (cooldown, action à
  faire).
- **Navigation** : `st.navigation(position="top")` via
  `core.ui.nav.build_navigation`, sections Suivi / Analyse / Données ; la
  sidebar (`context_panel`) ne porte que l'état des données. Le contrat de page
  `page_xxx(runs, sig)` est inchangé — les arguments sont figés par
  `functools.partial` sur chaque `st.Page`.

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
   `python tools/ui_snapshot.py capture` (et `… capture neige` si le chantier
   touche le dashboard neige) AVANT de toucher au code ;
2. modifier ;
3. les deux `… check` doivent être 100 % verts APRÈS (mêmes données, même
   heure « pleine »). Un changement de comportement VOULU se justifie dans le
   message de PR/commit, puis on re-capture.

Sous AppTest, une page de `st.navigation` construite à partir d'un callable ne
s'atteint ni par `switch_page` ni par `query_params` : passer par
`core.testing.apptest_nav.aller_a` (hash de l'`url_path`), jamais par un
widget de navigation.

## Git
- Ne jamais committer sans demande explicite de l'utilisateur.
- `Export/`, `.venv/`, `__pycache__/`, `tools/golden/` ne sont pas versionnés.
