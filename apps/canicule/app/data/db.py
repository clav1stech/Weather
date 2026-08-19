# -*- coding: utf-8 -*-
"""Couche données : lecture de la base plate unique produite par Forecast.py
(data/database_paris.parquet : [run_date, model, member, valid_time, t850…])
et conversions run_date ↔ cycle synoptique UTC.

Invariant : stockage en UTC tz-naïf, conversion vers l'heure de Paris SEULEMENT
à l'affichage (ici, dès load_db, car tout le dashboard est de l'affichage) ;
les cycles réels (0/6/12/18Z) se retrouvent via utc_cycle()."""

import os

import pandas as pd
import streamlit as st

import config as C
from app.runtime import LOCAL_TZ
from app.data.store import load_store_df, store_active, store_signature


def db_signature():
    """Signature (mtimes hot + archive) → invalide le cache au moindre nouveau
    run comme après un rollover hot/cold. Tant que l'archive n'existe pas
    (archivage canicule non activé), seule la composante hot varie — clé de
    cache opaque, aucun appelant n'en inspecte le contenu.

    En mode magasin externe (WEATHER_STORE, docs/DESIGN_sortie_git.md) : la
    signature devient l'ensemble des (nom, etag) des partitions — même rôle de
    clé opaque, invalidée dès qu'une partition change (mois courant re-uploadé).
    Magasin injoignable (erreur réseau/API) → repli silencieux sur la signature
    git ci-dessous, cohérent avec le repli de load_db (le parquet git reste le
    filet de sécurité derrière le magasin, jamais un crash de page)."""
    if store_active():
        try:
            sig = store_signature()
        except Exception:  # noqa: BLE001 — magasin injoignable, repli sur git
            sig = None
        if sig is not None:
            return sig
    sigs = []
    for path in (C.DB_PATH, C.DB_ARCHIVE_PATH):
        try:
            sigs.append(os.path.getmtime(path))
        except OSError:
            sigs.append(None)
    return None if sigs[0] is None else tuple(sigs)


@st.cache_resource(show_spinner=False, max_entries=1)
def load_db(sig):
    """Base complète. run_date / valid_time convertis UTC → heure de Paris (naïf).

    `sig` (db_signature) est un paramètre HASHÉ, jamais préfixé d'un underscore :
    c'est lui — et lui seul — qui fait entrer une nouvelle collecte dans le
    dashboard. Masqué du hachage, il figerait la base pour toute la vie du
    process, le `st.cache_data.clear()` du bouton « Rafraîchir » ne touchant pas
    le cache RESSOURCE. `max_entries=1` en borne la contrepartie : la base pèse
    plusieurs centaines de Mo, seule la signature courante est retenue.

    Cachée en `cache_resource` et NON en `cache_data` : cette dernière sérialise
    (pickle) la valeur retournée et en désérialise une COPIE COMPLÈTE à chaque
    appel — soit, pour une base de plusieurs centaines de Mo, autant de copies
    que de fonctions cachées qui l'appellent (runsets, presence…), au-delà du
    quota mémoire de Streamlit Cloud (le process est alors tué sans trace dans
    les logs applicatifs). `cache_resource` partage UNE instance : le dashboard
    étant strictement en lecture seule sur la base (tous les appelants filtrent
    immédiatement, et un filtrage booléen pandas renvoie une copie), aucun
    appelant ne peut muter l'objet partagé.

    Si le parquet COLD de l'archivage hot/cold existe (rollover canicule activé
    un jour — cf. config.DB_ARCHIVE_PATH), il est concaténé AVANT le hot : la
    base vue du dashboard reste l'historique ENTIER, aucun run archivé ne
    disparaît d'Explorer/Contrôle et les harnais de non-régression restent
    identiques avant/après un rollover. Archive absente (cas actuel) →
    comportement strictement inchangé. La lecture hot-seul des pages
    interactives (gain mémoire, design §3) est un chantier ultérieur distinct.

    Filtre aussi les modèles legacy qui auraient pu rester dans un parquet plus
    ancien (ex. AIGEFS/ICON retirés de config.MODELS) — évite tout crash sur des
    lignes orphelines sans couleur/config déclarée.

    Magasin externe PRIMAIRE, parquet git BACKUP automatique : en mode magasin
    (WEATHER_STORE), toute erreur (réseau, API injoignable) ou tout résultat
    vide bascule silencieusement sur la lecture git ci-dessous — jamais de page
    en erreur pour une panne du magasin, jamais de perte de service tant que le
    pipeline continue de committer dans git en double écriture."""
    if sig is None:
        return pd.DataFrame(columns=C.SCHEMA)
    if store_active():
        # Magasin externe : la base = concat des partitions mensuelles (elles
        # représentent l'historique ENTIER, pas de hot/archive séparés).
        try:
            df = load_store_df()
        except Exception:  # noqa: BLE001 — magasin injoignable, repli sur git
            df = pd.DataFrame()
        if not df.empty:
            return _finalize(df)
    if not os.path.exists(C.DB_PATH):
        return pd.DataFrame(columns=C.SCHEMA)
    df = pd.read_parquet(C.DB_PATH)
    if os.path.exists(C.DB_ARCHIVE_PATH):
        archive = pd.read_parquet(C.DB_ARCHIVE_PATH)
        df = pd.concat([archive, df], ignore_index=True)
        # Recouvrement hot/archive impossible après un rollover sain, mais la
        # lecture ne doit pas en dépendre : dédup défensive, hot prioritaire.
        df = df.drop_duplicates(subset=["run_date", "model", "member", "valid_time"],
                                keep="last")
    return _finalize(df)


