# -*- coding: utf-8 -*-
"""Sélections de runs : quels runs pooler pour chaque vue du dashboard.

C'est ici que vivent les trois politiques de sélection (invariants CLAUDE.md) :
  • latest_complete_run_sub — vues COMBINÉES : dernier run à HORIZON PLEIN par
    modèle (complétude mesurée empiriquement, jamais par heure de cycle) ;
  • latest_run_sub — « Dernier run » d'Explorer : dernier run non vide par
    modèle, AUCUNE exigence d'horizon (fraîcheur maximale, même partielle) ;
  • completed_pooled_sub — Convergence : backfill inter-runs échéance par
    échéance des modèles principaux (jamais les modèles d'appoint).
Ne pas « unifier » ces politiques : leurs différences sont voulues."""

import os
from datetime import datetime

import pandas as pd
import streamlit as st

import config as C
from app.runtime import VAR, user_tz
from app.data.db import load_db, list_runs, run_slice, utc_cycle
from app.stats.ensemble import super_ensemble, daily_aggregate

# Tolérance (heures) entre la portée RÉELLE d'un run stocké (max valid_time −
# run_date) et l'horizon nominal du modèle (config `horizon_h`), pour juger qu'un
# run atteint « l'horizon plein ». Assez large pour absorber le pas d'échéance et
# une légère avance de coupure, assez serré pour écarter les cycles courts.
FULL_HORIZON_TOLERANCE_H = 24


@st.cache_data(show_spinner=False)
def latest_complete_run_sub(_sig, as_of=None):
    """Pool multi-modèles où CHAQUE modèle est représenté par son dernier run à
    HORIZON PLEIN — base des vues combinées (super-ensemble global).

    `as_of` (run_date local, optionnel) : rejoue la sélection telle qu'elle
    était à ce cycle — seuls les runs `run_date ≤ as_of` sont considérés, la
    logique de complétude reste identique. Sert au sélecteur « Vu depuis » de
    la Vue d'ensemble (versions antérieures du super-ensemble).

    La complétude se mesure EMPIRIQUEMENT sur la portée réelle du run stocké
    (max valid_time − run_date ≥ horizon_h − tolérance), jamais par une règle
    codée en dur sur l'heure de cycle : un 6Z/18Z qui atteint réellement le plein
    horizon est donc éligible, et un 0Z/12Z anormalement court est écarté (cf.
    invariant : l'horizon réel d'un cycle varie d'un jour à l'autre). Chaque
    modèle garde son propre run_date/cycle — aucun cycle global partagé.

    Modèles sans `horizon_h` déclaré (ex. GEM) : complétude non jugeable → on
    retient leur dernier run non vide (à leurs cycles réels, jamais backfillé).
    Repli général : si aucun run n'atteint l'horizon plein, on prend le dernier
    run non vide (le modèle reste présent, signalé « horizon réduit »).

    Retourne (sub, sources, partial) :
      - sub     : lignes poolées (mêmes colonnes que la base) ;
      - sources : {label → run_date retenu} ;
      - partial : modèles principaux sans aucun run à horizon plein récent."""
    df = load_db(_sig)
    if as_of is not None:
        df = df[df["run_date"] <= pd.Timestamp(as_of)]
    if df.empty:
        return df, {}, []
    frames, sources, partial = [], {}, []
    for model in C.MODELS:
        label = model["label"]
        mdf = df[df["model"] == label]
        if mdf.empty:
            continue
        horizon = model.get("horizon_h")
        chosen, fallback = None, None
        for rd in sorted(mdf["run_date"].unique(), reverse=True):
            valid = mdf[(mdf["run_date"] == rd)].dropna(subset=[VAR])
            if valid.empty:
                continue
            if fallback is None:
                fallback = rd  # dernier run non vide, quel que soit son horizon
            if horizon is None:
                chosen = rd   # complétude non jugeable → plus récent non vide
                break
            reach_h = (valid["valid_time"].max() - pd.Timestamp(rd)) / pd.Timedelta(hours=1)
            if reach_h >= horizon - FULL_HORIZON_TOLERANCE_H:
                chosen = rd
                break
        if chosen is None:
            chosen = fallback
            if chosen is not None and horizon is not None:
                partial.append(label)  # aucun run à horizon plein → repli signalé
        if chosen is None:
            continue
        frames.append(mdf[mdf["run_date"] == chosen])
        sources[label] = chosen
    if not frames:
        return df.iloc[0:0], sources, partial
    return pd.concat(frames, ignore_index=True), sources, partial


