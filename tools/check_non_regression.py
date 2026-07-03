# -*- coding: utf-8 -*-
"""Preuve de non-régression du dashboard — calculs uniquement, AUCUNE écriture
dans les données (parquet et xlsx lus en lecture seule).

Principe : toutes les fonctions de calcul du dashboard (sélections de runs,
statistiques d'ensemble, risque, convergence, présence, candidats d'import
legacy) sont exécutées sur la base courante et leurs sorties condensées en
empreintes (sha256 du CSV + forme + colonnes), stockées dans
tools/golden/golden.json.

Usage (depuis la racine du projet) :
    python tools/check_non_regression.py capture   # fige la référence
    python tools/check_non_regression.py check     # compare à la référence

La résolution des fonctions est indirecte : le script cherche d'abord le
package modulaire (app/…), sinon retombe sur le monolithe meteo_app — la même
référence sert donc à comparer l'avant et l'après d'un refactor, tant que les
NOMS de fonctions sont conservés.

Limite connue : les sorties dépendent du contenu de data/database_paris.parquet
et des xlsx de legacy/. Capture et check doivent donc encadrer un refactor
SANS exécution du pipeline entre les deux (sinon régénérer la référence).
"""

import hashlib
import json
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
GOLDEN_PATH = os.path.join(ROOT, "tools", "golden", "golden.json")

import config as C  # noqa: E402


def resolve_namespace():
    """Dictionnaire {nom → fonction} depuis le package app/ si présent, sinon
    depuis meteo_app (monolithe). Les noms de fonctions sont le contrat."""
    try:
        from app.data import db, runsets, presence, legacy_import  # noqa: F401
        from app.stats import ensemble, tables, climato  # noqa: F401
        from app.domains.heatwave import logic as heatwave_logic  # noqa: F401
        mods = (db, runsets, presence, legacy_import, ensemble, tables, climato,
                heatwave_logic)
        ns = {}
        for mod in mods:
            ns.update(vars(mod))
        ns["__source__"] = "app package"
        return ns
    except ImportError:
        import meteo_app
        ns = dict(vars(meteo_app))
        ns["__source__"] = "meteo_app monolithe"
        return ns


def df_fingerprint(df):
    """Empreinte stable d'un DataFrame : forme, colonnes, sha256 du CSV.
    Les flottants sont sérialisés en pleine précision (%.12g) : deux calculs
    identiques donnent le même hash, toute dérive numérique le change."""
    if df is None:
        return None
    if isinstance(df, pd.Series):
        df = df.to_frame()
    csv = df.to_csv(index=True, float_format="%.12g")
    return {
        "shape": list(df.shape),
        "columns": [str(c) for c in df.columns],
        "sha256": hashlib.sha256(csv.encode("utf-8")).hexdigest(),
    }


def _ts(x):
    return None if x is None else str(pd.Timestamp(x))


def _sources(d):
    """{label → run_date} (ou → liste de run_dates) sérialisable."""
    out = {}
    for k, v in d.items():
        if isinstance(v, (list, tuple)):
            out[str(k)] = [_ts(x) for x in v]
        else:
            out[str(k)] = _ts(v)
    return out


