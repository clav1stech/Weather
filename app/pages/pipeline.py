# -*- coding: utf-8 -*-
"""Page « Lancer le pipeline » (local uniquement) : lancement manuel de
Forecast.py / run_dual.py, import ciblé legacy → parquet et historique du
contrôle croisé. Seule page qui ÉCRIT dans les données — toujours via les
garde-fous de app/data/legacy_import.py et Forecast.persist."""

import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import config as C
import run_dual
from app.data.legacy_import import import_legacy_run, legacy_import_candidates
from app.data.presence import legacy_signature


def _run_script(*args, timeout=300):
    """Lance un script Python du projet en sous-processus, capture stdout/stderr."""
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run([sys.executable, *args], cwd=C.BASE_DIR, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=child_env)
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    return proc.returncode, output


def _execute(entries):
    """Exécute une séquence de (label, script, timeout) avec spinner, et renvoie
    la liste [(label, code, output)] — stockée en session_state puis rendue
    PLEINE LARGEUR sous les colonnes (le déroulé d'un fetch ne doit pas être
    tassé dans une des 4 colonnes). code None = exception gérée (timeout/erreur),
    `output` porte alors le message."""
    out = []
    for label, script, timeout in entries:
        with st.spinner(f"Exécution de {script}…"):
            try:
                code, output = _run_script(os.path.join(C.BASE_DIR, script),
                                           timeout=timeout)
                out.append((label, code, output or "(aucune sortie)"))
            except subprocess.TimeoutExpired:
                out.append((label, None, f"⏱️ Délai dépassé ({timeout} s)."))
            except Exception as e:  # noqa: BLE001
                out.append((label, None, f"Erreur : {e}"))
    st.cache_data.clear()
    return out


def cross_check_log_signature():
    try:
        return os.path.getmtime(C.CROSS_CHECK_LOG_PATH)
    except OSError:
        return None


@st.cache_data(show_spinner=False)
def load_cross_check_log(_sig):
    if _sig is None or not os.path.exists(C.CROSS_CHECK_LOG_PATH):
        return pd.DataFrame()
    df = pd.read_csv(C.CROSS_CHECK_LOG_PATH, parse_dates=["checked_at", "run_date", "valid_time"])
    return df.sort_values("checked_at", ascending=False).reset_index(drop=True)


