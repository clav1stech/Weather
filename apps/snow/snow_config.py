# -*- coding: utf-8 -*-
"""Configuration de l'app NEIGE (Megève) — pipeline + futur dashboard.

Nommé snow_config (et non config) pour ne JAMAIS entrer en collision avec le
config.py racine du canicule sur sys.path : les scripts d'apps/snow/pipeline/
ajoutent la racine du repo à leur chemin (pour `core`), un homonyme serait un
piège d'import silencieux.

Deux flux SÉPARÉS, deux parquets (jamais fusionnés, jamais de comblement de
l'un par l'autre — dégradation silencieuse si l'un manque) :
  • flux ensemble/synoptique (fetch_ensemble.py) : Ensemble API (membres
    individuels, rétention API ~3 j — spread court terme) + Ensemble Mean API
    (mean+spread, rétention longue — pilote de l'historique/convergence)
    → data/db_megeve.parquet ;
  • flux maille fine (fetch_hd.py) : API Forecast standard, AROME France HD +
    ICON-D2, courte échéance → data/db_megeve_hd.parquet (append-only, aucun
    member ni cycle synoptique — pattern du flux Tx/Tn HD du canicule).
"""

import os

# --------------------------------------------------------------------------- #
#  Points de mesure (FIXÉS — ne pas les redéterminer)
# --------------------------------------------------------------------------- #
# Deux points couvrent l'ensemble des besoins prévisionnels neige — ne pas en
# ajouter. Les cellules Open-Meteo correspondantes tombent à ~1094 m (village)
# et ~1804 m (sommet), vérifié en conditions réelles : altitudes représentatives.
# `code` = valeur stockée dans la colonne `site` des parquets.
SITES = [
    {"code": "village", "nom": "Megève village",
     "lat": 45.85887380014435, "lon": 6.619374792266182, "alt": 1100},
    {"code": "sommet", "nom": "Mont d'Arbois (sommet)",
     "lat": 45.85847167028437, "lon": 6.662339797334486, "alt": 1830},
]

TIMEZONE = "UTC"        # jamais "auto" : offset local unique appliqué à toute la
                        # fenêtre → décalage d'1 h si un changement d'heure tombe
                        # dedans (bug API confirmé côté canicule). UTC pur : sûr.
FORECAST_DAYS = 15      # horizon du flux ensemble (grille horaire)
HTTP_TIMEOUT = 60       # secondes
PUBLICATION_LAG_HOURS = 4   # délai cycle synoptique → run exploitable (repli horloge)
META_API_URL_TPL = "https://api.open-meteo.com/data/{slug}/static/meta.json"

