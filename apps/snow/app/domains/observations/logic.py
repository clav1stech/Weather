# -*- coding: utf-8 -*-
"""Logique du domaine Observations neige — fonctions PURES (testables sans
Streamlit). Tout est tolérant aux NaN : l'instrumentation varie par station
(absence structurelle, jamais une panne à signaler)."""

import pandas as pd

from apps.snow import snow_config as SC


def est_perimee(valid_time, now=None):
    """True si l'observation est plus vieille que OBS_PERIMEE_H — elle doit
    alors être signalée « ancienne », jamais présentée comme actuelle."""
    now = pd.Timestamp.now() if now is None else pd.Timestamp(now)
    return (now - pd.Timestamp(valid_time)) > pd.Timedelta(hours=SC.OBS_PERIMEE_H)


def iso0_observee(latest):
    """Altitude OBSERVÉE de l'isotherme 0 °C, interpolée linéairement entre les
    deux stations d'altitudes adjacentes qui encadrent le passage à 0 °C
    (t ≥ 0 en bas, t < 0 en haut). Signal d'appui à confronter à l'iso 0°
    prévue du flux ensemble — purement indicatif (gradient réel non linéaire,
    inversions). Renvoie une altitude (m) ou None si pas d'encadrement (tout
    positif, tout négatif, ou moins de deux stations avec t valide)."""
    if latest is None or latest.empty:
        return None
    df = latest.dropna(subset=["t"]).copy()
    if len(df) < 2:
        return None
    df["alt"] = df["station_id"].map(
        {s["id"]: s["alt"] for s in SC.OBS_STATIONS})
    df = df.sort_values("alt")
    alts, temps = df["alt"].to_numpy(), df["t"].to_numpy()
    for i in range(len(df) - 1):
        if temps[i] >= 0 > temps[i + 1]:
            span = temps[i] - temps[i + 1]
            if span <= 0:
                return None
            frac = temps[i] / span
            return float(alts[i] + frac * (alts[i + 1] - alts[i]))
    return None


def gradient_thermique(latest):
    """Gradient thermique observé (°C/1000 m) entre la station valide la plus
    basse et la plus haute — un gradient faible ou inversé signe une inversion
    (redoux d'altitude, air froid piégé en vallée). None si < 2 stations."""
    if latest is None or latest.empty:
        return None
    df = latest.dropna(subset=["t"]).copy()
    if len(df) < 2:
        return None
    df["alt"] = df["station_id"].map(
        {s["id"]: s["alt"] for s in SC.OBS_STATIONS})
    df = df.sort_values("alt")
    bas, haut = df.iloc[0], df.iloc[-1]
    d_alt = haut["alt"] - bas["alt"]
    if d_alt <= 0:
        return None
    return float((bas["t"] - haut["t"]) / d_alt * 1000.0)


def txtn_jours_complets(daily, n_jours=7):
    """Les Tx/Tn journaliers des `n_jours` derniers jours RÉVOLUS quasi
    complets (n_heures ≥ OBS_JOUR_COMPLET_MIN_H) — un jour troué ou en cours
    donnerait de faux extrêmes. DataFrame vide si rien ne qualifie."""
    if daily is None or daily.empty:
        return daily
    aujourdhui = pd.Timestamp.now().normalize()
    ok = daily[(daily["date"] < aujourdhui)
               & (daily["n_heures"] >= SC.OBS_JOUR_COMPLET_MIN_H)]
    if ok.empty:
        return ok
    jours = sorted(ok["date"].unique(), reverse=True)[:n_jours]
    return ok[ok["date"].isin(jours)].reset_index(drop=True)
