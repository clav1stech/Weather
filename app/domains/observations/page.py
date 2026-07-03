# -*- coding: utf-8 -*-
"""Page « Observations en direct » — domaine observations.

Trois blocs : dernière observation par station (temps réel), comparaison
inter-stations sur 24-48 h (l'écart ICU nocturne est le message), et
confrontation Tx/Tn prévus (flux HD, prévision UNIQUE pour Paris) vs observés
par station — qui mesure l'ICU local par rapport à la prévision générale.

Flux d'affichage pur : ces observations n'influencent ni la détection
canicule, ni la sélection des runs, ni les KPI. Base d'observations absente ou
partielle = cas normal (message d'attente, jamais de crash) ; une station
muette n'empêche jamais l'affichage des autres."""

from datetime import datetime

import pandas as pd
import streamlit as st

import config as C
from app.data.observations import (
    daily_txtn_obs, latest_obs, load_obs, obs_signature, obs_window)
from app.data.t2m import t2m_signature, txtn_by_day
from app.domains.observations.charts import (
    comparaison_stations, ecart_icu_chart, prevu_vs_observe_chart)
from app.domains.observations.logic import (
    comparaison_prevu_observe, ecart_icu_series, obs_est_perimee,
    verdict_icu_nocturne)


def _carte_station(col, station, row, now_local):
    """Carte « temps réel » d'une station : température + heure d'observation,
    et pour la station de référence (Montsouris) humidité / vent / pluie /
    pression. `row` None = station sans la moindre observation en base."""
    with col:
        st.markdown(f"**{station['nom']}**")
        st.caption(station["profil"])
        if row is None:
            st.info("Pas encore d'observation en base.")
            return
        perimee = obs_est_perimee(row["valid_time"], now_local)
        t_txt = f"{row['t']:.1f} °C" if pd.notna(row["t"]) else "—"
        st.metric("Température", t_txt,
                  help=f"Station {station['reseau']} · alt. {station['alt']} m")
        heure = f"{row['valid_time']:%a %d %b · %Hh%M}"
        if perimee:
            st.caption(f"⚠️ Dernière obs : {heure} — donnée ancienne "
                       "(collecte ou API en retard)")
        else:
            st.caption(f"🕐 Observé le {heure} (heure de Paris)")
        if station["reference"]:
            détails = []
            if pd.notna(row["humidite"]):
                détails.append(f"💧 {row['humidite']:.0f} %")
            if pd.notna(row["vent_ff"]):
                vent = f"🌬️ {row['vent_ff'] * 3.6:.0f} km/h"
                if pd.notna(row["vent_dir"]):
                    vent += f" ({row['vent_dir']:.0f}°)"
                détails.append(vent)
            if pd.notna(row["precip_1h"]):
                détails.append(f"🌧️ {row['precip_1h']:.1f} mm/1h")
            if pd.notna(row["pression_mer"]):
                détails.append(f"🔽 {row['pression_mer']:.0f} hPa")
            if détails:
                st.caption(" · ".join(détails))


