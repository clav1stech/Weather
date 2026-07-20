# -*- coding: utf-8 -*-
"""Tests synthétiques de la classification et de la coupe verticale neige."""

import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from apps.snow.app.domains.neige import weather_type as WT  # noqa: E402
from apps.snow import snow_config as SC  # noqa: E402


NOW = pd.Timestamp("2026-01-10 00:00")


def _ensemble_members(site="village"):
    """Un jour J+4 avec un membre de chaque catégorie."""
    values = [
        # member, precip, t850, epaisseur, catégorie attendue
        (0, 0.4, 5.0, 5500.0, "sec"),
        (1, 5.0, -3.0, 5300.0, "neigeux"),
        (2, 5.0, 4.0, 5500.0, "pluvieux"),
        (3, 5.0, 1.0, 5400.0, "mixte"),
    ]
    rows = []
    for member, precip, t850, epaisseur, _ in values:
        rows.append({
            "model": "ECMWF", "member": member, "site": site,
            "valid_time": NOW + pd.Timedelta(days=4, hours=12),
            "precip": precip, "t850": t850, "epaisseur": epaisseur,
        })
    return pd.DataFrame(rows)


def test_classification_exerce_sec_neige_pluie_et_mixte():
    result = WT.ensemble_daily_weather_types(_ensemble_members(), now=NOW)
    assert result.available and len(result.daily) == 1
    row = result.daily.iloc[0]
    assert row["n_classes"] == 4
    assert row["n_non_classes"] == 0
    for category in WT.CATEGORIES:
        assert row[category] == 25.0


def test_seuil_sec_est_prioritaire_independamment_du_froid():
    assert WT.classify_member_day(0.49, -10.0, 5100.0) == "sec"
    assert WT.PRECIP_BRUIT_MM_JOUR == 0.5


def test_trace_chaude_reste_mixte_et_non_journee_pluvieuse():
    assert WT.classify_member_day(1.9, 5.0, 5500.0) == "mixte"
    assert WT.classify_member_day(2.0, 5.0, 5500.0) == "pluvieux"
    assert WT.PRECIP_PLUIE_SIGNIFICATIVE_MM_JOUR == 2.0


def test_discriminant_incomplet_ou_intermediaire_reste_mixte():
    assert WT.classify_member_day(5.0, np.nan, 5300.0) == "mixte"
    assert WT.classify_member_day(5.0, 1.0, 5400.0) == "mixte"


def test_epaisseur_douce_ne_rend_pas_la_neige_impossible():
    # Une t850 franchement froide suffit à garder un membre neigeux même si
    # l'épaisseur profonde est douce ou absente : ce signal n'est pas un veto.
    assert WT.classify_member_day(5.0, -3.0, 5500.0) == "neigeux"
    assert WT.classify_member_day(5.0, -3.0, np.nan) == "neigeux"


def test_redoux_t850_avec_epaisseur_tres_froide_reste_mixte():
    assert WT.classify_member_day(5.0, 4.0, 5300.0) == "mixte"


def test_classification_inclut_les_trois_premiers_jours():
    data = _ensemble_members()
    data["valid_time"] = NOW + pd.Timedelta(days=1, hours=12)
    result = WT.ensemble_daily_weather_types(data, now=NOW)
    assert result.available
    assert list(result.daily["jour"]) == [1]


def test_gefs_est_secondaire_pour_le_type_de_temps_local_a_court_terme():
    rows = []
    for model, precip in (("ECMWF", 0.0), ("AIFS", 0.0), ("GEFS", 5.0)):
        rows.append({
            "model": model, "member": 0, "site": "village",
            "valid_time": NOW + pd.Timedelta(days=1, hours=12),
            "precip": precip, "t850": 5.0, "epaisseur": 5500.0,
        })
    result = WT.ensemble_daily_weather_types(pd.DataFrame(rows), now=NOW)
    row = result.daily.iloc[0]
    assert row["sec"] == 85.0
    assert row["pluvieux"] == 15.0


