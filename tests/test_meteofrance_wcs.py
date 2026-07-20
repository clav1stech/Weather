# -*- coding: utf-8 -*-
"""Tests purs du client WCS Météo-France (aucun appel réseau)."""

from datetime import datetime

import pytest
import requests

from core.services import meteofrance_wcs as WCS


class _Response:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class _Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_load_api_key_uses_exact_name_without_generic_fallback(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "METEOFRANCE_API_KEY=generique\n"
        "METEOFRANCE_AROME_PE_KEY=dediee\n", encoding="utf-8")
    monkeypatch.delenv("METEOFRANCE_AROME_PE_KEY", raising=False)

    assert WCS.load_api_key("METEOFRANCE_AROME_PE_KEY", dotenv) == "dediee"
    with pytest.raises(SystemExit, match="METEOFRANCE_AROME_IFS_KEY"):
        WCS.load_api_key("METEOFRANCE_AROME_IFS_KEY", dotenv)


def test_catalogue_selects_latest_exact_product_and_period():
    ids = [
        "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___2026-07-20T03.00.00Z_PT1H",
        "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___2026-07-20T03.00.00Z_P1D",
        "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___2026-07-20T09.00.00Z_P1D",
        "TOTAL_SNOW_PRECIPITATION__GROUND_OR_WATER_SURFACE___2026-07-20T09.00.00Z_P1D",
    ]
    xml = ("<wcs:Capabilities xmlns:wcs='http://www.opengis.net/wcs/2.0'>"
           + "".join(f"<wcs:CoverageId>{item}</wcs:CoverageId>" for item in ids)
           + "</wcs:Capabilities>")

    parsed = WCS.coverage_ids(xml.encode())
    selected = WCS.latest_coverage(
        parsed, "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE", "P1D")

    assert selected == ids[2]
    assert WCS.coverage_run_date(selected) == datetime(2026, 7, 20, 9)
    assert WCS.latest_coverage(parsed, "INCONNU", "P1D") is None


def test_latest_coverage_can_restrict_complete_run_hours():
    product = "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE"
    ids = [
        f"{product}___2026-07-20T00.00.00Z_P1D",
        f"{product}___2026-07-20T06.00.00Z_P1D",
        f"{product}___2026-07-19T12.00.00Z_P1D",
    ]
    selected = WCS.latest_coverage(
        ids, product, "P1D", allowed_run_hours=(0, 12))
    assert WCS.coverage_run_date(selected) == datetime(2026, 7, 20, 0)


def test_get_capabilities_retries_429_without_leaking_key():
    sleeps = []
    session = _Session(
        _Response(429, headers={"Retry-After": "2"}),
        _Response(200, b"<Capabilities/>")
    )

    payload = WCS.get_capabilities(
        session, "https://example.test/GetCapabilities", key="secret",
        attempts=2, sleep_fn=sleeps.append)

    assert payload == b"<Capabilities/>"
    assert sleeps == [2.0]
    assert len(session.calls) == 2
    assert session.calls[0][1]["headers"] == {"apikey": "secret"}


def test_get_coverage_requires_time_and_rejects_xml_error():
    with pytest.raises(ValueError, match="time"):
        WCS.get_coverage(
            _Session(), "https://example.test/GetCoverage", key="secret",
            coverage_id="X", time_value=None)

    session = _Session(_Response(
        200, b"<?xml version='1.0'?><Exception/>",
        {"Content-Type": "application/xml"}))
    with pytest.raises(ValueError, match="non GRIB"):
        WCS.get_coverage(
            session, "https://example.test/GetCoverage", key="secret",
            coverage_id="X", time_value=3600, attempts=1)


def _tiny_grib():
    eccodes = pytest.importorskip("eccodes")
    grib = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        for key, value in {
            "Ni": 2, "Nj": 2,
            "latitudeOfFirstGridPointInDegrees": 46.0,
            "longitudeOfFirstGridPointInDegrees": 6.5,
            "latitudeOfLastGridPointInDegrees": 45.5,
            "longitudeOfLastGridPointInDegrees": 7.0,
            "iDirectionIncrementInDegrees": 0.5,
            "jDirectionIncrementInDegrees": 0.5,
            "dataDate": 20260720, "dataTime": 900,
            "forecastTime": 1, "stepUnits": 1, "paramId": 228,
        }.items():
            eccodes.codes_set(grib, key, value)
        eccodes.codes_set_values(grib, [1.0, 2.0, 3.0, 4.0])
        return eccodes.codes_get_message(grib)
    finally:
        eccodes.codes_release(grib)


def test_decode_nearest_point_reads_metadata_without_disk_artifact(tmp_path):
    point = WCS.decode_nearest_point(
        _tiny_grib(), target_lat=45.86, target_lon=6.62)

    assert point.run_date == datetime(2026, 7, 20, 9)
    assert point.valid_time == datetime(2026, 7, 20, 10)
    assert point.short_name == "tp"
    assert point.value == pytest.approx(1.0)
    assert list(tmp_path.iterdir()) == []


def test_decode_nearest_points_reuses_one_grid_for_two_sites():
    points = WCS.decode_nearest_points(
        _tiny_grib(), {"north_west": (45.86, 6.62),
                       "south_east": (45.51, 6.99)})

    assert points["north_west"].value == pytest.approx(1.0)
    assert points["south_east"].value == pytest.approx(4.0)
