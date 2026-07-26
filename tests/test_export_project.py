"""Tests des présélections d'export IA par application."""

from tools import export_project as export


def _relative_paths(app):
    return {
        path.relative_to(export.project_dir).as_posix()
        for path in export.collect_files("ai", app=app)
    }


def test_canicule_scope_keeps_canicule_and_common_code_only():
    paths = _relative_paths("canicule")

    assert "meteo_app.py" in paths
    assert "Forecast.py" in paths
    assert "apps/canicule/app/pages/overview.py" in paths
    assert "core/stats/ensemble.py" in paths
    assert "CLAUDE.md" in paths
    assert "snow_app.py" not in paths
    assert not any(path.startswith("apps/snow/") for path in paths)
    assert not any(path.startswith("tests/test_snow_") for path in paths)


def test_snow_scope_keeps_snow_and_common_code_only():
    paths = _relative_paths("snow")

    assert "snow_app.py" in paths
    assert "apps/snow/pipeline/fetch_ensemble.py" in paths
    assert "tests/test_snow_pipeline.py" in paths
    assert "core/pipeline/ensemble_runs.py" in paths
    assert "CLAUDE.md" in paths
    assert "meteo_app.py" not in paths
    assert "Forecast.py" not in paths
    assert not any(path.startswith("apps/canicule/") for path in paths)


def test_outline_scope_uses_the_same_curated_file_set():
    ai_paths = {
        path.relative_to(export.project_dir).as_posix()
        for path in export.collect_files("ai", app="snow")
    }
    outline_paths = {
        path.relative_to(export.project_dir).as_posix()
        for path in export.collect_files("outline", app="snow")
    }
    assert outline_paths == ai_paths


def test_python_outline_keeps_api_but_drops_function_bodies():
    source = '''"""Module de démonstration."""
import pandas as pd

PUBLIC_LIMIT = 12
_PRIVATE_LIMIT = 99
private_value = "hors structure"

def calculate(value, factor=2):
    """Calcule un indicateur synthétique."""
    body_secret = "NE_DOIT_PAS_SORTIR"
    return value * factor

def _private_helper():
    return "bruit interne"
'''
    outline = export._python_outline(source)

    assert "Module de démonstration" in outline
    assert "pandas" in outline
    assert "PUBLIC_LIMIT = 12" in outline
    assert "_PRIVATE_LIMIT" not in outline
    assert "def calculate(value, factor=2)" in outline
    assert "Calcule un indicateur synthétique" in outline
    assert "private_value" not in outline
    assert "_private_helper" not in outline
    assert "NE_DOIT_PAS_SORTIR" not in outline
    assert "return value * factor" not in outline


def test_app_scope_is_ai_only_and_exclusive_with_only():
    try:
        export.collect_files("backup", app="snow")
    except ValueError as exc:
        assert "profil IA" in str(exc)
    else:
        raise AssertionError("Une présélection d'app ne doit pas cibler --backup")

    try:
        export.collect_files("ai", only=["core"], app="snow")
    except ValueError as exc:
        assert "--only" in str(exc)
    else:
        raise AssertionError("Une présélection d'app ne doit pas se combiner avec --only")


def test_snow_export_uses_its_independent_version(monkeypatch, tmp_path):
    captured = {}

    def _capture(_files, _base_name, _git, app_version, version_label, excluded=None):
        captured.update(version=app_version, label=version_label, excluded=excluded)

    monkeypatch.setattr(export, "export_dir", tmp_path)
    monkeypatch.setattr(export, "export_ai", _capture)
    monkeypatch.setattr(export, "_get_git_info", lambda: {})

    export.run("ai", app="snow")

    assert captured["version"] == export.SNOW_APP_VERSION
    assert captured["label"] == "SNOW_APP_VERSION"
    assert captured["excluded"]


def test_outline_run_uses_the_compact_renderer(monkeypatch, tmp_path):
    captured = {}

    def _capture(files, base_name, _git, _version, _label, excluded=None):
        captured.update(files=files, base_name=base_name, excluded=excluded)

    monkeypatch.setattr(export, "export_dir", tmp_path)
    monkeypatch.setattr(export, "export_outline", _capture)
    monkeypatch.setattr(export, "_get_git_info", lambda: {})

    export.run("outline", app="canicule")

    assert captured["files"]
    assert "outline_canicule" in captured["base_name"]
    assert captured["excluded"]
