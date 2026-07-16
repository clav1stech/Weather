# -*- coding: utf-8 -*-
"""Page « Observations en direct » — domaine observations.

Trois blocs : dernière observation par station (temps réel), comparaison
inter-stations sur 24-48 h (l'écart ICU nocturne est le message), et
convergence de la prévision 15 min à Montsouris (courbe observée 6 min vs
prévisions émises à divers reculs — vintages : voit-on la prévision se
resserrer vers l'observé à l'approche de l'échéance ?).

Flux d'affichage pur : ces observations n'influencent ni la détection
canicule, ni la sélection des runs, ni les KPI. Base d'observations absente ou
partielle = cas normal (message d'attente, jamais de crash) ; une station
muette n'empêche jamais l'affichage des autres."""

from datetime import datetime

import pandas as pd
import streamlit as st

import config as C
from app.runtime import LOCAL_TZ
from app.data.observations import (
    latest_obs, load_obs, obs_signature, obs_window, txtn_du_jour)
from app.data.observations_6m import latest_obs_6m, load_obs_6m, obs_6m_signature
from app.data.vintages import load_vintages, vintages_signature
from app.services import cooldown
from app.services.live_observations import fetch_live_snapshot
from app.domains.observations.charts import (
    comparaison_stations, ecart_icu_chart, vintage_comparison_chart)
from app.domains.observations.logic import (
    OBS_CARTE_ALERTE_H, ecart_icu_series, obs_est_perimee,
    verdict_icu_nocturne, vintage_comparison_series)


def _cols_cartes():
    """Colonnes des rangées temps réel : une par station, se partageant à parts
    égales toute la largeur de la page (comme les KPI de la page canicule).
    st.columns garde TOUJOURS ses colonnes sur une seule ligne (responsive) :
    aucun ascenseur horizontal ni retour à la ligne possible."""
    return st.columns(len(C.OBS_STATIONS))


def _champ_frais(col, *rows):
    """Valeur la plus FRAÎCHE de la colonne `col` parmi plusieurs sources d'une
    même station (obs horaire, obs 6 min, aperçu en direct du bouton) : la
    source la plus récente prime dès qu'elle porte cette colonne avec une
    valeur non-NaN (grandeurs instantanées — température, humidité, vent,
    pression). Les colonnes propres au flux horaire (precip_1h, tx/tn :
    cumuls/extrêmes horaires absents du 6 min et de l'aperçu en direct)
    retombent naturellement sur l'horaire. Retourne (valeur ou NaN, horodatage
    ou None). Une source absente se passe simplement `None`."""
    best_val, best_t = float("nan"), None
    for r in rows:
        if r is None or col not in r or pd.isna(r[col]) or pd.isna(r["valid_time"]):
            continue
        if best_t is None or r["valid_time"] > best_t:
            best_val, best_t = r[col], r["valid_time"]
    return best_val, best_t


