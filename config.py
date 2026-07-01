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
LATITUDE = 48.86
LONGITUDE = 2.33
TIMEZONE = "auto"              # l'API renvoie l'heure locale + utc_offset_seconds
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
     "cycles": [0, 6, 12, 18], "horizon_h": 360, "meta_slug": "ecmwf_ifs025",
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
# Avec une seule variable la base a exactement 5 colonnes :
#   [run_date, model, member, valid_time, t850]
# Ajouter {"api": "geopotential_500hPa", "col": "z500"} suffit à stocker z500.
VARIABLES = [
    {"api": "temperature_850hPa", "col": "t850"},
]

# Variable principale (KPI, panache, risque). Première de la liste par défaut.
PRIMARY_VAR = VARIABLES[0]["col"]

# --------------------------------------------------------------------------- #
#  Stockage
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "database_paris.parquet")

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
LEGACY_MODELS = {"ECMWF": "ECMWF", "AIFS": "AIFS", "GEFS": "GEFS"}  # label -> feuille xlsx
LEGACY_DET_NAMES = {"DET", "GFS"}  # nom de la colonne contrôle selon le modèle
LEGACY_FORECASTS_DIR = os.path.join(BASE_DIR, "Forecasts")

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
LABEL_BY_API = {m["api"]: m["label"] for m in MODELS}
COLOR_BY_LABEL = {m["label"]: m["color"] for m in MODELS}
API_BY_LABEL = {m["label"]: m["api"] for m in MODELS}
VAR_COLS = [v["col"] for v in VARIABLES]
VAR_API_BY_COL = {v["col"]: v["api"] for v in VARIABLES}
SCHEMA = ["run_date", "model", "member", "valid_time"] + VAR_COLS
