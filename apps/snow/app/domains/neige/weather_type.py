# -*- coding: utf-8 -*-
"""Pondération et classification pluie/neige/sec du domaine neige.

Ce module est le point de calibration unique de ces diagnostics. Ses seuils
et pondérations sont des VALEURS DE DÉMARRAGE, **à affiner in situ au fil de
la saison**, comme les seuils d'épaisseur 1000–500 hPa. Les pages ne doivent
pas dupliquer ces nombres : les ajuster ici ne change ni les schémas ni la
mécanique de collecte.

Deux lectures, alignées sur ``snow_config.HORIZON_REGIMES`` :

* H0–H+48 : détail horaire issu de la maille fine aux quatre altitudes, avec
  bilan quotidien pluie/neige ; la phase utilise la limite pluie-neige (LPN),
  jamais le seul iso 0 °C ;
* J0–J+15 : classification de chaque membre et de chaque jour au site village,
  puis probabilité catégorielle par comptage des membres. Sur J0–J+3, elle
  complète la coupe HD en montrant la dispersion du scénario d'ensemble.

Le module ne dépend d'aucune page Streamlit et reste entièrement testable sur
des DataFrames synthétiques.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from apps.snow import snow_config as SC


# --------------------------------------------------------------------------- #
#  Constantes de calibration — à affiner in situ au fil de la saison
# --------------------------------------------------------------------------- #

# En dessous de ce cumul journalier, la sortie d'un membre est assimilée au
# bruit numérique des modèles. La priorité « sec » reste indépendante de la
# température, conformément à l'ordre de classification métier.
PRECIP_BRUIT_MM_JOUR = 0.5
# Le seuil de bruit dit seulement qu'une précipitation existe. Il ne suffit
# pas à qualifier toute la journée de pluvieuse : entre 0,5 et 2 mm, un membre
# décrit plutôt une trace ou une averse faible, classée mixte/incertaine. Cette
# valeur de démarrage est elle aussi à affiner in situ au fil de la saison.
PRECIP_PLUIE_SIGNIFICATIVE_MM_JOUR = 2.0
# PE-AROME fournit directement les cumuls total et neige. Une phase n'est
# forcée que si au moins 60 % du cumul est d'un même type ; la bande 40–60 %
# reste mixte. Valeurs de démarrage, à affiner in situ au fil de la saison.
PE_AROME_FRACTION_NEIGE_MIN = 0.60
PE_AROME_FRACTION_PLUIE_MIN = 0.60
# Le flux HD est horaire : seuil plus fin, juste au-dessus du pas de sortie
# numérique usuel. Une trace < 0,1 mm/h reste affichée comme sèche.
PRECIP_BRUIT_MM_HEURE = 0.1

# Un cycle AROME-PI plus vieux que cette valeur ne doit jamais prendre la
# priorité sur la maille fine courante. Le job tourne toutes les deux heures ;
# trois heures laissent une marge de publication sans masquer une panne.
# Valeur à affiner in situ au fil de la saison avec la disponibilité réelle.
AROME_PI_AGE_MAX_H = 3.0
# AROME-IFS est produit toutes les six heures et disponible plus tardivement.
# Neuf heures couvrent publication + polling 2 h sans laisser un vieux cycle
# masquer durablement AROME France/ICON-D2. À affiner in situ sur les heures de
# mise à disposition observées pendant la saison.
AROME_IFS_AGE_MAX_H = 9.0

# La phase directe d'AROME-PI est qualifiée par la part de neige dans le cumul
# total. Entre 40 et 60 %, la précipitation reste explicitement mixte : une
# interpolation verticale ne justifie pas de forcer pluie ou neige.
AROME_PI_FRACTION_NEIGE_MIN = 0.60
AROME_PI_FRACTION_PLUIE_MIN = 0.60

# Conversion volontairement lisible pour la coupe : 1 mm de précipitation
# liquide équivalente devient 1 cm de neige au-dessus de la LPN. Ce ratio 10:1
# est une convention de départ ; la densité réelle varie fortement selon
# l'épisode et devra elle aussi être affinée in situ.
RATIO_NEIGE_CM_PAR_MM = 1.0

# Seuils d'épaisseur 1000–500 hPa : valeurs de démarrage transposées du
# repère 528 dam en plaine. À recalibrer en fin de saison avec la page
# Convergence. Ils vivent ici pour que toute la classification soit ajustable
# sans toucher à logic.py ni aux pages.
EPAISSEUR_NEIGE_M = {"village": 5340.0, "sommet": 5400.0}

# L'épaisseur est un modulateur de la masse d'air, jamais un veto. Sa
# contribution passe progressivement de froide à douce sur cette bande.
EPAISSEUR_TRANSITION_M = 120.0
POIDS_T850 = 0.70
POIDS_EPAISSEUR = 0.30
SCORE_FROID_NEIGE_MIN = 0.70
SCORE_FROID_PLUIE_MAX = 0.20
SCORE_SIGNAL_MANQUANT = 0.50

# Profil vertical demandé par la Vue d'ensemble.
ALTITUDES_PROFIL_M = (1100, 1300, 1600, 2000)
SITE_VILLAGE_M = 1100.0
SITE_SOMMET_M = 1830.0

# Seule l'extrapolation au-dessus du sommet est bornée. L'interpolation entre
# les deux mesures conserve le gradient observé, y compris une inversion.
# 9,8 °C/km correspond au gradient adiabatique sec maximal de sécurité.
GRADIENT_ADIABATIQUE_MAX_C_KM = 9.8

# La marge de LPN est pour l'instant PARTAGÉE avec le flux ensemble. Elle a
# été validée sur sa maille plus grossière et doit être à recalibrer en fin
# de saison sur AROME HD/ICON-D2 avant d'envisager une constante distincte.
LPN_MARGE_HD_M = SC.LPN_MARGE_M

CLASSIFICATION_SITE = "village"
CATEGORIES = ("neigeux", "pluvieux", "sec", "mixte")

# Pondération réservée au TYPE DE TEMPS LOCAL (précipitation + phase).
# Les trois ensembles n'ont ni la même taille ni la même pertinence pour
# représenter l'orographie de la vallée de l'Arve. On classe toujours chaque
# membre brut, puis on calcule une proportion par modèle avant de combiner les
# modèles avec ces poids. Ainsi, 51 membres ne valent pas mécaniquement plus
# que 31. GEFS reste volontairement secondaire à courte échéance pour la
# pluie/neige locale, mais retrouve du poids quand l'horizon devient surtout
# un diagnostic de masse d'air. NE PAS réutiliser ces poids pour minorer GEFS
# dans les graphes t850/épaisseur : il y reste un signal synoptique pertinent.
# Valeurs de démarrage, à affiner in situ au fil de la saison.
POIDS_MODELES_TYPE_TEMPS = (
    {"max_j": SC.HORIZON_REGIMES[0]["max_j"],
     "weights": {"ECMWF": 0.425, "AIFS": 0.425, "GEFS": 0.15}},
    {"max_j": SC.HORIZON_REGIMES[1]["max_j"],
     "weights": {"ECMWF": 0.40, "AIFS": 0.40, "GEFS": 0.20}},
    {"max_j": SC.HORIZON_REGIMES[2]["max_j"],
     "weights": {"ECMWF": 0.35, "AIFS": 0.35, "GEFS": 0.30}},
)

# Part absolue réservée au meilleur ensemble régional disponible. PE-AROME
# reste prioritaire ; PE-ARPEGE n'est utilisé que lorsque PE-AROME ne couvre
# plus la journée. Le solde est ventilé entre les globaux selon la table
# d'horizon ci-dessus. Valeurs à affiner in situ au fil de la saison.
POIDS_REGIONAL_TYPE_TEMPS = {"PE-AROME": 0.70, "PE-ARPEGE": 0.50}
PRIORITE_MODELES_REGIONAUX = ("PE-AROME", "PE-ARPEGE")

# Tuile grand public « Changement de temps » : formulation d'un régime météo à
# venir à partir des signaux DÉJÀ calculés (proportions de type de temps de
# ensemble_daily_weather_types + bascule de pression logic.pmsl_bascule). Aucun
# nouveau calcul météo — seulement des seuils de formulation/catégorisation,
# du même esprit que les autres seuils ci-dessus, à affiner in situ au fil de
# la saison. Les parts lues sont des MOYENNES sur les premiers jours (fenêtre
# REGIME_FENETRE_J), où l'incertitude reste faible : le régime décrit une
# tendance de fond, jamais une prévision jour par jour.
REGIME_FENETRE_J = 4              # nb de jours moyennés pour lire le régime
REGIME_SEC_MIN = 0.50            # part « sec » moyenne → tendance anticyclonique
REGIME_PERTURBE_WET_MIN = 0.45   # part neige+pluie moyenne → temps perturbé
REGIME_INCERTAIN_MIXTE_MIN = 0.40  # part « mixte » moyenne → scénario ambigu
REGIME_CONSENSUS_MIN = 0.45      # sous ce plus fort accord, modèles jugés divergents
REGIME_TENDANCE_MARGE = 0.12     # écart neige−pluie requis pour oser une tendance


@dataclass(frozen=True)
class WeatherTypeResult:
    """Répartition journalière ou indisponibilité explicitement motivée."""

    daily: pd.DataFrame
    available: bool
    reason: str | None = None
    regional_reason: str | None = None


@dataclass(frozen=True)
class VerticalProfileResult:
    """Coupe verticale HD ou indisponibilité motivée."""

    daily: pd.DataFrame
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class RegimeMeteo:
    """Régime météo à venir formulé pour la tuile grand public.

    ``key`` classe le régime (sec / perturbé / incertain / variable) ;
    ``label`` est le texte principal, ``detail`` la ligne d'appui, ``tendance``
    la tendance de phase possible (« neige » / « pluie » / None) — jamais une
    affirmation de précipitation certaine.
    """

    key: str
    label: str
    detail: str
    tendance: str | None = None


def lpn_from_iso0(iso0_m):
    """Limite pluie-neige estimée, jamais l'iso 0 °C brut."""
    if iso0_m is None or pd.isna(iso0_m):
        return np.nan
    return float(iso0_m) - LPN_MARGE_HD_M


