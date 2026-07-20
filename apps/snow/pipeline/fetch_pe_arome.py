# -*- coding: utf-8 -*-
"""Collecte PE-AROME au village — cumuls 24 h par membre.

Le WCS Météo-France ne renvoie qu'un champ 2D par requête : une valeur de
``time`` est obligatoire. Reconstituer les 25 membres × 51 heures × plusieurs
paramètres serait coûteux et inutile puisque AROME-PI/IFS porteront le timing
horaire. Ce collecteur conserve donc deux fenêtres glissantes de 24 h, finissant
à H+24 et H+48, pour le total et la neige : 100 petits champs par cycle.

Les GRIB ne sont jamais archivés. ecCodes extrait directement la cellule la
plus proche de Megève village ; seules les valeurs normalisées en mm équivalent
eau rejoignent ``db_megeve_mf_local.parquet``. Un cycle doit être complet pour
être persisté. Au moindre échec, le parquet existant reste intact.
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


MODEL = SC.PE_AROME_MODEL
SITE = SC.SITE_BY_CODE["village"]
_BBOX_DEG = 0.04
_MM_UNITS = {"kg m-2", "kg m**-2", "kg m^-2", "mm"}


def _url(member, operation):
    return SC.PE_AROME_WCS_URL_TPL.format(
        member=int(member), operation=operation)


def discover_cycle(session, key):
    """Retourne ``(run_date, coverage total, coverage neige)`` du dernier run."""
    payload = WCS.get_capabilities(
        session, _url(0, "GetCapabilities"), key=key,
        timeout=SC.HTTP_TIMEOUT)
    ids = WCS.coverage_ids(payload)
    selected = {
        column: WCS.latest_coverage(ids, product, "P1D")
        for column, product in SC.PE_AROME_PRODUCTS.items()
    }
    missing = [column for column, coverage in selected.items() if coverage is None]
    if missing:
        raise RuntimeError(
            "Coverage PE-AROME P1D absent pour : " + ", ".join(missing))
    runs = {WCS.coverage_run_date(coverage) for coverage in selected.values()}
    if len(runs) != 1:
        raise RuntimeError("Les produits PE-AROME ne portent pas le même cycle.")
    return runs.pop(), selected


def _precip_mm(point):
    """Normalise un cumul en mm d'eau et refuse une unité inconnue."""
    units = point.units.strip().lower()
    if units not in _MM_UNITS:
        raise ValueError(
            f"Unité PE-AROME inattendue pour {point.short_name}: {point.units}")
    value = float(point.value)
    if not np.isfinite(value):
        raise ValueError("Valeur PE-AROME non finie.")
    if value < -0.01:
        raise ValueError(f"Cumul PE-AROME négatif incohérent : {value}")
    return max(value, 0.0)


def candidate_from_points(run_date, points):
    """Construit le run normalisé depuis les points décodés.

    ``points[(member, step_s, column)]`` contient un :class:`WCS.GribPoint`.
    La pluie liquide est ``total - neige`` bornée à zéro. Un très léger
    dépassement de la composante neige est toléré comme bruit d'arrondi ; un
    écart > 0,05 mm invalide le cycle plutôt que d'inventer une phase.
    """
    run_date = pd.Timestamp(run_date).to_pydatetime().replace(tzinfo=None)
    rows = []
    for member in range(SC.PE_AROME_MEMBER_COUNT):
        for step_s in SC.PE_AROME_DAILY_STEPS_S:
            total_point = points[(member, step_s, "precip")]
            snow_point = points[(member, step_s, "neige_eau")]
            for point in (total_point, snow_point):
                if point.run_date != run_date:
                    raise ValueError("Cycle du GRIB différent du catalogue PE-AROME.")
            if total_point.valid_time != snow_point.valid_time:
                raise ValueError("Total et neige PE-AROME n'ont pas la même échéance.")
            expected_valid = run_date + dt.timedelta(seconds=int(step_s))
            if total_point.valid_time != expected_valid:
                raise ValueError(
                    "Échéance GRIB différente du subset time demandé.")
            precip = _precip_mm(total_point)
            snow = _precip_mm(snow_point)
            if snow > precip + 0.05:
                raise ValueError(
                    f"Neige PE-AROME ({snow:.3f}) supérieure au total "
                    f"({precip:.3f}) au-delà de la tolérance.")
            snow = min(snow, precip)
            row = {column: np.nan for column in SC.MF_LOCAL_SCHEMA}
            row.update({
                "run_date": run_date,
                "model": MODEL,
                "kind": "member",
                "member": member,
                "site": "village",
                "valid_time": total_point.valid_time,
                "period_h": 24,
                "cell_lat": total_point.latitude,
                "cell_lon": total_point.longitude,
                "precip": precip,
                "neige_eau": snow,
                "pluie_eau": max(precip - snow, 0.0),
            })
            rows.append(row)
    candidate = pd.DataFrame(rows)[SC.MF_LOCAL_SCHEMA]
    validate_complete(candidate, run_date)
    return candidate


