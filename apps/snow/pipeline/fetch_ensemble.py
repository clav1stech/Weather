# -*- coding: utf-8 -*-
"""Pipeline neige Megève — flux ENSEMBLE/SYNOPTIQUE (pilote analyse/convergence).

Deux appels HTTP sur le même endpoint /v1/ensemble, chacun multi-points
(village + sommet, un seul call par API) :
  1. Ensemble API (membres individuels) — modèles ENS_MODELS. Les membres ne
     sont retenus par Open-Meteo que ~3 j : ce sous-flux sert le spread court
     terme, pas l'historique ;
  2. Ensemble Mean API (mêmes familles, identifiants `_ensemble_mean`) —
     moyenne + spread, rétention longue : c'est le sous-flux de référence pour
     l'historique et la convergence.

Les deux alimentent le MÊME parquet (snow_config.DB_ENS_PATH) sous des labels
de modèle DISTINCTS (ECMWF vs ECMWF_MEAN…) : chaque sous-flux garde son propre
couple (run_date, modèle), donc sa propre fraîcheur/complétude/anti-régression
— les deux endpoints ne se renouvellent pas exactement en même temps pour un
même cycle, un label partagé les ferait s'écraser mutuellement au fil des
polls (le remplacement par couple emporte toutes les lignes du couple).

Mécanique de persistance : core/pipeline/ensemble_runs.py (version générique
des mécanismes du pipeline canicule — fraîcheur empirique échéance par
échéance, portée réelle contiguë, persistance conditionnée à la complétude,
garde anti-régression, fusion (run_date, modèle), écriture atomique).
"""

import os
import re
import sys
import datetime as dt

# Scripts lancés par chemin (python apps/snow/pipeline/fetch_ensemble.py) :
# seul leur dossier est sur sys.path — on ajoute la racine du repo (→ core et
# namespace apps.snow).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "..")))

import numpy as np
import pandas as pd

from apps.snow import snow_config as SC
from core.pipeline import ensemble_runs as ER
from core.services import openmeteo as OM


# --------------------------------------------------------------------------- #
#  Requêtes API
# --------------------------------------------------------------------------- #
def fetch_members_payload():
    """Ensemble API : membres individuels, tous modèles, les deux points."""
    params = {
        **OM.multi_coord_params(SC.SITES),
        "hourly": ",".join(v["api"] for v in SC.ENS_VARIABLES),
        "models": ",".join(m["api"] for m in SC.ENS_MODELS),
        "timezone": SC.TIMEZONE,
        "forecast_days": SC.FORECAST_DAYS,
    }
    return OM.fetch_json(SC.ENS_API_URL, params, SC.HTTP_TIMEOUT,
                         context="Open-Meteo Ensemble")


def fetch_mean_payload():
    """Ensemble Mean API : moyenne (nom de variable simple) + spread
    (suffixe `_spread`, pour les variables qui le déclarent)."""
    hourly = [v["api"] for v in SC.ENS_VARIABLES]
    hourly += [f"{v['api']}_spread" for v in SC.ENS_VARIABLES if v.get("spread")]
    params = {
        **OM.multi_coord_params(SC.SITES),
        "hourly": ",".join(hourly),
        "models": ",".join(m["api"] for m in SC.MEAN_MODELS),
        "timezone": SC.TIMEZONE,
        "forecast_days": SC.FORECAST_DAYS,
    }
    return OM.fetch_json(SC.ENS_API_URL, params, SC.HTTP_TIMEOUT,
                         context="Open-Meteo Ensemble Mean")


def _run_dates(models, now_utc):
    """{label → run_date} : Metadata API officielle en priorité (cycle exact,
    hors quota), repli horloge sinon. Les slugs sont partagés entre un modèle
    membres et son homologue mean (même famille) : une seule requête par slug."""
    by_slug = {}
    out = {}
    for model in models:
        slug = model.get("meta_slug")
        if slug not in by_slug:
            by_slug[slug] = OM.fetch_run_date(SC.META_API_URL_TPL, slug,
                                              SC.HTTP_TIMEOUT)
        meta_rd = by_slug[slug]
        out[model["label"]] = meta_rd if meta_rd is not None else \
            OM.clock_run_date(model["cycles"], SC.PUBLICATION_LAG_HOURS, now_utc)
    return out