def test_gefs_retrouve_du_poids_sur_le_regime_lointain():
    weights = WT.weather_type_model_weights(10, ["ECMWF", "AIFS", "GEFS"])
    assert weights == {"ECMWF": 0.35, "AIFS": 0.35, "GEFS": 0.30}


def test_pe_arome_classe_la_phase_directe_et_garde_une_zone_mixte():
    assert WT.classify_pe_arome_day(0.49, np.nan) == "sec"
    assert WT.classify_pe_arome_day(5.0, 4.0) == "neigeux"
    assert WT.classify_pe_arome_day(5.0, 0.5) == "pluvieux"
    assert WT.classify_pe_arome_day(5.0, 2.5) == "mixte"
    assert WT.classify_pe_arome_day(5.0, np.nan) is None


def test_pe_arome_domine_le_type_local_sans_ecraser_les_globaux():
    globals_ = []
    for model in ("ECMWF", "AIFS", "GEFS"):
        globals_.append({
            "model": model, "member": 0, "site": "village",
            "valid_time": NOW + pd.Timedelta(days=1, hours=12),
            "precip": 0.0, "t850": -4.0, "epaisseur": 5250.0,
        })
    regional = pd.DataFrame([{
        "model": "PE-AROME", "member": 0, "site": "village",
        "valid_time": NOW + pd.Timedelta(days=1, hours=12),
        "precip": 5.0, "neige_eau": 4.0,
    }])

    result = WT.ensemble_daily_weather_types(
        pd.DataFrame(globals_), now=NOW, regional_sub=regional)
    row = result.daily.iloc[0]

    assert result.available
    assert row["neigeux"] == 70.0
    assert row["sec"] == 30.0
    assert bool(row["pe_arome"])
    assert "PE-ARPEGE indisponible" in result.regional_reason


def test_pe_arpege_prend_le_relais_a_50_pourcent_sans_pe_arome():
    globals_ = []
    for model in ("ECMWF", "AIFS", "GEFS"):
        globals_.append({
            "model": model, "member": 0, "site": "village",
            "valid_time": NOW + pd.Timedelta(days=3, hours=12),
            "precip": 0.0, "t850": -4.0, "epaisseur": 5250.0,
        })
    arpege = pd.DataFrame([{
        "model": "PE-ARPEGE", "member": 0, "site": "village",
        "valid_time": NOW + pd.Timedelta(days=3, hours=12),
        "precip": 5.0, "neige_eau": 4.0,
    }])
    result = WT.ensemble_daily_weather_types(
        pd.DataFrame(globals_), now=NOW, arpege_sub=arpege)
    row = result.daily.iloc[0]
    assert row["neigeux"] == 50.0
    assert row["sec"] == 50.0
    assert bool(row["pe_arpege"])
    assert row["regional_model"] == "PE-ARPEGE"


def test_pe_arome_reste_prioritaire_sur_arpege_en_cas_de_recouvrement():
    globals_ = pd.DataFrame([{
        "model": model, "member": 0, "site": "village",
        "valid_time": NOW + pd.Timedelta(days=1, hours=12),
        "precip": 0.0, "t850": -4.0, "epaisseur": 5250.0,
    } for model in ("ECMWF", "AIFS", "GEFS")])
    common = {
        "member": 0, "site": "village",
        "valid_time": NOW + pd.Timedelta(days=1, hours=12), "precip": 5.0,
    }
    pe_arome = pd.DataFrame([{
        **common, "model": "PE-AROME", "neige_eau": 4.0}])
    pe_arpege = pd.DataFrame([{
        **common, "model": "PE-ARPEGE", "neige_eau": 0.0}])
    result = WT.ensemble_daily_weather_types(
        globals_, now=NOW, regional_sub=pe_arome, arpege_sub=pe_arpege)
    row = result.daily.iloc[0]
    assert row["neigeux"] == 70.0
    assert row["sec"] == 30.0
    assert bool(row["pe_arome"])
    assert not bool(row["pe_arpege"])
    assert row["regional_model"] == "PE-AROME"


