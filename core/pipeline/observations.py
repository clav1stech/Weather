# -*- coding: utf-8 -*-
"""Mécanique GÉNÉRIQUE d'un flux d'observations Météo-France DPPaquetObs —
version paramétrée du motif de fetch_observations.py (racine) : un appel
« Paquet Observation » par département (toutes les stations, fenêtre glissante
de plusieurs jours → backfill naturel et rattrapage d'un cron manqué), parsing
avec conversions d'unités AVANT stockage, persistance append-only dédupliquée
par (station_id, valid_time), écriture atomique.

Comme core/pipeline/ensemble_runs.py : module STRICTEMENT config-agnostique
(aucun import config/app.*, tout arrive en paramètres). Consommé par les
pipelines des nouvelles apps (apps/snow/pipeline/fetch_observations.py). Le
pipeline canicule racine conserve VOLONTAIREMENT ses implémentations inline
(partie critique, jamais rebranchée sur core/ — duplication du motif assumée,
pas un oubli).

Invariants portés ici (identiques au flux canicule) :
  • une observation stockée n'est JAMAIS remplacée ni modifiée (fait acquis) ;
  • clé de dédup (station_id, valid_time) où valid_time = validity_time de
    l'API (heure de l'observation, UTC) — jamais reference_time (heure de
    production du lot, renouvelée à chaque poll : en faire la clé dupliquerait
    chaque obs à chaque poll) ;
  • valeur absente/non numérique → NaN (absence structurelle d'instrumentation,
    jamais une valeur inventée ni une erreur) ;
  • seule la panne TOTALE (l'appel département échoue) interrompt le poll —
    une station absente du paquet est simplement moins alimentée cette fois-ci.
"""

import os

import pandas as pd
import requests

from core.io.atomic import atomic_write_parquet


# --------------------------------------------------------------------------- #
#  Requête API — un seul appel paquet pour tout le département
# --------------------------------------------------------------------------- #
def fetch_paquet(base_url, key, departement, timeout, context="DPPaquetObs"):
    """GET /paquet/horaire?id-departement=… → liste plate d'entrées
    (station, heure) sur la fenêtre glissante du paquet. Échec = panne totale :
    SystemExit propre, parquet intact — les messages n'incluent JAMAIS la clé
    (le header n'est pas répercuté dans les exceptions requests)."""
    url = f"{base_url}/paquet/horaire"
    try:
        resp = requests.get(url, headers={"apikey": key},
                            params={"id-departement": departement,
                                    "format": "json"},
                            timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except requests.exceptions.Timeout:
        raise SystemExit(f"❌ Timeout {context} après {timeout} s — "
                         "API lente ou injoignable, relancer plus tard.")
    except requests.exceptions.HTTPError as exc:
        raise SystemExit(f"❌ Erreur HTTP {context} {exc.response.status_code} — "
                         "clé/abonnement invalide (403), quota atteint (429) ou "
                         "panne API. Parquet laissé intact.")
    except (requests.exceptions.RequestException, ValueError) as exc:
        raise SystemExit(f"❌ Erreur réseau/JSON {context} : {type(exc).__name__}. "
                         "Parquet laissé intact.")
    if not isinstance(payload, list):
        raise SystemExit(f"❌ Réponse {context} inattendue (liste attendue). "
                         "Parquet laissé intact.")
    return payload


# --------------------------------------------------------------------------- #
#  Normalisation JSON → lignes plates (conversions faites ICI, avant stockage)
# --------------------------------------------------------------------------- #
def convert_value(value, conv):
    """Conversion d'unité déclarée en config : "kelvin" (K → °C), "pa_to_hpa"
    (Pa → hPa), "m_to_cm" (m → cm, hauteur de neige), None (valeur telle
    quelle). None/non numérique → NaN."""
    v = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(v):
        return float("nan")
    if conv == "kelvin":
        return float(v) - 273.15
    if conv == "pa_to_hpa":
        return float(v) / 100.0
    if conv == "m_to_cm":
        return float(v) * 100.0
    return float(v)


def parse_observations(payload, station_by_id, variables, schema, var_cols):
    """Liste plate DPPaquetObs → DataFrame plat au schéma fourni.

    `station_by_id` : {id_station: {"id":…, "nom":…}} — les stations du
    département hors de ce dict sont ignorées silencieusement. Toutes les
    heures de la fenêtre du paquet sont parsées (backfill/rattrapage). Une
    entrée sans validity_time exploitable ou sans la moindre valeur valide est
    écartée sans bruit. Timestamps stockés en UTC tz-naïf (suffixe Z côté
    API) — conversion locale à l'affichage seulement."""
    rows = []
    for obs in payload:
        station = station_by_id.get(str(obs.get("geo_id_insee", "")))
        if station is None:
            continue
        valid_time = pd.to_datetime(obs.get("validity_time"), errors="coerce", utc=True)
        if pd.isna(valid_time):
            continue
        row = {"valid_time": valid_time.tz_localize(None),
               "station_id": station["id"], "station_nom": station["nom"]}
        for var in variables:
            row[var["col"]] = convert_value(obs.get(var["api"]), var["conv"])
        if all(pd.isna(row[c]) for c in var_cols):
            continue
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=schema)
    # La clé d'unicité du parquet ne doit pas dépendre de la bonne conduite de
    # l'API (aucun doublon (station, heure) constaté dans un même paquet, mais
    # dédoublonnage défensif quand même — première entrée conservée).
    return (pd.DataFrame(rows)[schema]
              .drop_duplicates(subset=["station_id", "valid_time"], keep="first")
              .reset_index(drop=True))


# --------------------------------------------------------------------------- #
#  Persistance — append-only, dédup (station_id, valid_time), écriture atomique
# --------------------------------------------------------------------------- #
def load_existing(path, schema):
    """Base observations existante, réalignée sur le schéma courant (colonne
    ajoutée après coup → NaN) — même principe que les autres pipelines."""
    if os.path.exists(path):
        df = pd.read_parquet(path)
        for col in schema:
            if col not in df.columns:
                df[col] = pd.NA
        return df[schema]
    return pd.DataFrame(columns=schema)


def persist(fresh, path, schema, existing=None):
    """Append des seules observations nouvelles — un couple (station_id,
    valid_time) déjà stocké n'est JAMAIS remplacé ni modifié — puis écriture
    atomique sur `path`. Retourne (base combinée, nb de lignes ajoutées)."""
    if existing is None:
        existing = load_existing(path, schema)
    if not existing.empty:
        known = pd.MultiIndex.from_frame(existing[["station_id", "valid_time"]])
        fresh_idx = pd.MultiIndex.from_frame(fresh[["station_id", "valid_time"]])
        fresh = fresh[~fresh_idx.isin(known)].reset_index(drop=True)
    if fresh.empty:
        return existing, 0

    combined = pd.concat([existing, fresh], ignore_index=True) \
                 .sort_values(["valid_time", "station_id"]).reset_index(drop=True)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_parquet(combined, path)
    return combined, len(fresh)