@st.cache_data(show_spinner=False)
def latest_run_sub(_sig):
    """Pool « dernier run » : pour CHAQUE modèle, son dernier run non vide, quel
    que soit son cycle (0/6/12/18Z) — contrairement aux vues combinées
    (latest_complete_run_sub), AUCUNE exigence d'horizon plein : on montre
    l'information la plus fraîche disponible, même partielle. Chaque modèle
    garde son propre run_date/cycle. Retourne (sub, sources)."""
    df = load_db(_sig)
    if df.empty:
        return df, {}
    frames, sources = [], {}
    for label in C.MODEL_LABELS:
        mdf = df[df["model"] == label]
        valid = mdf.dropna(subset=[VAR])
        if valid.empty:
            continue
        rd = valid["run_date"].max()
        frames.append(mdf[mdf["run_date"] == rd])
        sources[label] = rd
    if not frames:
        return df.iloc[0:0], sources
    return pd.concat(frames, ignore_index=True), sources


@st.cache_data(show_spinner=False)
def latest_z500_sub(_sig, as_of=None):
    """Pool Z500 : pour CHAQUE modèle, son dernier run contenant du z500 valide —
    en remontant l'historique si le run T850 retenu par latest_complete_run_sub
    n'en a pas (ex. juste après l'ajout de la variable, ou run legacy comblé
    entre-temps). Le géopotentiel n'est qu'un contexte synoptique : aucune
    exigence d'horizon/complétude ici, contrairement aux vues combinées — on
    veut la dernière valeur connue par modèle, même issue d'un run plus ancien
    que celui affiché pour T850. `as_of` : mêmes bornes que
    latest_complete_run_sub (rejeu « Vu depuis »). z500 absent partout (base
    pas encore alimentée à cette date, runs legacy) → pool vide, dégradation
    silencieuse en aval (var_median renvoie None)."""
    df = load_db(_sig)
    if as_of is not None:
        df = df[df["run_date"] <= pd.Timestamp(as_of)]
    if df.empty or "z500" not in df.columns:
        return df.iloc[0:0]
    frames = []
    for label in C.MODEL_LABELS:
        mdf = df[df["model"] == label]
        valid = mdf.dropna(subset=["z500"])
        if valid.empty:
            continue
        rd = valid["run_date"].max()
        frames.append(mdf[mdf["run_date"] == rd])
    if not frames:
        return df.iloc[0:0]
    return pd.concat(frames, ignore_index=True)


def main_labels_expected_at(run_date):
    """Modèles principaux attendus au cycle synoptique de `run_date`.
    Utilise `expected_cycles` (config) — ex. ECMWF attendu seulement à 0Z/12Z,
    donc absent à 6Z/18Z sans déclencher d'alerte."""
    h = utc_cycle(run_date).hour
    return [m for m in C.MAIN_LABELS if h in C.EXPECTED_CYCLES_BY_LABEL.get(m, [])]


def latest_refresh_status(runs, sig):
    """Heure du dernier rafraîchissement (mtime du parquet, dans le fuseau de
    l'utilisateur — jamais l'heure du serveur, qui est UTC sur le cloud) et
    complétude (tous les modèles principaux ATTENDUS À CE CYCLE présents ou
    non) du dernier run."""
    if runs.empty:
        return None, True, []
    try:
        refreshed_at = datetime.fromtimestamp(os.path.getmtime(C.DB_PATH), tz=user_tz())
    except OSError:
        refreshed_at = None
    last_rd = runs.iloc[0]["run_date"]
    present = set(run_slice(sig, last_rd)["model"].unique())
    expected = main_labels_expected_at(last_rd)
    missing = [m for m in expected if m not in present]
    return refreshed_at, not missing, missing