def page_observations(runs, sig):
    st.title("🏙️ Observations en direct")
    obs_sig = obs_signature()
    base = load_obs(obs_sig)

    st.caption("Quatre stations Météo-France parisiennes, choisies pour leur "
               "contraste d'exposition à l'**îlot de chaleur urbain (ICU)** — "
               "données d'observation officielles (API DPObs), rafraîchies "
               "toutes les heures.")

    with st.expander("❓ Pourquoi ces 4 stations — et pourquoi certaines mesures manquent"):
        lignes = "\n".join(
            f"- **{s['nom']}** ({s['reseau']}, alt. {s['alt']} m) — {s['profil']}."
            for s in C.OBS_STATIONS)
        st.markdown(
            f"{lignes}\n\n"
            "**L'îlot de chaleur urbain** se lit surtout **la nuit** : le tissu "
            "urbain dense (Lariboisière, Luxembourg) restitue la chaleur "
            "accumulée le jour, tandis que les zones végétalisées (Montsouris, "
            "Longchamp) se refroidissent bien plus vite. En journée, les quatre "
            "thermomètres racontent presque la même histoire — une seule mesure "
            "(Montsouris, la référence historique) suffit alors.\n\n"
            "**Certaines mesures ne sont disponibles que sur 2 stations sur 4.** "
            "Lariboisière et Luxembourg appartiennent au réseau « étendu », "
            "moins instrumenté : elles ne publient que la température et les "
            "précipitations — humidité, vent et pression n'y sont **pas "
            "mesurés** (ce n'est pas une panne). Ces variables étant bien plus "
            "homogènes que la température à l'échelle de Paris, celles de "
            "Montsouris suffisent : elles ne sont affichées que là.\n\n"
            "La comparaison entre stations porte donc uniquement sur la "
            "**température**, seule variable mesurée partout.")

    if base.empty:
        st.info("La base d'observations n'est pas encore alimentée — le flux "
                "démarre avec la première exécution du pipeline "
                "(fetch_observations.py, cron horaire). Revenez dans une heure.")
        return

    now_local = pd.Timestamp(datetime.now())

    # ── Bloc 1 : temps réel ──────────────────────────────────────────────────
    st.subheader("📍 Dernières observations")
    latest = latest_obs(obs_sig)
    by_nom = {r["station_nom"]: r for _, r in latest.iterrows()}
    cols = st.columns(len(C.OBS_STATIONS))
    for col, station in zip(cols, C.OBS_STATIONS):
        _carte_station(col, station, by_nom.get(station["nom"]), now_local)

    # ── Bloc 2 : comparaison inter-stations (ICU) ────────────────────────────
    st.subheader("🌃 L'écart ville / verdure, heure par heure")
    fenetre_h = st.radio("Fenêtre", [24, 48], format_func=lambda h: f"{h} h",
                         horizontal=True, label_visibility="collapsed")
    dfw = obs_window(obs_sig, fenetre_h)
    if dfw.empty or dfw["t"].notna().sum() == 0:
        st.info("Pas assez d'observations sur la fenêtre pour comparer les stations.")
    else:
        st.caption("Bandes grises = nuits (22 h – 6 h, heure de Paris) : c'est là "
                   "que l'écart entre quartiers denses et zones végétalisées se "
                   "creuse — le cœur de l'effet d'îlot de chaleur.")
        st.plotly_chart(comparaison_stations(
            dfw, f"Température observée — {fenetre_h} dernières heures"),
            width="stretch")

        ecarts = ecart_icu_series(dfw)
        verdict = verdict_icu_nocturne(ecarts)
        if verdict is not None:
            moy, label, phrase = verdict
            st.markdown(f"**Écart ICU nocturne moyen : {moy:+.1f} °C ({label})** — {phrase}")
        if not ecarts.empty:
            st.plotly_chart(ecart_icu_chart(ecarts), width="stretch")
            st.caption("Écart = moyenne des stations urbaines denses "
                       f"({', '.join(s['nom'] for s in C.OBS_STATIONS if s['icu'] == 'urbain')}) "
                       "− moyenne des stations aérées "
                       f"({', '.join(s['nom'] for s in C.OBS_STATIONS if s['icu'] == 'aere')}), "
                       "aux heures où les deux groupes ont des mesures.")

    # ── Bloc 3 : prévision vs réalité ────────────────────────────────────────
    st.subheader("🎯 La prévision collait-elle à la réalité ?")
    st.caption("La prévision Tx/Tn haute résolution (Météo-France / DWD ICON) est "
               "calculée pour **un point unique de Paris** — pas pour chaque "
               "station. L'écart par station n'est donc pas une « erreur » du "
               "modèle : il montre quelle station colle le mieux à la prévision "
               "générale et laquelle s'en écarte le plus (signal d'ICU local).")
    today = pd.Timestamp(datetime.now().date())
    cmp_df = comparaison_prevu_observe(txtn_by_day(t2m_signature()),
                                       daily_txtn_obs(obs_sig), today)
    if cmp_df.empty:
        st.info("Pas encore de journée complète recoupant prévision et "
                "observations — ce bloc se remplira de lui-même après un ou "
                "deux jours de collecte.")
        return
    col_tx, col_tn = st.columns(2)
    col_tx.plotly_chart(prevu_vs_observe_chart(cmp_df, "tx"), width="stretch")
    col_tn.plotly_chart(prevu_vs_observe_chart(cmp_df, "tn"), width="stretch")

    # Biais moyen par station (observé − prévu) — se consolide avec l'historique.
    n_jours = cmp_df["date"].nunique()
    biais = (cmp_df.groupby("station_nom", as_index=False)
                   .agg(ecart_tx=("ecart_tx", "mean"), ecart_tn=("ecart_tn", "mean")))
    ordre = {s["nom"]: i for i, s in enumerate(C.OBS_STATIONS)}
    biais = biais.assign(_o=biais["station_nom"].map(ordre)).sort_values("_o")
    st.caption(f"Écart moyen observé − prévu sur {n_jours} journée(s) complète(s) — "
               "en positif : la station est plus chaude que la prévision générale.")
    st.dataframe(
        biais.rename(columns={"station_nom": "Station",
                              "ecart_tx": "Écart Tx (°C)", "ecart_tn": "Écart Tn (°C)"})
             .drop(columns="_o").set_index("Station").round(1),
        width="stretch")
