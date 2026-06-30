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
HTTP_TIMEOUT = 60              # secondes

# Décalage entre l'heure synoptique d'un run (0/6/12/18Z) et le moment où il est
# publié/exploitable. Le workflow tourne à 04:15/10:15/16:15/22:15 UTC pour
# « chasser » chaque cycle ~4 h après son initialisation.
PUBLICATION_LAG_HOURS = 4

# --------------------------------------------------------------------------- #
#  Modèles d'ensemble
# --------------------------------------------------------------------------- #
# api    : identifiant Open-Meteo (paramètre `models`)
# label  : nom court affiché / stocké dans la colonne `model`
# main   : modèle « principal » attendu à chaque run (sert au contrôle de
#          divergence inter-modèles). Mettre False pour un modèle d'appoint.
# color  : couleur d'affichage (dashboard)
# cycles : heures UTC où le modèle publie RÉELLEMENT un run (fait connu, propre
#          à chaque modèle — pas une heuristique). Ex. GEM ne tourne qu'à
#          0Z/12Z ; à 6Z/18Z son « run » n'existe pas.
# desc   : description courte affichée dans le dashboard (page « Indicateur de
#          canicule » → expander explicatif).
#
# Pas de champ « horizon nominal » : l'horizon réel d'un cycle 6Z/18Z varie d'un
# jour à l'autre (constaté sur AIFS, qui dépasse parfois largement la moitié de
# période) — toute table figée serait donc non fiable. La distinction entre
# échéances réellement renouvelées et queue collée de l'ancien cycle se fait de
# façon empirique au pipeline (cf. Forecast.mask_stale_tail).
MODELS = [
    {"api": "ecmwf_ifs025_ensemble",   "label": "ECMWF", "main": True,  "color": "#1F618D",
     "cycles": [0, 6, 12, 18],
     "desc": "modèle *physique* du Centre européen (Reading, Royaume-Uni), référence "
             "mondiale de la prévision à moyenne échéance"},
    {"api": "ecmwf_aifs025_ensemble",  "label": "AIFS",  "main": True,  "color": "#1E8449",
     "cycles": [0, 6, 12, 18],
     "desc": "le modèle d'**intelligence artificielle** du même Centre européen, récent, "
             "très rapide et désormais très performant"},
    {"api": "ncep_gefs_seamless",      "label": "GEFS",  "main": True,  "color": "#B9770E",
     "cycles": [0, 6, 12, 18],
     "desc": "l'ensemble américain de la **NOAA** (États-Unis)"},
    {"api": "gem_global_ensemble",     "label": "GEM",   "main": False, "color": "#16A085",
     "cycles": [0, 12],
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
# Le run de contrôle (membre 0 / DET) représente la même trajectoire physique
# des deux côtés : un écart au-delà de ce seuil est jugé suspect (à investiguer),
# pas une simple divergence de modèle.
CROSS_CHECK_TOLERANCE_C = 0.5
CROSS_CHECK_LOG_PATH = os.path.join(DATA_DIR, "cross_check_log.csv")
LEGACY_MODELS = {"ECMWF": "ECMWF", "AIFS": "AIFS", "GEFS": "GEFS"}  # label -> feuille xlsx
LEGACY_DET_NAMES = {"DET", "GFS"}  # nom de la colonne contrôle selon le modèle
LEGACY_FORECASTS_DIR = os.path.join(BASE_DIR, "Forecasts")

# Stratégie de comparaison par modèle :
#   • "det"    : colonne DET/GFS legacy vs membre de contrôle (member 0) Open-Meteo
#                — valide seulement quand les deux représentent la MÊME trajectoire
#                physique (vrai pour ECMWF/AIFS, leur run de contrôle est partagé
#                entre les deux sources).
#   • "median" : médiane des membres d'ensemble des deux côtés — pour GEFS, où la
#                colonne « GFS » scrapée est en réalité le run déterministe haute
#                résolution séparé (produit différent du membre de contrôle de
#                l'ensemble GEFS), donc pas comparable au DET. La médiane reste
#                comparable car les deux sources poolent le même ensemble GEFS
#                (31 membres, 0 à 30).
LEGACY_COMPARE_STRATEGY = {"ECMWF": "det", "AIFS": "det", "GEFS": "median"}

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
