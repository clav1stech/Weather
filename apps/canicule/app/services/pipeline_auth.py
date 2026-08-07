# -*- coding: utf-8 -*-
"""Adaptateur canicule de la porte par mot de passe (core/ui/auth.py) : lit le
secret dans st.secrets (core/ n'y touche jamais) et fixe la clé de session.

Le secret porte le nom déclaré par config.PIPELINE_PASSWORD_SECRET, à
configurer dans les Secrets de Streamlit Cloud. Absent → déclenchement
impossible (fermé par défaut, cf. core/ui/auth.py)."""

import streamlit as st

import config as C
from core.ui import auth as _core

SESSION_KEY = "pipeline_unlocked"


def _expected():
    """Mot de passe attendu, ou None si non configuré. st.secrets peut ne pas
    exister du tout en local (aucun fichier secrets.toml) : l'accès y est de
    toute façon accordé sans mot de passe (cf. page_run)."""
    try:
        return st.secrets.get(C.PIPELINE_PASSWORD_SECRET)
    except Exception:  # noqa: BLE001 — pas de secrets.toml : simplement non configuré
        return None


def gate() -> bool:
    """Affiche le champ de saisie et renvoie True si la session est autorisée
    à déclencher une collecte."""
    return _core.gate(
        _expected(), SESSION_KEY, label="🔒 Mot de passe",
        help_text="Requis pour déclencher une collecte. Consultation libre sans mot de passe.")


def unlocked() -> bool:
    return _core.unlocked(SESSION_KEY)
