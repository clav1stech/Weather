# CODEMAP — carte du projet

> Pour un agent IA : lire ce fichier + `CLAUDE.md` (invariants) suffit à se
> repérer. Ne charger ensuite QUE les modules concernés par la tâche.

## Vue d'ensemble

Deux sous-systèmes indépendants, reliés uniquement par `config.py` et le
parquet :

```
┌── PIPELINE (racine, SENSIBLE — collecte des données) ──────────────────┐
│ Forecast.py            API Open-Meteo → data/database_paris.parquet    │
│ forecast_t2m_hd.py     API Forecast (Tx/Tn HD, flux annexe 7 j)        │
│                        → data/database_paris_t2m.parquet               │
│ fetch_observations.py  API Météo-France DPObs (obs 4 stations Paris,   │
│                        clé via env METEOFRANCE_API_KEY uniquement)     │
│                        → data/database_paris_observations.parquet      │
│ Forecast_legacy.py     scrape Météociel → legacy/*.xlsx (0Z/12Z)       │
│ validate_cross_pipeline.py   contrôle croisé OM ↔ legacy               │
│ run_dual.py            orchestrateur manuel (bouton dashboard)         │
│ .github/workflows/run_forecast.yml   cron 2h (API) + 0Z/12Z (legacy)   │
│                        + cron horaire (observations MF)                │
└─────────────────────────────────────────────────────────────────────────┘
                    │ écrit                         ▲ lit (lecture seule)
                    ▼                               │
        data/database_paris.parquet          legacy/*.xlsx
                    │ lit (lecture seule, sauf import ciblé encadré)
                    ▼
┌── DASHBOARD (package app/, refactorable librement) ─────────────────────┐
│ meteo_app.py           point d'entrée Streamlit : set_page_config,      │
│                        sidebar, routage — RIEN d'autre                  │
│ app/runtime.py         IS_LOCAL, LOCAL_TZ, VAR, user_tz                 │
│ app/data/              accès parquet + sélections de runs               │
│ app/stats/             statistiques d'ensemble génériques               │
│ app/ui/                thème + graphiques + composants génériques       │
│ app/domains/<nom>/     UN PHÉNOMÈNE MÉTIER = un sous-package            │
│ app/pages/             pages transverses                                │
└─────────────────────────────────────────────────────────────────────────┘
```

**Règle de sécurité absolue** : le pipeline (racine) est la partie critique —
si la collecte casse à un instant T, les données de cet instant sont perdues à
jamais. On ne le modifie qu'avec une raison forte, jamais « au passage » d'un
refactor du dashboard. Le dashboard, lui, peut être réorganisé : les données
restent intactes.

