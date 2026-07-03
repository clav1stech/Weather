# -*- coding: utf-8 -*-
"""Pipeline de données météo — API d'ensembles Open-Meteo → base plate unifiée.

Un seul appel HTTP couvre tous les modèles à la fois, mais chaque modèle cycle à
son propre rythme (cf. config.MODELS `cycles`). À chaque exécution :
  1. pour CHAQUE modèle, on interroge en parallèle la Metadata API officielle
     d'Open-Meteo (config.META_API_URL_TPL) — ces requêtes ne sont pas comptées
     dans le quota — afin d'obtenir le `last_run_initialisation_time` exact.
     En cas d'indisponibilité (réseau, endpoint inconnu, JSON invalide), on replie
     sur l'heuristique infer_run_date() (cf. §Détection du run) ;
  2. interroge l'API ensembles (tous les modèles / variables de config.py) ;
  3. normalise le JSON en table plate « tidy »
     [run_date, model, member, valid_time, <variables...>], run_date variant donc
     d'un modèle à l'autre dans un même fetch ;
  4. ne conserve, par modèle, que les données réellement renouvelées par rapport
     au dernier run stocké (cf. mask_stale_tail) — garde-fou si un modèle n'a pas
     encore publié son cycle attendu au moment du poll ;
  5. fusionne dans data/database_paris.parquet sans jamais perdre l'historique
     (remplace, par (run_date, modèle), le run identique éventuel, append le run
     frais, écriture atomique).

Aucun scraping HTML : tout passe par l'API. Voir config.py pour les réglages.
"""

import os
import re
import sys
import datetime as dt
import concurrent.futures

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


def _snap_to_cycle(when, cycles):
    """Datetime (tz-naïf) du cycle (heure ∈ `cycles`) le plus proche de `when`,
    en considérant veille / jour / lendemain pour gérer les bords de journée."""
    best = None
    for day_offset in (-1, 0, 1):
        day = (when + dt.timedelta(days=day_offset)).date()
        for h in cycles:
            cand = dt.datetime(day.year, day.month, day.day, h)
            if best is None or abs((cand - when)) < abs((best - when)):
                best = cand
    return best


def infer_run_date(model, last_valid, now_utc=None):
    """run_date de CE modèle, piloté par la donnée, avec repli horloge sûr.

    L'identité du cycle vit dans la DERNIÈRE échéance publiée (init + horizon),
    pas dans la première (l'API reboucle les heures passées depuis 00:00 local).
    On rétro-calcule init = last_valid − horizon_h et on le cale sur la grille de
    cycles du modèle.

    Garde-fous (runs partiels/tronqués, off-cycles courts — dont on ne connaît pas
    la liste à l'avance) : l'inférence n'est RETENUE que si l'init calé tombe net
    sur un cycle (résidu ≤ RUN_SNAP_TOLERANCE_H) ET reste à portée du cycle
    horloge (≤ RUN_INFER_MAX_SHIFT_H) — elle ne peut donc que CORRIGER vers un
    cycle voisin (ex. 06Z servi sous étiquette 12Z → ramené à 06Z), jamais
    téléporter. Sinon (horizon inconnu, run tronqué, off-cycle plus court que son
    nominal) → repli sur detect_model_run_date (comportement horloge éprouvé)."""
    wall = detect_model_run_date(model, now_utc)
    horizon = model.get("horizon_h")
    if horizon is None or last_valid is None or pd.isna(last_valid):
        return wall
    inferred = pd.Timestamp(last_valid) - pd.Timedelta(hours=horizon)
    snapped = _snap_to_cycle(inferred.to_pydatetime(), model["cycles"])
    residual_h = abs((inferred - snapped).total_seconds()) / 3600
    shift_h = abs((snapped - wall).total_seconds()) / 3600
    if residual_h <= C.RUN_SNAP_TOLERANCE_H and shift_h <= C.RUN_INFER_MAX_SHIFT_H:
        return snapped
    return wall


