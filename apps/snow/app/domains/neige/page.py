# -*- coding: utf-8 -*-
"""Page « Vue d'ensemble neige » — KPI config-driven calculés sur les
ÉCHÉANCES À VENIR uniquement (jamais les heures passées rebouchées par l'API),
pool = dernier run à horizon plein de chaque modèle (latest_complete_run_sub).

Hiérarchie des signaux par échéance (snow_config.HORIZON_REGIMES) : quantités
et iso 0° en tête de fenêtre, masse d'air (t850) et régime (pmsl) au-delà.
Tout signal météo absent se dégrade sans panne ; l'absence de précipitation,
qui empêche la classification sec/pluie/neige, est expliquée dans l'interface
afin qu'un historique ``NaN`` ne soit jamais interprété comme du temps sec."""

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from ...data.db import (hd_signature, latest_mf_local_deterministic,
                        latest_mf_local_members, latest_mf_regional_members,
                        load_hd, mf_local_signature, mf_regional_signature,
                        run_label_text)
from ...data.runsets import latest_complete_run_sub
from . import logic, weather_type
from .charts import (daily_snow_chart, lpn_chart, medians_chart,
                     hourly_vertical_weather_chart, weather_type_chart)
from core.stats.ensemble import member_matrix


def _kpi_prochaine_chute(daily):
    """Premier jour « à neige » (proba × sévérité) d'un site, ou None."""
    jours = logic.jours_a_neige(daily)
    if jours is None or jours.empty:
        return None
    return jours.iloc[0]


