# Instructions Claude

## Langue
- Toujours répondre en **français**, sauf si l'utilisateur demande explicitement une autre langue.

## Vue d'ensemble du projet
Dashboard météo (Streamlit) des prévisions d'ensemble T850 à Paris.
- `Forecast.py` : pipeline API Open-Meteo → base plate unique `data/database_paris.parquet`.
- `meteo_app.py` : dashboard, recalcule toutes les stats à la volée depuis la base.
- `config.py` : **point de configuration central** (modèles, variables, seuils, climatologie).

## Invariants à NE JAMAIS casser

### Historique des runs (ne pas écraser)
- **Jamais de perte d'historique.** La fusion (`persist`) se déduplique par couple **(run_date, modèle)**, jamais par `run_date` seul. Un modèle absent d'un fetch **conserve** son run antérieur intact.
- **Écriture atomique obligatoire** : écrire dans un `.tmp` puis `os.replace` — jamais d'écriture directe dans le parquet (pas d'état partiel sur le disque).
- **Valider avant d'écrire** (`_validate`) : ne jamais persister un run vide ou sans valeur valide.

### Détection des runs partiels / fraîcheur
- La distinction « échéances réellement renouvelées » vs « queue recollée de l'ancien cycle » est **empirique et échéance par échéance** (`mask_stale_tail`, seuil `FRESHNESS_EPS`). **Ne pas** la remplacer par une table d'horizons codée en dur ni par une troncature par horizon nominal — l'horizon réel d'un cycle varie d'un jour à l'autre.
- L'identité du cycle (`run_date`) se déduit de la **dernière échéance publiée** (init + horizon), pas de la première (rebouchée par l'API depuis 00:00 local).
- Les garde-fous `RUN_SNAP_TOLERANCE_H` / `RUN_INFER_MAX_SHIFT_H` ne doivent que **corriger vers un cycle voisin** (ex. 12Z→06Z), jamais téléporter. En cas d'ambiguïté → repli sur la détection horloge.
- **Chaque modèle a son propre `run_date`** dans un même fetch (cycles différents). Ne jamais supposer un cycle global partagé.

### Modèles principaux vs modèles d'appoint
- Les modèles d'appoint (`main: False`, ex. GEM) ne sont **jamais backfillés** et n'existent qu'à leurs cycles réels (`cycles` dans config). Comparaison cycle-à-cycle uniquement.
- Le backfill inter-runs du dashboard (`completed_pooled_sub`) ne concerne que les modèles **principaux**, échéance par échéance, jusqu'à `n-3`.

### Robustesse NaN / horizon 16 j
- Toutes les statistiques restent **tolérantes aux NaN** (`skipna`) : l'horizon 16 j doit s'afficher proprement même quand les membres se raréfient (~7,5 j).

### Cohérence temporelle
- Stockage en **UTC tz-naïf** ; conversion vers l'heure de Paris **seulement à l'affichage**. Les `run_date` portent le vrai cycle synoptique (0/6/12/18Z), jamais l'heure locale.

## Conventions de travail
- **Config-driven** : tout réglage variable (modèle, variable, seuil, climato) se déclare dans `config.py` — une ligne suffit, sans toucher la logique de parsing/stockage/affichage.
- **Préserver la densité de commentaires** : le code est richement documenté en français (le *pourquoi*, pas seulement le *quoi*). Maintenir ce niveau, et expliquer les invariants subtils.
- **Schéma parquet stable** : ne pas casser la compatibilité de `C.SCHEMA` (`load_db` filtre déjà les modèles legacy orphelins).
- **Contrôle croisé legacy** : médiane-vs-médiane (pas det-vs-det), limité aux runs 0Z/12Z (cf. `validate_cross_pipeline.py`).
- **Git / fichiers** : ne pas committer sans demande explicite. `Export/`, `.venv/`, `__pycache__/` ne sont jamais versionnés.
