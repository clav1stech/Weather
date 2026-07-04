# -*- coding: utf-8 -*-
"""Pipeline ANNEXE d'observations INFRA-HORAIRES 6 min — API Météo-France
DPPaquetObs (/paquet/infrahoraire-6m) → parquet séparé
data/database_paris_observations_6m.parquet (config.DB_OBS_6M_PATH).

Jumeau du flux horaire (fetch_observations.py) à trois différences près :
  • endpoint /paquet/infrahoraire-6m (une mesure toutes les 6 min, pas par
    heure) — d'où la fraîcheur recherchée pour les cartes « temps réel » ;
  • interrogé PAR STATION (paramètre `id_station`, un appel par station des 4) :
    ce paquet n'accepte pas le filtre `id-departement` du flux horaire (400) ;
  • variables INSTANTANÉES seulement (config.OBS_6M_VARIABLES : t/td/u/vent/
    rafales/pression + rr_per, cumul 6 min de la période) — aucun extrême ni
    cumul horaire (tx/tn/rr1 n'ont pas de sens à 6 min) ; le flux horaire reste
    l'unique source des Tx/Tn journaliers.

Les 4 stations répondent au 6 min, mais l'instrumentation diffère : RADOME
(Montsouris, Longchamp) publie tout (pression à Montsouris seule) ; ETENDU
(Lariboisière, Luxembourg) ne renseigne que t et rr_per, le reste restant null
par construction (jamais une panne). Chaque appel renvoie ~4,4 j de points de
6 min (fenêtre bien plus large que 24 h) : le backfill initial s'amorce en un
seul poll.

La gestion de la clé API (secret METEOFRANCE_API_KEY, jamais en dur ni loguée)
et la conversion d'unités sont RÉUTILISÉES telles quelles depuis
fetch_observations.py : source unique pour la logique sensible (sécurité) et
pour la cohérence des conversions (K→°C, Pa→hPa). Déduplication, append-only,
écriture atomique (tmp + os.replace) et dégradation en cas de panne partielle
suivent exactement le flux horaire — une observation est un fait acquis, jamais
remplacée ni modifiée.
"""

import os
import sys

import requests
import pandas as pd

import config as C
from fetch_observations import api_key, _convert


# --------------------------------------------------------------------------- #
#  Requête API — un appel par station (id_station), fenêtre ~4,4 j par station
# --------------------------------------------------------------------------- #
# Ce paquet 6 min n'accepte pas le filtre `id-departement` (400) : on interroge
# chaque station par `id_station`. Un message d'erreur HTTP distinct par code
# (400 requête invalide, 403 clé/abonnement, 429 quota) — sans jamais exposer la
# clé (le header n'est pas répercuté dans les exceptions requests).
_HTTP_MOTIF = {
    400: "requête invalide (paramètre/station inconnu)",
    403: "clé absente/non abonnée à DPPaquetObs 6 min",
    429: "quota d'appels atteint",
}


def _motif_http(code):
    return _HTTP_MOTIF.get(code, "panne API")


def fetch_paquet_6m(key):
    """Observations 6 min des 4 stations suivies : un appel par `id_station`,
    concaténés en une liste plate d'entrées (station, instant) — chaque appel
    couvre ~4,4 j (backfill initial en un poll). Une station en échec HTTP est
    signalée puis IGNORÉE (panne partielle : le paquet suivant comblera) ; seul
    l'échec des 4 stations est une panne totale (SystemExit propre, parquet
    intact). La clé n'apparaît jamais dans les messages (header non répercuté)."""
    url = f"{C.OBS_API_BASE}{C.OBS_6M_ENDPOINT}"
    payload, echecs = [], []
    for station in C.OBS_STATIONS:
        try:
            resp = requests.get(url, headers={"apikey": key},
                                params={"id_station": station["id"],
                                        "format": "json"},
                                timeout=C.HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            echecs.append(f"{station['nom']} (timeout {C.HTTP_TIMEOUT} s)")
            continue
        except requests.exceptions.HTTPError as exc:
            code = exc.response.status_code
            echecs.append(f"{station['nom']} (HTTP {code} — {_motif_http(code)})")
            continue
        except (requests.exceptions.RequestException, ValueError) as exc:
            echecs.append(f"{station['nom']} ({type(exc).__name__})")
            continue
        if not isinstance(data, list):
            echecs.append(f"{station['nom']} (réponse inattendue)")
            continue
        payload.extend(data)

    if echecs:
        print(f"   ⚠️  Station(s) 6 min en échec à ce poll (ignorée(s), le poll "
              f"suivant comblera) : {', '.join(echecs)}")
    if not payload:
        raise SystemExit("❌ Aucune station 6 min joignable à ce poll (clé/abonnement, "
                         "quota ou panne API). Parquet laissé intact.")
    return payload


# --------------------------------------------------------------------------- #
#  Normalisation JSON → lignes plates (conversions via _convert du flux horaire)
# --------------------------------------------------------------------------- #
def parse_observations_6m(payload):
    """Liste plate DPPaquetObs 6 min (appels par station concaténés) → DataFrame
    plat OBS_6M_SCHEMA. Filtre sur les stations de config.OBS_STATIONS
    (`geo_id_insee`) et parse tous les instants de la fenêtre. Les stations
    ETENDU ne renseignent que t et rr_per (reste NaN, structurel). Une entrée
    sans validity_time exploitable ou sans la moindre valeur valide est écartée
    sans bruit. Timestamps stockés en UTC tz-naïf (suffixe Z)."""
    station_by_id = C.OBS_STATION_BY_ID
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
        for var in C.OBS_6M_VARIABLES:
            row[var["col"]] = _convert(obs.get(var["api"]), var["conv"])
        if all(pd.isna(row[c]) for c in C.OBS_6M_VAR_COLS):
            continue
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=C.OBS_6M_SCHEMA)
    return (pd.DataFrame(rows)[C.OBS_6M_SCHEMA]
              .drop_duplicates(subset=["station_id", "valid_time"], keep="first")
              .reset_index(drop=True))