def previous_runs_sub(sig, sub):
    """Pool « run précédent » : pour chaque couple (modèle, run) présent dans
    `sub`, les lignes du dernier run STRICTEMENT antérieur de CE modèle — chaque
    modèle recule vers son propre cycle précédent, jamais de cycle global
    partagé (un 6Z peut ainsi être comparé au 0Z pour ECMWF et au 6Z−6h pour
    GEFS). Sert de référence aux colonnes Δ des tableaux d'export. None si
    aucun modèle n'a d'antécédent."""
    df = load_db(sig)
    frames = []
    for (model, rd), _ in sub.groupby(["model", "run_date"]):
        prior = df[(df["model"] == model) & (df["run_date"] < rd)].dropna(subset=[VAR])
        if prior.empty:
            continue
        prev_rd = prior["run_date"].max()
        frames.append(df[(df["model"] == model) & (df["run_date"] == prev_rd)])
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def n_days_before_sub(sig, sub, days):
    """Pool « run le plus proche de J-`days` » : pour chaque couple (modèle,
    run) présent dans `sub`, le run de CE modèle dont le run_date est le PLUS
    PROCHE de (run_date − `days` jours), parmi les runs STRICTEMENT antérieurs
    au run affiché (jamais un cycle postérieur ou le run lui-même). Sert aux
    colonnes de variation T850 J-1/J-2 des tableaux d'export — contrairement à
    previous_runs_sub (cycle précédent immédiat, quel qu'il soit), on vise ici
    une cible calendaire, avec repli sur le cycle disponible le plus proche si
    l'exact J-`days` n'existe pas. None si aucun modèle n'a d'antécédent."""
    df = load_db(sig)
    frames = []
    for (model, rd), _ in sub.groupby(["model", "run_date"]):
        mdf = df[(df["model"] == model) & (df["run_date"] < rd)].dropna(subset=[VAR])
        if mdf.empty:
            continue
        target = pd.Timestamp(rd) - pd.Timedelta(days=days)
        dates = mdf["run_date"].unique()
        closest = min(dates, key=lambda d: abs(pd.Timestamp(d) - target))
        frames.append(df[(df["model"] == model) & (df["run_date"] == closest)])
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


# Backfill inter-runs, ÉCHÉANCE PAR ÉCHÉANCE : pour chaque modèle PRINCIPAL, on
# part du run courant (sa portion réellement fraîche, cf. Forecast.mask_stale_tail
# côté pipeline — au-delà, NaN) et on comble les échéances encore NaN avec celles
# du run antérieur le plus proche qui les couvre, et ainsi de suite jusqu'à
# n-3 (3 runs sautés). Un run partiel à 6Z/18Z se voit donc complété par la
# moitié du run précédent (lui-même éventuellement partiel), puis par celui
# d'avant si besoin — jamais une simple substitution tout-ou-rien par modèle.
#
# Les modèles d'appoint (non principaux, ex. GEM) ne sont JAMAIS backfillés :
# GEM n'existe qu'à 0Z/12Z (cf. config.MODELS `cycles`), le comparer doit donc
# toujours se faire cycle identique à cycle identique — pas de « GEM 6Z » fabriqué.
BACKFILL_MAX_LOOKBACK = 3

# Tolérance (heures) sous l'horizon nominal pour qu'un run CANDIDAT au backfill
# soit jugé valide (portée réelle = dernière échéance − SON PROPRE run_date).
# Plus large que FULL_HORIZON_TOLERANCE_H (vues combinées) : on veut ici juste
# écarter les runs franchement périmés (queue recollée de l'ancien cycle, cf.
# Forecast.mask_stale_tail — portée nulle ou négative), pas exiger un horizon
# quasi complet. Sans ce filtre, un run comme GEFS 12Z entièrement périmé
# (aucune échéance ≥ son propre cycle) est certes ignoré comme source, mais
# silencieusement — et la recherche saute alors un run 18Z pourtant valide
# situé plus loin si celui-ci n'est plus dans la liste examinée.
BACKFILL_HORIZON_TOLERANCE_H = 40
MODEL_HORIZON_H = {m["label"]: m.get("horizon_h") for m in C.MODELS}