def altitude_is_snow(altitude_m, iso0_m):
    """Vrai si l'altitude est au-dessus de la LPN estimée."""
    lpn_m = lpn_from_iso0(iso0_m)
    if pd.isna(lpn_m):
        return None
    return float(altitude_m) >= lpn_m


def interpolate_temperature_profile(t_village_c, t_sommet_c,
                                    altitudes=ALTITUDES_PROFIL_M):
    """Interpole T2m entre 1100 et 1830 m et extrapole prudemment à 2000 m.

    Le gradient observé est conservé entre les deux points mesurés. Hors de
    cet intervalle, sa valeur est bornée à ``±9,8 °C/km`` afin qu'un écart
    ponctuel aberrant ne produise pas une extrapolation physiquement extrême.
    """
    altitudes = tuple(float(a) for a in altitudes)
    if pd.isna(t_village_c) or pd.isna(t_sommet_c):
        return pd.DataFrame({"altitude_m": altitudes, "t2m_c": np.nan})

    observed_c_km = ((float(t_sommet_c) - float(t_village_c))
                     / (SITE_SOMMET_M - SITE_VILLAGE_M) * 1000.0)
    bounded_c_km = float(np.clip(observed_c_km,
                                 -GRADIENT_ADIABATIQUE_MAX_C_KM,
                                 GRADIENT_ADIABATIQUE_MAX_C_KM))
    values = []
    for altitude in altitudes:
        if altitude <= SITE_SOMMET_M:
            value = float(t_village_c) + observed_c_km * (
                altitude - SITE_VILLAGE_M) / 1000.0
        else:
            value = float(t_sommet_c) + bounded_c_km * (
                altitude - SITE_SOMMET_M) / 1000.0
        values.append(value)
    return pd.DataFrame({"altitude_m": altitudes, "t2m_c": values})


