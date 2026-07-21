# -*- coding: utf-8 -*-
"""Seuils d'interprétation et calculs propres au domaine observations :
fraîcheur des observations, écart d'îlot de chaleur urbain (ICU) entre
stations, confrontation Tx/Tn prévus (flux HD) vs observés.

Les groupes de stations viennent du champ EXPLICITE `icu` de
config.OBS_STATIONS (« urbain » = tissu dense qui retient la chaleur la nuit,
« aere » = parc/bois qui se refroidit bien, « neutre » = exclue du calcul —
cas de Montsouris, qui reste en pratique proche de l'urbain la nuit malgré
son profil de parc) — jamais déduits du réseau, dont la coïncidence avec
l'exposition est propre à ce choix de 4 stations."""

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
# (page.py) : avec le flux 6 min (les 4 stations) qui rafraîchit toutes les
# ~15 min, une obs vieille de plus d'1 h signale un problème de mise à jour avant
# le seuil OBS_PERIMEE_H (3 h, pensé pour l'ère hourly-only) — sert seulement
# à la couleur de cette pastille, pas à cacher l'observation.
OBS_CARTE_ALERTE_H = 1.0

# Paliers de l'écart ICU nocturne moyen (°C) — interprétation, pas physique.
ICU_MARQUE_C = 1.0
ICU_FORT_C = 3.0

# Fenêtre d'affichage de la convergence de prévision (h glissantes depuis la
# dernière donnée disponible, pas depuis l'horloge — cf. obs_window).
VINTAGE_WINDOW_H = 48
# Tolérance d'appariement d'un lead : un vintage n'incarne « la prévision émise
# il y a h » que si son fetched_at tombe à ± cette marge de (valid_time − h).
# Demi-espacement des leads standards (6 h) → 3 h : jamais d'ambiguïté entre
# deux leads voisins, et une échéance trop récente pour avoir h de recul reste
# simplement SANS point à ce lead (jamais un vintage plus jeune déguisé en J-h).
VINTAGE_LEAD_TOL_H = 3.0


def vintage_comparison_series(vintages_df, obs6m_df=None,
                              leads_h=(0, 6, 12, 18, 24),
                              window_h=VINTAGE_WINDOW_H,
                              tol_h=VINTAGE_LEAD_TOL_H):
    """Séries de prévision « à J-h » pour Montsouris, à confronter à la courbe
    observée : pour chaque échéance (`valid_time`) et chaque lead `h`, la
    température prévue par le vintage émis ~h heures avant l'échéance.

    Fonction PURE (aucune lecture de fichier ; DataFrames passés en argument) —
    `vintages_df` et `obs6m_df` doivent partager le MÊME fuseau (la conversion
    UTC → Paris appartient à l'appelant, cf. invariant temporel du projet).

    Règles de sélection, échéance par échéance :
      • On ne considère QUE les vintages tels que `fetched_at ≤ valid_time` :
        une prévision ne peut pas être postérieure à l'échéance qu'elle prédit.
      • lead 0 = « dernière prévision disponible » : le `fetched_at` le plus
        RÉCENT (≤ valid_time) parmi les vintages `source="live"` s'il en existe
        un pour cette échéance. À DÉFAUT (flux live encore trop jeune pour avoir
        produit un vintage sur cette échéance — typiquement les ~24 premières
        heures du flux), repli sur le vintage `source="bootstrap"` : ce n'est
        pas une vraie prévision « émise en temps réel » (le comblement initial
        interroge tout le passé en un seul instant), mais la meilleure estimation
        connue pour cette heure en attendant qu'un vrai vintage live existe — un
        repère temporaire qui s'efface de lui-même dès qu'un live apparaît.
      • lead h > 0 : le vintage dont `fetched_at` est le plus proche de
        (valid_time − h), à condition d'être à ± `tol_h` de cette cible —
        sinon l'échéance est ABSENTE de cette série (jamais d'interpolation, et
        le bootstrap n'est JAMAIS repli ici : un faux « J-6h » serait trompeur
        sur la capacité prédictive du modèle, contrairement au lead 0 qui ne
        prétend qu'à « la meilleure estimation connue »).

    Fenêtre : `window_h` heures glissantes finissant à la dernière donnée
    disponible (dernière obs 6 min si fournie — l'ancre « présent » naturelle,
    l'observé n'allant pas dans le futur —, sinon dernier `valid_time` des
    vintages). DataFrame long [valid_time, lead_h, temperature] (une trace par
    lead côté chart) ; vide si rien d'exploitable."""
    cols = ["valid_time", "lead_h", "temperature"]
    if vintages_df is None or vintages_df.empty:
        return pd.DataFrame(columns=cols)
    v = vintages_df[["valid_time", "fetched_at", "temperature", "source"]].copy()
    v["valid_time"] = pd.to_datetime(v["valid_time"])
    v["fetched_at"] = pd.to_datetime(v["fetched_at"])

    if obs6m_df is not None and not obs6m_df.empty:
        anchor = pd.to_datetime(obs6m_df["valid_time"]).max()
    else:
        anchor = v["valid_time"].max()
    if pd.isna(anchor):
        return pd.DataFrame(columns=cols)
    start = anchor - pd.Timedelta(hours=window_h)
    v = v[(v["valid_time"] > start) & (v["valid_time"] <= anchor)]
    if v.empty:
        return pd.DataFrame(columns=cols)

    tol = pd.Timedelta(hours=tol_h)
    rows = []
    for vt, grp in v.groupby("valid_time"):
        # Pool "live" : jamais de prévision post-échéance (fetched_at ≤ vt) —
        # seul pool utilisé pour les leads > 0. Le bootstrap est traité à part :
        # comblement initial en un seul instantané, son fetched_at (l'instant du
        # comblement) est quasi toujours POSTÉRIEUR aux valid_time passés — il
        # échouerait donc toujours ce filtre alors qu'il porte la seule donnée
        # disponible avant que le flux live ait tourné assez longtemps.
        live = grp[(grp["source"] != "bootstrap") & (grp["fetched_at"] <= vt)]
        boot = grp[grp["source"] == "bootstrap"]
        if live.empty and boot.empty:
            continue
        for h in leads_h:
            if h == 0:
                if not live.empty:
                    pick = live.loc[live["fetched_at"].idxmax()]
                elif not boot.empty:
                    pick = boot.loc[boot["fetched_at"].idxmax()]
                else:
                    continue
            else:
                if live.empty:                       # bootstrap jamais repli ici
                    continue
                target = vt - pd.Timedelta(hours=h)
                dist = (live["fetched_at"] - target).abs()
                if dist.min() > tol:                 # pas de recul de ~h → absent
                    continue
                pick = live.loc[dist.idxmin()]
            temp = pick["temperature"]
            if pd.isna(temp):
                continue
            rows.append({"valid_time": vt, "lead_h": h, "temperature": float(temp)})
    return pd.DataFrame(rows, columns=cols)


