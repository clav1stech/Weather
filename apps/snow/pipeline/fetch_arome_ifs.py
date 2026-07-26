# -*- coding: utf-8 -*-
"""Collecte AROME-IFS horaire H+1 à H+45 aux deux sites de Megève.

AROME-IFS est une ressource de l'API AROME Météo-France : même cœur haute
résolution qu'AROME, mais initialisation et conditions latérales issues
d'IFS/ECMWF. Il ne doit donc pas être confondu avec AROME France obtenu via
Open-Meteo, ni moyenné silencieusement avec lui.

Trois champs horaires suffisent ici : cumul total, cumul neige en équivalent
eau et température à 2 m. Une petite emprise unique contient village et
sommet, donc chaque GRIB est téléchargé une fois et décodé aux deux points.
Au quota de 50 requêtes/minute, un nouveau cycle représente 135 appels espacés
de 1,3 s ; un cycle déjà complet ne produit aucun appel GetCoverage.
"""

import datetime as dt
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _ROOT)

import numpy as np
import pandas as pd
import requests

from apps.snow import snow_config as SC
from apps.snow.pipeline import mf_summary as MS
from core.pipeline import ensemble_runs as ER
from core.services import meteofrance_wcs as WCS


MODEL = SC.AROME_IFS_MODEL
_MM_UNITS = {"kg m-2", "kg m**-2", "kg m^-2", "mm"}
_KELVIN_UNITS = {"k", "kelvin"}
_CELSIUS_UNITS = {"c", "degc", "°c", "degree celsius"}


def _url(operation):
    return SC.AROME_IFS_WCS_URL.format(operation=operation)


def discover_cycle(session, key):
    """Retourne le dernier cycle commun aux trois produits requis."""
    payload = WCS.get_capabilities(
        session, _url("GetCapabilities"), key=key, timeout=SC.HTTP_TIMEOUT)
    ids = WCS.coverage_ids(payload)
    selected = {
        column: WCS.latest_coverage(ids, product, period)
        for column, (product, period) in SC.AROME_IFS_PRODUCTS.items()
    }
    missing = [column for column, coverage in selected.items() if coverage is None]
    if missing:
        raise RuntimeError(
            "Coverage AROME-IFS absent pour : " + ", ".join(missing))
    runs = {WCS.coverage_run_date(coverage) for coverage in selected.values()}
    if len(runs) != 1:
        raise RuntimeError("Les produits AROME-IFS ne portent pas le même cycle.")
    return runs.pop(), selected


def _precip_mm(point):
    units = point.units.strip().lower()
    if units not in _MM_UNITS:
        raise ValueError(
            f"Unité de précipitation AROME-IFS inattendue : {point.units}")
    value = float(point.value)
    if not np.isfinite(value) or value < -0.01:
        raise ValueError(f"Cumul AROME-IFS incohérent : {value}")
    return max(value, 0.0)


def _temperature_c(point):
    units = point.units.strip().lower()
    value = float(point.value)
    if not np.isfinite(value):
        raise ValueError("Température AROME-IFS non finie.")
    if units in _KELVIN_UNITS:
        return value - 273.15
    if units in _CELSIUS_UNITS:
        return value
    raise ValueError(f"Unité de température AROME-IFS inattendue : {point.units}")


def candidate_from_points(run_date, points):
    """Normalise les points ``(step_s, produit, site)`` au schéma local."""
    run_date = pd.Timestamp(run_date).to_pydatetime().replace(tzinfo=None)
    rows = []
    for step_s in SC.AROME_IFS_STEPS_S:
        expected_valid = run_date + dt.timedelta(seconds=int(step_s))
        for site in SC.SITES:
            site_code = site["code"]
            values = {
                column: points[(step_s, column, site_code)]
                for column in SC.AROME_IFS_PRODUCTS
            }
            for point in values.values():
                if point.run_date != run_date or point.valid_time != expected_valid:
                    raise ValueError(
                        "Cycle/échéance du GRIB AROME-IFS incohérent.")
            precip = _precip_mm(values["precip"])
            snow = _precip_mm(values["neige_eau"])
            if snow > precip + 0.05:
                raise ValueError(
                    f"Neige AROME-IFS ({snow:.3f}) supérieure au total "
                    f"({precip:.3f}) au-delà de la tolérance.")
            snow = min(snow, precip)
            row = {column: np.nan for column in SC.MF_LOCAL_SCHEMA}
            row.update({
                "run_date": run_date, "model": MODEL,
                "kind": "deterministic", "member": 0, "site": site_code,
                "valid_time": expected_valid, "period_h": 1,
                "cell_lat": values["precip"].latitude,
                "cell_lon": values["precip"].longitude,
                "precip": precip, "neige_eau": snow,
                "pluie_eau": max(precip - snow, 0.0),
                "t2m": _temperature_c(values["t2m"]),
            })
            rows.append(row)
    candidate = pd.DataFrame(rows)[SC.MF_LOCAL_SCHEMA]
    validate_complete(candidate, run_date)
    return candidate