def _interpolate_amount(village, sommet, altitude_m):
    """Interpolation prudente d'un cumul ; pas d'extrapolation croissante."""
    if altitude_m < SITE_VILLAGE_M or altitude_m > SITE_SOMMET_M:
        if altitude_m > SITE_SOMMET_M and pd.notna(sommet):
            return float(sommet)
        return float(village) if pd.notna(village) else np.nan
    if pd.isna(village) or pd.isna(sommet):
        return np.nan
    ratio = (altitude_m - SITE_VILLAGE_M) / (SITE_SOMMET_M - SITE_VILLAGE_M)
    return float(village) + ratio * (float(sommet) - float(village))


def _latest_hd_rows(hd_df):
    """Dernière collecte exacte de chaque modèle/site/échéance."""
    keys = ["model", "site", "target_datetime"]
    return (hd_df.sort_values("fetched_at")
                 .drop_duplicates(subset=keys, keep="last")
                 .reset_index(drop=True))


def hd_vertical_hourly_profile(hd_df, now=None):
    """Coupe HD horaire des prochaines 48 h aux quatre altitudes.

    La précipitation de chaque heure est interpolée entre village et sommet.
    Au-dessus de la LPN elle est convertie en neige ; en dessous, elle reste
    pluie. LPN et iso 0 servent au calcul et au survol, mais ne sont plus tracés
    sur le même axe : ils ne doivent pas écraser la lecture des quatre niveaux.
    """
    columns = ["valid_time", "date", "lead_h", "altitude_m", "quantite",
               "unite", "phase", "neige_cm", "pluie_mm", "t2m_c",
               "precip_mm", "lpn_m", "iso0_m", "n_modeles", "source"]
    empty = pd.DataFrame(columns=columns)
    if hd_df is None or hd_df.empty:
        return VerticalProfileResult(empty, False, "Flux HD indisponible.")
    required = {"fetched_at", "model", "site", "target_datetime", "t2m",
                "precip", "iso0"}
    missing = sorted(required - set(hd_df.columns))
    if missing:
        return VerticalProfileResult(
            empty, False, f"Variables HD absentes : {', '.join(missing)}.")
    if not hd_df["precip"].notna().any():
        return VerticalProfileResult(
            empty, False,
            "Précipitation HD encore absente de l'historique ; la coupe ne peut "
            "pas distinguer un temps sec d'un épisode pluvieux.")

    now = pd.Timestamp(now or pd.Timestamp.now())
    latest = _latest_hd_rows(hd_df)
    latest = latest[(latest["target_datetime"] >= now)
                    & (latest["target_datetime"] <= now + pd.Timedelta(hours=48))]
    if latest.empty:
        return VerticalProfileResult(empty, False,
                                     "Aucune échéance HD sur les prochaines 48 h.")

    model_rows = []
    for (model, valid_time), group in latest.groupby(["model", "target_datetime"]):
        village_rows = group[group["site"] == "village"]
        sommet_rows = group[group["site"] == "sommet"]
        if village_rows.empty or sommet_rows.empty:
            continue
        village = village_rows.iloc[-1]
        sommet = sommet_rows.iloc[-1]
        precip_v = pd.to_numeric(pd.Series([village["precip"]]),
                                 errors="coerce").iloc[0]
        precip_s = pd.to_numeric(pd.Series([sommet["precip"]]),
                                 errors="coerce").iloc[0]
        if pd.isna(precip_v) and pd.isna(precip_s):
            continue

        iso0_m = pd.to_numeric(pd.Series([village["iso0"]]),
                               errors="coerce").iloc[0]
        lpn_m = lpn_from_iso0(iso0_m)
        t_village = pd.to_numeric(pd.Series([village["t2m"]]),
                                  errors="coerce").iloc[0]
        t_sommet = pd.to_numeric(pd.Series([sommet["t2m"]]),
                                 errors="coerce").iloc[0]
        temp = interpolate_temperature_profile(t_village, t_sommet) \
            .set_index("altitude_m")["t2m_c"]

        for altitude in ALTITUDES_PROFIL_M:
            precip_alt = _interpolate_amount(precip_v, precip_s, altitude)
            model_rows.append({
                "model": model, "valid_time": valid_time,
                "altitude_m": altitude,
                "t2m_c": temp.get(float(altitude), np.nan),
                "precip_mm": precip_alt, "lpn_m": lpn_m, "iso0_m": iso0_m,
            })

    if not model_rows:
        return VerticalProfileResult(
            empty, False,
            "Précipitation disponible, mais LPN HD insuffisante pour "
            "construire la coupe verticale.")

    by_model = pd.DataFrame(model_rows)
    if not by_model["lpn_m"].notna().any():
        return VerticalProfileResult(
            empty, False,
            "Précipitation disponible, mais iso 0/LPN HD absente pour la "
            "coupe verticale.")
    hourly = (by_model.groupby(["valid_time", "altitude_m"], as_index=False)
                      .agg(t2m_c=("t2m_c", "mean"),
                           precip_mm=("precip_mm", "mean"),
                           lpn_m=("lpn_m", "mean"),
                           iso0_m=("iso0_m", "mean"),
                           n_modeles=("model", "nunique")))
    # Discriminant PHYSIQUE : LPN = iso0 - marge. Ne jamais comparer
    # directement l'altitude à iso0_m.
    hourly["phase"] = np.where(
        hourly["precip_mm"] < PRECIP_BRUIT_MM_HEURE, "sec",
        np.where(hourly["altitude_m"] >= hourly["lpn_m"], "neige", "pluie"))
    hourly["neige_cm"] = np.where(
        hourly["phase"] == "neige",
        hourly["precip_mm"] * RATIO_NEIGE_CM_PAR_MM, np.nan)
    hourly["pluie_mm"] = np.where(
        hourly["phase"] == "pluie", hourly["precip_mm"], np.nan)
    hourly["quantite"] = np.select(
        [hourly["phase"] == "neige", hourly["phase"] == "pluie"],
        [hourly["neige_cm"], hourly["pluie_mm"]], default=0.0)
    hourly["unite"] = np.where(hourly["phase"] == "neige", "cm", "mm")
    hourly["date"] = pd.to_datetime(hourly["valid_time"]).dt.normalize()
    hourly["lead_h"] = ((hourly["valid_time"] - now)
                        / pd.Timedelta(hours=1)).round(1)
    hourly["source"] = "Maille fine HD"
    return VerticalProfileResult(hourly[columns], True)


