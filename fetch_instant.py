# -*- coding: utf-8 -*-
"""Pipeline ANNEXE de prévision instantanée 15 minutes — API Forecast standard
d'Open-Meteo → parquet séparé data/database_paris_instant.parquet
(config.DB_INSTANT_PATH).

Quatrième flux annexe du projet, indépendant des trois autres (Forecast.py,
forecast_t2m_hd.py, fetch_observations.py) : il ne touche à AUCUN autre fichier
de données. Troisième granularité temporelle, à ne PAS fusionner :
  • ensemble T850  : horaire, 16 j, synoptique (Forecast.py) ;
  • Tx/Tn HD       : journalier, 7 j (forecast_t2m_hd.py) ;
  • instantané     : 15 min, ~48 h de futur (ICI) — très court terme fin.

Un seul appel HTTP sur l'endpoint Forecast (PAS l'endpoint Ensemble), en
`minutely_15=temperature_2m,relative_humidity_2m,precipitation`, modèle
meteofrance_seamless. Unités déjà exploitables (°C, %, mm) : AUCUN recalcul au
parsing (contrairement aux observations MF en Kelvin/Pa).

Déduplication = `validtime` SEUL (upsert, dernière prévision conservée) : à la
différence d'une observation (fait acquis, jamais remplacé), une prévision est
RÉVISABLE — la valeur d'une même échéance future change légitimement d'un poll
à l'autre à mesure qu'on s'en rapproche. On ne garde donc que l'estimation la
plus fraîche de chaque échéance (pas d'historique des révisions : table
légère). `fetched_at` est conservé en colonne comme MÉTA (fraîcheur de la
valeur affichée), jamais dans la clé.

Backfill via `past_days` : au TOUT PREMIER run (parquet absent), amorçage de
plusieurs jours d'historique d'un coup (INSTANT_BACKFILL_PAST_DAYS_INIT) ; aux
runs suivants, un past_days modeste (INSTANT_BACKFILL_PAST_DAYS) suffit à
combler un trou de cron sans re-télécharger tout l'historique — la fenêtre
future ~48 h est de toute façon toujours renvoyée.

Écriture atomique (tmp + os.replace), jamais d'état partiel sur le disque.
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
def _past_days():
    """past_days adapté à l'état de la base : large au premier run (parquet
    absent → amorçage d'historique), modeste ensuite (simple rattrapage d'un
    trou de cron ; la fenêtre future est renvoyée quoi qu'il arrive)."""
    return (C.INSTANT_BACKFILL_PAST_DAYS_INIT if not os.path.exists(C.DB_INSTANT_PATH)
            else C.INSTANT_BACKFILL_PAST_DAYS)


