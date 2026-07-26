# -*- coding: utf-8 -*-
"""Pipeline neige — flux ANNEXE observations Météo-France (Alpes du Nord).

Liaison snow_config → mécanique générique core/pipeline/observations.py :
un appel « Paquet Observation » DPPaquetObs/v2 sur le département 74 renvoie
les observations horaires de TOUTES les stations de Haute-Savoie sur une
fenêtre glissante de plusieurs jours ; on ne persiste que les stations de
snow_config.OBS_STATIONS → parquet séparé apps/snow/data/db_obs_alpes.parquet
(append-only, dédup (station_id, valid_time)). Ce script ne touche à AUCUN
autre fichier de données.

Clé API : réutilise api_key() du fetch_observations.py racine (canicule) —
la gestion de la clé reste MONO-SOURCE (env METEOFRANCE_API_KEY, .env
gitignoré en repli, jamais en dur ni loguée), même précédent que le flux
6 min du canicule.

Mode `--list-stations` : affiche les stations réellement présentes dans le
paquet du département (id, coordonnées, altitude, nb d'observations) SANS rien
écrire — sert à contrôler les identifiants déclarés en config contre le
terrain (les stations d'OBS_STATIONS absentes du paquet y sont signalées).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "..")))

from apps.snow import snow_config as SC
from core.pipeline import observations as OBS
from fetch_observations import api_key  # racine : gestion de clé mono-source


def list_stations(payload):
    """Inventaire du paquet : une ligne par station présente, avec repérage
    de celles déclarées en config — contrôle des identifiants, aucune écriture."""
    seen = {}
    for obs in payload:
        sid = str(obs.get("geo_id_insee", ""))
        if not sid:
            continue
        entry = seen.setdefault(sid, {"n": 0, "lat": obs.get("lat"),
                                      "lon": obs.get("lon")})
        entry["n"] += 1
    print(f"📋 {len(seen)} station(s) dans le paquet du département "
          f"{SC.OBS_DEPARTEMENT} :")
    for sid in sorted(seen):
        e = seen[sid]
        nom = SC.OBS_NOM_BY_ID.get(sid)
        marque = f"  ← config : {nom}" if nom else ""
        print(f"   {sid}  lat={e['lat']} lon={e['lon']}  {e['n']} obs{marque}")
    absentes = [s["nom"] for s in SC.OBS_STATIONS if s["id"] not in seen]
    if absentes:
        print(f"   ⚠️  Déclarée(s) en config mais ABSENTE(s) du paquet : "
              f"{', '.join(absentes)} — identifiant à revérifier.")


def main():
    key = api_key()
    print(f"⏳ Requête Météo-France DPPaquetObs (paquet horaire, département "
          f"{SC.OBS_DEPARTEMENT})…")
    payload = OBS.fetch_paquet(SC.OBS_API_BASE, key, SC.OBS_DEPARTEMENT,
                               SC.HTTP_TIMEOUT,
                               context="DPPaquetObs (Alpes du Nord)")
    if "--list-stations" in sys.argv:
        list_stations(payload)
        return

    fresh = OBS.parse_observations(payload, SC.OBS_STATION_BY_ID,
                                   SC.OBS_VARIABLES, SC.OBS_SCHEMA,
                                   SC.OBS_VAR_COLS)
    if fresh.empty:
        print("ℹ️  Aucune observation exploitable pour les stations suivies — "
              "base laissée telle quelle.")
        return
    absentes = [s["nom"] for s in SC.OBS_STATIONS
                if s["nom"] not in set(fresh["station_nom"])]
    if absentes:
        print(f"   ⚠️  Station(s) absente(s) du paquet à ce poll (les autres sont "
              f"persistées, le paquet suivant comblera) : {', '.join(absentes)}")

    existing = OBS.load_existing(SC.DB_OBS_PATH, SC.OBS_SCHEMA)
    combined, n_new = OBS.persist(fresh, SC.DB_OBS_PATH, SC.OBS_SCHEMA,
                                  existing=existing)
    if n_new == 0:
        print("ℹ️  Toutes les observations du paquet déjà en base — rien à écrire.")
        return
    # Détail par station des points réellement AJOUTÉS : dans les logs CI, un
    # nombre > 1 après un cron manqué est la preuve visible du rattrapage.
    added = combined.merge(existing[["station_id", "valid_time"]],
                           on=["station_id", "valid_time"], how="left",
                           indicator=True)
    added = added[added["_merge"] == "left_only"]
    for nom, g in added.groupby("station_nom"):
        print(f"   {nom} : +{len(g)} point(s), de {g['valid_time'].min():%d %b %H:%M} "
              f"à {g['valid_time'].max():%d %b %H:%M} UTC")
    print(f"✅ Base observations Alpes mise à jour : +{n_new} ligne(s) · "
          f"{len(combined):,} au total")
    print(f"   → {SC.DB_OBS_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du pipeline observations Alpes : {exc}")
