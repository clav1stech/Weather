# -*- coding: utf-8 -*-
"""Logique du domaine neige : seuils d'interprétation, labels, calculs.

Architecture MULTI-SIGNAUX (snow_config.HORIZON_REGIMES) : les pivots se
hiérarchisent par échéance — coupe quantités/LPN à courte échéance,
classification par membre sur precip/épaisseur/t850 ensuite, pmsl comme timing
du régime à longue échéance. Aucun signal de contexte (z500, t500) ne porte
de seuil : appui qualitatif, dégradation silencieuse."""

import numpy as np
import pandas as pd

from apps.snow import snow_config as SC
from core.stats.ensemble import member_matrix
from . import weather_type

# Alias de compatibilité pour les lectures existantes. Le point de calibration
# unique vit dans weather_type.py avec les autres seuils pluie/neige/sec.
EPAISSEUR_NEIGE_M = weather_type.EPAISSEUR_NEIGE_M

# Libellés des paliers d'intensité (bornes dans SC.PALIERS_NEIGE_CM).
_PALIER_LABELS = ["—", "petite chute", "vraie chute", "grosse chute"]
_PALIER_ICONS = ["", "🌨️", "❄️", "❄️❄️"]


def regime_for_lead(days_ahead):
    """Régime d'échéance (pivots + description) pour une avance en jours."""
    for regime in SC.HORIZON_REGIMES:
        if days_ahead <= regime["max_j"]:
            return regime
    return SC.HORIZON_REGIMES[-1]


def upcoming(sub, now=None):
    """Échéances À VENIR uniquement — les heures passées rebouchées par l'API
    fausseraient tous les KPI (même règle que la Vue d'ensemble canicule)."""
    if sub.empty:
        return sub
    now = now or pd.Timestamp.now()
    return sub[sub["valid_time"] >= now]


def palier_neige(cumul_cm):
    """(libellé, icône) du palier d'intensité d'un cumul journalier."""
    if cumul_cm is None or np.isnan(cumul_cm):
        return _PALIER_LABELS[0], _PALIER_ICONS[0]
    idx = int(np.searchsorted(SC.PALIERS_NEIGE_CM, cumul_cm, side="right"))
    return _PALIER_LABELS[idx], _PALIER_ICONS[idx]


def daily_snowfall(sub_site):
    """Cumuls journaliers de neige fraîche par membre poolé (site donné) :
    [date, prob (cumul ≥ SEUIL_NEIGE_JOUR_CM), attendu (moyenne des membres),
    P90, n_membres]. Cumul PAR MEMBRE d'abord (sum min_count=1 : un membre
    sans la moindre valeur du jour reste NaN, jamais un zéro inventé), stats
    entre membres ensuite. None si la variable est absente du pool."""
    piv = member_matrix(sub_site, "neige")
    if piv is None or not piv.notna().any(axis=None):
        return None
    dates = pd.to_datetime(piv.index).normalize()
    daily = piv.groupby(dates).sum(min_count=1)
    n = daily.notna().sum(axis=1)
    out = pd.DataFrame({
        "date": daily.index,
        "prob": daily.ge(SC.SEUIL_NEIGE_JOUR_CM).sum(axis=1) / n.replace(0, np.nan),
        "attendu": daily.mean(axis=1),
        "P90": daily.quantile(0.90, axis=1),
        "n_membres": n,
    }).reset_index(drop=True)
    return out[out["n_membres"] > 0].reset_index(drop=True)


def jours_a_neige(daily):
    """Jours « à neige » (proba × sévérité, KPI validé) : proba journalière
    ≥ KPI_NEIGE_PROB_MIN OU cumul attendu ≥ KPI_NEIGE_CUMUL_MIN_CM — le second
    critère capte les queues neigeuses à proba modeste."""
    if daily is None or daily.empty:
        return daily
    mask = (daily["prob"] >= SC.KPI_NEIGE_PROB_MIN) \
        | (daily["attendu"] >= SC.KPI_NEIGE_CUMUL_MIN_CM)
    return daily[mask]


def signal_neige_affichable(daily):
    """Jours méritant un graphique dans la lecture opérationnelle.

    Ce filtre d'AFFICHAGE ne modifie ni les membres ni ``daily_snowfall``. Il
    retire seulement les traces isolées sous tous les seuils de pertinence :
    probabilité minimale, cumul moyen minimal, ou P90 atteignant le seuil
    physique d'un jour neigeux. Il reste plus sensible que ``jours_a_neige``
    afin de montrer un scénario à surveiller avant qu'il ne devienne un KPI.
    """
    if daily is None or daily.empty:
        return daily
    mask = (daily["prob"] >= SC.DISPLAY_NEIGE_PROB_MIN) \
        | (daily["attendu"] >= SC.DISPLAY_NEIGE_EXPECTED_MIN_CM) \
        | (daily["P90"] >= SC.SEUIL_NEIGE_JOUR_CM)
    return daily[mask].reset_index(drop=True)


def temperature_mediane_horizon(sub_site, horizon_h, now=None):
    """Médiane T2m du scénario central sur les prochaines ``horizon_h``.

    Résumé d'affichage uniquement : il explique pourquoi des traces neigeuses
    isolées ne constituent pas un signal cohérent dans une masse d'air douce.
    """
    now = now or pd.Timestamp.now()
    window = sub_site[(sub_site["valid_time"] >= now)
                      & (sub_site["valid_time"] <= now + pd.Timedelta(hours=horizon_h))]
    piv = member_matrix(window, "t2m")
    if piv is None or not piv.notna().any(axis=None):
        return None
    return float(piv.median(axis=1).median())