# --------------------------------------------------------------------------- #
#  Metadata API — run_date officiel par modèle
# --------------------------------------------------------------------------- #
def fetch_model_metadata(meta_slug, url_tpl=None):
    """Interroge la Metadata API Open-Meteo pour un modèle donné.

    Retourne un dict {run_date, availability_time, modification_time,
    update_interval_h, temporal_resolution_h} avec des datetime UTC tz-naïfs,
    ou None si le slug est absent, l'endpoint inaccessible, ou le JSON invalide.

    Ces requêtes ne sont pas comptées dans le quota Open-Meteo (cf. doc officielle).
    """
    if not meta_slug:
        return None
    url = (url_tpl or C.META_API_URL_TPL).format(slug=meta_slug)
    try:
        resp = requests.get(url, timeout=C.HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError):
        return None

    try:
        init_ts = data.get("last_run_initialisation_time")
        if init_ts is None:
            return None

        def _ts(key):
            # fromtimestamp(tz=UTC).replace(tzinfo=None) → datetime UTC tz-naïf,
            # cohérent avec toutes les autres datetime du pipeline.
            v = data.get(key)
            return (dt.datetime.fromtimestamp(v, tz=dt.timezone.utc).replace(tzinfo=None)
                    if v is not None else None)

        return {
            "run_date":           _ts("last_run_initialisation_time"),
            "availability_time":  _ts("last_run_availability_time"),
            "modification_time":  _ts("last_run_modification_time"),
            "update_interval_h":  data.get("update_interval_seconds", 0) / 3600,
            "temporal_resolution_h": data.get("temporal_resolution_seconds", 0) / 3600,
        }
    except (TypeError, OSError, ValueError):
        return None