def _direct_mf_vertical_hourly_profile(
        source_df, *, model, horizon_h, age_max_h, label, now=None):
    """Profil d'un AROME direct fondé sur ses cumuls total et neige.

    Les cumuls total et neige sont interpolés séparément entre village et
    sommet, puis maintenus constants au-dessus du sommet : une extrapolation
    croissante de précipitation serait artificielle. La neige affichée vient
    du cumul neige du modèle, pas de l'iso 0 ni d'une température seuil. La LPN
    demeure le discriminant du flux HD utilisé au-delà de cette couverture.
    """
    columns = ["valid_time", "date", "lead_h", "altitude_m", "quantite",
               "unite", "phase", "neige_cm", "pluie_mm", "t2m_c",
               "precip_mm", "lpn_m", "iso0_m", "n_modeles", "source"]
    empty = pd.DataFrame(columns=columns)
    if source_df is None or source_df.empty:
        return VerticalProfileResult(empty, False, f"{label} indisponible.")
    required = {"run_date", "model", "kind", "site", "valid_time",
                "precip", "neige_eau", "t2m"}
    missing = sorted(required - set(source_df.columns))
    if missing:
        return VerticalProfileResult(
            empty, False, f"Variables {label} absentes : {', '.join(missing)}.")

    now = pd.Timestamp(now or pd.Timestamp.now())
    direct = source_df[(source_df["model"] == model)
                       & (source_df["kind"] == "deterministic")].copy()
    if direct.empty:
        return VerticalProfileResult(empty, False, f"Cycle {label} absent.")
    run_date = pd.to_datetime(direct["run_date"]).max()
    age_h = (now - run_date) / pd.Timedelta(hours=1)
    if age_h > age_max_h:
        return VerticalProfileResult(
            empty, False,
            f"{label} trop ancien ({age_h:.1f} h) : priorité locale désactivée.")
    direct = direct[(pd.to_datetime(direct["run_date"]) == run_date)
                    & (pd.to_datetime(direct["valid_time"]) >= now)
                    & (pd.to_datetime(direct["valid_time"])
                       <= now + pd.Timedelta(hours=horizon_h))]
    if direct.empty:
        return VerticalProfileResult(
            empty, False, f"Aucune échéance {label} future disponible.")

    rows = []
    for valid_time, group in direct.groupby("valid_time"):
        village_rows = group[group["site"] == "village"]
        sommet_rows = group[group["site"] == "sommet"]
        if village_rows.empty or sommet_rows.empty:
            continue
        village, sommet = village_rows.iloc[-1], sommet_rows.iloc[-1]
        temp = interpolate_temperature_profile(village["t2m"], sommet["t2m"]) \
            .set_index("altitude_m")["t2m_c"]
        for altitude in ALTITUDES_PROFIL_M:
            total = _interpolate_amount(
                village["precip"], sommet["precip"], altitude)
            snow = _interpolate_amount(
                village["neige_eau"], sommet["neige_eau"], altitude)
            if pd.isna(total) or pd.isna(snow):
                continue
            total = max(float(total), 0.0)
            snow = float(np.clip(float(snow), 0.0, total))
            rain = max(total - snow, 0.0)
            if total < PRECIP_BRUIT_MM_HEURE:
                phase = "sec"
            else:
                snow_fraction = snow / total
                rain_fraction = rain / total
                if snow_fraction >= AROME_PI_FRACTION_NEIGE_MIN:
                    phase = "neige"
                elif rain_fraction >= AROME_PI_FRACTION_PLUIE_MIN:
                    phase = "pluie"
                else:
                    phase = "mixte"
            snow_cm = snow * RATIO_NEIGE_CM_PAR_MM
            if phase == "neige":
                quantity, unit = snow_cm, "cm"
            elif phase == "pluie":
                quantity, unit = rain, "mm"
            elif phase == "mixte":
                quantity, unit = total, "mm éq. eau"
            else:
                quantity, unit = 0.0, "mm"
            rows.append({
                "valid_time": pd.Timestamp(valid_time),
                "altitude_m": altitude, "quantite": quantity,
                "unite": unit, "phase": phase, "neige_cm": snow_cm,
                "pluie_mm": rain, "t2m_c": temp.get(float(altitude), np.nan),
                "precip_mm": total, "lpn_m": np.nan, "iso0_m": np.nan,
                "n_modeles": 1, "source": label,
            })
    if not rows:
        return VerticalProfileResult(
            empty, False, f"{label} incomplet aux deux altitudes de référence.")
    hourly = pd.DataFrame(rows)
    hourly["date"] = pd.to_datetime(hourly["valid_time"]).dt.normalize()
    hourly["lead_h"] = ((hourly["valid_time"] - now)
                        / pd.Timedelta(hours=1)).round(1)
    return VerticalProfileResult(hourly[columns], True)