# --------------------------------------------------------------------------- #
#  Flux ensemble — Ensemble API (membres) + Ensemble Mean API (mean/spread)
# --------------------------------------------------------------------------- #
ENS_API_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Les deux appels partagent le même endpoint : l'Ensemble Mean API est le même
# /v1/ensemble avec des identifiants de modèles suffixés `_ensemble_mean` —
# variables demandées en nom SIMPLE (le mean est la valeur par défaut) +
# suffixe `_spread` pour l'écart-type (vérifié en conditions réelles ; un nom
# `<var>_mean` renvoie HTTP 400).
#
# MEAN ≠ MEMBRES dans la base : les deux appels alimentent le MÊME parquet mais
# sous des labels de modèle DISTINCTS (ECMWF vs ECMWF_MEAN…). Chaque flux a
# ainsi son propre couple (run_date, modèle) — fraîcheur, complétude et
# anti-régression indépendantes. Indispensable : les deux endpoints ne se
# renouvellent pas exactement en même temps pour un même cycle ; un label
# partagé ferait s'écraser mutuellement membres et moyenne au fil des polls
# (le remplacement par couple emporte TOUTES les lignes du couple).
#
# horizon_h : seuil de complétude AVANT persistance (portée réelle contiguë
# exigée, à tolérance près) — calé sur la fenêtre réellement servie, pas sur
# l'horizon nominal du modèle : avec forecast_days=15, la fenêtre s'arrête à
# 360 h après 00Z du jour, donc un run 18Z plafonne à ~342 h atteignables
# (idem GEFS, nominal 384 h mais tronqué par la fenêtre). 336 h + tolérance
# 24 h = seuil 312 h : tous les cycles pleins passent, les cycles nativement
# courts (ECMWF 6Z/18Z ≈ 144 h) restent exclus — voulu.
ENS_MODELS = [
    {"api": "ecmwf_ifs025_ensemble", "label": "ECMWF", "main": True,
     "color": "#1F618D", "cycles": [0, 6, 12, 18], "expected_cycles": [0, 12],
     "horizon_h": 336, "meta_slug": "ecmwf_ifs025"},
    {"api": "ecmwf_aifs025_ensemble", "label": "AIFS", "main": True,
     "color": "#1E8449", "cycles": [0, 6, 12, 18], "expected_cycles": [0, 6, 12, 18],
     "horizon_h": 336, "meta_slug": "ecmwf_aifs025_ensemble"},
    {"api": "ncep_gefs_seamless", "label": "GEFS", "main": True,
     "color": "#B9770E", "cycles": [0, 6, 12, 18], "expected_cycles": [0, 6, 12, 18],
     "horizon_h": 336, "meta_slug": "ncep_gefs025"},
]
# Flux mean/spread : mêmes familles, labels suffixés _MEAN (couples séparés,
# cf. ci-dessus). Mêmes cycles/horizons que la famille membre.
MEAN_MODELS = [
    {"api": "ecmwf_ifs025_ensemble_mean", "label": "ECMWF_MEAN",
     "base_label": "ECMWF", "cycles": [0, 6, 12, 18], "expected_cycles": [0, 12],
     "horizon_h": 336, "meta_slug": "ecmwf_ifs025"},
    {"api": "ecmwf_aifs025_ensemble_mean", "label": "AIFS_MEAN",
     "base_label": "AIFS", "cycles": [0, 6, 12, 18], "expected_cycles": [0, 6, 12, 18],
     "horizon_h": 336, "meta_slug": "ecmwf_aifs025_ensemble"},
    {"api": "ncep_gefs025_ensemble_mean", "label": "GEFS_MEAN",
     "base_label": "GEFS", "cycles": [0, 6, 12, 18], "expected_cycles": [0, 6, 12, 18],
     "horizon_h": 336, "meta_slug": "ncep_gefs025"},
]

# Variables du flux ensemble — une ligne = une colonne du parquet.
# api    : nom Open-Meteo (paramètre `hourly`)
# col    : colonne parquet
# sites  : codes des points où la variable est CONSERVÉE au parsing (l'appel
#          multi-points renvoie tout partout ; on ne stocke que ce qui sert,
#          pour ne pas surcharger la lecture). freezing_level_height est une
#          altitude de ZONE, quasi identique aux deux points sur cette emprise :
#          UNE seule colonne, portée par les lignes village uniquement.
# spread : demander aussi `<api>_spread` au flux mean (sans objet côté membres).
# transient : colonne intermédiaire de dérivation, jamais stockée (z1000 ne
#          sert qu'à calculer l'épaisseur 1000-500).
#
# Trous STRUCTURELS constatés en conditions réelles → NaN, jamais une panne :
# AIFS ne publie ni freezing_level_height, ni snow_depth, ni wind_gusts_10m ;
# le flux mean ne sert pas freezing_level_height ; GEFS_MEAN n'a pas les
# niveaux de pression (t850/z500/t500/z1000 → épaisseur NaN). Même discipline
# que z500 côté canicule : dégradation silencieuse.
ENS_VARIABLES = [
    {"api": "temperature_2m",              "col": "t2m",    "sites": ["village", "sommet"], "spread": True},
    {"api": "temperature_850hPa",          "col": "t850",   "sites": ["village"], "spread": True},
    {"api": "pressure_msl",                "col": "pmsl",   "sites": ["village"], "spread": True},
    {"api": "geopotential_height_500hPa",  "col": "z500",   "sites": ["village"], "spread": False},
    {"api": "geopotential_height_1000hPa", "col": "z1000",  "sites": ["village"], "spread": False, "transient": True},
    {"api": "temperature_500hPa",          "col": "t500",   "sites": ["village"], "spread": False},
    {"api": "freezing_level_height",       "col": "iso0",   "sites": ["village"], "spread": True},
    # Quantité totale pluie + averses + neige : indispensable pour distinguer
    # un membre sec d'un membre pluvieux. Le discriminant t850/épaisseur vit
    # au village ; ne pas dupliquer cette variable au sommet dans l'ensemble.
    {"api": "precipitation",                "col": "precip", "sites": ["village"], "spread": False},
    {"api": "snow_depth",                  "col": "hneige", "sites": ["sommet"], "spread": False},
    {"api": "snowfall",                    "col": "neige",  "sites": ["sommet"], "spread": True},
    {"api": "wind_gusts_10m",              "col": "raf",    "sites": ["sommet"], "spread": False},
]
# Épaisseur 1000-500 hPa (discriminant pluie/neige), DÉRIVÉE au parsing :
# z500 − z1000, lignes village. Jamais dérivée sur les lignes spread (l'écart-
# type d'une différence n'est pas la différence des écarts-types → NaN).
EPAISSEUR_COL = "epaisseur"