# --------------------------------------------------------------------------- #
#  Normalisation JSON → table plate
# --------------------------------------------------------------------------- #
def _member_pattern(var_api, model_api):
    """Regex des clés d'une variable pour un modèle du flux membres.
    Groupe 1 = numéro de membre (None pour le contrôle = 0)."""
    return re.compile(rf"^{re.escape(var_api)}_(?:member(\d+)_)?{re.escape(model_api)}$")


def _members_of(hourly, model_api):
    """Numéros de membres présents pour un modèle (0 = contrôle), via t2m
    (première variable, publiée par tous les modèles aux deux points)."""
    pat = _member_pattern(SC.ENS_VARIABLES[0]["api"], model_api)
    members = set()
    for key in hourly:
        m = pat.match(key)
        if m:
            members.add(int(m.group(1)) if m.group(1) else 0)
    return sorted(members)


def _series(hourly, key, n):
    """Série numérique d'une clé du payload — NaN si absente (trou structurel,
    ex. AIFS sans freezing_level : dégradation silencieuse, jamais une panne)."""
    vals = hourly.get(key)
    return (pd.to_numeric(pd.Series(vals), errors="coerce").to_numpy()
            if vals is not None else np.full(n, np.nan))


def _site_frame(valid_time, site_code, kind, member, values_by_col):
    """Une série (site, kind, member) → DataFrame au schéma ENS_SCHEMA, avec
    filtrage par site (une variable non déclarée pour ce site reste NaN — on ne
    stocke que ce qui sert, cf. snow_config.ENS_VARIABLES) et dérivation de
    l'épaisseur 1000-500 (jamais sur les lignes spread : l'écart-type d'une
    différence ne se déduit pas des écarts-types)."""
    frame = pd.DataFrame({"valid_time": valid_time})
    frame["kind"] = kind
    frame["member"] = member
    frame["site"] = site_code
    n = len(valid_time)
    for var in SC.ENS_VARIABLES:
        col_vals = values_by_col.get(var["col"], np.full(n, np.nan))
        frame[var["col"]] = col_vals if site_code in var["sites"] else np.nan
    if kind == "spread" or "z500" not in frame or "z1000" not in frame:
        frame[SC.EPAISSEUR_COL] = np.nan
    else:
        frame[SC.EPAISSEUR_COL] = frame["z500"] - frame["z1000"]
    return frame.drop(columns=[v["col"] for v in SC.ENS_VARIABLES
                               if v.get("transient")])


def parse_members(payload, run_date_by_label):
    """Payload Ensemble API (liste, un élément par site) → lignes kind="member".
    Grille horaire uniforme conservée : un modèle qui s'arrête tôt laisse des
    NaN en queue (statistiques tolérantes aux NaN en aval)."""
    frames = []
    for site, point in zip(SC.SITES, OM.as_payload_list(payload)):
        hourly = point["hourly"]
        utc_offset = int(point.get("utc_offset_seconds", 0))
        valid_time = pd.to_datetime(hourly["time"]) - pd.Timedelta(seconds=utc_offset)
        n = len(valid_time)
        for model in SC.ENS_MODELS:
            for member in _members_of(hourly, model["api"]):
                suffix = (model["api"] if member == 0
                          else f"member{member:02d}_{model['api']}")
                values = {v["col"]: _series(hourly, f"{v['api']}_{suffix}", n)
                          for v in SC.ENS_VARIABLES}
                frame = _site_frame(valid_time, site["code"], "member", member, values)
                frame["run_date"] = run_date_by_label[model["label"]]
                frame["model"] = model["label"]
                frames.append(frame)
    return frames


def parse_mean(payload, run_date_by_label):
    """Payload Ensemble Mean API → lignes kind="mean" (nom de variable simple)
    et kind="spread" (clés `<var>_spread_<model>`), member=0. Les deux natures
    partagent les mêmes colonnes de variables (valeur = mean ou spread)."""
    frames = []
    for site, point in zip(SC.SITES, OM.as_payload_list(payload)):
        hourly = point["hourly"]
        utc_offset = int(point.get("utc_offset_seconds", 0))
        valid_time = pd.to_datetime(hourly["time"]) - pd.Timedelta(seconds=utc_offset)
        n = len(valid_time)
        for model in SC.MEAN_MODELS:
            mean_vals = {v["col"]: _series(hourly, f"{v['api']}_{model['api']}", n)
                         for v in SC.ENS_VARIABLES}
            spread_vals = {v["col"]: _series(hourly, f"{v['api']}_spread_{model['api']}", n)
                           for v in SC.ENS_VARIABLES if v.get("spread")}
            for kind, values in (("mean", mean_vals), ("spread", spread_vals)):
                frame = _site_frame(valid_time, site["code"], kind, 0, values)
                frame["run_date"] = run_date_by_label[model["label"]]
                frame["model"] = model["label"]
                frames.append(frame)
    return frames