def arome_pi_vertical_hourly_profile(pi_df, now=None):
    """Profil AROME-PI H+1–H+6, prioritaire sur toute autre source."""
    return _direct_mf_vertical_hourly_profile(
        pi_df, model=SC.AROME_PI_MODEL, horizon_h=SC.AROME_PI_HORIZON_H,
        age_max_h=AROME_PI_AGE_MAX_H, label=SC.AROME_PI_MODEL, now=now)


def arome_ifs_vertical_hourly_profile(ifs_df, now=None):
    """Profil AROME-IFS H+1–H+45, prioritaire sur la maille Open-Meteo."""
    return _direct_mf_vertical_hourly_profile(
        ifs_df, model=SC.AROME_IFS_MODEL, horizon_h=SC.AROME_IFS_HORIZON_H,
        age_max_h=AROME_IFS_AGE_MAX_H, label=SC.AROME_IFS_MODEL, now=now)


def combine_vertical_hourly_profiles(hd_profile, pi_profile):
    """Superpose un profil prioritaire au profil de repli, sans doublon.

    Une ligne du second argument prend priorité pour le même couple
    heure/altitude. Cela permet d'appliquer successivement IFS sur la maille
    Open-Meteo, puis PI sur IFS, sans moyenner deux scénarios déterministes.
    """
    hd = pd.DataFrame() if hd_profile is None else hd_profile.copy()
    pi = pd.DataFrame() if pi_profile is None else pi_profile.copy()
    if pi.empty:
        return hd
    if hd.empty:
        return pi.sort_values(["valid_time", "altitude_m"]).reset_index(drop=True)
    keys = ["valid_time", "altitude_m"]
    pi_keys = pd.MultiIndex.from_frame(pi[keys])
    hd_keys = pd.MultiIndex.from_frame(hd[keys])
    merged = pd.concat([hd.loc[~hd_keys.isin(pi_keys)], pi], ignore_index=True)
    return merged.sort_values(keys).reset_index(drop=True)


def hd_daily_amounts(hourly_profile):
    """Bilan civil par jour et altitude à partir des pas horaires HD."""
    columns = ["date", "altitude_m", "pluie_mm", "neige_cm"]
    if hourly_profile is None or hourly_profile.empty:
        return pd.DataFrame(columns=columns)
    summary = (hourly_profile.groupby(["date", "altitude_m"], as_index=False)
                             .agg(pluie_mm=("pluie_mm", "sum"),
                                  neige_cm=("neige_cm", "sum")))
    return summary[columns]


def hd_daily_reference(hourly_profile, altitude_m=SITE_VILLAGE_M):
    """Verdict HD journalier au village pour contextualiser l'ensemble.

    Ce diagnostic ne remplace jamais le comptage des membres : il expose le
    scénario maille fine indépendant sur sa seule fenêtre de 48 h. Les jours
    couverts moins de 20 heures sont marqués partiels pour ne pas transformer
    une demi-journée sèche en prévision sèche sur 24 heures.
    """
    columns = ["date", "categorie", "pluie_mm", "neige_cm", "heures_hd",
               "partiel"]
    if hourly_profile is None or hourly_profile.empty:
        return pd.DataFrame(columns=columns)
    level = hourly_profile[
        pd.to_numeric(hourly_profile["altitude_m"], errors="coerce")
        == float(altitude_m)
    ].copy()
    if level.empty:
        return pd.DataFrame(columns=columns)
    daily = (level.groupby("date", as_index=False)
                  .agg(pluie_mm=("pluie_mm", "sum"),
                       neige_cm=("neige_cm", "sum"),
                       heures_hd=("valid_time", "nunique")))

    def _category(row):
        equivalent_mm = (float(row["pluie_mm"])
                         + float(row["neige_cm"]) / RATIO_NEIGE_CM_PAR_MM)
        if equivalent_mm < PRECIP_BRUIT_MM_JOUR:
            return "sec"
        has_rain = row["pluie_mm"] >= PRECIP_BRUIT_MM_HEURE
        has_snow = row["neige_cm"] >= (
            PRECIP_BRUIT_MM_HEURE * RATIO_NEIGE_CM_PAR_MM)
        if has_rain and has_snow:
            return "mixte"
        if has_snow:
            return "neigeux"
        if row["pluie_mm"] >= PRECIP_PLUIE_SIGNIFICATIVE_MM_JOUR:
            return "pluvieux"
        return "mixte"

    daily["categorie"] = daily.apply(_category, axis=1)
    daily["partiel"] = daily["heures_hd"] < 20
    return daily[columns]


def _linear_cold_score(value, cold_limit, warm_limit):
    """1 côté froid, 0 côté doux, interpolation continue entre les deux."""
    if value is None or pd.isna(value):
        return SCORE_SIGNAL_MANQUANT
    return float(np.clip((warm_limit - float(value))
                         / (warm_limit - cold_limit), 0.0, 1.0))


