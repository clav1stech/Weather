# Changelog

## [3.2.0-rc.2] - 2026-08-21
Dashboard neige : empreinte mémoire bornée, plus de redémarrage forcé.

Le dashboard neige était tué à intervalles réguliers par l'hébergeur, faute de
mémoire. Depuis la sortie de git, le flux ensemble n'est plus jamais retaillé :
ses lignes membres, 97 % du volume et 25 Mo de RAM par jour collecté,
s'accumulaient sans borne dans la base chargée. Le dashboard ne lit désormais
les membres que sur une fenêtre de dix jours, relative au dernier run stocké et
non à l'horloge ; le flux mean/spread, support de la convergence, reste lu sur
tout l'historique. Le magasin conserve tout : seule la lecture est bornée.

Les filtres descendent dans la lecture parquet, jusqu'aux groupes de lignes, au
lieu de s'appliquer sur une base déjà matérialisée, et les colonnes de libellés
sont lues en dictionnaire Arrow plutôt que recastées après coup. Les bases
volumineuses passent en `cache_resource`, qui partage une instance, là où
`cache_data` en désérialisait une copie complète par appelant ; membres et
mean/spread sont cachés séparément, un recollement caché tenant les membres
deux fois en mémoire. Les pools de runs se choisissent enfin sur un index de
portées plutôt qu'en découpant la base modèle par modèle, et le contrôle des
runs lit les deux flux tels quels.

Base tenue en mémoire ramenée de 805 à 372 Mo et désormais bornée ; pic mesuré
sur le rendu des cinq pages de 3,0 à 2,0 Go. Sorties strictement identiques
sur des données que la fenêtre ne coupe pas, flux mean/spread inchangé au bit
près, 174 tests au vert.

## [3.2.0-rc.1] - 2026-08-20
Refonte de l'interface : design system partagé, navigation en barre du haut.

Les deux dashboards partageaient douze lignes de CSS pour tout habillage, trois
mécaniques de KPI concurrentes et une vingtaine de couleurs écrites en dur au
fil des pages : le rendu restait celui de Streamlit par défaut. Je pose un
design system unique dans `core/ui/`, consommé par le canicule et par la neige.
`tokens.py` devient la source de vérité des couleurs sémantiques, des rayons,
de l'échelle d'espacement et des hauteurs de graphique, en deux palettes
complètes claire et sombre ; `design.py` en dérive la feuille de style, exposée
en variables CSS et en classes, si bien que les composants n'écrivent plus
aucun style inline. `.streamlit/config.toml` habille en plus les widgets
eux-mêmes, ce que le CSS injecté n'atteint pas.

La navigation quitte le bouton radio de la barre latérale pour une barre
d'onglets en haut de page, groupée par intention de lecture, et la barre
latérale devient un panneau de contexte : état des données, fraîcheur,
rafraîchissement, sources. Le contrat des pages (`page(runs, sig)`) est
inchangé — les domaines et les pages transverses n'ont pas bougé.

`components.py` factorise en-tête de page, bandeau de statut, carte KPI, badge,
calendrier, état vide et habillage de tableau. Le calendrier du risque quitte la
heatmap Plotly d'une seule ligne pour une grille de cartes qui se replie sur
écran étroit ; sa couleur reste pilotée par la seule probabilité T850, la
fiabilité Tx/Tn restant un glyphe. Les bandeaux de statut remplacent la grille
de métriques en tête des pages métier plutôt que de s'y ajouter, et rendent
visible le détail qui vivait en infobulle. `plotly_theme.py` enregistre enfin
des gabarits clair et sombre et unifie marges, légendes et hauteurs, jusque-là
divergentes d'un graphique à l'autre.

Le harnais de rendu couvre désormais les deux apps, avec sa propre référence
côté neige, et compare les tableaux stylés sur leurs valeurs. Il navigue par le
hash d'URL des pages, isolé dans `core/testing/apptest_nav.py` : ni
`switch_page`, qui exige un script sur disque, ni les paramètres d'URL ne
savent piloter une navigation construite sur des callables. Non-régression
vérifiée : 35 sorties de calcul strictement identiques, quatorze pages rendues
sans écart de valeur.


## [3.1.13] - 2026-08-22
Dashboard neige : empreinte mémoire bornée, plus de redémarrage forcé.

Le dashboard neige était tué à intervalles réguliers par l'hébergeur, faute de
mémoire. Depuis la sortie de git, le flux ensemble n'est plus jamais retaillé :
ses lignes membres, 97 % du volume et 25 Mo de RAM par jour collecté,
s'accumulaient sans borne dans la base chargée. Le dashboard ne lit désormais
les membres que sur une fenêtre de dix jours, relative au dernier run stocké et
non à l'horloge ; le flux mean/spread, support de la convergence, reste lu sur
tout l'historique. Le magasin conserve tout : seule la lecture est bornée.