def _fetch_all_metadata(models):
    """Interroge en parallèle la Metadata API pour tous les modèles.

    Retourne un dict {label → meta_dict | None}. Un modèle sans meta_slug ou dont
    l'endpoint échoue reçoit None (repli sur infer_run_date dans parse_payload).
    """
    def _fetch_one(model):
        url_tpl = model.get("meta_base_url") or C.META_API_URL_TPL
        return model["label"], fetch_model_metadata(model.get("meta_slug"), url_tpl)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as pool:
        return dict(pool.map(_fetch_one, models))


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
    try:
        resp = requests.get(C.API_URL, params=params, timeout=C.HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise SystemExit(
            f"❌ Timeout Open-Meteo après {C.HTTP_TIMEOUT} s — "
            "l'API est lente ou injoignable. Relancer dans quelques minutes."
        )
    except requests.exceptions.ConnectionError as exc:
        raise SystemExit(f"❌ Erreur réseau Open-Meteo : {exc}")
    except requests.exceptions.HTTPError as exc:
        raise SystemExit(f"❌ Erreur HTTP Open-Meteo {exc.response.status_code} : {exc}")
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


def _model_last_valid(hourly, valid_time, model_api, members):
    """Dernière `valid_time` où AU MOINS un membre du modèle a une valeur non-NaN
    pour la variable primaire (= init + horizon réellement publié). NaT si le
    modèle est absent du payload. Calculé sur le payload BRUT — avant tout
    masquage de fraîcheur — car c'est la couverture réelle de l'API qui porte
    l'identité du cycle (cf. infer_run_date)."""
    primary = C.VARIABLES[0]["api"]
    mask = np.zeros(len(valid_time), dtype=bool)
    for member in members:
        vals = hourly.get(_series_key(primary, model_api, member))
        if vals is not None:
            mask |= pd.to_numeric(pd.Series(vals), errors="coerce").notna().to_numpy()
    return valid_time[mask].max() if mask.any() else pd.NaT


def parse_payload(payload, now_utc=None):
    """JSON Open-Meteo → DataFrame plat. Grille horaire uniforme conservée jusqu'à
    16 j ; un modèle qui s'arrête tôt laisse simplement des NaN en queue.

    Chaque modèle reçoit SA propre `run_date` — source prioritaire : la Metadata
    API officielle (last_run_initialisation_time, cf. _fetch_all_metadata), qui
    donne le cycle réel sans heuristique. Repli sur infer_run_date() si l'endpoint
    est absent ou inaccessible. Un même fetch peut donc mélanger des modèles à des
    cycles différents (ex. GEM en 0Z pendant que ECMWF/AIFS/GEFS sont en 6Z).

    Aucune troncature par horizon nominal ici : l'horizon réel d'un cycle (combien
    d'échéances un modèle a effectivement recalculées) varie d'un jour à l'autre et
    n'est pas un fait fixe par modèle (constaté empiriquement sur AIFS) — toute
    table d'horizons codée en dur serait donc non fiable. La distinction entre
    « échéances réellement renouvelées » et « queue collée de l'ancien cycle » se
    fait plus loin, empiriquement, par comparaison échéance-par-échéance au dernier
    run stocké (cf. mask_stale_tail).
    """
    hourly = payload["hourly"]
    # C.TIMEZONE="UTC" → utc_offset_seconds vaut toujours 0 (pas de notion de DST
    # à soustraire). On le lit quand même depuis le payload plutôt que de le
    # supposer nul en dur, au cas où l'appel serait un jour reconfiguré.
    utc_offset = int(payload.get("utc_offset_seconds", 0))
    valid_time = pd.to_datetime(hourly["time"]) - pd.Timedelta(seconds=utc_offset)

    # Récupération parallèle des métadonnées avant la boucle modèle.
    all_meta = _fetch_all_metadata(C.MODELS)

    frames = []
    source_by_model = {}   # {label → "meta" | "heuristique"} — exposé à main()
    for model in C.MODELS:
        members = _members_of(hourly, model["api"])
        last_valid = _model_last_valid(hourly, valid_time, model["api"], members)

        meta = all_meta.get(model["label"])
        if meta is not None and meta["run_date"] is not None:
            model_run_date = meta["run_date"]
            source_by_model[model["label"]] = "meta"
            # Audit croisé : avertir si metadata et heuristique s'écartent
            # significativement (bug de slug, endpoint périmé…).
            heuristic = infer_run_date(model, last_valid, now_utc)
            diff_h = abs((model_run_date - heuristic).total_seconds()) / 3600
            if diff_h > C.META_HEURISTIC_DIVERGENCE_WARN_H:
                print(f"   ⚠️  {model['label']} : metadata run_date={model_run_date} "
                      f"diverge de {diff_h:.1f} h vs heuristique ({heuristic}) — "
                      "vérifier le meta_slug dans config.py")
        else:
            model_run_date = infer_run_date(model, last_valid, now_utc)
            source_by_model[model["label"]] = "heuristique"

        for member in members:
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
        return pd.DataFrame(columns=C.SCHEMA), source_by_model

    df = pd.concat(frames, ignore_index=True)

    if C.DROP_EMPTY_SERIES:
        # Écarte les séries (modèle, membre) entièrement vides (modèle indisponible
        # ce run) ; conserve les séries partielles (valides + queue NaN).
        non_empty = df.groupby(["model", "member"])[C.VAR_COLS].transform(
            lambda s: s.notna().any())
        df = df[non_empty.any(axis=1)].reset_index(drop=True)

    return df[C.SCHEMA], source_by_model


# --------------------------------------------------------------------------- #
#  Fraîcheur — détection empirique, échéance par échéance
# --------------------------------------------------------------------------- #
def load_existing():
    """Base existante, réalignée sur le schéma courant : une variable ajoutée à
    config.VARIABLES après l'écriture du parquet n'y a pas encore de colonne —
    créée ici à NaN, sinon toute comparaison colonne à colonne sur l'historique
    (mask_stale_tail, _reach_h_by_key) lèverait un KeyError au premier fetch."""
    if os.path.exists(C.DB_PATH):
        df = pd.read_parquet(C.DB_PATH)
        for col in C.VAR_COLS:
            if col not in df.columns:
                df[col] = np.nan
        return df
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


_MODEL_BY_LABEL = {m["label"]: m for m in C.MODELS}


def _contiguous_reach_h(run_date, valid_times):
    """Portée réelle CONTIGUË (h) : dernière échéance valide atteignable depuis
    `run_date` sans trou > PERSIST_MAX_GAP_H entre échéances valides successives
    (run_date compris comme point de départ). Les échéances antérieures au cycle
    (passé rebouché par l'API depuis 00:00 local, partagé avec l'ancien cycle)
    sont ignorées : elles ne disent rien de la fraîcheur du run.

    Sans cette contiguïté, une réponse creuse de l'API — un point parasite isolé
    en queue au-dessus d'un désert de NaN — simulerait une portée pleine, et le
    run fantôme passerait le seuil de persistance PUIS bloquerait le vrai run
    via la garde anti-régression (cf. config.PERSIST_MAX_GAP_H)."""
    hours = np.sort(((valid_times[valid_times >= run_date] - run_date)
                     / pd.Timedelta(hours=1)).unique())
    hours = np.concatenate(([0.0], hours))
    breaks = np.flatnonzero(np.diff(hours) > C.PERSIST_MAX_GAP_H)
    return hours[breaks[0]] if breaks.size else hours[-1]


def _persist_horizon_reach_h(group):
    """Portée réelle contiguë (h) du run frais (cf. _contiguous_reach_h).
    None si aucune valeur valide (rien à mesurer). Une ligne compte dès qu'AU
    MOINS une variable y est valide (how="all") : la portée d'un run est portée
    par sa variable principale — une variable secondaire à couverture moindre
    ne doit ni raccourcir la portée mesurée, ni bloquer la persistance."""
    valid = group.dropna(subset=C.VAR_COLS, how="all")
    if valid.empty:
        return None
    run_date = pd.Timestamp(group["run_date"].iloc[0])
    return _contiguous_reach_h(run_date, valid["valid_time"])


def _meets_persist_horizon(model_label, group):
    """Le run frais de ce modèle est-il assez avancé pour être persisté ?

    cf. config.PERSIST_HORIZON_TOLERANCE_H / MIN_PERSIST_HORIZON_H : deux causes
    distinctes peuvent rendre un run frais court, dans les deux cas on ne veut PAS
    l'écrire comme dernier run du modèle (il masquerait un run PLEIN déjà en base) :
      1. modèle à horizon plein 4×/j (AIFS, GEFS) : run encore en cours de calcul
         côté Open-Meteo au moment du poll → retentera de lui-même au poll suivant ;
      2. cycle nativement plus court par construction (ex. ECMWF ENS 6Z/18Z
         ≈ 144 h contre 360 h à 0Z/12Z) → ne dépassera JAMAIS le seuil, ce run ne
         sera donc simplement jamais persisté (voulu : comparaison à horizon plein
         entre modèles principaux).
    Troisième cas couvert par la même mesure : une réponse CREUSE de l'API
    (désert de NaN percé de quelques points isolés) — la portée étant contiguë
    (cf. _contiguous_reach_h), un point parasite en queue ne la gonfle pas et le
    run fantôme reste sous le seuil.
    On attend qu'il atteigne son horizon nominal (à tolérance près), ou un seuil
    fixe si le modèle n'a pas d'horizon_h connu (ex. GEM)."""
    reach_h = _persist_horizon_reach_h(group)
    if reach_h is None:
        return False
    horizon = _MODEL_BY_LABEL.get(model_label, {}).get("horizon_h")
    threshold = (horizon - C.PERSIST_HORIZON_TOLERANCE_H) if horizon is not None \
        else C.MIN_PERSIST_HORIZON_H
    return reach_h >= threshold


def filter_fresh_rows(fresh, existing):
    """Applique mask_stale_tail à chaque modèle, puis n'accepte à la persistance
    que les modèles dont le run frais atteint une portée suffisante (cf.
    _meets_persist_horizon) — que ce soit un calcul encore en cours (AIFS/GEFS,
    se résout au poll suivant) ou un cycle nativement court (ECMWF 6Z/18Z, ne se
    résoudra jamais) est laissé de côté, comme un cycle inchangé : l'ancien run
    complet reste en base.

    Écarte entièrement les modèles sans la moindre échéance renouvelée (cf.
    mask_stale_tail), et ceux dont le run renouvelé reste trop court pour être
    comparable aux autres modèles."""
    kept, stale_labels, partial_labels = [], [], []
    for model_label, candidate in fresh.groupby("model"):
        masked, a_du_neuf = mask_stale_tail(model_label, candidate, existing)
        if not a_du_neuf:
            stale_labels.append(model_label)
        elif _meets_persist_horizon(model_label, masked):
            kept.append(masked)
        else:
            partial_labels.append(model_label)
    out = pd.concat(kept, ignore_index=True) if kept else fresh.iloc[0:0]
    return out, stale_labels, partial_labels


def complete_missing_vars(candidate, existing):
    """Comble, sur le DERNIER run déjà stocké de chaque modèle, une variable
    ENTIÈREMENT absente de ce run (ex. z500 ajouté à config.VARIABLES après
    coup, alors que le cycle en base n'a pas changé depuis) avec les valeurs du
    poll courant — jamais les variables déjà renseignées (ex. t850), qui
    restent gérées par mask_stale_tail/filter_fresh_rows.

    Un cycle inchangé (t850 identique) est normalement écarté par
    filter_fresh_rows comme « rien de neuf » ; c'est correct pour t850, mais
    empêcherait alors À VIE qu'une variable ajoutée en cours de cycle soit
    persistée, puisque le cycle ne redeviendra jamais « frais » pour autant.
    Cette fonction traite ce cas séparément : une variable absente n'a par
    définition aucune « queue périmée » à détecter (rien à comparer), donc pas
    de passage par mask_stale_tail — c'est un premier remplissage, pas un
    re-poll à filtrer.

    Absence AVÉRÉE uniquement (colonne 100 % NaN pour ce run stocké) — une
    variable partiellement renseignée n'est jamais retouchée par cette voie.
    Ne comble que les couples (member, valid_time) déjà présents dans le run
    stocké — jamais d'extension de portée ici (uniquement filter_fresh_rows
    étend la portée d'un run). Ne s'applique que si `candidate` porte
    exactement le même run_date que celui déjà stocké : un run_date différent
    est un cycle réellement nouveau, déjà couvert par filter_fresh_rows.

    Retourne un DataFrame au format de `fresh` (prêt pour persist()), vide si
    rien à compléter."""
    if existing.empty or candidate.empty:
        return candidate.iloc[0:0]
    frames = []
    for label in C.MODEL_LABELS:
        ex_model = existing[existing["model"] == label]
        if ex_model.empty:
            continue
        latest_rd = ex_model["run_date"].max()
        ex_group = ex_model[ex_model["run_date"] == latest_rd]
        missing_cols = [c for c in C.VAR_COLS if ex_group[c].isna().all()]
        if not missing_cols:
            continue
        cand_group = candidate[(candidate["model"] == label)
                                & (candidate["run_date"] == latest_rd)]
        if cand_group.empty:
            continue
        fillable = [c for c in missing_cols if cand_group[c].notna().any()]
        if not fillable:
            continue
        completed = ex_group.merge(
            cand_group[["member", "valid_time", *fillable]],
            on=["member", "valid_time"], how="left", suffixes=("", "_new"))
        for col in fillable:
            completed[col] = completed[f"{col}_new"]
            completed = completed.drop(columns=[f"{col}_new"])
        frames.append(completed[C.SCHEMA])
    if not frames:
        return candidate.iloc[0:0]
    return pd.concat(frames, ignore_index=True)


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


def _reach_h_by_key(df):
    """{(modèle, run_date) → portée réelle CONTIGUË en heures} sur les lignes à
    valeur valide de `df` (cf. _contiguous_reach_h — même métrique que le filtre
    de persistance : un run creux déjà en base ne peut donc pas surclasser un
    run frais sain). Clés sans la moindre valeur valide absentes du dict."""
    # how="all" : une ligne est valide dès qu'une variable l'est — indispensable
    # pour que les runs historiques (antérieurs à l'ajout d'une variable, donc
    # NaN sur celle-ci) gardent leur vraie portée : sinon la garde anti-régression
    # les croirait vides et laisserait un glitch API écraser un run complet.
    valid = df.dropna(subset=C.VAR_COLS, how="all")
    if valid.empty:
        return {}
    return {key: _contiguous_reach_h(key[1], g["valid_time"])
            for key, g in valid.groupby(["model", "run_date"])}


def _drop_regressions(fresh, existing):
    """Dernier rempart avant écriture, indépendant de filter_fresh_rows : retire de
    `fresh` tout (run_date, modèle) qui RÉGRESSERAIT un run déjà persisté sous ce
    même couple (glitch API renvoyant, pour un cycle déjà en base, moins
    d'échéances valides qu'avant — ex. réponse tronquée d'un poll). On ne compare
    que les couples présents des DEUX côtés : un `run_date` nouveau pour ce modèle
    n'a rien à régresser, c'est un cycle différent (cf. filter_fresh_rows pour sa
    propre complétude)."""
    overlap = existing[["model", "run_date"]].drop_duplicates().merge(
        fresh[["model", "run_date"]].drop_duplicates(), on=["model", "run_date"])
    if overlap.empty:
        return fresh

    existing_reach = _reach_h_by_key(existing)
    fresh_reach = _reach_h_by_key(fresh)
    regressed = [
        (row.model, row.run_date) for row in overlap.itertuples()
        if existing_reach.get((row.model, row.run_date), -1)
        > fresh_reach.get((row.model, row.run_date), -1)
    ]
    if not regressed:
        return fresh

    labels = ", ".join(f"{m} {run_label(rd)} ({rd.date()})" for m, rd in regressed)
    print(f"   🛡️  Run existant plus complet conservé (fresh régressif ignoré) : {labels}")
    idx = pd.MultiIndex.from_tuples(regressed, names=["model", "run_date"])
    keep = ~fresh.set_index(["model", "run_date"]).index.isin(idx)
    return fresh[keep].reset_index(drop=True)


def persist(fresh, existing=None):
    """Fusion atomique : retire, pour chaque (run_date, modèle) présent dans
    `fresh`, les lignes déjà stockées sous ce même couple, puis append. Comme
    `fresh` peut mélanger des modèles à des cycles différents (ex. GEM en 0Z,
    ECMWF en 6Z), le dédoublonnage se fait par couple — jamais par run_date seul.
    Les modèles absents de `fresh` (cycle pas encore renouvelé, cf.
    filter_fresh_models) gardent leur run antérieur intact — jamais de perte
    d'historique.

    Avant tout remplacement, `_drop_regressions` écarte les couples où `fresh`
    serait MOINS complet que ce qui est déjà en base sous ce même (run_date,
    modèle) : on ne remplace jamais un run complet par une version régressive.

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
        fresh = _drop_regressions(fresh, existing)
        if fresh.empty:
            return existing
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

    candidate, sources = parse_payload(payload, now_utc)
    rd_by_model = candidate.drop_duplicates("model").set_index("model")["run_date"]
    _src_tag = {"meta": "📡", "heuristique": "~"}
    print("   Cycle retenu par modèle : " +
          ", ".join(
              f"{m['label']} {run_label(rd_by_model[m['label']])} "
              f"({_src_tag.get(sources.get(m['label'], ''), '?')} "
              f"{sources.get(m['label'], '?')})"
              for m in C.MODELS if m["label"] in rd_by_model.index))

    existing = load_existing()
    fresh, stale, partial = filter_fresh_rows(candidate, existing)
    if stale:
        print(f"   ⏸️  Cycle inchangé (run déjà en stock conservé) : {', '.join(stale)}")
    if partial:
        print(f"   ⏳ Cycle détecté mais portée contiguë trop courte pour être persisté "
              f"(calcul en cours, cycle nativement partiel — ex. ECMWF 6Z/18Z — ou "
              f"réponse creuse ; run complet précédent conservé) : {', '.join(partial)}")

    # Cycle inchangé (t850 identique, écarté ci-dessus) ne veut pas dire « rien à
    # faire » : une variable ajoutée à config.VARIABLES après coup (ex. z500) peut
    # rester absente à vie sur ce run si on ne la comble pas séparément.
    completions = complete_missing_vars(candidate, existing)
    completed_labels = set(completions["model"].unique()) if not completions.empty else set()
    if completed_labels:
        print(f"   🧩 Variable manquante comblée sur le run déjà stocké : "
              f"{', '.join(sorted(completed_labels))}")
        fresh = pd.concat([fresh, completions], ignore_index=True) if not fresh.empty else completions

    if fresh.empty:
        print("ℹ️  Aucun modèle renouvelé à ce poll — base laissée telle quelle.")
        return
    for model_label, g in fresh.groupby("model"):
        valid = g.dropna(subset=C.VAR_COLS, how="all")
        last = valid["valid_time"].max() if not valid.empty else None
        if model_label in completed_labels:
            print(f"   🧩 {model_label} — run inchangé, variable comblée jusqu'à {last}")
        else:
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
