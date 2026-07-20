# -*- coding: utf-8 -*-
"""Tests du collecteur PE-ARPEGE, sans réseau ni parquet réel."""

from datetime import datetime, timedelta

import pytest

from apps.snow import snow_config as SC
from apps.snow.pipeline import fetch_pe_arpege as PE
from core.services.meteofrance_wcs import GribPoint


RUN = datetime(2026, 1, 10, 0)


def _point(value, step_s, short_name="tp", units="kg m-2"):
    return GribPoint(
        value=value, latitude=45.75, longitude=6.50,
        short_name=short_name, units=units, run_date=RUN,
        valid_time=RUN + timedelta(seconds=step_s),
        step_range=str(step_s // 3600),
    )


def _complete_points(total=4.0, snow=1.5):
    points = {}
    for member in range(SC.PE_ARPEGE_MEMBER_COUNT):
        for step in SC.PE_ARPEGE_DAILY_STEPS_S:
            points[(member, step, "precip")] = _point(total + member / 10, step)
            points[(member, step, "neige_eau")] = _point(
                snow, step, short_name="unknown")
    return points


def test_candidate_preserves_35_members_and_four_daily_windows():
    candidate = PE.candidate_from_points(RUN, _complete_points())
    assert len(candidate) == SC.PE_ARPEGE_MEMBER_COUNT * 4
    assert set(candidate["member"]) == set(range(35))
    assert set(candidate["period_h"]) == {24}
    assert set((candidate["valid_time"] - candidate["run_date"])
               .dt.total_seconds()) == set(SC.PE_ARPEGE_DAILY_STEPS_S)
    first = candidate.iloc[0]
    assert first["precip"] == pytest.approx(4.0)
    assert first["neige_eau"] == pytest.approx(1.5)
    assert first["pluie_eau"] == pytest.approx(2.5)


def test_candidate_refuses_non_complete_06z_cycle():
    with pytest.raises(ValueError, match="06/18Z"):
        PE.candidate_from_points(
            RUN.replace(hour=6), _complete_points())


def test_complete_cycle_is_an_explicit_noop():
    complete = PE.candidate_from_points(RUN, _complete_points())
    assert PE.is_complete_in_store(complete, RUN)
    assert not PE.is_complete_in_store(complete.iloc[:-1], RUN)


def test_discover_cycle_ignores_newer_06z_catalog(monkeypatch):
    ids = []
    for product in SC.PE_ARPEGE_PRODUCTS.values():
        ids.extend([
            f"{product}___2026-01-10T00.00.00Z_P1D",
            f"{product}___2026-01-10T06.00.00Z_P1D",
        ])
    xml = ("<wcs:Capabilities xmlns:wcs='http://www.opengis.net/wcs/2.0'>"
           + "".join(f"<wcs:CoverageId>{item}</wcs:CoverageId>" for item in ids)
           + "</wcs:Capabilities>").encode()
    monkeypatch.setattr(PE.WCS, "get_capabilities", lambda *args, **kwargs: xml)

    run, selected = PE.discover_cycle(object(), "secret")

    assert run == RUN
    assert all("T00.00.00Z" in coverage for coverage in selected.values())
