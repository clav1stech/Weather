# -*- coding: utf-8 -*-
"""Point de configuration central — partagé par le pipeline (Forecast.py) et le
dashboard (meteo_app.py).

Toute donnée susceptible de changer ou d'être étendue se déclare ICI :
ajouter un modèle = une ligne dans MODELS, ajouter une variable = une ligne dans
VARIABLES. La logique de parsing / stockage / affichage n'a pas à être touchée.
"""

import os

# --------------------------------------------------------------------------- #
#  Localisation & API
# --------------------------------------------------------------------------- #
LATITUDE = 48.8534
LONGITUDE = 2.3488
TIMEZONE = "UTC"               # jamais "auto" : "auto" applique un unique offset
                                # local (Europe/Paris) à toute la fenêtre de 16 j,
                                # ce qui décale d'1 h les échéances après un
                                # changement d'heure (DST) tombant dans la fenêtre —
                                # bug confirmé empiriquement sur l'API. En UTC pur,
                                # aucune notion de DST : alignement toujours correct.
FORECAST_DAYS = 16             # horizon (résolution horaire native)
API_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
# Metadata API : renvoie last_run_initialisation_time exact par modèle,
# sans quota. Slug différent du paramètre `api` (cf. champ meta_slug de MODELS).
META_API_URL_TPL = "https://api.open-meteo.com/data/{slug}/static/meta.json"
HTTP_TIMEOUT = 60              # secondes
# Seuil d'audit : si metadata et heuristique divergent de plus de N heures,
# un avertissement est loggé (le pipeline n'est pas bloqué pour autant).
META_HEURISTIC_DIVERGENCE_WARN_H = 6

# Décalage entre l'heure synoptique d'un run (0/6/12/18Z) et le moment où il est
# publié/exploitable. Le workflow tourne à 04:15/10:15/16:15/22:15 UTC pour
# « chasser » chaque cycle ~4 h après son initialisation.
PUBLICATION_LAG_HOURS = 4

