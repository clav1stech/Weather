# Commandes du projet

Mémo unique des commandes utiles (lancement, pipelines, tests, non-régression,
archivage, export). Toutes se lancent depuis la **racine** du dépôt.
Environnement : dépendances dans `requirements.txt`
(`pip install -r requirements.txt`).

---

## Dashboards (Streamlit)

```sh
# Dashboard canicule (Paris) — point d'entrée : routage/sidebar, code dans app/
streamlit run meteo_app.py

# Dashboard neige (Megève) — point d'entrée : routage/sidebar, code dans apps/snow/app/
streamlit run snow_app.py
```

---

## Pipeline de collecte — canicule (racine)

> **Partie critique** : ces scripts alimentent `data/database_paris.parquet` et les xlsx
> legacy. Ne jamais les remanier à l'occasion d'un chantier dashboard.

```sh
# Source principale — fetch API Open-Meteo (le workflow le lance toutes les 2h)
python3 Forecast.py

# Scrape legacy Météociel — argument de cycle optionnel (défaut : cycle courant)
#   auto : 0Z / 12Z uniquement ; 6Z / 18Z réservés au déclenchement manuel (AIFS/GEFS)
python3 Forecast_legacy.py 0Z
python3 Forecast_legacy.py 12Z

# Contrôle croisé OM ↔ legacy (médiane-vs-médiane), même argument de cycle
python3 validate_cross_pipeline.py 0Z

# Orchestrateur manuel (Forecast + legacy + contrôle croisé selon le cycle)
#   n'est plus appelé par le workflow ; sert au lancement manuel (bouton dashboard)
python3 run_dual.py
```

---

## Pipeline de collecte — neige (apps/snow/pipeline/)

> Parquets dans `apps/snow/data/`, jamais dans `data/` (réservé canicule/Paris).
> En production, tout passe par `run_forecast.yml` (jobs `fetch-snow` toutes les 2 h,
> `fetch-snow-obs` horaire, `rollover-snow` hebdomadaire).

```sh
# Flux ensemble (membres + mean/spread Open-Meteo) → apps/snow/data/db_megeve.parquet
python3 apps/snow/pipeline/fetch_ensemble.py

# Flux maille fine (AROME France HD + ICON-D2) → apps/snow/data/db_megeve_hd.parquet
python3 apps/snow/pipeline/fetch_hd.py

# Observations Météo-France Alpes du Nord (DPPaquetObs dept 74, clé METEOFRANCE_API_KEY
# requise : secret CI, ou .env local `METEOFRANCE_API_KEY=<jwt>` — sans guillemets ni
# espace autour du =) → apps/snow/data/db_obs_alpes.parquet
python3 apps/snow/pipeline/fetch_observations.py

# Contrôle des identifiants stations contre le paquet API réel (AUCUNE écriture)
python3 apps/snow/pipeline/fetch_observations.py --list-stations
```

---

## Archivage hot/cold (rollover)

> Mécanique `core/pipeline/hot_cold.py` : sauvegardes datées `.bak`, vérification
> stricte de non-perte avant toute écriture, idempotent. Détail : `CLAUDE.md` et
> `docs/DESIGN_archivage_pipeline.md`.

```sh
# Neige (ACTIF, fenêtre 45 j) — bascule le plus ancien des 3 parquets vers *_archive
python3 apps/snow/pipeline/rollover.py --dry-run   # rapport sans aucune écriture
python3 apps/snow/pipeline/rollover.py             # exécution réelle

# Canicule (PRÉPARÉ, NON déclenché) — analyse chiffrée sur une COPIE de la base
python3 tools/archive_hot_cold_dry_run.py [--retention-days 35]

# Canicule — script d'activation : dry-run par défaut ; --execute REFUSÉ hors de main
# (procédure post-merge complète : docs/DESIGN_archivage_pipeline.md §7)
python3 tools/rollover_canicule.py
python3 tools/rollover_canicule.py --execute
```

---

## Tests

```sh
# Suite complète (canicule + neige + core)
python3 -m pytest tests/ -q

# Chaque fichier est aussi exécutable seul, sans pytest
python3 tests/test_snow_pipeline.py
python3 tests/test_hot_cold.py
```

---

## Non-régression (obligatoire pour tout refactor dashboard)

Deux harnais **en lecture seule** sur les données (dashboard **canicule** — la
non-régression neige passe par `tests/`). Protocole : `capture` **AVANT**
modification, puis `check` **APRÈS** → 100 % identique attendu (mêmes données, même
heure pleine ; références non versionnées).

```sh
# Calculs — capturer la référence avant modification
python3 tools/check_non_regression.py capture
# … puis vérifier après modification
python3 tools/check_non_regression.py check      # 'check' est le mode par défaut

# Rendu des pages (AppTest, sans navigateur) — même protocole
python3 tools/ui_snapshot.py capture
python3 tools/ui_snapshot.py check
```

