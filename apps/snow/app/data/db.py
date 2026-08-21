# -*- coding: utf-8 -*-
"""Couche données du dashboard neige : lecture des parquets produits par
apps/snow/pipeline/ (ensemble global, PNT Météo-France local et maille fine),
en LECTURE SEULE.

Invariants : stockage en UTC tz-naïf, conversion vers l'heure de Paris
SEULEMENT à l'affichage (ici, dès load_db — tout le dashboard est de
l'affichage) ; les cycles réels (0/6/12/18Z) se retrouvent via utc_cycle().
Parquet absent/vide/corrompu → DataFrame vide, dégradation silencieuse
(jamais un crash, jamais une alerte intrusive)."""

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from apps.snow.app.data.store import (flux_signature as _signature, read_flux,
                                      read_flux_fenetre)
from ..runtime import LOCAL_TZ


def db_signature():
    """Signature combinée hot + cold (rollover hot/cold) : le cache s'invalide
    aussi bien après une collecte qu'après une bascule d'archive."""
    return (_signature(SC.DB_ENS_PATH), _signature(SC.DB_ENS_COLD_PATH))


def hd_signature():
    return _signature(SC.DB_HD_PATH)


def mf_local_signature():
    return _signature(SC.DB_MF_LOCAL_PATH)


def mf_regional_signature():
    return _signature(SC.DB_MF_REGIONAL_PATH)


def mf_summary_signature():
    return _signature(SC.DB_MF_SUMMARY_PATH)


def _to_paris(df, cols):
    for col in cols:
        s = pd.to_datetime(df[col])
        df[col] = s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return df


def _align_schema(df, schema):
    """Réaligne une base historique sur le schéma courant sans la réécrire.

    Une variable ajoutée après le début de la collecte reste ``NaN`` sur
    l'historique : absence explicite, jamais zéro inventé ni migration
    rétroactive des parquets.

    Le DataFrame reçu sort d'une lecture parquet et n'appartient qu'à
    l'appelant : il est complété EN PLACE, sans copie préalable — sur le flux
    ensemble, cette copie doublait à elle seule le pic mémoire du chargement.
    """
    for col in schema:
        if col not in df.columns:
            df[col] = float("nan")
    return df[schema]


# Colonnes suffisantes pour situer la fenêtre des membres (passe 1 de la
# lecture en deux temps) — quelques Mo au lieu du flux entier.
_SONDE_ENS = ("run_date", "kind")


def _filtres_membres_recents(sonde):
    """Filtres pyarrow (forme DNF : liste de conjonctions) ne gardant que les
    lignes MEMBRES des ``ENS_MEMBERS_WINDOW_DAYS`` derniers jours de collecte.

    La borne se calcule sur le dernier run MEMBRE réellement stocké, jamais
    sur l'horloge : une base qui date de la veille reste lisible en entier.
    Aucun run membre en base (cas d'un flux mean seul) → filtre sur le seul
    `kind`, qui ne renverra rien."""
    membres = sonde.loc[sonde["kind"] == "member", "run_date"]
    if membres.empty:
        return _MEMBRES_SEULS
    depuis = (pd.Timestamp(membres.max())
              - pd.Timedelta(days=SC.ENS_MEMBERS_WINDOW_DAYS))
    return [[("kind", "==", "member"), ("run_date", ">=", depuis)]]


# Membres seuls, sans borne temporelle (base ne contenant aucun run membre).
_MEMBRES_SEULS = [[("kind", "==", "member")]]


# Colonnes converties en CATÉGORIES dès la lecture Arrow (jamais après coup :
# un recast a posteriori construirait un second exemplaire de la table).
# Ces trois colonnes ne prennent qu'une poignée de libellés, mais une chaîne
# stockée par ligne y coûte à elle seule un quart de la table.
_CATEGORIES_ENS = ("model", "kind", "site")


