# -*- coding: utf-8 -*-
"""Adaptateur neige du déclenchement à distance mutualisé
(core/services/github_dispatch.py) : lie le dépôt/workflow/chemin d'état de
snow_config et la lecture du secret (st.secrets) aux fonctions génériques.

core/ ne touche jamais à st.secrets : la lecture du PAT reste ici."""

import streamlit as st

from apps.snow import snow_config as SC
from core.services import github_dispatch as _core


def can_trigger() -> tuple[bool, float]:
    """(autorisé, secondes_restantes) — cooldown partagé entre visiteurs."""
    return _core.can_trigger(SC.GITHUB_DISPATCH_STATE_PATH,
                             SC.GITHUB_DISPATCH_COOLDOWN_S)


def record_trigger() -> None:
    """Marque l'instant du déclenchement — seulement après un POST réussi."""
    _core.record_trigger(SC.GITHUB_DISPATCH_STATE_PATH)


def trigger_workflow(target: str) -> tuple[bool, str]:
    """POST workflow_dispatch sur le job neige visé. Secret absent → refus
    explicite, jamais un appel réseau à l'aveugle."""
    token = st.secrets.get(SC.GITHUB_DISPATCH_TOKEN_SECRET)
    if not token:
        return False, "Déclenchement à distance non configuré (secret absent)."
    return _core.trigger_workflow(SC.GITHUB_DISPATCH_OWNER, SC.GITHUB_DISPATCH_REPO,
                                  SC.GITHUB_DISPATCH_WORKFLOW, token, target)