# `kind` distingue les natures de lignes au sein du parquet :
#   "member" : un membre d'ensemble (colonne member = numéro, 0 = contrôle) ;
#   "mean"   : moyenne d'ensemble (flux mean, member = 0) ;
#   "spread" : écart-type d'ensemble, MÊMES colonnes de variables que mean
#              (valeur = spread) — compact et extensible, pas de colonnes dédiées.
ENS_KINDS = ("member", "mean", "spread")

# --------------------------------------------------------------------------- #
#  Flux maille fine — API Forecast standard (AROME France HD, ICON-D2)
# --------------------------------------------------------------------------- #
# Pattern du flux Tx/Tn HD canicule : append-only, daté par instant de collecte
# (fetched_at) — les modèles à maille fine n'exposent pas de run synoptique
# exploitable ici, et jamais de member. Horizon demandé 4 j (le besoin est
# J+2 à J+4) : les modèles s'arrêtent naturellement avant (~J+2 constaté),
# les échéances au-delà sont absentes — cas normal, pas une erreur.
#
# Trous STRUCTURELS constatés : AROME France HD ne publie ici que t2m et le
# vent/rafales — ni snowfall, ni snow_depth, ni freezing_level, ni pression
# (ICON-D2 porte seul les variables neige). NaN toléré, jamais de comblement.
HD_API_URL = "https://api.open-meteo.com/v1/forecast"
HD_MODELS = [
    {"api": "meteofrance_arome_france_hd", "label": "AROME HD"},
    {"api": "icon_d2",                     "label": "ICON-D2"},
]
HD_FORECAST_DAYS = 4
HD_VARIABLES = [
    {"api": "temperature_2m",        "col": "t2m",    "sites": ["village", "sommet"]},
    # Les deux points sont nécessaires à la coupe verticale J0–J+3.
    {"api": "precipitation",         "col": "precip", "sites": ["village", "sommet"]},
    {"api": "snowfall",              "col": "neige",  "sites": ["village", "sommet"]},
    {"api": "snow_depth",            "col": "hneige", "sites": ["sommet"]},
    {"api": "freezing_level_height", "col": "iso0",   "sites": ["village"]},
    {"api": "wind_gusts_10m",        "col": "raf",    "sites": ["sommet"]},
]

