# Changelog

## [2.4.6] - 2026-07-04
Ajouter l'option 'all' au déclenchement manuel du pipeline.

Permet de lancer les 6 jobs (api, legacy, t2m, obs, obs6m, instant) en un
seul workflow_dispatch au lieu de les déclencher un par un.

## [2.4.5] - 2026-07-04
Detect and retry missing legacy slots dynamically.

Replace cron-based window logic with dynamic detection of Météociel legacy runs already published but not yet fetched. Instead of assuming data is ready at fixed hours (10:15 UTC for 0Z, 22:15 UTC for 12Z), the new `_missing_legacy_slots()` checks the current timestamp against actual publication times and identifies which slots are due but absent from `legacy/`, handling date edge cases (J-1 files when polling just after UTC midnight). Simplifies the dashboard UI and makes the pipeline more resilient to timing variations.

Also bump APP_VERSION to 2.4.5.

## [2.3.4] - 2026-07-04
Add 6-min infra‑hourly observations feed.

Ajoute un flux annexe d'observations infra‑horaires (6 min).
- Nouveau pipeline: fetch_observations_6m.py (DPPaquetObs /paquet/infrahoraire-6m) et parquet séparé data/database_paris_observations_6m.parquet
- Couche lecture: app/data/observations_6m.py (load/latest, dégradation silencieuse)
- Config: OBS_6M_* (endpoint, variables, schema) et DB_OBS_6M_PATH
- CI: cron + job GitHub Actions, option workflow_dispatch obs6m
- UI: intégration fraîcheur 6min dans app/domains/observations/page.py, nouvelle logique d'alerte OBS_CARTE_ALERTE_H
- Docs/CODEMAP et pipeline page mis à jour, app version → 2.4.3
Conservation: parquet 6min strictement séparé du flux horaire (pas de fusion).

## [2.4.2] - 2026-07-03
Ajout flux instantané 15 min + job CI.

Ajoute un nouveau flux annexe de prévision 15 minutes (fetch_instant.py) et son stockage séparé data/database_paris_instant.parquet. Ajout des constantes et du schéma INSTANT_* dans config.py, mise à jour de docs/CODEMAP.md et CLAUDE.md. CI: nouvelle job fetch-instant dans .github/workflows/run_forecast.yml (cron 5,35 * * * *) et option workflow_dispatch 'instant'. Persistance atomique + upsert sur la clé validtime (révisions acceptées), backfill adaptatif (init vs runs suivants). Bump de APP_VERSION (2.4.1→2.4.2).

## [2.4.1] - 2026-07-03
Basculer sur DPPaquetObs pour les obs.

Remplace l'utilisation mono-station DPObs par le « Paquet Observation » DPPaquetObs : changement de endpoint et ajout de OBS_DEPARTEMENT dans config.py. fetch_observations.py refactorisé pour appeler /paquet/horaire, parser la liste d'entrées, supporter le backfill/rattrapage après cron manqué, dédoublonnage défensif et erreurs explicites (parquet laissé intact en cas de panne totale). Mise à jour des docs (CODEMAP.md, CLAUDE.md) et du parquet d'observations. Bump de APP_VERSION → 2.4.1 et ajustement de l'exclusion dans tools/export_project.py pour ne pas exclure app/data.

## [2.4.0] - 2026-07-03
Ajout pipeline observations MF (DPObs).

Ajoute un flux annexe pour les observations Météo‑France (DPObs) et une page UI associée.

- Nouveau pipeline fetch_observations.py → data/database_paris_observations.parquet (append-only, dédup (station_id, valid_time), conversions K→°C et Pa→hPa, écriture atomique).
- Lecture et helpers: app/data/observations.py (dégradation silencieuse si absent/corrompu).
- Domaine UI: app/domains/observations/* + enregistrement dans app/domains/__init__.py (page « Observations en direct »).
- CI: .github/workflows/run_forecast.yml ajoute job cron horaire et option dispatch 'obs'.
- Config, docs et .gitignore mis à jour (OBS_* en config.py, CODEMAP.md, .env ignoré).

Respect de la sécurité: clé via METEOFRANCE_API_KEY (env/.env local), jamais en dur ni loguée.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>

## [2.3.6] - 2026-07-03
Complétion z500 et pool latest_z500.

Ajout d'une logique pour combler une variable entièrement absente (ex. z500) sur le dernier run stocké : nouvelle fonction complete_missing_vars() dans Forecast.py, intégrée au flux principal et affichant un message explicite lors du comblement. Ajout d'un pool dédié latest_z500_sub() dans app/data/runsets.py — récupère pour chaque modèle sa dernière valeur z500 connue (peut remonter à un run antérieur à celui affiché pour T850). Utilisation de ce pool dans app/domains/heatwave/page.py et app/pages/overview.py pour le contexte synoptique, et mise à jour de la doc (CLAUDE.md). Version de l'app bumpée à 2.3.6 et fichier de données parquet mis à jour. Les invariants de persistance (pas d'extension de portée, pas d'écrasement de variables existantes) sont respectés.

## [2.3.0] - 2026-07-03
Ajouter support Z500 (géopotentiel 500 hPa).

Intègre le géopotentiel 500 hPa (z500) comme variable de contexte.

- config: ajoute la variable z500 et paramètres de climatologie Z500.
- Forecast: gestion rétrocompatible des colonnes manquantes (load_existing) et adoption de dropna(..., how="all") pour que une ligne soit valide si au moins une variable l'est.
- stats: nouvelle clim_z500_normal et var_median; member_matrix accepte une variable paramétrée.
- ui/pages/domains: chart z500, onglet Z500 dans Explore, expander technique dans Overview, signal_synoptique dans heatwave.
- Docs: mise à jour CODEMAP et CLAUDE.md.

Z500 reste un signal d'appui — n'altère pas la logique pilote T850.
