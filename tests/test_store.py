# -*- coding: utf-8 -*-
"""Tests du magasin de données externe (core/store/) — Phase 0 du chantier
« sortie de git » (docs/DESIGN_sortie_git.md).

Fonctions pures de partition + magasin local (fichiers) + magasin GitHub (CLI
`gh` mockée, aucun réseau). Chemins temporaires uniquement — ne touche JAMAIS
aux vraies bases. Exécutable sans pytest : `python tests/test_store.py`."""

import os
import subprocess
import sys
import tempfile

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.store import partition as P  # noqa: E402
from core.store.github import GitHubReleaseStore  # noqa: E402
from core.store.local import LocalDirStore  # noqa: E402


def _df(run_dates, vals=None):
    """Base synthétique : run_date + model + une valeur."""
    vals = vals if vals is not None else list(range(len(run_dates)))
    return pd.DataFrame({
        "run_date": pd.to_datetime(run_dates),
        "model": "ECMWF",
        "t850": [float(v) for v in vals],
    })


# --------------------------------------------------------------- partition pure
def test_month_key_and_names():
    assert P.month_key("2026-07-26 12:00") == "2026-07"
    assert P.partition_name("database_paris", "2026-07") == "database_paris_2026-07.parquet"


def test_parse_partition_name_handles_underscored_prefix():
    # Le préfixe contient des « _ » : seul le dernier segment est la clé mois.
    assert P.parse_partition_name("database_paris_observations_2026-07.parquet") \
        == ("database_paris_observations", "2026-07")
    assert P.parse_partition_name("database_paris_2026-07.parquet") \
        == ("database_paris", "2026-07")
    # Noms hors schéma → None (asset étranger ignoré sans bruit).
    assert P.parse_partition_name("readme.txt") is None
    assert P.parse_partition_name("database_paris.parquet") is None


def test_split_by_month_partitions_on_run_date():
    df = _df(["2026-06-30", "2026-07-01", "2026-07-15"], vals=[1, 2, 3])
    parts = P.split_by_month(df, "run_date")
    assert set(parts) == {"2026-06", "2026-07"}
    assert list(parts["2026-06"]["t850"]) == [1.0]
    assert sorted(parts["2026-07"]["t850"]) == [2.0, 3.0]
    # Aucune colonne technique ajoutée.
    assert list(parts["2026-07"].columns) == ["run_date", "model", "t850"]


def test_split_by_month_empty_is_empty_dict():
    assert P.split_by_month(pd.DataFrame(), "run_date") == {}


def test_split_by_month_rejects_nat_never_loses_a_row():
    df = _df(["2026-07-01", "2026-07-02"])
    df.loc[0, "run_date"] = pd.NaT
    try:
        P.split_by_month(df, "run_date")
    except ValueError:
        return
    raise AssertionError("un run_date NaT doit lever, jamais perdre la ligne")


def test_roundtrip_split_concat_preserves_all_rows():
    # Invariant ABSOLU : concat(split) == original (mêmes lignes, ordre libre).
    df = _df(["2026-06-17", "2026-06-30", "2026-07-01", "2026-07-26", "2026-07-26"],
             vals=[10, 20, 30, 40, 40])  # doublon volontaire : préservé
    parts = P.split_by_month(df, "run_date")
    rebuilt = P.concat_partitions(parts.values())
    assert P.same_rows(rebuilt, df)
    assert len(rebuilt) == len(df)  # le doublon n'est pas absorbé


def test_same_rows_detects_a_difference():
    a = _df(["2026-07-01"], vals=[1])
    b = _df(["2026-07-01"], vals=[2])
    assert not P.same_rows(a, b)
    assert not P.same_rows(a, a.drop(columns=["t850"]))  # colonnes différentes


def test_concat_partitions_empty():
    assert P.concat_partitions([]).empty
    assert P.concat_partitions([pd.DataFrame(), pd.DataFrame()]).empty


# ------------------------------------------------------------------ local store
def test_local_store_roundtrip_and_prefix():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalDirStore(os.path.join(tmp, "store"))
        src = os.path.join(tmp, "src.parquet")
        _df(["2026-07-01"]).to_parquet(src, index=False)

        store.upload("database_paris_2026-07.parquet", src)
        store.upload("database_paris_observations_2026-07.parquet", src)
        # Filtrage par préfixe.
        names = [a.name for a in store.list("database_paris_2026")]
        assert names == ["database_paris_2026-07.parquet"]
        assert len(store.list()) == 2

        dest = os.path.join(tmp, "out.parquet")
        store.download("database_paris_2026-07.parquet", dest)
        assert P.same_rows(pd.read_parquet(dest), _df(["2026-07-01"]))


