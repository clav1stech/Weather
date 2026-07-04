# Dashboard Météo — Prévisions d'ensemble T850 à Paris

Dashboard Streamlit qui suit les prévisions d'ensemble de température à
Paris (48.86°N, 2.33°E) à partir de plusieurs modèles météo, avec un
pipeline de collecte automatisé et un contrôle croisé contre une source
indépendante.

## Aperçu

- **Pipeline** : interroge l'API Open-Meteo Ensemble toutes les 2h, fusionne
  les nouveaux runs dans une base plate unique (`data/database_paris.parquet`)
  sans jamais perdre d'historique.
- **Dashboard** : lit cette base et l'expose via des pages Streamlit (vue
  d'ensemble, indicateur de canicule, exploration d'un run, convergence des
  runs, contrôle de présence des modèles).
- **Contrôle croisé** : un scraper legacy (Météociel) sert de source
  indépendante pour valider les données Open-Meteo aux cycles 0Z/12Z.
- **Observations en direct** : quatre stations Météo-France parisiennes
  (flux annexes horaire + 6 min), avec un bouton public « Voir un aperçu
  instantané » qui interroge l'API en direct sans jamais écrire en base
  (cf. § Secrets ci-dessous).

## Modèles suivis

| Modèle | Origine | Statut |
|---|---|---|
| ECMWF (IFS ENS) | ECMWF | principal |
| AIFS | ECMWF (IA) | principal |
| GEFS | NOAA | principal |
| GEM | ECCC | d'appoint (0Z/12Z uniquement) |

Chaque run couvre un horizon de 16 jours en résolution horaire.

## Installation

```bash
pip install -r requirements.txt
```

Nécessite Python 3.x avec `streamlit`, `plotly`, `pandas`, `numpy`,
`requests`, `pyarrow`, `matplotlib`, `beautifulsoup4`, `openpyxl`.

## Lancer le dashboard

```bash
streamlit run meteo_app.py
```

Ou double-clic sur `lancer_dashboard_Mac.command` (Mac) /
`lancer_dashboard_PC.bat` (PC), qui forcent le mode local
(`WEATHER_LOCAL=1`, active la page « Lancer le pipeline »).

## Lancer le pipeline manuellement

```bash
python Forecast.py              # fetch API Open-Meteo → parquet
python Forecast_legacy.py       # scrape Météociel → xlsx (0Z/12Z auto ; 6Z/18Z en manuel)
python validate_cross_pipeline.py   # contrôle croisé OM ↔ legacy
python run_dual.py               # orchestrateur manuel (bouton dashboard)
```

En production, `.github/workflows/run_forecast.yml` automatise ces
étapes : fetch API toutes les 2h, scrape + contrôle croisé aux créneaux
réels 0Z/12Z de Météociel.

## Structure du projet

```
Forecast.py                 pipeline : API Open-Meteo → data/database_paris.parquet
Forecast_legacy.py          scraper Météociel → legacy/*.xlsx
validate_cross_pipeline.py  contrôle croisé OM ↔ legacy
run_dual.py                 orchestrateur manuel
config.py                   configuration centrale (modèles, variables, seuils, climato)
meteo_app.py                point d'entrée Streamlit (routage/sidebar uniquement)
app/
  runtime.py                 contexte (local/cloud, fuseaux, variable principale)
  data/                       accès parquet, sélection de runs, import legacy
  stats/                      statistiques d'ensemble (tolérantes NaN), climatologie
  ui/                         thème, composants, graphiques génériques
  services/                   intégrations externes (ex. aperçu en direct API Météo-France)
  domains/<nom>/              un phénomène météo = un domaine (ex. canicule)
  pages/                      pages transverses (vue d'ensemble, exploration…)
tools/                       harnais de non-régression, export du projet
docs/
  CODEMAP.md                  carte détaillée du code
  CONVENTIONS.md               règles de style
```

Pour une carte complète du code, voir [`docs/CODEMAP.md`](docs/CODEMAP.md).
Les invariants du projet (intégrité des données, historique des runs,
détection de fraîcheur, vues combinées…) sont documentés dans
[`CLAUDE.md`](CLAUDE.md).

## Secrets (Streamlit Cloud)

La page « Observations en direct » propose un bouton public « Voir un aperçu
instantané » : il interroge l'API Météo-France 6 min **en direct** et affiche
le résultat une seule fois, sans jamais l'écrire en base — la base réelle
(`data/database_paris_observations_6m.parquet`) continue de se réactualiser
uniquement via le cron GitHub Actions habituel (≤ 15 min). Nécessite le même
secret que le pipeline, mais côté app cette fois, dans les réglages Streamlit
Cloud (Settings → Secrets), **jamais** dans `.env` ni versionné :

```toml
METEOFRANCE_API_KEY = "..."
```

En son absence, le bouton affiche un message d'indisponibilité sans jamais
planter la page.

*Alternative dormante* : une implémentation par déclenchement à distance du
workflow (`workflow_dispatch`) existe dans le code
(`app/services/github_dispatch.py`) mais n'est plus appelée par la page —
conservée au cas où ce choix serait reconsidéré. Elle nécessiterait alors un
second secret, un PAT GitHub **fine-grained** scopé au seul dépôt `Weather`
avec uniquement la permission **Actions: write** :

```toml
GITHUB_DISPATCH_TOKEN = "github_pat_..."
```

## Non-régression

Avant/après tout refactor du dashboard :

```bash
python tools/check_non_regression.py capture   # avant modification
python tools/check_non_regression.py check     # après : doit être 100 % identique
python tools/ui_snapshot.py capture
python tools/ui_snapshot.py check
```

## Données

- `data/database_paris.parquet` : base plate unique, stockage en UTC
  tz-naïf, un modèle par (run_date, modèle).
- `legacy/*.xlsx` : archives Météociel, en lecture seule — dernier recours
  en cas de corruption du parquet.

⚠️ Ces données sont irremplaçables : voir `CLAUDE.md` § Invariants avant
toute opération d'écriture.
