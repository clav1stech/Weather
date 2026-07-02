# -*- coding: utf-8 -*-
"""Logique métier du domaine canicule : paliers de probabilité, indice de
tendance récente, libellés vulgarisés. Les seuils physiques (SEUIL_CHALEUR_850,
SEUIL_CANICULE_850) et KPI (KPI_RISK_*) restent dans config.py — ici ne vivent
que les paliers d'INTERPRÉTATION propres à l'affichage du domaine."""

import pandas as pd

# Paliers de probabilité de canicule PARTAGÉS entre le calendrier du risque
# (_canicule_label) et le KPI « Statut canicule » (statut gradué) — une seule
# échelle pour toute la page, jamais deux jugements différents du même chiffre.
PROB_CANICULE_QUASI = 0.50
PROB_RISQUE_MARQUE = 0.25
PROB_RISQUE_MODERE = 0.10


def _canicule_label(prob):
    if prob >= PROB_CANICULE_QUASI:
        return "🔴 Canicule quasi-certaine"
    if prob >= PROB_RISQUE_MARQUE:
        return "🟠 Risque marqué"
    if prob >= PROB_RISQUE_MODERE:
        return "🟡 Risque modéré"
    return "🟢 Pas de signal de canicule"


# Indice de tendance récente (grand public) : fenêtre de runs considérée et
# seuils (°C) de qualification des révisions. |Δ| < STABLE = stable ;
# ≥ STRONG = révision nette. La fenêtre ~66 h ≈ les runs des 3 derniers jours
# (0Z/12Z + cycles récents), assez large pour lisser un run isolé.
TREND_WINDOW_H = 66
TREND_STABLE_C = 0.5
TREND_STRONG_C = 1.5


def tendance_recente(trend, window_h=TREND_WINDOW_H):
    """Indice de variation PAR JOURNÉE cible : écart entre ce que prévoit le
    dernier run et ce que prévoyait le plus ancien run de la fenêtre (médiane
    journalière du super-ensemble complété, cf. trend_daily_medians). Positif =
    les calculs récents ont réchauffé la prévision pour ce jour. Une journée
    n'est notée que si ≥ 2 runs de la fenêtre la couvrent."""
    if trend.empty:
        return pd.DataFrame(columns=["target", "delta"])
    latest = trend["run_dt"].max()
    win = trend[trend["run_dt"] >= latest - pd.Timedelta(hours=window_h)]
    rows = []
    for target, grp in win.groupby("target"):
        grp = grp.sort_values("run_dt")
        if grp["run_dt"].nunique() < 2:
            continue
        rows.append({"target": pd.Timestamp(target),
                     "delta": float(grp.iloc[-1]["median"] - grp.iloc[0]["median"])})
    return pd.DataFrame(rows)


def _tendance_label(delta):
    """(flèche, libellé vulgarisé) d'une révision — jamais de valeur brute."""
    if delta >= TREND_STRONG_C:
        return "⬆", "nette révision à la hausse"
    if delta >= TREND_STABLE_C:
        return "↗", "légère révision à la hausse"
    if delta <= -TREND_STRONG_C:
        return "⬇", "nette révision à la baisse"
    if delta <= -TREND_STABLE_C:
        return "↘", "légère révision à la baisse"
    return "＝", "prévision stable"


# Seuils (°C) sur le spread journalier P90−P10 pour le libellé grand public de
# confiance : scénarios groupés / partagés / très dispersés. Ordres de grandeur
# T850 : < 3 °C = bon accord, ≥ 6 °C = fourchette trop large pour trancher.
CONF_SPREAD_BON_C = 3.0
CONF_SPREAD_FAIBLE_C = 6.0


def _confiance_label(spread):
    if spread < CONF_SPREAD_BON_C:
        return "🟢 bonne (scénarios groupés)", "#2ECC71"
    if spread < CONF_SPREAD_FAIBLE_C:
        return "🟡 moyenne (scénarios partagés)", "#F1C40F"
    return "🟠 faible (scénarios très dispersés)", "#E67E22"