def cold_score(t850_c, epaisseur_m, site_code=CLASSIFICATION_SITE):
    """Score de masse d'air : t850 dominant, épaisseur non bloquante.

    La couche 1000–500 hPa est trop profonde pour décrire seule les couches de
    fusion proches du relief. Elle module donc le diagnostic t850 sur une
    transition continue, sans rendre la neige physiquement impossible.
    """
    t850 = _linear_cold_score(
        t850_c, SC.SEUIL_T850_NEIGE[site_code], SC.SEUIL_T850_REDOUX)
    epaisseur = _linear_cold_score(
        epaisseur_m, EPAISSEUR_NEIGE_M[site_code],
        EPAISSEUR_NEIGE_M[site_code] + EPAISSEUR_TRANSITION_M)
    return POIDS_T850 * t850 + POIDS_EPAISSEUR * epaisseur


def classify_member_day(precip_mm, t850_c, epaisseur_m,
                        site_code=CLASSIFICATION_SITE):
    """Classe un membre/jour dans l'ordre sec → neige → pluie → mixte.

    ``None`` signifie que la précipitation est inconnue : ce membre n'entre pas
    au dénominateur. Un discriminant thermique manquant avec précipitation
    présente reste au contraire explicitement ``mixte``.
    """
    if site_code != CLASSIFICATION_SITE:
        raise ValueError("La classification ensemble n'est définie qu'au site village.")
    if precip_mm is None or pd.isna(precip_mm):
        return None
    if float(precip_mm) < PRECIP_BRUIT_MM_JOUR:
        return "sec"
    # t850 est le signal primaire disponible à tous les membres. Sans lui,
    # l'épaisseur profonde seule ne justifie jamais une phase forcée.
    if t850_c is None or pd.isna(t850_c):
        return "mixte"
    score = cold_score(t850_c, epaisseur_m, site_code)
    if score >= SCORE_FROID_NEIGE_MIN:
        return "neigeux"
    # Une trace chaude n'est pas une « journée pluvieuse ». Elle reste visible
    # dans la zone mixte/incertaine, distincte du sec comme de la pluie établie.
    if float(precip_mm) < PRECIP_PLUIE_SIGNIFICATIVE_MM_JOUR:
        return "mixte"
    if float(t850_c) >= SC.SEUIL_T850_REDOUX \
            and score <= SCORE_FROID_PLUIE_MAX:
        return "pluvieux"
    return "mixte"


def classify_pe_arome_day(precip_mm, snow_water_mm):
    """Classe un membre régional depuis sa phase microphysique directe.

    PE-AROME et PE-ARPEGE fournissent total et neige : aucun proxy
    t850/épaisseur n'est nécessaire. Une composante manquante reste non
    classée, jamais transformée en sec/pluie.
    """
    if precip_mm is None or pd.isna(precip_mm):
        return None
    precip = float(precip_mm)
    if precip < PRECIP_BRUIT_MM_JOUR:
        return "sec"
    if snow_water_mm is None or pd.isna(snow_water_mm):
        return None
    snow = float(np.clip(float(snow_water_mm), 0.0, precip))
    snow_fraction = snow / precip
    rain_fraction = 1.0 - snow_fraction
    if snow_fraction >= PE_AROME_FRACTION_NEIGE_MIN:
        return "neigeux"
    if precip < PRECIP_PLUIE_SIGNIFICATIVE_MM_JOUR:
        return "mixte"
    if rain_fraction >= PE_AROME_FRACTION_PLUIE_MIN:
        return "pluvieux"
    return "mixte"


def _precip_weighted(group, column):
    values = pd.to_numeric(group[column], errors="coerce")
    weights = pd.to_numeric(group["precip"], errors="coerce")
    valid = values.notna() & weights.notna() & (weights > 0)
    if not valid.any() or weights[valid].sum() <= 0:
        return np.nan
    return float(np.average(values[valid], weights=weights[valid]))


def weather_type_model_weights(jour, available_models):
    """Poids normalisés des modèles disponibles pour un jour d'horizon.

    Un modèle momentanément absent ne devient jamais implicitement du sec :
    son poids est redistribué entre les seuls modèles effectivement classés.
    Un nouveau label non calibré est refusé explicitement afin qu'une future
    source régionale ne soit pas agrégée avec un poids arbitraire.
    """
    regime = next((item for item in POIDS_MODELES_TYPE_TEMPS
                   if int(jour) <= item["max_j"]),
                  POIDS_MODELES_TYPE_TEMPS[-1])
    available = list(dict.fromkeys(str(model) for model in available_models))
    calibrated = set(regime["weights"]) | set(POIDS_REGIONAL_TYPE_TEMPS)
    unknown = sorted(set(available) - calibrated)
    if unknown:
        raise ValueError(
            "Poids de type de temps non calibré pour : " + ", ".join(unknown))
    global_models = [model for model in available if model in regime["weights"]]
    regional_models = [model for model in PRIORITE_MODELES_REGIONAUX
                       if model in available]
    active_regional = regional_models[0] if regional_models else None
    if not global_models and active_regional is None:
        return {}
    weights = {model: 0.0 for model in available}
    if active_regional is None:
        global_share = 1.0
    elif not global_models:
        weights[active_regional] = 1.0
        return weights
    else:
        weights[active_regional] = POIDS_REGIONAL_TYPE_TEMPS[active_regional]
        global_share = 1.0 - weights[active_regional]
    raw_global_total = sum(regime["weights"][model] for model in global_models)
    if raw_global_total > 0:
        for model in global_models:
            weights[model] = (regime["weights"][model] / raw_global_total
                              * global_share)
    return weights


