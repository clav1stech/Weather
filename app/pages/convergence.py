# -*- coding: utf-8 -*-
"""Page « Révisions & convergence » — comment la prévision d'une même date a
évolué d'un run à l'autre. Compare des super-ensembles COMPLÉTÉS (backfill
échéance par échéance des modèles principaux, cf. completed_pooled_sub) pour ne
jamais comparer « 4 modèles vs 1 »."""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from app.data.db import _run_utc_naive, utc_cycle
from app.data.presence import openmeteo_presence
from app.data.runsets import (
    _convergence_runs, completed_super_ensemble_daily, main_labels_expected_at)
from app.ui.theme import _ink, _plotly_template, _rgba


def page_convergence(runs, sig):
    st.title("🔄 Révisions & convergence des prévisions")
    st.caption(
        "Chaque nouveau calcul (run) corrige le précédent. Cette page montre **comment la "
        "prévision d'une même date a évolué d'un run à l'autre** : si elle se stabilise, "
        "on peut s'y fier ; si elle bouge encore beaucoup, l'incertitude reste forte.")

    runs_full = runs.reset_index(drop=True)  # historique complet, AVANT le filtre d'affichage
    runs = _convergence_runs(runs)
    if len(runs) < 2:
        st.warning("Il faut au moins 2 runs pour analyser la convergence.")
        return

    # --- Historique commun : médiane/P10/P90 journalières de CHAQUE run (recalcul brut) ---
    # Un modèle principal absent d'un run est backfillé depuis le run antérieur le plus
    # proche qui le contient (jusqu'à n-3) : on compare ainsi des super-ensembles à
    # modèles comparables, pas « 4 modèles vs 1 ». backfill_src : {run_date -> sources}.
    # La recherche se fait sur `runs_full` (tous les cycles, pas seulement 0Z/12Z) : le
    # filtre d'affichage de `_convergence_runs` allège l'axe des runs tracés mais ne doit
    # pas faire disparaître un run 6Z/18Z par ailleurs valide de la recherche de backfill.
    full_pos = {rd: i for i, rd in enumerate(runs_full["run_date"])}
    records = []
    backfill_src = {}
    for pos in range(len(runs)):
        r = runs.iloc[pos]
        syn, sources = completed_super_ensemble_daily(runs_full, full_pos[r["run_date"]], sig)
        backfill_src[r["run_date"]] = sources
        if syn is None or syn.empty:
            continue
        for _, row in syn.iterrows():
            target = pd.Timestamp(row["valid_time"]).normalize()
            run_dt = pd.Timestamp(r["run_date"])
            if target < run_dt.normalize():
                continue
            # Délai réel run → échéance (en jours, fractionnaire) : sépare les cycles
            # d'un même jour → supprime les dents de scie du graphique de convergence.
            lead = (pd.Timestamp(row["valid_time"]) - run_dt).total_seconds() / 86400
            records.append({"run_dt": r["run_date"], "lead": lead, "target": target,
                            "median": row.get("Médiane"), "p10": row.get("P10"),
                            "p90": row.get("P90")})
    long = pd.DataFrame(records).dropna(subset=["median"])
    if long.empty:
        st.warning("Données insuffisantes.")
        return

    # Runs affichés dont au moins un modèle principal a été partiellement complété
    # (échéances comblées par un run antérieur) ou reste introuvable dans la
    # fenêtre n-3. backfilled / manquants : {run_date -> ...}. `sources[model]`
    # est la liste des run_date utilisés pour CE modèle, du plus récent au plus
    # ancien (plusieurs si un run partiel a été complété par un run antérieur).
    runs_affiches = set(long["run_dt"].unique())
    label_par_dt = {r["run_date"]: r["label"] for _, r in runs.iterrows()}
    # first_seen_utc : garde symétrique avec _missing_by_run — on ne signale « manque »
    # que si le modèle avait déjà été collecté à cette date (évite les faux positifs
    # pour les runs antérieurs à la 1re collecte d'un modèle, ex. ECMWF 17 Jun).
    _om_pres = openmeteo_presence(sig)
    first_seen_utc = (_om_pres.groupby("model")["run_utc"].min().to_dict()
                      if not _om_pres.empty else {})
    backfilled, manquants = {}, {}
    for run_dt, sources in backfill_src.items():
        if run_dt not in runs_affiches:
            continue
        expected_at = set(main_labels_expected_at(run_dt))
        # On ne signale "repris" que pour les modèles attendus à ce cycle ET totalement
        # absents du run courant (srcs[0] != run_dt). Les cas où le run courant a des
        # données mais avec quelques trous comblés silencieusement ne sont PAS alertés :
        # si la donnée a été trouvée pour ce run, le contrôle des modèles le montre, et
        # l'alerte "complété par" serait incohérente avec le fait d'avoir un run présent.
        bf = {m: srcs for m, srcs in sources.items()
              if m in expected_at and srcs and srcs[0] != run_dt}
        run_utc_val = _run_utc_naive(run_dt)
        miss = [m for m in expected_at
                if not sources.get(m)
                and m in first_seen_utc
                and run_utc_val >= first_seen_utc[m]]
        if bf:
            backfilled[run_dt] = bf
        if miss:
            manquants[run_dt] = miss
    imparfaits = set(backfilled) | set(manquants)

    def _run_tick(dt):
        u = utc_cycle(dt)
        s = f"{u:%d %b} {u.hour:02d}Z"
        return f"<i>{s}*</i>" if dt in imparfaits else s

    if imparfaits:
        parts = []
        for run_dt in sorted(imparfaits, reverse=True):
            notes = []
            for m, srcs in backfilled.get(run_dt, {}).items():
                extra = [s for s in srcs if s != run_dt]
                if not extra:
                    continue
                extra_txt = ", ".join(f"{utc_cycle(s):%d %b} {utc_cycle(s).hour:02d}Z" for s in extra)
                notes.append(f"{m} repris par {extra_txt}")
            if run_dt in manquants:
                notes.append(f"manque {', '.join(manquants[run_dt])}")
            parts.append(f"{label_par_dt[run_dt]} ({' ; '.join(notes)})")
        st.warning(
            "⚠️ Certains runs affichés ont un modèle principal **absent** : son dernier run "
            "disponible est repris à sa place (jusqu'à n-3, soit ~1 jour) pour comparer des "
            "super-ensembles à nombre de modèles équivalent. Les modèles d'appoint (ex. GEM) "
            "ne sont jamais ainsi repris — ils n'apparaissent qu'à leurs propres cycles réels "
            "(0Z/12Z). Ces runs sont notés en *italique* avec un astérisque (\\*).\n\n"
            + " · ".join(parts))

    # ── 1. Révisions vs runs précédents ──
    st.subheader("📐 Révisions de la médiane vs runs précédents")
    st.caption("Chaque barre = écart de la médiane de ce run vs un run antérieur, "
               "pour une même date. Rouge = hausse, bleu = baisse.")
    idx = st.selectbox("Run de référence", runs.index,
                       format_func=lambda i: runs.loc[i, "label"], key="conv_run_sel")
    ref_run = runs.loc[idx]
    ref_med = long[long["run_dt"] == ref_run["run_date"]].set_index("target")["median"]
    prev_runs = [rd for rd in sorted(long["run_dt"].unique(), reverse=True)
                 if rd < ref_run["run_date"]][:5]
    if not ref_med.empty and prev_runs:
        fig = go.Figure()
        for i, pr in enumerate(prev_runs):
            prev_med = long[long["run_dt"] == pr].set_index("target")["median"]
            delta = (ref_med - prev_med).dropna()
            if delta.empty:
                continue
            colors = [_rgba("#E74C3C", 0.8) if v > 0 else _rgba("#2980B9", 0.8)
                      if v < 0 else _rgba("#888888", 0.4) for v in delta.values]
            fig.add_trace(go.Bar(x=delta.index, y=delta.values, offsetgroup=i,
                                 name="Δ vs " + _run_tick(pr), marker_color=colors))
        fig.add_hline(y=0, line_color=_ink(), line_width=1.5)
        fig.update_layout(height=380, template=_plotly_template(), hovermode="x unified",
                          barmode="group", xaxis_title="Date prévue", yaxis_title="Révision (°C)",
                          legend=dict(orientation="h", y=1.12), margin=dict(t=30, l=10, r=10, b=10))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Pas de run antérieur comparable.")

    st.markdown("---")

    # ── 2. Convergence par date cible ──
    st.subheader("📈 Comment la prévision a évolué au fil des jours")
    st.caption(
        "**Un panneau = une date cible.** Dans chacun, la courbe montre comment la médiane "
        "prévue a évolué selon l'ancienneté du run ; la bande = l'incertitude P10–P90. "
        "À droite (J-0) = prévision la plus récente, à gauche = prévision lointaine. "
        "Une courbe qui se stabilise vers la droite = le modèle a convergé. "
        "Un panneau qui s'arrête tôt (ex. à J-4) est **normal** : il n'existe pas encore de "
        "run plus proche de cette date (échéance future).")
    today = pd.Timestamp(datetime.now().date())
    targets = sorted(t for t in long["target"].unique() if t >= today)
    chosen = st.multiselect("Dates cibles", targets, default=targets[:5],
                            format_func=lambda t: pd.Timestamp(t).strftime("%d %b %Y"),
                            key="conv_targets")
    if chosen:
        palette = ["#E74C3C", "#2980B9", "#27AE60", "#8E44AD", "#E67E22",
                   "#16A085", "#C0392B", "#2C3E50"]
        chosen_sorted = sorted(chosen)
        n = len(chosen_sorted)
        ncols = min(3, n)
        nrows = (n + ncols - 1) // ncols
        titles = [pd.Timestamp(t).strftime("%d %b") for t in chosen_sorted]
        sub_l = long[long["target"].isin(chosen_sorted)]
        max_lead = float(sub_l["lead"].max())
        ymin, ymax = float(sub_l["p10"].min()), float(sub_l["p90"].max())
        marge = max(0.5, (ymax - ymin) * 0.08)
        fig = make_subplots(rows=nrows, cols=ncols, subplot_titles=titles,
                            horizontal_spacing=0.04, vertical_spacing=0.14)
        for i, t in enumerate(chosen_sorted):
            r, cpos = i // ncols + 1, i % ncols + 1
            d = long[long["target"] == t].sort_values("lead", ascending=False)
            if d.empty:
                continue
            c = palette[i % len(palette)]
            fig.add_trace(go.Scatter(x=d["lead"], y=d["p90"], mode="lines", line=dict(width=0),
                                     hoverinfo="skip", showlegend=False), row=r, col=cpos)
            fig.add_trace(go.Scatter(x=d["lead"], y=d["p10"], mode="lines", line=dict(width=0),
                                     fill="tonexty", fillcolor=_rgba(c, 0.15),
                                     hoverinfo="skip", showlegend=False), row=r, col=cpos)
            fig.add_trace(go.Scatter(x=d["lead"], y=d["median"], mode="lines+markers",
                                     line=dict(color=c, width=2.5), marker=dict(size=5),
                                     showlegend=False, customdata=d[["p10", "p90"]].values,
                                     hovertemplate=(f"{titles[i]} — J-%{{x:.1f}}<br>"
                                                    "Médiane : %{y:.1f} °C<br>P10–P90 : "
                                                    "%{customdata[0]:.1f}–%{customdata[1]:.1f}"
                                                    "<extra></extra>")), row=r, col=cpos)
            fig.add_hline(y=d.iloc[-1]["median"], line=dict(color=c, width=1, dash="dot"),
                          opacity=0.4, row=r, col=cpos)
        fig.update_yaxes(range=[ymin - marge, ymax + marge])
        fig.update_xaxes(autorange=False, range=[max_lead + 0.3, -0.3], ticksuffix=" j")
        fig.update_yaxes(title_text="Temp. prévue (°C)", col=1)
        for cpos in range(1, ncols + 1):
            fig.update_xaxes(title_text="Jours avant l'échéance", row=nrows, col=cpos)
        fig.update_layout(height=240 * nrows, template=_plotly_template(),
                          margin=dict(t=40, l=10, r=10, b=10))
        st.plotly_chart(fig, width="stretch")

    st.markdown("---")

    # ── 3. Heatmap des révisions run-à-run ──
    st.subheader("🗺️ Carte des révisions run-à-run")
    st.caption(
        "Rouge = le run a **revu à la hausse** la prévision vs le run précédent. "
        "Bleu = révision à la baisse. Blanc = pas de changement = prévision stable et fiable.")
    # Le diff se calcule sur TOUS les runs (y compris expirés) pour que chaque run
    # affiché reste comparé à son run précédent réel, même si celui-ci a disparu de
    # l'axe affiché ensuite. Un run est « expiré » quand sa dernière échéance
    # (dernier `target`) est déjà passée : il ne représente plus qu'une prévision
    # entièrement réalisée, sans intérêt pour lire les révisions en cours — on ne
    # l'exclut qu'après coup, comme colonne affichée.
    pivot = long.pivot_table(index="target", columns="run_dt", values="median").sort_index()
    delta_pivot = pivot.diff(axis=1)
    last_target_by_run = long.groupby("run_dt")["target"].max()
    active_runs = [c for c in delta_pivot.columns if last_target_by_run.get(c, pd.NaT) >= today]
    delta_pivot = delta_pivot[active_runs].dropna(axis=0, how="all")
    if not delta_pivot.empty:
        abs_max = max(delta_pivot.abs().max().max(), 0.5)
        heat = go.Figure(data=go.Heatmap(
            z=delta_pivot.values, x=[_run_tick(c) for c in delta_pivot.columns],
            y=[pd.Timestamp(i).strftime("%d %b") for i in delta_pivot.index],
            colorscale="RdBu_r", zmid=0, zmin=-abs_max, zmax=abs_max,
            colorbar=dict(title="Révision (°C)"),
            hovertemplate="Run %{x}<br>Cible %{y}<br>Révision : %{z:+.1f} °C<extra></extra>"))
        heat.update_layout(height=max(300, 26 * len(delta_pivot.index) + 120),
                           template=_plotly_template(), xaxis_title="Run", yaxis_title="Date prévue",
                           margin=dict(t=10, l=10, r=10, b=10))
        st.plotly_chart(heat, width="stretch")
