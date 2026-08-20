# -*- coding: utf-8 -*-
"""Thème Plotly partagé — templates `weather_light` / `weather_dark` dérivés des
jetons (core/ui/tokens) + `apply_layout`, seul endroit où se règlent marges,
légende, survol et hauteur d'une figure.

Config-agnostique comme tout core/ : aucune couleur métier (modèles, stations,
altitudes) n'entre ici — elles restent dans config.py / snow_config.py et sont
posées trace par trace par les modules de graphiques.

Pourquoi un template ET une fonction : le template porte ce que Plotly sait
hériter (fonds, grilles, police, couleurs d'axes, colorway) ; `apply_layout`
porte ce que chaque figure redéclarait au cas par cas (hauteurs 340/360/480,
marges, position de légende) — c'est là que le rendu divergeait d'un graphique
à l'autre.
"""

import plotly.graph_objects as go
import plotly.io as pio

from core.ui.theme import _is_dark
from core.ui.tokens import CHART_H, FONT_STACK, tokens

# Noms enregistrés dans plotly.io.templates : une figure peut donc aussi les
# nommer directement (template="weather_dark") hors de apply_layout.
LIGHT_NAME = "weather_light"
DARK_NAME = "weather_dark"

# Marges normalisées. Généreuses en haut seulement quand la figure porte un
# titre ET une légende horizontale posée au-dessus de la zone de tracé — sinon
# ce blanc ne sert à rien et écrase la donnée sur les petites hauteurs.
_MARGIN_BASE = dict(l=10, r=10)
_TOP = {"nu": 24, "titre": 56, "titre_legende": 76, "legende": 44}
# Bas de figure : le minimum, sauf légende posée SOUS la zone de tracé. Les
# étiquettes d'axe sur deux lignes se réservent leur place via `bottom`.
_BOTTOM = {"nu": 10, "legende": 60}

# Titre calé en haut à gauche, dans le blanc de la marge : il se lit comme un
# libellé de section, pas comme une légende centrée qui mange la zone de tracé.
_TITLE_POS = dict(x=0, xanchor="left", xref="paper", y=0.98, yanchor="top")


def _template(dark):
    """Construit le template d'un thème à partir de la palette sémantique."""
    pal = tokens(dark)
    axis = dict(
        gridcolor=pal["border"],
        zerolinecolor=pal["border_strong"],
        linecolor=pal["border_strong"],
        tickfont=dict(color=pal["ink_soft"], size=11),
        title=dict(font=dict(color=pal["ink_soft"], size=12)),
        automargin=True,
    )
    layout = go.Layout(
        # Fonds transparents : la carte qui entoure le graphique (classe
        # .stPlotlyChart de la feuille de style) fournit déjà la surface, un
        # aplat opaque ici créerait un rectangle dans le rectangle.
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_STACK, color=pal["ink"], size=12),
        title=dict(font=dict(color=pal["ink"], size=15), **_TITLE_POS),
        colorway=[pal["accent"], pal["warm"], pal["cold"], pal["risk_2"],
                  pal["ok"], pal["risk_1"], pal["neutral"], pal["danger"]],
        hoverlabel=dict(font=dict(family=FONT_STACK, size=12),
                        bgcolor=pal["surface"], bordercolor=pal["border_strong"]),
        legend=dict(font=dict(color=pal["ink_soft"], size=11), bgcolor="rgba(0,0,0,0)"),
        xaxis=axis, yaxis=axis,
        margin=dict(**_MARGIN_BASE, t=_TOP["titre"], b=_BOTTOM["nu"]),
    )
    return go.layout.Template(layout=layout)


def register():
    """Enregistre les deux templates (idempotent : ré-enregistrer écrase à
    l'identique, ce qui permet d'appeler cette fonction à l'import)."""
    pio.templates[LIGHT_NAME] = _template(False)
    pio.templates[DARK_NAME] = _template(True)


register()


def template_name(dark=None):
    """Nom du template correspondant au thème actif (mode auto géré par _is_dark)."""
    if dark is None:
        dark = _is_dark()
    return DARK_NAME if dark else LIGHT_NAME


def chart_height(height):
    """Hauteur en pixels : soit une clé du registre CHART_H ("standard"…), soit
    un entier explicite (toléré pour les figures dont la hauteur dépend du
    nombre de lignes, ex. heatmaps). Une clé inconnue retombe sur `standard`
    plutôt que de lever — un graphique mal dimensionné vaut mieux qu'une page
    en erreur."""
    if isinstance(height, str):
        return CHART_H.get(height, CHART_H["standard"])
    return int(height)


def apply_layout(fig, *, height="standard", title=None, legend="top",
                 hovermode="x unified", x_title=None, y_title=None,
                 bottom=None, dark=None, **extra):
    """Applique le gabarit commun à une figure et la retourne.

    height : clé de CHART_H ou pixels.
    title  : titre de la figure (None = aucun, la page porte alors le libellé).
    legend : "top" (horizontale au-dessus, cas général), "bottom" (horizontale
             sous la zone de tracé, quand le haut est déjà chargé), "right",
             "inside" (superposée en haut à droite, pour les figures denses) ou
             None (légende masquée). Le blanc réservé suit ce choix.
    bottom : marge basse en pixels — à ne forcer que si les étiquettes de l'axe
             des x tiennent sur plusieurs lignes.
    extra  : passé tel quel à update_layout (axes secondaires, barmode…) —
             volontairement ouvert, mais tout réglage devenu récurrent a
             vocation à remonter ici plutôt qu'à se disperser dans les pages.
    """
    bas = _BOTTOM["nu"]
    if legend == "top":
        legend_cfg = dict(orientation="h", yanchor="bottom", y=1.02,
                          xanchor="left", x=0)
        top = _TOP["titre_legende"] if title else _TOP["legende"]
    elif legend == "bottom":
        legend_cfg = dict(orientation="h", yanchor="top", y=-0.16,
                          xanchor="left", x=0)
        top = _TOP["titre"] if title else _TOP["nu"]
        bas = _BOTTOM["legende"]
    elif legend == "right":
        legend_cfg = dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02)
        top = _TOP["titre"] if title else _TOP["nu"]
    elif legend == "inside":
        legend_cfg = dict(orientation="v", yanchor="top", y=0.99,
                          xanchor="right", x=0.99, bgcolor="rgba(0,0,0,0)")
        top = _TOP["titre"] if title else _TOP["nu"]
    else:
        legend_cfg = None
        top = _TOP["titre"] if title else _TOP["nu"]

    fig.update_layout(
        template=template_name(dark),
        height=chart_height(height),
        hovermode=hovermode,
        showlegend=legend_cfg is not None,
        margin=dict(**_MARGIN_BASE, t=top, b=bas if bottom is None else bottom),
    )
    if title:
        # Position répétée sur la figure (et pas seulement dans le template) :
        # elle fait partie du contrat de rendu vérifié par les tests.
        fig.update_layout(title=dict(text=title, **_TITLE_POS))
    else:
        fig.update_layout(title=None)
    if legend_cfg is not None:
        fig.update_layout(legend=legend_cfg)
    if x_title is not None:
        fig.update_xaxes(title_text=x_title)
    if y_title is not None:
        fig.update_yaxes(title_text=y_title)
    if extra:
        fig.update_layout(**extra)
    return fig
