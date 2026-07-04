# -*- coding: utf-8 -*-
"""Pipeline ANNEXE d'observations de surface — API Météo-France DPPaquetObs
→ parquet séparé data/database_paris_observations.parquet (config.DB_OBS_PATH).

Flux indépendant des autres pipelines (Forecast.py, forecast_t2m_hd.py) : il ne
touche à AUCUN autre fichier de données. Quatre stations parisiennes (cf.
config.OBS_STATIONS), choisies pour leur contraste d'exposition à l'îlot de
chaleur urbain — collectées via l'endpoint « Paquet Observation »
(/paquet/horaire, contexte DPPaquetObs/v2) : UN seul appel renvoie les
observations horaires de toutes les stations du département sur une fenêtre
glissante de plusieurs jours (~5 j constatés). Deux bénéfices sur le
mono-station /station/horaire (une observation par appel) : l'historique
s'amorce/se réamorce en un poll, et un cron manqué (panne CI, quota) est
rattrapé automatiquement au poll suivant — aucune heure n'est perdue tant que
l'interruption reste plus courte que la fenêtre du paquet. Les stations du
département hors config sont ignorées silencieusement.

Sécurité (règle absolue) : la clé API vit UNIQUEMENT dans la variable
d'environnement METEOFRANCE_API_KEY (secret GitHub Actions en CI, fichier .env
gitignoré en local — parsé ici sans dépendance externe). Jamais en dur, jamais
de valeur par défaut, jamais loguée — même partiellement — dans les
print/erreurs. Absente → échec explicite immédiat.

Particularités de l'API constatées en conditions réelles (2026-07) :
  • la réponse paquet est une LISTE PLATE d'observations — une entrée par
    (station, heure), mêmes champs que le mono-station /station/horaire,
    station identifiée par `geo_id_insee` ; aucun doublon (station,
    validity_time) constaté dans un même paquet ;
  • températures (t/td/tx/tn) en KELVIN, pressions (pres/pmer) en Pa —
    converties AU PARSING (°C, hPa) pour un parquet directement comparable aux
    données Open-Meteo (cf. config.OBS_VARIABLES, champ conv) ;
  • l'heure de l'observation est `validity_time` (UTC, suffixe Z) ;
    `reference_time` est l'heure de production du lot (renouvelée à chaque
    poll pour une même obs) — inutilisable comme clé. Déduplication :
    (station_id, valid_time), append-only — elle absorbe naturellement le
    recouvrement massif entre deux polls (la quasi-totalité des points du
    paquet sont déjà en base ; seuls les points réellement nouveaux, ou ceux
    qui comblent un trou après une panne, sont ajoutés) ;
  • les stations du réseau ETENDU (Lariboisière, Luxembourg) ne publient QUE
    t/tx/tn/rr1 — humidité, vent, pression restent null par construction
    (niveau d'instrumentation, pas un bug) → NaN, jamais une valeur inventée ;
    la pression n'existe qu'à Montsouris (Longchamp RADOME ne la publie pas).

Panne partielle = cas normal : une station absente ou raréfiée dans le paquet
est simplement moins alimentée à ce poll (les autres sont persistées, le
paquet suivant comblera rétroactivement) ; seule la panne TOTALE (l'appel
département échoue) interrompt la collecte de ce poll — sortie en erreur,
parquet intact. Écriture atomique (tmp + os.replace), jamais de perte
d'historique.
"""

