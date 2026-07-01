# Instructions Claude

## Général
- Toujours répondre en **français**, sauf si l'utilisateur demande explicitement une autre langue.
- Développement avec Claude Code dans VSC sur PC ou Mac.

## Tenir ce fichier à jour
- Dès que tu **détectes une règle générale** du projet (invariant, convention, contrainte, préférence récurrente de l'utilisateur, piège à éviter), **ajoute-la ici** dans la section adéquate — ne la laisse pas seulement dans un commentaire de code ou dans l'échange.
- Ne consigner que les règles **durables et générales**, pas un détail ponctuel propre à une seule tâche. En cas de doute sur la portée, demander avant d'inscrire.
- Préférer **mettre à jour** une consigne existante plutôt que d'en empiler une quasi-identique ; garder ce fichier concis et sans doublon.

## Vue d'ensemble du projet
Dashboard météo (Streamlit) des prévisions d'ensemble T850 à Paris.
- `Forecast.py` : pipeline API Open-Meteo → base plate unique `data/database_paris.parquet`.
- `meteo_app.py` : dashboard, recalcule toutes les stats à la volée depuis la base.
- `config.py` : **point de configuration central** (modèles, variables, seuils, climatologie).

## Invariants à NE JAMAIS casser

### Intégrité des données (règle absolue de non-régression)
- **Tout le projet repose sur ces données** : leur perte est irréversible. Aucune opération ne doit jamais risquer d'altérer, dégrader ou perdre le contenu de `data/database_paris.parquet` ou des xlsx legacy.
- **Données legacy (xlsx Météociel, `Forecasts/`) : intouchables.** C'est l'assurance-vie du projet, le dernier recours en cas de corruption du parquet. Ne **jamais** les modifier, réécrire, régénérer ni remplacer (y compris par les données d'un autre run) — quelle que soit la raison, même pour « corriger » une incohérence apparente. Lecture seule, toujours.
- **Le parquet ne peut être corrigé que sur demande explicite de l'utilisateur**, suite à un problème identifié et discuté — jamais de correction spontanée, même évidente. Toute correction commence par une **sauvegarde préalable** du fichier (copie datée avant modification).
- **Toute édition du parquet doit prouver sa non-régression** : vérifier avant/après que les données existantes ne sont pas altérées et qu'aucun run correctement récupéré n'est remplacé par des données de moindre qualité (portée réduite, valeurs manquantes, run fantôme). En cas de doute sur l'effet d'une écriture → ne pas écrire, demander.

### Historique des runs (ne pas écraser)
- **Jamais de perte d'historique.** La fusion (`persist`) se déduplique par couple **(run_date, modèle)**, jamais par `run_date` seul. Un modèle absent d'un fetch **conserve** son run antérieur intact.
- **Écriture atomique obligatoire** : écrire dans un `.tmp` puis `os.replace` — jamais d'écriture directe dans le parquet (pas d'état partiel sur le disque).
- **Valider avant d'écrire** (`_validate`) : ne jamais persister un run vide ou sans valeur valide.

### Détection des runs partiels / fraîcheur
- La distinction « échéances réellement renouvelées » vs « queue recollée de l'ancien cycle » est **empirique et échéance par échéance** (`mask_stale_tail`, seuil `FRESHNESS_EPS`). **Ne pas** la remplacer par une table d'horizons codée en dur ni par une troncature par horizon nominal — l'horizon réel d'un cycle varie d'un jour à l'autre.
- L'identité du cycle (`run_date`) se déduit de la **dernière échéance publiée** (init + horizon), pas de la première (rebouchée par l'API depuis 00:00 local).
- Les garde-fous `RUN_SNAP_TOLERANCE_H` / `RUN_INFER_MAX_SHIFT_H` ne doivent que **corriger vers un cycle voisin** (ex. 12Z→06Z), jamais téléporter. En cas d'ambiguïté → repli sur la détection horloge.
- **Chaque modèle a son propre `run_date`** dans un même fetch (cycles différents). Ne jamais supposer un cycle global partagé.
- **La portée réelle d'un run est CONTIGUË** (`_contiguous_reach_h`, seuil `PERSIST_MAX_GAP_H`) : dernière échéance valide atteignable depuis `run_date` sans trou > 24 h entre échéances valides successives, en ignorant le passé rebouché (`valid_time < run_date`). Ne jamais la remplacer par un simple `max(valid_time) − run_date` : une réponse creuse de l'API (heures rebouchées en tête + point parasite isolé en queue, tout le reste NaN) simulerait une portée pleine, serait persistée comme run fantôme (ligne droite sur les graphes) PUIS bloquerait le vrai run via la garde anti-régression. Cette métrique unique sert aux deux garde-fous ci-dessous.
- **Ne persister un run frais que s'il est complet** (`filter_fresh_rows`/`_meets_persist_horizon` dans Forecast.py) : portée réelle contiguë ≥ `horizon_h − PERSIST_HORIZON_TOLERANCE_H` (ou `MIN_PERSIST_HORIZON_H` si `horizon_h` inconnu, ex. GEM), sinon laissé de côté (ancien run complet conservé, comme un cycle inchangé). Trois causes distinctes, même traitement : un modèle à horizon plein 4×/j (AIFS, GEFS) juste **en cours de calcul** (se résout au poll suivant), un cycle **nativement plus court** par construction (ex. ECMWF ENS à 6Z/18Z ≈ 144 h, jamais 360 h — ce cycle ne sera donc simplement **jamais persisté**, volontairement), ou une **réponse creuse** de l'API (portée contiguë ≈ 0). On continue de checker le modèle à chaque poll (Metadata API + heuristique) ; seule la persistance est conditionnée.
- **Ne jamais régresser un (run_date, modèle) déjà en base** : `persist()` appelle `_drop_regressions` en dernier rempart, indépendant de `filter_fresh_rows` — si `fresh` contient un couple (run_date, modèle) déjà stocké mais avec une portée réelle contiguë INFÉRIEURE à celle déjà en base (glitch API, réponse tronquée à un poll), ce couple est écarté de `fresh` avant fusion : le run existant, plus complet, n'est jamais remplacé par une version régressive. Un `run_date` nouveau pour ce modèle n'est pas concerné (rien à régresser, c'est `filter_fresh_rows` qui juge sa complétude).

### Modèles principaux vs modèles d'appoint
- Les modèles d'appoint (`main: False`, ex. GEM) ne sont **jamais backfillés** et n'existent qu'à leurs cycles réels (`cycles` dans config). Comparaison cycle-à-cycle uniquement.
- Le backfill inter-runs du dashboard (`completed_pooled_sub`) ne concerne que les modèles **principaux**, échéance par échéance, jusqu'à `n-3`. Il ne comble QUE les échéances **à venir** (`valid_time ≥ cycle du run`) : les heures antérieures au cycle (rebouchées par l'API depuis 00:00 local, aussi servies par le run précédent) sont du passé, jetées par la convergence — les backfiller ferait apparaître à tort presque tous les runs comme « complétés » par un ancien (bruit massif en grille horaire OM).
- **`cycles` ≠ `expected_cycles`** : `cycles` liste tous les cycles où un modèle **peut** publier (utilisé par le pipeline pour savoir quand appeler l'API). `expected_cycles` liste les cycles où son absence est une **anomalie à alerter** — ex. ECMWF tourne à 0/6/12/18Z mais n'est *requis complet* qu'à 0Z et 12Z ; son absence à 6Z/18Z n'est pas une anomalie. Les alertes d'interface (`latest_refresh_status`, `page_explore`, `page_convergence`, `_missing_by_run`) utilisent toujours `expected_cycles` (`C.EXPECTED_CYCLES_BY_LABEL`), jamais `cycles`.

### Vues combinées (super-ensemble global)
- Les vues **combinées** (Vue d'ensemble, Indicateur de canicule — **pas** *Explorer un run* ni *Convergence*) poolent, pour **chaque modèle, son dernier run à HORIZON PLEIN** (`latest_complete_run_sub`), chacun gardant son propre cycle. La complétude se mesure **empiriquement** sur la portée réelle du run stocké (`max valid_time − run_date ≥ horizon_h − FULL_HORIZON_TOLERANCE_H`) — **jamais** par une règle codée en dur sur l'heure de cycle : un 6Z/18Z réellement long est éligible, un 0Z/12Z anormalement court est écarté. Modèle sans `horizon_h` (GEM) → dernier run non vide ; aucun run à horizon plein → repli sur le dernier non vide, signalé « horizon réduit ».
- La Vue d'ensemble peut être **rejouée à un cycle antérieur** (sélecteur « Vu depuis », param `as_of` de `latest_complete_run_sub`) : base filtrée `run_date ≤ cycle` puis même logique de sélection — la carte Tendance se compare toujours au jeu précédent **relatif à la version affichée**. La référence « présent » des KPI est alors le cycle choisi, jamais l'horloge.
- Exception voulue sur la page canicule : ses sections vulgarisées « évolution au fil des runs » et « confiance » (`trend_daily_medians`) réutilisent la **mécanique de la page Convergence** (super-ensembles complétés `completed_super_ensemble_daily`, filtre `_convergence_runs`), pas `latest_complete_run_sub` — comparer des runs entre eux exige des pools à modèles équivalents (backfill), ce que la sélection « dernier run complet » ne garantit pas. Ne pas « unifier » les deux.
- Les **KPI de la Vue d'ensemble** sont config-driven (`KPI_*` dans config.py) et calculés sur les **échéances à venir uniquement** (les heures passées rebouchées par l'API fausseraient prochaine échéance, pic, tendance, anomalie). Le « jour à risque » mêle **probabilité × sévérité** : proba journalière ≥ `KPI_RISK_PROB_MIN` OU dépassement attendu E[max(T − seuil, 0)] ≥ `KPI_RISK_EXCESS_MIN_C` (colonne `exces` de `daily_risk`) — ne pas le réduire à un seuil de proba seul, le second critère capte les queues chaudes à proba modeste.

### Explorer un run : « Dernier run » et tableaux d'export
- L'option « Dernier run » du sélecteur (`latest_run_sub`) poole le **dernier run non vide de chaque modèle, quel que soit son cycle et sans exigence d'horizon plein** — c'est voulu (fraîcheur maximale, même partielle) ; ne pas la confondre avec `latest_complete_run_sub` (vues combinées, horizon plein requis).
- Les tableaux de l'onglet 🧾 sont **volontairement larges** (destinés à l'export pour analyse par IA) : stats du super-ensemble + par modèle médiane, contrôle (member 0), nb de membres, Δ médiane vs run précédent — ne pas les « alléger » pour la lisibilité écran.
- Le « run précédent » des colonnes Δ (`previous_runs_sub`) se calcule **par modèle** (dernier run strictement antérieur de CE modèle), jamais via un cycle global partagé.