def test_local_store_etag_changes_with_content():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalDirStore(os.path.join(tmp, "store"))
        a = os.path.join(tmp, "a.parquet")
        _df(["2026-07-01"], vals=[1]).to_parquet(a, index=False)
        store.upload("x_2026-07.parquet", a)
        e1 = store.list()[0].etag
        _df(["2026-07-01"], vals=[2]).to_parquet(a, index=False)
        store.upload("x_2026-07.parquet", a)  # remplace en place
        e2 = store.list()[0].etag
        assert e1 != e2  # jeton de changement effectif → invalide le cache
        assert len(store.list()) == 1  # remplacement, pas d'accumulation


def test_local_store_upload_atomic_no_tmp_left():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalDirStore(os.path.join(tmp, "store"))
        src = os.path.join(tmp, "src.parquet")
        _df(["2026-07-01"]).to_parquet(src, index=False)
        store.upload("x_2026-07.parquet", src)
        leftovers = [f for f in os.listdir(store.root) if f.endswith(".tmp")]
        assert not leftovers


def test_local_store_missing_download_and_delete_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalDirStore(os.path.join(tmp, "store"))
        try:
            store.download("absent.parquet", os.path.join(tmp, "o.parquet"))
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("download d'un asset absent doit lever")
        store.delete("absent.parquet")  # no-op, ne lève pas


# ----------------------------------------------------------------- github store
class _FakeGh(GitHubReleaseStore):
    """GitHubReleaseStore dont le seul appel système (`_run`) est simulé :
    on enregistre les commandes et on renvoie des sorties canned, sans réseau."""

    def __init__(self, assets_json=None, release_exists=True):
        super().__init__(repo="owner/repo", tag="data-store")
        self._assets_json = assets_json
        self._release_exists = release_exists
        self.calls = []

    def _run(self, args, capture=True):
        self.calls.append(list(args))
        if args[:2] == ["release", "view"]:
            if not self._release_exists:
                raise subprocess.CalledProcessError(1, ["gh", *args])
            if "assets" in args:
                return self._assets_json or '{"assets": []}'
            return '{"tagName": "data-store"}'
        return ""  # create/upload/download/delete : rien à renvoyer


def test_github_list_parses_assets_and_filters_prefix():
    js = ('{"assets": ['
          '{"name": "database_paris_2026-06.parquet", "size": 100, "updatedAt": "A"},'
          '{"name": "database_paris_2026-07.parquet", "size": 200, "updatedAt": "B"},'
          '{"name": "autre_flux_2026-07.parquet", "size": 50, "updatedAt": "C"}]}')
    store = _FakeGh(assets_json=js)
    got = store.list("database_paris_")
    assert [a.name for a in got] == ["database_paris_2026-06.parquet",
                                     "database_paris_2026-07.parquet"]
    assert got[0].size == 100 and got[0].etag == "A"  # updatedAt = etag


def test_github_list_absent_release_is_empty():
    assert _FakeGh(release_exists=False).list() == []


def test_github_upload_stages_file_under_asset_name():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "local_name_quelconque.parquet")
        _df(["2026-07-01"]).to_parquet(src, index=False)
        store = _FakeGh()
        store.upload("database_paris_2026-07.parquet", src)
        upload_calls = [c for c in store.calls if c[:2] == ["release", "upload"]]
        assert len(upload_calls) == 1
        staged = upload_calls[0][3]  # gh release upload <tag> <fichier> --clobber
        # L'asset est nommé d'après le basename → doit être le nom cible.
        assert os.path.basename(staged) == "database_paris_2026-07.parquet"
        assert "--clobber" in upload_calls[0]


def test_github_ensure_release_creates_when_absent():
    store = _FakeGh(release_exists=False)
    store.ensure_release()
    assert any(c[:2] == ["release", "create"] for c in store.calls)


def test_github_ensure_release_noop_when_present():
    store = _FakeGh(release_exists=True)
    store.ensure_release()
    assert not any(c[:2] == ["release", "create"] for c in store.calls)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("Tous les tests du magasin (core/store/) passent.")