---

## Export du projet

Trois profils. Artefacts dans `Export/` (**gitignoré** — jamais commité).

```sh
# Profil IA (défaut) — .txt curé : code + doc .md + manifeste (git, version, ~tokens, sommaire)
#   exclut tools/ et Forecast_legacy.py
python3 tools/export_project.py
python3 tools/export_project.py --ai

# Profil IA par application — app choisie + code/configuration/documentation communs utiles
python3 tools/export_project.py --canicule
python3 tools/export_project.py --snow       # alias français : --neige

# Profil outline — vue globale pour réfléchir avec un chat IA, sans corps de fonctions
python3 tools/export_project.py --outline
python3 tools/export_project.py --outline --canicule
python3 tools/export_project.py --outline --snow

# Profil sauvegarde — .zip complet et restaurable : tout le code (dont tools/ et docs/),
#   hors data/ et legacy/ ; rotation des 15 plus récents (BACKUP_KEEP)
python3 tools/export_project.py --backup
```

Le nom du `.txt`/`.zip` généré inclut le périmètre exporté (`full` par défaut, ou le tag
`canicule`, `snow`, `outline_*` ou `--only`), pour s'identifier sans avoir à l'ouvrir. Les
modes par application sont utilisables avec `--ai` (implicite) ou `--outline`, et ne se
combinent pas avec `--only`.
L'export neige reprend `SNOW_APP_VERSION` ; les autres profils reprennent `APP_VERSION`.

Le profil `--outline` conserve intégralement `CODEMAP.md` et `CONVENTIONS.md` ; toutes les
règles de `CLAUDE.md` restent présentes mais leurs longues justifications sont bornées. Pour
chaque module Python, il ne garde que le rôle du module, ses dépendances, les constantes et
l'API publiques, avec signatures et docstrings : les corps de fonctions et helpers privés
sont volontairement absents. Les workflows sont réduits à leurs jobs, étapes,
actions et cadences ; les autres documents à leurs titres et puces. C'est le profil adapté
pour discuter architecture, fonctionnalités, améliorations ou optimisations avec un chat qui
n'a pas besoin d'écrire lui-même le code.

### Export ciblé par domaine (`--only`)

Réduit le volume envoyé à l'IA quand la question ne porte que sur un domaine — `CLAUDE.md`
et `docs/` (CODEMAP, CONVENTIONS) restent toujours inclus en profil `--ai`. Cumulable
(répéter `--only` ou séparer par des virgules).

```sh
# Pipeline de collecte (racine)
python3 tools/export_project.py --only Forecast.py,Forecast_legacy.py,validate_cross_pipeline.py,run_dual.py,config.py

# Couche runtime / accès données (app/runtime.py, app/data/)
python3 tools/export_project.py --only apps/canicule/app/runtime.py,apps/canicule/app/data

# Couche stats (app/stats/)
python3 tools/export_project.py --only apps/canicule/app/stats

# Couche UI transverse (app/ui/)
python3 tools/export_project.py --only apps/canicule/app/ui

# Pages du dashboard (app/pages/) — ou une page précise
python3 tools/export_project.py --only apps/canicule/app/pages
python3 tools/export_project.py --only apps/canicule/app/pages/overview.py

# Domaine métier (app/domains/<nom>/) — ex. canicule
python3 tools/export_project.py --only apps/canicule/app/domains/heatwave

# Outils de non-régression / export (tools/) — hors périmètre du profil --ai par défaut
python3 tools/export_project.py --only tools

# Combiner plusieurs périmètres (ex. un domaine + la couche stats dont il dépend)
python3 tools/export_project.py --only apps/canicule/app/domains/heatwave,apps/canicule/app/stats

# App neige complète, ou code mutualisé core/
python3 tools/export_project.py --only apps/snow,snow_app.py
python3 tools/export_project.py --only core
```

### Vérifs rapides des artefacts

```sh
ls -1 Export/
head -30 "$(ls -t Export/*.txt | head -1)"        # manifeste + sommaire du dernier .txt
unzip -l "$(ls -t Export/*.zip | head -1)"          # contenu du dernier .zip
```

### Sauvegarde automatique (hook git, optionnel)

Zip à chaque commit via `.git/hooks/post-commit` (non versionné) :

```sh
#!/bin/sh
python3 tools/export_project.py --backup >/dev/null 2>&1 || true
```

> Le hook capture l'arbre **au moment du commit** ; le travail non commité n'est pas
> sauvegardé (utiliser `--backup` à la main pour figer un état en cours).

---

## Utilitaires ponctuels

```sh
# One-off historique : rétro-remplissage du parquet depuis les xlsx (avant PIPELINE_LIVE_SINCE)
python3 tools/migrate.py
```
