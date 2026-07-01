# -*- coding: utf-8 -*-
"""Contrôle croisé : compare une métrique Open-Meteo à son équivalent scrapé sur
Météociel (legacy), échéance par échéance, pour ECMWF/AIFS/GEFS.

Limité aux runs 0Z/12Z — les seuls publiés en intégralité par Météociel, et les
seuls que le legacy scraper sait produire proprement. N'exécute aucun
fetch/scrape lui-même : consomme les sorties déjà produites par Forecast.py
(parquet) et Forecast_legacy.py (xlsx) pour le run demandé. Orchestré par
run_dual.py.

Comparaison médiane-vs-médiane pour TOUS les modèles (cf.
config.LEGACY_COMPARE_STRATEGY) : la colonne « DET »/« GFS » scrapée sur
Météociel est le run déterministe HAUTE RÉSOLUTION (HRES/GFS-det), un produit
distinct du membre de contrôle (member 0) de l'API ensemble — les confronter
fabrique ~7 °C d'écart artificiel à longue échéance (bords opposés du panache).
La médiane des membres reste comparable, les deux sources poolant le même
ensemble. Le seuil de flag s'élargit avec l'échéance (tol = BASE + PER_DAY·jours,
plafonné) : on cherche un BUG pipeline (offset court-terme), pas la divergence-
modèle légitime de longue échéance.
"""

import glob
import os
import re
import datetime as dt

import pandas as pd

import config as C


# Regex pour extraire le run_date dans la ligne d'info du xlsx Météociel.
# Ex : 'Informations : Run ECMWF-ENS du 01/07/2026 00Z'
_LEGACY_RUN_DATE_RE = re.compile(r"du\s+(\d{2}/\d{2}/\d{4})\s+(\d{1,2})Z", re.IGNORECASE)


def _parse_legacy_run_date(path, sheet):
    """Run_date déclaré par Météociel dans l'en-tête du xlsx (row 1), datetime
    UTC tz-naïf. Retourne None si la ligne est absente ou illisible.

    C'est LA source fiable pour savoir quel run le xlsx représente ; la date dans
    le nom de fichier est la date de scrape, pas le run_date."""
    try:
        cell = pd.read_excel(path, sheet_name=sheet, header=None, nrows=2).iloc[1, 0]
    except Exception:
        return None
    m = _LEGACY_RUN_DATE_RE.search(str(cell))
    if not m:
        return None
    try:
        base = dt.datetime.strptime(m.group(1), "%d/%m/%Y")
        return base.replace(hour=int(m.group(2)))
    except ValueError:
        return None


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
    """DataFrame [valid_time, legacy_value] pour la métrique demandée.

    En stratégie « median », on n'émet une échéance que si au moins
    CROSS_CHECK_MIN_MEMBERS membres y sont valides (sinon la médiane n'est pas
    représentative — cf. config)."""
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
        out = pd.DataFrame({"valid_time": df["valid_time"],
                            "legacy_value": members.median(axis=1),
                            "_n": members.notna().sum(axis=1)})
        out = out[out["_n"] >= C.CROSS_CHECK_MIN_MEMBERS].drop(columns="_n")
    return out.dropna(subset=["valid_time"])


def _openmeteo_metric(db, model_label, run_date, strategy):
    """DataFrame [valid_time, openmeteo_value] pour la métrique demandée.

    En « median », on n'émet une échéance que si au moins CROSS_CHECK_MIN_MEMBERS
    membres y sont valides — symétrique du garde-fou legacy (la queue masquée par
    mask_stale_tail laisse des échéances à 0-2 membres, non représentatives)."""
    sub = db[(db["model"] == model_label) & (db["run_date"] == run_date)]
    if strategy == "det":
        out = sub[sub["member"] == 0][["valid_time", C.PRIMARY_VAR]]
        return out.rename(columns={C.PRIMARY_VAR: "openmeteo_value"})
    # "median"
    agg = sub.groupby("valid_time")[C.PRIMARY_VAR].agg(
        openmeteo_value="median", _n="count")
    agg = agg[agg["_n"] >= C.CROSS_CHECK_MIN_MEMBERS]
    return agg.drop(columns="_n").reset_index()


def _nearest_run_date(db, model_label, reference, window_h=24):
    """run_date effectif le plus récent pour ce modèle dans la base, à ±window_h
    de `reference`. Renvoie None si le modèle est absent ou hors fenêtre.

    Nécessaire car chaque modèle a son propre cycle synoptique (AIFS peut être
    06Z, GEFS 18Z…) alors que le run_label '0Z'/'12Z' est une convention legacy."""
    sub = db[db["model"] == model_label]
    if sub.empty:
        return None
    candidates = [
        r for r in sub["run_date"].unique()
        if abs((pd.Timestamp(r) - pd.Timestamp(reference)).total_seconds()) <= window_h * 3600
    ]
    return max(candidates) if candidates else None


