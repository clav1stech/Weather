# -*- coding: utf-8 -*-
"""Pipeline ANNEXE Tx/Tn haute résolution — API Forecast standard d'Open-Meteo
→ parquet séparé data/database_paris_t2m.parquet (cf. config.DB_T2M_PATH).

Flux volontairement simple, indépendant du pipeline d'ensemble (Forecast.py) :
  • un seul appel HTTP sur l'endpoint Forecast (PAS l'endpoint Ensemble), en
    `daily=temperature_2m_max,temperature_2m_min` — les valeurs stockées sont
    celles renvoyées telles quelles par l'API, aucun recalcul maison ;
  • pas de membres d'ensemble, pas de détection de cycle : les modèles
    « seamless » (AROME→ARPEGE, ICON-D2→ICON) mélangent plusieurs grilles et
    n'ont pas de run synoptique unique identifiable — chaque collecte est datée
    par son instant de poll (`fetched_at`, UTC tz-naïf), et une collecte dont
    les valeurs sont identiques à la dernière stockée n'est pas ré-appendée
    (l'historique ne garde que les RÉVISIONS réelles, pas 12 copies par jour) ;
  • horizon court assumé (config.T2M_FORECAST_DAYS = 4 j) : Météo-France ne
    publie que J à J+3 (null au-delà, constaté empiriquement) ; DWD ICON sert
    de secours jour par jour. Ce flux est un appoint d'affichage, jamais une
    extension d'horizon du dashboard.

Schéma parquet (config.T2M_SCHEMA, stable et rétro-compatible) :
  [fetched_at, model, target_date, tx, tn]
  — fetched_at   : instant UTC (tz-naïf) de la collecte ;
  — model        : label court (config.T2M_MODELS) ;
  — target_date  : jour cible (datetime normalisé, jour UTC de l'API) ;
  — tx / tn      : temperature_2m_max / temperature_2m_min (°C), NaN toléré.
Un jour sans AUCUNE valeur valide pour un modèle n'est pas stocké (ex. J+4 et
au-delà chez Météo-France) : l'absence est un état normal, pas une erreur.

Écriture atomique (tmp + os.replace), comme le reste du pipeline. Ce script ne
touche à AUCUN autre fichier de données (ni DB_PATH, ni legacy/).
"""

import os
import sys
import subprocess
import tempfile
import datetime as dt

import requests
import pandas as pd

import config as C