def _finalize(df):
    """Traitement aval commun aux deux sources (parquet git OU partitions du
    magasin) : filtre les modèles legacy orphelins (sans config/couleur
    déclarée), compacte les dtypes puis convertit run_date/valid_time UTC →
    heure de Paris (naïf). Isolé pour garantir un DataFrame IDENTIQUE quelle que
    soit la provenance — c'est ce qui rend la bascule non régressive."""
    df = df[df["model"].isin(C.MODEL_LABELS)].reset_index(drop=True)
    df = _compact_dtypes(df)
    for col in ("run_date", "valid_time"):
        s = pd.to_datetime(df[col])
        df[col] = (s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None))
    return df


def _compact_dtypes(df):
    """Dtypes minimaux à information CONSTANTE — la base tient entièrement en
    mémoire et croît d'environ 5 M de lignes par mois, ce qui la rend seule
    responsable de l'essentiel de l'empreinte du dashboard.

    Conversions STRICTEMENT sans perte, pour que les valeurs restent au bit près
    celles du parquet : `member` est un indice d'ensemble à deux chiffres, et
    `model` ne prend que les quelques labels de config.MODELS, d'où la catégorie
    (le stockage d'une chaîne par ligne y coûte à lui seul près d'un quart de la
    table). Les colonnes de variables restent en float64 : le float32 les
    couvrirait largement en précision physique (~1e-6 °C), mais introduirait un
    écart numérique que les harnais de non-régression compareraient à jamais à
    des références divergentes, pour moins de 6 % d'empreinte gagnée.
    Colonne absente (variable ajoutée après coup, cf. z500) → ignorée."""
    casts = {"member": "int16", "model": "category"}
    return df.astype({c: t for c, t in casts.items() if c in df.columns})


def utc_cycle(local_run_date):
    """Reconvertit un run_date stocké en heure locale Paris vers son instant UTC
    réel — nécessaire pour retrouver le vrai cycle synoptique (0/6/12/18Z)."""
    return pd.Timestamp(local_run_date).tz_localize(LOCAL_TZ).tz_convert("UTC")


def _run_utc_naive(local_run_date):
    """Cycle synoptique UTC (0/6/12/18Z), tz-naïf — même convention que le
    run_date legacy parsé depuis l'en-tête Météociel, donc directement comparable."""
    return utc_cycle(local_run_date).replace(tzinfo=None)


def run_label_text(local_run_date):
    """Nom du run d'après son vrai cycle UTC, ex. « 30 Jun 2026 — 06Z » — jamais
    l'heure locale, qui ne correspond à aucun cycle synoptique réel."""
    u = utc_cycle(local_run_date)
    return f"{u:%d %b %Y} — {u.hour:02d}Z"


@st.cache_data(show_spinner=False)
def list_runs(sig):
    """Runs disponibles (run_date distinctes), du plus récent au plus ancien."""
    df = load_db(sig)
    if df.empty:
        return pd.DataFrame(columns=["run_date", "label"])
    runs = pd.DataFrame({"run_date": sorted(df["run_date"].unique(), reverse=True)})
    runs["label"] = runs["run_date"].apply(run_label_text)
    return runs.reset_index(drop=True)


def run_slice(sig, run_date):
    df = load_db(sig)
    return df[df["run_date"] == run_date]
