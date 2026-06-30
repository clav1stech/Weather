# -*- coding: utf-8 -*-
"""Pipeline de données météo — API d'ensembles Open-Meteo → base plate unifiée.

Un seul appel HTTP couvre tous les modèles à la fois, mais chaque modèle cycle à
son propre rythme (cf. config.MODELS `cycles`) : l'API ne publie pas l'heure
d'initialisation réelle par modèle, donc à chaque exécution :
  1. pour CHAQUE modèle, on estime SON cycle le plus récent (0/6/12/18Z) d'après
     l'heure UTC et sa propre liste de cycles — ex. à un poll « 6Z », GEM (qui ne
     cycle qu'à 0Z/12Z) est étiqueté 0Z, pas 6Z ;
  2. interroge l'API Open-Meteo (tous les modèles / variables de config.py) ;
  3. normalise le JSON en table plate « tidy »
     [run_date, model, member, valid_time, <variables...>], run_date variant donc
     d'un modèle à l'autre dans un même fetch ;
  4. ne conserve, par modèle, que les données réellement renouvelées par rapport
     au dernier run stocké (cf. _is_fresh) — garde-fou si un modèle n'a pas encore
     publié son cycle attendu au moment du poll ;
  5. fusionne dans data/database_paris.parquet sans jamais perdre l'historique
     (remplace, par (run_date, modèle), le run identique éventuel, append le run
     frais, écriture atomique).

Aucun scraping HTML : tout passe par l'API. Voir config.py pour les réglages.
"""

import os
import re
import sys
import datetime as dt

import requests
import numpy as np
import pandas as pd

import config as C


# --------------------------------------------------------------------------- #
#  Détection du run — par modèle (chaque modèle a son propre rythme de cycle)
# --------------------------------------------------------------------------- #
def detect_model_run_date(model, now_utc=None):
    """Cycle le plus récent de CE modèle (datetime UTC tz-naïf), d'après sa propre
    liste `cycles` (cf. config.MODELS) — pas un cycle global partagé.

    Open-Meteo recomble les heures passées de la journée depuis le run précédent :
    le premier pas non-NaN tombe toujours à 00:00 local et n'identifie donc pas le
    cycle. On le déduit de l'heure UTC (moins le délai de publication, le run étant
    exploitable ~PUBLICATION_LAG_HOURS après son initialisation), en ne retenant que
    les heures de cycle réellement supportées par ce modèle.
    """
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    anchored = now - dt.timedelta(hours=C.PUBLICATION_LAG_HOURS)
    cycles = sorted(model["cycles"])
    eligible = [h for h in cycles if h <= anchored.hour]
    day = anchored.date()
    if eligible:
        cycle_hour = eligible[-1]
    else:
        cycle_hour = cycles[-1]
        day -= dt.timedelta(days=1)
    return dt.datetime(day.year, day.month, day.day, cycle_hour)


def run_label(run_date):
    return f"{run_date.hour:02d}Z"


