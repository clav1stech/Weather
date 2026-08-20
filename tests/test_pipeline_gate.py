# -*- coding: utf-8 -*-
"""Tests de la porte par mot de passe de la page « Lancer le pipeline »
(core/ui/auth.py) et de l'exposition de la page selon l'environnement.

Aucun appel réseau, aucun déclenchement réel : le workflow_dispatch n'est
jamais atteint (la porte reste fermée, ou le bouton n'est pas cliqué).

L'invariant vérifié ici est de sécurité : la page est consultable de tous,
mais un secret absent ou un mot de passe faux ne doit JAMAIS laisser passer
le moindre bouton de déclenchement, et l'import legacy (seule écriture dans
le parquet) ne doit jamais apparaître hors du mode local."""

import os
import sys
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "apps", "canicule"))

import pytest  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from core.testing.apptest_nav import aller_a, page_rendue  # noqa: E402
from core.ui import auth  # noqa: E402

APP = os.path.join(_ROOT, "meteo_app.py")
APP_SNOW = os.path.join(_ROOT, "snow_app.py")
MDP = "s3cret"


# --------------------------------------------------------------------------- #
#  Comparaison du mot de passe (unitaire, sans Streamlit)
# --------------------------------------------------------------------------- #
def test_gate_ferme_par_defaut(monkeypatch):
    """Secret absent/vide → accès REFUSÉ (jamais de repli permissif)."""
    import streamlit as st
    monkeypatch.setattr(st, "session_state", {}, raising=False)
    assert auth.gate(None, "k") is False
    assert auth.gate("", "k") is False


def test_unlocked_suit_la_session(monkeypatch):
    import streamlit as st
    state = {}
    monkeypatch.setattr(st, "session_state", state, raising=False)
    assert auth.unlocked("k") is False
    state["k"] = True
    assert auth.unlocked("k") is True
    # Une session déverrouillée ne redemande jamais le mot de passe.
    assert auth.gate("peu importe", "k") is True


# --------------------------------------------------------------------------- #
#  Rendu de la page (AppTest)
# --------------------------------------------------------------------------- #
def _page(secrets, local):
    """Rend la page « Lancer le pipeline » dans l'environnement demandé.

    IS_LOCAL est une constante figée à l'import du package : on patche donc le
    nom LÀ OÙ LA PAGE LE LIT, plutôt que la variable d'environnement (qui ne
    serait plus relue et rendrait ces tests dépendants de leur ordre)."""
    at = AppTest.from_file(APP, default_timeout=180)
    with patch("streamlit.secrets", secrets), \
            patch("app.pages.pipeline.IS_LOCAL", local):
        at.run()
        aller_a(at, "Lancer le pipeline").run()
    return at


def _labels(at):
    return [b.label for b in at.button]


def test_page_visible_en_ligne():
    """La page est proposée dans la navigation même hors local."""
    at = _page({}, local=False)
    assert page_rendue(at, "Lancer le pipeline")
    assert not at.exception


def test_sans_secret_aucun_declenchement():
    """Secret absent : consultation possible, aucun bouton de lancement."""
    at = _page({}, local=False)
    assert not any("Lancer" in l for l in _labels(at))
    assert any("non configuré" in i.value for i in at.info)
    # L'historique du contrôle croisé, lui, reste consultable.
    assert any("contrôle croisé" in s.value for s in at.subheader)


def test_mauvais_mot_de_passe_refuse():
    at = _page({"PIPELINE_PASSWORD": MDP}, local=False)
    with patch("streamlit.secrets", {"PIPELINE_PASSWORD": MDP}), \
            patch("app.pages.pipeline.IS_LOCAL", False):
        at.text_input[0].set_value("mauvais").run()
    assert any("incorrect" in e.value for e in at.error)
    assert not any("Lancer" in l for l in _labels(at))


def test_bon_mot_de_passe_ouvre_le_declenchement():
    at = _page({"PIPELINE_PASSWORD": MDP}, local=False)
    with patch("streamlit.secrets", {"PIPELINE_PASSWORD": MDP}), \
            patch("app.pages.pipeline.IS_LOCAL", False):
        at.text_input[0].set_value(MDP).run()
    assert any("Lancer" in l for l in _labels(at))
    assert not at.exception


def test_import_legacy_jamais_expose_en_ligne():
    """L'import legacy ÉCRIT dans le parquet : jamais visible hors local,
    même une fois le déclenchement déverrouillé."""
    at = _page({"PIPELINE_PASSWORD": MDP}, local=False)
    with patch("streamlit.secrets", {"PIPELINE_PASSWORD": MDP}), \
            patch("app.pages.pipeline.IS_LOCAL", False):
        at.text_input[0].set_value(MDP).run()
    assert not any("Import ciblé" in s.value for s in at.subheader)
    assert not any("Importer" in l for l in _labels(at))


def test_mode_local_inchange():
    """En local : scripts en sous-processus et import legacy, sans mot de passe."""
    at = _page({}, local=True)
    labels = _labels(at)
    assert any("Forecast.py" in l for l in labels)
    assert any("Importer" in l for l in labels)
    assert not at.text_input  # aucune porte en local
    assert not at.exception


# --------------------------------------------------------------------------- #
#  Dashboard NEIGE — même porte, même invariant
# --------------------------------------------------------------------------- #
def _page_snow(secrets, local):
    at = AppTest.from_file(APP_SNOW, default_timeout=180)
    with patch("streamlit.secrets", secrets), \
            patch("apps.snow.app.pages.pipeline.IS_LOCAL", local):
        at.run()
        aller_a(at, "Lancer le pipeline").run()
    return at


def test_snow_page_visible_en_ligne():
    at = _page_snow({}, local=False)
    assert page_rendue(at, "Lancer le pipeline")
    assert not at.exception


def test_snow_sans_secret_aucun_declenchement():
    at = _page_snow({}, local=False)
    assert not any("Lancer —" in l for l in _labels(at))
    assert any("non configuré" in i.value for i in at.info)


def test_snow_mot_de_passe_ouvre_le_declenchement():
    at = _page_snow({"PIPELINE_PASSWORD": MDP}, local=False)
    with patch("streamlit.secrets", {"PIPELINE_PASSWORD": MDP}), \
            patch("apps.snow.app.pages.pipeline.IS_LOCAL", False):
        at.text_input[0].set_value("mauvais").run()
    assert any("incorrect" in e.value for e in at.error)
    assert not any("Lancer —" in l for l in _labels(at))

    at = _page_snow({"PIPELINE_PASSWORD": MDP}, local=False)
    with patch("streamlit.secrets", {"PIPELINE_PASSWORD": MDP}), \
            patch("apps.snow.app.pages.pipeline.IS_LOCAL", False):
        at.text_input[0].set_value(MDP).run()
    assert any("Lancer —" in l for l in _labels(at))


def test_snow_rollover_jamais_expose_en_ligne():
    """Le diagnostic rollover lance un sous-processus : sans objet en ligne."""
    at = _page_snow({"PIPELINE_PASSWORD": MDP}, local=False)
    with patch("streamlit.secrets", {"PIPELINE_PASSWORD": MDP}), \
            patch("apps.snow.app.pages.pipeline.IS_LOCAL", False):
        at.text_input[0].set_value(MDP).run()
    assert not any("rollover" in l.lower() for l in _labels(at))


def test_snow_mode_local_inchange():
    at = _page_snow({}, local=True)
    labels = _labels(at)
    assert any("7 collectes actives" in l for l in labels)
    assert any("rollover" in l.lower() for l in labels)
    assert not at.text_input  # aucune porte en local


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
