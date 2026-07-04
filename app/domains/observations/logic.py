# -*- coding: utf-8 -*-
"""Seuils d'interprétation et calculs propres au domaine observations :
fraîcheur des observations, écart d'îlot de chaleur urbain (ICU) entre
stations, confrontation Tx/Tn prévus (flux HD) vs observés.

Les groupes de stations viennent du champ EXPLICITE `icu` de
config.OBS_STATIONS (« urbain » = tissu dense qui retient la chaleur la nuit,
« aere » = parc/bois qui se refroidit bien) — jamais déduits du réseau, dont
la coïncidence avec l'exposition est propre à ce choix de 4 stations."""

import pandas as pd

import config as C

# Stations comparées pour l'écart ICU : l'écart se lit URBAIN − AÉRÉ (positif
# = l'urbain dense retient la chaleur). Listes de noms, pilotées par config.
STATIONS_URBAINES = [s["nom"] for s in C.OBS_STATIONS if s["icu"] == "urbain"]
STATIONS_AEREES = [s["nom"] for s in C.OBS_STATIONS if s["icu"] == "aere"]

# Fenêtre nocturne (heures LOCALES Paris) où l'ICU s'exprime le plus : le jour,
# une seule mesure suffit presque (Montsouris) ; la nuit, l'urbain dense se
# refroidit bien moins vite que les zones végétalisées. Approximation civile
# fixe — pas d'éphéméride, l'ordre de grandeur suffit pour un signal qualitatif.
NUIT_DEBUT_H = 22
NUIT_FIN_H = 6

# Observation « périmée » : plus vieille que ce délai vs maintenant (le cron
# tourne toutes les heures ; à 3 h de retard, l'API ou le workflow a un souci
# et l'affichage doit le dire plutôt que de faire passer l'obs pour actuelle).
OBS_PERIMEE_H = 3.0

# Seuil, plus resserré, de l'horodatage en rouge sur les cartes « temps réel »
# (page.py) : avec le flux 6 min (RADOME) qui rafraîchit toutes les ~15 min,
# une obs vieille de plus d'1 h signale un problème de mise à jour bien avant
# le seuil OBS_PERIMEE_H (3 h, pensé pour l'ère hourly-only) — sert seulement
# à la couleur de cette pastille, pas à cacher l'observation.
OBS_CARTE_ALERTE_H = 1.0

# Paliers de l'écart ICU nocturne moyen (°C) — interprétation, pas physique.
ICU_MARQUE_C = 1.0
ICU_FORT_C = 3.0

# Un jour civil d'observations n'est comparable aux Tx/Tn prévus que s'il est
# quasi complet : Tn/Tx d'une journée trouée (collecte démarrée en cours de
# journée, panne prolongée) seraient faussés. 24 obs attendues par jour.
JOUR_COMPLET_MIN_H = 20


def obs_est_perimee(valid_time_local, now_local, seuil_h=OBS_PERIMEE_H):
    """L'observation est-elle trop ancienne pour être présentée comme
    « en direct » ? (les deux instants en heure de Paris, tz-naïfs). `seuil_h`
    paramétrable pour réutiliser cette même logique avec un seuil différent
    (cf. OBS_CARTE_ALERTE_H, plus resserré, pour la couleur des cartes)."""
    if pd.isna(valid_time_local):
        return True
    return (now_local - valid_time_local) > pd.Timedelta(hours=seuil_h)


def _label_icu(ecart_c):
    """Palier qualitatif d'un écart ICU (°C)."""
    if ecart_c >= ICU_FORT_C:
        return "🔴 fort"
    if ecart_c >= ICU_MARQUE_C:
        return "🟠 marqué"
    if ecart_c > -ICU_MARQUE_C:
        return "⚪ faible"
    return "🔵 inversé"