# --------------------------------------------------------------------------- #
#  Requête API
# --------------------------------------------------------------------------- #
def fetch_payload():
    """Appel HTTP unique couvrant tous les modèles et variables."""
    params = {
        "latitude": C.LATITUDE,
        "longitude": C.LONGITUDE,
        "hourly": ",".join(v["api"] for v in C.VARIABLES),
        "models": ",".join(m["api"] for m in C.MODELS),
        "timezone": C.TIMEZONE,
        "forecast_days": C.FORECAST_DAYS,
    }
    resp = requests.get(C.API_URL, params=params, timeout=C.HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------- #
#  Normalisation JSON → table plate
# --------------------------------------------------------------------------- #
def _member_pattern(var_api, model_api):
    """Regex matchant les clés d'une variable pour un modèle.
    Groupe 1 = numéro de membre (None pour le membre de contrôle = 0)."""
    return re.compile(rf"^{re.escape(var_api)}_(?:member(\d+)_)?{re.escape(model_api)}$")


def _members_of(hourly, model_api):
    """Numéros de membres présents pour un modèle (0 = contrôle), via la variable
    primaire. On suppose le même jeu de membres pour toutes les variables."""
    pat = _member_pattern(C.VARIABLES[0]["api"], model_api)
    members = set()
    for key in hourly:
        m = pat.match(key)
        if m:
            members.add(int(m.group(1)) if m.group(1) else 0)
    return sorted(members)


def _series_key(var_api, model_api, member):
    return f"{var_api}_{model_api}" if member == 0 else f"{var_api}_member{member:02d}_{model_api}"


def parse_payload(payload, now_utc=None):
    """JSON Open-Meteo → DataFrame plat. Grille horaire uniforme conservée jusqu'à
    16 j ; un modèle qui s'arrête tôt laisse simplement des NaN en queue.

    Chaque modèle reçoit SA propre `run_date` (cf. detect_model_run_date) — un même
    fetch peut donc mélanger des modèles étiquetés à des cycles différents (ex. GEM
    en 0Z pendant que ECMWF/AIFS/GEFS sont en 6Z).

    Aucune troncature par horizon nominal ici : l'horizon réel d'un cycle (combien
    d'échéances un modèle a effectivement recalculées) varie d'un jour à l'autre et
    n'est pas un fait fixe par modèle (constaté empiriquement sur AIFS) — toute
    table d'horizons codée en dur serait donc non fiable. La distinction entre
    « échéances réellement renouvelées » et « queue collée de l'ancien cycle » se
    fait plus loin, empiriquement, par comparaison échéance-par-échéance au dernier
    run stocké (cf. mask_stale_tail).
    """
    hourly = payload["hourly"]
    utc_offset = int(payload.get("utc_offset_seconds", 0))
    # Heure locale renvoyée par l'API → UTC tz-naïf (comparable aux run_date).
    valid_time = pd.to_datetime(hourly["time"]) - pd.Timedelta(seconds=utc_offset)

    frames = []
    for model in C.MODELS:
        model_run_date = detect_model_run_date(model, now_utc)

        for member in _members_of(hourly, model["api"]):
            frame = pd.DataFrame({"valid_time": valid_time})
            frame["run_date"] = model_run_date
            frame["model"] = model["label"]
            frame["member"] = member
            for var in C.VARIABLES:
                key = _series_key(var["api"], model["api"], member)
                vals = hourly.get(key)
                frame[var["col"]] = (
                    pd.to_numeric(pd.Series(vals), errors="coerce").to_numpy()
                    if vals is not None else np.nan
                )
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=C.SCHEMA)

    df = pd.concat(frames, ignore_index=True)

    if C.DROP_EMPTY_SERIES:
        # Écarte les séries (modèle, membre) entièrement vides (modèle indisponible
        # ce run) ; conserve les séries partielles (valides + queue NaN).
        non_empty = df.groupby(["model", "member"])[C.VAR_COLS].transform(
            lambda s: s.notna().any())
        df = df[non_empty.any(axis=1)].reset_index(drop=True)

    return df[C.SCHEMA]


# --------------------------------------------------------------------------- #
#  Fraîcheur — détection empirique, échéance par échéance
# --------------------------------------------------------------------------- #
def load_existing():
    if os.path.exists(C.DB_PATH):
        return pd.read_parquet(C.DB_PATH)
    return pd.DataFrame(columns=C.SCHEMA)


def mask_stale_tail(model_label, candidate, existing):
    """NaN-ifie, dans `candidate` (lignes d'UN modèle), les échéances dont les
    valeurs sont quasi identiques à celles du dernier run stocké pour ce modèle —
    signe qu'Open-Meteo ressert encore la queue de l'ancien cycle, pas une donnée
    réellement recalculée par le cycle courant.

    Comparaison échéance par échéance (et non un seul verdict pour tout le
    modèle) : deux membres d'ensemble indépendants ne tombent jamais pile sur la
    même valeur par hasard (perturbations aléatoires), donc un écart quasi nul à
    une échéance donnée trahit fiablement une copie, quel que soit l'horizon réel
    du cycle — qui varie d'un jour à l'autre et n'est plus supposé à l'avance.

    Renvoie (candidate masqué, a_du_neuf). a_du_neuf=False si AUCUNE échéance
    n'a changé : le modèle entier est alors considéré comme pas encore renouvelé
    (équivalent à l'ancien comportement « skip » par modèle).
    """
    prior = existing[existing["model"] == model_label]
    if prior.empty:
        return candidate, True
    latest_run = prior["run_date"].max()
    prior_latest = prior[prior["run_date"] == latest_run]

    merged = candidate.merge(
        prior_latest[["member", "valid_time", *C.VAR_COLS]],
        on=["member", "valid_time"], suffixes=("_new", "_old"), how="left")

    abs_diff = pd.Series(0.0, index=merged.index)
    n_comparable = pd.Series(0, index=merged.index)
    has_new_value = pd.Series(False, index=merged.index)
    for col in C.VAR_COLS:
        new_v, old_v = merged[f"{col}_new"], merged[f"{col}_old"]
        both_valid = new_v.notna() & old_v.notna()
        abs_diff += (new_v - old_v).abs().where(both_valid, 0.0)
        n_comparable += both_valid.astype(int)
        has_new_value |= new_v.notna()

    per_step = pd.DataFrame({"valid_time": merged["valid_time"], "abs_diff": abs_diff,
                             "n": n_comparable, "has_new": has_new_value})

    def _classify(g):
        # n=0 : aucune valeur comparable des deux côtés.
        #   • le candidat a quand même une valeur (`has_new`) → couverture
        #     inédite (ex. au-delà de ce que le run précédent couvrait) → fraîche.
        #   • sinon, NaN nativement des deux côtés → ni fraîche ni périmée,
        #     n'influence pas le verdict (déjà NaN, rien à masquer).
        if g["n"].sum() == 0:
            return "fresh" if g["has_new"].any() else "empty"
        return "fresh" if (g["abs_diff"].sum() / g["n"].sum()) > C.FRESHNESS_EPS else "stale"

    step_class = per_step.groupby("valid_time").apply(_classify, include_groups=False)
    stale_steps = step_class[step_class == "stale"].index

    candidate = candidate.copy()
    candidate.loc[candidate["valid_time"].isin(stale_steps), C.VAR_COLS] = np.nan
    a_du_neuf = bool((step_class == "fresh").any())
    return candidate, a_du_neuf