# --------------------------------------------------------------------------- #
#  Flux PNT Météo-France ciblés — AROME local / ensembles régionaux
# --------------------------------------------------------------------------- #
# Les GRIB ne sont jamais stockés : seules les cellules proches des deux sites
# sont normalisées dans des parquets dédiés. PE-AROME est volontairement
# journalier : le WCS impose une requête par membre, paramètre ET échéance ;
# l'horaire 25 × 51 provoquerait une avalanche de requêtes sans valeur ajoutée,
# puisque AROME-PI/IFS portent le timing fin.
AROME_PI_MODEL = "AROME-PI"
AROME_IFS_MODEL = "AROME-IFS"
PE_AROME_MODEL = "PE-AROME"
MF_LOCAL_MODELS = (AROME_PI_MODEL, AROME_IFS_MODEL, PE_AROME_MODEL)
MF_LOCAL_API_KEY_ENVS = {
    AROME_PI_MODEL: "METEOFRANCE_AROME_PI_KEY",
    AROME_IFS_MODEL: "METEOFRANCE_AROME_KEY",
    PE_AROME_MODEL: "METEOFRANCE_AROME_PE_KEY",
}
AROME_PI_HORIZON_H = 6
AROME_PI_STEPS_S = tuple(range(3_600, 21_601, 3_600))
AROME_PI_REQUEST_INTERVAL_S = 0.15
AROME_PI_CATALOG_ATTEMPTS = 3
AROME_PI_CATALOG_RETRY_S = 5.0
AROME_PI_WCS_URL = (
    "https://public-api.meteofrance.fr/public/aromepi/wcs/"
    "MF-NWP-HIGHRES-AROMEPI-001-FRANCE-WCS/{operation}"
)
AROME_PI_PRODUCTS = {
    "precip": ("TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE", "PT1H"),
    "neige_eau": ("TOTAL_SNOW_PRECIPITATION__GROUND_OR_WATER_SURFACE", "PT1H"),
    "ptype": ("PRECIPITATION_TYPE_15_MIN__GROUND_OR_WATER_SURFACE", None),
    "t2m": ("TEMPERATURE__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND", None),
}
AROME_IFS_HORIZON_H = 45
AROME_IFS_STEPS_S = tuple(range(3_600, AROME_IFS_HORIZON_H * 3_600 + 1, 3_600))
AROME_IFS_REQUEST_INTERVAL_S = 1.3       # quota API AROME : 50 requêtes/min
AROME_IFS_WCS_URL = (
    "https://public-api.meteofrance.fr/public/arome/wcs/"
    "MF-NWP-HIGHRES-AROMEIFS-0025-FRANCE-WCS/{operation}"
)
# Total et neige directs suffisent à diagnostiquer la phase ; redemander le
# code ptype sur 45 échéances consommerait 45 appels sans modifier le verdict.
AROME_IFS_PRODUCTS = {
    "precip": ("TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE", "PT1H"),
    "neige_eau": ("TOTAL_SNOW_PRECIPITATION__GROUND_OR_WATER_SURFACE", "PT1H"),
    "t2m": ("TEMPERATURE__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND", None),
}
PE_AROME_MEMBER_COUNT = 25                 # contrôle 000 + 24 perturbations
PE_AROME_HORIZON_H = 51
PE_AROME_DAILY_STEPS_S = (86_400, 172_800) # fins des fenêtres 0–24 h / 24–48 h
PE_AROME_REQUEST_INTERVAL_S = 1.3          # < 50 req/min, à affiner si quota évolue
PE_AROME_WCS_URL_TPL = (
    "https://public-api.meteofrance.fr/public/pearome/wcs/"
    "MF-NWP-HIGHRES-PEARO{member:03d}-0025-FRANCE-WCS/{operation}"
)
PE_AROME_PRODUCTS = {
    "precip": "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE",
    "neige_eau": "TOTAL_SNOW_PRECIPITATION__GROUND_OR_WATER_SURFACE",
}