def _carte_station(col, station, row_h, row_6m, row_live, now_local, txtn=None):
    """Carte « temps réel » d'une station : température + heure d'observation.
    Cadre bordé à hauteur naturelle (contenu identique d'une carte à l'autre :
    nom + métrique + horodatage → cartes de même hauteur, aucun CSS requis). La
    température affichée est la plus fraîche entre l'horaire, l'infra-horaire
    6 min (`row_6m`, stations RADOME seules) et l'aperçu en direct du bouton
    (`row_live`, session en cours seulement — remplace les autres sources dès
    qu'il est plus frais, ce qu'il est presque toujours juste après un clic).
    Les trois `None` = station sans la moindre observation. Les mesures
    communes (humidité, vent, pluie, pression) ne sont volontairement PAS ici :
    elles ne valent qu'à Montsouris mais décrivent tout Paris (variables
    homogènes à cette échelle) — les afficher dans son rectangle donnerait à
    tort l'impression d'une donnée propre à cette seule station, cf.
    `_conditions_generales`."""
    with col, st.container(border=True):
        st.markdown(f"**{station['nom']}**")
        if row_h is None and row_6m is None and row_live is None:
            st.info("Pas encore d'observation en base.")
            return
        t, t_time = _champ_frais("t", row_h, row_6m, row_live)
        # Température absente à ce poll : on horodate quand même avec l'obs la
        # plus récente disponible (au moins une existe, cf. garde ci-dessus).
        if t_time is None:
            t_time = max(r["valid_time"] for r in (row_h, row_6m, row_live)
                         if r is not None and pd.notna(r["valid_time"]))
        t_txt = f"{t:.1f} °C" if pd.notna(t) else "—"
        st.metric("Température", t_txt,
                  help=f"Station {station['reseau']} · alt. {station['alt']} m")
        # Min/max PROVISOIRES du jour civil en cours (depuis 00 h, flux horaire
        # seul — cf. txtn_du_jour) : ligne présente sur TOUTES les cartes, « — »
        # si donnée absente, pour garder hauteur et alignement identiques.
        tn = txtn["tn"] if txtn is not None else float("nan")
        tx = txtn["tx"] if txtn is not None else float("nan")
        tn_txt = f"{tn:.1f}°" if pd.notna(tn) else "—"
        tx_txt = f"{tx:.1f}°" if pd.notna(tx) else "—"
        st.caption(f"Depuis 00 h : min {tn_txt} · max {tx_txt}")
        heure = f"Le {t_time:%d/%m à %Hh%M}"
        # Rouge si l'obs a plus d'OBS_CARTE_ALERTE_H (1 h) — seuil resserré vs
        # OBS_PERIMEE_H (3 h) : avec le flux 6 min, une obs vieille d'1 h+ est
        # déjà le signe d'un souci de collecte, pas juste un cron un peu lent.
        if obs_est_perimee(t_time, now_local, seuil_h=OBS_CARTE_ALERTE_H):
            st.markdown(f":red[{heure}]")
        else:
            st.caption(heure)


def _conditions_generales(row_ref_h, row_ref_6m, row_ref_live):
    """Bloc unique « conditions générales » (humidité, vent, pluie, pression) —
    séparé des rectangles par station : ces variables, plus homogènes que la
    température à l'échelle de Paris, ne sont mesurées qu'à Montsouris mais
    décrivent toute l'agglomération. Grandeurs instantanées rafraîchies au 6 min
    ou à l'aperçu en direct quand l'un des deux est plus frais (`row_ref_6m`,
    `row_ref_live`) ; la pluie (cumul 1 h) reste horaire (absente des deux
    autres sources). Les trois `None` → rien affiché."""
    if row_ref_h is None and row_ref_6m is None and row_ref_live is None:
        return
    hum, _ = _champ_frais("humidite", row_ref_h, row_ref_6m, row_ref_live)
    ff, _ = _champ_frais("vent_ff", row_ref_h, row_ref_6m, row_ref_live)
    dd, _ = _champ_frais("vent_dir", row_ref_h, row_ref_6m, row_ref_live)
    pr1, _ = _champ_frais("precip_1h", row_ref_h, row_ref_6m, row_ref_live)
    pmer, _ = _champ_frais("pression_mer", row_ref_h, row_ref_6m, row_ref_live)
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


_LIVE_SNAPSHOT_KEY = "obs_live_snapshot"  # st.session_state — jamais persisté sur disque


