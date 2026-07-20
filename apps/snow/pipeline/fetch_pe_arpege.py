# -*- coding: utf-8 -*-
"""Collecte PE-ARPEGE Europe 0,1° au village — cumuls 24 h par membre.

Le catalogue publie des cycles 00/06/12/18Z, mais seuls 00/12Z sont retenus :
ils portent les cycles complets validés pour cet archivage. Pour chacun des 35
membres, le collecteur extrait le total et la neige des fenêtres P1D finissant
à H+24, H+48, H+72 et H+96. Les diagnostics de masse d'air restent fournis
par les ensembles globaux déjà collectés ; les dupliquer ici multiplierait les
requêtes WCS sans améliorer la classification locale.

Le WCS PE-ARPEGE interdit officiellement tout sous-ensemble ``lat/long`` :
chaque champ Europe 0,1° complet est donc décodé en mémoire, la cellule la
plus proche de Megève est extraite, puis le GRIB est immédiatement libéré sans
jamais être archivé. Un cycle incomplet ne modifie jamais la base.
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


MODEL = SC.PE_ARPEGE_MODEL
SITE = SC.SITE_BY_CODE["village"]
_MM_UNITS = {"kg m-2", "kg m**-2", "kg m^-2", "mm"}


def _url(member, operation):
    return SC.PE_ARPEGE_WCS_URL_TPL.format(
        member=int(member), operation=operation)


def _coverages_by_run(ids, product):
    """Indexe les couvertures P1D 00/12Z exactes par cycle."""
    found = {}
    prefix = f"{product}___"
    for coverage_id in ids:
        if not coverage_id.startswith(prefix) or not coverage_id.endswith("_P1D"):
            continue
        try:
            run_date = WCS.coverage_run_date(coverage_id)
        except ValueError:
            continue
        if run_date.hour in SC.PE_ARPEGE_ALLOWED_RUN_HOURS:
            found[run_date] = coverage_id
    return found


def discover_cycle(session, key):
    """Dernier cycle 00/12Z commun au contrôle et aux perturbations.

    Le membre 0 est fréquemment publié avant le reste de l'ensemble. Le
    membre 1 sert de sentinelle : partir sur le seul contrôle provoquerait un
    GetCoverage 400 dès la première perturbation encore sur l'ancien cycle.
    """
    catalogs = {}
    for member in SC.PE_ARPEGE_DISCOVERY_MEMBERS:
        payload = WCS.get_capabilities(
            session, _url(member, "GetCapabilities"), key=key,
            timeout=SC.HTTP_TIMEOUT)
        ids = WCS.coverage_ids(payload)
        catalogs[member] = {
            column: _coverages_by_run(ids, product)
            for column, product in SC.PE_ARPEGE_PRODUCTS.items()
        }
    run_sets = [
        set(by_run)
        for products in catalogs.values() for by_run in products.values()
    ]
    common_runs = set.intersection(*run_sets) if run_sets else set()
    if not common_runs:
        raise RuntimeError(
            "Aucun cycle PE-ARPEGE P1D 00/12Z commun au contrôle et aux "
            "perturbations.")
    run_date = max(common_runs)
    control = SC.PE_ARPEGE_DISCOVERY_MEMBERS[0]
    selected = {
        column: catalogs[control][column][run_date]
        for column in SC.PE_ARPEGE_PRODUCTS
    }
    return run_date, selected


def _precip_mm(point):
    units = point.units.strip().lower()
    if units not in _MM_UNITS:
        raise ValueError(
            f"Unité PE-ARPEGE inattendue pour {point.short_name}: {point.units}")
    value = float(point.value)
    if not np.isfinite(value):
        raise ValueError("Valeur PE-ARPEGE non finie.")
    if value < -0.01:
        raise ValueError(f"Cumul PE-ARPEGE négatif incohérent : {value}")
    return max(value, 0.0)


def candidate_from_points(run_date, points):
    """Normalise 35 membres × quatre fenêtres depuis les points décodés."""
    run_date = pd.Timestamp(run_date).to_pydatetime().replace(tzinfo=None)
    if run_date.hour not in SC.PE_ARPEGE_ALLOWED_RUN_HOURS:
        raise ValueError("Un cycle PE-ARPEGE 06/18Z ne doit jamais être archivé.")
    rows = []
    for member in range(SC.PE_ARPEGE_MEMBER_COUNT):
        for step_s in SC.PE_ARPEGE_DAILY_STEPS_S:
            total_point = points[(member, step_s, "precip")]
            snow_point = points[(member, step_s, "neige_eau")]
            expected_valid = run_date + dt.timedelta(seconds=int(step_s))
            for point in (total_point, snow_point):
                if point.run_date != run_date or point.valid_time != expected_valid:
                    raise ValueError(
                        "Cycle/échéance du GRIB PE-ARPEGE incohérent.")
            precip = _precip_mm(total_point)
            snow = _precip_mm(snow_point)
            if snow > precip + 0.05:
                raise ValueError(
                    f"Neige PE-ARPEGE ({snow:.3f}) supérieure au total "
                    f"({precip:.3f}) au-delà de la tolérance.")
            snow = min(snow, precip)
            row = {column: np.nan for column in SC.MF_REGIONAL_SCHEMA}
            row.update({
                "run_date": run_date, "model": MODEL, "kind": "member",
                "member": member, "site": "village",
                "valid_time": expected_valid, "period_h": 24,
                "cell_lat": total_point.latitude,
                "cell_lon": total_point.longitude,
                "precip": precip, "neige_eau": snow,
                "pluie_eau": max(precip - snow, 0.0),
            })
            rows.append(row)
    candidate = pd.DataFrame(rows)[SC.MF_REGIONAL_SCHEMA]
    validate_complete(candidate, run_date)
    return candidate


def validate_complete(candidate, run_date):
    """Exige exactement 35 membres × quatre fenêtres et aucun cumul NaN."""
    expected = {
        (member, pd.Timestamp(run_date) + pd.Timedelta(seconds=step))
        for member in range(SC.PE_ARPEGE_MEMBER_COUNT)
        for step in SC.PE_ARPEGE_DAILY_STEPS_S
    }
    actual = set(map(tuple, candidate[["member", "valid_time"]]
                           .drop_duplicates().itertuples(index=False, name=None)))
    if actual != expected or len(candidate) != len(expected):
        raise ValueError("Run PE-ARPEGE incomplet : membres/échéances manquants.")
    if candidate[["precip", "neige_eau", "pluie_eau"]].isna().any(axis=None):
        raise ValueError("Run PE-ARPEGE incomplet : cumul manquant.")


def is_complete_in_store(existing, run_date):
    sub = existing[(existing["model"] == MODEL)
                   & (pd.to_datetime(existing["run_date"]) == pd.Timestamp(run_date))]
    try:
        validate_complete(sub, run_date)
    except ValueError:
        return False
    return True


def fetch_candidate(session, key, run_date, coverages, sleep_fn=time.sleep):
    """Télécharge 280 grilles Europe, sans sous-ensemble spatial interdit."""
    points = {}
    requests_left = (SC.PE_ARPEGE_MEMBER_COUNT
                     * len(SC.PE_ARPEGE_DAILY_STEPS_S)
                     * len(coverages))
    for member in range(SC.PE_ARPEGE_MEMBER_COUNT):
        for step_s in SC.PE_ARPEGE_DAILY_STEPS_S:
            for column, coverage_id in coverages.items():
                try:
                    payload = WCS.get_coverage(
                        session, _url(member, "GetCoverage"), key=key,
                        coverage_id=coverage_id, time_value=step_s,
                        subsets=(), timeout=SC.HTTP_TIMEOUT)
                except RuntimeError as exc:
                    raise RuntimeError(
                        f"PE-ARPEGE membre {member:02d}, {column}, "
                        f"H+{step_s // 3600} : {exc}") from exc
                points[(member, step_s, column)] = WCS.decode_nearest_point(
                    payload, SITE["lat"], SITE["lon"])
                requests_left -= 1
                if requests_left:
                    sleep_fn(SC.PE_ARPEGE_REQUEST_INTERVAL_S)
    return candidate_from_points(run_date, points)


def main():
    key = WCS.load_api_key(
        SC.PE_ARPEGE_API_KEY_ENV, os.path.join(_ROOT, ".env"))
    session = requests.Session()
    run_date, coverages = discover_cycle(session, key)
    existing = ER.load_existing(
        SC.DB_MF_REGIONAL_PATH, SC.MF_REGIONAL_SCHEMA)
    if is_complete_in_store(existing, run_date):
        MS.persist_summary(existing[(existing["model"] == MODEL)
                                    & (pd.to_datetime(existing["run_date"])
                                       == pd.Timestamp(run_date))])
        print(f"ℹ️  PE-ARPEGE {run_date:%d %b %HZ} déjà complet — aucun appel GRIB.")
        return

    print(f"⏳ PE-ARPEGE {run_date:%d %b %HZ} — "
          f"{SC.PE_ARPEGE_MEMBER_COUNT} membres, cumuls H+24/48/72/96…")
    candidate = fetch_candidate(session, key, run_date, coverages)
    combined = ER.persist(
        candidate, SC.DB_MF_REGIONAL_PATH, schema=SC.MF_REGIONAL_SCHEMA,
        var_cols=SC.MF_REGIONAL_VAR_COLS,
        sort_cols=["run_date", "model", "kind", "member", "site", "valid_time"],
        max_gap_h=24, existing=existing)
    MS.persist_summary(combined[(combined["model"] == MODEL)
                                & (pd.to_datetime(combined["run_date"])
                                   == pd.Timestamp(run_date))])
    n_runs = combined[["run_date", "model"]].drop_duplicates().shape[0]
    print(f"✅ Base PE-ARPEGE : {len(combined):,} lignes · "
          f"{n_runs} run(s) archivés")
    print(f"   → {SC.DB_MF_REGIONAL_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline PE-ARPEGE : {exc}")
