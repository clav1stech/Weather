# -*- coding: utf-8 -*-
"""Tests du collecteur AROME-PI, sans réseau ni parquet réel."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from apps.snow import snow_config as SC
from apps.snow.pipeline import fetch_arome_pi as PI
from core.services.meteofrance_wcs import GribPoint


RUN = datetime(2026, 1, 10, 13)


def _point(value, step_s, short_name, units):
    return GribPoint(
        value=value, latitude=45.85, longitude=6.62,
        short_name=short_name, units=units, run_date=RUN,
        valid_time=RUN + timedelta(seconds=step_s),
        step_range=str(step_s // 3600),
    )


def _complete_points():
    points = {}
    for step in SC.AROME_PI_STEPS_S:
        for site in SC.SITES:
            code = site["code"]
            points[(step, "precip", code)] = _point(2.0, step, "tp", "kg m-2")
            points[(step, "neige_eau", code)] = _point(
                1.25 if code == "sommet" else 0.25, step, "unknown", "kg m-2")
            points[(step, "ptype", code)] = _point(
                5 if code == "sommet" else 1, step, "ptype", "Numeric")
            points[(step, "t2m", code)] = _point(
                271.15 if code == "sommet" else 275.15, step, "2t", "K")
    return points


def test_candidate_keeps_six_hours_and_both_sites():
    candidate = PI.candidate_from_points(RUN, _complete_points())

    assert len(candidate) == len(SC.AROME_PI_STEPS_S) * len(SC.SITES)
    assert set(candidate["site"]) == {"village", "sommet"}
    assert set(candidate["period_h"]) == {1}
    village = candidate[candidate["site"] == "village"].iloc[0]
    sommet = candidate[candidate["site"] == "sommet"].iloc[0]
    assert village["t2m"] == pytest.approx(2.0)
    assert village["ptype"] == 1
    assert village["pluie_eau"] == pytest.approx(1.75)
    assert sommet["t2m"] == pytest.approx(-2.0)
    assert sommet["ptype"] == 5
    assert sommet["neige_eau"] == pytest.approx(1.25)


def test_ptype_9999_remains_missing_not_dry():
    points = _complete_points()
    step = SC.AROME_PI_STEPS_S[0]
    points[(step, "ptype", "village")] = _point(9999, step, "ptype", "Numeric")
    candidate = PI.candidate_from_points(RUN, points)
    row = candidate[(candidate["site"] == "village")
                    & (candidate["valid_time"] == RUN + timedelta(seconds=step))]
    assert np.isnan(row.iloc[0]["ptype"])


def test_complete_cycle_is_noop_but_missing_site_is_not():
    candidate = PI.candidate_from_points(RUN, _complete_points())
    assert PI.is_complete_in_store(candidate, RUN)
    assert not PI.is_complete_in_store(candidate.iloc[:-1], RUN)


def test_candidate_rejects_unknown_temperature_unit():
    points = _complete_points()
    step = SC.AROME_PI_STEPS_S[0]
    points[(step, "t2m", "village")] = _point(42, step, "2t", "mystery")
    with pytest.raises(ValueError, match="Unité de température"):
        PI.candidate_from_points(RUN, points)


def test_discover_cycle_retries_catalog_temporarily_empty(monkeypatch):
    empty = b"<Capabilities/>"
    ids = [
        f"{product}___{RUN:%Y-%m-%dT%H.00.00Z}"
        + (f"_{period}" if period else "")
        for product, period in SC.AROME_PI_PRODUCTS.values()
    ]
    complete = ("<wcs:Capabilities "
                "xmlns:wcs='http://www.opengis.net/wcs/2.0'>"
                + "".join(
                    f"<wcs:CoverageId>{item}</wcs:CoverageId>" for item in ids)
                + "</wcs:Capabilities>").encode()
    responses = iter((empty, complete))
    sleeps = []
    monkeypatch.setattr(
        PI.WCS, "get_capabilities", lambda *args, **kwargs: next(responses))

    run, selected = PI.discover_cycle(
        object(), "secret", attempts=2, sleep_fn=sleeps.append)

    assert run == RUN
    assert set(selected) == set(SC.AROME_PI_PRODUCTS)
    assert sleeps == [SC.AROME_PI_CATALOG_RETRY_S]