def parse_payloads(members_payload, mean_payload, now_utc=None):
    """JSON des deux appels → DataFrame plat ENS_SCHEMA. Chaque modèle (membres
    ET mean) reçoit SA propre run_date — jamais de cycle global partagé. Les
    séries (model, kind, member, site) entièrement vides sont écartées."""
    run_dates = _run_dates(SC.ENS_MODELS + SC.MEAN_MODELS, now_utc)
    frames = parse_members(members_payload, run_dates) \
        + parse_mean(mean_payload, run_dates)
    if not frames:
        return pd.DataFrame(columns=SC.ENS_SCHEMA)
    df = pd.concat(frames, ignore_index=True)

    # Écarte les séries sans la moindre valeur (site non concerné par les
    # variables d'un kind — ex. lignes spread au sommet si aucune variable
    # spread n'y est déclarée — ou modèle absent du payload).
    non_empty = df.groupby(["model", "kind", "member", "site"])[SC.ENS_VAR_COLS] \
                  .transform(lambda s: s.notna().any())
    df = df[non_empty.any(axis=1)].reset_index(drop=True)
    return df[SC.ENS_SCHEMA]


# --------------------------------------------------------------------------- #
#  Entrée
# --------------------------------------------------------------------------- #
def main():
    now_utc = dt.datetime.now(dt.timezone.utc)
    print("⏳ Requête Open-Meteo Ensemble (membres) + Ensemble Mean — Megève…")
    candidate = parse_payloads(fetch_members_payload(), fetch_mean_payload(), now_utc)
    if candidate.empty:
        print("ℹ️  Aucune donnée exploitable dans les réponses — base laissée telle quelle.")
        return

    rd_by_model = candidate.drop_duplicates("model").set_index("model")["run_date"]
    print("   Cycle retenu par modèle : " +
          ", ".join(f"{label} {rd.hour:02d}Z" for label, rd in rd_by_model.items()))

    existing = ER.load_existing(SC.DB_ENS_PATH, SC.ENS_SCHEMA)
    fresh, stale, partial = ER.filter_fresh_rows(
        candidate, existing,
        var_cols=SC.ENS_VAR_COLS, id_cols=["kind", "member", "site"],
        eps=SC.FRESHNESS_EPS, horizon_h_by_model=SC.HORIZON_BY_LABEL,
        horizon_tol_h=SC.PERSIST_HORIZON_TOLERANCE_H,
        min_horizon_h=SC.MIN_PERSIST_HORIZON_H, max_gap_h=SC.PERSIST_MAX_GAP_H)
    if stale:
        print(f"   ⏸️  Cycle inchangé (run déjà en stock conservé) : {', '.join(stale)}")
    if partial:
        print(f"   ⏳ Portée contiguë trop courte pour être persisté (calcul en "
              f"cours, cycle nativement partiel ou réponse creuse ; run complet "
              f"précédent conservé) : {', '.join(partial)}")
    if fresh.empty:
        print("ℹ️  Aucun modèle renouvelé à ce poll — base laissée telle quelle.")
        return

    for model_label, g in fresh.groupby("model"):
        valid = g.dropna(subset=SC.ENS_VAR_COLS, how="all")
        last = valid["valid_time"].max() if not valid.empty else None
        print(f"   ✅ {model_label} renouvelé — échéances valides jusqu'à {last}")

    combined = ER.persist(
        fresh, SC.DB_ENS_PATH, schema=SC.ENS_SCHEMA, var_cols=SC.ENS_VAR_COLS,
        sort_cols=["run_date", "model", "kind", "member", "site", "valid_time"],
        max_gap_h=SC.PERSIST_MAX_GAP_H, existing=existing)
    n_runs = combined[["run_date", "model"]].drop_duplicates().shape[0]
    print(f"✅ Base neige mise à jour : {len(combined):,} lignes · "
          f"{n_runs} run(s) modèle archivés")
    print(f"   → {SC.DB_ENS_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline neige (ensemble) : {exc}")
