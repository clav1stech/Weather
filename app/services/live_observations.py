# -*- coding: utf-8 -*-
"""Snapshot EN DIRECT (non persisté) des observations 6 min Météo-France —
sert uniquement le bouton « Rafraîchir » de la page Observations : un clic
interroge l'API à la demande, affiche le résultat une fois, puis l'oublie.
La base réelle (data/database_paris_observations_6m.parquet) reste à la
charge exclusive du cron GitHub Actions habituel — ce module n'écrit RIEN sur
disque (dashboard toujours en lecture seule sur les parquets).

Ne réutilise volontairement PAS fetch_observations_6m.py (pipeline racine) :
l'invariant CLAUDE.md limite la surface pipeline→dashboard à quelques points
d'entrée déclarés, et l'élargir pour ce besoin ponctuel n'en vaut pas la
peine — la requête/parsing utile ici tient en quelques lignes, dupliquées
depuis la même logique (cf. fetch_observations.py) plutôt que de créer un
couplage supplémentaire.

Clé API lue depuis st.secrets (Streamlit Cloud), sous le même nom que la
variable d'environnement du pipeline (config.OBS_API_KEY_ENV) — mais une
source différente : le pipeline la lit d'un secret GitHub Actions/.env, le
dashboard n'a accès qu'à st.secrets."""

import pandas as pd
import requests
import streamlit as st

import config as C
from app.runtime import LOCAL_TZ


def _convert(value, conv):
    """Même logique que fetch_observations._convert (K→°C, Pa→hPa) — absence
    structurelle (stations ETENDU) ou valeur non numérique → NaN, jamais une
    valeur inventée."""
    v = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(v):
        return float("nan")
    if conv == "kelvin":
        return float(v) - 273.15
    if conv == "pa_to_hpa":
        return float(v) / 100.0
    return float(v)


def fetch_live_snapshot():
    """(dict station_nom -> ligne de valeurs, liste d'erreurs par station) ou
    (None, [message]) si la clé API n'est pas configurée — dans ce cas, aucun
    appel réseau n'est tenté. Une station en échec HTTP est signalée mais
    n'empêche pas l'affichage des autres (panne partielle = cas normal, comme
    dans le pipeline)."""
    key = st.secrets.get(C.OBS_API_KEY_ENV)
    if not key:
        return None, ["Aperçu en direct non configuré (secret API absent)."]

    url = f"{C.OBS_API_BASE}{C.OBS_6M_ENDPOINT}"
    resultats, erreurs = {}, []
    for station in C.OBS_STATIONS:
        try:
            resp = requests.get(url, headers={"apikey": key},
                                params={"id_station": station["id"], "format": "json"},
                                timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as exc:
            erreurs.append(f"{station['nom']} (HTTP {exc.response.status_code})")
            continue
        except requests.exceptions.RequestException:
            erreurs.append(f"{station['nom']} (injoignable)")
            continue
        if not isinstance(data, list) or not data:
            erreurs.append(f"{station['nom']} (aucune donnée)")
            continue
        # La fenêtre glissante renvoie plusieurs instants : on ne garde que le
        # plus récent (validity_time max) pour l'aperçu.
        dernier = max(data, key=lambda o: o.get("validity_time") or "")
        # Converti en heure de Paris naïve dès ici — même format que les lignes
        # déjà chargées (app/data/observations*.py) avec lesquelles cette ligne
        # sera comparée (fraîcheur, tri) : jamais de mélange naïf/aware.
        valid_time = pd.to_datetime(dernier.get("validity_time"), errors="coerce", utc=True)
        if pd.notna(valid_time):
            valid_time = valid_time.tz_convert(LOCAL_TZ).tz_localize(None)
        ligne = {"valid_time": valid_time}
        for var in C.OBS_6M_VARIABLES:
            ligne[var["col"]] = _convert(dernier.get(var["api"]), var["conv"])
        resultats[station["nom"]] = ligne
    return resultats, erreurs