# --------------------------------------------------------------------------- #
#  Modèles d'ensemble
# --------------------------------------------------------------------------- #
# api       : identifiant Open-Meteo (paramètre `models`)
# label     : nom court affiché / stocké dans la colonne `model`
# main      : modèle « principal » attendu à chaque run (sert au contrôle de
#             divergence inter-modèles). Mettre False pour un modèle d'appoint.
# color     : couleur d'affichage (dashboard)
# cycles    : heures UTC où le modèle publie RÉELLEMENT un run (fait connu, propre
#             à chaque modèle — pas une heuristique). Ex. GEM ne tourne qu'à
#             0Z/12Z ; à 6Z/18Z son « run » n'existe pas.
# meta_slug : slug de la Metadata API (GET META_API_URL_TPL.format(slug=…)), qui
#             renvoie last_run_initialisation_time exact — peut différer du param
#             `api` (ex. ncep_gefs_seamless → ncep_gefs025). None = pas d'endpoint
#             connu ; le pipeline replie sur l'heuristique infer_run_date.
# meta_base_url : domaine de base pour la Metadata API si différent de
#             META_API_URL_TPL (ex. ensemble-api.open-meteo.com pour GEM/GEPS).
# desc      : description courte affichée dans le dashboard (page « Indicateur de
#             canicule » → expander explicatif).
# horizon_h (OPTIONNEL) : horizon nominal du cycle PRINCIPAL (0Z/12Z), en heures.
#          Sert UNIQUEMENT à désambiguïser l'étiquette de run à partir de la
#          dernière échéance publiée (cf. Forecast.infer_run_date) — ce n'est PAS
#          une table de troncature bloquante. L'inférence n'est retenue que si
#          elle tombe net sur un cycle ET reste à portée du cycle horloge ; sinon
#          (run partiel/tronqué, off-cycle 6Z/18Z plus court que son nominal)
#          repli sur la détection horloge. Un modèle SANS horizon_h (ex. GEM, dont
#          l'horizon réel n'est pas connu de façon fiable) utilise directement ce
#          repli — c'est le choix sûr quand on ignore si le modèle publie partiel.
#
# La distinction « échéances réellement renouvelées » vs « queue collée de l'ancien
# cycle » reste empirique au pipeline (cf. Forecast.mask_stale_tail) : horizon_h
# ne fige rien, il ne fait que lever l'ambiguïté 06Z-servi-comme-12Z.
MODELS = [
    {"api": "ecmwf_ifs025_ensemble",   "label": "ECMWF", "main": True,  "color": "#1F618D",
     "cycles": [0, 6, 12, 18], "expected_cycles": [0, 12], "horizon_h": 360, "meta_slug": "ecmwf_ifs025",
     "desc": "modèle *physique* du Centre européen (Reading, Royaume-Uni), référence "
             "mondiale de la prévision à moyenne échéance"},
    {"api": "ecmwf_aifs025_ensemble",  "label": "AIFS",  "main": True,  "color": "#1E8449",
     "cycles": [0, 6, 12, 18], "horizon_h": 360, "meta_slug": "ecmwf_aifs025_ensemble",
     "desc": "le modèle d'**intelligence artificielle** du même Centre européen, récent, "
             "très rapide et désormais très performant"},
    {"api": "ncep_gefs_seamless",      "label": "GEFS",  "main": True,  "color": "#B9770E",
     "cycles": [0, 6, 12, 18], "horizon_h": 384, "meta_slug": "ncep_gefs025",
     "desc": "l'ensemble américain de la **NOAA** (États-Unis)"},
    {"api": "gem_global_ensemble",     "label": "GEM",   "main": False, "color": "#16A085",
     "cycles": [0, 12], "meta_slug": "cmc_gem_geps",
     "meta_base_url": "https://ensemble-api.open-meteo.com/data/{slug}/static/meta.json",
     "desc": "l'ensemble canadien d'**Environnement Canada** (ECCC)"},
]

# --------------------------------------------------------------------------- #
#  Variables
# --------------------------------------------------------------------------- #
# api : nom Open-Meteo (paramètre `hourly`)
# col : nom de la colonne « valeur » dans la base plate
# Chaque variable = une colonne dans le schéma [run_date, model, member,
# valid_time, <col>…]. Le pipeline (fetch/parse/persist) est générique : une
# ligne ici suffit. Les lignes stockées AVANT l'ajout d'une variable restent
# valides (colonne absente du parquet historique → NaN, schéma rétro-compatible ;
# le pipeline et le dashboard tolèrent tous deux cette absence).
#
# z500 : géopotentiel à 500 hPa (contexte synoptique — dorsale/talweg), en
# mètres géopotentiels (~5 500-5 900 m). Le nom API exact est
# `geopotential_height_500hPa` (vérifié : `geopotential_500hPa` → HTTP 400) ;
# exposé par les 4 modèles avec le même pattern de clés membre que t850 et la
# même couverture temporelle. Variable de CONTEXTE uniquement : la détection
# canicule et toutes les sélections de runs restent pilotées par PRIMARY_VAR.
VARIABLES = [
    {"api": "temperature_850hPa", "col": "t850"},
    {"api": "geopotential_height_500hPa", "col": "z500"},
]

# Variable principale (KPI, panache, risque). Première de la liste par défaut.
PRIMARY_VAR = VARIABLES[0]["col"]