def cross_check(run_label, now_utc=None):
    """Compare, pour le run `run_label` ('0Z' ou '12Z') du jour courant, la
    métrique Open-Meteo à son équivalent legacy pour ECMWF/AIFS/GEFS sur les
    échéances communes (cf. config.LEGACY_COMPARE_STRATEGY). Ajoute le résultat
    à C.CROSS_CHECK_LOG_PATH (append).

    Renvoie le DataFrame du rapport (vide si rien à comparer — fichier legacy ou
    parquet absent, ou aucune échéance commune)."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    cycle_hour = 0 if run_label == "0Z" else 12
    reference_date = dt.datetime(now_utc.year, now_utc.month, now_utc.day, cycle_hour)

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

        # Référence prioritaire : run_date déclaré par Météociel dans le xlsx.
        # Repli sur la date calendaire si l'en-tête est illisible.
        legacy_run_date = _parse_legacy_run_date(legacy_file, sheet)
        reference = legacy_run_date if legacy_run_date is not None else reference_date
        if legacy_run_date is None:
            print(f"   ⚠️  {model_label} : run_date Météociel illisible dans {os.path.basename(legacy_file)}, "
                  "repli sur date calendaire.")

        run_date = _nearest_run_date(db, model_label, reference)
        if run_date is None:
            print(f"   ℹ️  {model_label} : aucun run dans la base à ±24h de {reference:%Y-%m-%d %HZ}, ignoré.")
            continue

        # Audit d'alignement : si le parquet et le xlsx ne pointent pas sur le
        # même cycle, le contrôle croisé serait trompeur.
        if legacy_run_date is not None:
            gap_h = abs((pd.Timestamp(run_date) - pd.Timestamp(legacy_run_date)).total_seconds()) / 3600
            if gap_h > C.META_HEURISTIC_DIVERGENCE_WARN_H:
                print(f"   ⚠️  {model_label} : run xlsx={legacy_run_date:%Y-%m-%d %HZ} "
                      f"≠ run parquet={pd.Timestamp(run_date):%Y-%m-%d %HZ} "
                      f"(écart {gap_h:.0f} h) — résultats potentiellement non comparables.")
        om = _openmeteo_metric(db, model_label, run_date, strategy)
        if om.empty:
            continue
        merged = legacy.merge(om, on="valid_time", how="inner")
        if merged.empty:
            continue
        merged["diff"] = (merged["legacy_value"] - merged["openmeteo_value"]).round(2)
        # Tolérance fonction de l'échéance : un bug pipeline ressort à courte
        # échéance (offset constant) ; à longue échéance, deux ensembles distincts
        # divergent légitimement de 1-2 °C (cf. config). tol = BASE + PER_DAY·jours,
        # plafonnée à CAP.
        lead_h = (merged["valid_time"] - run_date).dt.total_seconds() / 3600
        merged["lead_h"] = lead_h.round().astype(int)
        tol = (C.CROSS_CHECK_TOLERANCE_BASE_C
               + C.CROSS_CHECK_TOLERANCE_PER_DAY_C * (lead_h / 24)).clip(
            upper=C.CROSS_CHECK_TOLERANCE_CAP_C)
        merged["tol"] = tol.round(2)
        merged["flag"] = merged["diff"].abs() > tol
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
                     "lead_h", "legacy_value", "openmeteo_value", "diff", "tol", "flag"]]

    os.makedirs(C.DATA_DIR, exist_ok=True)
    header = not os.path.exists(C.CROSS_CHECK_LOG_PATH)
    report.to_csv(C.CROSS_CHECK_LOG_PATH, mode="a", header=header, index=False)

    n_flag = int(report["flag"].sum())
    print(f"🔍 Contrôle croisé {run_label} : {len(report)} échéances comparées "
          f"({', '.join(sorted(report['model'].unique()))}), "
          f"{n_flag} écart(s) hors tolérance "
          f"({C.CROSS_CHECK_TOLERANCE_BASE_C}→{C.CROSS_CHECK_TOLERANCE_CAP_C}°C selon échéance)")
    if n_flag:
        worst = report.reindex(report["diff"].abs().sort_values(ascending=False).index)
        print(worst.head(5).to_string(index=False))
    print(f"   → log : {C.CROSS_CHECK_LOG_PATH}")
    return report