def filter_fresh_rows(fresh, existing):
    """Applique mask_stale_tail à chaque modèle ; écarte entièrement les modèles
    sans la moindre échéance renouvelée (cf. mask_stale_tail)."""
    kept, stale_labels = [], []
    for model_label, candidate in fresh.groupby("model"):
        masked, a_du_neuf = mask_stale_tail(model_label, candidate, existing)
        if a_du_neuf:
            kept.append(masked)
        else:
            stale_labels.append(model_label)
    out = pd.concat(kept, ignore_index=True) if kept else fresh.iloc[0:0]
    return out, stale_labels


# --------------------------------------------------------------------------- #
#  Persistance
# --------------------------------------------------------------------------- #
def _validate(df):
    if df is None or df.empty:
        raise ValueError("Run frais vide — rien à écrire.")
    missing = [c for c in C.SCHEMA if c not in df.columns]
    if missing:
        raise ValueError(f"Colonnes manquantes dans le run frais : {missing}")
    if df[C.VAR_COLS].notna().any(axis=None) is False:
        raise ValueError("Aucune valeur valide dans le run frais.")


def persist(fresh, existing=None):
    """Fusion atomique : retire, pour chaque (run_date, modèle) présent dans
    `fresh`, les lignes déjà stockées sous ce même couple, puis append. Comme
    `fresh` peut mélanger des modèles à des cycles différents (ex. GEM en 0Z,
    ECMWF en 6Z), le dédoublonnage se fait par couple — jamais par run_date seul.
    Les modèles absents de `fresh` (cycle pas encore renouvelé, cf.
    filter_fresh_models) gardent leur run antérieur intact — jamais de perte
    d'historique.

    L'écrasement du fichier persistant n'a lieu qu'une fois le DataFrame complet
    validé et écrit dans un fichier temporaire (os.replace = remplacement atomique).
    """
    _validate(fresh)
    os.makedirs(C.DATA_DIR, exist_ok=True)

    if existing is None:
        existing = load_existing()

    if existing.empty:
        combined = fresh.copy()
    else:
        fresh_keys = fresh[["run_date", "model"]].drop_duplicates()
        merged = existing.merge(fresh_keys, on=["run_date", "model"],
                                how="left", indicator=True)
        dup_mask = (merged["_merge"] == "both").to_numpy()
        combined = pd.concat([existing[~dup_mask], fresh], ignore_index=True)

    combined = combined.sort_values(["run_date", "model", "member", "valid_time"]) \
                       .reset_index(drop=True)

    tmp = C.DB_PATH + ".tmp"
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, C.DB_PATH)  # remplacement atomique — jamais d'écriture partielle
    return combined


# --------------------------------------------------------------------------- #
#  Entrée
# --------------------------------------------------------------------------- #
def main():
    now_utc = dt.datetime.now(dt.timezone.utc)
    print("⏳ Requête Open-Meteo…")
    payload = fetch_payload()

    candidate = parse_payload(payload, now_utc)
    cycles = {m["label"]: detect_model_run_date(m, now_utc) for m in C.MODELS}
    print("   Cycle détecté par modèle : " +
          ", ".join(f"{label} {run_label(rd)}" for label, rd in cycles.items()))

    existing = load_existing()
    fresh, stale = filter_fresh_rows(candidate, existing)
    if stale:
        print(f"   ⏸️  Cycle inchangé (run déjà en stock conservé) : {', '.join(stale)}")
    if fresh.empty:
        print("ℹ️  Aucun modèle renouvelé à ce poll — base laissée telle quelle.")
        return
    for model_label, g in fresh.groupby("model"):
        valid = g.dropna(subset=C.VAR_COLS)
        last = valid["valid_time"].max() if not valid.empty else None
        print(f"   ✅ {model_label} renouvelé — échéances valides jusqu'à {last}")
    print(f"   Lignes du run frais  : {len(fresh):,}")

    combined = persist(fresh, existing)
    n_runs = combined[["run_date", "model"]].drop_duplicates().shape[0]
    print(f"✅ Base mise à jour : {len(combined):,} lignes · {n_runs} run(s) modèle archivés")
    print(f"   → {C.DB_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline : {exc}")