def validate_complete(candidate, run_date):
    """Exige H+1–H+45 aux deux sites et les trois variables météo."""
    expected = {
        (site["code"], pd.Timestamp(run_date) + pd.Timedelta(seconds=step))
        for site in SC.SITES for step in SC.AROME_IFS_STEPS_S
    }
    actual = set(map(tuple, candidate[["site", "valid_time"]]
                           .drop_duplicates().itertuples(index=False, name=None)))
    if actual != expected or len(candidate) != len(expected):
        raise ValueError("Run AROME-IFS incomplet : site/échéance manquant.")
    if candidate[["precip", "neige_eau", "pluie_eau", "t2m"]] \
            .isna().any(axis=None):
        raise ValueError("Run AROME-IFS incomplet : valeur météorologique manquante.")


def is_complete_in_store(existing, run_date):
    sub = existing[(existing["model"] == MODEL)
                   & (pd.to_datetime(existing["run_date"]) == pd.Timestamp(run_date))]
    try:
        validate_complete(sub, run_date)
    except ValueError:
        return False
    return True


def fetch_candidate(session, key, run_date, coverages, sleep_fn=time.sleep):
    """Télécharge 3 produits × 45 heures, chaque GRIB servant deux sites."""
    points = {}
    targets = {site["code"]: (site["lat"], site["lon"]) for site in SC.SITES}
    latitudes = [site["lat"] for site in SC.SITES]
    longitudes = [site["lon"] for site in SC.SITES]
    common_subsets = (
        f"lat({min(latitudes) - 0.02},{max(latitudes) + 0.02})",
        f"long({min(longitudes) - 0.02},{max(longitudes) + 0.02})",
    )
    requests_left = len(SC.AROME_IFS_STEPS_S) * len(coverages)
    for step_s in SC.AROME_IFS_STEPS_S:
        for column, coverage_id in coverages.items():
            subsets = common_subsets + (("height(2)",) if column == "t2m" else ())
            payload = WCS.get_coverage(
                session, _url("GetCoverage"), key=key,
                coverage_id=coverage_id, time_value=step_s,
                subsets=subsets, timeout=SC.HTTP_TIMEOUT)
            decoded = WCS.decode_nearest_points(payload, targets)
            for site_code, point in decoded.items():
                points[(step_s, column, site_code)] = point
            requests_left -= 1
            if requests_left:
                sleep_fn(SC.AROME_IFS_REQUEST_INTERVAL_S)
    return candidate_from_points(run_date, points)


def main():
    key = WCS.load_api_key(
        SC.MF_LOCAL_API_KEY_ENVS[MODEL], os.path.join(_ROOT, ".env"))
    session = requests.Session()
    run_date, coverages = discover_cycle(session, key)
    existing = ER.load_existing(SC.DB_MF_LOCAL_PATH, SC.MF_LOCAL_SCHEMA)
    if is_complete_in_store(existing, run_date):
        MS.persist_summary(existing[(existing["model"] == MODEL)
                                    & (pd.to_datetime(existing["run_date"])
                                       == pd.Timestamp(run_date))])
        print(f"ℹ️  AROME-IFS {run_date:%d %b %HZ} déjà complet "
              "— aucun appel GRIB.")
        return

    print(f"⏳ AROME-IFS {run_date:%d %b %HZ} — H+1 à H+45, "
          "village + sommet…")
    candidate = fetch_candidate(session, key, run_date, coverages)
    combined = ER.persist(
        candidate, SC.DB_MF_LOCAL_PATH, schema=SC.MF_LOCAL_SCHEMA,
        var_cols=SC.MF_LOCAL_VAR_COLS,
        sort_cols=["run_date", "model", "kind", "member", "site", "valid_time"],
        max_gap_h=1, existing=existing)
    MS.persist_summary(combined[(combined["model"] == MODEL)
                                & (pd.to_datetime(combined["run_date"])
                                   == pd.Timestamp(run_date))])
    n_runs = combined[["run_date", "model"]].drop_duplicates().shape[0]
    print(f"✅ Base Météo-France locale : {len(combined):,} lignes · "
          f"{n_runs} run(s) archivés")
    print(f"   → {SC.DB_MF_LOCAL_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline AROME-IFS : {exc}")