# PE-ARPEGE prolonge la probabilité locale après PE-AROME. Le catalogue
# publie aussi des cycles 06/18Z, mais seuls 00/12Z sont collectés ici : ce
# sont les cycles complets validés pour l'archivage. Les cumuls P1D glissants
# existent bien à H24/H48/H72/H96 dans DescribeCoverage (horizon H102).
PE_ARPEGE_MODEL = "PE-ARPEGE"
PE_ARPEGE_API_KEY_ENV = "METEOFRANCE_ARPEGE_PE_KEY"
PE_ARPEGE_MEMBER_COUNT = 35               # contrôle 000 + 34 perturbations
PE_ARPEGE_ALLOWED_RUN_HOURS = (0, 12)
# Le contrôle peut publier un nouveau cycle avant les perturbations. Le membre
# 1 sert de sentinelle de disponibilité de l'ensemble avant les 280 appels GRIB.
PE_ARPEGE_DISCOVERY_MEMBERS = (0, 1)
PE_ARPEGE_DAILY_STEPS_S = (86_400, 172_800, 259_200, 345_600)
PE_ARPEGE_REQUEST_INTERVAL_S = 0.2         # < 400 req/min, transfert dominant
# La requête ne porte que sur quelques mailles autour de Megève. La borne de
# taille empêche une évolution du WCS de réintroduire silencieusement les
# grilles Europe complètes (environ 0,8 Mo par champ, 280 champs par cycle).
PE_ARPEGE_SPATIAL_MARGIN_DEG = 0.10
PE_ARPEGE_MAX_GRIB_BYTES = 10_000
PE_ARPEGE_COVERAGE_ATTEMPTS = 3
PE_ARPEGE_COVERAGE_RETRY_DELAY_S = 1.0
PE_ARPEGE_WCS_URL_TPL = (
    "https://public-api.meteofrance.fr/public/pearpege/wcs/"
    "MF-NWP-GLOBAL-PEARP{member:03d}-01-EUROPE-WCS/{operation}"
)
PE_ARPEGE_PRODUCTS = {
    "precip": "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE",
    "neige_eau": "TOTAL_SNOW_PRECIPITATION__GROUND_OR_WATER_SURFACE",
}

# Schéma commun préparé pour PI/IFS/PE-AROME. Les colonnes non publiées par
# une source restent NaN ; elles ne sont jamais comblées depuis un autre
# modèle. ``neige_eau`` est l'équivalent en eau (mm), distinct des cm affichés.
MF_LOCAL_VAR_COLS = [
    "precip", "neige_eau", "pluie_eau", "ptype", "t2m", "iso0", "t850",
    "epaisseur", "pmsl", "cape",
]
MF_LOCAL_SCHEMA = [
    "run_date", "model", "kind", "member", "site", "valid_time",
    "period_h", "cell_lat", "cell_lon",
] + MF_LOCAL_VAR_COLS

# Schéma identique mais parquet volontairement distinct : PE-ARPEGE est un
# ensemble régional/global plus volumineux, avec sa propre cadence et sa
# propre archive. Les colonnes futures restent NaN sur l'historique existant.
MF_REGIONAL_VAR_COLS = list(MF_LOCAL_VAR_COLS)
MF_REGIONAL_SCHEMA = list(MF_LOCAL_SCHEMA)

# Archive compacte de long terme : une ligne moyenne par cycle/modèle/site/
# échéance et le nombre de membres source. ``ptype`` y est un mode, jamais
# une moyenne de codes. Le brut reste disponible en HOT/COLD pour recalibrer.
MF_SUMMARY_SCHEMA = [
    "run_date", "model", "site", "valid_time", "period_h", "n_members",
    "cell_lat", "cell_lon",
] + MF_LOCAL_VAR_COLS
MF_SUMMARY_VAR_COLS = list(MF_LOCAL_VAR_COLS)

# --------------------------------------------------------------------------- #
#  Seuils du pipeline (fraîcheur / complétude / anti-régression)
# --------------------------------------------------------------------------- #
# Mêmes valeurs éprouvées que le pipeline canicule (cf. config.py racine pour
# la justification détaillée de chacun).
FRESHNESS_EPS = 0.05            # écart moyen abs. minimal PAR ÉCHÉANCE (fraîcheur)
PERSIST_HORIZON_TOLERANCE_H = 24
MIN_PERSIST_HORIZON_H = 312     # repli si horizon_h inconnu (aucun modèle actuel)
PERSIST_MAX_GAP_H = 24          # trou max dans la chaîne contiguë de portée réelle

