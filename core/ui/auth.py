# -*- coding: utf-8 -*-
"""Porte d'accès par mot de passe pour une action sensible d'un dashboard
public (ici : déclencher une collecte depuis la page « Lancer le pipeline »).

Config-agnostique comme tout core/ : le mot de passe attendu arrive en
paramètre, jamais lu d'une config ni d'un secret ici — c'est l'adaptateur
`app/` de chaque application qui va le chercher dans `st.secrets`.

Principes de sécurité, dans l'ordre d'importance :
  • **Fermé par défaut** : un secret absent ou vide REFUSE l'accès, il ne
    l'ouvre jamais. Pas de mot de passe par défaut, pas de repli permissif.
  • **Comparaison à temps constant** (`hmac.compare_digest`) : une comparaison
    `==` sur des chaînes fuit la longueur du préfixe correct par son temps
    d'exécution.
  • **Le mot de passe n'est jamais réaffiché ni journalisé**, et n'est pas
    stocké en session : seul l'état « déverrouillé » (booléen) l'est.
"""

import hmac

import streamlit as st


def unlocked(session_key: str) -> bool:
    """True si cette session a déjà été déverrouillée."""
    return bool(st.session_state.get(session_key))


def gate(expected: str | None, session_key: str, *,
         label: str = "Mot de passe", help_text: str | None = None,
         diagnostic: str | None = None) -> bool:
    """Affiche le champ de saisie tant que la session n'est pas déverrouillée
    et renvoie l'état d'accès.

    `expected` = mot de passe attendu (None/vide → accès refusé). `diagnostic`
    permet à l'appelant de préciser POURQUOI il est absent — « secret manquant »
    et « secrets illisibles » se corrigent différemment, et les confondre rend
    la panne indiagnosticable depuis l'interface."""
    if unlocked(session_key):
        return True

    if not expected:
        st.info(diagnostic or "Déclenchement non configuré sur cette instance "
                              "(mot de passe absent côté serveur).")
        return False

    saisi = st.text_input(label, type="password", key=f"{session_key}_input",
                          help=help_text)
    if not saisi:
        return False
    # compare_digest exige des types identiques ; encoder évite aussi de
    # dépendre de la normalisation Unicode de la saisie.
    if hmac.compare_digest(saisi.encode("utf-8"), expected.encode("utf-8")):
        st.session_state[session_key] = True
        return True
    st.error("Mot de passe incorrect.")
    return False