def _bouton_rafraichissement():
    """Bouton public (visiteurs anonymes de l'app Cloud) : interroge l'API
    Météo-France 6 min EN DIRECT et stocke le résultat dans st.session_state
    (propre à cette session de navigateur, jamais écrit sur disque) — le
    dashboard n'écrit JAMAIS lui-même en base (invariant lecture seule), la
    base réelle ne se réactualise qu'au prochain cron GitHub Actions habituel
    (≤ 15 min). Un `st.rerun()` fait relire ce snapshot dès le haut de la page
    : les cartes « temps réel » l'affichent DIRECTEMENT à la place des
    valeurs stockées (`_champ_frais` retient la source la plus fraîche), sans
    bloc séparé. Cooldown vérifié AVANT tout appel réseau : garde-fou
    anti-abus d'un public anonyme sur l'API Météo-France, cf.
    app/services/cooldown."""
    ok, remaining = cooldown.can(C.OBS_LIVE_REFRESH_STATE_PATH,
                                  C.OBS_LIVE_REFRESH_COOLDOWN_S)
    if not ok:
        st.button("🔄 Voir un aperçu instantané", disabled=True,
                  help=f"Réessayez dans ~{int(remaining) + 1} s.")
        return
    if not st.button("🔄 Voir un aperçu instantané",
                      help="Interroge l'API Météo-France en direct : les "
                           "cartes ci-dessus affichent aussitôt ce relevé à "
                           "la place des valeurs stockées — non enregistré "
                           "en base, juste affiché pour cette visite ; la "
                           "base réelle se réactualise au prochain cron "
                           "(≤ 15 min)."):
        return
    with st.spinner("Interrogation de l'API Météo-France…"):
        resultats, erreurs = fetch_live_snapshot()
    if resultats is None:
        # Pré-condition non remplie (clé absente) : aucun appel réseau tenté,
        # le cooldown n'a donc pas lieu d'être consommé.
        st.error(erreurs[0])
        return
    cooldown.record(C.OBS_LIVE_REFRESH_STATE_PATH)
    st.session_state[_LIVE_SNAPSHOT_KEY] = {
        "data": resultats, "erreurs": erreurs, "time": datetime.now()}
    st.rerun()


def _section_convergence_prevision():
    """Convergence de la prévision 15 min à Montsouris : température observée
    (6 min) vs prévisions émises à divers reculs (vintages). Lecture seule des
    deux flux via leurs couches data ; dégradation silencieuse tant que
    l'historique de vintages est trop court pour comparer les reculs."""
    st.subheader("🔭 Convergence de la prévision — Montsouris")
    st.caption("Pour chaque heure, la température **observée** à Montsouris "
               "(trait bleu épais) et les prévisions qui la visaient, émises à "
               "divers reculs — la dernière en date, puis 6, 12, 18 et 24 h plus "
               "tôt (ambre, de plus en plus pâle). Les prévisions se resserrent "
               "vers l'observé à mesure que l'échéance approche : c'est la "
               "convergence du modèle. Courbes de prévision lissées (moyenne "
               "~1 h) pour atténuer l'intermittence nocturne du modèle en couche "
               "limite stable — l'observé n'est jamais lissé.")
    vdf = load_vintages(vintages_signature())
    if vdf.empty:
        st.info("Le flux de prévision 15 min vient d'être mis en place — "
                "l'historique des révisions se constitue (il faut ~24 h de recul "
                "pour comparer tous les reculs). Revenez dans quelques heures.")
        return
    # Vintages stockés en UTC (cf. app/data/vintages) ; l'observé 6 min est déjà
    # en heure de Paris → on aligne les vintages sur le même fuseau à l'affichage
    # (invariant temporel : conversion vers Paris seulement au rendu).
    for c in ("valid_time", "fetched_at"):
        s = pd.to_datetime(vdf[c])
        vdf[c] = s.dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)

    ref = next((s for s in C.OBS_STATIONS if s["reference"]), None)
    obs6 = load_obs_6m(obs_6m_signature())
    if ref is not None and not obs6.empty:
        obs_ref = (obs6[(obs6["station_id"] == ref["id"]) & obs6["t"].notna()]
                   .sort_values("valid_time"))
    else:
        obs_ref = pd.DataFrame(columns=["valid_time", "t"])

    series = vintage_comparison_series(vdf, obs_ref if not obs_ref.empty else None)
    if series.empty:
        st.info("Pas encore assez de recul dans l'historique de prévision pour "
                "tracer la convergence — revenez dans quelques heures.")
        return
    # Observé restreint à la fenêtre couverte par les séries de prévision.
    if not obs_ref.empty:
        obs_ref = obs_ref[obs_ref["valid_time"] >= series["valid_time"].min()]
    st.plotly_chart(vintage_comparison_chart(
        obs_ref, series, "Prévision vs observé — Montsouris (48 h)"),
        width="stretch")