# --------------------------------------------------------------------------- #
#  Domaine neige — seuils et régimes (validés)
# --------------------------------------------------------------------------- #
# Architecture MULTI-SIGNAUX : pas de variable pivot unique (contrairement au
# PRIMARY_VAR du canicule) — les signaux se hiérarchisent PAR ÉCHÉANCE, trois
# régimes déclarés ci-dessous. z500/t500 restent du contexte synoptique pur :
# JAMAIS de seuil de risque dessus (même discipline que Z500 côté canicule).
HORIZON_REGIMES = [
    {"max_j": 3,  "pivots": ["neige", "precip", "iso0"],
     "desc": "maille fine 48 h : phase et quantité horaires par altitude"},
    {"max_j": 7,  "pivots": ["precip", "iso0", "epaisseur", "t850"],
     "desc": "classification des membres et intensité du froid"},
    {"max_j": 15, "pivots": ["precip", "epaisseur", "t850", "pmsl"],
     "desc": "classification des membres, masse d'air et timing du régime"},
]

# Limite pluie-neige ≈ iso 0° − marge (la neige tient ~300 m sous l'isotherme 0°).
LPN_MARGE_M = 300

# t850 (le niveau 850 hPa est à ~1450-1500 m, ENTRE les deux points — pivot
# naturel) : neige probable si t850 ≤ seuil du site ; redoux pluvieux au-delà
# de SEUIL_T850_REDOUX quel que soit le site.
SEUIL_T850_NEIGE = {"sommet": 1.0, "village": -1.0}   # °C
SEUIL_T850_REDOUX = 3.0                                # °C

# pmsl : signal de TIMING uniquement (bascule de régime si la médiane chute
# d'au moins ce montant en 24 h) — jamais un critère neige en soi.
PMSL_BASCULE_HPA_24H = 5.0

# KPI « jour à neige » (proba × sévérité, calqué sur le KPI risque canicule) :
# proba journalière (membres poolés : cumul du jour ≥ SEUIL_NEIGE_JOUR_CM)
# ≥ PROB_MIN, OU cumul moyen attendu ≥ CUMUL_MIN — le second critère capte les
# queues neigeuses à proba modeste. Paliers d'intensité pour les libellés.
SEUIL_NEIGE_JOUR_CM = 1.0
KPI_NEIGE_PROB_MIN = 0.50
KPI_NEIGE_CUMUL_MIN_CM = 5.0
PALIERS_NEIGE_CM = [1.0, 5.0, 20.0]   # petite / vraie / grosse chute

# Seuil d'AFFICHAGE du graphique neige, volontairement plus sensible que le
# KPI « jour à neige » mais assez haut pour ne pas présenter quelques traces
# numériques de membres isolés comme un épisode crédible. Les sorties brutes
# restent stockées et accessibles dans Explorer ; seule la lecture principale
# est filtrée. Le P90 ≥ 1 cm capte aussi une queue significative même si la
# probabilité agrégée ou la moyenne restent modestes.
DISPLAY_NEIGE_PROB_MIN = 0.10
DISPLAY_NEIGE_EXPECTED_MIN_CM = 0.5

# Horizon plein empirique des vues combinées (même logique que le canicule :
# mesuré sur la portée réelle du run stocké, jamais une règle d'heure de cycle).
FULL_HORIZON_TOLERANCE_H = 24

# Contrôle opérationnel : fenêtre récente assez longue pour couvrir plusieurs
# cycles de chaque famille sans transformer la page en audit d'archive.
RUN_QUALITY_LOOKBACK_DAYS = 7

# Couleurs d'affichage par site (graphiques multi-sites).
SITE_COLORS = {"village": "#1F618D", "sommet": "#7D3C98"}

# --------------------------------------------------------------------------- #
#  Flux annexe observations Météo-France (DPPaquetObs — Alpes du Nord)
# --------------------------------------------------------------------------- #
# Même API/clé que le flux observations du canicule (contexte DPPaquetObs/v2,
# secret METEOFRANCE_API_KEY — règle absolue de sécurité de la clé identique,
# cf. CLAUDE.md), mais département 74 et parquet SÉPARÉ dans apps/snow/data/.
# La mécanique générique (paquet département, dédup (station_id, valid_time),
# append-only) vit dans core/pipeline/observations.py ; le pipeline canicule
# racine garde ses implémentations inline (partie critique — jamais rebranchée
# sur core/, duplication du motif assumée).
OBS_API_BASE = "https://public-api.meteofrance.fr/public/DPPaquetObs/v2"
OBS_DEPARTEMENT = "74"                    # Haute-Savoie : toutes les stations en UN appel
OBS_API_KEY_ENV = "METEOFRANCE_API_KEY"   # même secret que le canicule (mono-source)

