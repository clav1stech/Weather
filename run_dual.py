# -*- coding: utf-8 -*-
"""Orchestre le pipeline Open-Meteo (Forecast.py) et, pour les créneaux 0Z/12Z
du jour déjà publiés côté Météociel, le scrape legacy (Forecast_legacy.py) +
le contrôle croisé entre les deux (cf. validate_cross_pipeline.py). Sert au
lancement manuel (bouton « Double run » du dashboard) — le workflow GitHub
Actions, lui, appelle désormais Forecast.py (toutes les 2h) et
Forecast_legacy.py/validate_cross_pipeline.py (aux créneaux 0Z/12Z)
séparément, sur des cadences propres à chaque source.

Météociel ne publie ses runs 0Z/12Z en intégralité qu'avec un net retard (0Z
complet vers midi heure de Paris, 12Z complet vers minuit). Plutôt que de se
caler sur une fenêtre autour de l'heure de cron (un poll manqué ou en retard
ratait alors le scrape), on compare le stock (fichiers `legacy/` du jour) à ce
qui devrait déjà être publié à l'heure actuelle, et on rattrape tout créneau
manquant — Forecast_legacy.py ne pouvant scraper que le run COURANT du site
Météociel, ce rattrapage reste borné au jour même (cf. `_run_legacy_scrape`)."""

import datetime as dt
import os
import subprocess
import sys

import Forecast
import validate_cross_pipeline as validate
import config as C

# Heure UTC (minute :15) à partir de laquelle Météociel a fini de publier le
# run 0Z/12Z correspondant en intégralité.
LEGACY_PUBLISH_HOUR_BY_SLOT = {"0Z": 10, "12Z": 22}


def _legacy_file_path(date_utc, slot):
    return os.path.join(C.LEGACY_FORECASTS_DIR, f"Forecast-{date_utc:%d%m%Y}-{slot}.xlsx")


def _missing_legacy_slots(now_utc):
    """Créneaux legacy déjà publiés côté Météociel (heure de publication passée)
    mais absents de `legacy/` — à rattraper, quel que soit l'écart au cron
    habituel. Forecast_legacy.py nomme le fichier d'après la date de run
    réellement affichée par Météociel (pas la date système) : selon la
    disparité de mise à jour entre modèles, ce peut être la veille au moment du
    poll (ex. juste après minuit UTC, le run 12Z pas encore roulé côté site) —
    on vérifie donc la présence du fichier à J comme à J-1 avant de conclure à
    une absence."""
    missing = []
    for slot, hour in LEGACY_PUBLISH_HOUR_BY_SLOT.items():
        publish_at = now_utc.replace(hour=hour, minute=15, second=0, microsecond=0)
        if now_utc < publish_at:
            continue
        today, yesterday = now_utc, now_utc - dt.timedelta(days=1)
        if not (os.path.exists(_legacy_file_path(today, slot))
                or os.path.exists(_legacy_file_path(yesterday, slot))):
            missing.append(slot)
    return missing


def _run_legacy_scrape(run_label):
    """Lance Forecast_legacy.py en sous-processus (son argv/sys.exit ne doivent
    pas interférer avec ce script). Renvoie True si le scrape a réussi."""
    try:
        subprocess.run([sys.executable, "Forecast_legacy.py", run_label],
                       check=True, cwd=C.BASE_DIR)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"⚠️  Scrape legacy {run_label} échoué (code {exc.returncode}) — "
              "contrôle croisé ignoré, pipeline Open-Meteo conservé.")
        return False


def main():
    now_utc = dt.datetime.now(dt.timezone.utc)

    print("=== Pipeline Open-Meteo ===")
    Forecast.main()

    missing = _missing_legacy_slots(now_utc)
    if not missing:
        print("\nℹ️  Stock legacy à jour : rien à rattraper côté Météociel pour l'instant.")
        return

    for run_label in missing:
        print(f"\n=== Pipeline legacy Météociel ({run_label}) ===")
        if not _run_legacy_scrape(run_label):
            continue

        print(f"\n=== Contrôle croisé ({run_label}) ===")
        validate.cross_check(run_label, now_utc)


if __name__ == "__main__":
    main()
