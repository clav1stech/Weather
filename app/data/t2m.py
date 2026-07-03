# -*- coding: utf-8 -*-
"""Couche données du flux ANNEXE Tx/Tn haute résolution (parquet séparé
data/database_paris_t2m.parquet, produit par forecast_t2m_hd.py).

Lecture seule, dégradation silencieuse : parquet absent, vide ou partiel →
DataFrame vide, jamais d'exception — l'absence de Tx/Tn est un état NORMAL
(flux plus récent que la base, horizon 4 j dépassé, modèle indisponible), le
calendrier du risque s'affiche alors strictement comme avant.

`target_date` est un jour UTC (l'API daily est requêtée en UTC, comme tout le
stockage) : à l'affichage il est mis tel quel en face des jours du calendrier
(heure de Paris) — pour une valeur JOURNALIÈRE, le jour civil UTC et le jour
civil parisien coïncident, il n'y a pas d'heure à convertir."""

import os

import pandas as pd
import streamlit as st

import config as C


def t2m_signature():
    """Signature (mtime) du parquet T2m → invalide le cache à chaque collecte.
    None si le fichier n'existe pas (état normal, pas une anomalie)."""
    try:
        return os.path.getmtime(C.DB_T2M_PATH)
    except OSError:
        return None


@st.cache_data(show_spinner=False)
def load_t2m(_sig):
    """Base Tx/Tn complète (historique append-only). DataFrame vide au schéma
    T2M_SCHEMA si le fichier est absent, illisible ou sans colonne attendue."""
    if _sig is None or not os.path.exists(C.DB_T2M_PATH):
        return pd.DataFrame(columns=C.T2M_SCHEMA)
    try:
        df = pd.read_parquet(C.DB_T2M_PATH)
    except Exception:  # noqa: BLE001 — fichier corrompu/partiel : on dégrade, jamais de crash
        return pd.DataFrame(columns=C.T2M_SCHEMA)
    for col in C.T2M_SCHEMA:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[C.T2M_SCHEMA].copy()
    df["target_date"] = pd.to_datetime(df["target_date"]).dt.normalize()
    return df


_TXTN_COLS = ["date", "tx", "tn", "model", "model_alt", "ecart_tx", "ecart_tn", "solo"]


@st.cache_data(show_spinner=False)
def txtn_by_day(_sig):
    """Un SEUL couple Tx/Tn affiché par jour cible, + de quoi juger sa fiabilité :
    DataFrame _TXTN_COLS [date, tx, tn, model, model_alt, ecart_tx, ecart_tn, solo].

    Pour chaque (modèle, jour), seule la DERNIÈRE collecte compte (l'historique
    ne sert qu'à l'archive). Puis, jour par jour, le premier modèle de
    config.T2M_MODELS ayant au moins une valeur l'emporte (Météo-France
    prioritaire, DWD ICON en secours) — jamais de mélange ni de double affichage.

    Le second modèle éventuel n'est PAS affiché mais sert de recoupement :
      • `model_alt` = son label, `ecart_tx`/`ecart_tn` = |primaire − second| (NaN
        si l'un des deux manque cette variable) — matière à un indicateur
        d'incertitude par divergence (cf. heatwave/logic.incertitude_txtn) ;
      • `solo` = True quand un seul modèle couvre le jour (cas typique J+4→J+6 :
        MF s'arrête à ~4 j, ICON seul) → valeur indicative, sans recoupement.

    Les jours passés sont conservés (dernier état connu) ; le filtrage temporel
    appartient à l'appelant."""
    df = load_t2m(_sig)
    empty = pd.DataFrame(columns=_TXTN_COLS)
    if df.empty:
        return empty
    latest = (df.dropna(subset=["tx", "tn"], how="all")
                .sort_values("fetched_at")
                .groupby(["model", "target_date"], as_index=False).last())
    if latest.empty:
        return empty
    # Rang de priorité = position dans T2M_LABELS ; un label inconnu (retiré de
    # la config après coup) est relégué derrière tous les modèles déclarés.
    rank = {label: i for i, label in enumerate(C.T2M_LABELS)}
    latest["_rank"] = latest["model"].map(lambda m: rank.get(m, len(rank)))

    rows = []
    for date, g in latest.groupby("target_date"):
        g = g.sort_values("_rank")
        primary = g.iloc[0]
        row = {"date": date, "tx": primary["tx"], "tn": primary["tn"],
               "model": primary["model"], "model_alt": None,
               "ecart_tx": float("nan"), "ecart_tn": float("nan"), "solo": True}
        if len(g) >= 2:
            second = g.iloc[1]
            row["model_alt"] = second["model"]
            row["solo"] = False
            if pd.notna(primary["tx"]) and pd.notna(second["tx"]):
                row["ecart_tx"] = abs(primary["tx"] - second["tx"])
            if pd.notna(primary["tn"]) and pd.notna(second["tn"]):
                row["ecart_tn"] = abs(primary["tn"] - second["tn"])
        rows.append(row)
    return pd.DataFrame(rows, columns=_TXTN_COLS).sort_values("date").reset_index(drop=True)
