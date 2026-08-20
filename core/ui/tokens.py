# -*- coding: utf-8 -*-
"""Jetons de design partagés par les deux dashboards — source de vérité UNIQUE
des couleurs, rayons, espacements et hauteurs de graphique.

Config-agnostique comme tout core/ : aucune couleur métier ici (modèles,
stations), celles-ci restent dans config.py / snow_config.py et arrivent en
paramètres. On ne trouve ici que des couleurs SÉMANTIQUES (surface, encre,
accent, niveaux de risque), c'est-à-dire ce qui décrit l'interface elle-même.

Deux jeux complets, clair et sombre : le thème n'est jamais « un jeu clair
assombri à la volée », chaque valeur est choisie pour son fond. `tokens()`
sélectionne le jeu actif via `_is_dark()` (theme.py), qui gère le mode auto.
"""

from core.ui.theme import _is_dark

# --------------------------------------------------------------------------- #
#  Mesures — indépendantes du thème
# --------------------------------------------------------------------------- #
# Échelle d'espacement (rem) : toute marge verticale du dashboard s'y ramène,
# c'est ce qui donne un rythme régulier plutôt que des écarts au jugé.
SPACE = {"xs": 0.25, "sm": 0.5, "md": 1.0, "lg": 1.5, "xl": 2.5}

RADIUS = {"sm": "8px", "md": "12px", "lg": "16px", "pill": "999px"}

# Hauteurs normalisées des graphiques. Les figures ne fixent plus leur hauteur
# au cas par cas (340/360/480 auparavant) : elles choisissent un registre.
CHART_H = {"strip": 150, "compact": 300, "standard": 380, "tall": 520}

# Police : pile système d'abord (aucun téléchargement, rendu natif sur chaque
# OS), Inter en tête si l'utilisateur l'a installée localement.
FONT_STACK = ('"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
              '"Helvetica Neue", Arial, sans-serif')
FONT_MONO = '"SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace'

# --------------------------------------------------------------------------- #
#  Palettes sémantiques
# --------------------------------------------------------------------------- #
# Clés communes aux deux jeux (un écart de clé serait un bug silencieux : la CSS
# générée référencerait une variable inexistante) :
#   bg / surface / surface_alt : fonds, du plus profond au plus détaché
#   border / border_strong     : traits de séparation et bordures de carte
#   ink / ink_soft / ink_faint : texte principal, secondaire, discret
#   accent                     : couleur d'action (liens, item actif, primary)
#   risk_0..risk_3             : échelle de risque (nul → élevé), commune aux
#                                deux apps — le canicule la lit en chaleur, la
#                                neige en intensité d'épisode
#   warm / cold / neutral      : signe d'une anomalie (au-dessus, en dessous, nul)
#   ok / warn / danger         : statut d'exploitation (données fraîches, partielles, absentes)
#   danger_soft                : aplat d'alerte (fond d'une ligne de tableau signalée)
_LIGHT = {
    "bg": "#F7F8FA",
    "surface": "#FFFFFF",
    "surface_alt": "#EFF2F6",
    "border": "rgba(100,116,139,0.20)",
    "border_strong": "rgba(100,116,139,0.38)",
    "ink": "#1B2733",
    "ink_soft": "#4A5768",
    "ink_faint": "#7A8698",
    "accent": "#1F618D",
    "accent_soft": "rgba(31,97,141,0.12)",
    "risk_0": "#7FB3D5",
    "risk_1": "#F4D03F",
    "risk_2": "#E67E22",
    "risk_3": "#C0392B",
    "warm": "#C0392B",
    "cold": "#2980B9",
    "neutral": "#7F8C8D",
    "ok": "#1E8449",
    "warn": "#B9770E",
    "danger": "#C0392B",
    "danger_soft": "rgba(192,57,43,0.10)",
    "shadow": "0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.10)",
}

_DARK = {
    "bg": "#0E1117",
    "surface": "#171B23",
    "surface_alt": "#1F242E",
    "border": "rgba(148,163,184,0.20)",
    "border_strong": "rgba(148,163,184,0.40)",
    "ink": "#E6E9EE",
    "ink_soft": "#AAB4C4",
    "ink_faint": "#78849B",
    "accent": "#5DADE2",
    "accent_soft": "rgba(93,173,226,0.16)",
    "risk_0": "#4A7FA5",
    "risk_1": "#D4B03A",
    "risk_2": "#DD7B2A",
    "risk_3": "#D9534F",
    "warm": "#E8776F",
    "cold": "#5DADE2",
    "neutral": "#95A5A6",
    "ok": "#52BE80",
    "warn": "#E5A93C",
    "danger": "#E8776F",
    "danger_soft": "rgba(232,119,111,0.16)",
    "shadow": "0 1px 2px rgba(0,0,0,0.30), 0 1px 3px rgba(0,0,0,0.40)",
}

assert set(_LIGHT) == set(_DARK), "Les deux palettes doivent porter les mêmes clés."

# Échelle de risque ordonnée — les composants (calendrier, badges) l'indexent
# par niveau plutôt que de nommer une couleur à la main.
RISK_KEYS = ["risk_0", "risk_1", "risk_2", "risk_3"]


def tokens(dark=None):
    """Palette active. `dark=None` → détection du thème courant (mode auto géré)."""
    if dark is None:
        dark = _is_dark()
    return dict(_DARK if dark else _LIGHT)


def risk_color(level, dark=None):
    """Couleur d'un niveau de risque 0-3, bornée (un niveau hors échelle est
    ramené aux extrémités plutôt que de lever : l'UI ne casse jamais sur une
    valeur inattendue)."""
    pal = tokens(dark)
    idx = max(0, min(len(RISK_KEYS) - 1, int(level)))
    return pal[RISK_KEYS[idx]]


def anomaly_color(delta, seuil=0.05, dark=None):
    """Couleur du signe d'une anomalie : au-dessus, en dessous, ou nulle dans la
    bande morte ±seuil (évite de colorer un écart non significatif)."""
    pal = tokens(dark)
    if delta >= seuil:
        return pal["warm"]
    if delta <= -seuil:
        return pal["cold"]
    return pal["neutral"]