Les filtres descendent dans la lecture parquet, jusqu'aux groupes de lignes, au
lieu de s'appliquer sur une base déjà matérialisée, et les colonnes de libellés
sont lues en dictionnaire Arrow plutôt que recastées après coup. Les bases
volumineuses passent en `cache_resource`, qui partage une instance, là où
`cache_data` en désérialisait une copie complète par appelant ; membres et
mean/spread sont cachés séparément, un recollement caché tenant les membres
deux fois en mémoire. Les pools de runs se choisissent enfin sur un index de
portées plutôt qu'en découpant la base modèle par modèle, et le contrôle des
runs lit les deux flux tels quels.

Base tenue en mémoire ramenée de 805 à 372 Mo et désormais bornée ; pic mesuré
sur le rendu des cinq pages de 3,0 à 2,0 Go. Sorties strictement identiques
sur des données que la fenêtre ne coupe pas, flux mean/spread inchangé au bit
près, 174 tests au vert.

## [3.1.12] - 2026-08-20
Interfaces canicule et neige : libellés sans pictogrammes.

Je retire les emojis des titres, boutons, onglets, expanders et messages de
statut des deux dashboards : la couleur des blocs Streamlit porte déjà le
niveau d'un succès ou d'une alerte, et un pictogramme en tête de titre n'ajoute
rien à la lecture. Là où l'emoji portait seul l'information, il est remplacé
par du texte plutôt que supprimé : cumuls de la maille fine (« neige 4 cm ·
pluie 1 mm »), tuiles de la frise des types de temps (libellé court à la place
du symbole), repère HD au-dessus des barres, renvois aux couleurs des
graphiques (« en rouge » plutôt que le rond de couleur). `palier_neige` et
`signal_synoptique` ne renvoient plus d'icône, seulement leur libellé. Les
croix et coches des matrices de présence restent : ce sont des marqueurs de
tableau, pas de la décoration.

## [3.1.11] - 2026-08-20
Bouton « Rafraîchir » : les caches se vident réellement, dashboards figés
jusqu'au reboot corrigés.

Les fonctions cachées des couches données recevaient leur signature sous le nom
`_sig` : Streamlit exclut du hachage tout argument préfixé d'un underscore, la
clé de cache ne contenait donc pas la signature et une nouvelle collecte
n'invalidait rien. Je renomme le paramètre en `sig` partout (canicule et neige)
pour que l'invalidation automatique fonctionne comme les docstrings le
décrivent. La base canicule, cachée en `cache_resource`, échappait en plus au
`st.cache_data.clear()` du bouton : elle est désormais bornée à une entrée et
le bouton vide les trois niveaux — métadonnées du magasin (mémoïsées avec TTL
côté processus), `cache_data` et `cache_resource`. Tant que les données
transitaient par git, chaque commit du pipeline redéployait l'app et purgeait
les caches toutes les 2 h ; la sortie de git a supprimé ce redémarrage
implicite et rendu le défaut visible.

## [3.1.10] - 2026-08-12
Normale climatique T850 : table journalière réelle ERA5 au lieu du seul cosinus.

Le cosinus 1 harmonique (v3.1.9) impose une symétrie printemps/automne que le
cycle annuel réel n'a pas (jusqu'à 1°C d'écart en fin d'année). Je calcule
la vraie moyenne journalière ERA5 1991-2020 (4 échéances synoptiques/jour,
point exact de Paris) jour par jour, lissée par moyenne glissante circulaire
de 21 jours pour absorber le bruit d'un échantillon de 30 ans, et stocke le
résultat (365 valeurs) comme table statique dans config.py — calculée une
fois pour toutes, jamais recalculée à l'affichage (core.stats.climato.
daily_table_normal, un simple indexage). Le modèle cosinus (CLIM_MEAN/
AMPLITUDE/PEAK_DOY, fit sur cette même table) devient le repli manuel : la
page Réglages avancés y bascule automatiquement dès qu'on modifie un des 3
réglages, sinon la table fait foi par défaut.

## [3.1.9] - 2026-08-12
Normale climatique T850 calée sur ERA5 1991-2020 au lieu de valeurs estimées.

CLIM_MEAN/AMPLITUDE/PEAK_DOY (3 °C / 7,0 °C / jour 198) étaient une pure
estimation sans normale officielle derrière. Je les remplace par un fit
cosinus 1 harmonique sur la climatologie mensuelle réelle ERA5 1991-2020
(Copernicus CDS, point exact de Paris) : moyenne 4,38 °C, amplitude 5,59 °C,
pic au jour 213,5 (~1er août, contre le 17 juillet estimé auparavant).

