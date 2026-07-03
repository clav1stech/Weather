# Commandes du projet

Mémo unique des commandes utiles (lancement, pipeline, non-régression, export).
Toutes se lancent depuis la **racine** du dépôt. Environnement : dépendances dans
`requirements.txt` (`pip install -r requirements.txt`).

---

## Dashboard (Streamlit)

```sh
# Lancer le dashboard en local
streamlit run meteo_app.py
```

`meteo_app.py` est le point d'entrée (routage/sidebar) ; tout le code vit dans `app/`.

---

## Pipeline de collecte

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

## Non-régression (obligatoire pour tout refactor dashboard)

Deux harnais **en lecture seule** sur les données. Protocole : `capture` **AVANT**
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

Deux profils. Artefacts dans `Export/` (**gitignoré** — jamais commité).

```sh
# Profil IA (défaut) — .txt curé : code + doc .md + manifeste (git, version, ~tokens, sommaire)
#   exclut tools/ et Forecast_legacy.py
python3 tools/export_project.py
python3 tools/export_project.py --ai

# Profil sauvegarde — .zip complet et restaurable : tout le code (dont tools/ et docs/),
#   hors data/ et legacy/ ; rotation des 15 plus récents (BACKUP_KEEP)
python3 tools/export_project.py --backup
```

Le nom du `.txt`/`.zip` généré inclut le périmètre exporté (`full` par défaut, ou le tag
`--only`), pour s'identifier sans avoir à l'ouvrir.

### Export ciblé par domaine (`--only`)

Réduit le volume envoyé à l'IA quand la question ne porte que sur un domaine — `CLAUDE.md`
et `docs/` (CODEMAP, CONVENTIONS) restent toujours inclus en profil `--ai`. Cumulable
(répéter `--only` ou séparer par des virgules).

```sh
# Pipeline de collecte (racine)
python3 tools/export_project.py --only Forecast.py,Forecast_legacy.py,validate_cross_pipeline.py,run_dual.py,config.py

# Couche runtime / accès données (app/runtime.py, app/data/)
python3 tools/export_project.py --only app/runtime.py,app/data

# Couche stats (app/stats/)
python3 tools/export_project.py --only app/stats

# Couche UI transverse (app/ui/)
python3 tools/export_project.py --only app/ui

# Pages du dashboard (app/pages/) — ou une page précise
python3 tools/export_project.py --only app/pages
python3 tools/export_project.py --only app/pages/overview.py

# Domaine métier (app/domains/<nom>/) — ex. canicule
python3 tools/export_project.py --only app/domains/heatwave

# Outils de non-régression / export (tools/) — hors périmètre du profil --ai par défaut
python3 tools/export_project.py --only tools

# Combiner plusieurs périmètres (ex. un domaine + la couche stats dont il dépend)
python3 tools/export_project.py --only app/domains/heatwave,app/stats
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