def ensemble_daily_weather_types(sub_site, now=None, regional_sub=None,
                                 arpege_sub=None):
    """Classe les membres puis combine des proportions pondérées par modèle.

    Le seul site admis est ``village`` : ``precip``, t850 et épaisseur y sont
    colocalisés. Tout autre site produit un résultat indisponible avec motif,
    de sorte qu'aucune classification sommet ne puisse se produire en silence.
    Chaque membre conserve exactement une voix AU SEIN de son modèle. Les
    proportions par modèle sont ensuite combinées selon l'horizon : ce second
    niveau empêche la taille d'un ensemble et la maille globale de GEFS de
    dominer silencieusement un phénomène local de montagne. ``n_non_classes``
    expose l'historique encore à NaN.
    """
    columns = ["date", "jour", *CATEGORIES, "n_classes", "n_non_classes",
               "pe_arome", "pe_arpege", "regional_model"]
    empty = pd.DataFrame(columns=columns)
    if sub_site is None or sub_site.empty:
        return WeatherTypeResult(empty, False, "Données d'ensemble absentes.")
    required = {"model", "member", "site", "valid_time", "precip", "t850",
                "epaisseur"}
    missing = sorted(required - set(sub_site.columns))
    if missing:
        return WeatherTypeResult(
            empty, False, f"Variables ensemble absentes : {', '.join(missing)}.")
    sites = set(sub_site["site"].dropna().unique())
    if sites != {CLASSIFICATION_SITE}:
        return WeatherTypeResult(
            empty, False,
            "Classification disponible uniquement au village : la "
            "précipitation ensemble n'est pas collectée au sommet.")
    if not sub_site["precip"].notna().any():
        return WeatherTypeResult(
            empty, False,
            "Précipitation ensemble encore absente de l'historique ; aucune "
            "classification sec/pluie/neige fiable n'est possible.")

    now = pd.Timestamp(now or pd.Timestamp.now())
    today = now.normalize()
    work = sub_site[sub_site["valid_time"] >= now].copy()
    work["date"] = pd.to_datetime(work["valid_time"]).dt.normalize()
    work["jour"] = (work["date"] - today).dt.days
    work = work[(work["jour"] >= 0)
                & (work["jour"] <= SC.HORIZON_REGIMES[-1]["max_j"])]
    if work.empty:
        return WeatherTypeResult(empty, False,
                                 "Aucune échéance ensemble future.")

    members = []
    keys = ["model", "member", "date", "jour"]
    for key, group in work.groupby(keys, dropna=False):
        precip = pd.to_numeric(group["precip"], errors="coerce").sum(min_count=1)
        t850 = _precip_weighted(group, "t850")
        epaisseur = _precip_weighted(group, "epaisseur")
        category = classify_member_day(precip, t850, epaisseur)
        members.append({**dict(zip(keys, key)), "categorie": category})

    regional_reasons = []

    def _append_regional(frame, model_label):
        """Ajoute les votes directs d'une source ou expose son indisponibilité."""
        if frame is None or frame.empty:
            regional_reasons.append(f"{model_label} indisponible")
            return
        regional_required = {"model", "member", "site", "valid_time",
                             "precip", "neige_eau"}
        regional_missing = sorted(regional_required - set(frame.columns))
        regional_sites = set(frame.get("site", pd.Series(dtype=object))
                              .dropna().unique())
        if regional_missing:
            regional_reasons.append(
                f"{model_label} non classé : variables absentes : "
                + ", ".join(regional_missing))
        elif regional_sites != {CLASSIFICATION_SITE}:
            regional_reasons.append(
                f"{model_label} non classé : classification strictement "
                "limitée au village")
        else:
            regional = frame[frame["model"] == model_label].copy()
            if regional.empty:
                regional_reasons.append(
                    f"{model_label} absent du parquet régional")
                return
            regional = regional[regional["valid_time"] >= now]
            regional["date"] = pd.to_datetime(regional["valid_time"]).dt.normalize()
            regional["jour"] = (regional["date"] - today).dt.days
            regional = regional[(regional["jour"] >= 0)
                                & (regional["jour"]
                                   <= SC.HORIZON_REGIMES[-1]["max_j"])]
            for key, group in regional.groupby(keys, dropna=False):
                # Une ligne PE = cumul roulant des 24 h précédentes. Si une
                # duplication d'archive survient, on garde la dernière valeur
                # plutôt que de sommer deux fois la même fenêtre.
                last = group.sort_values("valid_time").iloc[-1]
                category = classify_pe_arome_day(
                    pd.to_numeric(pd.Series([last["precip"]]),
                                  errors="coerce").iloc[0],
                    pd.to_numeric(pd.Series([last["neige_eau"]]),
                                  errors="coerce").iloc[0])
                members.append({**dict(zip(keys, key)), "categorie": category})

    _append_regional(regional_sub, SC.PE_AROME_MODEL)
    _append_regional(arpege_sub, SC.PE_ARPEGE_MODEL)
    member_daily = pd.DataFrame(members)

    # Sur une journée couverte par les deux sources, PE-AROME est le seul
    # ensemble régional actif : sa maille 0,025° est plus pertinente pour le
    # relief local que PE-ARPEGE 0,25°. ARPEGE prend le relais, il ne dilue pas
    # AROME dans une moyenne régionale opaque.
    pe_arome_dates = set(member_daily.loc[
        (member_daily["model"] == SC.PE_AROME_MODEL)
        & member_daily["categorie"].notna(), "date"])
    if pe_arome_dates:
        member_daily = member_daily[~(
            (member_daily["model"] == SC.PE_ARPEGE_MODEL)
            & member_daily["date"].isin(pe_arome_dates))]
    regional_reason = (" ; ".join(regional_reasons) +
                       " : repli sur les sources disponibles ; estimation "
                       "globale seulement si aucun régional ne couvre le jour, "
                       "jamais assimilation au sec.") \
        if regional_reasons else None

    rows = []
    for (date, jour), group in member_daily.groupby(["date", "jour"]):
        n_classified = int(group["categorie"].notna().sum())
        n_unclassified = int(group["categorie"].isna().sum())
        if n_classified == 0:
            continue
        model_shares = []
        for model, model_group in group.groupby("model"):
            classified = model_group["categorie"].dropna()
            if classified.empty:
                continue
            counts = classified.value_counts()
            model_shares.append({
                "model": model,
                **{category: float(counts.get(category, 0) / len(classified))
                   for category in CATEGORIES},
            })
        if not model_shares:
            continue
        model_shares = pd.DataFrame(model_shares)
        try:
            weights = weather_type_model_weights(jour, model_shares["model"])
        except ValueError as exc:
            return WeatherTypeResult(empty, False, str(exc))
        has_pe_arome = bool(((group["model"] == SC.PE_AROME_MODEL)
                             & group["categorie"].notna()).any())
        has_pe_arpege = bool(((group["model"] == SC.PE_ARPEGE_MODEL)
                              & group["categorie"].notna()).any())
        regional_model = (SC.PE_AROME_MODEL if has_pe_arome else
                          SC.PE_ARPEGE_MODEL if has_pe_arpege else None)
        row = {"date": date, "jour": int(jour), "n_classes": n_classified,
               "n_non_classes": n_unclassified,
               "pe_arome": has_pe_arome, "pe_arpege": has_pe_arpege,
               "regional_model": regional_model}
        for category in CATEGORIES:
            row[category] = round(float(sum(
                share[category] * weights[share["model"]]
                for share in model_shares.to_dict("records")) * 100.0), 10)
        rows.append(row)
    if not rows:
        return WeatherTypeResult(
            empty, False,
            "Précipitation inconnue pour tous les membres futurs.")
    daily = pd.DataFrame(rows)[columns].sort_values("date")
    if not (daily["pe_arome"].any() or daily["pe_arpege"].any()) \
            and regional_reason is None:
        regional_reason = (
            "Aucun ensemble régional ne fournit de fenêtre classable : "
            "estimation globale seulement.")
    return WeatherTypeResult(daily, True, regional_reason=regional_reason)


