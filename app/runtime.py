# -*- coding: utf-8 -*-
"""Contexte d'exécution du dashboard : local vs cloud, fuseaux horaires,
variable principale. Aucune dépendance interne — module racine du package."""

import os
from zoneinfo import ZoneInfo

import streamlit as st

import config as C

LOCAL_TZ = ZoneInfo("Europe/Paris")
VAR = C.PRIMARY_VAR  # variable principale affichée (t850)


# --------------------------------------------------------------------------- #
#  Détection local / cloud (le bouton « run » n'a de sens qu'en local)
# --------------------------------------------------------------------------- #
def _detect_local():
    forced = os.environ.get("WEATHER_LOCAL")
    if forced in ("0", "1"):
        return forced == "1"
    base = C.BASE_DIR.replace("\\", "/")
    on_cloud = base.startswith("/mount/src") or \
        os.environ.get("HOSTNAME", "").startswith("streamlit")
    return not on_cloud


IS_LOCAL = _detect_local()


def user_tz():
    """Fuseau horaire du NAVIGATEUR de l'utilisateur (st.context), repli sur
    l'heure de Paris. Sert aux horodatages « temps réel » (rafraîchissement) —
    les données météo, elles, restent affichées en heure de Paris (LOCAL_TZ)."""
    try:
        tz = st.context.timezone
        if tz:
            return ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        pass
    return LOCAL_TZ