def page_run(runs, sig):
    st.title("🚀 Lancer le pipeline")

    now_utc = datetime.now(ZoneInfo("UTC"))
    missing = run_dual._missing_legacy_slots(now_utc)
    st.caption(f"Heure UTC actuelle : **{now_utc:%H:%M}**")
    if missing:
        st.info(f"📥 À rattraper côté Météociel : **{', '.join(missing)}** (publié mais pas "
                "encore en stock) — le double run le scrapera.")
    else:
        st.success("✅ Stock legacy à jour : rien à rattraper pour l'instant.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.subheader("① Open-Meteo seul")
        st.caption("`Forecast.py` : interroge l'API, détecte le cycle par modèle, met à jour "
                  "`data/database_paris.parquet`. ~10-30 s.")
        if st.button("▶️ Lancer Forecast.py", type="secondary"):
            st.session_state["pipeline_results"] = _execute(
                [("Pipeline Open-Meteo", "Forecast.py", 300)])

    with col2:
        st.subheader("② Double run + contrôle croisé")
        st.caption("`run_dual.py` : Open-Meteo, puis (si créneau favorable) scrape Météociel "
                  "+ comparaison ECMWF/AIFS/GEFS échéance par échéance. ~30-90 s.")
        if st.button("🔁 Lancer le double run", type="primary"):
            st.session_state["pipeline_results"] = _execute(
                [("Double run", "run_dual.py", 600)])

    with col3:
        st.subheader("③ Tx/Tn haute résolution")
        st.caption("`forecast_t2m_hd.py` : API Forecast standard (Météo-France/DWD ICON), "
                  "met à jour `data/database_paris_t2m.parquet` (flux annexe, 4 j). ~5-15 s.")
        if st.button("🌡️ Lancer Tx/Tn HD", type="secondary"):
            st.session_state["pipeline_results"] = _execute(
                [("Pipeline Tx/Tn HD", "forecast_t2m_hd.py", 300)])

    with col4:
        st.subheader("④ Observations + instantané")
        st.caption("`fetch_observations.py` (paquet horaire Météo-France, "
                  "4 stations), `fetch_observations_6m.py` (infra-horaire 6 min, "
                  "4 stations) puis `fetch_instant.py` (Open-Meteo "
                  "minutely_15, prévision 15 min) — flux annexes indépendants, à "
                  "la suite. ~15-30 s.")
        if st.button("📡 Lancer obs + 6 min + instant", type="secondary"):
            st.session_state["pipeline_results"] = _execute([
                ("Observations Météo-France", "fetch_observations.py", 60),
                ("Observations 6 min", "fetch_observations_6m.py", 60),
                ("Prévision instantanée 15 min", "fetch_instant.py", 60),
            ])

    # Déroulé de la dernière exécution — rendu PLEINE LARGEUR sous les colonnes
    # (jamais tassé dans une seule des 4 colonnes ci-dessus).
    results = st.session_state.get("pipeline_results")
    if results:
        st.markdown("#### 📄 Déroulé de la dernière exécution")
        for label, code, output in results:
            if code == 0:
                st.success(f"✅ {label} : terminé.")
            elif code is None:
                st.error(f"❌ {label} : {output}")
                continue
            else:
                st.error(f"❌ {label} : code de sortie {code}.")
            st.code(output)

    st.markdown("---")
    st.subheader("🩹 Import ciblé depuis le legacy")
    st.caption("Comble une **absence avérée** du parquet Open-Meteo depuis un xlsx "
               "Météociel (même principe que `migrate.py`, mais un seul couple "
               "run × modèle à la fois). Ne liste que les couples présents en "
               "legacy et **sans aucune donnée valide** côté Open-Meteo — jamais "
               "d'écrasement. Sauvegarde datée du parquet avant écriture ; les "
               "xlsx restent en lecture seule.")
    cands = legacy_import_candidates(sig, legacy_signature())
    if cands.empty:
        st.info("Aucune absence à combler : tous les runs legacy sont déjà "
                "couverts par des données valides dans le parquet Open-Meteo.")
    else:
        def _cand_label(r):
            reach = "" if pd.isna(r["lead_h"]) else f" · portée {r['lead_h']/24:.1f} j"
            return (f"{r['model']} — run {r['run_date']:%d/%m/%Y} "
                    f"{r['run_date'].hour}Z · {int(r['n_members'])} membres"
                    f"{reach} · {r['file']}")

        choice = st.selectbox("Run legacy à importer (absent du parquet)",
                              cands.to_dict("records"), format_func=_cand_label)
        confirm = st.checkbox("Je confirme l'import de ce run dans le parquet "
                              "(sauvegarde datée créée automatiquement avant écriture).")
        if st.button("📥 Importer ce run", type="primary", disabled=not confirm):
            with st.spinner("Import en cours…"):
                try:
                    ok, msg = import_legacy_run(choice["file"], choice["model"],
                                                choice["run_date"])
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, f"Erreur inattendue : {e}"
            if ok:
                st.success(f"✅ {msg}")
                st.cache_data.clear()
            else:
                st.error(f"❌ {msg}")

    st.markdown("---")
    st.subheader("🔍 Historique du contrôle croisé")
    st.caption("Comparaison **médiane d'ensemble** (ECMWF/AIFS/GEFS) entre Open-Meteo et "
              "Météociel, échéance par échéance. Seuil de signalement (⚠️) élargi avec "
              f"l'échéance, de {C.CROSS_CHECK_TOLERANCE_BASE_C:.1f} à "
              f"{C.CROSS_CHECK_TOLERANCE_CAP_C:.1f} °C (un bug pipeline ressort à courte "
              "échéance ; à longue échéance deux ensembles distincts divergent légitimement).")
    log_sig = cross_check_log_signature()
    log = load_cross_check_log(log_sig)
    if log.empty:
        st.info("Aucun contrôle croisé enregistré pour l'instant. Lance le double run à un "
               "créneau favorable (10:15 ou 22:15 UTC) pour en générer un.")
    else:
        latest_check = log["checked_at"].max()
        latest = log[log["checked_at"] == latest_check]
        st.caption(f"Dernier contrôle : **{latest_check:%d/%m/%Y %Hh%M}** UTC · "
                  f"run **{pd.Timestamp(latest['run_date'].iloc[0]):%d %b %Hh}** UTC")
        summary = latest.groupby(["model", "metric"]).agg(
            n=("diff", "size"), mean_abs=("diff", lambda s: s.abs().mean()),
            max_abs=("diff", lambda s: s.abs().max()), n_flag=("flag", "sum")).reset_index()
        summary.columns = ["Modèle", "Métrique", "N", "Écart moyen abs.", "Écart max abs.", "Flags"]
        st.dataframe(summary.style.format({"Écart moyen abs.": "{:.2f}", "Écart max abs.": "{:.2f}"}),
                    width="stretch", hide_index=True)

        if int(latest["flag"].sum()):
            st.warning(f"⚠️ {int(latest['flag'].sum())} échéance(s) au-delà du seuil sur le "
                      "dernier contrôle — détail ci-dessous.")
        with st.expander("📋 Détail du dernier contrôle"):
            detail_cols = ["model", "metric", "valid_time", "lead_h", "legacy_value",
                           "openmeteo_value", "diff", "tol", "flag"]
            # Rétro-compat : un log antérieur au format lead-aware n'a ni lead_h ni tol.
            show = latest[[c for c in detail_cols if c in latest.columns]].sort_values(
                "diff", key=lambda s: s.abs(), ascending=False)
            styler = show.style.apply(
                lambda r: ["background-color:#fdecea;color:#611a15" if r["flag"] else ""
                           for _ in r], axis=1
            ).format({"legacy_value": "{:.1f}", "openmeteo_value": "{:.1f}",
                      "diff": "{:+.2f}", "tol": "{:.2f}"})
            st.dataframe(styler, width="stretch", height=400, hide_index=True)

        with st.expander("📜 Historique complet (tous contrôles)"):
            st.dataframe(log, width="stretch", height=400, hide_index=True)
            st.download_button("⬇️ Télécharger l'historique (CSV)",
                               log.to_csv(index=False).encode("utf-8-sig"),
                               file_name="cross_check_log.csv", mime="text/csv")
