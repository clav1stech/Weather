# -*- coding: utf-8 -*-
"""Tests de la double écriture pipeline → magasin externe (Forecast.mirror_to_store,
Phase 1 de « sortie de git »). L'appel `gh` réel est mocké : aucun réseau, aucun
release touché, et surtout AUCUNE écriture dans la vraie base (on ne teste que la
logique de découpage/upload/vérification, jamais persist()). Exécutable sans
pytest : `python tests/test_mirror_store.py`."""

import os
import shutil
import sys
import tempfile

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import Forecast as F  # noqa: E402


def _combined(run_dates, vals):
    return pd.DataFrame({
        "run_date": pd.to_datetime(run_dates),
        "model": "ECMWF", "member": 0,
        "valid_time": pd.to_datetime(run_dates),
        "t850": [float(v) for v in vals],
    })


def _install_fake_store(monkeypatch_dir):
    """Remplace _gh/_ensure_store_release par un faux magasin adossé à un
    dossier : upload = copie, download = copie inverse. Renvoie (restore)."""
    orig_gh, orig_ensure = F._gh, F._ensure_store_release

    def fake_gh(args):
        if args[:2] == ["release", "upload"]:
            src = args[3]
            shutil.copy2(src, os.path.join(monkeypatch_dir, os.path.basename(src)))
        elif args[:2] == ["release", "download"]:
            name = args[args.index("--pattern") + 1]
            out = args[args.index("--output") + 1]
            shutil.copy2(os.path.join(monkeypatch_dir, name), out)
        return ""

    F._gh = fake_gh
    F._ensure_store_release = lambda: None

    def restore():
        F._gh, F._ensure_store_release = orig_gh, orig_ensure
    return restore


def test_mirror_uploads_only_touched_months_and_verifies():
    with tempfile.TemporaryDirectory() as store:
        restore = _install_fake_store(store)
        try:
            # Base à cheval juin/juillet ; seul juillet est « frais » ce cycle.
            combined = _combined(["2026-06-30", "2026-07-01", "2026-07-02"], [1, 2, 3])
            F.mirror_to_store(combined, months=["2026-07"])
            files = sorted(os.listdir(store))
            # Seul le mois touché est uploadé (juin, mois clos, reste intact).
            assert files == ["database_paris_2026-07.parquet"], files
            got = pd.read_parquet(os.path.join(store, files[0]))
            assert sorted(got["t850"]) == [2.0, 3.0]  # tranche juillet exacte
        finally:
            restore()


def test_mirror_uploads_both_months_at_boundary():
    with tempfile.TemporaryDirectory() as store:
        restore = _install_fake_store(store)
        try:
            combined = _combined(["2026-06-30", "2026-07-01"], [1, 2])
            F.mirror_to_store(combined, months=["2026-06", "2026-07"])
            assert sorted(os.listdir(store)) == [
                "database_paris_2026-06.parquet",
                "database_paris_2026-07.parquet",
            ]
        finally:
            restore()


def test_mirror_never_raises_on_failure():
    # Un échec d'upload (gh qui lève) ne doit JAMAIS propager : git est source
    # de vérité, la collecte a déjà réussi.
    orig_gh, orig_ensure = F._gh, F._ensure_store_release
    F._ensure_store_release = lambda: None

    def boom(args):
        raise RuntimeError("gh indisponible")
    F._gh = boom
    try:
        F.mirror_to_store(_combined(["2026-07-01"], [1]), months=["2026-07"])
    finally:
        F._gh, F._ensure_store_release = orig_gh, orig_ensure  # aucune exception attendue


def test_mirror_detects_divergence():
    # Si la partition re-téléchargée ne correspond pas à la tranche écrite, la
    # vérification lève en interne — mais reste captée (non bloquant). On vérifie
    # que _same_rows discrimine bien.
    a = _combined(["2026-07-01"], [1])
    b = _combined(["2026-07-01"], [2])
    assert F._same_rows(a, a)
    assert not F._same_rows(a, b)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("Tous les tests du miroir pipeline passent.")
