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


def _expected():
    """Mot de passe attendu, ou None si non configuré. st.secrets peut ne pas
    exister du tout en local (aucun secrets.toml) : l'accès y est de toute
    façon accordé sans mot de passe (cf. page_run)."""
    try:
        return st.secrets.get(SC.PIPELINE_PASSWORD_SECRET)
    except Exception:  # noqa: BLE001 — pas de secrets.toml : simplement non configuré
        return None


def gate() -> bool:
    """Champ de saisie tant que la session n'est pas déverrouillée."""
    return _core.gate(
        _expected(), SESSION_KEY, label="🔒 Mot de passe",
        help_text="Requis pour déclencher une collecte. Consultation libre sans mot de passe.")


def unlocked() -> bool:
    return _core.unlocked(SESSION_KEY)
