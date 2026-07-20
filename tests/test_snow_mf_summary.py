# -*- coding: utf-8 -*-
"""Tests purs de l'archive moyenne Météo-France ciblée."""

import numpy as np
import pandas as pd
import pytest

from apps.snow import snow_config as SC
from apps.snow.pipeline import mf_summary as MS


RUN = pd.Timestamp("2026-01-10 00:00")


def _raw_members():
    rows = []
    for member, precip, snow, ptype in (
            (0, 2.0, 1.0, 5), (1, 4.0, 3.0, 5), (2, 6.0, 2.0, 1)):
        row = {column: np.nan for column in SC.MF_LOCAL_SCHEMA}
        row.update({
            "run_date": RUN, "model": SC.PE_AROME_MODEL, "kind": "member",
            "member": member, "site": "village",
            "valid_time": RUN + pd.Timedelta(hours=24), "period_h": 24,
            "cell_lat": 45.85, "cell_lon": 6.62,
            "precip": precip, "neige_eau": snow,
            "pluie_eau": precip - snow, "ptype": ptype,
        })
        rows.append(row)
    return pd.DataFrame(rows)[SC.MF_LOCAL_SCHEMA]


def test_summary_uses_means_member_count_and_ptype_mode():
    summary = MS.summarize_rows(_raw_members())
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["n_members"] == 3
    assert row["precip"] == pytest.approx(4.0)
    assert row["neige_eau"] == pytest.approx(2.0)
    assert row["pluie_eau"] == pytest.approx(2.0)
    assert row["ptype"] == 5
    assert pd.isna(row["t850"])


def test_identical_summary_is_an_explicit_noop_without_write(monkeypatch):
    candidate = MS.summarize_rows(_raw_members())
    monkeypatch.setattr(MS.ER, "load_existing", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(
        MS.ER, "persist",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("persist ne doit pas être appelé")))

    combined, wrote = MS.persist_summary(_raw_members(), "unused.parquet")
    assert not wrote
    assert combined.equals(candidate)


def test_summary_rejects_incomplete_old_schema_explicitly():
    with pytest.raises(ValueError, match="Colonnes brutes absentes"):
        MS.summarize_rows(_raw_members().drop(columns=["neige_eau"]))
