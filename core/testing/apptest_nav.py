# -*- coding: utf-8 -*-
"""Navigation d'une app `st.navigation` sous streamlit.testing.v1.AppTest.

Les deux dashboards construisent leurs pages à partir de CALLABLES
(`functools.partial`), pas de fichiers : ni `AppTest.switch_page` (qui exige un
script sur disque) ni `query_params` ne savent alors changer de page. Streamlit
identifie une page par le hash de son `url_path` ; on pose donc ce hash sur
l'AppTest, calculé avec le slug de la navigation (`core.ui.nav.page_slug`).

C'est le SEUL point de couplage des tests avec l'interne de Streamlit : il est
isolé ici pour n'avoir qu'un endroit à corriger si l'API bouge, et il est
vérifiable (`page_rendue`) — une page non atteinte ne rend pas son en-tête.
"""

import html as _html

from streamlit.util import calc_hash

from core.ui.nav import page_slug


def aller_a(at, libelle):
    """Ouvre la page `libelle` (équivalent d'un clic sur son onglet). Ne relance
    PAS le script : appeler `.run()` ensuite, comme après un `set_value`."""
    at._page_hash = calc_hash(page_slug(libelle))
    return at


def page_rendue(at, extrait):
    """La page affichée porte-t-elle `extrait` dans son EN-TÊTE (classe
    wx-header de core.ui.components.page_header) ? C'est la preuve que la
    navigation a abouti sur la bonne page et non sur la page par défaut.

    `extrait` est un fragment du TITRE DE LA PAGE, qui n'est pas toujours son
    libellé de navigation (« Contrôle des runs » s'affiche « Contrôle de
    présence des modèles »). Le fragment est échappé comme le fait le
    composant, sans quoi une apostrophe ne se retrouverait jamais."""
    cible = _html.escape(str(extrait), quote=True)
    return any("wx-header" in m.value and cible in m.value for m in at.markdown)
