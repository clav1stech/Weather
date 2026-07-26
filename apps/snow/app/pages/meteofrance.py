# -*- coding: utf-8 -*-
"""Page « Maille fine Météo-France » — exploration DIRECTE des sources PNT.

Ces flux Météo-France (AROME-PI/IFS déterministes, PE-AROME/PE-ARPEGE membres)
ne sont ailleurs consommés qu'indirectement (coupe 48 h et frise de la Vue
d'ensemble). Cette page les expose en lecture seule, CHACUN selon sa nature :

  • déterministes PI/IFS → météogramme horaire (pluie/neige/T2m), pas de membres ;
  • ensembles PE-AROME/PE-ARPEGE → dispersion des membres par fenêtre de cumul
    + probabilité de dépassement des paliers ;
  • AROME-PI → frise du type de précipitation (ptype 15 min, mapping provisoire).

Aucune fusion entre sources, aucune substitution : une source absente se
dégrade en message explicite, jamais comblée par une autre (mêmes invariants
que le reste du pipeline neige)."""

import pandas as pd
import streamlit as st

from apps.snow import snow_config as SC
from ..data.db import (latest_mf_local_deterministic, latest_mf_local_members,
                       latest_mf_regional_members, load_mf_local,
                       load_mf_regional, mf_local_signature,
                       mf_regional_signature, run_label_text)
from ..domains.neige import weather_type
from ..domains.neige.charts import mf_meteogram, mf_member_box, ptype_strip

_RATIO = weather_type.RATIO_NEIGE_CM_PAR_MM
_PALIERS = SC.PALIERS_NEIGE_CM   # [1, 5, 20] cm


def _deterministic_series(det, site, now=None):
    """Série horaire d'un modèle déterministe (dernier run déjà filtré) au site
    demandé, échéances à venir : valid_time, pluie_mm, neige_mm, t2m_c.
    neige_mm = cumul neige (mm éq. eau) ; pluie_mm = total − neige (borné ≥ 0)."""
    if det is None or det.empty:
        return pd.DataFrame(columns=["valid_time", "pluie_mm", "neige_mm", "t2m_c"])
    now = pd.Timestamp(now or pd.Timestamp.now())
    rows = det[(det["site"] == site)
               & (pd.to_datetime(det["valid_time"]) >= now)].copy()
    if rows.empty:
        return pd.DataFrame(columns=["valid_time", "pluie_mm", "neige_mm", "t2m_c"])
    rows = rows.sort_values("valid_time")
    total = pd.to_numeric(rows["precip"], errors="coerce")
    snow = pd.to_numeric(rows["neige_eau"], errors="coerce").clip(lower=0.0)
    snow = snow.where(snow <= total, total)          # neige jamais > total
    return pd.DataFrame({
        "valid_time": pd.to_datetime(rows["valid_time"]).values,
        "pluie_mm": (total - snow).clip(lower=0.0).values,
        "neige_mm": snow.values,
        "t2m_c": pd.to_numeric(rows["t2m"], errors="coerce").values,
    })


def _member_windows(members, site):
    """Cumuls par membre et par fenêtre glissante (dernier run déjà filtré).

    Renvoie un format long : window (libellé), window_order (fin de fenêtre en
    h), member, neige_cm, precip_mm. Chaque ligne = un cumul de fenêtre d'un
    membre ; la dispersion inter-membres est le signal probabiliste local."""
    cols = ["window", "window_order", "member", "neige_cm", "precip_mm"]
    if members is None or members.empty:
        return pd.DataFrame(columns=cols)
    rows = members[members["site"] == site].copy()
    if rows.empty or "run_date" not in rows:
        return pd.DataFrame(columns=cols)
    run_date = pd.to_datetime(rows["run_date"]).max()
    end_h = ((pd.to_datetime(rows["valid_time"]) - run_date)
             / pd.Timedelta(hours=1)).round().astype(int)
    period = pd.to_numeric(rows["period_h"], errors="coerce").fillna(24).astype(int)
    start_h = (end_h - period).clip(lower=0)
    out = pd.DataFrame({
        "window": [f"{s}–{e} h" for s, e in zip(start_h, end_h)],
        "window_order": end_h.values,
        "member": rows["member"].values,
        "neige_cm": (pd.to_numeric(rows["neige_eau"], errors="coerce")
                     * _RATIO).values,
        "precip_mm": pd.to_numeric(rows["precip"], errors="coerce").values,
    })
    return out.sort_values("window_order").reset_index(drop=True)


def _exceedance_table(dist, thresholds=_PALIERS):
    """Probabilité de dépassement (part des membres) des paliers de neige, par
    fenêtre. Un membre à NaN est exclu du dénominateur, jamais compté sec."""
    if dist is None or dist.empty or not dist["neige_cm"].notna().any():
        return pd.DataFrame()
    rows = []
    for _, group in dist.groupby("window_order"):
        valid = group["neige_cm"].dropna()
        if valid.empty:
            continue
        row = {"Fenêtre": group["window"].iloc[0]}
        for seuil in thresholds:
            row[f"≥ {seuil:.0f} cm"] = f"{(valid >= seuil).mean() * 100:.0f} %"
        row["Membres"] = len(valid)
        rows.append(row)
    return pd.DataFrame(rows)