def lpn_series(sub_village):
    """Limite pluie-neige par échéance : médiane d'ensemble de l'iso 0°
    (lignes village — variable de zone) − LPN_MARGE_M, avec bande P10-P90
    elle aussi décalée de la marge. None si iso0 absent du pool (ex. pool
    AIFS seul) — dégradation silencieuse."""
    piv = member_matrix(sub_village, "iso0")
    if piv is None or not piv.notna().any(axis=None):
        return None
    out = pd.DataFrame({
        "valid_time": piv.index,
        "lpn": piv.median(axis=1) - SC.LPN_MARGE_M,
        "lpn_p10": piv.quantile(0.10, axis=1) - SC.LPN_MARGE_M,
        "lpn_p90": piv.quantile(0.90, axis=1) - SC.LPN_MARGE_M,
        "n_membres": piv.notna().sum(axis=1),
    }).reset_index(drop=True)
    return out[out["n_membres"] > 0].reset_index(drop=True)


def neige_au_site(lpn_m, site_code):
    """La LPN passe-t-elle sous le site ? (neige plutôt que pluie à ce point)."""
    if lpn_m is None or np.isnan(lpn_m):
        return None
    return lpn_m <= SC.SITE_BY_CODE[site_code]["alt"]


def t850_label(t850_c, site_code):
    """Lecture masse d'air du site : « neige probable » si t850 ≤ seuil du
    site, « redoux pluvieux » au-delà de SEUIL_T850_REDOUX, entre-deux sinon."""
    if t850_c is None or np.isnan(t850_c):
        return None
    if t850_c <= SC.SEUIL_T850_NEIGE[site_code]:
        return "neige probable"
    if t850_c >= SC.SEUIL_T850_REDOUX:
        return "redoux pluvieux"
    return "limite, à surveiller"


def epaisseur_label(epaisseur_m, site_code):
    """Lecture d'appui de l'épaisseur 1000-500 (JAMAIS seule, cf. régimes) —
    seuils de démarrage EPAISSEUR_NEIGE_M, à calibrer en fin de saison."""
    if epaisseur_m is None or np.isnan(epaisseur_m):
        return None
    return ("air froid en profondeur" if epaisseur_m <= EPAISSEUR_NEIGE_M[site_code]
            else "colonne trop douce")


def pmsl_bascule(sub_village):
    """Premier instant À VENIR où la médiane d'ensemble de pmsl chute d'au
    moins PMSL_BASCULE_HPA_24H en 24 h — signal de TIMING d'un changement de
    régime, jamais un critère neige. None si pas de bascule ou pmsl absent."""
    piv = member_matrix(sub_village, "pmsl")
    if piv is None or not piv.notna().any(axis=None):
        return None
    med = piv.median(axis=1).dropna()
    if len(med) < 25:
        return None
    drop_24h = med - med.shift(24)  # grille horaire → 24 pas = 24 h
    bascule = drop_24h[drop_24h <= -SC.PMSL_BASCULE_HPA_24H]
    return bascule.index[0] if not bascule.empty else None


def contexte_synoptique(sub_village):
    """Phrase de CONTEXTE z500/t500 (médianes d'ensemble sur les 5 prochains
    jours) — qualitatif pur, aucun seuil de risque, None si variables absentes
    (dégradation silencieuse, même discipline que Z500 côté canicule)."""
    horizon = pd.Timestamp.now() + pd.Timedelta(days=5)
    sub = sub_village[sub_village["valid_time"] <= horizon]
    piv_z = member_matrix(sub, "z500")
    piv_t = member_matrix(sub, "t500")
    if piv_z is None or not piv_z.notna().any(axis=None):
        return None
    z_med = float(piv_z.median(axis=1).mean())
    trend = piv_z.median(axis=1)
    z_slope = float(trend.iloc[-1] - trend.iloc[0]) if len(trend) > 1 else 0.0
    sens = "en hausse (dorsale qui se construit)" if z_slope > 30 else \
        "en baisse (creusement en approche)" if z_slope < -30 else "stable"
    txt = f"Z500 médian ≈ {z_med:.0f} m, {sens}"
    if piv_t is not None and piv_t.notna().any(axis=None):
        txt += f" · T500 médiane ≈ {float(piv_t.median(axis=1).mean()):.0f} °C"
    return txt


def hd_prochaines_48h(hd_df, site_code, now=None):
    """Appui maille fine : cumul de neige et iso 0° minimal des prochaines
    48 h d'après la DERNIÈRE collecte HD portant chaque échéance (ICON-D2
    porte seul les variables neige — AROME HD n'y publie que t2m/rafales).
    Renvoie dict {cumul_cm, iso0_min_m, source} ou None (flux absent/vide)."""
    if hd_df is None or hd_df.empty:
        return None
    now = now or pd.Timestamp.now()
    window = hd_df[(hd_df["target_datetime"] >= now)
                   & (hd_df["target_datetime"] <= now + pd.Timedelta(hours=48))]
    if window.empty:
        return None
    # Dernière révision par (model, site, échéance), puis site demandé.
    latest = (window.sort_values("fetched_at")
                    .groupby(["model", "site", "target_datetime"], as_index=False).last())
    out = {}
    neige = latest[(latest["site"] == site_code) & latest["neige"].notna()]
    if not neige.empty:
        # Un seul modèle porte la neige (ICON-D2) : jamais de somme inter-modèles.
        model = neige["model"].iloc[0]
        out["cumul_cm"] = float(neige[neige["model"] == model]["neige"].sum())
        out["source"] = model
    iso0 = latest[(latest["site"] == "village") & latest["iso0"].notna()]
    if not iso0.empty:
        out["iso0_min_m"] = float(iso0["iso0"].min())
    return out or None
