# -*- coding: utf-8 -*-
"""Pipeline neige Megève — flux MAILLE FINE (prévision courte échéance).

Calque du flux annexe Tx/Tn HD du canicule (forecast_t2m_hd.py), en horaire :
  • un seul appel HTTP sur l'API Forecast standard (PAS l'API Ensemble),
    multi-points (village + sommet), modèles AROME France HD + ICON-D2 ;
  • pas de membres d'ensemble, pas de détection de cycle : chaque collecte est
    datée par son instant de poll (`fetched_at`, UTC tz-naïf) ; une collecte
    dont les valeurs sont identiques à la dernière stockée n'est pas
    ré-appendée (l'historique ne garde que les RÉVISIONS réelles) ;
  • horizon demandé 4 j (le besoin est J+2 à J+4) : les modèles s'arrêtent
    naturellement avant (~J+2 constaté) — les échéances au-delà sont absentes,
    cas normal. Une échéance sans AUCUNE valeur valide pour un (modèle, site)
    n'est pas stockée.

Parquet séparé (snow_config.DB_HD_PATH), append-only par
(fetched_at, model, site, target_datetime) — JAMAIS fusionné avec le flux
ensemble, jamais de comblement d'un flux par l'autre. Écriture atomique
(core.io.atomic). Ce script ne touche à aucun autre fichier de données.
"""

import os
import sys
import datetime as dt

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "..")))

import numpy as np
import pandas as pd

from apps.snow import snow_config as SC
from core.io.atomic import atomic_write_parquet
from core.services import openmeteo as OM


# --------------------------------------------------------------------------- #
#  Requête API
# --------------------------------------------------------------------------- #
def fetch_payload():
    """Appel HTTP unique : les deux modèles HD, les deux points."""
    params = {
        **OM.multi_coord_params(SC.SITES),
        "hourly": ",".join(v["api"] for v in SC.HD_VARIABLES),
        "models": ",".join(m["api"] for m in SC.HD_MODELS),
        "timezone": SC.TIMEZONE,
        "forecast_days": SC.HD_FORECAST_DAYS,
    }
    return OM.fetch_json(SC.HD_API_URL, params, SC.HTTP_TIMEOUT,
                         context="Open-Meteo Forecast (maille fine)")


# --------------------------------------------------------------------------- #
#  Normalisation JSON → table plate
# --------------------------------------------------------------------------- #
def parse_payload(payload, fetched_at):
    """JSON Open-Meteo Forecast → DataFrame plat HD_SCHEMA. Une ligne par
    (modèle, site, échéance) ayant AU MOINS une valeur valide — les échéances
    entièrement null (au-delà de l'horizon réel du modèle, ou variable non
    publiée : AROME HD ne sert ici que t2m/rafales) ne sont pas stockées, leur
    absence dit déjà tout. Filtrage par site comme au flux ensemble (une
    variable non déclarée pour un site reste NaN)."""
    rows = []
    for site, point in zip(SC.SITES, OM.as_payload_list(payload)):
        hourly = point.get("hourly", {})
        target = pd.to_datetime(hourly.get("time", []))
        n = len(target)
        if n == 0:
            continue
        for model in SC.HD_MODELS:
            frame = pd.DataFrame({
                "fetched_at": fetched_at,
                "model": model["label"],
                "site": site["code"],
                "target_datetime": target,
            })
            for var in SC.HD_VARIABLES:
                if site["code"] not in var["sites"]:
                    frame[var["col"]] = np.nan
                    continue
                vals = hourly.get(f"{var['api']}_{model['api']}")
                frame[var["col"]] = (
                    pd.to_numeric(pd.Series(vals), errors="coerce").to_numpy()
                    if vals is not None else np.nan)
            rows.append(frame.dropna(subset=SC.HD_VAR_COLS, how="all"))

    if not rows:
        return pd.DataFrame(columns=SC.HD_SCHEMA)
    return pd.concat(rows, ignore_index=True)[SC.HD_SCHEMA]


# --------------------------------------------------------------------------- #
#  Persistance
# --------------------------------------------------------------------------- #
def load_existing():
    """Base HD existante, réalignée sur le schéma courant (colonne ajoutée
    après coup → NaN) — même principe que les autres flux."""
    if os.path.exists(SC.DB_HD_PATH):
        df = pd.read_parquet(SC.DB_HD_PATH)
        for col in SC.HD_SCHEMA:
            if col not in df.columns:
                df[col] = np.nan
        return df[SC.HD_SCHEMA]
    return pd.DataFrame(columns=SC.HD_SCHEMA)


def _drop_unchanged(fresh, existing):
    """Écarte de `fresh` les lignes dont toutes les variables sont identiques
    à la DERNIÈRE valeur stockée pour ce (model, site, target_datetime) : le
    cron repolle plus souvent que les modèles ne se renouvellent — sans ce
    filtre, l'historique serait noyé de copies sans information."""
    if existing.empty:
        return fresh
    last = (existing.sort_values("fetched_at")
                    .groupby(["model", "site", "target_datetime"], as_index=False).last())
    merged = fresh.merge(last[["model", "site", "target_datetime", *SC.HD_VAR_COLS]],
                         on=["model", "site", "target_datetime"],
                         how="left", suffixes=("", "_old"))

    def _same(a, b):
        return (a == b) | (a.isna() & b.isna())

    unchanged = pd.Series(True, index=merged.index)
    for col in SC.HD_VAR_COLS:
        unchanged &= _same(merged[col], merged[f"{col}_old"])
    return fresh[~unchanged.to_numpy()].reset_index(drop=True)


def persist(fresh, existing=None):
    """Append des seules lignes réellement nouvelles/révisées, puis écriture
    atomique. Aucune ligne existante n'est modifiée ni supprimée :
    historique append-only."""
    if existing is None:
        existing = load_existing()
    fresh = _drop_unchanged(fresh, existing)
    if fresh.empty:
        return existing, 0

    combined = pd.concat([existing, fresh], ignore_index=True) \
                 .sort_values(["fetched_at", "model", "site", "target_datetime"]) \
                 .reset_index(drop=True)

    os.makedirs(SC.DATA_DIR, exist_ok=True)
    atomic_write_parquet(combined, SC.DB_HD_PATH)
    return combined, len(fresh)


# --------------------------------------------------------------------------- #
#  Entrée
# --------------------------------------------------------------------------- #
def main():
    fetched_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    print("⏳ Requête Open-Meteo Forecast (maille fine) — Megève…")
    fresh = parse_payload(fetch_payload(), fetched_at)
    if fresh.empty:
        print("ℹ️  Aucune valeur exploitable dans la réponse — base laissée telle quelle.")
        return
    for (model_label, site), g in fresh.groupby(["model", "site"]):
        print(f"   {model_label} @ {site} : {len(g)} échéance(s), jusqu'au "
              f"{g['target_datetime'].max():%d %b %Hh}")

    combined, n_new = persist(fresh)
    if n_new == 0:
        print("ℹ️  Valeurs identiques à la dernière collecte — rien à écrire.")
        return
    print(f"✅ Base maille fine mise à jour : +{n_new} ligne(s) · {len(combined):,} au total")
    print(f"   → {SC.DB_HD_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline neige (maille fine) : {exc}")
