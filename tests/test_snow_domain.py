# -*- coding: utf-8 -*-
"""Cas de test du domaine neige (apps/snow/app/domains/neige/logic.py) sur un
ÉPISODE HIVERNAL synthétique : les contrôles en conditions réelles (juillet)
ne valident que la branche « pas de neige » (t850 chaud, iso0 haut, cumuls
nuls) — ce fichier construit un ensemble synthétique (masse d'air froide,
iso 0° bas, chutes de neige positives sur plusieurs membres, chute de pmsl)
pour vérifier le KPI « jour à neige », les paliers 1/5/20 cm, le calcul de
LPN et la bascule pmsl sur la branche neigeuse.
Exécutable sans pytest : `python tests/test_snow_domain.py` (asserts simples)."""

import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from apps.snow import snow_config as SC  # noqa: E402
from apps.snow.app.domains.neige import logic  # noqa: E402

MODELS = ["ECMWF", "AIFS", "GEFS"]
N_MEMBERS = 5
RUN0 = pd.Timestamp("2026-01-15 00:00")


def _close(a, b, tol=1e-6):
    return abs(a - b) < tol


def _member_rows(site, var_col, value_fn, hours):
    """Lignes (model, member, site, valid_time, var_col) — MODELS × N_MEMBERS,
    valeur donnée par value_fn(heure, membre) (kind="member", comme les
    lignes que consomment réellement les pools de app/data/runsets.py :
    latest_complete_run_sub filtre déjà sur kind="member" en amont)."""
    rows = []
    for model in MODELS:
        for member in range(N_MEMBERS):
            for h in hours:
                rows.append({
                    "model": model, "kind": "member", "member": member,
                    "site": site, "valid_time": RUN0 + pd.Timedelta(hours=h),
                    var_col: value_fn(h, member),
                })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
#  Scénario : masse d'air froide, iso 0° bas (neige au sommet, pluie au
#  village), chutes de neige sur 4 jours (0,9 / 2 / 12 / 22 cm — encadrant les
#  paliers 1/5/20 cm et la KPI proba×sévérité), pmsl en chute franche.
# --------------------------------------------------------------------------- #
HOURS = range(96)  # 4 jours, grille horaire
CUMUL_CIBLE_PAR_JOUR = [0.9, 2.0, 12.0, 22.0]  # cm — cf. SC.PALIERS_NEIGE_CM = [1, 5, 20]