def test_absence_pe_arome_est_expliquee_sans_modifier_les_poids_globaux():
    result = WT.ensemble_daily_weather_types(_ensemble_members(), now=NOW)
    assert result.available
    assert not bool(result.daily.iloc[0]["pe_arome"])
    assert "estimation globale seulement" in result.regional_reason


def test_classification_sommet_est_refusee_avec_motif_explicite():
    result = WT.ensemble_daily_weather_types(_ensemble_members("sommet"), now=NOW)
    assert not result.available
    assert result.daily.empty
    assert "uniquement au village" in result.reason
    assert "sommet" in result.reason


def test_precipitation_nan_n_est_jamais_convertie_en_sec():
    data = _ensemble_members()
    data["precip"] = np.nan
    result = WT.ensemble_daily_weather_types(data, now=NOW)
    assert not result.available
    assert "Précipitation" in result.reason


def _hd_transition_profile():
    rows = []
    for site, t2m, neige in (("village", 1.0, 1.0), ("sommet", -4.0, 4.0)):
        rows.append({
            "fetched_at": NOW - pd.Timedelta(hours=1), "model": "ICON-D2",
            "site": site, "target_datetime": NOW + pd.Timedelta(hours=1),
            "t2m": t2m, "precip": 2.0, "neige": neige,
            "iso0": 1500.0 if site == "village" else np.nan,
        })
    return pd.DataFrame(rows)


def test_coupe_altitude_utilise_lpn_et_non_iso0_brut():
    result = WT.hd_vertical_hourly_profile(_hd_transition_profile(), now=NOW)
    assert result.available
    hourly = result.daily.set_index("altitude_m")
    assert (hourly["lpn_m"] == 1200.0).all()  # iso0 1500 - marge 300
    assert pd.isna(hourly.loc[1100, "neige_cm"])
    assert hourly.loc[1100, "phase"] == "pluie"
    assert hourly.loc[1100, "pluie_mm"] == 2.0
    assert hourly.loc[1100, "unite"] == "mm"
    # 1300 m est sous l'iso 0 brut mais au-dessus de la LPN : la neige DOIT
    # être affichée. Ce cas échouerait avec le mauvais discriminant iso0.
    assert hourly.loc[1300, "neige_cm"] > 0
    assert hourly.loc[1300, "phase"] == "neige"
    assert hourly.loc[1300, "neige_cm"] == 2.0
    assert hourly.loc[1300, "unite"] == "cm"

    summary = WT.hd_daily_amounts(result.daily).set_index("altitude_m")
    assert summary.loc[1100, "pluie_mm"] == 2.0
    assert summary.loc[1300, "neige_cm"] == 2.0

    reference = WT.hd_daily_reference(result.daily).iloc[0]
    assert reference["categorie"] == "pluvieux"
    assert reference["heures_hd"] == 1
    assert bool(reference["partiel"])


def test_coupe_hd_est_strictement_limitee_aux_prochaines_48h():
    near = _hd_transition_profile()
    far = near.copy()
    far["target_datetime"] = NOW + pd.Timedelta(hours=49)
    result = WT.hd_vertical_hourly_profile(pd.concat([near, far]), now=NOW)
    assert result.available
    assert result.daily["valid_time"].max() <= NOW + pd.Timedelta(hours=48)


def test_extrapolation_2000m_borne_le_gradient_observe():
    profile = WT.interpolate_temperature_profile(20.0, -20.0).set_index("altitude_m")
    expected = -20.0 - WT.GRADIENT_ADIABATIQUE_MAX_C_KM * 0.170
    assert abs(profile.loc[2000.0, "t2m_c"] - expected) < 1e-9


def _arome_pi_transition_profile(valid_time=None):
    valid_time = valid_time or NOW + pd.Timedelta(hours=1)
    rows = []
    # Total identique, mais fraction neige croissante avec l'altitude : le
    # niveau 1300 m tombe volontairement dans la bande mixte 40–60 %.
    for site, snow, t2m in (("village", 0.6, 2.0),
                            ("sommet", 1.7, -2.0)):
        rows.append({
            "run_date": NOW, "model": "AROME-PI",
            "kind": "deterministic", "member": 0, "site": site,
            "valid_time": valid_time, "precip": 2.0,
            "neige_eau": snow, "pluie_eau": 2.0 - snow, "t2m": t2m,
        })
    return pd.DataFrame(rows)