def completed_pooled_sub(runs, pos, sig, max_lookback=BACKFILL_MAX_LOOKBACK,
                         run_slicer=run_slice):
    """Lignes du run `pos` (index dans `runs`, trié du plus récent au plus
    ancien), en complétant, échéance par échéance, les NaN des modèles PRINCIPAUX
    avec les runs antérieurs (pos+1 → pos+max_lookback) — priorité au plus frais.

    Retourne (sub_complet, sources) où `sources` mappe chaque modèle principal à
    la liste des run_date effectivement utilisés (le run courant en premier s'il
    a contribué, puis les runs antérieurs ayant comblé des échéances manquantes ;
    liste vide si le modèle est introuvable dans toute la fenêtre).

    `run_slicer(sig, run_date)` : fournisseur des lignes d'un run — par défaut
    run_slice (scan O(N) de la base). Quand cette fonction est appelée en boucle
    sur tous les runs (Convergence), l'appelant peut injecter un index local
    {run_date → lignes} construit une seule fois, supprimant le rescan redondant
    (même sortie, cf. convergence_long). Défaut inchangé → harnais/autres appelants
    non affectés."""
    n = len(runs)
    run_start = runs.iloc[pos]["run_date"]  # cycle (heure locale) du run analysé
    frames, sources = [], {}
    for model in C.MAIN_LABELS:
        used, covered_vt = [], set()
        horizon = MODEL_HORIZON_H.get(model)
        for j in range(pos, min(pos + max_lookback + 1, n)):
            cand_run_date = runs.iloc[j]["run_date"]
            cand = run_slicer(sig, cand_run_date)
            cand = cand[cand["model"] == model]
            if cand.empty:
                continue
            cand_valid = cand.dropna(subset=[VAR])
            if cand_valid.empty:
                continue
            # Run candidat invalide (queue périmée du cycle précédent) : sa portée
            # réelle depuis SON PROPRE run_date n'atteint pas l'horizon nominal
            # (à BACKFILL_HORIZON_TOLERANCE_H près) → on l'écarte explicitement de
            # la recherche, plutôt que de compter sur le fait qu'il ne recouvrira
            # par coïncidence aucune échéance utile du run analysé.
            if horizon is not None:
                reach_h = ((cand_valid["valid_time"].max() - pd.Timestamp(cand_run_date))
                           / pd.Timedelta(hours=1))
                if reach_h < horizon - BACKFILL_HORIZON_TOLERANCE_H:
                    continue
            # On ne considère que les échéances À VENIR (≥ cycle du run) : les heures
            # antérieures au cycle sont du passé, rebouchées par l'API depuis 00:00
            # local (et souvent aussi servies par le run précédent). La convergence
            # les jette de toute façon (filtre target ≥ run) ; les compter ici
            # faisait apparaître QUASI TOUS les runs comme « complétés » par un
            # ancien alors qu'ils sont pleins — bruit massif. On garde ensuite les
            # échéances valides et pas déjà couvertes (priorité au plus frais).
            valid = cand_valid[(cand_valid["valid_time"] >= run_start)
                                & (~cand_valid["valid_time"].isin(covered_vt))]
            if valid.empty:
                continue
            frames.append(valid)
            covered_vt.update(valid["valid_time"].unique())
            used.append(cand_run_date)
        sources[model] = used
    # Modèles d'appoint (non principaux) : jamais backfillés, uniquement si présents
    # au run courant (mêmes échéances à venir, pour rester cohérent avec ci-dessus).
    current = run_slicer(sig, run_start)
    extra = current[(~current["model"].isin(C.MAIN_LABELS))
                    & (current["valid_time"] >= run_start)]
    if not extra.empty:
        frames.append(extra)
    if not frames:
        return None, sources
    return pd.concat(frames, ignore_index=True), sources


def completed_super_ensemble_daily(runs, pos, sig, max_lookback=BACKFILL_MAX_LOOKBACK,
                                   run_slicer=run_slice):
    """Super-ensemble journalier du run `pos`, modèles principaux complétés
    échéance par échéance. Retourne (df_daily, sources) — voir completed_pooled_sub."""
    sub_complet, sources = completed_pooled_sub(runs, pos, sig, max_lookback, run_slicer)
    if sub_complet is None:
        return None, sources
    return daily_aggregate(super_ensemble(sub_complet)), sources


