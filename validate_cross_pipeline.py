# -*- coding: utf-8 -*-
"""Contrôle croisé : compare une métrique Open-Meteo à son équivalent scrapé sur
Météociel (legacy), échéance par échéance, pour ECMWF/AIFS/GEFS.

Limité aux runs 0Z/12Z — les seuls publiés en intégralité par Météociel, et les
seuls que le legacy scraper sait produire proprement. N'exécute aucun
fetch/scrape lui-même : consomme les sorties déjà produites par Forecast.py
(parquet) et Forecast_legacy.py (xlsx) pour le run demandé. Orchestré par
run_dual.py.

Stratégie de comparaison par modèle (cf. config.LEGACY_COMPARE_STRATEGY) :
  • "det"    : colonne DET legacy vs membre de contrôle (member 0) Open-Meteo —
    valide pour ECMWF/AIFS, qui partagent le même run de contrôle entre les
    deux sources (même trajectoire physique).
  • "median" : médiane des membres d'ensemble des deux côtés — pour GEFS, dont
    la colonne « GFS » scrapée est en réalité le run déterministe haute
    résolution séparé, pas le membre de contrôle de l'ensemble GEFS (constaté
    empiriquement : écarts de 5-15°C en comparaison DET-à-DET). La médiane,
    elle, reste comparable puisque les deux sources poolent le même ensemble
    GEFS (31 membres, 0 à 30).
"""

import glob
import os
import re
import datetime as dt

import pandas as pd

import config as C


def _latest_legacy_file(run_label):
    """Fichier legacy le plus récent pour ce run (DDMMYYYY le plus grand)."""
    pattern = os.path.join(C.LEGACY_FORECASTS_DIR, f"Forecast-*-{run_label}.xlsx")
    files = glob.glob(pattern)
    if not files:
        return None

    def _file_dt(f):
        m = re.search(r"Forecast-(\d{8})-", os.path.basename(f))
        return dt.datetime.strptime(m.group(1), "%d%m%Y") if m else dt.datetime.min

    return max(files, key=_file_dt)


def _parse_legacy_valid_time(date_str):
    """'2026-06-30 00Z' → Timestamp UTC naïf (même convention que valid_time)."""
    parts = str(date_str).split()
    if len(parts) != 2:
        return pd.NaT
    base = pd.to_datetime(parts[0], errors="coerce")
    if pd.isna(base):
        return pd.NaT
    m = re.match(r"(\d{1,2})Z$", parts[1])
    hour = int(m.group(1)) if m else 0
    return base + pd.Timedelta(hours=hour)


def _read_legacy_sheet(path, sheet):
    """Lit une feuille modèle legacy. Renvoie (df, det_col, member_cols) où `df`
    a une colonne `valid_time` ajoutée ; ([], None, []) si inexploitable."""
    try:
        df = pd.read_excel(path, sheet_name=sheet, skiprows=3)
    except (ValueError, FileNotFoundError):
        return None, None, []
    df = df.dropna(how="all")
    if "Date" not in df.columns:
        return None, None, []
    df = df.copy()
    df["valid_time"] = df["Date"].apply(_parse_legacy_valid_time)
    df = df.dropna(subset=["valid_time"])

    det_col = next((c for c in df.columns if str(c).strip().upper() in C.LEGACY_DET_NAMES), None)
    member_cols = [c for c in df.columns if c not in ("Date", "Ech.", "valid_time", det_col)]
    return df, det_col, member_cols


def _legacy_metric(path, sheet, strategy):
    """DataFrame [valid_time, legacy_value] pour la métrique demandée."""
    df, det_col, member_cols = _read_legacy_sheet(path, sheet)
    empty = pd.DataFrame(columns=["valid_time", "legacy_value"])
    if df is None:
        return empty
    if strategy == "det":
        if det_col is None:
            return empty
        out = pd.DataFrame({"valid_time": df["valid_time"],
                            "legacy_value": pd.to_numeric(df[det_col], errors="coerce")})
    else:  # "median"
        if not member_cols:
            return empty
        members = df[member_cols].apply(pd.to_numeric, errors="coerce")
        out = pd.DataFrame({"valid_time": df["valid_time"], "legacy_value": members.median(axis=1)})
    return out.dropna(subset=["valid_time"])