def _compact_dtypes(df):
    """Dtypes minimaux à information CONSTANTE — le flux ensemble tient
    entièrement en mémoire et pèse à lui seul l'essentiel de l'empreinte du
    dashboard. Complète les catégories déjà posées à la lecture.

    Conversion STRICTEMENT sans perte, les valeurs restant au bit près celles
    du parquet : ``member`` est un indice d'ensemble à deux chiffres. Les
    colonnes de variables restent en float64 : le float32 les couvrirait
    largement en précision physique, mais introduirait un écart numérique que
    les tests compareraient à jamais à des références divergentes. La colonne
    est réaffectée seule, jamais via un astype de tout le DataFrame — qui en
    recopierait chaque colonne pour n'en changer qu'une."""
    if "member" in df.columns:
        df["member"] = df["member"].astype("int16")
    return df


# Membres exclus dès la lecture parquet (lecture du seul flux mean/spread).
_SANS_MEMBRES = [[("kind", "!=", "member")]]


def _read_ens(path, membres="tous"):
    """Un parquet au schéma ensemble → DataFrame filtré (labels de config),
    compacté et converti en heure de Paris. Absent/corrompu → vide,
    dégradation silencieuse.

    ``membres`` règle le sort des lignes MEMBRES, seule partie volumineuse du
    flux, TOUJOURS dans la lecture parquet et jamais après coup : « tous »,
    « fenetre » (membres des runs récents SEULS, cf. _filtres_membres_recents)
    ou « exclus » (flux lu uniquement pour ses mean/spread)."""
    if membres == "fenetre":
        df = read_flux_fenetre(path, _SONDE_ENS, _filtres_membres_recents,
                               categories=_CATEGORIES_ENS)
    elif membres == "exclus":
        df = read_flux(path, filters=_SANS_MEMBRES, categories=_CATEGORIES_ENS)
    else:
        df = read_flux(path, categories=_CATEGORIES_ENS)
    if df is None:
        return pd.DataFrame(columns=SC.ENS_SCHEMA)
    df = _align_schema(df, SC.ENS_SCHEMA)
    # Modèles retirés de la config : lignes orphelines écartées. Le masque
    # n'est appliqué que s'il retire vraiment quelque chose — un filtrage
    # booléen recopie tout le DataFrame, prix inutile dans le cas courant où
    # aucune ligne n'est orpheline.
    connus = df["model"].isin(SC.ENS_LABELS + SC.MEAN_LABELS)
    if not connus.all():
        df = df[connus].reset_index(drop=True)
    return _to_paris(_compact_dtypes(df), ("run_date", "valid_time"))


def _read_mf_local(path):
    """Parquet PNT Météo-France local, tolérant au schéma progressif."""
    df = read_flux(path)
    if df is None:
        return pd.DataFrame(columns=SC.MF_LOCAL_SCHEMA)
    df = _align_schema(df, SC.MF_LOCAL_SCHEMA)
    return _to_paris(df, ("run_date", "valid_time"))


def _read_mf_regional(path):
    """Parquet PE-ARPEGE dédié, absent tant que le flux n'a pas tourné."""
    df = read_flux(path)
    if df is None:
        return pd.DataFrame(columns=SC.MF_REGIONAL_SCHEMA)
    df = _align_schema(df, SC.MF_REGIONAL_SCHEMA)
    return _to_paris(df, ("run_date", "valid_time"))


def _read_mf_summary(path):
    """Archive moyenne compacte, tolérante aux futures colonnes ajoutées."""
    df = read_flux(path)
    if df is None:
        return pd.DataFrame(columns=SC.MF_SUMMARY_SCHEMA)
    df = _align_schema(df, SC.MF_SUMMARY_SCHEMA)
    return _to_paris(df, ("run_date", "valid_time"))


