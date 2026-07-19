# -*- coding: utf-8 -*-
"""Analyse HOT/COLD du parquet canicule — SUR COPIE UNIQUEMENT, aucune
activation en production.

Étape préalable au chantier d'archivage du canicule (design
docs/DESIGN_archivage_pipeline.md, mécanique core/pipeline/hot_cold.py déjà
active côté neige) : ce script copie `data/database_paris.parquet` dans un
dossier de travail temporaire, y joue le rollover (dry-run PUIS exécution
réelle sur la copie, vérifications bloquantes comprises) et imprime le bilan —
lignes basculées, tailles hot/cold résultantes, gain mémoire attendu.

La base de production n'est JAMAIS touchée : lecture seule + copie. La bascule
réelle du canicule (chemins config, load_db_full côté dashboard, job CI) reste
un chantier séparé, à lancer uniquement après validation de l'utilisateur.

Usage : python tools/archive_hot_cold_dry_run.py [--retention-days 35]
"""

import os
import shutil
import sys
import tempfile

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config as C  # noqa: E402
from core.pipeline.hot_cold import format_report, rollover  # noqa: E402


def _mb(path):
    return os.path.getsize(path) / 1e6 if os.path.exists(path) else 0.0


def main():
    retention = 35
    if "--retention-days" in sys.argv:
        retention = int(sys.argv[sys.argv.index("--retention-days") + 1])

    if not os.path.exists(C.DB_PATH):
        sys.exit(f"❌ Base introuvable : {C.DB_PATH}")
    print(f"📦 Base de production (lecture seule) : {C.DB_PATH} "
          f"({_mb(C.DB_PATH):.1f} Mo)")

    with tempfile.TemporaryDirectory(prefix="hotcold_canicule_") as tmp:
        hot = os.path.join(tmp, "database_paris.parquet")
        cold = os.path.join(tmp, "database_paris_archive.parquet")
        shutil.copy2(C.DB_PATH, hot)
        print(f"   copie de travail : {hot}")

        # 1) Dry-run : rapport sans aucune écriture (même sur la copie).
        report = rollover(hot, cold, "run_date", retention, dry_run=True)
        print(f"\n— Dry-run (fenêtre {retention} j) —")
        print("   " + format_report(report, "canicule"))
        if report["moved"] == 0:
            print("   Rien à basculer : base plus jeune que la fenêtre.")
            return

        # 2) Exécution réelle SUR LA COPIE : passe les vérifications bloquantes
        #    (union intacte, cold append pur) et matérialise les deux fichiers.
        report = rollover(hot, cold, "run_date", retention, dry_run=False)
        print("\n— Exécution sur la copie —")
        print("   " + format_report(report, "canicule"))

        # 3) Bilan chiffré : c'est la borne mémoire que verrait load_db().
        df_hot, df_cold = pd.read_parquet(hot), pd.read_parquet(cold)
        print(f"\n— Bilan (copie) —")
        print(f"   HOT  : {len(df_hot):>9,} lignes · {_mb(hot):6.1f} Mo · runs "
              f"{df_hot['run_date'].min():%Y-%m-%d} → {df_hot['run_date'].max():%Y-%m-%d}")
        print(f"   COLD : {len(df_cold):>9,} lignes · {_mb(cold):6.1f} Mo · runs "
              f"{df_cold['run_date'].min():%Y-%m-%d} → {df_cold['run_date'].max():%Y-%m-%d}")
        part = len(df_hot) / (len(df_hot) + len(df_cold)) * 100
        print(f"   → les pages interactives ne chargeraient plus que {part:.0f} % "
              "des lignes actuelles (mémoire bornée par la fenêtre).")
    print("\n✅ Analyse terminée — base de production intacte, copie supprimée.")


if __name__ == "__main__":
    main()