## [3.1.8] - 2026-08-11
Cooldown du déclenchement manuel du pipeline ramené à 1 min.

Les deux pages « Lancer le pipeline » (canicule et neige) imposaient 10 min
entre deux déclenchements publics, calées sur la cadence du flux 6 min plutôt
que sur une contrainte API réelle. La limite secondaire (anti-abus) de l'API
GitHub tolère largement plus qu'un clic manuel occasionnel : 1 min suffit à
écarter un script d'appels en rafale tout en restant confortable pour un
usage humain normal.

## [3.1.7] - 2026-08-11
Jeton d'authentification sur la lecture du magasin externe en ligne.

`GitHubReleaseHttpStore` ne lisait un jeton que dans l'environnement, un canal
inerte sur Streamlit Cloud : la lecture du magasin y restait anonyme, plafonnée
à 60 requêtes/h côté API GitHub. Le quota une fois atteint, le dashboard
basculait silencieusement sur le repli parquet git et pouvait rester figé sur
un run périmé bien après le retour à la normale côté données, un état dont
seul un redémarrage complet du process permettait de sortir.

Les adaptateurs canicule et neige transmettent maintenant `GITHUB_DISPATCH_TOKEN`
(déjà utilisé pour déclencher un run) à ce client, portant le quota à 5000
requêtes/h — l'authentification d'un jeton suffit à en bénéficier sur un dépôt
public, sans permission particulière requise pour la lecture.

## [3.1.6] - 2026-08-11
Lecture des données depuis le magasin externe, effective en ligne.

Le basculement du dashboard vers le magasin dépendait d'un flag lu dans le
seul environnement, alors que Streamlit Cloud ne configure une application que
par ses secrets : la bascule était donc inopérante en ligne. Le dashboard
neige, dont le magasin est l'unique source de vérité, affichait de ce fait des
données figées à la date de sortie de git.

Les deux applications lisent maintenant le magasin par défaut en ligne, le
parquet du dépôt ne servant plus que de repli, et restent sur le parquet en
local pour que dev, tests et harnais ne dépendent jamais du réseau.
`WEATHER_STORE` ne sert plus qu'à forcer explicitement l'une ou l'autre
source, et se lit dans les secrets puis dans l'environnement.

Côté collecte, les cinq pipelines canicule lisent désormais leur base
existante dans le magasin (`WEATHER_STORE_SOURCE`), préalable au retrait des
commits de parquet : sans cela, une collecte repartirait de la copie gelée du
clone et réécrirait le magasin depuis un historique tronqué. Cette lecture est
bloquante et refuse un magasin muet, l'amorçage restant du ressort des scripts
dédiés. Même garde portée au pipeline neige, en laissant démarrer un flux
réellement neuf.

## [3.1.5] - 2026-08-07
Diagnostic explicite quand le mot de passe du pipeline est introuvable.

Un seul message couvrait toutes les causes d'indisponibilité, ce qui rendait
la situation indiagnosticable depuis l'interface : secrets illisibles, secret
mal nommé ou rangé dans une section, et secrets posés sur une autre app
(chaque app Streamlit a les siens) produisaient le même « mot de passe absent
côté serveur ».

Le message distingue désormais ces cas et indique combien de secrets l'app
voit réellement — jamais leur nom ni leur valeur. Voir « aucun secret visible »
alors qu'on vient de les saisir désigne immédiatement la bonne piste.

## [3.1.4] - 2026-08-07
Page « Lancer le pipeline » du dashboard neige accessible en ligne.

Le dashboard neige gardait sa page de lancement réservée au mode local : en
ligne elle n'apparaissait pas du tout, alors que les deux apps partagent la
même version affichée — de quoi croire à un déploiement en retard.

Elle rejoint donc la navigation commune, avec la même mécanique que le
canicule : saisie du mot de passe sur la page, comparée au secret côté
serveur, puis déclenchement du job CI neige (workflow_dispatch) parmi ses
quatre cibles — toutes les collectes, ensemble et régionaux, maille fine, ou
observations. Le dashboard ne collecte jamais lui-même.

Le diagnostic rollover reste local : il lance un sous-processus sur les
parquets du conteneur, sans objet une fois les données dans le magasin.

## [3.1.3] - 2026-08-07
CI neige : isoler les flux pour résister aux échecs partiels.

