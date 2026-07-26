# -*- coding: utf-8 -*-
"""Package du dashboard NEIGE Megève. Même architecture en couches que le
dashboard canicule (core ← runtime ← data ← domains/pages), mêmes règles :
jamais d'import entre pages ni entre domaines, config dans snow_config.py.

Le nom d'import canonique est `apps.snow.app` : il reste distinct du package
canicule top-level `app`, y compris lorsque tests et harnais partagent le même
processus Python."""
