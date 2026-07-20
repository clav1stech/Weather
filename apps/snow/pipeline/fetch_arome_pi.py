# -*- coding: utf-8 -*-
"""Collecte AROME-PI — six prochaines heures aux deux sites de Megève.

AROME-PI est recalculé chaque heure, mais le job neige ne tourne que toutes les
deux heures : chaque poll prend le dernier catalogue publié et conserve H+1 à
H+6 au pas horaire. Quatre champs suffisent : cumul total et neige sur l'heure
précédente, type de précipitation diagnostiqué et température à 2 m.

La petite emprise WCS contient village et sommet. Chaque GRIB est téléchargé
une fois puis décodé pour les deux cellules, soit 24 requêtes par nouveau run.
Un run incomplet n'est jamais persisté ; le parquet commun PNT local conserve
les autres modèles grâce à la fusion par ``(run_date, model)``.
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


MODEL = SC.AROME_PI_MODEL
_MM_UNITS = {"kg m-2", "kg m**-2", "kg m^-2", "mm"}
_KELVIN_UNITS = {"k", "kelvin"}
_CELSIUS_UNITS = {"c", "degc", "°c", "degree celsius"}
_PTYPE_MISSING = 9999


def _url(operation):
    return SC.AROME_PI_WCS_URL.format(operation=operation)


def discover_cycle(session, key, *, attempts=None, sleep_fn=time.sleep):
    """Dernier cycle PI co-publié, avec retry de la rotation du catalogue.

    GetCapabilities peut répondre HTTP 200 pendant quelques secondes avec un
    contenu vide ou partiel lors du remplacement horaire. Ce cas sémantique
    doit être retenté comme une indisponibilité transitoire, sans persistance.
    """
    attempts = attempts or SC.AROME_PI_CATALOG_ATTEMPTS
    last_error = None
    for attempt in range(attempts):
        payload = WCS.get_capabilities(
            session, _url("GetCapabilities"), key=key, timeout=SC.HTTP_TIMEOUT)
        ids = WCS.coverage_ids(payload)
        selected = {
            column: WCS.latest_coverage(ids, product, period)
            for column, (product, period) in SC.AROME_PI_PRODUCTS.items()
        }
        missing = [
            column for column, coverage in selected.items() if coverage is None]
        if missing:
            last_error = "Coverage absent pour : " + ", ".join(missing)
        else:
            runs = {
                WCS.coverage_run_date(coverage) for coverage in selected.values()}
            if len(runs) == 1:
                return runs.pop(), selected
            last_error = "produits publiés sur des cycles différents"
        if attempt + 1 < attempts:
            sleep_fn(SC.AROME_PI_CATALOG_RETRY_S)
    raise RuntimeError(
        f"Catalogue AROME-PI incomplet après {attempts} tentatives : "
        f"{last_error}.")


def _precip_mm(point):
    units = point.units.strip().lower()
    if units not in _MM_UNITS:
        raise ValueError(f"Unité de précipitation PI inattendue : {point.units}")
    value = float(point.value)
    if not np.isfinite(value) or value < -0.01:
        raise ValueError(f"Cumul AROME-PI incohérent : {value}")
    return max(value, 0.0)


def _temperature_c(point):
    units = point.units.strip().lower()
    value = float(point.value)
    if not np.isfinite(value):
        raise ValueError("Température AROME-PI non finie.")
    if units in _KELVIN_UNITS:
        return value - 273.15
    if units in _CELSIUS_UNITS:
        return value
    raise ValueError(f"Unité de température PI inattendue : {point.units}")


def _ptype(point):
    value = float(point.value)
    if not np.isfinite(value) or int(round(value)) == _PTYPE_MISSING:
        return np.nan
    return int(round(value))


def candidate_from_points(run_date, points):
    """Normalise les points ``(step_s, produit, site)`` au schéma local."""
    run_date = pd.Timestamp(run_date).to_pydatetime().replace(tzinfo=None)
    rows = []
    for step_s in SC.AROME_PI_STEPS_S:
        expected_valid = run_date + dt.timedelta(seconds=int(step_s))
        for site in SC.SITES:
            site_code = site["code"]
            values = {
                column: points[(step_s, column, site_code)]
                for column in SC.AROME_PI_PRODUCTS
            }
            for point in values.values():
                if point.run_date != run_date or point.valid_time != expected_valid:
                    raise ValueError(
                        "Cycle/échéance du GRIB AROME-PI incohérent.")
            precip = _precip_mm(values["precip"])
            snow = _precip_mm(values["neige_eau"])
            if snow > precip + 0.05:
                raise ValueError(
                    f"Neige AROME-PI ({snow:.3f}) supérieure au total "
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
                "ptype": _ptype(values["ptype"]),
                "t2m": _temperature_c(values["t2m"]),
            })
            rows.append(row)
    candidate = pd.DataFrame(rows)[SC.MF_LOCAL_SCHEMA]
    validate_complete(candidate, run_date)
    return candidate


def validate_complete(candidate, run_date):
    """Exige les 6 heures aux deux sites ; ptype seul peut être manquant."""
    expected = {
        (site["code"], pd.Timestamp(run_date) + pd.Timedelta(seconds=step))
        for site in SC.SITES for step in SC.AROME_PI_STEPS_S
    }
    actual = set(map(tuple, candidate[["site", "valid_time"]]
                           .drop_duplicates().itertuples(index=False, name=None)))
    if actual != expected or len(candidate) != len(expected):
        raise ValueError("Run AROME-PI incomplet : site/échéance manquant.")
    if candidate[["precip", "neige_eau", "pluie_eau", "t2m"]] \
            .isna().any(axis=None):
        raise ValueError("Run AROME-PI incomplet : valeur météorologique manquante.")


def is_complete_in_store(existing, run_date):
    sub = existing[(existing["model"] == MODEL)
                   & (pd.to_datetime(existing["run_date"]) == pd.Timestamp(run_date))]
    try:
        validate_complete(sub, run_date)
    except ValueError:
        return False
    return True


def fetch_candidate(session, key, run_date, coverages, sleep_fn=time.sleep):
    """Télécharge 4 produits × 6 heures ; chaque GRIB sert les deux sites."""
    points = {}
    targets = {site["code"]: (site["lat"], site["lon"]) for site in SC.SITES}
    latitudes = [site["lat"] for site in SC.SITES]
    longitudes = [site["lon"] for site in SC.SITES]
    common_subsets = (
        f"lat({min(latitudes) - 0.02},{max(latitudes) + 0.02})",
        f"long({min(longitudes) - 0.02},{max(longitudes) + 0.02})",
    )
    requests_left = len(SC.AROME_PI_STEPS_S) * len(coverages)
    for step_s in SC.AROME_PI_STEPS_S:
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
                sleep_fn(SC.AROME_PI_REQUEST_INTERVAL_S)
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
        print(f"ℹ️  AROME-PI {run_date:%d %b %HZ} déjà complet — aucun appel GRIB.")
        return

    print(f"⏳ AROME-PI {run_date:%d %b %HZ} — H+1 à H+6, village + sommet…")
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
        sys.exit(f"❌ Échec du pipeline AROME-PI : {exc}")
