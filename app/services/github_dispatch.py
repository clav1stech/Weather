# -*- coding: utf-8 -*-
"""Déclenchement à distance du workflow GitHub Actions depuis le dashboard
public — le processus Streamlit n'écrit JAMAIS lui-même dans les parquets ni
dans le dépôt : il se contente de POSTer un `workflow_dispatch`, c'est
toujours le job CI existant qui collecte et pousse, exactement comme un cron
normal (cf. invariant CLAUDE.md « dashboard en lecture seule »).

Exposé à un public anonyme non authentifié → le cooldown (`can_trigger`/
`record_trigger`) est un garde-fou de sécurité, pas un confort d'UX : il doit
être vérifié AVANT tout appel réseau, jamais après."""

import os
import time

import requests
import streamlit as st

import config as C


def can_trigger() -> tuple[bool, float]:
    """(autorisé, secondes_restantes). Lit l'horodatage partagé sur disque —
    persiste entre visiteurs/sessions tant que le conteneur vit (contrairement
    à st.session_state seul, contournable par un simple refresh de page), mais
    PAS entre redémarrages du conteneur Streamlit Cloud (limite acceptée :
    pire cas, un cooldown remis à zéro, jamais un abus permanent)."""
    try:
        with open(C.GITHUB_DISPATCH_STATE_PATH) as f:
            last = float(f.read().strip())
    except (OSError, ValueError):
        return True, 0.0
    remaining = C.GITHUB_DISPATCH_COOLDOWN_S - (time.time() - last)
    return remaining <= 0, max(remaining, 0.0)


def record_trigger() -> None:
    """Marque l'instant du déclenchement — appelé seulement après un POST
    réussi (204), jamais avant : un échec ne doit pas consommer le cooldown."""
    os.makedirs(os.path.dirname(C.GITHUB_DISPATCH_STATE_PATH), exist_ok=True)
    with open(C.GITHUB_DISPATCH_STATE_PATH, "w") as f:
        f.write(str(time.time()))


def trigger_workflow(target: str = "obs6m") -> tuple[bool, str]:
    """POST workflow_dispatch sur le job existant (target=obs6m par défaut).
    (succès, message) — le message est toujours sûr à afficher tel quel, il ne
    contient jamais le token ni le corps brut de la réponse GitHub."""
    token = st.secrets.get(C.GITHUB_DISPATCH_TOKEN_SECRET)
    if not token:
        return False, "Rafraîchissement à distance non configuré (secret absent)."

    url = (f"https://api.github.com/repos/{C.GITHUB_DISPATCH_OWNER}/"
           f"{C.GITHUB_DISPATCH_REPO}/actions/workflows/"
           f"{C.GITHUB_DISPATCH_WORKFLOW}/dispatches")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    payload = {"ref": "main", "inputs": {"target": target}}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException:
        return False, "GitHub Actions injoignable pour le moment — réessayez plus tard."

    if resp.status_code == 204:
        return True, "Collecte lancée."
    if resp.status_code in (401, 403):
        return False, "Déclenchement refusé (permissions insuffisantes côté serveur)."
    if resp.status_code == 404:
        return False, "Workflow introuvable côté serveur."
    return False, f"Échec du déclenchement (code {resp.status_code})."
