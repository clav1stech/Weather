# -*- coding: utf-8 -*-
"""Rollover HOT/COLD de la base canicule — script d'ACTIVATION (à distinguer
de tools/archive_hot_cold_dry_run.py, qui ne travaille que sur une copie).

data/database_paris.parquet (HOT) → data/database_paris_archive.parquet
(COLD, config.DB_ARCHIVE_PATH), fenêtre config.HOT_RETENTION_DAYS (35 j),
découpe par `run_date` (un run bascule entier). Mécanique et garanties :
core/pipeline/hot_cold.py (sauvegardes datées .bak, vérification stricte de
non-perte AVANT toute écriture, écritures atomiques, idempotent).

GARDE-FOUS D'ACTIVATION (la bascule canicule n'est pas encore validée) :
  • par défaut le script est en DRY-RUN : rapport complet, AUCUNE écriture —
    l'exécution réelle exige le drapeau explicite `--execute` ;
  • `--execute` refuse de tourner hors de la branche `main` : la base de
    production n'évolue que sur main (crons CI) ; un rollover exécuté sur une
    branche divergente créerait un conflit binaire sur le parquet, insoluble
    sans risque de perte (invariant absolu d'intégrité des données).

Vit dans tools/ (et non à la racine) : ce n'est pas un flux de collecte — le
pipeline de collecte racine reste sans import core/ (invariant CLAUDE.md) ;
un job de maintenance appartient à la même famille que les harnais de tools/.

Procédure d'activation post-merge : docs/DESIGN_archivage_pipeline.md §7.
Usage : python tools/rollover_canicule.py [--execute] [--retention-days N]
"""

import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config as C  # noqa: E402
from core.pipeline.hot_cold import format_report, rollover  # noqa: E402


def _current_branch():
    """Branche git courante — GITHUB_REF_NAME en CI (checkout détaché), git
    en local. None si indéterminable (le garde-fou refuse alors d'exécuter)."""
    branch = os.environ.get("GITHUB_REF_NAME")
    if branch:
        return branch
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                             cwd=_ROOT, capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def main():
    execute = "--execute" in sys.argv
    retention = C.HOT_RETENTION_DAYS
    if "--retention-days" in sys.argv:
        retention = int(sys.argv[sys.argv.index("--retention-days") + 1])

    if execute:
        branch = _current_branch()
        if branch != "main":
            sys.exit(f"❌ --execute refusé : branche courante « {branch} » ≠ main. "
                     "La base de production n'évolue que sur main (crons CI) — "
                     "un rollover sur une branche divergente créerait un conflit "
                     "binaire insoluble sur le parquet. Dry-run seul autorisé ici.")

    mode = "EXÉCUTION RÉELLE" if execute else "DRY-RUN (aucune écriture)"
    print(f"⏳ Rollover hot/cold canicule — fenêtre {retention} j, {mode}")
    report = rollover(C.DB_PATH, C.DB_ARCHIVE_PATH, "run_date", retention,
                      dry_run=not execute)
    print("   " + format_report(report, "canicule"))
    for bak in report["backups"]:
        print(f"     sauvegarde : {bak}")
    print("✅ Terminé.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"❌ Échec du rollover canicule : {exc}")