def validate_complete(candidate, run_date):
    """Exige exactement 25 membres × deux fenêtres, total et neige valides."""
    expected = {
        (member, pd.Timestamp(run_date) + pd.Timedelta(seconds=step))
        for member in range(SC.PE_AROME_MEMBER_COUNT)
        for step in SC.PE_AROME_DAILY_STEPS_S
    }
    actual = set(map(tuple, candidate[["member", "valid_time"]]
                           .drop_duplicates().itertuples(index=False, name=None)))
    if actual != expected or len(candidate) != len(expected):
        raise ValueError("Run PE-AROME incomplet : membres/échéances manquants.")
    if candidate[["precip", "neige_eau", "pluie_eau"]].isna().any(axis=None):
        raise ValueError("Run PE-AROME incomplet : cumul manquant.")


def is_complete_in_store(existing, run_date):
    """Le cycle exact est-il déjà complet ? Alors le poll est un no-op."""
    sub = existing[(existing["model"] == MODEL)
                   & (pd.to_datetime(existing["run_date"]) == pd.Timestamp(run_date))]
    try:
        validate_complete(sub, run_date)
    except ValueError:
        return False
    return True


def fetch_candidate(session, key, run_date, coverages, sleep_fn=time.sleep):
    """Télécharge les 100 champs du cycle avec espacement sous le quota."""
    points = {}
    requests_left = (SC.PE_AROME_MEMBER_COUNT
                     * len(SC.PE_AROME_DAILY_STEPS_S)
                     * len(coverages))
    subsets = (
        f"lat({SITE['lat'] - _BBOX_DEG},{SITE['lat'] + _BBOX_DEG})",
        f"long({SITE['lon'] - _BBOX_DEG},{SITE['lon'] + _BBOX_DEG})",
    )
    for member in range(SC.PE_AROME_MEMBER_COUNT):
        for step_s in SC.PE_AROME_DAILY_STEPS_S:
            for column, coverage_id in coverages.items():
                payload = WCS.get_coverage(
                    session, _url(member, "GetCoverage"), key=key,
                    coverage_id=coverage_id, time_value=step_s,
                    subsets=subsets, timeout=SC.HTTP_TIMEOUT)
                points[(member, step_s, column)] = WCS.decode_nearest_point(
                    payload, SITE["lat"], SITE["lon"])
                requests_left -= 1
                if requests_left:
                    sleep_fn(SC.PE_AROME_REQUEST_INTERVAL_S)
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
        print(f"ℹ️  PE-AROME {run_date:%d %b %HZ} déjà complet — aucun appel GRIB.")
        return

    print(f"⏳ PE-AROME {run_date:%d %b %HZ} — "
          f"{SC.PE_AROME_MEMBER_COUNT} membres, cumuls H+24/H+48…")
    candidate = fetch_candidate(session, key, run_date, coverages)
    combined = ER.persist(
        candidate, SC.DB_MF_LOCAL_PATH, schema=SC.MF_LOCAL_SCHEMA,
        var_cols=SC.MF_LOCAL_VAR_COLS,
        sort_cols=["run_date", "model", "kind", "member", "site", "valid_time"],
        max_gap_h=24, existing=existing)
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
        sys.exit(f"❌ Échec du pipeline PE-AROME : {exc}")