**Autres emplacements** :
- `tools/` — utilitaires hors exploitation : harnais de non-régression
  (`check_non_regression.py`, `ui_snapshot.py`), `export_project.py` (snapshot
  texte du code pour analyse externe), `migrate.py` (one-off historique de
  rétro-remplissage xlsx → parquet, conservé comme référence — ne plus lancer,
  passer par l'import ciblé du dashboard).
- `lancer_dashboard_PC.bat` / `lancer_dashboard_Mac.command` — lanceurs
  double-clic locaux (forcent `WEATHER_LOCAL=1`).
- `data/backups/` — copies datées du parquet créées avant tout import legacy
  (gitignoré : locales + OneDrive, jamais poussées sur GitHub).

## Carte des modules du dashboard

| Module | Responsabilité | Fonctions clés |
|---|---|---|
| `meteo_app.py` | entrée : page config, sidebar, routage, `APP_VERSION` | `main` |
| `app/runtime.py` | contexte : local/cloud, fuseaux, variable principale | `IS_LOCAL`, `LOCAL_TZ`, `VAR`, `user_tz` |
| `app/data/db.py` | lecture parquet, conversion TZ, liste des runs | `load_db`, `list_runs`, `run_slice`, `utc_cycle`, `run_label_text` |
| `app/data/runsets.py` | POLITIQUES de sélection de runs (3, volontairement distinctes) + backfill convergence | `latest_complete_run_sub`, `latest_run_sub`, `previous_runs_sub`, `completed_pooled_sub`, `trend_daily_medians`, `main_labels_expected_at`, `latest_refresh_status` |
| `app/data/presence.py` | diagnostic présence OM & legacy (lecture seule) | `openmeteo_presence`, `legacy_presence`, `_missing_by_run`, `legacy_signature` |
| `app/data/legacy_import.py` | import ciblé xlsx → parquet (seule ÉCRITURE, ultra-encadrée) | `legacy_import_candidates`, `import_legacy_run` |
| `app/data/t2m.py` | lecture du parquet Tx/Tn HD (flux annexe, dégradation silencieuse) | `t2m_signature`, `load_t2m`, `txtn_by_day` |
| `app/data/observations.py` | lecture du parquet observations MF (flux annexe, dégradation silencieuse) | `obs_signature`, `load_obs`, `latest_obs`, `obs_window`, `daily_txtn_obs` |
| `app/stats/ensemble.py` | stats génériques tolérantes NaN sur un pool de membres | `super_ensemble`, `model_data`, `model_medians`, `divergence`, `daily_aggregate`, `daily_risk`, `var_median` |
| `app/stats/tables.py` | tables d'export larges (onglet 🧾) | `enriched_super_table`, `model_table` |
| `app/stats/climato.py` | normale saisonnière cosinus (ajustable en session) | `clim_normal`, `clim_params`, `clim_z500_normal` |
| `app/ui/theme.py` | thème clair/sombre, CSS global, couleurs | `_plotly_template`, `_ink`, `_rgba`, `GLOBAL_CSS` |
| `app/ui/charts.py` | graphiques Plotly génériques | `fan_chart`, `spaghetti_chart`, `models_median_chart`, `divergence_chart`, `spread_chart`, `z500_median_chart` |
| `app/ui/components.py` | composants Streamlit réutilisables | `_kpi_card`, `complete_runs_caption` |
| `app/domains/__init__.py` | REGISTRE des domaines (navigation) | `DOMAIN_PAGES` |
| `app/domains/heatwave/` | domaine canicule : `logic.py` (paliers/labels), `charts.py`, `page.py` | `page_grand_public`, `tendance_recente`, `signal_synoptique` |
| `app/domains/observations/` | domaine observations MF : ICU inter-stations, prévu vs observé | `page_observations`, `ecart_icu_series`, `comparaison_prevu_observe` |
| `app/pages/overview.py` | Vue d'ensemble (KPI config-driven `KPI_*`) | `page_overview` |
| `app/pages/explore.py` | Explorer un run (onglets + tables d'export) | `page_explore` |
| `app/pages/convergence.py` | Révisions & convergence run-à-run | `page_convergence` |
| `app/pages/diagnostic.py` | Contrôle de présence des modèles | `page_diagnostic` |
| `app/pages/pipeline.py` | Lancer le pipeline (local), import legacy, log croisé | `page_run` |

**Sens des dépendances** (jamais l'inverse) :
`runtime` ← `data/db` ← `stats` ← `data/runsets` ← {`ui/charts`, `pages`, `domains`}.
`config.py` est importable partout ; `pages`/`domains` n'importent jamais entre eux.

## Flux de données (dashboard)

1. `db_signature()` (mtime du parquet) sert de clé de cache : tout
   `@st.cache_data` prend `_sig` en 1er argument → un nouveau run invalide tout.
2. `load_db(sig)` charge la base plate et convertit UTC → heure de Paris
   (tz-naïf). Tout le dashboard travaille en heure de Paris ; `utc_cycle()`
   restitue le vrai cycle synoptique quand il faut raisonner en 0/6/12/18Z.
3. Une page choisit un POOL de runs via `app/data/runsets.py` (3 politiques,
   voir CLAUDE.md § Vues combinées), puis passe le pool aux stats
   (`super_ensemble`…), puis aux graphiques (`app/ui/charts.py` ou
   `app/domains/<x>/charts.py`).
4. Contrat d'une page : `page_xxx(runs, sig)` — `runs` = `list_runs(sig)`.

## Ajouter un domaine météo (ex. neige/ski) — checklist

1. **Config** : ajouter les paramètres physiques dans `config.py` (section
   dédiée, ex. `SEUIL_NEIGE_*`) et, si nouvelle variable, une ligne dans
   `VARIABLES` (le pipeline et le schéma parquet suivent tout seuls).
2. **Créer** `app/domains/<nom>/` avec :
   - `logic.py` — seuils d'interprétation, labels, calculs propres au domaine
     (réutiliser `app/stats/` au maximum : `daily_risk(sub, seuil)` est
     générique, seuls les seuils/labels sont du domaine) ;
   - `charts.py` — graphiques propres au domaine (thème via `app/ui/theme.py`) ;
   - `page.py` — `page_<nom>(runs, sig)`, qui choisit son pool de runs dans
     `app/data/runsets.py`.
3. **Enregistrer** la page dans `app/domains/__init__.py` (`DOMAIN_PAGES`,
   une entrée). C'est la SEULE modification hors du nouveau sous-package.
4. **Ne pas** modifier les domaines existants, `app/pages/`, ni le pipeline.
5. Vérifier la non-régression des pages existantes (voir ci-dessous) —
   le nouveau domaine, lui, s'ajoute au golden UI à la prochaine capture.

Ajouter un modèle ou une variable = une ligne dans `config.py`
(`MODELS`/`VARIABLES`) — aucune logique à toucher (invariant config-driven).

## Invariants & pièges

**La source de vérité des invariants est `CLAUDE.md`** (intégrité des données,
historique des runs, runs partiels/fraîcheur, vues combinées, NaN, cohérence
temporelle). Correspondance code :

| Invariant (CLAUDE.md) | Où il vit |
|---|---|
| Fusion (run_date, modèle), écriture atomique, anti-régression | `Forecast.py` (`persist`, `_drop_regressions`) |
| Fraîcheur empirique échéance par échéance | `Forecast.py` (`mask_stale_tail`, `FRESHNESS_EPS`) |
| Portée réelle CONTIGUË, persistance conditionnée | `Forecast.py` (`_contiguous_reach_h`, `filter_fresh_rows`) |
| Horizon plein empirique (vues combinées) | `app/data/runsets.py` (`latest_complete_run_sub`) |
| Backfill modèles principaux seulement, échéances à venir | `app/data/runsets.py` (`completed_pooled_sub`) |
| `cycles` ≠ `expected_cycles` (alerte vs capacité) | `config.py` + `app/data/runsets.py` (`main_labels_expected_at`) |
| Import legacy = absence avérée uniquement | `app/data/legacy_import.py` |
| Tables d'export volontairement larges | `app/stats/tables.py` |
| Clé API MF via env only, dédup (station, validity_time), K→°C au parsing | `fetch_observations.py` |
| Obs : dégradation silencieuse, groupes ICU explicites, prévu-vs-observé jours complets | `app/data/observations.py`, `app/domains/observations/` |

## Non-régression — comment vérifier

Deux harnais en lecture seule, à exécuter depuis la racine (Anaconda Python) :

```
python tools/check_non_regression.py capture   # AVANT une modification : fige la référence
python tools/check_non_regression.py check     # APRÈS : 35+ sorties de calcul identiques ?
python tools/ui_snapshot.py capture            # idem pour le RENDU des 6 pages (AppTest)
python tools/ui_snapshot.py check
```

Les références (`tools/golden/*.json`) dépendent du contenu de la base au
moment de la capture : **capture et check doivent encadrer la modification
sans exécution du pipeline entre les deux** (sinon re-capturer). Elles ne sont
pas versionnées. Les KPI dépendant de l'heure courante, éviter de franchir une
heure pleine entre capture et check (sinon quelques diffs d'horodatage
légitimes à inspecter à la main).
