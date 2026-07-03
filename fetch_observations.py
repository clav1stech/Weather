# -*- coding: utf-8 -*-
"""Pipeline ANNEXE d'observations de surface — API Météo-France DPObs
→ parquet séparé data/database_paris_observations.parquet (config.DB_OBS_PATH).

Flux indépendant des autres pipelines (Forecast.py, forecast_t2m_hd.py) : il ne
touche à AUCUN autre fichier de données. Quatre stations parisiennes (cf.
config.OBS_STATIONS), choisies pour leur contraste d'exposition à l'îlot de
chaleur urbain — l'endpoint /station/horaire ne renvoie que l'observation la
plus récente d'UNE station, donc un appel par station (parallélisés, même
pattern que Forecast._fetch_all_metadata).

Sécurité (règle absolue) : la clé API vit UNIQUEMENT dans la variable
d'environnement METEOFRANCE_API_KEY (secret GitHub Actions en CI, fichier .env
gitignoré en local — parsé ici sans dépendance externe). Jamais en dur, jamais
de valeur par défaut, jamais loguée — même partiellement — dans les
print/erreurs. Absente → échec explicite immédiat.

Particularités de l'API constatées en conditions réelles (2026-07) :
  • températures (t/td/tx/tn) en KELVIN, pressions (pres/pmer) en Pa —
    converties AU PARSING (°C, hPa) pour un parquet directement comparable aux
    données Open-Meteo (cf. config.OBS_VARIABLES, champ conv) ;
  • l'heure de l'observation est `validity_time` (UTC, suffixe Z) ;
    `reference_time` est l'heure de production du lot (identique pour toutes
    les stations, renouvelée à chaque poll pour une même obs) — inutilisable
    comme clé. Déduplication : (station_id, valid_time), append-only ;
  • les stations du réseau ETENDU (Lariboisière, Luxembourg) ne publient QUE
    t/tx/tn/rr1 — humidité, vent, pression restent null par construction
    (niveau d'instrumentation, pas un bug) → NaN, jamais une valeur inventée ;
    la pression n'existe qu'à Montsouris (Longchamp RADOME ne la publie pas).

Panne partielle = cas normal : une station injoignable est ignorée à ce poll
(les autres sont persistées) ; toutes injoignables → sortie en erreur.
Écriture atomique (tmp + os.replace), jamais de perte d'historique.
"""

import os
import sys
import concurrent.futures

import requests
import pandas as pd

import config as C


# --------------------------------------------------------------------------- #
#  Clé API — env d'abord, .env local en repli ; jamais en dur, jamais loguée
# --------------------------------------------------------------------------- #
def _load_dotenv_key():
    """Renseigne os.environ depuis le fichier .env local (gitignoré) si la
    variable n'est pas déjà dans l'environnement — parsing minimal KEY=VALUE,
    sans dépendance externe (python-dotenv serait surdimensionné pour un champ).
    """
    if os.environ.get(C.OBS_API_KEY_ENV):
        return
    dotenv = os.path.join(C.BASE_DIR, ".env")
    if not os.path.exists(dotenv):
        return
    with open(dotenv, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(f"{C.OBS_API_KEY_ENV}=") and not line.startswith("#"):
                os.environ[C.OBS_API_KEY_ENV] = line.split("=", 1)[1].strip()
                return


def api_key():
    """Clé API, ou échec EXPLICITE si absente — jamais de repli silencieux qui
    laisserait le cron « réussir » sans rien collecter. Le message n'expose
    jamais la valeur de la clé."""
    _load_dotenv_key()
    key = os.environ.get(C.OBS_API_KEY_ENV, "").strip()
    if not key:
        raise SystemExit(
            f"❌ Variable d'environnement {C.OBS_API_KEY_ENV} absente ou vide. "
            "En CI : secret GitHub Actions ; en local : fichier .env à la racine "
            "(gitignoré) avec la ligne METEOFRANCE_API_KEY=<clé>."
        )
    return key


# --------------------------------------------------------------------------- #
#  Requêtes API — un appel par station (l'endpoint est mono-station)
# --------------------------------------------------------------------------- #
def fetch_station(station, key):
    """Observation horaire la plus récente d'une station (dict JSON), ou None
    en cas d'échec — la panne d'UNE station ne doit jamais faire échouer les
    autres. Les messages d'erreur n'incluent jamais la clé (le header n'est
    pas répercuté dans les exceptions requests)."""
    url = f"{C.OBS_API_BASE}/station/horaire"
    try:
        resp = requests.get(url, headers={"apikey": key},
                            params={"id_station": station["id"], "format": "json"},
                            timeout=C.HTTP_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.exceptions.HTTPError as exc:
        print(f"   ⚠️  {station['nom']} : HTTP {exc.response.status_code} — station ignorée à ce poll")
        return None
    except (requests.exceptions.RequestException, ValueError) as exc:
        print(f"   ⚠️  {station['nom']} : {type(exc).__name__} — station ignorée à ce poll")
        return None
    # L'API renvoie une liste (une seule observation par défaut) ; vide ou
    # inattendue → station ignorée, comme une panne.
    if not isinstance(payload, list) or not payload:
        print(f"   ⚠️  {station['nom']} : réponse vide/inattendue — station ignorée à ce poll")
        return None
    return payload[0]


def fetch_all(key):
    """{station_id → observation dict | None}, appels parallèles (4 stations,
    quota 100 req/min : très large marge)."""
    def _one(station):
        return station["id"], fetch_station(station, key)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(C.OBS_STATIONS)) as pool:
        return dict(pool.map(_one, C.OBS_STATIONS))


# --------------------------------------------------------------------------- #
#  Normalisation JSON → lignes plates (conversions faites ICI, avant stockage)
# --------------------------------------------------------------------------- #
def _convert(value, conv):
    """Applique la conversion d'unité déclarée en config (K→°C, Pa→hPa) ;
    None/valeur non numérique → NaN (absence structurelle sur les stations
    ETENDU : jamais une valeur inventée, jamais une erreur)."""
    v = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(v):
        return float("nan")
    if conv == "kelvin":
        return float(v) - 273.15
    if conv == "pa_to_hpa":
        return float(v) / 100.0
    return float(v)


def parse_observations(raw_by_id):
    """{station_id → obs dict | None} → DataFrame plat OBS_SCHEMA.
    Une ligne par station ayant répondu avec un validity_time exploitable et au
    moins une valeur valide. Timestamps stockés en UTC tz-naïf (l'API renvoie
    un suffixe Z), conversion vers l'heure de Paris à l'affichage seulement."""
    rows = []
    for station in C.OBS_STATIONS:
        obs = raw_by_id.get(station["id"])
        if obs is None:
            continue
        valid_time = pd.to_datetime(obs.get("validity_time"), errors="coerce", utc=True)
        if pd.isna(valid_time):
            print(f"   ⚠️  {station['nom']} : validity_time absent — observation ignorée")
            continue
        row = {"valid_time": valid_time.tz_localize(None),
               "station_id": station["id"], "station_nom": station["nom"]}
        for var in C.OBS_VARIABLES:
            row[var["col"]] = _convert(obs.get(var["api"]), var["conv"])
        if all(pd.isna(row[c]) for c in C.OBS_VAR_COLS):
            print(f"   ⚠️  {station['nom']} : aucune valeur valide — observation ignorée")
            continue
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=C.OBS_SCHEMA)
    return pd.DataFrame(rows)[C.OBS_SCHEMA]