import os
import sys

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
#  Requête API — un seul appel paquet pour tout le département
# --------------------------------------------------------------------------- #
def fetch_paquet(key):
    """Observations horaires de TOUTES les stations du département (liste
    plate d'entrées (station, heure) sur la fenêtre glissante du paquet,
    ~5 j constatés). Le paquet département est préféré au mono-station
    /station/horaire pour deux raisons : l'historique s'amorce en un poll
    (backfill naturel) et un cron manqué est rattrapé automatiquement au poll
    suivant (les heures intermédiaires sont toutes dans le paquet, rien n'est
    perdu). L'échec de CET appel est une panne totale : SystemExit propre,
    parquet intact — les messages d'erreur n'incluent jamais la clé (le
    header n'est pas répercuté dans les exceptions requests)."""
    url = f"{C.OBS_API_BASE}/paquet/horaire"
    try:
        resp = requests.get(url, headers={"apikey": key},
                            params={"id-departement": C.OBS_DEPARTEMENT,
                                    "format": "json"},
                            timeout=C.HTTP_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.exceptions.Timeout:
        raise SystemExit(f"❌ Timeout DPPaquetObs après {C.HTTP_TIMEOUT} s — "
                         "API lente ou injoignable, relancer plus tard.")
    except requests.exceptions.HTTPError as exc:
        raise SystemExit(f"❌ Erreur HTTP DPPaquetObs {exc.response.status_code} — "
                         "clé/abonnement invalide (403), quota atteint (429) ou "
                         "panne API. Parquet laissé intact.")
    except (requests.exceptions.RequestException, ValueError) as exc:
        raise SystemExit(f"❌ Erreur réseau/JSON DPPaquetObs : {type(exc).__name__}. "
                         "Parquet laissé intact.")
    if not isinstance(payload, list):
        raise SystemExit("❌ Réponse DPPaquetObs inattendue (liste attendue). "
                         "Parquet laissé intact.")
    return payload


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


def parse_observations(payload):
    """Liste plate DPPaquetObs → DataFrame plat OBS_SCHEMA.

    Filtre sur les 4 stations de config.OBS_STATIONS (`geo_id_insee`) — les
    autres stations du département sont ignorées silencieusement — puis parse
    TOUTES les heures de la fenêtre du paquet, pas seulement la plus récente
    (c'est ce qui donne le backfill et le rattrapage de cron manqué). Une
    entrée sans validity_time exploitable ou sans la moindre valeur valide est
    écartée sans bruit (le paquet en contient des centaines, l'entrée voisine
    dit déjà tout). Timestamps stockés en UTC tz-naïf (l'API renvoie un
    suffixe Z), conversion vers l'heure de Paris à l'affichage seulement."""
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
        for var in C.OBS_VARIABLES:
            row[var["col"]] = _convert(obs.get(var["api"]), var["conv"])
        if all(pd.isna(row[c]) for c in C.OBS_VAR_COLS):
            continue
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=C.OBS_SCHEMA)
    # Un même paquet n'a jamais montré de doublon (station, heure), mais la clé
    # d'unicité du parquet ne doit pas dépendre de cette bonne conduite : on
    # dédoublonne défensivement (première entrée conservée).
    return (pd.DataFrame(rows)[C.OBS_SCHEMA]
              .drop_duplicates(subset=["station_id", "valid_time"], keep="first")
              .reset_index(drop=True))


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
    print(f"⏳ Requête Météo-France DPPaquetObs (paquet horaire, département "
          f"{C.OBS_DEPARTEMENT})…")
    payload = fetch_paquet(key)

    fresh = parse_observations(payload)
    if fresh.empty:
        print("ℹ️  Aucune observation exploitable pour les stations suivies — "
              "base laissée telle quelle.")
        return
    absentes = [s["nom"] for s in C.OBS_STATIONS
                if s["nom"] not in set(fresh["station_nom"])]
    if absentes:
        print(f"   ⚠️  Station(s) absente(s) du paquet à ce poll (les autres sont "
              f"persistées, le paquet suivant comblera) : {', '.join(absentes)}")

    existing = load_existing()
    combined, n_new = persist(fresh, existing)
    if n_new == 0:
        print("ℹ️  Toutes les observations du paquet déjà en base — rien à écrire.")
        return
    # Détail par station des points réellement AJOUTÉS (pas ceux du paquet, déjà
    # connus pour la plupart) : dans les logs CI, un nombre > 1 après un cron
    # manqué est la preuve visible que le rattrapage a fonctionné.
    added = combined.merge(existing[["station_id", "valid_time"]],
                           on=["station_id", "valid_time"], how="left",
                           indicator=True)
    added = added[added["_merge"] == "left_only"]
    for nom, g in added.groupby("station_nom"):
        print(f"   {nom} : +{len(g)} point(s), de {g['valid_time'].min():%d %b %H:%M} "
              f"à {g['valid_time'].max():%d %b %H:%M} UTC")
    print(f"✅ Base observations mise à jour : +{n_new} ligne(s) · {len(combined):,} au total")
    print(f"   → {C.DB_OBS_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline observations : {exc}")