# --------------------------------------------------------------------------- #
#  Tx/Tn haute résolution (API Forecast standard, PAS l'API Ensemble)
# --------------------------------------------------------------------------- #
# Flux ANNEXE en lecture seule (cf. forecast_t2m_hd.py) : température 2 m
# max/min journalière à très haute résolution locale, affichée en appui dans le
# calendrier du risque de canicule. N'influence NI la détection canicule, NI la
# sélection des runs, NI les KPI — t850 (PRIMARY_VAR) reste l'unique pilote.
# api   : identifiant Open-Meteo (paramètre `models` de l'API Forecast). Les
#         « seamless » combinent les grilles du fournisseur (ex. AROME→ARPEGE) ;
#         il n'y a donc PAS de cycle unique identifiable — le flux est daté par
#         instant de collecte (fetched_at), pas par run synoptique.
# label : nom stocké dans la colonne `model` du parquet T2m.
# L'ORDRE de la liste est l'ordre de PRIORITÉ à l'affichage : pour chaque jour,
# le premier modèle disposant d'une valeur l'emporte, le suivant ne sert que de
# secours — jamais les deux à la fois pour un même jour.
T2M_API_URL = "https://api.open-meteo.com/v1/forecast"
T2M_MODELS = [
    {"api": "meteofrance_seamless", "label": "Météo-France"},
    {"api": "dwd_icon_seamless",    "label": "DWD ICON"},
]
# Horizon 7 jours : Météo-France (AROME/ARPEGE seamless) ne publie que J à J+3
# (null au-delà, constaté empiriquement) ; DWD ICON couvre les 7 jours. On
# collecte donc 7 j pour exploiter la valeur qu'ICON apporte au-delà de MF —
# sur J+4 à J+6, ICON est seul (MF prioritaire tant qu'il existe, cf. T2M_MODELS)
# et l'affichage le SIGNALE comme valeur indicative (source unique, pas de
# recoupement). Ce flux reste un appui d'affichage, jamais un critère de risque.
T2M_FORECAST_DAYS = 7

# --------------------------------------------------------------------------- #
#  Stockage
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "database_paris.parquet")
# Parquet SÉPARÉ pour le flux Tx/Tn HD : pas un ensemble (aucun `member`), le
# mélanger à DB_PATH casserait la sémantique de la base plate principale.
DB_T2M_PATH = os.path.join(DATA_DIR, "database_paris_t2m.parquet")

# Une série (model, member) entièrement NaN (modèle indisponible ce run) n'est
# pas stockée. Les modèles qui s'arrêtent tôt gardent en revanche leurs lignes
# NaN de queue jusqu'à 16 j (grille horaire uniforme).
DROP_EMPTY_SERIES = True

# Seuil de fraîcheur empirique (cf. Forecast.mask_stale_tail) : l'API Open-Meteo
# ne publie aucune métadonnée d'initialisation par modèle ni d'horizon nominal
# fiable par cycle. À CHAQUE échéance, on compare la moyenne des écarts absolus
# (sur tous les membres comparables) à la valeur déjà stockée pour ce modèle au
# run précédent. Écart moyen ≤ ce seuil → échéance jugée non renouvelée (copie de
# l'ancien cycle, NaN-ifiée) ; au-dessus → échéance fraîche, conservée. Seuil
# volontairement strict : des membres d'ensemble indépendants (perturbations
# aléatoires) ne tombent quasiment jamais sous ce seuil par coïncidence.
FRESHNESS_EPS = 0.05  # °C — écart moyen abs. minimal, PAR ÉCHÉANCE, pour la juger fraîche

# --- Inférence du run pilotée par la donnée (cf. Forecast.infer_run_date) ----- #
# L'identité du cycle vit dans la DERNIÈRE échéance publiée (init + horizon), pas
# dans la première (rebouchée par l'API depuis 00:00 local). On rétro-calcule
# init = dernière_échéance − horizon_h et on le cale sur la grille de cycles.
# Deux garde-fous, pour ne JAMAIS dégrader le comportement horloge actuel :
RUN_SNAP_TOLERANCE_H = 3   # init calé doit tomber à ≤ 3 h d'un cycle (sinon repli)
RUN_INFER_MAX_SHIFT_H = 9  # …et à ≤ 9 h du cycle horloge : l'inférence ne peut que
                           # corriger vers un cycle VOISIN (ex. 12Z→06Z), jamais
                           # téléporter. Au-delà (run tronqué, off-cycle court) → repli.

