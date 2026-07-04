# -*- coding: utf-8 -*-
"""Cooldown générique à base de fichier — protège une action publique
(bouton exposé à des visiteurs anonymes) contre les clics répétés, AVANT tout
appel réseau. Horodatage sur disque plutôt que `st.session_state` seul : il
est partagé par tous les visiteurs/sessions tant que le conteneur vit
(`st.session_state` seul se contournerait trivialement par un simple refresh),
mais ne survit pas à un redémarrage du conteneur Streamlit Cloud (limite
acceptée : pire cas, un cooldown remis à zéro, jamais un abus permanent)."""

import os
import time


def can(state_path: str, cooldown_s: float) -> tuple[bool, float]:
    """(autorisé, secondes_restantes)."""
    try:
        with open(state_path) as f:
            last = float(f.read().strip())
    except (OSError, ValueError):
        return True, 0.0
    remaining = cooldown_s - (time.time() - last)
    return remaining <= 0, max(remaining, 0.0)


def record(state_path: str) -> None:
    """Marque l'instant présent — à appeler seulement après une action
    effectivement tentée (pas avant un échec de pré-condition comme une clé
    absente, qui ne consomme aucune ressource réseau)."""
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w") as f:
        f.write(str(time.time()))
