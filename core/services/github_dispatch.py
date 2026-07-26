# -*- coding: utf-8 -*-
"""Déclenchement à distance d'un workflow GitHub Actions depuis un dashboard
public — le processus Streamlit n'écrit JAMAIS lui-même dans les parquets ni
dans le dépôt : il se contente de POSTer un `workflow_dispatch`, c'est
toujours le job CI existant qui collecte et pousse, exactement comme un cron
normal (cf. invariant CLAUDE.md « dashboard en lecture seule »).

Exposé à un public anonyme non authentifié → le cooldown (`can_trigger`/
`record_trigger`) est un garde-fou de sécurité, pas un confort d'UX : il doit
être vérifié AVANT tout appel réseau, jamais après.

Module GÉNÉRIQUE (règle core/) : dépôt, workflow, jeton et chemin d'état
arrivent en paramètres — la lecture des secrets (st.secrets) et de la config
reste dans l'adaptateur de chaque app."""

import requests

from core.services import cooldown


def can_trigger(state_path: str, cooldown_s: float) -> tuple[bool, float]:
    """(autorisé, secondes_restantes). Lit l'horodatage partagé sur disque —
    persiste entre visiteurs/sessions tant que le conteneur vit (contrairement
    à st.session_state seul, contournable par un simple refresh de page), mais
    PAS entre redémarrages du conteneur Streamlit Cloud (limite acceptée :
    pire cas, un cooldown remis à zéro, jamais un abus permanent)."""
    return cooldown.can(state_path, cooldown_s)


def record_trigger(state_path: str) -> None:
    """Marque l'instant du déclenchement — appelé seulement après un POST
    réussi (204), jamais avant : un échec ne doit pas consommer le cooldown."""
    cooldown.record(state_path)


def trigger_workflow(owner: str, repo: str, workflow: str, token: str,
                     target: str, ref: str = "main") -> tuple[bool, str]:
    """POST workflow_dispatch sur le job visé. (succès, message) — le message
    est toujours sûr à afficher tel quel, il ne contient jamais le token ni le
    corps brut de la réponse GitHub."""
    url = (f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
           f"{workflow}/dispatches")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    payload = {"ref": ref, "inputs": {"target": target}}

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
