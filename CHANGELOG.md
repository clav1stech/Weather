# Changelog

## [2.6.2] - 2026-07-19
Canicule : calendrier du risque lisible sur mobile (case compacte).

Sur un viewport de ~375 px, les 16 cases du calendrier font ~20 px chacune :
l'empilement Tx/Tn/glyphe ≈ se superposait en une ligne illisible. La case ne
montre plus que l'essentiel — couleur du risque (probabilité T850, inchangée)
et Tx au sol en chiffre nu (sans flèche, glyphe ni « ° », l'unité est dans la
légende) ; le détail (Tn, modèle, fiabilité) se lit au survol ou d'un tap sur
mobile. Rendu vérifié en 375 px et en desktop via captures navigateur
avant/après. Aucun changement du Statut canicule ni de ses calculs. (issue #16)

## [2.6.1] - 2026-07-19
Pipeline : relance isolée de chaque flux d'observation + note vintages corrigée.

La colonne ④ de la page « Lancer le pipeline » (locale) propose, sous le
bouton groupé existant, un bouton par flux (obs horaires, obs 6 min,
vintages) : les trois flux étant indépendants (parquets, crons et scripts
séparés), un diagnostic ciblé ne doit pas consommer les appels API des deux
autres. Côté doc, la note CLAUDE.md « aucun affichage dashboard branché »
du flux vintages était obsolète : le graphique de convergence Montsouris et
son tableau d'écarts sont branchés sur la page Observations. La frontière
public/local reste intacte (aucune collecte persistée depuis la page
publique ; l'aperçu live y est déjà attenant aux cartes temps réel).
(issue #21)

## [2.6.0] - 2026-07-19
Observations : fraîcheur du graphique inter-stations (message + prolongement 6 min).

Le flux horaire consolidé accuse structurellement quelques heures de retard
(délai de publication du paquet horaire côté API Météo-France — pas une
panne). Deux réponses d'affichage : un message indique jusqu'où va la donnée
horaire consolidée, et les courbes de température sont prolongées en
pointillé par les mesures 6 min plus fraîches (`obs_6m_depuis`), raccordées
au dernier point horaire — stations RADOME seules (Montsouris, Longchamp),
les stations ETENDU ne publiant pas ce flux. Fusion calculée au rendu
uniquement : l'écart ICU, les Tx/Tn journaliers et les min/max du jour
restent calculés sur l'horaire seul (frontière documentée dans CLAUDE.md).
(issue #22)

## [2.5.7] - 2026-07-19
Observations : horodatages « maintenant » en heure de Paris explicite.

L'aperçu instantané et les contrôles de fraîcheur de la page Observations
utilisaient `datetime.now()` nu — heure système du serveur, donc UTC sur
Streamlit Cloud, incohérente avec les autres horodatages de la page.
Horodatage du snapshot live et `now_local` (alertes de fraîcheur, borne du
jour civil des min/max provisoires) désormais calculés en `LOCAL_TZ`,
conformément à la convention du dashboard (stockage UTC, conversion à
l'affichage). Cooldown et donnée du snapshot inchangés. (issue #15)

## [2.5.6] - 2026-07-16
Convergence Montsouris : tableau d'écarts prévision − observé sous le graphique.

Pour chaque recul (dernière prévision, J−6 h, J−12 h, J−18 h, J−24 h) :
écart au dernier point observé, à l'instant du min observé et à l'instant
du max observé sur la fenêtre de 48 h. Les références min/max sont celles
de la température OBSERVÉE ; la prévision est comparée lissée, exactement
comme elle est tracée (lissage mutualisé dans `lisser_prevision`,
observations/logic.py, réutilisé par le graphique). Appariement au point
de prévision le plus proche à ± 10 min, sinon « — » — jamais de valeur
interpolée. Lecture seule des flux, `vintage_comparison_series` inchangé.
(issue #13)

## [2.5.5] - 2026-07-16
Observations : min/max provisoires du jour sur les cartes station.

Chaque carte « temps réel » affiche, sous la température, le min/max
observé depuis 00 h (heure de Paris) du jour civil en cours — ligne
discrète (caption), « — » si donnée absente, hauteur et alignement des
4 cartes inchangés. Calcul dédié `txtn_du_jour` (flux horaire seul, sans
exigence de complétude, valeur explicitement provisoire) — distinct de
`daily_txtn_obs`, réservé aux jours révolus quasi complets. Affichage
seul : aucune influence sur la détection canicule ni les KPI. (issue #12)

## [2.5.4] - 2026-07-16
Canicule : la durée d'épisode tolère les creux chauds d'un jour.

La durée affichée était calculée sur des séquences strictement consécutives
de jours ≥ 50 % de probabilité : un seul jour intermédiaire légèrement sous
ce seuil faisait retomber « Durée de l'épisode » de 6 jours à 1 jour du
jour au lendemain. Nouvelle fonction pure `episode_chaleur`
(heatwave/logic.py, testée dans tests/) : un jour sous le seuil de
probabilité mais dont la médiane reste ≥ seuil chaleur relie deux jours
confirmés sans couper l'épisode — les creux ne font que relier, jamais
étendre le début ou la fin. Le détail (jours confirmés vs creux) se lit au
survol de la métrique ; le badge Statut canicule garde sa définition
stricte, inchangée. (issue #14)

## [2.5.3] - 2026-07-16
Sidebar : suppression du compteur « Prévisions archivées » et bloc allégé.

Le nombre brut de runs en base grossit sans fin et ne dit rien de la
fraîcheur/fiabilité des données. La sidebar ne garde que l'essentiel :
dernier run, heure de rafraîchissement, statut complet/partiel, bouton
Rafraîchir ; les mentions statiques (mise à jour auto, sources, version)
sont regroupées en un seul bloc. (issue #11)

## [2.5.2] - 2026-07-09
CI : évite les conflits de push git sur les parquets binaires du pipeline.

Un `git pull --rebase` ne sait pas fusionner deux écritures concurrentes
d'un même fichier binaire (conflit non auto-résoluble, contrairement à du
texte) — risque rencontré en pratique malgré le `concurrency.group`
partagé, en particulier sur le job à cadence la plus rapprochée
(`fetch-observations-6m`, 15 min). Les 5 jobs dont le flux repose sur
`persist()`/dédup par clé (`fetch-api`, `fetch-t2m-hd`, `fetch-observations`,
`fetch-observations-6m`, `fetch-vintages`) ne tentent plus ce merge côté
git : en cas d'échec de push, ils repartent de l'état distant à jour et
relancent leur script de collecte, qui recalcule la fusion sur cette base
fraîche. `scrape-legacy` garde le `pull --rebase` (son contrôle croisé
n'est pas idempotent). Aucun changement de comportement du dashboard.

## [2.5.1] - 2026-07-08
Exclure Montsouris du calcul de l'écart ICU (page Observations).

Montsouris était classée station "aérée" mais reste en pratique proche des
stations urbaines la nuit, ce qui diluait l'écart ICU affiché. Nouvelle
valeur `"neutre"` pour le champ `icu` de `OBS_STATIONS` : Montsouris est
exclue du calcul d'écart (le groupe aéré ne comprend plus que Longchamp),
tout en restant affichée sur les graphiques et dans le texte explicatif.

## [2.5.0] - 2026-07-06
Performance : pages Convergence et Contrôle des runs nettement plus réactives, sans aucun changement de valeur affichée.

Le cœur lourd de la page Convergence (une passe de backfill inter-runs par run
affiché) est désormais mémoïsé (convergence_long) et calculé une seule fois par
donnée au lieu d'être recalculé à chaque interaction : les rendus successifs
passent de ~3 s à instantané. Ce calcul remplace en interne le rescan complet de
la base (run_slice, O(nombre total de lignes)) par un index local des lignes par
run construit une seule fois puis libéré — le 1er rendu est ~2× plus rapide et le
coût cesse de croître aussi vite avec l'historique. openmeteo_presence (balayage
de toute la base, utilisé par Convergence et Contrôle) est mis en cache. Sortie
strictement identique (non-régression calculs et rendu vérifiée à 100 %).

## [2.4.12] - 2026-07-06
Exclure les runs expirés de la carte des révisions run-à-run (Convergence) ; renommer le seuil "Canicule exceptionnelle" en "Canicule" sur le graphique d'évolution de la chaleur prévue.

Sur la page Convergence, la carte des révisions run-à-run (§3) n'affiche plus
en colonne les runs dont la dernière échéance prévue est déjà passée : le
delta de médiane reste calculé sur l'historique complet (chaque run comparé à
son vrai run précédent, même si celui-ci a depuis disparu de l'axe affiché),
seul l'affichage des colonnes est filtré après coup, et les lignes de dates
cibles devenues entièrement vides sont retirées. Sur la page canicule, le
libellé de seuil au-dessus du graphique "Évolution de la chaleur prévue"
passe de "Canicule exceptionnelle" à "Canicule".

## [2.4.11] - 2026-07-05
Ajouter Z500 par modèle et les variations T850 J-1/J-2 aux tableaux d'export d'Explorer un run.

Dans l'onglet 🧾 Tableaux, chaque modèle gagne une colonne de médiane Z500
(contexte synoptique, silencieusement absente si la variable n'est pas
disponible sur le run) ainsi que deux colonnes de variation de la médiane
T850 vs le run le plus proche de J-1 et J-2 (nouvelle fonction
`n_days_before_sub`, calquée sur `previous_runs_sub` mais ciblant une échéance
calendaire plutôt que le cycle précédent immédiat, avec repli sur le run
disponible le plus proche). Retire aussi de la page Observations la mention
des données manquantes par réseau, redondante avec le fait que humidité/vent/
pression ne sont de toute façon affichés que pour la station de référence.

## [2.4.10] - 2026-07-04
Ajouter un aperçu en direct des observations 6 min sur la page publique.

Bouton « Voir un aperçu instantané » sur la page Observations : interroge
l'API Météo-France 6 min à la demande et affiche le relevé directement dans
les cartes « temps réel » existantes (remplace les valeurs stockées dès qu'il
est plus frais, via `_champ_frais` généralisé à trois sources) — jamais écrit
en base, jamais persisté au-delà de la session du visiteur ; la base réelle
continue de se réactualiser uniquement par le cron GitHub Actions habituel.
Cooldown fichier (`app/services/cooldown.py`) avant tout appel réseau, garde-
fou contre un abus de clics d'un public anonyme sur l'API Météo-France.
Nécessite le secret `METEOFRANCE_API_KEY` côté Streamlit Cloud (cf. README).
Une alternative par déclenchement à distance du workflow GitHub Actions
(`app/services/github_dispatch.py`) a été explorée puis mise de côté (dormante,
non appelée) au profit de cette solution plus simple, sans PAT à gérer.

## [2.4.9] - 2026-07-04
Ajouter le graphique de convergence de la prévision Montsouris (vintages vs observé).

Dans le domaine observations, remplace la section « prévision Tx/Tn collait-elle
à la réalité » par un graphique de convergence : température observée 6 min à
Montsouris confrontée aux prévisions issues des vintages, émises à différents
reculs (dernière en date, puis 6/12/18/24 h plus tôt). Le lead 0 retombe sur le
vintage `bootstrap` (comblement initial) tant qu'aucun vintage `live` n'existe
encore pour une échéance donnée — le live reprend la main dès qu'il apparaît ;
les leads 6h+ restent réservés aux vrais vintages, jamais de repli. Les courbes
de prévision sont lissées (moyenne ~1 h) pour atténuer l'intermittence nocturne
propre au modèle en couche limite stable, sans toucher à la donnée stockée ni à
l'observé. Ajout pur au domaine `app/domains/observations/` (logic/charts/page),
aucune modification du moteur de collecte ni des schémas.

## [2.4.8] - 2026-07-04
Remplacer le flux instant 15 min par un flux vintages Montsouris (historique des révisions).

L'ancien flux `fetch_instant.py` (upsert sur `validtime`, une seule valeur par
échéance) est remplacé par `fetch_montsouris_vintages.py` : au point de
Montsouris, chaque poll fige un « vintage » (couple `valid_time`, `fetched_at`)
en append-only, conservant l'historique borné des révisions successives d'une
prévision — un futur graphique comparera la courbe observée aux prévisions
émises il y a 6/12/18/24 h. Une compaction à deux régimes (`VINTAGE_RETENTION_H`
= 48 h) borne la table : au-delà de la fenêtre, une échéance passée ne garde que
son vintage le plus proche de la réalisation. Colonne `source`
(`bootstrap`/`live`), couche données `app/data/vintages.py` en lecture seule.
CI : job `fetch-instant` → `fetch-vintages` (cron horaire `50 * * * *`), option
`workflow_dispatch` `instant` → `vintages`. L'ancien parquet
`database_paris_instant.parquet` est laissé en place (orphelin, plus lu).

## [2.4.7] - 2026-07-04
Ajouter un retry sur le push git des jobs du pipeline.

Corrige l'annulation de jobs encore en attente (`cancel-in-progress: false`
ne protège que les jobs déjà en cours) quand une requête plus récente arrive
sur le même `concurrency.group` pendant qu'un déclenchement `target=all` est
encore en file d'attente. Chaque job retente `pull --rebase` + `push`
jusqu'à 5 fois avant d'échouer.

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
