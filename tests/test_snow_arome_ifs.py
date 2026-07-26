# -*- coding: utf-8 -*-
"""Tests du collecteur AROME-IFS, sans réseau ni parquet réel."""

from datetime import datetime, timedelta

import pytest

from apps.snow import snow_config as SC
from apps.snow.pipeline import fetch_arome_ifs as IFS
from core.services.meteofrance_wcs import GribPoint


RUN = datetime(2026, 1, 10, 6)


def _point(value, step_s, short_name, units):
    return GribPoint(
        value=value, latitude=45.85, longitude=6.62,
        short_name=short_name, units=units, run_date=RUN,
        valid_time=RUN + timedelta(seconds=step_s),
        step_range=str(step_s // 3600),
    )


def _complete_points():
    points = {}
    for step in SC.AROME_IFS_STEPS_S:
        for site in SC.SITES:
            code = site["code"]
            points[(step, "precip", code)] = _point(
                2.0, step, "tp", "kg m-2")
            points[(step, "neige_eau", code)] = _point(
                1.5 if code == "sommet" else 0.25,
                step, "unknown", "kg m-2")
            points[(step, "t2m", code)] = _point(
                271.15 if code == "sommet" else 275.15,
                step, "2t", "K")
    return points


def test_candidate_keeps_45_hours_and_both_sites():
    candidate = IFS.candidate_from_points(RUN, _complete_points())

    assert len(candidate) == SC.AROME_IFS_HORIZON_H * len(SC.SITES)
    assert set(candidate["site"]) == {"village", "sommet"}
    assert set(candidate["period_h"]) == {1}
    assert candidate["ptype"].isna().all()
    village = candidate[candidate["site"] == "village"].iloc[0]
    sommet = candidate[candidate["site"] == "sommet"].iloc[0]
    assert village["t2m"] == pytest.approx(2.0)
    assert village["pluie_eau"] == pytest.approx(1.75)
    assert sommet["t2m"] == pytest.approx(-2.0)
    assert sommet["neige_eau"] == pytest.approx(1.5)


def test_complete_cycle_is_noop_but_truncated_cycle_is_not():
    candidate = IFS.candidate_from_points(RUN, _complete_points())
    assert IFS.is_complete_in_store(candidate, RUN)
    assert not IFS.is_complete_in_store(candidate.iloc[:-1], RUN)


def test_candidate_rejects_snow_above_total_beyond_tolerance():
    points = _complete_points()
    step = SC.AROME_IFS_STEPS_S[0]
    points[(step, "neige_eau", "sommet")] = _point(
        2.2, step, "unknown", "kg m-2")
    with pytest.raises(ValueError, match="supérieure au total"):
        IFS.candidate_from_points(RUN, points)