def fetch_payload(past_days=None):
    """Appel HTTP unique (minutely_15, modèle unique). Même endpoint et même
    timezone UTC que le pipeline T2m HD — une seule source de vérité pour les
    coordonnées (config.LATITUDE/LONGITUDE)."""
    params = {
        "latitude": C.LATITUDE,
        "longitude": C.LONGITUDE,
        "minutely_15": ",".join(v["api"] for v in C.INSTANT_VARIABLES),
        "models": C.INSTANT_MODEL,
        "timezone": C.TIMEZONE,
        "past_days": _past_days() if past_days is None else past_days,
    }
    try:
        resp = requests.get(C.INSTANT_API_URL, params=params, timeout=C.HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise SystemExit(
            f"❌ Timeout Open-Meteo (Forecast minutely_15) après {C.HTTP_TIMEOUT} s — "
            "l'API est lente ou injoignable. Relancer dans quelques minutes."
        )
    except requests.exceptions.ConnectionError as exc:
        raise SystemExit(f"❌ Erreur réseau Open-Meteo (Forecast minutely_15) : {exc}")
    except requests.exceptions.HTTPError as exc:
        raise SystemExit(f"❌ Erreur HTTP Open-Meteo (Forecast minutely_15) "
                         f"{exc.response.status_code} : {exc}")
    return resp.json()


# --------------------------------------------------------------------------- #
#  Normalisation JSON → table plate
# --------------------------------------------------------------------------- #
def parse_payload(payload, fetched_at):
    """JSON Open-Meteo minutely_15 → DataFrame plat INSTANT_SCHEMA.

    Une ligne par échéance quart-horaire ayant AU MOINS une valeur valide ; les
    échéances entièrement NaN (donnée pas encore consolidée en tout début de
    fenêtre, cas normal) sont écartées — leur absence dit déjà tout. Une réponse
    sans bloc minutely_15 exploitable → DataFrame vide (dégradation propre)."""
    block = payload.get("minutely_15") or {}
    times = pd.to_datetime(block.get("time", []))
    if len(times) == 0:
        return pd.DataFrame(columns=C.INSTANT_SCHEMA)
    # C.TIMEZONE="UTC" → offset toujours nul ; lu depuis le payload par prudence
    # (cohérent avec Forecast.parse_payload) plutôt que supposé.
    utc_offset = int(payload.get("utc_offset_seconds", 0))
    validtime = times - pd.Timedelta(seconds=utc_offset)

    data = {"validtime": validtime, "fetched_at": fetched_at}
    for var in C.INSTANT_VARIABLES:
        vals = block.get(var["api"])
        data[var["col"]] = (pd.to_numeric(pd.Series(vals), errors="coerce").to_numpy()
                            if vals is not None else float("nan"))
    frame = pd.DataFrame(data)
    return frame.dropna(subset=C.INSTANT_VAR_COLS, how="all")[C.INSTANT_SCHEMA]


# --------------------------------------------------------------------------- #
#  Persistance — upsert sur validtime, écriture atomique
# --------------------------------------------------------------------------- #
def load_existing():
    """Base instantanée existante, réalignée sur le schéma courant (colonne
    ajoutée après coup → NaN) — même principe que les autres pipelines."""
    if os.path.exists(C.DB_INSTANT_PATH):
        df = pd.read_parquet(C.DB_INSTANT_PATH)
        for col in C.INSTANT_SCHEMA:
            if col not in df.columns:
                df[col] = pd.NA
        return df[C.INSTANT_SCHEMA]
    return pd.DataFrame(columns=C.INSTANT_SCHEMA)


def persist(fresh, existing=None):
    """Upsert par `validtime` : la prévision fraîche REMPLACE la valeur stockée
    pour les échéances qu'elle recouvre (révision — on garde toujours la plus
    récente), et append les échéances inédites. Les échéances antérieures à la
    fenêtre du poll (hors past_days) ne sont pas touchées : elles conservent
    leur dernière valeur connue, figée. Écriture atomique (tmp + os.replace).

    Retourne (base combinée, nb d'échéances ajoutées, nb d'échéances révisées)."""
    if existing is None:
        existing = load_existing()
    if fresh.empty:
        return existing, 0, 0

    if existing.empty:
        combined = fresh.copy()
        n_new, n_rev = len(fresh), 0
    else:
        fresh_vts = set(fresh["validtime"])
        overlap_mask = existing["validtime"].isin(fresh_vts).to_numpy()
        n_rev = int(overlap_mask.sum())            # échéances déjà connues, réécrites
        n_new = len(fresh) - n_rev                 # échéances inédites
        # On retire de l'existant tout validtime que `fresh` recouvre, puis append :
        # la nouvelle valeur (plus fraîche) l'emporte systématiquement.
        combined = pd.concat([existing[~overlap_mask], fresh], ignore_index=True)

    combined = combined.sort_values("validtime").reset_index(drop=True)

    os.makedirs(C.DATA_DIR, exist_ok=True)
    tmp = C.DB_INSTANT_PATH + ".tmp"
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, C.DB_INSTANT_PATH)
    return combined, n_new, n_rev


# --------------------------------------------------------------------------- #
#  Entrée
# --------------------------------------------------------------------------- #
def main():
    fetched_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    pd_used = _past_days()
    amorcage = not os.path.exists(C.DB_INSTANT_PATH)
    print(f"⏳ Requête Open-Meteo Forecast (minutely_15, {C.INSTANT_MODEL}, "
          f"past_days={pd_used}{' — amorçage' if amorcage else ''})…")
    payload = fetch_payload()

    fresh = parse_payload(payload, fetched_at)
    if fresh.empty:
        print("ℹ️  Aucune échéance 15 min exploitable dans la réponse — base laissée telle quelle.")
        return
    print(f"   {len(fresh)} échéance(s) 15 min, de {fresh['validtime'].min():%d %b %H:%M} "
          f"à {fresh['validtime'].max():%d %b %H:%M} UTC")

    combined, n_new, n_rev = persist(fresh)
    print(f"✅ Base instantanée mise à jour : +{n_new} nouvelle(s), {n_rev} révisée(s) "
          f"· {len(combined):,} échéance(s) au total")
    print(f"   → {C.DB_INSTANT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline instantané : {exc}")
