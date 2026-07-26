# -*- coding: utf-8 -*-
"""Composants génériques des pages de lancement local d'un pipeline.

Le module ne connaît ni app, ni config : le dossier de travail et les scripts
arrivent en paramètres. Les sous-processus capturent toujours stdout/stderr ;
aucune donnée n'est interprétée ni écrite par cette couche d'interface.
"""

import os
import subprocess
import sys

import streamlit as st


def run_script(base_dir, *args, timeout=300):
    """Lance Python dans ``base_dir`` et renvoie ``(code, sortie)``."""
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, *args], cwd=base_dir, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
        env=child_env,
    )
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr)
                                    if proc.stderr else "")
    return proc.returncode, output


def execute(entries, *, base_dir, runner=run_script):
    """Exécute une séquence ``(label, script, timeout)`` avec spinner.

    ``code=None`` représente une exception déjà rendue lisible. Les autres
    flux continuent afin que le bouton groupé fournisse un diagnostic complet.
    """
    out = []
    for label, script, timeout in entries:
        with st.spinner(f"Exécution de {script}…"):
            try:
                code, output = runner(
                    base_dir, os.path.join(base_dir, script), timeout=timeout)
                out.append((label, code, output or "(aucune sortie)"))
            except subprocess.TimeoutExpired:
                out.append((label, None, f"⏱️ Délai dépassé ({timeout} s)."))
            except Exception as exc:  # noqa: BLE001 — erreur affichée dans l'UI
                out.append((label, None, f"Erreur : {exc}"))
    st.cache_data.clear()
    return out


def render_execution_results(results):
    """Rend le déroulé d'exécution en pleine largeur sous les boutons."""
    if not results:
        return
    st.markdown("#### 📄 Déroulé de la dernière exécution")
    for label, code, output in results:
        if code == 0:
            st.success(f"✅ {label} : terminé.")
        elif code is None:
            st.error(f"❌ {label} : {output}")
            continue
        else:
            st.error(f"❌ {label} : code de sortie {code}.")
        st.code(output)
