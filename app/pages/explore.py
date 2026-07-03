# -*- coding: utf-8 -*-
"""Page « Explorer une prévision (run) » — panache, spaghetti, comparaison,
incertitude et tableaux d'export d'un run choisi (ou du pool « Dernier run »,
cf. latest_run_sub : fraîcheur maximale, aucune exigence d'horizon plein)."""

import streamlit as st

import config as C
from app.data.db import run_slice, utc_cycle
from app.data.runsets import (
    latest_run_sub, main_labels_expected_at, previous_runs_sub)
from app.stats.ensemble import (
    daily_aggregate, divergence, model_data, multimodel_cutoff, super_ensemble,
    var_median)
from app.stats.tables import enriched_super_table, model_table
from app.ui.charts import (
    divergence_chart, fan_chart, models_median_chart, spaghetti_chart, spread_chart,
    z500_median_chart)
from app.ui.components import complete_runs_caption


def page_explore(runs, sig):
    st.title("📊 Explorer une prévision (run)")
    st.caption(
        "Vue détaillée d'un run sous plusieurs angles : panache de dispersion, "
        "scénarios individuels, comparaison des modèles, divergence, incertitude et "
        "tableaux de données. « Dernier run » poole le run le plus récent de chaque "
        "modèle, même à cycles différents.")
    if runs.empty:
        st.warning("Aucun run disponible.")
        return

    # Sentinelle « dernier run » : pool du dernier run de CHAQUE modèle, quel que
    # soit son cycle (cf. latest_run_sub) — les vrais runs restent listés ensuite.
    LATEST = -1
    idx = st.selectbox(
        "Choisir un run", [LATEST] + list(runs.index),
        format_func=lambda i: ("🕐 Dernier run (le plus récent de chaque modèle, "
                               "tous cycles)" if i == LATEST else runs.loc[i, "label"]))
    if idx == LATEST:
        sub, sources = latest_run_sub(sig)
        run_label = "derniers runs par modèle"
        file_tag = "dernier"
        st.caption("Dernier run disponible de chaque modèle, même partiel (aucune "
                   f"exigence d'horizon plein) : {complete_runs_caption(sources)}")
        manquants = [m for m in C.MAIN_LABELS if m not in sources]
    else:
        run = runs.loc[idx]
        sub = run_slice(sig, run["run_date"])
        run_label = run["label"]
        u = utc_cycle(run["run_date"])
        file_tag = f"{u:%Y%m%d}_{u.hour:02d}Z"
        manquants = [m for m in main_labels_expected_at(run["run_date"])
                     if m not in sub["model"].unique()]
    syn = super_ensemble(sub)
    present = sorted(sub["model"].unique())
    cutoff = multimodel_cutoff(sub)

    if manquants:
        st.warning(f"⚠️ Modèle(s) principal(aux) absent(s) : **{', '.join(manquants)}**. "
                   "Super-ensemble appauvri (dispersion possiblement sous-estimée).")

    tab_fan, tab_spag, tab_cmp, tab_unc, tab_z500, tab_tbl = st.tabs(
        ["📈 Panache", "🍝 Spaghetti", "⚖️ Modèles", "📉 Incertitude", "🌀 Z500",
         "🧾 Tableaux"])

    with tab_fan:
        if syn is not None and not syn.empty:
            st.plotly_chart(fan_chart(syn, f"Super-ensemble — {run_label}"), width="stretch")
        else:
            st.info("Aucune donnée exploitable dans ce run.")

    with tab_spag:
        if present:
            model = st.radio("Modèle", present, horizontal=True, key="spag_model")
            loaded = model_data(sub, model)
            if loaded:
                stats, members, det = loaded
                st.plotly_chart(spaghetti_chart(members, stats, det, model), width="stretch")
        else:
            st.info("Aucun modèle dans ce run.")

    with tab_cmp:
        if present:
            st.plotly_chart(models_median_chart(sub, present, cutoff), width="stretch")
            div = divergence(sub)
            if div is not None and not div.empty:
                st.caption("Divergence calculée uniquement aux échéances à composition "
                           "complète (tous les modèles du run présents).")
                st.plotly_chart(divergence_chart(div, cutoff), width="stretch")
        else:
            st.info("Comparaison indisponible.")

    with tab_unc:
        if syn is not None and not syn.empty:
            st.plotly_chart(spread_chart(syn), width="stretch")
        else:
            st.info("Pas de données d'incertitude.")

    with tab_z500:
        # Vue technique : médiane d'ensemble seule (jamais de spaghetti Z500) —
        # lecture/contrôle de la donnée brute, la vulgarisation vit sur la page
        # Indicateur de canicule. Absence normale : runs antérieurs à la collecte
        # de z500 et runs importés du legacy (Météociel ne publie pas Z500).
        med = var_median(sub, "z500")
        if med is None or med.empty:
            st.info("Géopotentiel 500 hPa indisponible pour ce run (donnée collectée "
                    "uniquement par le pipeline Open-Meteo, à partir de son ajout à "
                    "la base — jamais présente sur les runs importés du legacy).")
        else:
            st.caption("Médiane du super-ensemble (tous membres poolés) du géopotentiel "
                       "à 500 hPa, en mètres. Au-dessus de la normale = dorsale "
                       "(situation anticyclonique d'altitude), en dessous = talweg.")
            st.plotly_chart(z500_median_chart(med, f"Géopotentiel 500 hPa — {run_label}"),
                            width="stretch")

    with tab_tbl:
        st.caption("Tableaux larges, pensés pour l'export vers une analyse externe "
                   "(IA) : stats du super-ensemble enrichies, par modèle, de la "
                   "médiane, du contrôle (member 0), du nombre de membres actifs et "
                   "du Δ de médiane vs le run précédent de chaque modèle — plus une "
                   "table détaillée par modèle.")
        prev_sub = previous_runs_sub(sig, sub)
        tables = {
            "Super-ensemble (infra-journalier)":
                lambda: enriched_super_table(sub, prev_sub),
            "Super-ensemble (journalier 12h)":
                lambda: daily_aggregate(enriched_super_table(sub, prev_sub)),
        }
        for m in present:
            tables[f"Modèle — {m}"] = (lambda m=m: model_table(sub, m, prev_sub))
        choice = st.selectbox("Table", list(tables), key="tbl_sheet")
        raw = tables[choice]()
        if raw is not None:
            raw = raw.drop(columns=["date"], errors="ignore").round(2)
        if raw is None or raw.empty:
            st.info("Table indisponible.")
        else:
            num_cols = raw.select_dtypes(include="number").columns
            styler = (raw.style.background_gradient(cmap="RdYlBu_r", subset=list(num_cols),
                                                    axis=None)
                      .set_properties(subset=list(num_cols), color="#1a2330")
                      .format(precision=1)
                      if len(num_cols) else raw)
            st.dataframe(styler, width="stretch", height=520)
            st.download_button("⬇️ Télécharger (CSV)",
                               raw.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"run_{file_tag}_{choice[:20]}.csv",
                               mime="text/csv")