# --------------------------------------------------------------------------- #
#  Persistance — append-only, dédup (station_id, valid_time), écriture atomique
# --------------------------------------------------------------------------- #
def load_existing():
    """Base observations existante, réalignée sur le schéma courant (colonne
    ajoutée après coup → NaN) — même principe que les autres pipelines."""
    if os.path.exists(C.DB_OBS_PATH):
        df = pd.read_parquet(C.DB_OBS_PATH)
        for col in C.OBS_SCHEMA:
            if col not in df.columns:
                df[col] = pd.NA
        return df[C.OBS_SCHEMA]
    return pd.DataFrame(columns=C.OBS_SCHEMA)


def persist(fresh, existing=None):
    """Append des seules observations nouvelles — un couple (station_id,
    valid_time) déjà stocké n'est JAMAIS remplacé ni modifié (une observation
    est un fait acquis, pas une prévision révisable) — puis écriture atomique
    (tmp + os.replace). Retourne (base combinée, nb de lignes ajoutées)."""
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
    tmp = C.DB_OBS_PATH + ".tmp"
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, C.DB_OBS_PATH)
    return combined, len(fresh)


# --------------------------------------------------------------------------- #
#  Entrée
# --------------------------------------------------------------------------- #
def main():
    key = api_key()
    print(f"⏳ Requête Météo-France DPObs ({len(C.OBS_STATIONS)} stations)…")
    raw = fetch_all(key)

    n_ok = sum(1 for v in raw.values() if v is not None)
    if n_ok == 0:
        raise SystemExit("❌ Aucune station n'a répondu — API indisponible ou clé invalide.")
    if n_ok < len(C.OBS_STATIONS):
        print(f"   ⚠️  {len(C.OBS_STATIONS) - n_ok} station(s) sans réponse à ce poll "
              "(les autres sont persistées normalement).")

    fresh = parse_observations(raw)
    if fresh.empty:
        print("ℹ️  Aucune observation exploitable dans les réponses — base laissée telle quelle.")
        return
    for r in fresh.itertuples():
        t_txt = f"{r.t:.1f} °C" if pd.notna(r.t) else "t manquante"
        print(f"   {r.station_nom} : {r.valid_time:%d %b %H:%M} UTC · {t_txt}")

    combined, n_new = persist(fresh)
    if n_new == 0:
        print("ℹ️  Observations déjà en base (poll plus fréquent que le pas horaire) — rien à écrire.")
        return
    print(f"✅ Base observations mise à jour : +{n_new} ligne(s) · {len(combined):,} au total")
    print(f"   → {C.DB_OBS_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline observations : {exc}")