# --------------------------------------------------------------------------- #
#  Persistance — append-only, dédup (station_id, valid_time), écriture atomique
# --------------------------------------------------------------------------- #
def load_existing():
    """Base 6 min existante, réalignée sur le schéma courant (colonne ajoutée
    après coup → NaN)."""
    if os.path.exists(C.DB_OBS_6M_PATH):
        df = pd.read_parquet(C.DB_OBS_6M_PATH)
        for col in C.OBS_6M_SCHEMA:
            if col not in df.columns:
                df[col] = pd.NA
        return df[C.OBS_6M_SCHEMA]
    return pd.DataFrame(columns=C.OBS_6M_SCHEMA)


def persist(fresh, existing=None):
    """Append des seules mesures nouvelles — un couple (station_id, valid_time)
    déjà stocké n'est JAMAIS remplacé — puis écriture atomique. Retourne (base
    combinée, nb de lignes ajoutées)."""
    if existing is None:
        existing = load_existing()
    if not existing.empty:
        known = pd.MultiIndex.from_frame(existing[["station_id", "valid_time"]])
        fresh_idx = pd.MultiIndex.from_frame(fresh[["station_id", "valid_time"]])
        fresh = fresh[~fresh_idx.isin(known)].reset_index(drop=True)
    if fresh.empty:
        return existing, 0

    combined = pd.concat([existing, fresh], ignore_index=True) \
                 .sort_values(["valid_time", "station_id"]).reset_index(drop=True)

    os.makedirs(C.DATA_DIR, exist_ok=True)
    tmp = C.DB_OBS_6M_PATH + ".tmp"
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, C.DB_OBS_6M_PATH)
    return combined, len(fresh)


# --------------------------------------------------------------------------- #
#  Entrée
# --------------------------------------------------------------------------- #
def main():
    key = api_key()
    print("⏳ Requête Météo-France DPPaquetObs (paquet 6 min, "
          f"{len(C.OBS_STATIONS)} stations)…")
    payload = fetch_paquet_6m(key)

    fresh = parse_observations_6m(payload)
    if fresh.empty:
        print("ℹ️  Aucune observation 6 min exploitable — base laissée telle quelle.")
        return
    # Les 4 stations publient le 6 min : une station sans aucune ligne exploitable
    # à ce poll (échec de son appel, déjà signalé par fetch_paquet_6m, ou fenêtre
    # vide) est une panne partielle — les autres sont persistées, le poll suivant
    # comblera rétroactivement.
    absentes = [s["nom"] for s in C.OBS_STATIONS
                if s["nom"] not in set(fresh["station_nom"])]
    if absentes:
        print(f"   ⚠️  Station(s) sans observation 6 min à ce poll (les autres "
              f"persistées, le poll suivant comblera) : {', '.join(absentes)}")

    existing = load_existing()
    combined, n_new = persist(fresh, existing)
    if n_new == 0:
        print("ℹ️  Toutes les mesures 6 min du paquet déjà en base — rien à écrire.")
        return
    added = combined.merge(existing[["station_id", "valid_time"]],
                           on=["station_id", "valid_time"], how="left",
                           indicator=True)
    added = added[added["_merge"] == "left_only"]
    for nom, g in added.groupby("station_nom"):
        print(f"   {nom} : +{len(g)} point(s), de {g['valid_time'].min():%d %b %H:%M} "
              f"à {g['valid_time'].max():%d %b %H:%M} UTC")
    print(f"✅ Base observations 6 min mise à jour : +{n_new} ligne(s) · "
          f"{len(combined):,} au total")
    print(f"   → {C.DB_OBS_6M_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline observations 6 min : {exc}")