Les flux neige sont indépendants (APIs, clés et parquets distincts), mais le
job les enchaînait directement : `set -e` interrompait la marche au premier
échec. Un 401 sur la clé PE-AROME faisait ainsi perdre PE-ARPEGE, AROME-PI,
AROME-IFS et la maille fine, pourtant sans rapport.

Chaque flux s'exécute désormais à part, ses échecs sont collectés et annotés,
et le job ne tombe en erreur que si aucun flux n'a abouti — une panne
systémique reste visible, sans que le caprice d'un backend WCS ou une clé
expirée ne mette le job en rouge à chaque passage.

## [3.1.2] - 2026-08-07
Page « Lancer le pipeline » accessible en ligne, déclenchement protégé.

La page rejoint la navigation commune au lieu d'être réservée au mode local.
Sa consultation (stock legacy, historique du contrôle croisé) reste libre et
en lecture seule ; ce sont ses capacités d'action qui s'adaptent désormais à
l'environnement.

En ligne, lancer une collecte ne peut pas passer par un sous-processus — il
n'écrirait que sur le disque éphémère du conteneur. Le bouton déclenche donc
le job GitHub Actions existant (workflow_dispatch), qui reste l'unique
écrivain des données : le mécanisme dormant de core/services/github_dispatch
est réactivé pour cet usage, avec son cooldown partagé vérifié avant tout
appel réseau et consommé seulement après un déclenchement réussi.

Le déclenchement est protégé par un mot de passe (core/ui/auth.py, secret
PIPELINE_PASSWORD côté Streamlit Cloud) : porte fermée par défaut — un secret
absent refuse l'accès plutôt que de l'ouvrir —, comparaison à temps constant,
mot de passe jamais journalisé ni conservé en session.

L'import ciblé legacy → parquet, seule fonction du dashboard qui écrit dans
les données, demeure strictement local et n'apparaît jamais en ligne, même
une fois la porte franchie.

## [3.1.1] - 2026-08-07
Sortie de git de la neige : magasin seul, sans double écriture.

Les six flux neige (ensemble Megève, maille fine, PNT Météo-France local,
PE-ARPEGE, synthèse par cycle, observations Alpes du Nord) et leurs archives
passent au magasin externe partitionné par mois. Contrairement au canicule,
pas de phase de double écriture : le magasin est d'emblée la source de vérité
et la CI ne committe plus aucun parquet neige — les fichiers restés dans git
sont figés et ne servent que de repli de lecture au dashboard.

D'où la règle qui structure le pipeline : l'existant se lit TOUJOURS dans le
magasin, jamais sur le disque — en CI le fichier local est la copie gelée, et
repartir de lui tronquerait l'historique à chaque run. Un magasin injoignable
lève donc côté pipeline, là où le dashboard se dégrade en silence.

Le rollover hot/cold neige devient sans objet : le partitionnement mensuel
fige un mois clos de lui-même. Le job CI `rollover-snow` est supprimé et le
script ne travaille plus que sur les parquets locaux — le brancher sur le
magasin ferait basculer la copie gelée par-dessus l'historique.

Miroir d'écriture mutualisé dans `core/store/mirror.py` (les pipelines neige
passant déjà par `core/pipeline/`, à la différence du canicule qui garde son
implémentation inline), amorçage vérifié par `apps/snow/pipeline/seed_store.py`.

## [3.1.0] - 2026-08-07
Sortie de git des données canicule vers un magasin GitHub Release.

Les cinq flux canicule (ensemble T850, Tx/Tn HD, observations horaires,
observations 6 min, vintages Montsouris) quittent le versionnement git au
profit d'assets d'un Release GitHub dédié (`data-store`), partitionnés par
mois — git n'a plus vocation à servir de base binaire à croissance non
bornée. Nouvelle abstraction `core/store/` à backends interchangeables
(CLI `gh` en écriture pipeline, HTTPS/REST sans authentification en lecture
dashboard) avec preuve de round-trip par mois.

Le dashboard lit désormais le magasin en PRIMAIRE, avec repli automatique
sur le parquet git en BACKUP au moindre échec ou résultat vide (réseau, API
injoignable) — jamais de page en erreur pour une panne du magasin tant que
la double écriture continue d'alimenter git en parallèle. Non-régression
vérifiée : sorties de calcul et rendu des pages strictement identiques entre
lecture git et lecture magasin.

## [3.0.3] - 2026-07-26
Neige : vue d'ensemble repensée grand public et page « Maille fine Météo-France ».

