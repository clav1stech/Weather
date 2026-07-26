# -*- coding: utf-8 -*-
"""Tests du collecteur PE-AROME, sans réseau ni parquet de production."""

from datetime import datetime, timedelta

import pytest

from apps.snow import snow_config as SC
from apps.snow.pipeline import fetch_pe_arome as PE
from core.services.meteofrance_wcs import GribPoint


RUN = datetime(2026, 1, 10, 3)


def _point(value, step_s, short_name="tp", units="kg m-2"):
    return GribPoint(
        value=value, latitude=45.85, longitude=6.62,
        short_name=short_name, units=units, run_date=RUN,
        valid_time=RUN + timedelta(seconds=step_s), step_range=str(step_s // 3600),
    )


def _complete_points(total=4.0, snow=1.5):
    points = {}
    for member in range(SC.PE_AROME_MEMBER_COUNT):
        for step in SC.PE_AROME_DAILY_STEPS_S:
            points[(member, step, "precip")] = _point(total + member / 10, step)
            points[(member, step, "neige_eau")] = _point(
                snow, step, short_name="unknown")
    return points


def test_candidate_preserves_each_member_and_derives_liquid_rain():
    candidate = PE.candidate_from_points(RUN, _complete_points())

    assert len(candidate) == SC.PE_AROME_MEMBER_COUNT * 2
    assert set(candidate["member"]) == set(range(SC.PE_AROME_MEMBER_COUNT))
    assert set(candidate["period_h"]) == {24}
    first = candidate.iloc[0]
    assert first["precip"] == pytest.approx(4.0)
    assert first["neige_eau"] == pytest.approx(1.5)
    assert first["pluie_eau"] == pytest.approx(2.5)
    assert first["site"] == "village"
    assert candidate["t850"].isna().all()


def test_candidate_rejects_snow_materially_above_total():
    points = _complete_points(total=1.0, snow=1.2)
    with pytest.raises(ValueError, match="supérieure au total"):
        PE.candidate_from_points(RUN, points)


def test_complete_cycle_is_an_explicit_noop():
    complete = PE.candidate_from_points(RUN, _complete_points())
    assert PE.is_complete_in_store(complete, RUN)
    assert not PE.is_complete_in_store(complete.iloc[:-1], RUN)


def test_discover_cycle_requires_total_and_snow_on_same_latest_run(monkeypatch):
    ids = [
        f"{SC.PE_AROME_PRODUCTS['precip']}___2026-01-10T03.00.00Z_P1D",
        f"{SC.PE_AROME_PRODUCTS['neige_eau']}___2026-01-10T03.00.00Z_P1D",
    ]
    xml = ("<wcs:Capabilities xmlns:wcs='http://www.opengis.net/wcs/2.0'>"
           + "".join(f"<wcs:CoverageId>{item}</wcs:CoverageId>" for item in ids)
           + "</wcs:Capabilities>").encode()
    monkeypatch.setattr(PE.WCS, "get_capabilities", lambda *args, **kwargs: xml)

    run, selected = PE.discover_cycle(object(), "secret")

    assert run == RUN
    assert selected == {"precip": ids[0], "neige_eau": ids[1]}