def _convergence_runs(runs):
    """Filtre les runs affichés en convergence : les cycles 6Z/18Z sont partiels
    par construction — ECMWF/AIFS s'arrêtent à mi-période, GEM n'y existe pas
    (cf. config.MODELS) — et pollueraient la carte de lignes à moitié remplies
    sur l'historique. On ne garde donc les cycles 6Z/18Z que pour les DEUX runs
    les plus récents (`runs` est trié du plus récent au plus ancien) ; au-delà,
    seuls 0Z/12Z (tous modèles dispos sur la période complète) sont conservés."""
    runs = runs.reset_index(drop=True)
    recent = runs.index < 2
    main_cycle = runs["run_date"].apply(lambda d: utc_cycle(d).hour in (0, 12))
    return runs[recent | main_cycle].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def convergence_long(_sig):
    """Cœur lourd de la page Convergence, calculé UNE fois par donnée (clé `_sig`,
    invalidée comme load_db). Retourne (long, backfill_src) :
      • long : format long (run_dt / lead / target / median / p10 / p90) — médiane
        et P10/P90 JOURNALIÈRES de chaque run affiché, super-ensemble COMPLÉTÉ
        (backfill échéance par échéance des modèles principaux) et limité aux
        journées À VENIR de chaque run (target ≥ jour du cycle) ;
      • backfill_src : {run_date → sources} (cf. completed_pooled_sub).

    Optimisation clé face à la croissance de l'historique : un index LOCAL des
    lignes par run_date, construit une seule fois par un unique groupby puis
    libéré à la sortie (n'est PAS mis en cache — pas de seconde copie persistante
    de la base en mémoire), remplace le rescan O(N) que run_slice referait à
    chaque run × chaque fenêtre de backfill. Sortie strictement identique à la
    boucle historique (mêmes lignes, même ordre) : seul le chemin d'accès change."""
    runs_full = list_runs(_sig).reset_index(drop=True)
    runs = _convergence_runs(runs_full)
    if len(runs) < 2:
        return pd.DataFrame(columns=["run_dt", "lead", "target",
                                     "median", "p10", "p90"]), {}
    full_pos = {rd: i for i, rd in enumerate(runs_full["run_date"])}
    df = load_db(_sig)
    slices = {rd: g for rd, g in df.groupby("run_date", sort=False)}
    _empty = df.iloc[0:0]
    slicer = lambda _s, rd: slices.get(rd, _empty)
    records = []
    backfill_src = {}
    for pos in range(len(runs)):
        r = runs.iloc[pos]
        syn, sources = completed_super_ensemble_daily(
            runs_full, full_pos[r["run_date"]], _sig, run_slicer=slicer)
        backfill_src[r["run_date"]] = sources
        if syn is None or syn.empty:
            continue
        for _, row in syn.iterrows():
            target = pd.Timestamp(row["valid_time"]).normalize()
            run_dt = pd.Timestamp(r["run_date"])
            if target < run_dt.normalize():
                continue
            lead = (pd.Timestamp(row["valid_time"]) - run_dt).total_seconds() / 86400
            records.append({"run_dt": r["run_date"], "lead": lead, "target": target,
                            "median": row.get("Médiane"), "p10": row.get("P10"),
                            "p90": row.get("P90")})
    long = pd.DataFrame(records)
    if not long.empty:
        long = long.dropna(subset=["median"])
    return long, backfill_src


@st.cache_data(show_spinner=False)
def trend_daily_medians(_sig, n_runs=8):
    """Médiane/P10/P90 JOURNALIÈRES des `n_runs` derniers runs affichables —
    format long (run_dt / target / median / p10 / p90), pour la section grand
    public « évolution au fil des runs ».

    Mêmes règles de comparabilité que la page Convergence : chaque run est
    recalculé comme super-ensemble COMPLÉTÉ (backfill échéance par échéance des
    modèles principaux, cf. completed_pooled_sub) pour ne jamais comparer
    « 4 modèles vs 1 » ; les cycles 6Z/18Z anciens, partiels par construction,
    sont écartés via _convergence_runs (le backfill, lui, cherche dans TOUS les
    runs). Seules les journées À VENIR de chaque run sont gardées (target ≥ jour
    du cycle) — le passé rebouché par l'API fausserait la comparaison."""
    runs_full = list_runs(_sig).reset_index(drop=True)
    shown = _convergence_runs(runs_full).head(n_runs)
    full_pos = {rd: i for i, rd in enumerate(runs_full["run_date"])}
    records = []
    for rd in shown["run_date"]:
        syn, _ = completed_super_ensemble_daily(runs_full, full_pos[rd], _sig)
        if syn is None or syn.empty:
            continue
        run_day = pd.Timestamp(rd).normalize()
        for _, row in syn.iterrows():
            target = pd.Timestamp(row["valid_time"]).normalize()
            if target < run_day:
                continue
            records.append({"run_dt": rd, "target": target,
                            "median": row.get("Médiane"),
                            "p10": row.get("P10"), "p90": row.get("P90")})
    if not records:
        return pd.DataFrame(columns=["run_dt", "target", "median", "p10", "p90"])
    return pd.DataFrame(records).dropna(subset=["median"]).reset_index(drop=True)
