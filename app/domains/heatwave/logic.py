# -*- coding: utf-8 -*-
"""Logique métier du domaine canicule : paliers de probabilité, indice de
tendance récente, libellés vulgarisés. Les seuils physiques (SEUIL_CHALEUR_850,
SEUIL_CANICULE_850) et KPI (KPI_RISK_*) restent dans config.py — ici ne vivent
que les paliers d'INTERPRÉTATION propres à l'affichage du domaine."""

import pandas as pd

import config as C
from app.stats.climato import clim_z500_normal
from app.stats.ensemble import var_median

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


# ── Contexte synoptique Z500 (appui du signal T850, jamais un critère de risque) ──
# L'anomalie du géopotentiel 500 hPa vs sa normale saisonnière (cosinus,
# config.CLIM_Z500_*) trahit le régime d'altitude : anomalie nettement positive
# = dorsale/blocage anticyclonique qui installe et entretient la chaleur ;
# nettement négative = talweg, régime perturbé peu propice à une chaleur durable.
# Seuils en mètres géopotentiels, ordres de grandeur synoptiques estivaux (une
# dorsale marquée dépasse +60 m d'anomalie, un talweg net passe sous −40 m).
# Le signal reste QUALITATIF : simple, explicable, à base de seuils + persistance
# — jamais de valeur brute côté grand public, jamais de concurrence avec T850.
Z500_FENETRE_J = 7           # jours à venir considérés pour qualifier le régime
Z500_ANOM_DORSALE_M = 60.0   # anomalie journalière ≥ → jour « dorsale »
Z500_ANOM_TALWEG_M = -40.0   # anomalie journalière ≤ → jour « talweg »
Z500_JOURS_PERSISTANCE = 2   # jours dorsale/talweg suffisant à qualifier la fenêtre


def anomalie_z500_journaliere(sub):
    """Anomalie journalière (m) de la médiane d'ensemble Z500 vs la normale
    saisonnière : DataFrame [date, anom]. Médiane journalière = médiane des
    médianes horaires (Z500 varie peu dans la journée, inutile de repooler les
    membres). None si z500 est absent ou vide dans `sub` (base antérieure à la
    collecte, runs importés du legacy — absence normale, pas une anomalie)."""
    med = var_median(sub, "z500")
    if med is None or med.empty:
        return None
    med = med.copy()
    med["date"] = pd.to_datetime(med["valid_time"]).dt.normalize()
    daily = med.groupby("date")["median"].median().reset_index()
    daily["anom"] = daily["median"] - clim_z500_normal(daily["date"])
    return daily[["date", "anom"]]


def signal_synoptique(sub, today, fenetre_j=Z500_FENETRE_J):
    """Signal qualitatif grand public du régime d'altitude sur les prochains
    jours : (icône, libellé court, phrase d'appui) ou None si Z500 inexploitable
    (rien à afficher — le message T850 reste seul, strictement inchangé).

    Deux portes d'entrée symétriques par régime : anomalie MOYENNE de la fenêtre
    franchissant le seuil (régime installé), OU nombre de jours au-delà du seuil
    atteignant la persistance (régime qui s'installe en cours de fenêtre)."""
    daily = anomalie_z500_journaliere(sub)
    if daily is None:
        return None
    win = daily[(daily["date"] >= today) &
                (daily["date"] < today + pd.Timedelta(days=fenetre_j))]
    if win.empty:
        return None
    anom_moy = float(win["anom"].mean())
    n_dorsale = int((win["anom"] >= Z500_ANOM_DORSALE_M).sum())
    n_talweg = int((win["anom"] <= Z500_ANOM_TALWEG_M).sum())
    if anom_moy >= Z500_ANOM_DORSALE_M or n_dorsale >= Z500_JOURS_PERSISTANCE:
        return ("🔆", "favorable au maintien de la chaleur",
                "En altitude, les modèles voient une **dorsale anticyclonique** — une "
                "configuration qui installe la chaleur et la fait durer. Si un épisode "
                "chaud se dessine ci-dessous, ce contexte le rend plus solide.")
    if anom_moy <= Z500_ANOM_TALWEG_M or n_talweg >= Z500_JOURS_PERSISTANCE:
        return ("🌬️", "peu propice à une chaleur durable",
                "La circulation d'altitude reste de type **perturbé** : même si des "
                "journées chaudes apparaissent ci-dessous, ce contexte ne favorise pas "
                "leur installation dans la durée.")
    return ("⚖️", "neutre",
            "Pas de configuration d'altitude marquée : ni blocage qui entretiendrait "
            "la chaleur, ni régime perturbé qui la balaierait — le signal ci-dessous "
            "se suffit à lui-même.")


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


# ── Incertitude du Tx/Tn haute résolution (appui d'affichage, pas un critère) ──
# La valeur affichée est celle du modèle prioritaire (Météo-France tant qu'il
# couvre le jour). Sa fiabilité se juge sur DEUX régimes distincts :
#   • les deux modèles HD présents (J → J+3) : l'écart |MF − ICON| mesure leur
#     désaccord — deux modèles concordants renforcent la valeur, une forte
#     divergence la rend incertaine ;
#   • un seul modèle (J+4 → J+6 : MF s'arrête, ICON seul) : valeur indicative
#     par construction, aucun recoupement possible.
# Seuils en °C sur le plus grand des deux écarts (Tx ou Tn) : ordres de grandeur
# d'une prévision de température au sol à quelques jours.
T2M_ECART_BON_C = 1.5    # écart ≤ → modèles d'accord, valeur solide
T2M_ECART_FORT_C = 3.0   # écart ≥ → forte divergence, valeur indicative


def incertitude_txtn(ecart_tx, ecart_tn, solo, model, model_alt):
    """(glyphe de case, libellé court, phrase de survol) qualifiant la fiabilité
    d'un Tx/Tn HD. Glyphe non vide (« ≈ », signe « approximatif ») UNIQUEMENT
    dans les cas peu fiables (source unique ou forte divergence) — l'accord des
    modèles ne charge pas la case, il se lit au survol. Jamais de couleur ici :
    la teinte des cases reste réservée au risque T850."""
    if solo:
        if model != C.T2M_LABELS[0]:
            phrase = (f"au-delà de ~4 jours, {C.T2M_LABELS[0]} ne prévoit plus, seul "
                      f"{model} couvre cette échéance (valeur indicative, sans recoupement)")
        else:
            phrase = (f"seul {model} est disponible ce jour "
                      "(valeur indicative, sans recoupement)")
        return "≈", "source unique", phrase
    ecarts = [e for e in (ecart_tx, ecart_tn) if pd.notna(e)]
    ecart = max(ecarts) if ecarts else 0.0
    if ecart >= T2M_ECART_FORT_C:
        return ("≈", "forte divergence",
                f"{model} et {model_alt} s'écartent nettement (jusqu'à "
                f"{ecart:.0f} °C), prévision encore incertaine")
    if ecart >= T2M_ECART_BON_C:
        return ("", "léger désaccord",
                f"{model} et {model_alt} diffèrent un peu (jusqu'à {ecart:.0f} °C)")
    return ("", "modèles d'accord",
            f"{model} et {model_alt} concordent (écart ≤ {ecart:.0f} °C)")
