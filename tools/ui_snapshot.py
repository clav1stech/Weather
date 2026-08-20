# -*- coding: utf-8 -*-
"""Wrapper canicule du harnais de non-régression du RENDU (AppTest), mutualisé
dans core/testing/ui_snapshot.py — la commande documentée reste inchangée :

    python tools/ui_snapshot.py capture         # canicule (défaut)
    python tools/ui_snapshot.py check
    python tools/ui_snapshot.py capture neige   # dashboard neige
    python tools/ui_snapshot.py check neige

Les références restent dans tools/golden/ (non versionnées), une par app."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.testing.ui_snapshot import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