def page_observations(runs, sig):
    st.title("🏙️ Observations en direct")
    obs_sig = obs_signature()
    base = load_obs(obs_sig)

    st.caption("Quatre stations Météo-France parisiennes, choisies pour leur "
               "contraste d'exposition à l'**îlot de chaleur urbain (ICU)** — "
               "données d'observation officielles (API DPObs). La température "
               "est rafraîchie **toutes les quelques minutes** (flux 6 min).")

    with st.expander("❓ Pourquoi ces 4 stations"):
        lignes = "\n".join(
            f"- **{s['nom']}** ({s['reseau']}, alt. {s['alt']} m) — {s['profil']}."
            for s in C.OBS_STATIONS)
        st.markdown(
            f"{lignes}\n\n"
            "**L'îlot de chaleur urbain** se lit surtout **la nuit** : le tissu "
            "urbain dense (Lariboisière, Luxembourg) restitue la chaleur "
            "accumulée le jour, tandis que les zones aérées (Longchamp) se "
            "refroidissent bien plus vite. Montsouris (la référence historique), "
            "malgré son profil de parc, reste en pratique proche des stations "
            "urbaines la nuit — elle n'entre donc pas dans le calcul de l'écart "
            "ICU ci-dessous, mais reste affichée pour comparaison. En journée, "
            "les quatre thermomètres racontent presque la même histoire.\n\n"
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
    # Aperçu en direct du bouton (st.session_state, propre à cette session de
    # navigateur, jamais persisté) : source la plus fraîche dès qu'elle existe,
    # remplace donc les cartes ci-dessous sans bloc séparé (cf. _champ_frais).
    live = st.session_state.get(_LIVE_SNAPSHOT_KEY)
    by_nom_live = live["data"] if live else {}
    # Min/max provisoires du jour civil en cours, par station (flux horaire
    # seul — le 6 min ne porte pas tx/tn, l'aperçu en direct non plus).
    txtn_jour = txtn_du_jour(obs_sig, now_local.normalize())
    by_id_txtn = {r["station_id"]: r for _, r in txtn_jour.iterrows()}
    cols = _cols_cartes()
    for col, station in zip(cols, C.OBS_STATIONS):
        _carte_station(col, station, by_nom.get(station["nom"]),
                       by_nom6.get(station["nom"]),
                       by_nom_live.get(station["nom"]), now_local,
                       txtn=by_id_txtn.get(station["id"]))
    ref = next((s for s in C.OBS_STATIONS if s["reference"]), None)
    if ref is not None:
        _conditions_generales(by_nom.get(ref["nom"]), by_nom6.get(ref["nom"]),
                              by_nom_live.get(ref["nom"]))
    if live is not None:
        st.caption(f"Aperçu en direct du {live['time']:%d/%m à %Hh%M:%S} — "
                   "non enregistré, affiché pour cette visite seulement.")
        if live["erreurs"]:
            st.warning("Station(s) indisponible(s) à l'instant : "
                       + ", ".join(live["erreurs"]))
    _bouton_rafraichissement()

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
                       "aux heures où les deux groupes ont des mesures. Montsouris "
                       "n'entre pas dans ce calcul (cf. « Pourquoi ces 4 stations »).")

    # ── Bloc 3 : convergence de la prévision (vintages) ──────────────────────
    _section_convergence_prevision()
