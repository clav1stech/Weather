# -*- coding: utf-8 -*-
"""Rollover HOT/COLD des trois parquets neige (mécanique générique
core/pipeline/hot_cold.py, fenêtre snow_config.HOT_RETENTION_DAYS).

Chaque base garde en HOT ses lignes récentes ; le plus ancien bascule vers le
parquet *_archive (COLD, append-only). Colonne temporelle propre à chaque
flux : le flux ensemble se découpe par `run_date` (un run bascule ENTIER, ses
lignes partagent leur run_date), les flux HD et observations par leur
horodatage d'acquisition/observation.

Sécurité (cf. hot_cold.py) : sauvegardes datées .bak avant écriture,
vérification stricte de non-perte AVANT toute écriture — au moindre doute le
script sort en erreur sans rien toucher. Idempotent (rien à basculer → aucun
fichier touché). `--dry-run` : rapport complet sans aucune écriture."""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "..")))

from apps.snow import snow_config as SC
from core.pipeline.hot_cold import format_report, rollover

FLUX = [
    ("ensemble",     SC.DB_ENS_PATH, SC.DB_ENS_COLD_PATH, "run_date"),
    ("maille fine",  SC.DB_HD_PATH,  SC.DB_HD_COLD_PATH,  "fetched_at"),
    ("observations", SC.DB_OBS_PATH, SC.DB_OBS_COLD_PATH, "valid_time"),
]


def main():
    dry_run = "--dry-run" in sys.argv
    mode = "DRY-RUN (aucune écriture)" if dry_run else "exécution"
    print(f"⏳ Rollover hot/cold neige — fenêtre {SC.HOT_RETENTION_DAYS} j, {mode}")
    for label, hot, cold, time_col in FLUX:
        report = rollover(hot, cold, time_col, SC.HOT_RETENTION_DAYS,
                          dry_run=dry_run)
        print("   " + format_report(report, label))
        for bak in report["backups"]:
            print(f"     sauvegarde : {bak}")
    print("✅ Rollover terminé.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du rollover neige : {exc}")
