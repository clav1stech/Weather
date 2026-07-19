# -*- coding: utf-8 -*-
"""Contexte d'exécution du dashboard neige : local vs cloud, fuseau horaire.
Aucune dépendance interne — module racine du package."""

import os
from zoneinfo import ZoneInfo

from apps.snow import snow_config as SC

LOCAL_TZ = ZoneInfo("Europe/Paris")


def _detect_local():
    forced = os.environ.get("WEATHER_LOCAL")
    if forced in ("0", "1"):
        return forced == "1"
    base = SC.SNOW_DIR.replace("\\", "/")
    on_cloud = base.startswith("/mount/src") or \
        os.environ.get("HOSTNAME", "").startswith("streamlit")
    return not on_cloud


IS_LOCAL = _detect_local()
