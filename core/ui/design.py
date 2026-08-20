# -*- coding: utf-8 -*-
"""Feuille de style du dashboard — générée à partir des jetons (core/ui/tokens).

Un seul point d'injection par app (les points d'entrée), une seule source de
couleurs. Les composants n'écrivent JAMAIS de style inline : ils posent des
classes `wx-*` définies ici, ce qui rend le rendu modifiable sans toucher au
Python des pages.

Le thème natif Streamlit (.streamlit/config.toml) habille les widgets ; cette
feuille habille ce que le thème ne couvre pas — densité de page, en-têtes,
barre de navigation, cartes, badges, calendrier — et les quelques sélecteurs
internes de Streamlit qu'on veut resserrer.
"""

from core.ui.tokens import FONT_MONO, FONT_STACK, RADIUS, tokens


def _vars(pal):
    """Palette → variables CSS. Les composants ne référencent que ces variables :
    changer de thème ne change que ce bloc, jamais une règle."""
    lignes = [f"        --wx-{k.replace('_', '-')}: {v};" for k, v in pal.items()]
    lignes += [f"        --wx-radius-{k}: {v};" for k, v in RADIUS.items()]
    return "\n".join(lignes)


def stylesheet(dark=None):
    """Feuille complète, prête à injecter (balise <style> comprise)."""
    pal = tokens(dark)
    return f"""
    <style>
      :root {{
{_vars(pal)}
        --wx-font: {FONT_STACK};
        --wx-font-mono: {FONT_MONO};
      }}

      /* --- Densité de page ------------------------------------------------ */
      /* Streamlit réserve beaucoup de blanc en haut : on récupère cette hauteur
         pour l'en-tête de page, qui porte l'information utile. */
      .block-container {{padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1500px;}}
      h1, h2, h3 {{letter-spacing: -0.015em;}}
      h2 {{margin-top: 1.6rem;}}
      hr {{margin: 1.2rem 0; border-color: var(--wx-border);}}

      /* --- En-tête de page ------------------------------------------------ */
      .wx-header {{margin: 0 0 1.1rem 0;}}
      .wx-header .wx-eyebrow {{
          font-size: 0.72rem; font-weight: 650; letter-spacing: 0.09em;
          text-transform: uppercase; color: var(--wx-ink-faint);}}
      .wx-header h1 {{
          font-size: 1.85rem; font-weight: 700; line-height: 1.2;
          margin: 0.1rem 0 0.25rem 0; color: var(--wx-ink);}}
      .wx-header .wx-sub {{font-size: 0.92rem; color: var(--wx-ink-soft); margin: 0;}}

      /* --- Bandeau de statut ---------------------------------------------- */
      /* Barre de couleur à gauche plutôt qu'un aplat : lisible sans écraser le
         reste de la page, et le niveau se lit d'un coup d'œil. */
      .wx-banner {{
          display: flex; align-items: center; gap: 0.9rem;
          background: var(--wx-surface); border: 1px solid var(--wx-border);
          border-left: 5px solid var(--wx-accent);
          border-radius: var(--wx-radius-md); padding: 0.85rem 1.1rem;
          margin-bottom: 1.1rem; box-shadow: var(--wx-shadow);}}
      .wx-banner .wx-banner-txt {{flex: 1; min-width: 0;}}
      .wx-banner .wx-banner-title {{
          font-size: 1.05rem; font-weight: 650; color: var(--wx-ink); line-height: 1.3;}}
      .wx-banner .wx-banner-sub {{font-size: 0.85rem; color: var(--wx-ink-soft); margin-top: 0.15rem;}}
      .wx-banner .wx-banner-dot {{
          width: 12px; height: 12px; border-radius: 50%; flex: 0 0 auto;
          box-shadow: 0 0 0 4px var(--wx-accent-soft);}}

      /* --- Carte KPI ------------------------------------------------------ */
      .wx-kpi {{
          background: var(--wx-surface); border: 1px solid var(--wx-border);
          border-radius: var(--wx-radius-md); padding: 0.85rem 1rem; height: 100%;
          box-shadow: var(--wx-shadow);}}
      .wx-kpi-label {{
          font-size: 0.75rem; font-weight: 600; letter-spacing: 0.04em;
          text-transform: uppercase; color: var(--wx-ink-faint);}}
      .wx-kpi-value {{
          font-size: 1.8rem; font-weight: 650; line-height: 1.25; color: var(--wx-ink);
          margin-top: 0.15rem;}}
      .wx-kpi-unit {{font-size: 1rem; font-weight: 500; color: var(--wx-ink-soft); margin-left: 2px;}}
      .wx-kpi-delta {{font-size: 0.9rem; font-weight: 600; margin-left: 0.45rem; white-space: nowrap;}}
      .wx-kpi-sub {{
          font-size: 0.78rem; color: var(--wx-ink-soft); margin-top: 0.2rem;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;}}

      /* --- Badge / pastille ----------------------------------------------- */
      .wx-badge {{
          display: inline-flex; align-items: center; gap: 0.35rem;
          font-size: 0.76rem; font-weight: 600; line-height: 1;
          padding: 0.3rem 0.6rem; border-radius: var(--wx-radius-pill);
          border: 1px solid currentColor; white-space: nowrap;}}
      .wx-badge-dot {{width: 7px; height: 7px; border-radius: 50%; background: currentColor;}}

      /* --- Calendrier du risque ------------------------------------------- */
      /* Grille de cartes : contrairement à une heatmap Plotly d'une ligne, elle
         se replie proprement en écran étroit et accepte du texte par jour. */
      .wx-cal {{display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.3rem 0 0.8rem 0;}}
      .wx-cal-day {{
          flex: 1 1 90px; min-width: 90px; border-radius: var(--wx-radius-md);
          border: 1px solid var(--wx-border); padding: 0.55rem 0.6rem;
          background: var(--wx-surface); position: relative; overflow: hidden;}}
      .wx-cal-day::before {{
          content: ""; position: absolute; inset: 0 0 auto 0; height: 4px;
          background: var(--wx-day-color);}}
      .wx-cal-date {{
          font-size: 0.72rem; font-weight: 650; text-transform: uppercase;
          letter-spacing: 0.04em; color: var(--wx-ink-faint); margin-top: 0.15rem;}}
      .wx-cal-main {{font-size: 1.15rem; font-weight: 650; color: var(--wx-ink); line-height: 1.3;}}
      .wx-cal-sub {{font-size: 0.74rem; color: var(--wx-ink-soft); line-height: 1.35;}}
      .wx-cal-flag {{font-size: 0.74rem; color: var(--wx-ink-faint);}}

      /* --- État vide ------------------------------------------------------ */
      .wx-empty {{
          border: 1px dashed var(--wx-border-strong); border-radius: var(--wx-radius-md);
          padding: 1.4rem; text-align: center; color: var(--wx-ink-soft);
          font-size: 0.9rem; background: var(--wx-surface-alt);}}

      /* --- Panneau de contexte (sidebar) ---------------------------------- */
      .wx-ctx {{font-size: 0.82rem; color: var(--wx-ink-soft); line-height: 1.5;}}
      .wx-ctx strong {{color: var(--wx-ink);}}
      .wx-ctx-row {{
          display: flex; justify-content: space-between; gap: 0.6rem;
          padding: 0.22rem 0; border-bottom: 1px solid var(--wx-border);}}
      .wx-ctx-row:last-child {{border-bottom: none;}}
      .wx-foot {{font-size: 0.75rem; color: var(--wx-ink-faint); line-height: 1.5;}}

      /* --- Widgets Streamlit resserrés ------------------------------------ */
      div[data-testid="stMetric"] {{
          background: var(--wx-surface); border: 1px solid var(--wx-border);
          border-radius: var(--wx-radius-md); padding: 0.8rem 1rem;
          box-shadow: var(--wx-shadow);}}
      div[data-testid="stMetric"] label {{
          text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.75rem;}}
      .stPlotlyChart {{
          border: 1px solid var(--wx-border); border-radius: var(--wx-radius-md);
          padding: 0.25rem; background: var(--wx-surface);}}
      /* Onglets en pastilles : la barre soulignée par défaut se perd sur une
         page dense, et l'onglet actif y est peu contrasté. */
      div[data-baseweb="tab-list"] {{gap: 0.3rem; border-bottom: 1px solid var(--wx-border);}}
      button[data-baseweb="tab"] {{
          border-radius: var(--wx-radius-sm) var(--wx-radius-sm) 0 0;
          padding: 0.45rem 0.85rem;}}
      button[data-baseweb="tab"][aria-selected="true"] {{background: var(--wx-accent-soft);}}
      div[data-testid="stExpander"] details {{
          border: 1px solid var(--wx-border); border-radius: var(--wx-radius-md);
          background: var(--wx-surface);}}
      code, pre {{font-family: var(--wx-font-mono);}}

      /* --- Écrans étroits -------------------------------------------------- */
      @media (max-width: 640px) {{
        .block-container {{padding-left: 0.9rem; padding-right: 0.9rem;}}
        .wx-header h1 {{font-size: 1.45rem;}}
        .wx-kpi-value {{font-size: 1.5rem;}}
        .wx-cal-day {{flex: 1 1 45%;}}
      }}
    </style>
    """