def _concat(frames):
    """Recollement de flux au schéma ensemble, les DataFrames VIDES écartés.

    Un flux absent est représenté par un DataFrame vide construit sur le seul
    schéma, donc à colonnes `object` : le concaténer dégraderait le dtype des
    colonnes du flux réel (des dates redeviendraient des objets, et toute
    arithmétique de temps en aval échouerait). Tous vides → le schéma seul."""
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=SC.ENS_SCHEMA)
    if len(frames) == 1:
        return frames[0]
    return pd.concat(frames, ignore_index=True)


@st.cache_resource(show_spinner=False, max_entries=1)
def members_db(sig):
    """Lignes MEMBRES des runs récents (flux Ensemble API) — le pool des vues
    probabilistes, et de très loin la partie volumineuse de la base.

    `sig` (db_signature) est un paramètre HASHÉ, jamais préfixé d'un underscore :
    c'est lui — et lui seul — qui fait entrer une nouvelle collecte dans le
    dashboard. Masqué du hachage, il figerait la base pour toute la vie du
    process, le `st.cache_data.clear()` du bouton « Rafraîchir » ne touchant
    pas le cache RESSOURCE (que le bouton vide bien, lui aussi).
    `max_entries=1` borne la contrepartie : seule la signature courante est
    retenue en mémoire.

    Cachée en `cache_resource` et NON en `cache_data` : cette dernière
    sérialise (pickle) la valeur retournée et en désérialise une COPIE
    COMPLÈTE à chaque appel — soit, pour une base de plusieurs centaines de
    Mo, autant de copies que de fonctions cachées qui l'appellent (pools de
    runs, page Explorer, fraîcheur de la sidebar), au-delà du quota mémoire de
    Streamlit Cloud (le process est alors tué sans trace dans les logs
    applicatifs). `cache_resource` partage UNE instance : le dashboard étant
    strictement en lecture seule sur la base (tous les appelants filtrent
    immédiatement, et un filtrage pandas renvoie une copie), aucun appelant ne
    peut muter l'objet partagé.

    Les membres sont bornés à ENS_MEMBERS_WINDOW_DAYS : c'est la seule partie
    du flux qui croît sans limite depuis la sortie de git (plus de rollover
    pour retailler le parquet), et elle ne sert qu'aux runs récents. Le
    magasin, lui, conserve tout."""
    return _read_ens(SC.DB_ENS_PATH, membres="fenetre")


@st.cache_resource(show_spinner=False, max_entries=1)
def mean_spread_db(sig):
    """Lignes mean/spread du flux Ensemble Mean, hot + cold et SANS fenêtre :
    rétention API longue, c'est l'historique de la convergence. ~1 % du volume
    du flux, d'où l'absence de borne temporelle.

    Dédup défensive au cas où un crash de rollover aurait laissé un
    recouvrement entre archive et hot (hot prioritaire)."""
    df = _concat([_read_ens(SC.DB_ENS_COLD_PATH, membres="exclus"),
                  _read_ens(SC.DB_ENS_PATH, membres="exclus")])
    if df.empty:
        return df
    return df.drop_duplicates(subset=["run_date", "model", "kind", "member",
                                      "site", "valid_time"], keep="last")


def mean_db(sig, kind="mean"):
    """Lignes mean (ou spread) du flux Ensemble Mean — support de
    l'historique/convergence."""
    df = mean_spread_db(sig)
    return df[df["kind"] == kind]


def load_db(sig):
    """Flux ensemble complet tel que le voit le dashboard : membres des runs
    récents + mean/spread sur tout l'historique.

    Volontairement NON caché, à la différence de ses deux composants : ce
    recollement en garderait une copie permanente, soit les membres tenus deux
    fois en mémoire. Les vues lisent le flux dont elles ont besoin
    (`members_db` ou `mean_db`) ; ce recollement n'est qu'une commodité pour
    une lecture ponctuelle de la base entière."""
    return _concat([members_db(sig), mean_spread_db(sig)])