# Lissage AFFICHAGE SEUL des séries de PRÉVISION (jamais l'observé) : la nuit,
# la couche limite stable fait osciller la température à 2 m du modèle d'un pas
# de 15 min à l'autre (intermittence de mélange/découplage — un vrai signal du
# modèle, pas du bruit de collecte) qui parasite la lecture de la convergence.
# Moyenne glissante centrée ~1 h (5 pas de 15 min) ; la donnée stockée reste
# brute. Partagé entre le tracé (charts) et le tableau d'écarts : les deux
# doivent montrer exactement la même courbe.
LISSAGE_PREVISION_PTS = 5


def lisser_prevision(series):
    return series.rolling(window=LISSAGE_PREVISION_PTS, center=True,
                          min_periods=1).mean()


# Tolérance d'appariement (minutes) entre la grille de prévision (15 min) et
# la grille observée (6 min) : l'écart maximal entre un instant observé et le
# point de prévision le plus proche est de 7,5 min — au-delà de 10 min, la
# prévision n'a réellement pas de point sur cet instant (cellule vide, jamais
# d'interpolation).
ECART_APPARIEMENT_MIN = 10.0


def tableau_ecarts_convergence(series, obs_df, tol_min=ECART_APPARIEMENT_MIN):
    """Écarts prévision − observé (°C, positif = le modèle voyait trop chaud)
    aux trois instants de référence de la fenêtre affichée : dernier point
    OBSERVÉ (« actuel »), instant du MIN observé, instant du MAX observé —
    le min/max de référence est celui de la température observée, pas de la
    prévision. Une ligne par recul (lead_h) présent dans `series` (sortie de
    vintage_comparison_series, déjà restreinte à la fenêtre du graphique).

    La prévision est comparée LISSÉE (lisser_prevision), comme elle est tracée
    — le tableau doit chiffrer ce que l'œil voit sur les courbes, pas une
    donnée brute que le graphique ne montre pas ; l'observé reste brut, comme
    au tracé. Appariement au point de prévision le plus proche à ± tol_min,
    sinon NaN (l'appelant affiche « — », jamais de valeur inventée).

    DataFrame [lead_h, ecart_actuel, ecart_min, ecart_max] ; vide si l'un des
    deux flux manque (dégradation silencieuse, comme le reste du domaine)."""
    cols = ["lead_h", "ecart_actuel", "ecart_min", "ecart_max"]
    if (series is None or series.empty
            or obs_df is None or obs_df.empty):
        return pd.DataFrame(columns=cols)
    obs = obs_df.dropna(subset=["t"]).sort_values("valid_time")
    if obs.empty:
        return pd.DataFrame(columns=cols)
    reperes = [("ecart_actuel", obs.iloc[-1]),
               ("ecart_min", obs.loc[obs["t"].idxmin()]),
               ("ecart_max", obs.loc[obs["t"].idxmax()])]
    tol = pd.Timedelta(minutes=tol_min)
    rows = []
    for h in sorted(series["lead_h"].unique()):
        g = (series[(series["lead_h"] == h) & series["temperature"].notna()]
             .sort_values("valid_time").reset_index(drop=True))
        row = {"lead_h": int(h)}
        prev = lisser_prevision(g["temperature"])
        for cle, ref in reperes:
            if g.empty:
                row[cle] = float("nan")
                continue
            dist = (g["valid_time"] - ref["valid_time"]).abs()
            i = dist.idxmin()
            row[cle] = (float(prev.iloc[i] - ref["t"])
                        if dist.iloc[i] <= tol else float("nan"))
        rows.append(row)
    return pd.DataFrame(rows, columns=cols)


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
