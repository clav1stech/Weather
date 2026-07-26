# -*- coding: utf-8 -*-
"""Page « Observations » du dashboard neige — stations Météo-France des Alpes
du Nord (trois stations de référence Combloux / Mont d'Arbois / Aiguille du
Midi + stations d'appoint vallées).

AFFICHAGE SEUL : n'influence ni les KPI, ni les seuils, ni la sélection des
runs. Base vide/absente → message d'attente, jamais un crash. L'obs plus
vieille que OBS_PERIMEE_H est signalée « ancienne », jamais présentée comme
actuelle. Variables absentes d'une station (instrumentation) : la carte
n'affiche que ce qui existe."""

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from ...data.observations import (daily_txtn_obs, latest_obs, obs_signature,
                                  obs_window)
from . import logic
from .charts import stations_chart


def _carte_station(col, station, obs):
    """Carte « temps réel » d'une station : température en valeur principale,
    complétée des seuls champs réellement mesurés (NaN structurel → omis)."""
    with col:
        t = obs.get("t")
        valeur = f"{t:.1f} °C" if pd.notna(t) else "n/d"
        extras = []
        if pd.notna(obs.get("hneige")):
            extras.append(f"❄ {obs['hneige']:.0f} cm au sol")
        if pd.notna(obs.get("raf")):
            extras.append(f"💨 raf {obs['raf'] * 3.6:.0f} km/h")
        elif pd.notna(obs.get("vent_ff")):
            extras.append(f"💨 {obs['vent_ff'] * 3.6:.0f} km/h")
        if pd.notna(obs.get("humidite")):
            extras.append(f"{obs['humidite']:.0f} %")
        horodatage = f"{obs['valid_time']:%H:%M}"
        if logic.est_perimee(obs["valid_time"]):
            horodatage = f"⚠️ ancienne ({obs['valid_time']:%d %b %H:%M})"
        st.metric(f"{station['nom']} · {station['alt']} m", valeur,
                  " · ".join(extras) or horodatage, delta_color="off")
        if extras:
            st.caption(horodatage)


def page_observations(runs, sig):
    st.title("🌡️ Observations — Alpes du Nord")
    obs_sig = obs_signature()
    latest = latest_obs(obs_sig)
    if latest.empty:
        st.info("Aucune observation en base pour l'instant — le flux "
                "DPPaquetObs n'a pas encore collecté (ou la clé API n'est pas "
                "configurée côté pipeline).")
        return

    # ------------------------------------------------- Cartes temps réel --
    # Stations de référence en premier rang, appoint en second (ordre config).
    refs = [s for s in SC.OBS_STATIONS if s["reference"]]
    appoint = [s for s in SC.OBS_STATIONS if not s["reference"]]
    by_id = {r["station_id"]: r for _, r in latest.iterrows()}
    for groupe, libelle in ((refs, "Stations de référence"),
                            (appoint, "Stations d'appoint (vallées)")):
        présentes = [s for s in groupe if s["id"] in by_id]
        if not présentes:
            continue
        st.subheader(libelle)
        cols = st.columns(len(présentes))
        for col, station in zip(cols, présentes):
            _carte_station(col, station, by_id[station["id"]])

    # ---------------------------------------------- Signaux du gradient --
    signaux = []
    iso0 = logic.iso0_observee(latest)
    if iso0 is not None:
        signaux.append(f"iso 0 °C observée ≈ **{iso0:.0f} m** (interpolée "
                       "entre stations — indicatif, à confronter à l'iso 0° "
                       "prévue de la Vue d'ensemble)")
    grad = logic.gradient_thermique(latest)
    if grad is not None:
        détail = " (inversion probable)" if grad < 3.0 else ""
        signaux.append(f"gradient observé **{grad:.1f} °C/1000 m**{détail}")
    if signaux:
        st.caption("🌍 " + " · ".join(signaux))

    # ------------------------------------------------------- Graphiques --
    fenetre = obs_window(obs_sig, SC.OBS_FENETRE_GRAPHE_H)
    st.subheader(f"Température comparée ({SC.OBS_FENETRE_GRAPHE_H} h)")
    fig = stations_chart(fenetre, "t", "", "°C")
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("Pas de température exploitable sur la fenêtre.")

    fig = stations_chart(fenetre, "hneige",
                         "Hauteur de neige au sol (stations équipées)", "cm")
    if fig is not None:
        st.subheader("Neige au sol")
        st.plotly_chart(fig, use_container_width=True)
    # Aucune station équipée / hors saison : rien — absence structurelle.

    # ------------------------------------------- Tx/Tn des jours révolus --
    txtn = logic.txtn_jours_complets(daily_txtn_obs(obs_sig))
    if txtn is not None and not txtn.empty:
        st.subheader("Tx / Tn observés — jours révolus complets")
        table = txtn.assign(
            jour=txtn["date"].dt.strftime("%a %d %b"),
            txtn=[f"{r.tx:.1f} / {r.tn:.1f} °C"
                  if pd.notna(r.tx) and pd.notna(r.tn) else "n/d"
                  for r in txtn.itertuples()],
        ).pivot(index="jour", columns="station_nom", values="txtn")
        # Ré-ordonne lignes (chronologique) et colonnes (ordre config).
        ordre_jours = (txtn.drop_duplicates("date").sort_values("date"))
        table = table.reindex(ordre_jours["date"].dt.strftime("%a %d %b"))
        noms = [s["nom"] for s in SC.OBS_STATIONS if s["nom"] in table.columns]
        st.table(table[noms])
        st.caption(f"Jours comptant au moins {SC.OBS_JOUR_COMPLET_MIN_H} h "
                   "d'observations — un jour troué fausserait ses extrêmes.")