@st.cache_data(show_spinner=False)
def load_hd(sig):
    """Base maille fine (append-only). fetched_at / target_datetime convertis
    UTC → heure de Paris (naïf)."""
    df = read_flux(SC.DB_HD_PATH) if sig is not None else None
    if df is None:
        return pd.DataFrame(columns=SC.HD_SCHEMA)
    df = _align_schema(df, SC.HD_SCHEMA)
    return _to_paris(df, ("fetched_at", "target_datetime"))


@st.cache_data(show_spinner=False)
def load_mf_local(sig):
    """Runs locaux/régionaux HOT ; l'absence du nouveau parquet est normale."""
    return _read_mf_local(SC.DB_MF_LOCAL_PATH)


@st.cache_data(show_spinner=False)
def load_mf_regional(sig):
    """Runs PE-ARPEGE HOT ; le parquet dédié peut ne pas encore exister."""
    return _read_mf_regional(SC.DB_MF_REGIONAL_PATH)


@st.cache_data(show_spinner=False)
def load_mf_summary(sig):
    """Historique compact des moyennes PI/IFS/PE-AROME/PE-ARPEGE."""
    return _read_mf_summary(SC.DB_MF_SUMMARY_PATH)


def mf_local_members(sig):
    """Membres PE-AROME au village, sans les déterministes PI/IFS futurs."""
    df = load_mf_local(sig)
    return df[(df["kind"] == "member") & (df["model"] == SC.PE_AROME_MODEL)]


def latest_mf_local_members(sig):
    """Dernier cycle PE-AROME complet stocké, sans mélanger les runs."""
    df = mf_local_members(sig)
    if df.empty:
        return df
    return df[df["run_date"] == df["run_date"].max()].reset_index(drop=True)


def latest_mf_regional_members(sig):
    """Dernier cycle PE-ARPEGE complet stocké, sans mélanger les runs."""
    df = load_mf_regional(sig)
    df = df[(df["kind"] == "member") & (df["model"] == SC.PE_ARPEGE_MODEL)]
    if df.empty:
        return df
    return df[df["run_date"] == df["run_date"].max()].reset_index(drop=True)


def latest_mf_local_deterministic(sig, model):
    """Dernier cycle d'un modèle local déterministe, sans mélanger les runs.

    Le filtre de fraîcheur météorologique reste à la logique du domaine :
    cette couche de lecture ne transforme jamais un vieux cycle en prévision
    courante et ne substitue jamais silencieusement un autre modèle.
    """
    if model not in SC.MF_LOCAL_MODELS:
        raise ValueError(f"Modèle Météo-France local inconnu : {model}")
    df = load_mf_local(sig)
    df = df[(df["kind"] == "deterministic") & (df["model"] == model)]
    if df.empty:
        return df
    return df[df["run_date"] == df["run_date"].max()].reset_index(drop=True)


def utc_cycle(local_run_date):
    """Reconvertit un run_date affiché (heure de Paris) vers son instant UTC
    réel — nécessaire pour retrouver le vrai cycle synoptique (0/6/12/18Z)."""
    return pd.Timestamp(local_run_date).tz_localize(LOCAL_TZ).tz_convert("UTC")


def run_label_text(local_run_date):
    """Nom du run d'après son vrai cycle UTC (jamais l'heure locale)."""
    u = utc_cycle(local_run_date)
    return f"{u:%d %b %Y} — {u.hour:02d}Z"


@st.cache_data(show_spinner=False)
def list_runs(sig):
    """Runs membres disponibles (run_date distinctes), du plus récent au plus
    ancien — les runs _MEAN ne pilotent pas la navigation (flux d'appui)."""
    df = members_db(sig)
    if df.empty:
        return pd.DataFrame(columns=["run_date", "label"])
    runs = pd.DataFrame({"run_date": sorted(df["run_date"].unique(), reverse=True)})
    runs["label"] = runs["run_date"].apply(run_label_text)
    return runs.reset_index(drop=True)
