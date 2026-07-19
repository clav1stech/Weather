# -*- coding: utf-8 -*-
"""Client Open-Meteo générique — mutualisé entre apps (CONFIG-AGNOSTIQUE :
aucun import de config, tout arrive en paramètres).

Trois briques :
  • fetch_json()        : GET + gestion d'erreurs homogène (timeout / réseau /
                          HTTP) avec sortie explicite — même discipline que le
                          pipeline canicule (jamais d'échec silencieux à la
                          collecte) ;
  • fetch_run_date()    : Metadata API officielle (last_run_initialisation_time
                          exact par modèle, hors quota) → datetime UTC tz-naïf,
                          None si endpoint absent/inaccessible (l'appelant
                          replie sur clock_run_date) ;
  • clock_run_date()    : repli horloge éprouvé — cycle le plus récent d'un
                          modèle d'après sa liste d'heures de cycle et le délai
                          de publication (le premier pas non-NaN d'une réponse
                          Open-Meteo tombe à 00:00 local et n'identifie jamais
                          le cycle, cf. invariants CLAUDE.md).

Multi-coordonnées : Open-Meteo accepte des listes `latitude`/`longitude`
séparées par des virgules et renvoie alors une LISTE de payloads (un par
point, même ordre que la requête) — as_payload_list() normalise les deux
formes (dict mono-point / liste multi-points) en liste.
"""

import datetime as dt

import requests


def fetch_json(url, params, timeout, context="Open-Meteo"):
    """GET JSON avec gestion d'erreurs homogène. Toute erreur réseau/HTTP sort
    en SystemExit explicite : une collecte qui échoue doit échouer franchement
    (le parquet existant reste intact), jamais retourner un payload partiel."""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise SystemExit(
            f"❌ Timeout {context} après {timeout} s — "
            "l'API est lente ou injoignable. Relancer dans quelques minutes."
        )
    except requests.exceptions.ConnectionError as exc:
        raise SystemExit(f"❌ Erreur réseau {context} : {exc}")
    except requests.exceptions.HTTPError as exc:
        raise SystemExit(f"❌ Erreur HTTP {context} "
                         f"{exc.response.status_code} : {exc}")
    return resp.json()


def as_payload_list(payload):
    """Normalise une réponse Open-Meteo en liste de payloads par point : une
    requête multi-coordonnées renvoie une liste, une mono-point un dict."""
    return payload if isinstance(payload, list) else [payload]


def multi_coord_params(sites):
    """Paramètres latitude/longitude pour un appel multi-points (listes
    séparées par des virgules, ordre de `sites` conservé dans la réponse)."""
    return {
        "latitude": ",".join(str(s["lat"]) for s in sites),
        "longitude": ",".join(str(s["lon"]) for s in sites),
    }


def fetch_run_date(meta_url_tpl, meta_slug, timeout):
    """last_run_initialisation_time exact d'un modèle via la Metadata API
    (requêtes hors quota). Datetime UTC tz-naïf, ou None si le slug est vide,
    l'endpoint inaccessible ou le JSON invalide — l'appelant replie alors sur
    clock_run_date (jamais d'échec bloquant sur cette voie d'appoint)."""
    if not meta_slug:
        return None
    try:
        resp = requests.get(meta_url_tpl.format(slug=meta_slug), timeout=timeout)
        resp.raise_for_status()
        ts = resp.json().get("last_run_initialisation_time")
        if ts is None:
            return None
        # fromtimestamp(tz=UTC).replace(tzinfo=None) → UTC tz-naïf, cohérent
        # avec toutes les datetime des pipelines.
        return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).replace(tzinfo=None)
    except (requests.exceptions.RequestException, ValueError, TypeError, OSError):
        return None


def clock_run_date(cycles, publication_lag_h, now_utc=None):
    """Cycle le plus récent d'un modèle (datetime UTC tz-naïf) d'après sa liste
    d'heures de cycle et le délai de publication : un run n'est exploitable que
    ~publication_lag_h après son initialisation, on ancre donc l'horloge en
    retrait avant de chercher la dernière heure de cycle éligible (veille si
    aucune heure du jour n'est encore atteinte)."""
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    anchored = now - dt.timedelta(hours=publication_lag_h)
    ordered = sorted(cycles)
    eligible = [h for h in ordered if h <= anchored.hour]
    day = anchored.date()
    if eligible:
        cycle_hour = eligible[-1]
    else:
        cycle_hour = ordered[-1]
        day -= dt.timedelta(days=1)
    return dt.datetime(day.year, day.month, day.day, cycle_hour)