# id        : id_station DPObs (8 chiffres = INSEE commune + n° poste, vérifiés
#             via les relevés publics Infoclimat/Météociel qui republient ces
#             identifiants ; le mode --list-stations de fetch_observations.py
#             permet de les recontrôler sur le paquet réel).
# reference : True = les trois stations de référence du suivi Megève (village /
#             sommet / haute montagne) ; False = stations d'appoint (contexte
#             vallées). Champ d'AFFICHAGE (ordre, mise en avant), pas un filtre
#             de collecte : toutes les stations listées sont persistées.
# L'instrumentation varie fortement d'une station à l'autre (stations montagne
# sans pression, hauteur de neige publiée par un sous-ensemble seulement) :
# toute variable absente reste NaN — absence STRUCTURELLE, jamais une panne.
OBS_STATIONS = [
    {"id": "74083002", "nom": "Combloux",        "alt": 1183, "reference": True,
     "color": "#1F618D",
     "profil": "balcon face au Mont-Blanc — proxy du village de Megève (~4 km)"},
    {"id": "74236002", "nom": "Mont d'Arbois",   "alt": 1833, "reference": True,
     "color": "#7D3C98",
     "profil": "sommet du domaine — le point « sommet » du dashboard"},
    {"id": "74056006", "nom": "Aiguille du Midi", "alt": 3842, "reference": True,
     "color": "#117864",
     "profil": "haute montagne — état de l'atmosphère libre (≈ niveau 650 hPa)"},
    {"id": "74056001", "nom": "Chamonix",        "alt": 1042, "reference": False,
     "color": "#B9770E",
     "profil": "fond de vallée alpine voisine (inversions, redoux)"},
    {"id": "74182001", "nom": "Annecy-Meythet",  "alt": 458,  "reference": False,
     "color": "#C0392B",
     "profil": "avant-pays — référence basse altitude du gradient thermique"},
]

# Variables du paquet horaire — api = champ JSON DPObs, col = colonne parquet,
# conv = conversion AU PARSING (avant stockage), mêmes règles que le canicule :
# t/td/tx/tn en Kelvin → °C, ht_neige en m → cm (échelle usuelle des cumuls
# Open-Meteo `neige`/`hneige`). u (%), dd (°), ff/raf (m/s), rr1 (mm) tels
# quels. Pas de pression : aucune des stations suivies n'est en plaine
# instrumentée pour ça de façon fiable, et pmsl est déjà couvert par le flux
# ensemble — inutile de stocker une colonne structurellement vide.
OBS_VARIABLES = [
    {"api": "t",        "col": "t",         "conv": "kelvin"},
    {"api": "td",       "col": "td",        "conv": "kelvin"},
    {"api": "tx",       "col": "tx",        "conv": "kelvin"},
    {"api": "tn",       "col": "tn",        "conv": "kelvin"},
    {"api": "u",        "col": "humidite",  "conv": None},
    {"api": "dd",       "col": "vent_dir",  "conv": None},
    {"api": "ff",       "col": "vent_ff",   "conv": None},
    {"api": "raf",      "col": "raf",       "conv": None},
    {"api": "rr1",      "col": "precip_1h", "conv": None},
    {"api": "ht_neige", "col": "hneige",    "conv": "m_to_cm"},
]

OBS_PERIMEE_H = 3       # au-delà : l'obs est signalée « ancienne », jamais « actuelle »
OBS_FENETRE_GRAPHE_H = 72   # fenêtre du graphique inter-stations
OBS_JOUR_COMPLET_MIN_H = 20  # heures min pour juger un Tx/Tn journalier fiable

