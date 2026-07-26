# -*- coding: utf-8 -*-
"""Adaptateur canicule du déclenchement à distance mutualisé
(core/services/github_dispatch.py) : lie le dépôt/workflow/chemin d'état de la
config et la lecture du secret (st.secrets) aux fonctions génériques, en
conservant les signatures historiques (sans argument côté appelant)."""

import streamlit as st

import config as C
from core.services import github_dispatch as _core


def can_trigger() -> tuple[bool, float]:
    """(autorisé, secondes_restantes) — cf. core/services/github_dispatch."""
    return _core.can_trigger(C.GITHUB_DISPATCH_STATE_PATH,
                             C.GITHUB_DISPATCH_COOLDOWN_S)


def record_trigger() -> None:
    """Marque l'instant du déclenchement — seulement après un POST réussi."""
    _core.record_trigger(C.GITHUB_DISPATCH_STATE_PATH)


def trigger_workflow(target: str = "obs6m") -> tuple[bool, str]:
    """POST workflow_dispatch sur le job existant (target=obs6m par défaut).
    La lecture du secret reste ici : core/ ne touche jamais à st.secrets."""
    token = st.secrets.get(C.GITHUB_DISPATCH_TOKEN_SECRET)
    if not token:
        return False, "Rafraîchissement à distance non configuré (secret absent)."
    return _core.trigger_workflow(C.GITHUB_DISPATCH_OWNER, C.GITHUB_DISPATCH_REPO,
                                  C.GITHUB_DISPATCH_WORKFLOW, token, target)