def regime_meteo(daily, bascule=False):
    """Formule le régime météo à venir pour la tuile « Changement de temps ».

    Lit les signaux DÉJÀ calculés — proportions de type de temps
    (``ensemble_daily_weather_types``) moyennées sur les ``REGIME_FENETRE_J``
    premiers jours et la présence d'une bascule de pression
    (``logic.pmsl_bascule``) — sans aucun nouveau calcul météo. Ordre de
    décision voulu : d'abord l'incertitude (ne jamais sur-affirmer), puis le
    temps perturbé (prudence : signaler une précipitation possible), puis le
    sec, enfin le variable par défaut. La tendance neige/pluie n'est proposée
    qu'en régime perturbé et reste une possibilité, jamais une certitude.

    Renvoie un ``RegimeMeteo`` ou ``None`` si aucun signal n'est exploitable
    (dégradation silencieuse, comme le reste du domaine).
    """
    parts = None
    if daily is not None and not daily.empty:
        window = daily[daily["jour"] <= REGIME_FENETRE_J]
        window = window if not window.empty else daily
        parts = {cat: float(window[cat].mean()) / 100.0 for cat in CATEGORIES}

    # Aucun type de temps exploitable : on peut encore parler timing si une
    # bascule de pression est détectée, sinon rien à afficher.
    if parts is None:
        if bascule:
            return RegimeMeteo("perturbe", "Temps perturbé en approche",
                               "chute de pression attendue")
        return None

    wet = parts["neigeux"] + parts["pluvieux"]
    consensus = max(parts["sec"], wet, parts["mixte"])

    # 1. Modèles divergents : le plus fort accord reste faible, ou la part
    #    « mixte/incertain » domine — on n'ose aucune tendance.
    if parts["mixte"] >= REGIME_INCERTAIN_MIXTE_MIN \
            or consensus < REGIME_CONSENSUS_MIN:
        return RegimeMeteo("incertain", "Évolution incertaine",
                           "les modèles divergent")

    # 2. Temps perturbé : précipitations probables (part humide franche ou
    #    bascule de régime détectée). Tendance de phase seulement si l'écart
    #    neige/pluie est net.
    if wet >= REGIME_PERTURBE_WET_MIN or bascule:
        diff = parts["neigeux"] - parts["pluvieux"]
        if diff >= REGIME_TENDANCE_MARGE:
            return RegimeMeteo("perturbe", "Temps perturbé, plutôt neigeux",
                               "précipitations probables", tendance="neige")
        if -diff >= REGIME_TENDANCE_MARGE:
            return RegimeMeteo("perturbe", "Temps perturbé, plutôt pluvieux",
                               "précipitations probables", tendance="pluie")
        return RegimeMeteo("perturbe", "Temps perturbé",
                           "précipitations probables")

    # 3. Temps sec et anticyclonique : part « sec » dominante, pas de bascule.
    if parts["sec"] >= REGIME_SEC_MIN:
        return RegimeMeteo("sec", "Temps sec et stable", "hautes pressions")

    # 4. Défaut : signal réel mais sans régime marqué.
    return RegimeMeteo("variable", "Temps variable", "sans régime marqué")
