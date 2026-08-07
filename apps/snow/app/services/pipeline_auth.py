# -*- coding: utf-8 -*-
"""Adaptateur neige de la porte par mot de passe (core/ui/auth.py) : lit le
secret dans st.secrets (core/ n'y touche jamais) et fixe la clé de session.

Secret nommé par snow_config.PIPELINE_PASSWORD_SECRET, à configurer dans les
Secrets de Streamlit Cloud. Absent → déclenchement impossible (fermé par
défaut, cf. core/ui/auth.py)."""

import streamlit as st

from apps.snow import snow_config as SC
from core.ui import auth as _core

SESSION_KEY = "snow_pipeline_unlocked"


def _diagnostic_secrets():
    """Compte les secrets visibles par CETTE app, sans jamais en exposer le
    nom ni la valeur — distingue les deux pannes qui se ressemblent à l'écran :
    l'app ne voit AUCUN secret (secrets posés sur une autre app, ou pas encore
    appliqués) vs elle en voit mais pas celui-ci (nom mal orthographié, ou
    rangé dans une section alors que seule la racine est consultée)."""
    try:
        n = len(list(st.secrets.keys()))
    except Exception:  # noqa: BLE001 — aucun secret configuré du tout
        return "aucun secret visible par cette app"
    if n == 0:
        return "aucun secret visible par cette app"
    return f"{n} secret(s) visible(s) par cette app, mais pas celui-ci"


def _expected():
    """(mot de passe attendu | None, diagnostic | None).

    Distingue les causes d'indisponibilité, qui ne se corrigent pas de la même
    façon : secrets illisibles, secret absent/vide, ou secrets posés sur une
    AUTRE app (chaque app Streamlit a les siens)."""
    try:
        valeur = st.secrets.get(SC.PIPELINE_PASSWORD_SECRET)
    except Exception as exc:  # noqa: BLE001 — remonté tel quel, jamais masqué
        return None, ("Secrets du serveur illisibles ({}) — vérifier la syntaxe "
                      "TOML.".format(type(exc).__name__))
    if not valeur:
        return None, ("Déclenchement non configuré : secret `PIPELINE_PASSWORD` absent "
                      "ou vide — " + _diagnostic_secrets() + ". Il doit être défini "
                      "à la RACINE des secrets de CETTE app (pas dans une section, "
                      "et chaque app a ses propres secrets).")
    return valeur, None


def gate() -> bool:
    """Champ de saisie tant que la session n'est pas déverrouillée."""
    attendu, diagnostic = _expected()
    return _core.gate(
        attendu, SESSION_KEY, label="🔒 Mot de passe", diagnostic=diagnostic,
        help_text="Requis pour déclencher une collecte. Consultation libre sans mot de passe.")


def unlocked() -> bool:
    return _core.unlocked(SESSION_KEY)
