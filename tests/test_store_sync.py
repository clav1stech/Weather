# -*- coding: utf-8 -*-
"""Tests du miroir générique core/store/mirror.py — la mécanique de double
écriture partagée par les pipelines neige.

Magasin LOCAL (dossier temporaire) de bout en bout : aucun réseau, aucun
release touché, aucune base de production lue ni écrite.

Deux régimes distincts y sont vérifiés :
  • mirror_to_store (collecte) — n'uploade que les mois modifiés, ne retire
    jamais rien ;
  • l'isolement strict des préfixes, deux flux dont l'un préfixe l'autre
    (db_megeve / db_megeve_hd) ne devant jamais se marcher dessus."""

import os
import sys
import tempfile

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.store import (  # noqa: E402
    LocalDirStore, concat_partitions, parse_partition_name, same_rows,
)
from core.store.mirror import changed_months, mirror_to_store  # noqa: E402

DB = "/tmp/db_flux_test.parquet"  # jamais écrit : sert de porteur de préfixe


def _df(dates, vals):
    return pd.DataFrame({
        "run_date": pd.to_datetime(dates),
        "model": "ECMWF",
        "t850": [float(v) for v in vals],
    })


def _read_store(store, prefix="db_flux_test"):
    """Relit le magasin comme le fait le dashboard : concat des partitions."""
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for name in _names(store, prefix):
            dest = os.path.join(tmp, name)
            store.download(name, dest)
            frames.append(pd.read_parquet(dest))
    return concat_partitions(frames)


def _names(store, prefix="db_flux_test"):
    """Assets d'UN flux, à préfixe EXACT — store.list() ne filtre lui que par
    début de nom (db_megeve attraperait db_megeve_hd) ; l'égalité stricte est
    la responsabilité des adaptateurs, reproduite ici."""
    out = []
    for a in store.list(prefix):
        parsed = parse_partition_name(a.name)
        if parsed and parsed[0] == prefix:
            out.append(a.name)
    return sorted(out)


def test_changed_months_pure():
    """Fonction pure : seuls les mois dont les lignes changent sont listés."""
    existing = _df(["2026-06-01", "2026-07-01"], [1, 2])
    combined = _df(["2026-06-01", "2026-07-01", "2026-07-02"], [1, 2, 3])
    assert changed_months(existing, combined, "run_date") == ["2026-07"]
    assert changed_months(existing, existing, "run_date") == []
    # Base vide au départ → tous les mois sont à écrire.
    assert changed_months(pd.DataFrame(), combined, "run_date") == ["2026-06", "2026-07"]


def test_mirror_uploads_only_changed_month():
    """Un mois clos inchangé n'est jamais ré-uploadé (backstop immuable)."""
    with tempfile.TemporaryDirectory() as root:
        store = LocalDirStore(root)
        base = _df(["2026-06-01", "2026-07-01"], [1, 2])
        mirror_to_store(store, pd.DataFrame(), base, DB, "run_date", log=lambda *_: None)
        assert _names(store) == ["db_flux_test_2026-06.parquet",
                                 "db_flux_test_2026-07.parquet"]
        juin_etag = {a.name: a.etag for a in store.list("db_flux_test")}

        grown = _df(["2026-06-01", "2026-07-01", "2026-07-02"], [1, 2, 3])
        done = mirror_to_store(store, base, grown, DB, "run_date", log=lambda *_: None)
        assert done == ["db_flux_test_2026-07.parquet"]
        after = {a.name: a.etag for a in store.list("db_flux_test")}
        # Juin intact (etag inchangé), juillet réécrit.
        assert after["db_flux_test_2026-06.parquet"] == juin_etag["db_flux_test_2026-06.parquet"]
        assert same_rows(_read_store(store), grown)


def test_mirror_never_raises_on_store_failure():
    """Best-effort : un magasin en panne ne doit JAMAIS faire échouer le
    pipeline appelant — le flux repart du magasin au poll suivant."""
    class Broken:
        def list(self, prefix=""):
            raise RuntimeError("magasin injoignable")

        def upload(self, name, src):
            raise RuntimeError("magasin injoignable")

        def download(self, name, dest):
            raise RuntimeError("magasin injoignable")

        def delete(self, name):
            raise RuntimeError("magasin injoignable")

    msgs = []
    done = mirror_to_store(Broken(), pd.DataFrame(), _df(["2026-07-01"], [1]),
                           DB, "run_date", log=msgs.append)
    assert done == []
    assert any("miroir store échoué" in m for m in msgs)


def test_prefix_isolation():
    """Deux flux dont l'un préfixe l'autre (db_megeve / db_megeve_hd) ne se
    marchent jamais dessus — l'égalité de préfixe est stricte."""
    with tempfile.TemporaryDirectory() as root:
        store = LocalDirStore(root)
        ens = _df(["2026-07-01"], [1])
        hd = _df(["2026-07-01"], [9])
        mirror_to_store(store, pd.DataFrame(), ens, "/tmp/db_megeve.parquet",
                        "run_date", log=lambda *_: None)
        mirror_to_store(store, pd.DataFrame(), hd, "/tmp/db_megeve_hd.parquet",
                        "run_date", log=lambda *_: None)
        # Le préfixe court ne doit PAS attraper les partitions du flux long.
        assert _names(store, "db_megeve") == ["db_megeve_2026-07.parquet"]
        assert _names(store, "db_megeve_hd") == ["db_megeve_hd_2026-07.parquet"]
        assert same_rows(_read_store(store, "db_megeve_hd"), hd)
        assert same_rows(_read_store(store, "db_megeve"), ens)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("✅ Tests du miroir/synchro magasin OK.")