# --------------------------------------------------------------------------- #
#  Archivage hot/cold (core/pipeline/hot_cold.py — rollover périodique)
# --------------------------------------------------------------------------- #
# Fenêtre HOT commune aux parquets neige : 45 j couvre l'horizon 15 j,
# la fenêtre de convergence (runs _MEAN) et une marge confortable. Le rollover
# (apps/snow/pipeline/rollover.py) bascule le plus ancien vers les parquets
# *_archive (COLD, append-only, jamais réécrits hors append vérifié).
HOT_RETENTION_DAYS = 45

# --------------------------------------------------------------------------- #
#  Stockage
# --------------------------------------------------------------------------- #
SNOW_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SNOW_DIR, "data")
DB_ENS_PATH = os.path.join(DATA_DIR, "db_megeve.parquet")
DB_HD_PATH = os.path.join(DATA_DIR, "db_megeve_hd.parquet")
DB_OBS_PATH = os.path.join(DATA_DIR, "db_obs_alpes.parquet")
DB_MF_LOCAL_PATH = os.path.join(DATA_DIR, "db_megeve_mf_local.parquet")
DB_MF_REGIONAL_PATH = os.path.join(DATA_DIR, "db_megeve_arpege_pe.parquet")
DB_MF_SUMMARY_PATH = os.path.join(DATA_DIR, "db_megeve_mf_summary.parquet")
# Parquets COLD du rollover hot/cold — extension .parquet volontaire (les jobs
# CI les committent), les SAUVEGARDES datées du rollover prennent elles
# l'extension .bak pour ne jamais matcher les globs `*.parquet` des jobs.
DB_ENS_COLD_PATH = os.path.join(DATA_DIR, "db_megeve_archive.parquet")
DB_HD_COLD_PATH = os.path.join(DATA_DIR, "db_megeve_hd_archive.parquet")
DB_OBS_COLD_PATH = os.path.join(DATA_DIR, "db_obs_alpes_archive.parquet")
DB_MF_LOCAL_COLD_PATH = os.path.join(DATA_DIR, "db_megeve_mf_local_archive.parquet")
DB_MF_REGIONAL_COLD_PATH = os.path.join(
    DATA_DIR, "db_megeve_arpege_pe_archive.parquet")

# --------------------------------------------------------------------------- #
#  Dérivés (ne pas éditer)
# --------------------------------------------------------------------------- #
SITE_CODES = [s["code"] for s in SITES]
SITE_BY_CODE = {s["code"]: s for s in SITES}
ENS_VAR_COLS = [v["col"] for v in ENS_VARIABLES if not v.get("transient")] + [EPAISSEUR_COL]
ENS_SCHEMA = ["run_date", "model", "kind", "member", "site", "valid_time"] + ENS_VAR_COLS
ENS_LABELS = [m["label"] for m in ENS_MODELS]
MEAN_LABELS = [m["label"] for m in MEAN_MODELS]
HORIZON_BY_LABEL = {m["label"]: m.get("horizon_h")
                    for m in ENS_MODELS + MEAN_MODELS}
HD_VAR_COLS = [v["col"] for v in HD_VARIABLES]
OBS_STATION_IDS = [s["id"] for s in OBS_STATIONS]
OBS_STATION_BY_ID = {s["id"]: s for s in OBS_STATIONS}
OBS_NOM_BY_ID = {s["id"]: s["nom"] for s in OBS_STATIONS}
OBS_COLOR_BY_NOM = {s["nom"]: s["color"] for s in OBS_STATIONS}
OBS_VAR_COLS = [v["col"] for v in OBS_VARIABLES]
OBS_SCHEMA = ["valid_time", "station_id", "station_nom"] + OBS_VAR_COLS
HD_SCHEMA = ["fetched_at", "model", "site", "target_datetime"] + HD_VAR_COLS
HD_LABELS = [m["label"] for m in HD_MODELS]
COLOR_BY_LABEL = {m["label"]: m["color"] for m in ENS_MODELS}
MEAN_LABEL_BY_BASE = {m["base_label"]: m["label"] for m in MEAN_MODELS}
EXPECTED_CYCLES_BY_LABEL = {m["label"]: m.get("expected_cycles", m["cycles"])
                            for m in ENS_MODELS + MEAN_MODELS}
