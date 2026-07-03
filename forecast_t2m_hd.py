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
    l'évolution future du schéma — même principe que Forecast.load_existing."""
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

    combined, n_new = persist(fresh)
    if n_new == 0:
        print("ℹ️  Valeurs identiques à la dernière collecte — rien à écrire.")
        return
    print(f"✅ Base Tx/Tn mise à jour : +{n_new} ligne(s) · {len(combined):,} au total")
    print(f"   → {C.DB_T2M_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline Tx/Tn : {exc}")
