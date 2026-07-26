# -*- coding: utf-8 -*-
"""Wrapper canicule du harnais de non-régression des CALCULS, mutualisé dans
core/testing/non_regression.py — la commande documentée reste inchangée :

    python tools/check_non_regression.py capture   # fige la référence
    python tools/check_non_regression.py check     # compare à la référence

Les références restent dans tools/golden/ (non versionnées)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.testing.non_regression import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