### Robustesse NaN / horizon 16 j
- Toutes les statistiques restent **tolérantes aux NaN** (`skipna`) : l'horizon 16 j doit s'afficher proprement même quand les membres se raréfient (~7,5 j).

### Cohérence temporelle
- Stockage en **UTC tz-naïf** ; conversion vers l'heure de Paris **seulement à l'affichage**. Les `run_date` portent le vrai cycle synoptique (0/6/12/18Z), jamais l'heure locale.

## Conventions de travail
- **Config-driven** : tout réglage variable (modèle, variable, seuil, climato) se déclare dans `config.py` — une ligne suffit, sans toucher la logique de parsing/stockage/affichage.
- **Préserver la densité de commentaires** : le code est richement documenté en français (le *pourquoi*, pas seulement le *quoi*). Maintenir ce niveau, et expliquer les invariants subtils.
- **Nature du commentaire, pas son histoire** : un commentaire doit être bref, concis et complet — il éclaire la nature du code (particularités, pièges, invariants, points d'attention), jamais sa genèse. Proscrire les commentaires « de circonstance » qui répondent à une discussion ou à un bug découvert avec l'IA (ex. « corrigé suite à... », « ajouté car Claude a détecté... ») : reformuler en constat intemporel sur le code lui-même.
- **Schéma parquet stable** : ne pas casser la compatibilité de `C.SCHEMA` (`load_db` filtre déjà les modèles legacy orphelins).
- **Contrôle croisé legacy** : médiane-vs-médiane (pas det-vs-det), limité aux runs 0Z/12Z (cf. `validate_cross_pipeline.py`). **Météociel ne publie QUE 0Z/12Z** — les cycles 6Z/18Z d'Open-Meteo n'ont légitimement aucun équivalent legacy (ne jamais les traiter comme une absence anormale).
- **Workflow GitHub Actions (`run_forecast.yml`)** : deux jobs à cadences distinctes, pas un seul cron couplé. `fetch-api` (Forecast.py) tourne toutes les 2h — c'est la source principale. `scrape-legacy` (Forecast_legacy.py) ne tourne qu'aux créneaux réels 0Z/12Z de Météociel (~midi/minuit heure de Paris) et enchaîne toujours avec le contrôle croisé (`validate_cross_pipeline.py`), car Météociel est **structurellement** la dernière source à finaliser un run 0Z/12Z (publication complète décalée de plusieurs heures) — le contrôle croisé n'a donc jamais de raison d'être déclenché ailleurs. `run_dual.py` n'est plus appelé par le workflow ; il ne sert plus qu'au lancement manuel (bouton dashboard). Les deux jobs partagent un `concurrency.group` pour sérialiser leurs push git (évite un conflit quand les deux tombent au même horaire, ex. 10Z/22Z).
- **Bascule pipeline (`config.PIPELINE_LIVE_SINCE`)** : avant cette date, la base Open-Meteo est **rétro-remplie depuis les xlsx Météociel** (`migrate.py`) → toute comparaison OM↔legacy y est circulaire, et GEM / les cycles 6Z-18Z n'y existent pas. Ne confronter les deux sources qu'à partir de cette date. La détection d'absence d'un modèle se cale, elle, sur sa **1re apparition réelle** dans la base (pas de date en dur par modèle).
- **Git / fichiers** : ne pas committer sans demande explicite. `Export/`, `.venv/`, `__pycache__/` ne sont jamais versionnés.