def test_arome_pi_interpole_directement_pluie_neige_et_garde_mixte():
    result = WT.arome_pi_vertical_hourly_profile(
        _arome_pi_transition_profile(), now=NOW)
    assert result.available
    profile = result.daily.set_index("altitude_m")
    assert profile.loc[1100, "phase"] == "pluie"
    assert profile.loc[1300, "phase"] == "mixte"
    assert profile.loc[1600, "phase"] == "neige"
    assert profile.loc[2000, "phase"] == "neige"
    assert profile.loc[1300, "neige_cm"] > 0
    assert profile.loc[1300, "pluie_mm"] > 0
    assert (profile["source"] == "AROME-PI").all()
    assert profile["lpn_m"].isna().all()


def test_arome_pi_trop_ancien_ne_remplace_jamais_la_maille_fine():
    old = _arome_pi_transition_profile(valid_time=NOW + pd.Timedelta(hours=1))
    old["run_date"] = NOW - pd.Timedelta(
        hours=WT.AROME_PI_AGE_MAX_H + 0.1)
    result = WT.arome_pi_vertical_hourly_profile(old, now=NOW)
    assert not result.available
    assert "trop ancien" in result.reason


def test_arome_ifs_reutilise_la_phase_directe_sans_double_compter_arome():
    source = _arome_pi_transition_profile()
    source["model"] = SC.AROME_IFS_MODEL
    result = WT.arome_ifs_vertical_hourly_profile(source, now=NOW)

    assert result.available
    assert set(result.daily["source"]) == {SC.AROME_IFS_MODEL}
    profile = result.daily.set_index("altitude_m")
    assert profile.loc[1100, "phase"] == "pluie"
    assert profile.loc[1300, "phase"] == "mixte"
    assert profile.loc[1600, "phase"] == "neige"


def test_priorite_horaire_est_hd_puis_ifs_puis_pi():
    hd = WT.hd_vertical_hourly_profile(_hd_transition_profile(), now=NOW)
    ifs_source = _arome_pi_transition_profile()
    ifs_source["model"] = SC.AROME_IFS_MODEL
    ifs = WT.arome_ifs_vertical_hourly_profile(ifs_source, now=NOW)
    pi = WT.arome_pi_vertical_hourly_profile(
        _arome_pi_transition_profile(), now=NOW)

    with_ifs = WT.combine_vertical_hourly_profiles(hd.daily, ifs.daily)
    combined = WT.combine_vertical_hourly_profiles(with_ifs, pi.daily)

    assert set(with_ifs["source"]) == {SC.AROME_IFS_MODEL}
    assert set(combined["source"]) == {SC.AROME_PI_MODEL}


def test_fusion_horaire_remplace_seulement_les_heures_couvertes_par_pi():
    hd_rows = _hd_transition_profile()
    second = hd_rows.copy()
    second["target_datetime"] = NOW + pd.Timedelta(hours=2)
    hd = WT.hd_vertical_hourly_profile(
        pd.concat([hd_rows, second], ignore_index=True), now=NOW)
    pi = WT.arome_pi_vertical_hourly_profile(
        _arome_pi_transition_profile(), now=NOW)
    merged = WT.combine_vertical_hourly_profiles(hd.daily, pi.daily)

    h1 = merged[merged["valid_time"] == NOW + pd.Timedelta(hours=1)]
    h2 = merged[merged["valid_time"] == NOW + pd.Timedelta(hours=2)]
    assert len(h1) == len(WT.ALTITUDES_PROFIL_M)
    assert len(h2) == len(WT.ALTITUDES_PROFIL_M)
    assert set(h1["source"]) == {"AROME-PI"}
    assert set(h2["source"]) == {"Maille fine HD"}
