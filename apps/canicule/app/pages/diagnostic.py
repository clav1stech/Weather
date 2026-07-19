# -*- coding: utf-8 -*-
"""Page « Contrôle de présence des modèles » — diagnostic du pipeline
(Open-Meteo vs legacy/Météociel) : matrices de présence, anomalies, alignement
des cycles. Vue purement DIAGNOSTIQUE : n'écrit rien, ne pilote aucune
persistance (cf. app/data/presence.py)."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config as C
from app.data.db import run_label_text
from app.data.presence import (
    _missing_by_run, _nearest_om_run, legacy_presence, legacy_signature,
    openmeteo_presence)
from app.ui.theme import _plotly_template


def _cell_text(lead_days, members):
    """Contenu d'une cellule de matrice de présence : « 13.0 j · 51 m »."""
    if pd.isna(lead_days):
        return ""
    txt = f"{lead_days:.1f} j"
    if not pd.isna(members):
        txt += f"<br>{int(members)} m"
    return txt


def _presence_heatmap(mat, txt, missing, title, height):
    """Heatmap de présence : lignes = runs (plus récent en haut), colonnes =
    modèles, couleur = horizon en jours, texte = « horizon · membres ». Les
    cellules attendues mais absentes (`missing`) sont marquées d'une croix rouge."""
    fig = go.Figure(go.Heatmap(
        z=mat.values.astype(float), x=list(mat.columns), y=list(mat.index),
        text=txt.values, texttemplate="%{text}", textfont=dict(size=11),
        colorscale="YlGnBu", zmin=0, zmax=16.5, xgap=3, ygap=3,
        hoverongaps=False,
        colorbar=dict(title="Horizon<br>(jours)", thickness=12, len=0.9),
        hovertemplate="Run %{y}<br>Modèle %{x}<br>%{text}<extra></extra>"))
    for i, run in enumerate(mat.index):
        for j, model in enumerate(mat.columns):
            if bool(missing.iloc[i, j]):
                fig.add_annotation(x=model, y=run, text="✗", showarrow=False,
                                   font=dict(color="#C0392B", size=16, family="Arial Black"))
    fig.update_layout(title=title, height=height, template=_plotly_template(),
                      xaxis=dict(side="top"), yaxis=dict(autorange="reversed"),
                      margin=dict(t=90, l=10, r=10, b=10))
    return fig


def _build_matrices(pres, run_order, models, missing_by_key):
    """(mat, txt, missing) pour _presence_heatmap, à partir d'une table de présence
    [run_key, model, lead_days, n_members] déjà agrégée. `missing_by_key` : run_key
    → set des modèles attendus mais absents (marqués d'une croix rouge)."""
    mat = pres.pivot_table(index="run_key", columns="model", values="lead_days",
                           aggfunc="max").reindex(index=run_order, columns=models)
    mem = pres.pivot_table(index="run_key", columns="model", values="n_members",
                           aggfunc="max").reindex(index=run_order, columns=models)
    txt = pd.DataFrame("", index=run_order, columns=models)
    missing = pd.DataFrame(False, index=run_order, columns=models)
    for r in run_order:
        miss = missing_by_key.get(r, set())
        for mo in models:
            txt.loc[r, mo] = _cell_text(mat.loc[r, mo], mem.loc[r, mo])
            if mo in miss:
                missing.loc[r, mo] = True
    return mat, txt, missing


