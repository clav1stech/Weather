# -*- coding: utf-8 -*-
"""Preuve de non-régression du dashboard — rendu des pages (complément de
check_non_regression.py, qui ne couvre que les fonctions de calcul).

Rend chaque page via streamlit.testing.v1.AppTest (aucun navigateur, aucune
écriture dans les données) et condense tout ce qui est affiché : titres,
textes, KPI/metrics, alertes, tables (hash), nombre de graphiques. Le contenu
inline des pages (cartes KPI, statuts, avertissements) est ainsi comparé
avant/après refactor, pas seulement les fonctions.

Usage (depuis la racine du projet) :
    python tools/ui_snapshot.py capture   # fige la référence
    python tools/ui_snapshot.py check     # compare à la référence

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

GOLDEN_PATH = os.path.join(ROOT, "tools", "golden", "golden_ui.json")

PAGES = ["Indicateur de canicule", "Observations en direct", "Vue d'ensemble",
         "Explorer un run", "Convergence des runs", "Contrôle des runs",
         "Lancer le pipeline"]

# Horloges pures (heure courante, indépendante des données) → neutralisées.
_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def _norm(text):
    return _CLOCK_RE.sub("HH:MM", str(text))


def _texts(elems):
    return [_norm(getattr(e, "value", getattr(e, "body", ""))) for e in elems]


def _df_hash(df):
    try:
        csv = df.to_csv(index=True, float_format="%.12g")
    except Exception:  # styler ou objet non-DataFrame → repr
        csv = repr(df)
    return hashlib.sha256(_norm(csv).encode("utf-8")).hexdigest()


def snapshot_page(page):
    at = AppTest.from_file(os.path.join(ROOT, "meteo_app.py"), default_timeout=600)
    at.run()
    at.sidebar.radio[0].set_value(page).run()
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
        "selectbox": [{ "label": s.label, "value": _norm(s.value)} for s in at.selectbox],
        "dataframes": [_df_hash(d.value) for d in at.dataframe],
        "n_plotly": len(at.get("plotly_chart")),
    }
    # Page Explorer : rejouer aussi avec le run archivé le plus récent (index 0),
    # pas seulement la sentinelle « Dernier run » — exercer run_slice + tableaux.
    if page == "Explorer un run" and at.selectbox:
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


def collect():
    out = {}
    for page in PAGES:
        print(f"  rendu : {page}…")
        out[page] = snapshot_page(page)
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    data = collect()
    if mode == "capture":
        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        print(f"Référence UI capturée : {GOLDEN_PATH}")
        return 0

    with open(GOLDEN_PATH, encoding="utf-8") as f:
        golden = json.load(f)
    diffs = []
    for page in PAGES:
        g, d = golden.get(page, {}), data.get(page, {})
        for key in sorted(set(g) | set(d)):
            if g.get(key) != d.get(key):
                diffs.append((page, key, g.get(key), d.get(key)))
    if diffs:
        print(f"[FAIL] {len(diffs)} divergence(s) de rendu :")
        for page, key, gv, dv in diffs:
            print(f"  - {page} / {key}")
            print(f"      golden : {json.dumps(gv, ensure_ascii=False)[:300]}")
            print(f"      actuel : {json.dumps(dv, ensure_ascii=False)[:300]}")
        return 1
    print("[OK] Rendu identique à la référence sur les "
          f"{len(PAGES)} pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