def _openmeteo_metric(db, model_label, run_date, strategy):
    """DataFrame [valid_time, openmeteo_value] pour la métrique demandée."""
    sub = db[(db["model"] == model_label) & (db["run_date"] == run_date)]
    if strategy == "det":
        out = sub[sub["member"] == 0][["valid_time", C.PRIMARY_VAR]]
    else:  # "median"
        out = sub.groupby("valid_time", as_index=False)[C.PRIMARY_VAR].median()
    return out.rename(columns={C.PRIMARY_VAR: "openmeteo_value"})


def cross_check(run_label, now_utc=None):
    """Compare, pour le run `run_label` ('0Z' ou '12Z') du jour courant, la
    métrique Open-Meteo à son équivalent legacy pour ECMWF/AIFS/GEFS sur les
    échéances communes (cf. config.LEGACY_COMPARE_STRATEGY). Ajoute le résultat
    à C.CROSS_CHECK_LOG_PATH (append).

    Renvoie le DataFrame du rapport (vide si rien à comparer — fichier legacy ou
    parquet absent, ou aucune échéance commune)."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    cycle_hour = 0 if run_label == "0Z" else 12
    run_date = dt.datetime(now_utc.year, now_utc.month, now_utc.day, cycle_hour)

    legacy_file = _latest_legacy_file(run_label)
    if legacy_file is None:
        print(f"⚠️  Contrôle croisé {run_label} : aucun fichier legacy trouvé, ignoré.")
        return pd.DataFrame()
    if not os.path.exists(C.DB_PATH):
        print(f"⚠️  Contrôle croisé {run_label} : base Open-Meteo absente, ignoré.")
        return pd.DataFrame()

    db = pd.read_parquet(C.DB_PATH)

    rows = []
    for model_label, sheet in C.LEGACY_MODELS.items():
        strategy = C.LEGACY_COMPARE_STRATEGY.get(model_label, "det")
        legacy = _legacy_metric(legacy_file, sheet, strategy)
        if legacy.empty:
            continue
        om = _openmeteo_metric(db, model_label, run_date, strategy)
        if om.empty:
            continue
        merged = legacy.merge(om, on="valid_time", how="inner")
        if merged.empty:
            continue
        merged["diff"] = (merged["legacy_value"] - merged["openmeteo_value"]).round(2)
        merged["flag"] = merged["diff"].abs() > C.CROSS_CHECK_TOLERANCE_C
        merged.insert(0, "metric", strategy)
        merged.insert(0, "model", model_label)
        merged.insert(0, "run_date", run_date)
        merged.insert(0, "checked_at", now_utc.replace(microsecond=0, tzinfo=None))
        rows.append(merged)

    if not rows:
        print(f"⚠️  Contrôle croisé {run_label} : aucune échéance commune trouvée.")
        return pd.DataFrame()

    report = pd.concat(rows, ignore_index=True)
    report = report[["checked_at", "run_date", "model", "metric", "valid_time",
                     "legacy_value", "openmeteo_value", "diff", "flag"]]

    os.makedirs(C.DATA_DIR, exist_ok=True)
    header = not os.path.exists(C.CROSS_CHECK_LOG_PATH)
    report.to_csv(C.CROSS_CHECK_LOG_PATH, mode="a", header=header, index=False)

    n_flag = int(report["flag"].sum())
    print(f"🔍 Contrôle croisé {run_label} : {len(report)} échéances comparées "
          f"({', '.join(sorted(report['model'].unique()))}), "
          f"{n_flag} écart(s) > {C.CROSS_CHECK_TOLERANCE_C}°C")
    if n_flag:
        worst = report.reindex(report["diff"].abs().sort_values(ascending=False).index)
        print(worst.head(5).to_string(index=False))
    print(f"   → log : {C.CROSS_CHECK_LOG_PATH}")
    return report