# --- Garde-fou de persistance : ne stocker un run frais que s'il est COMPLET -- #
# Un cycle fraîchement détecté (mask_stale_tail dit « a_du_neuf ») peut rester
# court pour deux raisons bien distinctes, et dans les deux cas on ne veut PAS
# le persister comme dernier run du modèle (il écraserait, dans l'usage — ex.
# Explorer un run, qui prend le run le plus récent par modèle sans revérifier sa
# portée —, un run PLEIN déjà en base par un run tronqué non comparable) :
#   1. Modèle qui tourne à horizon plein 4×/j (AIFS, GEFS) : un run court au
#      moment du poll est simplement ENCORE EN COURS DE CALCUL côté Open-Meteo ;
#      il finira par atteindre son horizon_h nominal → on retente au poll suivant.
#   2. Modèle dont certains cycles sont NATIVEMENT plus courts par construction
#      (ex. ECMWF ENS à 6Z/18Z ≈ 144 h, contre 360 h à 0Z/12Z — cf.
#      `expected_cycles` en commentaire de MODELS) : ce n'est pas un calcul en
#      cours, ce run ne dépassera JAMAIS ~150 h — il ne sera donc simplement
#      jamais persisté (cohérent avec la volonté de ne comparer les modèles
#      principaux qu'à horizon plein).
# Dans les deux cas le traitement est identique : on exige que la portée réelle
# du run frais (dernière échéance valide − run_date) atteigne un seuil minimal
# avant persistance ; sinon il est laissé de côté (comme un cycle inchangé :
# l'ancien run complet reste en base).
#   • modèle avec `horizon_h` connu (ECMWF/AIFS/GEFS) : seuil = horizon_h − tolérance ;
#   • modèle sans `horizon_h` (ex. GEM) : seuil fixe MIN_PERSIST_HORIZON_H.
PERSIST_HORIZON_TOLERANCE_H = 24
MIN_PERSIST_HORIZON_H = 360  # °h — ~15 jours, portée minimale pour être comparable

# La portée réelle se mesure sur la CHAÎNE CONTIGUË d'échéances valides depuis
# run_date : tout trou entre deux échéances valides successives (ou entre
# run_date et la première) supérieur à ce seuil termine la chaîne. Sans cela,
# une réponse creuse de l'API (quelques heures rebouchées en tête + un point
# parasite isolé en queue) simule une portée pleine : elle passerait le seuil de
# persistance ET, une fois en base, bloquerait le vrai run via la garde
# anti-régression (sa « portée » naïve dépassant celle du run sain). Le pas de
# temps natif le plus lâche observé est 6 h (AIFS en fin d'horizon) : 24 h laisse
# une marge large sans jamais enjamber un trou d'une journée.
PERSIST_MAX_GAP_H = 24

# --------------------------------------------------------------------------- #
#  Climatologie & seuils (à 850 hPa)
# --------------------------------------------------------------------------- #
# Normale climatique saisonnière modélisée par un cosinus :
#   normale(jour) = MEAN + AMPLITUDE * cos(2π (doy - PEAK_DOY) / 365.25)
# Paramètres pour la T850 région parisienne (max ~mi-juillet).
CLIM_MEAN = 3          # °C — moyenne annuelle
CLIM_AMPLITUDE = 7.0     # °C — demi-amplitude saisonnière
CLIM_PEAK_DOY = 198      # jour de l'année du maximum (~17 juillet)

SEUIL_CHALEUR_850 = 14.0   # °C — ligne de repère « chaleur notable »
SEUIL_CANICULE_850 = 18.0  # °C — seuil de canicule exceptionnelle (pilote le risque)