def _hd_daily_table(summary):
    """Tableau compact pluie/neige par jour civil et altitude."""
    if summary is None or summary.empty:
        return pd.DataFrame()
    display = summary.copy()

    def _label(row):
        parts = []
        if row["pluie_mm"] >= weather_type.PRECIP_BRUIT_MM_HEURE:
            parts.append(f"🌧️ {row['pluie_mm']:.1f} mm")
        if row["neige_cm"] >= (weather_type.PRECIP_BRUIT_MM_HEURE
                               * weather_type.RATIO_NEIGE_CM_PAR_MM):
            parts.append(f"❄️ {row['neige_cm']:.1f} cm")
        return " · ".join(parts) if parts else "☀️ sec"

    display["bilan"] = display.apply(_label, axis=1)
    table = display.pivot(index="altitude_m", columns="date", values="bilan")
    table = table.sort_index(ascending=False)
    table.index = [f"{alt:.0f} m" for alt in table.index]
    table.columns = [f"{pd.Timestamp(date):%a %d %b}" for date in table.columns]
    table.index.name = "Altitude"
    return table


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
    signal_sommet = logic.signal_neige_affichable(daily_sommet)
    t2m_sommet_48h = logic.temperature_mediane_horizon(sommet, 48)
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
    hd_df = load_hd(hd_signature())
    hd = logic.hd_prochaines_48h(hd_df, "sommet")
    if hd and "cumul_cm" in hd:
        iso_txt = (f" · iso 0° min {hd['iso0_min_m']:.0f} m"
                   if "iso0_min_m" in hd else "")
        st.caption(f"🔬 Maille fine ({hd['source']}, 48 h) : "
                   f"{hd['cumul_cm']:.1f} cm au sommet{iso_txt}")
    ctx = logic.contexte_synoptique(village)
    if ctx:
        st.caption(f"🗺️ Contexte synoptique : {ctx}")

    # --------------------------------------- Deux blocs par régime d'horizon --
    st.subheader("Prochaines 48 h — Détail horaire par altitude")
    st.caption("Un point par heure et par altitude : couleur/forme = phase, "
               "taille = quantité. Survolez un point pour lire les cm/mm "
               "exacts. Conversion de départ : 1 mm de précipitation = "
               "1 cm de neige.")
    hd_profile = weather_type.hd_vertical_hourly_profile(hd_df)
    mf_sig = mf_local_signature()
    pi_df = latest_mf_local_deterministic(mf_sig, SC.AROME_PI_MODEL)
    pi_profile = weather_type.arome_pi_vertical_hourly_profile(pi_df)
    combined = weather_type.combine_vertical_hourly_profiles(
        hd_profile.daily if hd_profile.available else pd.DataFrame(),
        pi_profile.daily if pi_profile.available else pd.DataFrame())
    profile = weather_type.VerticalProfileResult(
        combined, not combined.empty,
        hd_profile.reason if combined.empty else None)
    hd_reference = pd.DataFrame()
    if profile.available:
        st.plotly_chart(hourly_vertical_weather_chart(profile.daily),
                        width="stretch")
        summary = weather_type.hd_daily_amounts(profile.daily)
        hd_reference = weather_type.hd_daily_reference(profile.daily)
        st.markdown("**Bilan quotidien sur la fenêtre affichée**")
        st.dataframe(_hd_daily_table(summary), width="stretch")
        if pi_profile.available:
            pi_run = pd.to_datetime(pi_df["run_date"]).max()
            st.caption(
                f"🏔️ AROME-PI {run_label_text(pi_run)} prioritaire sur ses "
                "six heures : phase issue directement des cumuls total/neige. "
                "La maille fine HD prend ensuite le relais jusqu'à 48 h.")
        else:
            st.caption(f"⚠️ {pi_profile.reason} Repli explicite sur la maille "
                       "fine HD, sans substitution cachée.")
        st.caption(f"La phase utilise toujours la LPN (iso 0 °C − "
                   f"{weather_type.LPN_MARGE_HD_M:.0f} m), sans tracer LPN/iso 0 "
                   "sur l'axe des altitudes, sauf sur AROME-PI qui fournit "
                   "directement les cumuls pluie/neige. Le premier et le dernier jour "
                   "peuvent être partiels car la fenêtre est glissante sur 48 h.")
    else:
        st.info(profile.reason)

    st.subheader("J0 à J+15 — Risque neige/pluie/sec par membre")
    st.caption(f"J0–J+{SC.HORIZON_REGIMES[0]['max_j']} complète la maille fine "
               "par la dispersion de l'ensemble. "
               f"J+4–J+{SC.HORIZON_REGIMES[1]['max_j']} : "
               f"{SC.HORIZON_REGIMES[1]['desc']}. "
               f"Puis jusqu'à J+{SC.HORIZON_REGIMES[-1]['max_j']} : "
               f"{SC.HORIZON_REGIMES[-1]['desc']}. Chaque membre est classé "
               "brut au village, puis les proportions sont pondérées par "
               "pertinence du modèle et de l'horizon ; aucune classification "
               "n'est tentée au sommet.")
    pe_arome = latest_mf_local_members(mf_sig)
    pe_arpege = latest_mf_regional_members(mf_regional_signature())
    weather = weather_type.ensemble_daily_weather_types(
        village, regional_sub=pe_arome, arpege_sub=pe_arpege)
    if weather.available:
        st.plotly_chart(weather_type_chart(weather.daily, hd_reference),
                        width="stretch")
        st.caption(f"Sec si precip < {weather_type.PRECIP_BRUIT_MM_JOUR:.1f} mm/j. "
                   f"Entre {weather_type.PRECIP_BRUIT_MM_JOUR:.1f} et "
                   f"{weather_type.PRECIP_PLUIE_SIGNIFICATIVE_MM_JOUR:.1f} mm/j, "
                   "une trace ou faible averse chaude reste mixte/incertaine ; "
                   "la catégorie pluvieuse commence au second seuil. "
                   f"Le score froid combine t850 ({weather_type.POIDS_T850:.0%}) "
                   f"et épaisseur ({weather_type.POIDS_EPAISSEUR:.0%}). "
                   "L'épaisseur module le diagnostic mais ne rend jamais la "
                   "neige impossible à elle seule ; pluie seulement lors d'un "
                   "redoux t850 franc, sinon la zone douteuse reste mixte.")
        if weather.regional_reason:
            st.caption(f"⚠️ {weather.regional_reason}")
        if weather.daily["pe_arome"].any():
            pe_run = pe_arome["run_date"].max()
            st.caption(
                f"🏔️ PE-AROME {run_label_text(pe_run)} : 25 membres, "
                "cumuls glissants 0–24 h et 24–48 h. La phase vient directement "
                "des cumuls neige/total du modèle régional, sans veto "
                "t850/épaisseur.")
            global_only = weather.daily[
                (weather.daily["jour"] <= SC.HORIZON_REGIMES[0]["max_j"])
                & ~weather.daily["pe_arome"]]
            if not global_only.empty:
                labels = ", ".join(
                    f"J+{int(day)}" for day in global_only["jour"].unique())
                st.caption(f"⚠️ {labels} : PE-AROME hors couverture ; "
                           "PE-ARPEGE prend le relais s'il couvre le jour, "
                           "sinon pondération globale explicite.")
        if weather.daily["pe_arpege"].any():
            arpege_run = pe_arpege["run_date"].max()
            st.caption(
                f"🗺️ PE-ARPEGE {run_label_text(arpege_run)} : 35 membres, "
                "cumuls glissants H24/H48/H72/H96. Il prend 50 % du type "
                "de temps local uniquement quand PE-AROME ne couvre plus "
                "la journée ; total/neige sont lus directement.")
        short_weights = weather_type.POIDS_MODELES_TYPE_TEMPS[0]["weights"]
        st.caption(
            "Hiérarchie locale : PE-AROME 70 % lorsqu'il couvre le jour ; "
            "sinon PE-ARPEGE 50 % sur sa couverture. Le solde, ou 100 % sans "
            "régional, suit ECMWF/AIFS/GEFS au ratio "
            + "/".join(f"{weight:.1%}" for weight in short_weights.values())
            + ". GEFS reste pleinement consultable pour la masse d'air "
                     "(t850/épaisseur) : cette minoration ne concerne que le "
                     "type de temps local en vallée.")
        regional_days = weather.daily[
            weather.daily["pe_arome"] | weather.daily["pe_arpege"]]["jour"]
        if not regional_days.empty:
            last_regional_day = int(regional_days.max())
            global_tail = weather.daily[
                weather.daily["jour"] > last_regional_day]
            if not global_tail.empty:
                st.caption(
                    f"🌍 Après J+{last_regional_day}, la couverture "
                    "régionale est terminée : retour à 100 % aux ensembles "
                    "ECMWF/AIFS/GEFS. Les graphes t850, épaisseur et pression "
                    "ci-dessous décrivent alors surtout la masse d'air et le "
                    "timing synoptique, pas une quantité locale précise.")
        else:
            st.caption(
                "🌍 Aucun jour n'est actuellement couvert par un ensemble "
                "régional : la classification est 100 % ECMWF/AIFS/GEFS ; "
                "elle renseigne mieux la masse d'air que les quantités locales.")
        if not hd_reference.empty:
            st.caption("HD ☀️/🌧️/❄️ au-dessus des premières barres = "
                       "scénario maille fine indépendant au village ; * = "
                       "journée civile partiellement couverte par la fenêtre "
                       "glissante de 48 h. Un désaccord HD/ensemble est conservé "
                       "et rendu visible, jamais moyenné silencieusement.")
        if weather.daily["n_non_classes"].gt(0).any():
            maximum = int(weather.daily["n_non_classes"].max())
            st.caption(f"⚠️ Jusqu'à {maximum} membre(s) non classé(s) selon "
                       "le jour car leur précipitation est inconnue ; ils sont "
                       "exclus du dénominateur, jamais assimilés à du sec.")
    else:
        st.info(weather.reason)

    # ---------------------------------------------- Graphes détaillés --
    st.subheader("Signal neige — Mont d'Arbois (1830 m)")
    if signal_sommet is not None and not signal_sommet.empty:
        st.caption("Seuls les jours portant un signal exploitable sont tracés ; "
                   "les sorties brutes sous tous les seuils de pertinence "
                   "restent consultables dans Explorer un run.")
        st.plotly_chart(daily_snow_chart(signal_sommet, "sommet"),
                        width="stretch")
        # Calendrier compact des jours à neige (paliers validés 1/5/20 cm).
        jours = logic.jours_a_neige(daily_sommet)
        if jours is not None and not jours.empty:
            lignes = [f"**{r['date']:%a %d %b}** {logic.palier_neige(r['attendu'])[1]} "
                      f"{logic.palier_neige(r['attendu'])[0]} "
                      f"(~{r['attendu']:.0f} cm · {r['prob'] * 100:.0f} %)"
                      for _, r in jours.iterrows()]
            st.markdown(" · ".join(lignes))
    elif daily_sommet is not None and not daily_sommet.empty:
        traces = daily_sommet[(daily_sommet["prob"] > 0)
                              | (daily_sommet["attendu"] > 0)]
        if traces.empty:
            st.info("🌤️ Aucun signal de neige sur l'horizon visible.")
        else:
            douceur = (f"La température médiane au sommet est d'environ "
                        f"{t2m_sommet_48h:.0f} °C sur 48 h. "
                        if t2m_sommet_48h is not None else "")
            st.info("🌤️ Aucun signal de neige crédible sur l'horizon visible. "
                    + douceur
                    + f"Les sorties isolées restent sous {traces['prob'].max() * 100:.1f} % "
                    f"de probabilité et {traces['attendu'].max():.2f} cm de cumul "
                    "moyen : elles ne sont pas présentées comme un épisode neigeux.")
    else:
        st.caption("Variable neige absente du pool courant.")

    st.subheader("Limite pluie-neige (iso 0° − "
                 f"{SC.LPN_MARGE_M} m) vs altitude des points")
    if lpn is not None and not lpn.empty:
        st.plotly_chart(lpn_chart(lpn), width="stretch")
    else:
        st.caption("Iso 0° absent du pool courant (cas normal selon les modèles).")

    st.subheader("Masse d'air et régime (moyenne/longue échéance)")
    for fig in (
        medians_chart(village, "t850", "T850 — médianes et P10–P90", "°C",
                      seuils_h={"neige sommet": SC.SEUIL_T850_NEIGE["sommet"],
                                "neige village": SC.SEUIL_T850_NEIGE["village"]}),
        medians_chart(village, "pmsl", "Pression — médianes et P10–P90", "hPa"),
        medians_chart(village, "epaisseur",
                      "Épaisseur 1000–500 hPa — médianes et P10–P90", "m",
                      seuils_h={"repère village": logic.EPAISSEUR_NEIGE_M["village"],
                                "repère sommet": logic.EPAISSEUR_NEIGE_M["sommet"]}),
    ):
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
