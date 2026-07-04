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
from app.data.observations_6m import latest_obs_6m, obs_6m_signature
from app.data.t2m import t2m_signature, txtn_by_day
from app.domains.observations.charts import (
    comparaison_stations, ecart_icu_chart, prevu_vs_observe_chart)
from app.domains.observations.logic import (
    OBS_CARTE_ALERTE_H, comparaison_prevu_observe, ecart_icu_series,
    obs_est_perimee, verdict_icu_nocturne)


def _cols_cartes():
    """Colonnes des rangées temps réel : une par station, se partageant à parts
    égales toute la largeur de la page (comme les KPI de la page canicule).
    st.columns garde TOUJOURS ses colonnes sur une seule ligne (responsive) :
    aucun ascenseur horizontal ni retour à la ligne possible."""
    return st.columns(len(C.OBS_STATIONS))


def _champ_frais(row_h, row_6m, col):
    """Valeur la plus FRAÎCHE de la colonne `col` entre l'obs horaire et l'obs
    6 min d'une même station : le 6 min prime dès qu'il porte cette colonne avec
    une valeur non-NaN et un horodatage plus récent (grandeurs instantanées —
    température, humidité, vent, pression). Les colonnes propres au flux horaire
    (precip_1h, tx/tn : cumuls/extrêmes horaires absents du 6 min) retombent
    naturellement sur l'horaire. Retourne (valeur ou NaN, horodatage ou None).
    `row_6m` None = station sans 6 min (ETENDU, ou base 6 min absente)."""
    best_val, best_t = float("nan"), None
    for r in (row_h, row_6m):
        if r is None or col not in r or pd.isna(r[col]) or pd.isna(r["valid_time"]):
            continue
        if best_t is None or r["valid_time"] > best_t:
            best_val, best_t = r[col], r["valid_time"]
    return best_val, best_t


def _carte_station(col, station, row_h, row_6m, now_local):
    """Carte « temps réel » d'une station : température + heure d'observation.
    Cadre bordé à hauteur naturelle (contenu identique d'une carte à l'autre :
    nom + métrique + horodatage → cartes de même hauteur, aucun CSS requis). La
    température affichée est la plus fraîche entre l'horaire et l'infra-horaire
    6 min (`row_6m`, stations RADOME seules — None ailleurs). `row_h`/`row_6m`
    tous deux None = station sans la moindre observation en base. Les mesures
    communes (humidité, vent, pluie, pression) ne sont volontairement PAS ici :
    elles ne valent qu'à Montsouris mais décrivent tout Paris (variables
    homogènes à cette échelle) — les afficher dans son rectangle donnerait à
    tort l'impression d'une donnée propre à cette seule station, cf.
    `_conditions_generales`."""
    with col, st.container(border=True):
        st.markdown(f"**{station['nom']}**")
        if row_h is None and row_6m is None:
            st.info("Pas encore d'observation en base.")
            return
        t, t_time = _champ_frais(row_h, row_6m, "t")
        # Température absente à ce poll : on horodate quand même avec l'obs la
        # plus récente disponible (au moins une existe, cf. garde ci-dessus).
        if t_time is None:
            t_time = max(r["valid_time"] for r in (row_h, row_6m)
                         if r is not None and pd.notna(r["valid_time"]))
        t_txt = f"{t:.1f} °C" if pd.notna(t) else "—"
        st.metric("Température", t_txt,
                  help=f"Station {station['reseau']} · alt. {station['alt']} m")
        heure = f"Le {t_time:%d/%m à %Hh%M}"
        # Rouge si l'obs a plus d'OBS_CARTE_ALERTE_H (1 h) — seuil resserré vs
        # OBS_PERIMEE_H (3 h) : avec le flux 6 min, une obs vieille d'1 h+ est
        # déjà le signe d'un souci de collecte, pas juste un cron un peu lent.
        if obs_est_perimee(t_time, now_local, seuil_h=OBS_CARTE_ALERTE_H):
            st.markdown(f":red[{heure}]")
        else:
            st.caption(heure)


def _conditions_generales(row_ref_h, row_ref_6m):
    """Bloc unique « conditions générales » (humidité, vent, pluie, pression) —
    séparé des rectangles par station : ces variables, plus homogènes que la
    température à l'échelle de Paris, ne sont mesurées qu'à Montsouris mais
    décrivent toute l'agglomération. Grandeurs instantanées rafraîchies au 6 min
    quand il est plus frais (`row_ref_6m`) ; la pluie (cumul 1 h) reste horaire.
    `row_ref_h`/`row_ref_6m` tous deux None → rien affiché."""
    if row_ref_h is None and row_ref_6m is None:
        return
    hum, _ = _champ_frais(row_ref_h, row_ref_6m, "humidite")
    ff, _ = _champ_frais(row_ref_h, row_ref_6m, "vent_ff")
    dd, _ = _champ_frais(row_ref_h, row_ref_6m, "vent_dir")
    pr1, _ = _champ_frais(row_ref_h, row_ref_6m, "precip_1h")
    pmer, _ = _champ_frais(row_ref_h, row_ref_6m, "pression_mer")
    détails = []
    if pd.notna(hum):
        détails.append(("💧 Humidité", f"{hum:.0f} %"))
    if pd.notna(ff):
        vent = f"{ff * 3.6:.0f} km/h"
        if pd.notna(dd):
            vent += f" ({dd:.0f}°)"
        détails.append(("🌬️ Vent", vent))
    if pd.notna(pr1):
        détails.append(("🌧️ Précipitations", f"{pr1:.1f} mm/1h"))
    if pd.notna(pmer):
        détails.append(("🔽 Pression", f"{pmer:.0f} hPa"))
    if not détails:
        return
    st.caption("Conditions générales sur Paris (mesurées à Montsouris)")
    # Mêmes colonnes que les cartes (station + espaceur) → métriques alignées
    # dessous, même largeur, jamais de débordement.
    cols = _cols_cartes()
    for c, (label, valeur) in zip(cols, détails):
        c.metric(label, valeur)


def page_observations(runs, sig):
    st.title("🏙️ Observations en direct")
    obs_sig = obs_signature()
    base = load_obs(obs_sig)

    st.caption("Quatre stations Météo-France parisiennes, choisies pour leur "
               "contraste d'exposition à l'**îlot de chaleur urbain (ICU)** — "
               "données d'observation officielles (API DPObs). La température "
               "est rafraîchie **toutes les quelques minutes** (flux 6 min).")

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
    # Fraîcheur infra-horaire 6 min (stations RADOME seules) : flux séparé qui
    # ne rafraîchit QUE les valeurs instantanées affichées ici — jamais la
    # comparaison inter-stations ni les Tx/Tn (grille horaire).
    latest6 = latest_obs_6m(obs_6m_signature())
    by_nom6 = {r["station_nom"]: r for _, r in latest6.iterrows()}
    cols = _cols_cartes()
    for col, station in zip(cols, C.OBS_STATIONS):
        _carte_station(col, station, by_nom.get(station["nom"]),
                       by_nom6.get(station["nom"]), now_local)
    ref = next((s for s in C.OBS_STATIONS if s["reference"]), None)
    if ref is not None:
        _conditions_generales(by_nom.get(ref["nom"]), by_nom6.get(ref["nom"]))

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