def ecart_icu_series(dfw):
    """Série temporelle de l'écart ICU : moyenne des températures des stations
    urbaines denses − moyenne des stations aérées, aux heures où les DEUX
    groupes ont au moins une valeur (tolérant à une station manquante dans un
    groupe : la moyenne se fait sur ce qui existe). DataFrame [valid_time,
    ecart, nuit] ; vide si aucun instant comparable."""
    if dfw.empty:
        return pd.DataFrame(columns=["valid_time", "ecart", "nuit"])
    pivot = dfw.pivot_table(index="valid_time", columns="station_nom",
                            values="t", aggfunc="mean")
    urbain = pivot.reindex(columns=STATIONS_URBAINES).mean(axis=1, skipna=True)
    aere = pivot.reindex(columns=STATIONS_AEREES).mean(axis=1, skipna=True)
    out = pd.DataFrame({"valid_time": pivot.index,
                        "ecart": (urbain - aere).to_numpy()}).dropna(subset=["ecart"])
    heures = pd.to_datetime(out["valid_time"]).dt.hour
    out["nuit"] = (heures >= NUIT_DEBUT_H) | (heures < NUIT_FIN_H)
    return out.reset_index(drop=True)


def verdict_icu_nocturne(ecarts):
    """(écart moyen °C, label, phrase) sur les heures NOCTURNES de la fenêtre,
    ou None si aucune heure de nuit comparable (fenêtre trop courte, données
    manquantes) — l'appelant n'affiche alors rien."""
    if ecarts.empty:
        return None
    nuit = ecarts[ecarts["nuit"]]
    if nuit.empty:
        return None
    moy = float(nuit["ecart"].mean())
    label = _label_icu(moy)
    if moy >= ICU_MARQUE_C:
        phrase = ("les quartiers denses gardent la chaleur la nuit, "
                  "les zones végétalisées respirent mieux.")
    elif moy > -ICU_MARQUE_C:
        phrase = ("peu de contraste cette nuit entre quartiers denses "
                  "et zones végétalisées (vent, nuages ou masse d'air homogène).")
    else:
        phrase = ("situation atypique : les stations aérées sont restées plus "
                  "chaudes que l'urbain dense (advection locale, microclimat).")
    return moy, label, phrase


def comparaison_prevu_observe(txtn_prevu, txtn_obs, today):
    """Confrontation Tx/Tn PRÉVUS (flux HD — une seule prévision pour tout
    Paris, cf. config.LATITUDE/LONGITUDE) vs OBSERVÉS par station, sur les
    jours révolus (date < today) dont l'observation est quasi complète
    (n_heures ≥ JOUR_COMPLET_MIN_H). Le flux HD n'étant PAS spécifique à
    chaque station, l'écart par station mesure justement l'ICU local vs la
    prévision « générale » — c'est le message de ce bloc, pas un défaut.

    DataFrame [date, station_nom, tx_obs, tn_obs, tx_prevu, tn_prevu,
    ecart_tx, ecart_tn] (écart = observé − prévu) ; vide tant que
    l'historique ne recoupe pas encore les deux flux."""
    cols = ["date", "station_nom", "tx_obs", "tn_obs", "tx_prevu", "tn_prevu",
            "ecart_tx", "ecart_tn"]
    if txtn_prevu.empty or txtn_obs.empty:
        return pd.DataFrame(columns=cols)
    obs = txtn_obs[(txtn_obs["date"] < today)
                   & (txtn_obs["n_heures"] >= JOUR_COMPLET_MIN_H)]
    if obs.empty:
        return pd.DataFrame(columns=cols)
    prevu = txtn_prevu.rename(columns={"tx": "tx_prevu", "tn": "tn_prevu"})
    merged = obs.rename(columns={"tx": "tx_obs", "tn": "tn_obs"}) \
                .merge(prevu[["date", "tx_prevu", "tn_prevu"]], on="date", how="inner")
    if merged.empty:
        return pd.DataFrame(columns=cols)
    merged["ecart_tx"] = merged["tx_obs"] - merged["tx_prevu"]
    merged["ecart_tn"] = merged["tn_obs"] - merged["tn_prevu"]
    return merged[cols].sort_values(["date", "station_nom"]).reset_index(drop=True)
