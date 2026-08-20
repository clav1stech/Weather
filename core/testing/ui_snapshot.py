# -*- coding: utf-8 -*-
"""Preuve de non-régression du rendu des pages (complément de
check_non_regression.py, qui ne couvre que les fonctions de calcul).

Rend chaque page via streamlit.testing.v1.AppTest (aucun navigateur, aucune
écriture dans les données) et condense tout ce qui est affiché : titres,
textes, KPI/metrics, alertes, tables (hash), nombre de graphiques. Le contenu
inline des pages (cartes KPI, statuts, avertissements) est ainsi comparé
avant/après refactor, pas seulement les fonctions.

Harnais PARAMÉTRÉ PAR APP : les deux dashboards du monorepo (canicule et
neige) partagent la même mécanique, seuls diffèrent le point d'entrée, les
chemins à exposer et la liste des pages (cf. APPS).

Usage (depuis la racine du projet) :
    python tools/ui_snapshot.py capture           # canicule (défaut)
    python tools/ui_snapshot.py check
    python tools/ui_snapshot.py capture neige     # dashboard neige
    python tools/ui_snapshot.py check neige

NAVIGATION : les apps utilisent `st.navigation(position="top")` avec des pages
construites à partir de CALLABLES (functools.partial), pas de fichiers. Ni
`AppTest.switch_page` (qui exige un script sur disque) ni `query_params` ne
permettent alors de changer de page. Streamlit identifie une page par le hash
de son `url_path` : on pose donc directement ce hash sur l'AppTest, en le
calculant avec le même slug que la navigation (core.ui.nav.page_slug) — c'est
le seul point de couplage du harnais avec l'interne de Streamlit, et il est
vérifié à chaque rendu (une page non atteinte n'afficherait pas son titre).

Limites : à rejouer dans la même base de données ET la même heure « ronde »
(les KPI dépendent de l'instant courant — la première échéance « à venir »
change à chaque heure pleine). Les horloges pures (Heure UTC actuelle) sont
neutralisées par normalisation.
"""

import hashlib
import json
import os
import re
import sys

# Racine du monorepo (ce module vit dans core/testing/). Le package `app` du
# dashboard canicule vit sous apps/canicule/ : les deux chemins sont exposés,
# comme le fait meteo_app.py (entrée racine).
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "apps", "canicule"))
os.environ["WEATHER_LOCAL"] = "1"  # inclut la page « Lancer le pipeline »

from streamlit.testing.v1 import AppTest  # noqa: E402
from streamlit.util import calc_hash  # noqa: E402

from core.ui.nav import page_slug  # noqa: E402

# Registre des apps couvertes. `explore` = libellé de la page dont il faut
# aussi rejouer le premier run archivé (exercer run_slice et les tableaux).
APPS = {
    "canicule": {
        "entry": "meteo_app.py",
        "golden": "golden_ui.json",
        "pages": ["Indicateur de canicule", "Observations en direct",
                  "Vue d'ensemble", "Explorer un run", "Convergence des runs",
                  "Contrôle des runs", "Lancer le pipeline"],
        "explore": "Explorer un run",
    },
    "neige": {
        "entry": "snow_app.py",
        "golden": "golden_ui_neige.json",
        "pages": ["Vue d'ensemble neige", "Observations", "Explorer un run",
                  "Maille fine Météo-France", "Convergence des runs",
                  "Contrôle des runs", "Lancer le pipeline"],
        "explore": "Explorer un run",
    },
}
DEFAUT = "canicule"

# Horloges pures (heure courante, indépendante des données) → neutralisées.
_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def golden_path(app):
    return os.path.join(ROOT, "tools", "golden", APPS[app]["golden"])


def _norm(text):
    return _CLOCK_RE.sub("HH:MM", str(text))


def _texts(elems):
    return [_norm(getattr(e, "value", getattr(e, "body", ""))) for e in elems]


def _df_hash(df):
    # Un Styler est comparé sur SA DONNÉE (attribut .data) : l'habillage de
    # tableau ne doit pas masquer un changement de valeur, ni en simuler un.
    df = getattr(df, "data", df)
    try:
        csv = df.to_csv(index=True, float_format="%.12g")
    except Exception:  # objet non-DataFrame → repr
        csv = repr(df)
    return hashlib.sha256(_norm(csv).encode("utf-8")).hexdigest()


def _aller_a(at, page):
    """Sélectionne une page de la barre de navigation du haut. Streamlit dérive
    l'identité d'une page du hash de son url_path : le poser sur l'AppTest
    équivaut à cliquer l'onglet (cf. note de navigation en tête de module)."""
    at._page_hash = calc_hash(page_slug(page))
    return at


def snapshot_page(app, page):
    conf = APPS[app]
    at = AppTest.from_file(os.path.join(ROOT, conf["entry"]), default_timeout=600)
    at.run()
    _aller_a(at, page).run()
    assert not at.exception, f"Exception sur la page {page} : {at.exception[0].value}"
    snap = {
        "title": _texts(at.title),
        "subheader": _texts(at.subheader),
        "markdown": _texts(at.markdown),
        "caption": _texts(at.caption),
        "metric": [f"{m.label} = {m.value}" for m in at.metric],
        "warning": _texts(at.warning),
        "success": _texts(at.success),
        "info": _texts(at.info),
        "error": _texts(at.error),
        "selectbox": [{"label": s.label, "value": _norm(s.value)} for s in at.selectbox],
        "dataframes": [_df_hash(d.value) for d in at.dataframe],
        "n_plotly": len(at.get("plotly_chart")),
    }
    # Page Explorer : rejouer aussi avec le run archivé le plus récent (index 0),
    # pas seulement la sentinelle « Dernier run » — exercer run_slice + tableaux.
    if page == conf.get("explore") and at.selectbox:
        opts = at.selectbox[0].options
        if len(opts) > 1:
            at.selectbox[0].set_value(0).run()
            assert not at.exception, f"Exception (run 0) : {at.exception[0].value}"
            snap["run0"] = {
                "markdown": _texts(at.markdown), "caption": _texts(at.caption),
                "warning": _texts(at.warning),
                "dataframes": [_df_hash(d.value) for d in at.dataframe],
                "n_plotly": len(at.get("plotly_chart")),
            }
    return snap


def collect(app):
    out = {}
    for page in APPS[app]["pages"]:
        print(f"  rendu : {page}…")
        out[page] = snapshot_page(app, page)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    mode = args[0] if args else "check"
    app = args[1] if len(args) > 1 else DEFAUT
    if app not in APPS:
        print(f"App inconnue : {app} (attendu : {', '.join(APPS)})")
        return 2
    chemin = golden_path(app)
    data = collect(app)
    if mode == "capture":
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        print(f"Référence UI capturée ({app}) : {chemin}")
        return 0

    with open(chemin, encoding="utf-8") as f:
        golden = json.load(f)
    diffs = []
    for page in APPS[app]["pages"]:
        g, d = golden.get(page, {}), data.get(page, {})
        for key in sorted(set(g) | set(d)):
            if g.get(key) != d.get(key):
                diffs.append((page, key, g.get(key), d.get(key)))
    if diffs:
        print(f"[FAIL] {len(diffs)} divergence(s) de rendu ({app}) :")
        for page, key, gv, dv in diffs:
            print(f"  - {page} / {key}")
            print(f"      golden : {json.dumps(gv, ensure_ascii=False)[:300]}")
            print(f"      actuel : {json.dumps(dv, ensure_ascii=False)[:300]}")
        return 1
    print(f"[OK] Rendu identique à la référence sur les "
          f"{len(APPS[app]['pages'])} pages ({app}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
