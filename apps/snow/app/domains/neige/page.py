# -*- coding: utf-8 -*-
"""Page « Vue d'ensemble neige » — KPI config-driven calculés sur les
ÉCHÉANCES À VENIR uniquement (jamais les heures passées rebouchées par l'API),
pool = dernier run à horizon plein de chaque modèle (latest_complete_run_sub).

Hiérarchie des signaux par échéance (snow_config.HORIZON_REGIMES) : quantités
et iso 0° en tête de fenêtre, masse d'air (t850) et régime (pmsl) au-delà.
Tout signal absent se dégrade en silence (rien d'affiché, jamais d'alerte)."""

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from ...data.db import hd_signature, load_hd, run_label_text
from ...data.runsets import latest_complete_run_sub
from . import logic
from .charts import daily_snow_chart, lpn_chart, medians_chart
from core.stats.ensemble import member_matrix


def _kpi_prochaine_chute(daily):
    """Premier jour « à neige » (proba × sévérité) d'un site, ou None."""
    jours = logic.jours_a_neige(daily)
    if jours is None or jours.empty:
        return None
    return jours.iloc[0]


def page_neige(runs, sig):
    st.title("🏔️ Vue d'ensemble neige — Megève")
    sub, flags = latest_complete_run_sub(sig)
    if sub.empty:
        st.info("Aucune donnée d'ensemble disponible pour l'instant — le "
                "pipeline n'a pas encore collecté de run complet.")
        return
    up = logic.upcoming(sub)
    if up.empty:
        st.info("Plus d'échéance à venir dans les runs stockés — en attente "
                "du prochain cycle.")
        return
    village = up[up["site"] == "village"]
    sommet = up[up["site"] == "sommet"]

    # Runs affichés (chacun son cycle) + repli éventuel signalé.
    cycles = ", ".join(f"{m} {run_label_text(rd)}"
                       for m, rd in sub.groupby("model")["run_date"].max().items())
    st.caption(f"Dernier run à horizon plein par modèle : {cycles}")
    for label, motif in flags.items():
        st.caption(f"⚠️ {label} : {motif} (aucun run à horizon plein disponible)")

    # ----------------------------------------------------------------- KPI --
    daily_sommet = logic.daily_snowfall(sommet)
    lpn = logic.lpn_series(village)
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        chute = _kpi_prochaine_chute(daily_sommet)
        if chute is not None:
            palier, icone = logic.palier_neige(chute["attendu"])
            st.metric("Prochain jour à neige (sommet)",
                      f"{chute['date']:%a %d %b}",
                      f"{icone} {palier} · ~{chute['attendu']:.0f} cm · "
                      f"{chute['prob'] * 100:.0f} %", delta_color="off")
        else:
            st.metric("Prochain jour à neige (sommet)", "aucun",
                      "sur l'horizon visible", delta_color="off")

    with c2:
        if lpn is not None and not lpn.empty:
            lpn48 = lpn[lpn["valid_time"] <= pd.Timestamp.now() + pd.Timedelta(hours=48)]
            val = float((lpn48 if not lpn48.empty else lpn)["lpn"].median())
            au_village = logic.neige_au_site(val, "village")
            au_sommet = logic.neige_au_site(val, "sommet")
            detail = ("neige jusqu'en vallée" if au_village
                      else "neige au sommet" if au_sommet else "pluie aux deux points")
            st.metric("Limite pluie-neige (48 h)", f"{val:.0f} m", detail,
                      delta_color="off")
        else:
            st.metric("Limite pluie-neige (48 h)", "n/d",
                      "iso 0° absent du pool", delta_color="off")

    with c3:
        piv = member_matrix(village[village["valid_time"] <= pd.Timestamp.now()
                                    + pd.Timedelta(days=7)], "t850")
        if piv is not None and piv.notna().any(axis=None):
            t850_med = float(piv.median(axis=1).median())
            st.metric("Masse d'air (t850, 7 j)", f"{t850_med:+.1f} °C",
                      logic.t850_label(t850_med, "sommet") or "", delta_color="off")
        else:
            st.metric("Masse d'air (t850, 7 j)", "n/d", "", delta_color="off")

    with c4:
        bascule = logic.pmsl_bascule(village)
        if bascule is not None:
            st.metric("Bascule de régime (pmsl)", f"{bascule:%a %d %b %Hh}",
                      f"chute ≥ {SC.PMSL_BASCULE_HPA_24H:.0f} hPa/24 h",
                      delta_color="off")
        else:
            st.metric("Bascule de régime (pmsl)", "aucune",
                      "pression stable sur l'horizon", delta_color="off")

    # Appui maille fine (48 h) + contexte synoptique — affichage pur.
    hd = logic.hd_prochaines_48h(load_hd(hd_signature()), "sommet")
    if hd and "cumul_cm" in hd:
        iso_txt = (f" · iso 0° min {hd['iso0_min_m']:.0f} m"
                   if "iso0_min_m" in hd else "")
        st.caption(f"🔬 Maille fine ({hd['source']}, 48 h) : "
                   f"{hd['cumul_cm']:.1f} cm au sommet{iso_txt}")
    ctx = logic.contexte_synoptique(village)
    if ctx:
        st.caption(f"🗺️ Contexte synoptique : {ctx}")

    # ------------------------------------------------------------- Graphes --
    st.subheader("Chutes de neige journalières — Mont d'Arbois (1830 m)")
    if daily_sommet is not None and not daily_sommet.empty:
        st.plotly_chart(daily_snow_chart(daily_sommet, "sommet"),
                        use_container_width=True)
        # Calendrier compact des jours à neige (paliers validés 1/5/20 cm).
        jours = logic.jours_a_neige(daily_sommet)
        if jours is not None and not jours.empty:
            lignes = [f"**{r['date']:%a %d %b}** {logic.palier_neige(r['attendu'])[1]} "
                      f"{logic.palier_neige(r['attendu'])[0]} "
                      f"(~{r['attendu']:.0f} cm · {r['prob'] * 100:.0f} %)"
                      for _, r in jours.iterrows()]
            st.markdown(" · ".join(lignes))
    else:
        st.caption("Pas de données de chutes exploitables dans le pool courant.")

    st.subheader("Limite pluie-neige (iso 0° − "
                 f"{SC.LPN_MARGE_M} m) vs altitude des points")
    if lpn is not None and not lpn.empty:
        st.plotly_chart(lpn_chart(lpn), use_container_width=True)
    else:
        st.caption("Iso 0° absent du pool courant (cas normal selon les modèles).")

    st.subheader("Masse d'air et régime (moyenne/longue échéance)")
    for fig in (
        medians_chart(village, "t850", "t850 — médianes par modèle", "°C",
                      seuils_h={"neige sommet": SC.SEUIL_T850_NEIGE["sommet"],
                                "neige village": SC.SEUIL_T850_NEIGE["village"]}),
        medians_chart(village, "pmsl", "Pression mer — médianes par modèle", "hPa"),
        medians_chart(village, "epaisseur",
                      "Épaisseur 1000-500 hPa — médianes par modèle (seuils de "
                      "démarrage, à calibrer en fin de saison)", "m",
                      seuils_h={"repère village": logic.EPAISSEUR_NEIGE_M["village"],
                                "repère sommet": logic.EPAISSEUR_NEIGE_M["sommet"]}),
    ):
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