def collect():
    ns = resolve_namespace()
    print(f"Fonctions résolues depuis : {ns['__source__']}")
    out = {"_source": ns["__source__"]}

    sig = ns["db_signature"]()
    df = ns["load_db"](sig)
    out["load_db"] = df_fingerprint(df)

    runs = ns["list_runs"](sig)
    out["list_runs"] = {"labels": runs["label"].tolist()}

    # --- Sélections de runs -------------------------------------------------
    sub_c, sources_c, partial_c = ns["latest_complete_run_sub"](sig)
    out["latest_complete_run_sub"] = {
        "df": df_fingerprint(sub_c), "sources": _sources(sources_c),
        "partial": sorted(partial_c)}

    if len(runs) > 3:  # rejouer « Vu depuis » sur un cycle antérieur
        as_of = runs.iloc[3]["run_date"]
        s, so, pa = ns["latest_complete_run_sub"](sig, as_of)
        out["latest_complete_run_sub_as_of3"] = {
            "df": df_fingerprint(s), "sources": _sources(so), "partial": sorted(pa)}

    sub_l, sources_l = ns["latest_run_sub"](sig)
    out["latest_run_sub"] = {"df": df_fingerprint(sub_l), "sources": _sources(sources_l)}

    prev = ns["previous_runs_sub"](sig, sub_c)
    out["previous_runs_sub"] = df_fingerprint(prev)

    # --- Statistiques d'ensemble (sur le pool des derniers runs complets) ---
    syn = ns["super_ensemble"](sub_c)
    out["super_ensemble"] = df_fingerprint(syn)
    out["daily_aggregate"] = df_fingerprint(ns["daily_aggregate"](syn))
    out["daily_risk"] = df_fingerprint(ns["daily_risk"](sub_c, C.SEUIL_CANICULE_850))
    out["model_medians"] = df_fingerprint(ns["model_medians"](sub_c))
    div = ns["divergence"](sub_c)
    out["divergence"] = df_fingerprint(div)
    out["multimodel_cutoff"] = _ts(ns["multimodel_cutoff"](sub_c))

    for label in C.MODEL_LABELS:
        loaded = ns["model_data"](sub_c, label)
        if loaded is None:
            out[f"model_data.{label}"] = None
            continue
        stats, members, det = loaded
        out[f"model_data.{label}"] = {
            "stats": df_fingerprint(stats), "members": df_fingerprint(members),
            "det": df_fingerprint(det)}
        out[f"model_table.{label}"] = df_fingerprint(ns["model_table"](sub_c, label, prev))

    out["enriched_super_table"] = df_fingerprint(ns["enriched_super_table"](sub_c, prev))

    # --- Convergence / backfill ---------------------------------------------
    conv = ns["_convergence_runs"](runs)
    out["_convergence_runs"] = {"labels": conv["label"].tolist()}
    for pos in range(min(3, len(runs))):
        pooled, srcs = ns["completed_pooled_sub"](runs, pos, sig)
        out[f"completed_pooled_sub.{pos}"] = {
            "df": df_fingerprint(pooled), "sources": _sources(srcs)}
        daily, _ = ns["completed_super_ensemble_daily"](runs, pos, sig)
        out[f"completed_super_ensemble_daily.{pos}"] = df_fingerprint(daily)

    trend = ns["trend_daily_medians"](sig)
    out["trend_daily_medians"] = df_fingerprint(trend)
    out["tendance_recente"] = df_fingerprint(ns["tendance_recente"](trend))

    # --- Climatologie (valeurs ponctuelles, dates fixes) ---------------------
    dates = pd.to_datetime(["2026-01-15", "2026-04-15", "2026-07-17", "2026-10-15"])
    out["clim_normal"] = [round(float(v), 6) for v in ns["clim_normal"](pd.Series(dates))]

    # --- Présence / diagnostic / import legacy (lecture seule) ---------------
    om = ns["openmeteo_presence"](sig)
    out["openmeteo_presence"] = df_fingerprint(om)
    out["_missing_by_run"] = {
        _ts(k): sorted(v) for k, v in sorted(ns["_missing_by_run"](om).items())}
    leg_sig = ns["legacy_signature"]()
    out["legacy_presence"] = df_fingerprint(ns["legacy_presence"](leg_sig))
    out["legacy_import_candidates"] = df_fingerprint(
        ns["legacy_import_candidates"](sig, leg_sig))

    # --- Étiquettes temporelles ----------------------------------------------
    out["run_labels"] = [ns["run_label_text"](rd) for rd in runs["run_date"].head(8)]
    out["main_labels_expected_at"] = [
        ns["main_labels_expected_at"](rd) for rd in runs["run_date"].head(8)]

    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    data = collect()
    if mode == "capture":
        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        print(f"Référence capturée : {GOLDEN_PATH} ({len(data)} entrées)")
        return 0

    with open(GOLDEN_PATH, encoding="utf-8") as f:
        golden = json.load(f)
    diffs = []
    for key in sorted(set(golden) | set(data)):
        if key.startswith("_"):
            continue
        if golden.get(key) != data.get(key):
            diffs.append(key)
    if diffs:
        print(f"[FAIL] {len(diffs)} divergence(s) vs la référence :")
        for k in diffs:
            print(f"  - {k}")
            print(f"      golden : {json.dumps(golden.get(k), ensure_ascii=False)[:200]}")
            print(f"      actuel : {json.dumps(data.get(k), ensure_ascii=False)[:200]}")
        return 1
    print(f"[OK] Non-régression vérifiée : {sum(1 for k in data if not k.startswith('_'))} "
          "sorties identiques à la référence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