Le bandeau de tuiles se resserre à l'essentiel (Prochaine neige · Limite
pluie-neige 48 h · Changement de temps, nouvelle tuile formulant un régime
sec/perturbé/incertain/variable à partir des signaux déjà calculés) ; la
tendance J0→J+15 devient une frise de tuiles (couleur+emoji = temps dominant,
opacité = accord des membres) et la coupe maille fine 48 h ne trace plus le
temps sec heure par heure mais en « rail » calme par altitude, pour faire
ressortir les précipitations en relief. Nouvelle page « Maille fine
Météo-France » (lecture seule) : météogramme déterministe AROME-PI/IFS,
dispersion des ensembles régionaux PE-AROME/PE-ARPEGE avec probabilité de
dépassement des paliers 1/5/20 cm, frise du type de précipitation (mapping
ptype provisoire, à valider in situ). Aucune fusion entre sources ; lecture
seule via les lecteurs existants.

## [3.0.2] - 2026-07-26
Neige : diagnostic pluie-neige pondéré et flux Météo-France directs.

Coupe verticale horaire pluie/neige/sec/mixte aux quatre altitudes et bilan
quotidien cm/mm, classification par membre pondérée sur J0–J+15 (seuils
centralisés dans `weather_type.py`, à affiner in situ). Collecteurs WCS
Météo-France en décodage GRIB direct (AROME-PI, PE-AROME puis PE-ARPEGE
restreint à l'emprise Megève, retries fiabilisés face à un backend WCS
erratique) et collecteur AROME-IFS direct (pluie/neige/T2m horaires,
H+1 à H+45) en complément d'Open-Meteo, avec hiérarchie explicite sans
double comptage. `core/stats/ensemble.py` (mutualisé avec le canicule) se
dégrade désormais silencieusement quand un modèle n'a aucune valeur pour la
variable tracée, utile aux flux régionaux aux schémas inégaux.

## [3.0.1] - 2026-07-26
Neige : archivage hot/cold, contrôle des runs, convergence multi-modèles.

Mécanique générique d'archivage `core/pipeline/hot_cold.py` (sauvegardes
datées, vérification stricte de non-perte, dry-run) avec rollover
hebdomadaire des parquets neige (job CI `rollover-snow`) ; activation
équivalente préparée côté canicule mais non déclenchée. Contrôle
opérationnel des runs (fraîcheur, portée contiguë, complétude, cycles
attendus) avec les mêmes fonctions que le pipeline, page locale de
lancement des flux en dry-run. Page Convergence enrichie (bande
mean ± spread par modèle, heatmap des révisions, mode « Tous modèles » à
poids égal, amplitude Min–Max) et Vue d'ensemble filtrant les traces isolées
sous les seuils de pertinence.

## [3.0.0] - 2026-07-26
Réunification du versioning canicule/neige ; fondations du dashboard neige.

Les deux dashboards du monorepo partagent une part croissante de code
(`core/`) et sont désormais versionnés comme un seul produit : `APP_VERSION`
et `SNOW_APP_VERSION` importent la même `SHARED_VERSION`
(`core/version.py`, source de vérité unique lue par `export_project.py`) —
le versioning séparé neige (`SNOW_APP_VERSION` 0.1.0–0.5.0,
`apps/snow/docs/CHANGELOG.md`) est archivé et gelé, cf. `CLAUDE.md` §
Versioning. Ce jalon couvre aussi les fondations du chantier neige :
squelette du dashboard (pipeline ensemble + maille fine, pages Vue
d'ensemble/Explorer un run/Convergence, entrée `snow_app.py`) et page
Observations dédiée aux stations Météo-France des Alpes du Nord
(DPPaquetObs département 74, Combloux/Mont d'Arbois/Aiguille du Midi en
référence), pipeline `fetch_observations.py` mutualisé via
`core/pipeline/observations.py`.

## [2.6.3] - 2026-07-26
Explorer un run : correction d'un warning pandas sur le round des tables.

`raw.round(2)` s'appliquait à l'ensemble du DataFrame des tableaux d'export
(onglet 🧾), y compris une colonne datetime résiduelle, ce que pandas
n'accepte plus silencieusement (`UserWarning: obj.round has no effect with
datetime, timedelta, or period dtypes`). Le round ne porte désormais que sur
les colonnes numériques (`select_dtypes(include="number")`), comme c'est déjà
le cas partout ailleurs dans le code (`app/stats/tables.py`,
`app/stats/ensemble.py`). Aucun changement de valeur affichée.

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
au dernier point horaire — la température étant publiée par les 4 stations
à ce pas, toutes peuvent en bénéficier. Fusion calculée au rendu uniquement :
l'écart ICU, les Tx/Tn journaliers et les min/max du jour restent calculés
sur l'horaire seul (frontière documentée dans CLAUDE.md). (issue #22)

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
