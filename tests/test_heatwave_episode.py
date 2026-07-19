# -*- coding: utf-8 -*-
"""Cas de test de episode_chaleur (durée/fin d'épisode canicule, issue #11-14) :
un creux d'un jour sous le seuil de probabilité mais chaud en médiane ne doit
PAS couper la durée affichée de l'épisode. Exécutable sans pytest :
`python tests/test_heatwave_episode.py` (asserts simples)."""

import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "apps", "canicule"))  # package app du dashboard canicule

from app.domains.heatwave.logic import episode_chaleur  # noqa: E402

SEUIL_CHALEUR = 15.0


def _jours(rows):
    """DataFrame minimal au format daily_risk : (date, prob, Médiane)."""
    return pd.DataFrame([{"date": pd.Timestamp(d), "prob": p, "Médiane": m}
                         for d, p, m in rows])


def test_creux_un_jour_ne_coupe_pas():
    # Scénario de l'issue : 3 jours confirmés, 1 creux chaud (prob 0.45 mais
    # médiane 16.5 ≥ seuil), 2 jours confirmés → épisode complet de 6 jours.
    jours = _jours([
        ("2026-07-20", 0.70, 17.0),
        ("2026-07-21", 0.80, 18.0),
        ("2026-07-22", 0.65, 17.5),
        ("2026-07-23", 0.45, 16.5),   # creux : sous 50 % mais chaud
        ("2026-07-24", 0.75, 18.5),
        ("2026-07-25", 0.60, 17.0),
        ("2026-07-26", 0.10, 13.0),
    ])
    ep = episode_chaleur(jours, SEUIL_CHALEUR)
    assert ep["duree"] == 6, ep
    assert ep["debut"] == pd.Timestamp("2026-07-20")
    assert ep["fin"] == pd.Timestamp("2026-07-25")
    assert ep["jours_canicule"] == 5
    assert ep["jours_creux"] == 1


def test_creux_froid_coupe():
    # Le jour intermédiaire est sous seuil_chaleur en médiane : vraie coupure,
    # l'épisode s'arrête avant le creux (comportement strict conservé).
    jours = _jours([
        ("2026-07-20", 0.70, 17.0),
        ("2026-07-21", 0.80, 18.0),
        ("2026-07-22", 0.30, 13.0),   # creux froid
        ("2026-07-23", 0.75, 18.5),
    ])
    ep = episode_chaleur(jours, SEUIL_CHALEUR)
    assert ep["duree"] == 2, ep
    assert ep["fin"] == pd.Timestamp("2026-07-21")
    assert ep["jours_creux"] == 0


def test_creux_final_n_etend_pas():
    # Les creux ne font que relier deux jours confirmés : un jour chaud non
    # confirmé APRÈS le dernier jour de canicule n'allonge pas l'épisode.
    jours = _jours([
        ("2026-07-20", 0.70, 17.0),
        ("2026-07-21", 0.80, 18.0),
        ("2026-07-22", 0.45, 16.5),   # chaud mais rien de confirmé ensuite
    ])
    ep = episode_chaleur(jours, SEUIL_CHALEUR)
    assert ep["duree"] == 2, ep
    assert ep["fin"] == pd.Timestamp("2026-07-21")


def test_jour_manquant_coupe():
    # Trou dans la grille journalière (au-delà de l'horizon, donnée absente) :
    # impossible de garantir la continuité, l'épisode s'arrête.
    jours = _jours([
        ("2026-07-20", 0.70, 17.0),
        ("2026-07-22", 0.75, 18.5),   # le 21 n'existe pas dans la base
    ])
    ep = episode_chaleur(jours, SEUIL_CHALEUR)
    assert ep["duree"] == 1, ep


def test_sequence_stricte_inchangee():
    # Sans creux, comportement identique à l'ancien calcul strict.
    jours = _jours([
        ("2026-07-20", 0.70, 17.0),
        ("2026-07-21", 0.80, 18.0),
        ("2026-07-22", 0.65, 17.5),
    ])
    ep = episode_chaleur(jours, SEUIL_CHALEUR)
    assert ep["duree"] == 3, ep
    assert ep["jours_creux"] == 0


def test_depuis_ancre_episode_en_cours():
    # Ancre `depuis` (badge « En cours ») : la fin retournée est celle de
    # l'épisode courant, creux chaud compris.
    jours = _jours([
        ("2026-07-20", 0.70, 17.0),
        ("2026-07-21", 0.45, 16.5),   # creux chaud
        ("2026-07-22", 0.75, 18.5),
        ("2026-07-23", 0.10, 13.0),
    ])
    ep = episode_chaleur(jours, SEUIL_CHALEUR, depuis=pd.Timestamp("2026-07-20"))
    assert ep["fin"] == pd.Timestamp("2026-07-22"), ep


def test_aucun_jour_confirme():
    jours = _jours([("2026-07-20", 0.30, 16.0)])
    assert episode_chaleur(jours, SEUIL_CHALEUR) is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK  {fn.__name__}")
    print(f"\n{len(fns)} tests passés.")