# --------------------------------------------------------------------------- #
#  Climatologie Z500 (géopotentiel 500 hPa, mètres géopotentiels)
# --------------------------------------------------------------------------- #
# Même modèle cosinus que la T850 (normale = MEAN + AMPLITUDE·cos(…)), valeurs
# ESTIMÉES pour la région parisienne (~5 560 m en janvier, ~5 760 m fin juillet)
# — pas une normale officielle. Sert uniquement à calculer l'ANOMALIE de Z500
# (dorsale/talweg) pour le signal de contexte synoptique : la valeur brute en
# mètres ne veut rien dire seule, seul l'écart à la saison est interprétable.
CLIM_Z500_MEAN = 5660.0      # m — moyenne annuelle
CLIM_Z500_AMPLITUDE = 100.0  # m — demi-amplitude saisonnière
CLIM_Z500_PEAK_DOY = 203     # jour de l'année du maximum (~22 juillet)

# --------------------------------------------------------------------------- #
#  KPI de la Vue d'ensemble (cartes en tête de page)
# --------------------------------------------------------------------------- #
# Horizon de confiance : première échéance où le spread P90−P10 du super-ensemble
# dépasse ce seuil — au-delà, les scénarios divergent trop pour être exploités
# individuellement (le panache reste informatif, pas le scénario central seul).
KPI_SPREAD_CONF_MAX_C = 6.0
# Jour « à risque » = probabilité × sévérité, deux portes d'entrée :
#   • proba journalière (membres poolés du jour ≥ SEUIL_CANICULE_850) ≥ PROB_MIN ;
#   • OU dépassement attendu E[max(T − seuil, 0)] ≥ EXCESS_MIN — capte les queues
#     chaudes (proba modeste mais sévérité forte) qu'un seuil de proba seul rate.
KPI_RISK_PROB_MIN = 0.50       # probabilité journalière qualifiante
KPI_RISK_EXCESS_MIN_C = 1.0    # °C — dépassement attendu qualifiant
# Fenêtre (jours) de l'anomalie moyenne vs la normale climatique (cosinus).
KPI_ANOMALIE_FENETRE_J = 7
# Nb de cycles proposés par le sélecteur « Vu depuis » (versions antérieures).
KPI_MAX_VERSIONS = 12

# --------------------------------------------------------------------------- #
#  Contrôle croisé Open-Meteo vs legacy (Météociel) — cf. validate_cross_pipeline.py
# --------------------------------------------------------------------------- #
# Objectif du contrôle : détecter un BUG pipeline (offset constant, corruption,
# mauvais cycle), PAS la divergence-modèle légitime. À courte échéance un bug se
# traduit par un écart constant ; à longue échéance, deux ensembles distincts
# (versions de modèle, post-traitement, échantillon de membres, résolution
# horaire vs 6-horaire) divergent de 1-2 °C sans anomalie. La tolérance s'élargit
# donc avec l'échéance : tol(lead) = BASE + PER_DAY·jours, plafonnée à CAP.
CROSS_CHECK_TOLERANCE_BASE_C = 0.5     # °C — seuil à échéance ~0
CROSS_CHECK_TOLERANCE_PER_DAY_C = 0.2  # °C — élargissement par jour d'échéance
CROSS_CHECK_TOLERANCE_CAP_C = 3.0      # °C — plafond (au-delà = vraie anomalie)
# Comparaison de médianes : exiger un minimum de membres valides des DEUX côtés,
# sinon la médiane n'est pas représentative (ex. queue AIFS NaN-ifiée, 1-2 membres).
CROSS_CHECK_MIN_MEMBERS = 5
# Tolérance d'alignement run : si le run_date Météociel (xlsx) et le run_date
# Open-Meteo (parquet) diffèrent de plus de N heures, les deux sources ne
# représentent pas le même cycle — le modèle est ignoré dans ce contrôle.
CROSS_CHECK_RUN_ALIGN_TOL_H = 3
CROSS_CHECK_LOG_PATH = os.path.join(DATA_DIR, "cross_check_log.csv")
# Date de bascule « pipeline réel » (UTC). AVANT cette date, la base Open-Meteo a
# été rétro-remplie depuis les xlsx Météociel (migrate.py) : la comparer au legacy
# est circulaire (mêmes données) et GEM / les cycles 6Z-18Z n'existent pas encore.
# La page « Contrôle des runs » ne confronte donc OM ↔ legacy qu'à partir d'ici.
# (La détection d'absence de modèle, elle, se cale sur la 1re apparition réelle de
# CHAQUE modèle — pas besoin de dater GEM en dur.)
PIPELINE_LIVE_SINCE = "2026-06-30"
LEGACY_MODELS = {"ECMWF": "ECMWF", "AIFS": "AIFS", "GEFS": "GEFS"}  # label -> feuille xlsx
LEGACY_DET_NAMES = {"DET", "GFS"}  # nom de la colonne contrôle selon le modèle
LEGACY_FORECASTS_DIR = os.path.join(BASE_DIR, "legacy")