def page_diagnostic(runs, sig):
    st.title("🩺 Contrôle de présence des modèles")
    st.caption(
        "Vue de fiabilisation du **double run** (Open-Meteo vs legacy/Météociel) : "
        "pour chaque run, quel modèle est présent, **jusqu'à quelle échéance** et avec "
        "combien de membres. Une croix rouge ✗ = modèle **attendu à ce cycle** mais "
        "absent. Objectif : repérer d'un coup d'œil les incohérences (modèle manquant, "
        "run anormalement tronqué, cycles désalignés, horizon divergent).")

    om = openmeteo_presence(sig)
    lg_raw = legacy_presence(legacy_signature())

    if om.empty and lg_raw.empty:
        st.warning("Aucune donnée : ni parquet Open-Meteo, ni fichier legacy exploitable.")
        return

    # ── Synthèse des anomalies (Open-Meteo) ────────────────────────────────── #
    st.subheader("⚠️ Anomalies détectées (Open-Meteo)")
    alerts = []
    om_missing = _missing_by_run(om) if not om.empty else {}
    if not om.empty:
        # Absences : à chaque run, un modèle attendu à ce cycle ET collecté à cette
        # époque (cf. _missing_by_run) mais introuvable dans le run.
        for run_date in sorted(om["run_date"].unique(), reverse=True):
            attendus = sorted(om_missing.get(run_date, set()))
            if attendus:
                alerts.append(f"🔴 **{run_label_text(run_date)}** — modèle(s) attendu(s) "
                              f"absent(s) : **{', '.join(attendus)}**.")
        # Horizon quasi nul / négatif : la fenêtre réellement fraîche est vide ou
        # avant le cycle (souvent une queue entièrement NaN-ifiée par mask_stale_tail).
        for _, r in om[om["lead_h"] <= 24].iterrows():
            alerts.append(f"🟠 **{run_label_text(r['run_date'])} · {r['model']}** — horizon "
                          f"anormalement court ({r['lead_h']:.0f} h de données fraîches "
                          "seulement). Run partiel/tronqué ou queue masquée ?")
    if alerts:
        st.markdown("\n\n".join(alerts))
    else:
        st.success("✅ Aucun modèle attendu manquant et aucun horizon anormalement court "
                   "sur les runs Open-Meteo archivés.")

    # ── Matrice Open-Meteo ─────────────────────────────────────────────────── #
    st.markdown("---")
    st.subheader("🛰️ Open-Meteo — présence & horizon par run")
    st.caption(
        "Chaque case : **horizon post-cycle** (dernière échéance non-NaN ≥ cycle − cycle) "
        "en jours et **nombre de membres**. Couleur = horizon. ✗ rouge = attendu mais absent "
        "ou données uniquement pré-cycle (rebouchage) — un modèle n'est attendu qu'à partir "
        "de sa 1re collecte réelle (GEM depuis le "
        f"{pd.Timestamp(C.PIPELINE_LIVE_SINCE):%d/%m}, cycles 6Z/18Z de même). Avant cette "
        "bascule, la base est rétro-remplie depuis les xlsx Météociel (migrate.py).")
    if om.empty:
        st.info("Base Open-Meteo vide.")
    else:
        om_disp = om.copy()
        om_disp["run_key"] = om_disp["run_date"].map(run_label_text)
        om_disp["lead_days"] = om_disp["lead_h"] / 24
        order_df = (om_disp[["run_key", "run_date"]].drop_duplicates()
                    .sort_values("run_date", ascending=False))
        run_order = order_df["run_key"].tolist()
        missing_by_key = {run_label_text(rd): om_missing.get(rd, set())
                          for rd in om_disp["run_date"].unique()}
        mat, txt, missing = _build_matrices(om_disp, run_order, C.MODEL_LABELS, missing_by_key)
        st.plotly_chart(
            _presence_heatmap(mat, txt, missing,
                              "Open-Meteo — horizon (jours) & membres par run",
                              height=max(320, 30 * len(run_order) + 120)),
            width="stretch")

    # ── Matrice legacy / Météociel ─────────────────────────────────────────── #
    st.markdown("---")
    st.subheader("📄 Legacy / Météociel — présence & horizon par run")
    st.caption("Runs scrapés sur Météociel (0Z/12Z uniquement). run_date = celui déclaré "
               "dans l'en-tête du xlsx ; en cas de re-scrape, seul le plus récent est retenu.")
    if lg_raw.empty:
        st.info("Aucun fichier legacy exploitable dans " + C.LEGACY_FORECASTS_DIR + ".")
        lg = lg_raw
    else:
        # Dédup : un même (run_date, modèle) peut avoir été scrapé plusieurs jours →
        # on garde le scrape le plus récent (le plus complet en principe).
        lg = (lg_raw.dropna(subset=["run_date"])
              .sort_values("scrape_date", ascending=False)
              .drop_duplicates(subset=["run_date", "model"], keep="first"))
        lg_disp = lg.copy()
        lg_disp["run_key"] = lg_disp["run_date"].map(
            lambda t: f"{t:%d %b %Y} — {t.hour:02d}Z")
        lg_disp["lead_days"] = lg_disp["lead_h"] / 24
        order_df = (lg_disp[["run_key", "run_date"]].drop_duplicates()
                    .sort_values("run_date", ascending=False))
        run_order = order_df["run_key"].tolist()
        leg_models = list(C.LEGACY_MODELS)
        # Météociel publie 0Z/12Z avec les 3 modèles legacy : tout modèle absent
        # d'un fichier est une anomalie (croix rouge).
        present_by_key = lg_disp.groupby("run_key")["model"].agg(set).to_dict()
        missing_by_key = {rk: set(leg_models) - present_by_key.get(rk, set())
                          for rk in run_order}
        mat, txt, missing = _build_matrices(lg_disp, run_order, leg_models, missing_by_key)
        st.plotly_chart(
            _presence_heatmap(mat, txt, missing,
                              "Météociel — horizon (jours) & membres par run",
                              height=max(320, 30 * len(run_order) + 120)),
            width="stretch")

    # ── Confrontation Open-Meteo ↔ legacy ──────────────────────────────────── #
    st.markdown("---")
    st.subheader("🔀 Confrontation Open-Meteo ↔ legacy (runs alignés)")
    live = pd.Timestamp(C.PIPELINE_LIVE_SINCE)
    st.caption(
        "Pour chaque run legacy, on cherche le run Open-Meteo du **même modèle** au cycle "
        "le plus proche (±12 h) et on confronte cycle, horizon et membres. Un ⚠️ signale "
        "une incohérence à investiguer : cycles désalignés "
        f"(> {C.CROSS_CHECK_RUN_ALIGN_TOL_H} h), écart d'horizon > 1 j, ou run Open-Meteo "
        f"introuvable côté modèle. Limitée aux runs **à partir du {live:%d/%m/%Y}** : avant, "
        "la base Open-Meteo est rétro-remplie depuis les xlsx Météociel (comparaison "
        "circulaire). Météociel ne publiant ni 6Z ni 18Z, ces cycles Open-Meteo n'ont "
        "légitimement aucun équivalent legacy et ne sont pas confrontés.")
    lg_live = lg[lg["run_date"] >= live] if not lg.empty else lg
    if om.empty or lg_live.empty:
        st.info(f"Aucun run legacy à partir du {live:%d/%m/%Y} à confronter "
                "(ou base Open-Meteo vide).")
    else:
        rows = []
        for _, lr in lg_live.iterrows():
            ref = lr["run_date"]
            om_row = _nearest_om_run(om, lr["model"], ref)
            gap_h = (abs((om_row["run_utc"] - ref).total_seconds()) / 3600
                     if om_row is not None else np.nan)
            lead_om = om_row["lead_h"] / 24 if om_row is not None else np.nan
            lead_lg = lr["lead_h"] / 24 if not pd.isna(lr["lead_h"]) else np.nan
            flags = []
            if om_row is None:
                flags.append("OM absent")
            else:
                if gap_h > C.CROSS_CHECK_RUN_ALIGN_TOL_H:
                    flags.append("cycles désalignés")
                if not pd.isna(lead_om) and not pd.isna(lead_lg) and abs(lead_om - lead_lg) > 1:
                    flags.append("horizon divergent")
            rows.append({
                "_sort": ref,
                "Run legacy": f"{ref:%d %b} {ref.hour:02d}Z",
                "Modèle": lr["model"],
                "Cycle OM": (f"{om_row['run_utc']:%d %b %HZ}" if om_row is not None else "—"),
                "Δ cycle (h)": round(gap_h, 1) if not pd.isna(gap_h) else np.nan,
                "Horizon OM (j)": round(lead_om, 1) if not pd.isna(lead_om) else np.nan,
                "Horizon legacy (j)": round(lead_lg, 1) if not pd.isna(lead_lg) else np.nan,
                "Membres OM": (int(om_row["n_members"]) if om_row is not None else np.nan),
                "Membres legacy": int(lr["n_members"]),
                "Alerte": " · ".join(f"⚠️ {f}" for f in flags),
            })
        comp = (pd.DataFrame(rows).sort_values(["_sort", "Modèle"], ascending=[False, True])
                .drop(columns="_sort"))
        n_flag = int((comp["Alerte"] != "").sum())
        if n_flag:
            st.warning(f"⚠️ {n_flag} ligne(s) présentent une incohérence — voir colonne « Alerte ».")
        else:
            st.success("✅ Tous les runs legacy s'alignent proprement sur un run Open-Meteo "
                       "(cycle et horizon cohérents).")
        styler = comp.style.apply(
            lambda r: ["background-color:#fdecea;color:#611a15" if r["Alerte"] else ""
                       for _ in r], axis=1
        ).format({"Δ cycle (h)": "{:.1f}", "Horizon OM (j)": "{:.1f}",
                  "Horizon legacy (j)": "{:.1f}", "Membres OM": "{:.0f}"}, na_rep="—")
        st.dataframe(styler, width="stretch", height=460, hide_index=True)
