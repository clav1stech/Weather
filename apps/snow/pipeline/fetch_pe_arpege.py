# -*- coding: utf-8 -*-
"""Collecte PE-ARPEGE au village — quatre cumuls 24 h par membre.

Le catalogue publie des cycles 00/06/12/18Z, mais seuls 00/12Z sont retenus :
ils portent les cycles complets validés pour cet archivage. Pour chacun des 35
membres, le collecteur extrait le total et la neige des fenêtres P1D finissant
à H+24, H+48, H+72 et H+96. Les diagnostics de masse d'air restent fournis
par les ensembles globaux déjà collectés ; les dupliquer ici multiplierait les
requêtes WCS sans améliorer la classification locale.

Les GRIB ne sont jamais archivés. ecCodes extrait directement la cellule la
plus proche de Megève village, puis seules les valeurs normalisées rejoignent
le parquet PE-ARPEGE dédié. Un cycle incomplet ne modifie jamais la base.
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
_BBOX_DEG = 0.30
_MM_UNITS = {"kg m-2", "kg m**-2", "kg m^-2", "mm"}


def _url(member, operation):
    return SC.PE_ARPEGE_WCS_URL_TPL.format(
        member=int(member), operation=operation)


def discover_cycle(session, key):
    """Dernier cycle 00/12Z portant ensemble total et neige P1D."""
    payload = WCS.get_capabilities(
        session, _url(0, "GetCapabilities"), key=key,
        timeout=SC.HTTP_TIMEOUT)
    ids = WCS.coverage_ids(payload)
    selected = {
        column: WCS.latest_coverage(
            ids, product, "P1D",
            allowed_run_hours=SC.PE_ARPEGE_ALLOWED_RUN_HOURS)
        for column, product in SC.PE_ARPEGE_PRODUCTS.items()
    }
    missing = [column for column, coverage in selected.items() if coverage is None]
    if missing:
        raise RuntimeError(
            "Coverage PE-ARPEGE P1D 00/12Z absent pour : " + ", ".join(missing))
    runs = {WCS.coverage_run_date(coverage) for coverage in selected.values()}
    if len(runs) != 1:
        raise RuntimeError("Les produits PE-ARPEGE ne portent pas le même cycle.")
    run_date = runs.pop()
    if run_date.hour not in SC.PE_ARPEGE_ALLOWED_RUN_HOURS:
        raise RuntimeError("Le catalogue PE-ARPEGE sélectionné n'est pas 00/12Z.")
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
    """Télécharge les 280 champs espacés sous le quota du service."""
    points = {}
    requests_left = (SC.PE_ARPEGE_MEMBER_COUNT
                     * len(SC.PE_ARPEGE_DAILY_STEPS_S)
                     * len(coverages))
    subsets = (
        f"lat({SITE['lat'] - _BBOX_DEG},{SITE['lat'] + _BBOX_DEG})",
        f"long({SITE['lon'] - _BBOX_DEG},{SITE['lon'] + _BBOX_DEG})",
    )
    for member in range(SC.PE_ARPEGE_MEMBER_COUNT):
        for step_s in SC.PE_ARPEGE_DAILY_STEPS_S:
            for column, coverage_id in coverages.items():
                payload = WCS.get_coverage(
                    session, _url(member, "GetCoverage"), key=key,
                    coverage_id=coverage_id, time_value=step_s,
                    subsets=subsets, timeout=SC.HTTP_TIMEOUT)
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
