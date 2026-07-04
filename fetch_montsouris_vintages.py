# -*- coding: utf-8 -*-
"""Pipeline ANNEXE de prévision Montsouris « vintages » 15 minutes — API Forecast
standard d'Open-Meteo → parquet séparé data/database_paris_montsouris_vintages.parquet
(config.DB_VINTAGE_PATH).

Quatrième flux annexe du projet, indépendant des trois autres (Forecast.py,
forecast_t2m_hd.py, fetch_observations.py) : il ne touche à AUCUN autre fichier
de données. Troisième granularité temporelle, à ne PAS fusionner :
  • ensemble T850  : horaire, 16 j, synoptique (Forecast.py) ;
  • Tx/Tn HD       : journalier, 7 j (forecast_t2m_hd.py) ;
  • vintages 15 min : quart-horaire, ~48 h de futur (ICI) — très court terme fin.

Un seul appel HTTP sur l'endpoint Forecast (PAS l'endpoint Ensemble), en
`minutely_15=temperature_2m,relative_humidity_2m,precipitation`, modèle
meteofrance_seamless, AU POINT DE MONTSOURIS (station de référence, coordonnées
VINTAGE_LAT/LON — pas le point Paris générique). Unités déjà exploitables
(°C, %, mm) : AUCUN recalcul au parsing (contrairement aux observations MF en
Kelvin/Pa).

INVARIANT — historique des vintages (append-only sur (valid_time, fetched_at)).
Une prévision est RÉVISABLE : la valeur d'une même échéance future change
légitimement d'un poll à l'autre à mesure qu'on s'en rapproche. On CONSERVE ces
révisions successives — chaque poll ajoute un « vintage » (une ligne
(valid_time, fetched_at)) sans jamais écraser un vintage antérieur. C'est ce qui
permet de comparer, pour une même échéance, les prévisions émises il y a
6/12/18/24 h et de visualiser la convergence. La clé est donc le COUPLE
(valid_time, fetched_at), jamais valid_time seul.

Compaction — la table est bornée par `compact()` : au-delà d'une fenêtre de
VINTAGE_RETENTION_H autour de l'instant du run, une échéance passée ne conserve
que son vintage le plus proche de la réalisation. Dans la fenêtre, tous les
vintages sont gardés.

Bootstrap — au TOUT PREMIER run (parquet absent), un seul appel avec un large
past_days (VINTAGE_BACKFILL_PAST_DAYS_INIT) amorce plusieurs jours d'historique,
marqués source="bootstrap" (approximation proche de l'observé, PAS un vrai
vintage figé). Aux runs suivants, un past_days modeste (VINTAGE_BACKFILL_PAST_DAYS)
comble un trou de cron ; ces lignes sont source="live".

Écriture atomique (tmp + os.replace), jamais d'état partiel sur le disque.
Dégradation silencieuse : réponse sans bloc minutely_15 exploitable → aucune
écriture, sortie propre.
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
    return (C.VINTAGE_BACKFILL_PAST_DAYS_INIT if not os.path.exists(C.DB_VINTAGE_PATH)
            else C.VINTAGE_BACKFILL_PAST_DAYS)


def fetch_payload(past_days=None):
    """Appel HTTP unique (minutely_15, modèle unique) au point de Montsouris.
    Même endpoint et même timezone UTC que le pipeline T2m HD, mais coordonnées
    dédiées (VINTAGE_LAT/LON) : ces prévisions sont confrontées aux observations
    de CETTE station."""
    params = {
        "latitude": C.VINTAGE_LAT,
        "longitude": C.VINTAGE_LON,
        "minutely_15": ",".join(v["api"] for v in C.VINTAGE_VARIABLES),
        "models": C.VINTAGE_MODEL,
        "timezone": C.TIMEZONE,
        "past_days": _past_days() if past_days is None else past_days,
    }
    try:
        resp = requests.get(C.VINTAGE_API_URL, params=params, timeout=C.HTTP_TIMEOUT)
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
def parse_payload(payload, fetched_at, source):
    """JSON Open-Meteo minutely_15 → DataFrame plat VINTAGE_SCHEMA.

    `source` ("live"/"bootstrap") marque tout le lot du poll : bootstrap = premier
    comblement via past_days (approximation proche de l'observé), live = poll
    régulier. Une ligne par échéance quart-horaire ayant AU MOINS une valeur
    valide ; les échéances entièrement NaN (donnée pas encore consolidée en tout
    début de fenêtre, cas normal) sont écartées. Une réponse sans bloc
    minutely_15 exploitable → DataFrame vide (dégradation propre)."""
    block = payload.get("minutely_15") or {}
    times = pd.to_datetime(block.get("time", []))
    if len(times) == 0:
        return pd.DataFrame(columns=C.VINTAGE_SCHEMA)
    # C.TIMEZONE="UTC" → offset toujours nul ; lu depuis le payload par prudence
    # (cohérent avec Forecast.parse_payload) plutôt que supposé.
    utc_offset = int(payload.get("utc_offset_seconds", 0))
    valid_time = times - pd.Timedelta(seconds=utc_offset)

    data = {"valid_time": valid_time, "fetched_at": fetched_at}
    for var in C.VINTAGE_VARIABLES:
        vals = block.get(var["api"])
        data[var["col"]] = (pd.to_numeric(pd.Series(vals), errors="coerce").to_numpy()
                            if vals is not None else float("nan"))
    frame = pd.DataFrame(data)
    frame = frame.dropna(subset=C.VINTAGE_VAR_COLS, how="all")
    frame["source"] = source
    return frame[C.VINTAGE_SCHEMA]


# --------------------------------------------------------------------------- #
#  Compaction — étape séparée, pure (sans I/O), testable isolément
# --------------------------------------------------------------------------- #
def compact(df, now):
    """Borne la table à deux régimes autour de `now` (instant du run) :

      • valid_time DANS la fenêtre [now − RETENTION, now + RETENTION] (passé ou
        futur) : TOUS les vintages conservés — jamais compacter une échéance
        encore (même partiellement) dans la fenêtre.
      • valid_time SORTI de la fenêtre côté PASSÉ (< now − RETENTION) : on ne
        garde QUE la ligne dont fetched_at est le plus proche du valid_time
        (dernière estimation avant réalisation ; privilégie naturellement un
        vintage live proche plutôt qu'un bootstrap lointain).
      • valid_time futur hors fenêtre (> now + RETENTION) : conservé tel quel
        (le modèle ne va guère au-delà de 48 h — cas quasi vide).

    Fonction PURE : ne lit ni n'écrit aucun fichier, prend/rend un DataFrame."""
    if df.empty:
        return df
    df = df.copy()
    vt = pd.to_datetime(df["valid_time"])
    fa = pd.to_datetime(df["fetched_at"])
    horizon = pd.Timedelta(hours=C.VINTAGE_RETENTION_H)
    now = pd.Timestamp(now)

    # Échéances passées HORS fenêtre → une seule ligne (fetched_at le plus proche
    # du valid_time). Tout le reste (dans la fenêtre + futur hors fenêtre) intact.
    stale_past = vt < (now - horizon)
    keep = df[~stale_past]

    stale = df[stale_past]
    if not stale.empty:
        dist = (fa[stale_past] - vt[stale_past]).abs()
        # idxmin par valid_time → index de la ligne la plus proche de la réalisation.
        winners = dist.groupby(stale["valid_time"]).idxmin()
        keep = pd.concat([keep, stale.loc[winners.to_numpy()]], ignore_index=False)

    return keep.sort_values(["valid_time", "fetched_at"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
#  Persistance — append-only sur (valid_time, fetched_at) + compaction
# --------------------------------------------------------------------------- #
def load_existing():
    """Base vintages existante, réalignée sur le schéma courant (colonne ajoutée
    après coup → NaN) — même principe que les autres pipelines."""
    if os.path.exists(C.DB_VINTAGE_PATH):
        df = pd.read_parquet(C.DB_VINTAGE_PATH)
        for col in C.VINTAGE_SCHEMA:
            if col not in df.columns:
                df[col] = pd.NA
        return df[C.VINTAGE_SCHEMA]
    return pd.DataFrame(columns=C.VINTAGE_SCHEMA)


def persist(fresh, existing=None, now=None):
    """APPEND-ONLY par (valid_time, fetched_at) puis compaction, écriture atomique.

    Chaque vintage frais s'AJOUTE à la base — jamais d'upsert qui écraserait un
    vintage antérieur (invariant du flux). Dédup défensive sur le couple (clé
    déjà unique en pratique : fetched_at unique à la seconde par run). La
    compaction (au-delà de VINTAGE_RETENTION_H) est appliquée APRÈS l'append :
    elle ne touche que le passé hors-fenêtre, jamais les lignes fraîches proches
    de `now`, donc aucune perte possible.

    `now` par défaut = instant courant UTC. Retourne (base combinée, nb de
    vintages ajoutés)."""
    if existing is None:
        existing = load_existing()
    if now is None:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if fresh.empty:
        return existing, 0

    combined = pd.concat([existing, fresh], ignore_index=True)
    combined = combined.drop_duplicates(subset=["valid_time", "fetched_at"])
    n_new = len(combined) - len(existing.drop_duplicates(subset=["valid_time", "fetched_at"]))

    combined = compact(combined, now)

    os.makedirs(C.DATA_DIR, exist_ok=True)
    tmp = C.DB_VINTAGE_PATH + ".tmp"
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, C.DB_VINTAGE_PATH)
    return combined, n_new


# --------------------------------------------------------------------------- #
#  Entrée
# --------------------------------------------------------------------------- #
def main():
    fetched_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    amorcage = not os.path.exists(C.DB_VINTAGE_PATH)
    source = "bootstrap" if amorcage else "live"
    pd_used = _past_days()
    print(f"⏳ Requête Open-Meteo Forecast (minutely_15, {C.VINTAGE_MODEL}, "
          f"Montsouris, past_days={pd_used}, source={source})…")
    payload = fetch_payload()

    fresh = parse_payload(payload, fetched_at, source)
    if fresh.empty:
        print("ℹ️  Aucune échéance 15 min exploitable dans la réponse — base laissée telle quelle.")
        return
    print(f"   {len(fresh)} échéance(s) 15 min, de {fresh['valid_time'].min():%d %b %H:%M} "
          f"à {fresh['valid_time'].max():%d %b %H:%M} UTC")

    combined, n_new = persist(fresh, now=fetched_at)
    print(f"✅ Base vintages mise à jour : +{n_new} vintage(s) frais "
          f"· {len(combined):,} ligne(s) au total (après compaction)")
    print(f"   → {C.DB_VINTAGE_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline vintages Montsouris : {exc}")