# Stratégie de comparaison par modèle :
#   • "median" : médiane des membres d'ensemble des deux côtés. Stratégie par
#                défaut pour TOUS les modèles, car la colonne « DET »/« GFS »
#                scrapée sur Météociel est en réalité le run déterministe HAUTE
#                RÉSOLUTION (HRES pour ECMWF, GFS-det pour GEFS), un produit
#                SÉPARÉ du membre de contrôle (member 0) de l'API ensemble.
#                Vérifié empiriquement : sur ECMWF à J+13, « DET » Météociel ≈
#                +4 °C vs sa propre médiane (bord chaud du HRES) tandis que le
#                member 0 Open-Meteo ≈ −6 °C vs sa médiane (bord froid de l'ENS
#                control) → la comparaison det-vs-det confronte deux produits aux
#                bords opposés du panache et fabrique ~7 °C d'écart artificiel. La
#                médiane-vs-médiane le ramène à 1-2 °C (les deux sources poolent
#                le même ensemble).
#   • "det"    : colonne DET/GFS legacy vs member 0 Open-Meteo — conservé pour
#                mémoire/usage explicite, mais NON recommandé (produits distincts,
#                cf. ci-dessus).
LEGACY_COMPARE_STRATEGY = {"ECMWF": "median", "AIFS": "median", "GEFS": "median"}

# --------------------------------------------------------------------------- #
#  Dérivés (ne pas éditer)
# --------------------------------------------------------------------------- #
MODEL_LABELS = [m["label"] for m in MODELS]
MAIN_LABELS = [m["label"] for m in MODELS if m["main"]]
# Cycles où chaque modèle est ATTENDU (absence = alerte). Distinct de `cycles`
# (cycles où il CAN publier) : ex. ECMWF tourne à 0/6/12/18Z mais n'est requis
# complet qu'à 0Z et 12Z ; une absence à 6Z/18Z n'est pas une anomalie.
EXPECTED_CYCLES_BY_LABEL = {m["label"]: m.get("expected_cycles", m["cycles"]) for m in MODELS}
LABEL_BY_API = {m["api"]: m["label"] for m in MODELS}
COLOR_BY_LABEL = {m["label"]: m["color"] for m in MODELS}
API_BY_LABEL = {m["label"]: m["api"] for m in MODELS}
VAR_COLS = [v["col"] for v in VARIABLES]
VAR_API_BY_COL = {v["col"]: v["api"] for v in VARIABLES}
SCHEMA = ["run_date", "model", "member", "valid_time"] + VAR_COLS
# Flux Tx/Tn HD — l'ordre de T2M_LABELS EST l'ordre de priorité d'affichage.
T2M_LABELS = [m["label"] for m in T2M_MODELS]
T2M_SCHEMA = ["fetched_at", "model", "target_date", "tx", "tn"]
