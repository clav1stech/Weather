# -*- coding: utf-8 -*-
"""Orchestre le pipeline Open-Meteo (Forecast.py) et, aux runs 0Z/12Z, le scrape
legacy Météociel (Forecast_legacy.py) + le contrôle croisé entre les deux
(cf. validate_cross_pipeline.py). Point d'entrée du workflow GitHub Actions.

Météociel ne publie ses runs 0Z/12Z en intégralité qu'avec un net retard (0Z
complet vers midi heure de Paris, 12Z complet vers minuit) : le scrape legacy
n'est donc déclenché qu'aux polls 10:15 UTC (0Z) et 22:15 UTC (12Z) du cron —
pas à 04:15/16:15, où Open-Meteo a déjà son propre 0Z/12Z mais Météociel pas
encore. Le contrôle croisé compare alors le scrape frais à la donnée Open-Meteo
déjà archivée ~6h plus tôt sous le même run_date.
"""

import datetime as dt
import subprocess
import sys

import Forecast
import validate_cross_pipeline as validate
import config as C

# Heure de cron (UTC) où Météociel a fini de publier le run 0Z/12Z correspondant.
LEGACY_SLOT_BY_CRON_HOUR = {10: "0Z", 22: "12Z"}
CRON_HOURS = (4, 10, 16, 22)


def _nearest_cron_hour(now_utc):
    return min(CRON_HOURS, key=lambda h: min(abs(now_utc.hour - h), 24 - abs(now_utc.hour - h)))


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

    run_label = LEGACY_SLOT_BY_CRON_HOUR.get(_nearest_cron_hour(now_utc))
    if run_label is None:
        print("\nℹ️  Hors créneau Météociel (0Z publié ~10h UTC, 12Z ~22h UTC) — "
              "pas de scrape legacy à ce poll.")
        return

    print(f"\n=== Pipeline legacy Météociel ({run_label}) ===")
    if not _run_legacy_scrape(run_label):
        return

    print(f"\n=== Contrôle croisé ({run_label}) ===")
    validate.cross_check(run_label, now_utc)


if __name__ == "__main__":
    main()
