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
import subprocess
import tempfile

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
    après coup → NaN).

    Source de vérité : le magasin externe dès que WEATHER_STORE_SOURCE est
    positionné (le parquet du disque n'est alors qu'une copie potentiellement
    gelée), sinon le disque comme avant. Le branchement vit ICI et non dans
    main() pour couvrir aussi le rechargement interne de persist()."""
    if _store_source_active():
        return load_existing_from_store(C.DB_OBS_6M_PATH, C.OBS_6M_SCHEMA)
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
#  Miroir vers le magasin externe — double écriture (« sortie de git »)
# --------------------------------------------------------------------------- #
# Bloc INLINE identique dans chaque pipeline racine (n'importe jamais core/ ni
# app/, cf. CLAUDE.md — duplication du motif assumée). git reste SOURCE DE
# VÉRITÉ : best-effort, JAMAIS bloquant. Activé par WEATHER_STORE_WRITE (CI
# post-merge) ; absent → pipeline strictement inchangé.

def _gh(args):
    """`gh <args> --repo STORE_REPO`, stdout capturé, lève sur échec."""
    return subprocess.run(["gh", *args, "--repo", C.STORE_REPO],
                          check=True, text=True, capture_output=True).stdout


def _ensure_store_release():
    """Crée le release porteur des partitions s'il manque (idempotent)."""
    try:
        _gh(["release", "view", C.STORE_TAG, "--json", "tagName"])
    except subprocess.CalledProcessError:
        _gh(["release", "create", C.STORE_TAG,
             "--title", "Données — partitions parquet (hors git)",
             "--notes", "Magasin de données du pipeline météo (cf. "
                        "docs/DESIGN_sortie_git.md). Ne pas supprimer.",
             "--latest=false"])


def _same_rows(a, b):
    """True si a et b portent exactement les mêmes lignes (ordre indifférent)."""
    if sorted(a.columns) != sorted(b.columns):
        return False
    cols = sorted(a.columns)
    return (a[cols].sort_values(cols, na_position="last").reset_index(drop=True)
            .equals(b[cols].sort_values(cols, na_position="last").reset_index(drop=True)))


def mirror_to_store(existing, combined, db_path, time_col):
    """Uploade vers data-store les partitions mensuelles dont l'ensemble de
    lignes a CHANGÉ entre existing et combined (append, et compaction pour les
    vintages), chacune re-vérifiée. Best-effort, jamais bloquant (git déjà
    écrit, source de vérité). Signature identique à tous les pipelines racine."""
    try:
        changed = pd.concat([existing, combined]).drop_duplicates(keep=False)
        if changed.empty:
            return
        months = sorted(pd.to_datetime(changed[time_col]).dt.strftime("%Y-%m").unique())
        prefix = os.path.splitext(os.path.basename(db_path))[0]
        c_month = pd.to_datetime(combined[time_col]).dt.strftime("%Y-%m").to_numpy()
        _ensure_store_release()
        with tempfile.TemporaryDirectory() as tmp:
            for m in months:
                sub = combined[c_month == m]
                if sub.empty:
                    continue
                name = f"{prefix}_{m}.parquet"
                path = os.path.join(tmp, name)
                sub.to_parquet(path, index=False)
                _gh(["release", "upload", C.STORE_TAG, path, "--clobber"])
                back = os.path.join(tmp, "back_" + name)
                _gh(["release", "download", C.STORE_TAG, "--pattern", name,
                     "--output", back, "--clobber"])
                if not _same_rows(pd.read_parquet(back), sub):
                    raise RuntimeError(f"partition {name} divergente après upload")
                print(f"   🪞 miroir store : {name} ({len(sub):,} lignes) vérifié")
    except Exception as exc:  # noqa: BLE001 — jamais bloquant en double écriture
        print(f"   ⚠️  miroir store échoué (git intact, source de vérité) : {exc}")


# --------------------------------------------------------------------------- #
#  Lecture de l'EXISTANT depuis le magasin (bascule « magasin source de vérité »)
# --------------------------------------------------------------------------- #
# Étape préalable au débranchement des commits de parquet : dès que la CI cesse
# de committer, le fichier du clone est une copie GELÉE — repartir de lui
# tronquerait l'historique à chaque run, et la fusion réécrirait ensuite le
# magasin à partir de cette base amputée. L'existant doit donc venir du magasin.
#
# Activé par WEATHER_STORE_SOURCE, indépendant de WEATHER_STORE_WRITE : le
# miroir (écriture) et la source de l'existant (lecture) se basculent
# séparément, ce qui permet de valider la lecture en CI réelle alors que git
# reste committé et source de vérité.
#
# Contrairement au miroir, cette lecture est BLOQUANTE : un magasin injoignable
# doit faire échouer le run, jamais se rabattre sur le parquet du disque (cf.
# apps/snow/pipeline/store_mirror.load_existing, même règle). Bloc INLINE
# identique dans chaque pipeline racine (n'importe jamais core/ ni app/).

def _store_source_active():
    """True si l'existant doit être lu dans le magasin plutôt que sur le disque."""
    return os.environ.get("WEATHER_STORE_SOURCE", "").strip().lower() \
        not in ("", "0", "false")


def _store_partition_names(prefix):
    """Assets du flux `prefix`, triés par mois. Filtrage par préfixe
    STRICTEMENT ÉGAL (parse du nom) et jamais un startswith : `database_paris`
    attraperait sinon `database_paris_t2m` et mélangerait deux flux."""
    sortie = _gh(["release", "view", C.STORE_TAG, "--json", "assets",
                  "--jq", ".assets[].name"])
    noms = []
    for nom in sortie.split():
        if not nom.endswith(".parquet"):
            continue
        prefixe, _, mois = nom[:-len(".parquet")].rpartition("_")
        if prefixe != prefix or len(mois) != 7 or mois[4] != "-":
            continue
        if mois[:4].isdigit() and mois[5:].isdigit():
            noms.append(nom)
    return sorted(noms)


def load_existing_from_store(db_path, schema):
    """Base existante reconstituée depuis les partitions mensuelles du magasin,
    réalignée sur `schema` (colonne ajoutée après coup → NaN, comme la lecture
    disque). Toute erreur (magasin injoignable, asset illisible) se propage.

    Un magasin SANS aucune partition pour ce flux lève également : l'amorçage
    est le rôle explicite de tools/seed_store.py, jamais l'effet de bord d'un
    poll. Sans cette garde, un listing vide ferait repartir la collecte d'une
    base nulle, puis réécrirait la partition du mois courant avec le seul lot
    frais — l'historique du magasin y serait perdu."""
    prefix = os.path.splitext(os.path.basename(db_path))[0]
    noms = _store_partition_names(prefix)
    if not noms:
        raise RuntimeError(
            f"magasin sans partition pour {prefix} — amorçage attendu via "
            f"tools/seed_store.py, jamais par une collecte")
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for nom in noms:
            dest = os.path.join(tmp, nom)
            _gh(["release", "download", C.STORE_TAG, "--pattern", nom,
                 "--output", dest, "--clobber"])
            frames.append(pd.read_parquet(dest))
    df = pd.concat(frames, ignore_index=True)
    for col in schema:
        if col not in df.columns:
            df[col] = pd.NA
    print(f"   📦 existant lu du magasin : {len(df):,} lignes "
          f"({len(noms)} partition(s))")
    return df[schema]



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
    if os.environ.get("WEATHER_STORE_WRITE", "").strip().lower() not in ("", "0", "false"):
        mirror_to_store(existing, combined, C.DB_OBS_6M_PATH, "valid_time")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline observations 6 min : {exc}")