def _ptype_frise(pi_df, site="village", now=None):
    """Frise du type de précipitation AROME-PI (dernier run) au site demandé :
    valid_time, code, categorie, label — échéances à venir."""
    cols = ["valid_time", "code", "categorie", "label"]
    if pi_df is None or pi_df.empty or "ptype" not in pi_df:
        return pd.DataFrame(columns=cols)
    now = pd.Timestamp(now or pd.Timestamp.now())
    rows = pi_df[(pi_df["site"] == site)
                 & (pd.to_datetime(pi_df["valid_time"]) >= now)
                 & pi_df["ptype"].notna()].sort_values("valid_time")
    if rows.empty:
        return pd.DataFrame(columns=cols)
    out = []
    for vt, code in zip(pd.to_datetime(rows["valid_time"]), rows["ptype"]):
        decoded = weather_type.classify_ptype(code)
        if decoded is None:
            continue
        categorie, label = decoded
        out.append({"valid_time": vt, "code": float(code),
                    "categorie": categorie, "label": label})
    return pd.DataFrame(out, columns=cols)


def _site_selector(key):
    code = st.radio("Point", SC.SITE_CODES, horizontal=True, key=key,
                    format_func=lambda c: SC.SITE_BY_CODE[c]["nom"])
    return code


def page_meteofrance(runs, sig):
    st.title("🇫🇷 Maille fine Météo-France — Megève")
    st.caption("Sources PNT Météo-France en lecture directe : haute résolution "
               "court terme (AROME-PI/IFS) et dimension probabiliste locale "
               "(PE-AROME/PE-ARPEGE). Complément des vues combinées, jamais "
               "fusionné avec l'ensemble global.")

    mf_sig = mf_local_signature()
    local = load_mf_local(mf_sig)
    if local is None or local.empty:
        st.info("Aucune donnée Météo-France PNT en base pour l'instant.")
        return

    # ------------------------------------- A. Météogramme déterministe --
    st.subheader("Météogramme déterministe")
    det_models = [m for m in (SC.AROME_PI_MODEL, SC.AROME_IFS_MODEL)
                  if not local[(local["model"] == m)
                               & (local["kind"] == "deterministic")].empty]
    if not det_models:
        st.info("Aucun run déterministe AROME-PI/IFS disponible.")
    else:
        model = st.radio("Modèle", det_models, horizontal=True, key="mf_det_model")
        site = _site_selector("mf_det_site")
        det = latest_mf_local_deterministic(mf_sig, model)
        series = _deterministic_series(det, site)
        fig = mf_meteogram(series, f"{model} — {SC.SITE_BY_CODE[site]['nom']}")
        if fig is not None:
            run = pd.to_datetime(det["run_date"]).max()
            st.caption(f"{model} {run_label_text(run)} — {len(series)} échéances "
                       "horaires à venir. Pluie et neige en mm équivalent eau.")
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Pas d'échéance à venir dans le dernier run de ce modèle.")

    # ------------------------------- B. Dispersion des ensembles régionaux --
    st.subheader("Ensembles régionaux — dispersion des membres")
    st.caption("Chaque boîte = les cumuls de neige des membres sur une fenêtre "
               "(P90/médiane/P10 + points). Le tableau donne la probabilité de "
               "dépasser chaque palier.")
    seuils = {f"{s:.0f} cm": s for s in _PALIERS}
    regional = [
        ("PE-AROME", latest_mf_local_members(mf_sig), "25 membres"),
        ("PE-ARPEGE", latest_mf_regional_members(mf_regional_signature()),
         "35 membres"),
    ]
    any_regional = False
    for label, members, taille in regional:
        if members is None or members.empty:
            st.caption(f"⚪ {label} indisponible ({taille}) — cas normal si le "
                       "cycle n'est pas encore collecté.")
            continue
        any_regional = True
        site = _site_selector(f"mf_ens_site_{label}")
        dist = _member_windows(members, site)
        if dist.empty:
            st.caption(f"⚪ {label} : aucune valeur exploitable à ce point.")
            continue
        run = pd.to_datetime(members["run_date"]).max()
        fig = mf_member_box(dist, "neige_cm", "cm",
                            f"{label} {run_label_text(run)} — "
                            f"{SC.SITE_BY_CODE[site]['nom']}", seuils_h=seuils)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        table = _exceedance_table(dist)
        if not table.empty:
            st.dataframe(table, width="stretch", hide_index=True)
    if not any_regional:
        st.info("Aucun ensemble régional Météo-France n'est actuellement en base.")

    # ------------------------------------------- C. Frise type de précip --
    st.subheader("Type de précipitation AROME-PI")
    pi_df = latest_mf_local_deterministic(mf_sig, SC.AROME_PI_MODEL)
    frise = _ptype_frise(pi_df)
    fig = ptype_strip(frise)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")
        st.caption("Mapping des codes ptype PROVISOIRE (hypothèse WMO GRIB2 "
                   "4.201, à valider in situ sur un premier épisode précipitant) "
                   "— le code brut est lisible au survol.")
    else:
        st.info("Type de précipitation AROME-PI indisponible (aucun run récent "
                "ou aucune échéance à venir).")
