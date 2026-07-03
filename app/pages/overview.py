# -*- coding: utf-8 -*-
"""Page « Vue d'ensemble » — KPI config-driven (config.KPI_*) + panache du
super-ensemble des derniers runs complets par modèle.

Invariant CLAUDE.md : les KPI sont calculés sur les échéances À VENIR
uniquement, et le « jour à risque » mêle probabilité × sévérité (deux portes
d'entrée) — ne pas le réduire à un seuil de proba seul."""

from datetime import datetime

import pandas as pd
import streamlit as st

import config as C
from app.runtime import LOCAL_TZ
from app.data.db import run_label_text
from app.data.runsets import (
    latest_complete_run_sub, latest_refresh_status, previous_runs_sub)
from app.stats.climato import clim_normal
from app.stats.ensemble import daily_risk, multimodel_cutoff, super_ensemble, var_median
from app.ui.charts import fan_chart, models_median_chart, z500_median_chart
from app.ui.components import _kpi_card, complete_runs_caption


def page_overview(runs, sig):
    st.title("🌡️ Dashboard Météo — Prévisions d'ensemble (Paris)")
    if runs.empty:
        st.warning("Base vide. Lancez le pipeline `Forecast.py` pour la remplir.")
        return
    # Sélecteur « Vu depuis » : rejoue la page telle qu'elle était après un cycle
    # antérieur (base filtrée run_date ≤ cycle, même logique de complétude par
    # modèle). La carte « Tendance » se compare alors au jeu précédent RELATIF à
    # la version affichée — on peut dérouler l'évolution d'un épisode a posteriori.
    opts = runs["run_date"].tolist()[:C.KPI_MAX_VERSIONS]
    opt_labels = [run_label_text(rd) for rd in opts]
    col_sel, _ = st.columns([1, 3])
    choice = col_sel.selectbox(
        "Vu depuis", ["Dernier état"] + opt_labels,
        help="Rejoue la vue avec les derniers runs complets disponibles à ce cycle.")
    as_of = None if choice == "Dernier état" else opts[opt_labels.index(choice)]

    sub, sources, partial = latest_complete_run_sub(sig, as_of)
    refreshed_at, _, _ = latest_refresh_status(runs, sig)
    missing = [m for m in C.MAIN_LABELS if m not in sources]
    if as_of is not None:
        refresh_txt = f" · vue reconstituée au cycle {run_label_text(as_of)}"
    else:
        refresh_txt = (f" · rafraîchi le {refreshed_at.strftime('%d/%m/%Y à %Hh%M')}"
                       if refreshed_at is not None else "")
    if missing:
        statut_txt = f"partiel ⚠️ (aucun run pour {', '.join(missing)})"
    elif partial:
        statut_txt = (f"horizon réduit ⚠️ pour {', '.join(partial)} "
                      "(pas de run à horizon plein récent)")
    else:
        statut_txt = "complet ✅"
    st.caption(f"Super-ensemble des **derniers runs complets** par modèle · "
               f"{len(runs)} prévisions (runs) archivées · températures à 850 hPa"
               f"{refresh_txt} · {statut_txt}")
    st.caption(f"Runs retenus (horizon plein, par modèle) : {complete_runs_caption(sources)}")

    syn = super_ensemble(sub)
    if syn is None or syn.empty:
        st.error("Aucune donnée exploitable pour ce run.")
        return

    # Référence « présent » des KPI : l'instant réel pour le dernier état, le
    # cycle choisi pour une version reconstituée (l'horloge n'y a aucun sens).
    ref_now = (pd.Timestamp(as_of) if as_of is not None
               else pd.Timestamp(datetime.now(LOCAL_TZ)).tz_localize(None))
    # KPI calculés sur les échéances À VENIR uniquement : les heures passées
    # (rebouchées par l'API depuis 00:00 local) fausseraient prochaine échéance,
    # pic, tendance et anomalie. Les graphiques, eux, gardent le panache complet.
    fut = syn[syn["valid_time"] >= ref_now]
    if fut.empty:
        fut = syn
    first = fut.iloc[0]
    peak = fut.loc[fut["Médiane"].idxmax()]

    # Tendance : Δ de la médiane du super-ensemble vs le pool des runs précédents
    # (recul PAR MODÈLE, cf. previous_runs_sub — jamais de cycle global partagé).
    delta_txt, delta_sub = "—", "aucun run antérieur en base"
    prev = previous_runs_sub(sig, sub)
    if prev is not None:
        syn_prev = super_ensemble(prev)
        if syn_prev is not None and not syn_prev.empty:
            both = fut.merge(syn_prev[["valid_time", "Médiane"]], on="valid_time",
                             suffixes=("", "_prev")).dropna(subset=["Médiane", "Médiane_prev"])
            if not both.empty:
                d = both["Médiane"] - both["Médiane_prev"]
                at_peak = both[both["valid_time"] == peak["valid_time"]]
                pic_txt = (f" · au pic {(at_peak.iloc[0]['Médiane'] - at_peak.iloc[0]['Médiane_prev']):+.1f} °C"
                           if not at_peak.empty else "")
                delta_txt = f"{d.mean():+.1f} °C"
                delta_sub = f"sur {len(both)} échéances communes{pic_txt}"

    # Horizon de confiance : première échéance où le spread P90−P10 dépasse le
    # seuil config — au-delà, le scénario central seul n'est plus exploitable.
    over = fut[fut["Spread"] > C.KPI_SPREAD_CONF_MAX_C]
    if over.empty:
        conf_txt = "Plein horizon"
        conf_sub = f"spread P90−P10 < {C.KPI_SPREAD_CONF_MAX_C:.0f} °C sur toute la fenêtre"
    else:
        t_lim = over.iloc[0]["valid_time"]
        conf_txt = f"J+{max((t_lim - ref_now) / pd.Timedelta(days=1), 0):.0f}"
        conf_sub = (f"spread > {C.KPI_SPREAD_CONF_MAX_C:.0f} °C dès {t_lim:%a %d %b} · "
                    f"au pic {peak['Spread']:.1f} °C")

    # Jours à risque = probabilité × sévérité (cf. daily_risk / seuils KPI_RISK_*) :
    # un jour compte si la proba journalière atteint PROB_MIN OU si le dépassement
    # attendu atteint EXCESS_MIN (queue chaude à proba modeste).
    risk = daily_risk(sub[sub["valid_time"] >= ref_now.normalize()], C.SEUIL_CANICULE_850)
    risk_txt, risk_sub = "0 j", "aucune donnée exploitable"
    if risk is not None and not risk.empty:
        flag = risk[(risk["prob"] >= C.KPI_RISK_PROB_MIN) |
                    (risk["exces"] >= C.KPI_RISK_EXCESS_MIN_C)]
        if flag.empty:
            worst = risk.loc[risk["exces"].idxmax()]
            risk_txt = "0 j"
            risk_sub = (f"max : {worst['date']:%a %d %b} · P {worst['prob']:.0%} · "
                        f"+{worst['exces']:.1f} °C attendu")
        else:
            f0 = flag.iloc[0]
            sev = f0["exces"] / f0["prob"] if f0["prob"] > 0 else 0.0
            risk_txt = f"{len(flag)} j"
            risk_sub = (f"1er : {f0['date']:%a %d %b} · P {f0['prob']:.0%} · "
                        f"+{sev:.1f} °C si dépassé")

    # Anomalie moyenne de la médiane vs la normale cosinus sur la fenêtre courte :
    # caractérise le régime (semaine chaude/froide) indépendamment du pic ponctuel.
    win = fut[fut["valid_time"] <= ref_now + pd.Timedelta(days=C.KPI_ANOMALIE_FENETRE_J)]
    if win.empty:
        win = fut
    anom = (win["Médiane"] - clim_normal(win["valid_time"])).mean()

    r1c1, r1c2, r1c3 = st.columns(3)
    r1c1.markdown(_kpi_card("Prochaine échéance (médiane)", f"{first['Médiane']:.1f} °C",
                            "Scénario central pour la première échéance à venir",
                            value_point=first["Médiane"], valid_time=first["valid_time"],
                            sub=f"{first['valid_time']:%a %d %b %Hh}"),
                  unsafe_allow_html=True)
    r1c2.markdown(_kpi_card("Pic de chaleur (médiane)", f"{peak['Médiane']:.1f} °C",
                            "Maximum du scénario central ; P90 = scénario chaud "
                            "plausible à la même échéance",
                            value_point=peak["Médiane"], valid_time=peak["valid_time"],
                            sub=f"{peak['valid_time']:%a %d %b %Hh} · P90 {peak['P90']:.1f} °C"),
                  unsafe_allow_html=True)
    r1c3.markdown(_kpi_card("Tendance vs runs précédents", delta_txt,
                            "Δ moyen de la médiane du super-ensemble vs le run "
                            "précédent de CHAQUE modèle (échéances communes à venir)",
                            sub=delta_sub),
                  unsafe_allow_html=True)

    r2c1, r2c2, r2c3 = st.columns(3)
    r2c1.markdown(_kpi_card("Horizon de confiance", conf_txt,
                            f"Première échéance où le spread P90−P10 du super-ensemble "
                            f"dépasse {C.KPI_SPREAD_CONF_MAX_C:.0f} °C",
                            sub=conf_sub),
                  unsafe_allow_html=True)
    r2c2.markdown(_kpi_card("Jours à risque canicule", risk_txt,
                            f"Jours où P(≥ {C.SEUIL_CANICULE_850:.0f} °C) ≥ "
                            f"{C.KPI_RISK_PROB_MIN:.0%} OU dépassement attendu ≥ "
                            f"{C.KPI_RISK_EXCESS_MIN_C:.1f} °C (probabilité × sévérité)",
                            sub=risk_sub),
                  unsafe_allow_html=True)
    r2c3.markdown(_kpi_card(f"Anomalie {C.KPI_ANOMALIE_FENETRE_J} j vs normale",
                            f"{anom:+.1f} °C",
                            f"Écart moyen de la médiane à la normale climatique sur "
                            f"les {C.KPI_ANOMALIE_FENETRE_J} prochains jours",
                            sub="médiane du super-ensemble vs normale saisonnière"),
                  unsafe_allow_html=True)

    st.caption("**Panache de dispersion** : ligne rouge = médiane ; bandes = part "
               "croissante des scénarios (Min–Max, P10–P90, P25–P75).")
    st.plotly_chart(fan_chart(syn, "Panache du super-ensemble — derniers runs complets par modèle"),
                    width="stretch")

    present = sorted(sub["model"].unique())
    cutoff = multimodel_cutoff(sub)
    st.caption("Trait plein = médiane par modèle, bande = dispersion (P10–P90), "
               "pointillés = run de contrôle.")
    st.plotly_chart(models_median_chart(sub, present, cutoff), width="stretch")

    # Contexte synoptique (technique, médiane seule) — rendu UNIQUEMENT si le pool
    # contient du Z500 : en son absence (base pas encore alimentée, runs legacy),
    # la page reste strictement identique à l'existant.
    med_z500 = var_median(sub, "z500")
    if med_z500 is not None and not med_z500.empty:
        with st.expander("🌀 Contexte synoptique — géopotentiel 500 hPa (médiane)"):
            st.caption("Médiane du super-ensemble du géopotentiel à 500 hPa (m). "
                       "Au-dessus de la normale = dorsale/blocage anticyclonique "
                       "(favorise chaleur durable) ; en dessous = talweg.")
            st.plotly_chart(z500_median_chart(
                med_z500, "Géopotentiel 500 hPa — derniers runs complets par modèle"),
                width="stretch")