def _neige_par_heure(h, member):
    # Uniforme entre modèles/membres (plusieurs membres positifs, cf. demande) :
    # simplifie le calcul attendu sans invalider la mécanique testée (somme
    # par membre puis stats entre membres — cf. logic.daily_snowfall).
    return CUMUL_CIBLE_PAR_JOUR[h // 24] / 24


def _iso0_par_heure(h, member):
    # Base 1500 m (froid), spread symétrique par membre → médiane = 1500 m
    # quel que soit l'instant (iso 0° constant ici, simplification acceptée :
    # seule la formule LPN = médiane − marge est sous test, pas la dynamique
    # temporelle de l'isotherme).
    offsets = [-150, -75, 0, 75, 150]
    return 1500.0 + offsets[member]


def _pmsl_par_heure(h, member):
    # Plateau 1020 hPa (h<48), puis chute linéaire vers 1008 hPa (h 48→71),
    # puis plateau 1008 hPa — reproduit une bascule de régime franche.
    if h < 48:
        return 1020.0
    if h <= 71:
        return 1020.0 - (h - 47) * 0.5
    return 1008.0


def _village_sub():
    iso0 = _member_rows("village", "iso0", _iso0_par_heure, HOURS)
    pmsl = _member_rows("village", "pmsl", _pmsl_par_heure, HOURS)
    return pd.concat([iso0, pmsl], ignore_index=True)


def _sommet_sub():
    return _member_rows("sommet", "neige", _neige_par_heure, HOURS)


def test_daily_snowfall_matches_designed_cumuls():
    daily = logic.daily_snowfall(_sommet_sub())
    assert daily is not None and len(daily) == 4
    for i, cible in enumerate(CUMUL_CIBLE_PAR_JOUR):
        row = daily.iloc[i]
        assert _close(row["attendu"], cible, tol=1e-3), (i, row["attendu"], cible)
        assert row["n_membres"] == len(MODELS) * N_MEMBERS
    # Jour 0 (0,9 cm) sous le seuil 1 cm → aucun membre ne le dépasse.
    assert _close(daily.iloc[0]["prob"], 0.0)
    # Jours 1-3 (≥ 1 cm) : tous les membres, uniformes, dépassent le seuil.
    for i in (1, 2, 3):
        assert _close(daily.iloc[i]["prob"], 1.0)


def test_jours_a_neige_kpi_proba_ou_severite():
    daily = logic.daily_snowfall(_sommet_sub())
    jours = logic.jours_a_neige(daily)
    # Jour 0 (0,9 cm, prob 0.0) exclu des deux côtés du critère OR ; les trois
    # jours suivants passent par le critère proba (>= 0.50, cf. KPI_NEIGE_PROB_MIN).
    assert len(jours) == 3
    assert list(jours["attendu"].round(1)) == [2.0, 12.0, 22.0]


def test_paliers_neige_encadrent_les_bornes_1_5_20():
    # Bornes validées : petite chute [1,5), vraie chute [5,20), grosse chute [20,+).
    assert logic.palier_neige(0.9)[0] == "—"
    assert logic.palier_neige(2.0)[0] == "petite chute"
    assert logic.palier_neige(12.0)[0] == "vraie chute"
    assert logic.palier_neige(22.0)[0] == "grosse chute"
    # Bornes exactes (side="right" : la borne elle-même appartient au palier
    # inférieur, cf. np.searchsorted dans logic.palier_neige).
    assert logic.palier_neige(1.0)[0] == "petite chute"
    assert logic.palier_neige(5.0)[0] == "vraie chute"
    assert logic.palier_neige(20.0)[0] == "grosse chute"


def test_lpn_series_neige_au_sommet_pluie_au_village():
    village = _village_sub()
    lpn = logic.lpn_series(village)
    assert lpn is not None and not lpn.empty
    # Médiane iso0 = 1500 m (spread symétrique) − marge 300 m = 1200 m, constant.
    assert (lpn["lpn"].round(3) == 1200.0).all()
    # 1200 m : sous le sommet (1830 m, neige) mais AU-DESSUS du village (1100 m,
    # pluie) — scénario transitionnel classique, les deux branches sont exercées.
    assert logic.neige_au_site(1200.0, "sommet") is True
    assert logic.neige_au_site(1200.0, "village") is False


def test_t850_label_neige_redoux_et_limite_par_site():
    # Masse d'air franchement froide : neige probable aux DEUX points.
    assert logic.t850_label(-3.0, "village") == "neige probable"
    assert logic.t850_label(-3.0, "sommet") == "neige probable"
    # Entre les deux seuils village (-1 °C) : « limite » au village, mais déjà
    # sous le seuil sommet (+1 °C) → neige probable au sommet seul (le pivot
    # se lit PAR SITE, cf. SC.SEUIL_T850_NEIGE).
    assert logic.t850_label(-0.5, "village") == "limite, à surveiller"
    assert logic.t850_label(-0.5, "sommet") == "neige probable"
    # Redoux net (≥ +3 °C) aux deux points.
    assert logic.t850_label(5.0, "village") == "redoux pluvieux"
    assert logic.t850_label(5.0, "sommet") == "redoux pluvieux"


def test_pmsl_bascule_detectee_au_bon_instant():
    village = _village_sub()
    bascule = logic.pmsl_bascule(village)
    assert bascule is not None
    # Chute construite pour atteindre exactement −5 hPa/24 h à h=57 (valeur
    # antérieure la plus proche qui satisfait le seuil PMSL_BASCULE_HPA_24H) :
    # med[57]=1015.0, med[57-24=33]=1020.0 → écart −5.0, première occurrence.
    assert bascule == RUN0 + pd.Timedelta(hours=57)
    assert SC.PMSL_BASCULE_HPA_24H == 5.0  # documente l'hypothèse du calcul ci-dessus


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("Tous les tests du domaine neige (scénario hivernal) passent.")