# --------------------------------------------------------------------------- #
#  Requête API
# --------------------------------------------------------------------------- #
def fetch_payload():
    """Appel HTTP unique couvrant les modèles HD (mêmes coordonnées et même
    timezone UTC que le pipeline principal — une seule source de vérité)."""
    params = {
        "latitude": C.LATITUDE,
        "longitude": C.LONGITUDE,
        "daily": "temperature_2m_max,temperature_2m_min",
        "models": ",".join(m["api"] for m in C.T2M_MODELS),
        "timezone": C.TIMEZONE,
        "forecast_days": C.T2M_FORECAST_DAYS,
    }
    try:
        resp = requests.get(C.T2M_API_URL, params=params, timeout=C.HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise SystemExit(
            f"❌ Timeout Open-Meteo (Forecast) après {C.HTTP_TIMEOUT} s — "
            "l'API est lente ou injoignable. Relancer dans quelques minutes."
        )
    except requests.exceptions.ConnectionError as exc:
        raise SystemExit(f"❌ Erreur réseau Open-Meteo (Forecast) : {exc}")
    except requests.exceptions.HTTPError as exc:
        raise SystemExit(f"❌ Erreur HTTP Open-Meteo (Forecast) "
                         f"{exc.response.status_code} : {exc}")
    return resp.json()


# --------------------------------------------------------------------------- #
#  Normalisation JSON → table plate
# --------------------------------------------------------------------------- #
def _daily_series(daily, var_api, model_api):
    """Valeurs d'une variable daily pour un modèle. En requête multi-modèles,
    l'API suffixe chaque clé du nom du modèle (`temperature_2m_max_<model>`) ;
    en mono-modèle elle ne suffixe PAS (constaté empiriquement) — les deux
    formes sont acceptées pour que la config reste libre de ne garder qu'un
    modèle. None si la clé est absente (modèle non servi dans la réponse)."""
    return daily.get(f"{var_api}_{model_api}", daily.get(var_api)
                     if len(C.T2M_MODELS) == 1 else None)


def parse_payload(payload, fetched_at):
    """JSON Open-Meteo Forecast → DataFrame plat T2M_SCHEMA.

    Une ligne par (modèle, jour cible) ayant AU MOINS une valeur valide : les
    jours entièrement null (ex. au-delà de l'horizon réel de Météo-France) ne
    sont pas stockés — leur absence dit déjà tout, inutile d'archiver des NaN.
    Un modèle totalement absent du payload est simplement ignoré (le secours
    jour par jour se joue à l'affichage, pas ici)."""
    daily = payload.get("daily", {})
    target_dates = pd.to_datetime(daily.get("time", []))

    rows = []
    for model in C.T2M_MODELS:
        tx = _daily_series(daily, "temperature_2m_max", model["api"])
        tn = _daily_series(daily, "temperature_2m_min", model["api"])
        if tx is None and tn is None:
            continue
        frame = pd.DataFrame({
            "fetched_at": fetched_at,
            "model": model["label"],
            "target_date": target_dates,
            "tx": pd.to_numeric(pd.Series(tx if tx is not None else [None] * len(target_dates)),
                                errors="coerce").to_numpy(),
            "tn": pd.to_numeric(pd.Series(tn if tn is not None else [None] * len(target_dates)),
                                errors="coerce").to_numpy(),
        })
        rows.append(frame.dropna(subset=["tx", "tn"], how="all"))

    if not rows:
        return pd.DataFrame(columns=C.T2M_SCHEMA)
    return pd.concat(rows, ignore_index=True)[C.T2M_SCHEMA]


# --------------------------------------------------------------------------- #
#  Persistance
# --------------------------------------------------------------------------- #
def load_existing():
    """Base T2m existante, réalignée sur le schéma courant (colonne ajoutée
    après coup → NaN) : l'historique déjà stocké reste lisible quelle que soit
    l'évolution future du schéma — même principe que Forecast.load_existing.

    Source de vérité : le magasin externe dès que WEATHER_STORE_SOURCE est
    positionné (le parquet du disque n'est alors qu'une copie potentiellement
    gelée), sinon le disque comme avant. Le branchement vit ICI et non dans
    main() pour couvrir aussi le rechargement interne de persist()."""
    if _store_source_active():
        return load_existing_from_store(C.DB_T2M_PATH, C.T2M_SCHEMA)
    if os.path.exists(C.DB_T2M_PATH):
        df = pd.read_parquet(C.DB_T2M_PATH)
        for col in C.T2M_SCHEMA:
            if col not in df.columns:
                df[col] = pd.NA
        return df[C.T2M_SCHEMA]
    return pd.DataFrame(columns=C.T2M_SCHEMA)


def _drop_unchanged(fresh, existing):
    """Écarte de `fresh` les lignes dont (tx, tn) est identique à la DERNIÈRE
    valeur stockée pour ce (model, target_date) : le cron tourne toutes les 2 h
    mais les modèles ne se renouvellent que quelques fois par jour — sans ce
    filtre, l'historique serait noyé de copies identiques sans information."""
    if existing.empty:
        return fresh
    last = (existing.sort_values("fetched_at")
                    .groupby(["model", "target_date"], as_index=False).last())
    merged = fresh.merge(last[["model", "target_date", "tx", "tn"]],
                         on=["model", "target_date"],
                         how="left", suffixes=("", "_old"))

    def _same(a, b):
        return (a == b) | (a.isna() & b.isna())

    unchanged = (_same(merged["tx"], merged["tx_old"])
                 & _same(merged["tn"], merged["tn_old"])).to_numpy()
    return fresh[~unchanged].reset_index(drop=True)


def persist(fresh, existing=None):
    """Append des seules lignes réellement nouvelles/révisées, puis écriture
    atomique (tmp + os.replace — jamais d'état partiel sur le disque). Aucune
    ligne existante n'est modifiée ni supprimée : historique append-only."""
    if existing is None:
        existing = load_existing()
    fresh = _drop_unchanged(fresh, existing)
    if fresh.empty:
        return existing, 0

    combined = pd.concat([existing, fresh], ignore_index=True) \
                 .sort_values(["fetched_at", "model", "target_date"]) \
                 .reset_index(drop=True)

    os.makedirs(C.DATA_DIR, exist_ok=True)
    tmp = C.DB_T2M_PATH + ".tmp"
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, C.DB_T2M_PATH)
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
    fetched_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    print("⏳ Requête Open-Meteo Forecast (Tx/Tn HD)…")
    payload = fetch_payload()

    fresh = parse_payload(payload, fetched_at)
    if fresh.empty:
        print("ℹ️  Aucune valeur Tx/Tn exploitable dans la réponse — base laissée telle quelle.")
        return
    for model_label, g in fresh.groupby("model"):
        print(f"   {model_label} : {len(g)} jour(s), du "
              f"{g['target_date'].min():%d %b} au {g['target_date'].max():%d %b}")

    existing = load_existing()
    combined, n_new = persist(fresh, existing)
    if n_new == 0:
        print("ℹ️  Valeurs identiques à la dernière collecte — rien à écrire.")
        return
    print(f"✅ Base Tx/Tn mise à jour : +{n_new} ligne(s) · {len(combined):,} au total")
    print(f"   → {C.DB_T2M_PATH}")
    if os.environ.get("WEATHER_STORE_WRITE", "").strip().lower() not in ("", "0", "false"):
        mirror_to_store(existing, combined, C.DB_T2M_PATH, "fetched_at")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline Tx/Tn : {exc}")
