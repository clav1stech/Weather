#!/bin/bash
# Lance le dashboard neige (Megève) en local sur macOS.
# Crée un venv Python (.venv/) si besoin, installe les dépendances, puis démarre Streamlit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

cd "$SCRIPT_DIR"

# --- Python ---
if command -v python3 &>/dev/null; then
    PYTHON=python3
else
    echo "❌  Python 3 introuvable. Installe-le via https://www.python.org ou Homebrew (brew install python)."
    exit 1
fi

# --- Environnement virtuel ---
if [ ! -f "$VENV/bin/activate" ]; then
    echo "📦  Création du venv (.venv/)…"
    "$PYTHON" -m venv "$VENV"
fi

source "$VENV/bin/activate"

# --- Dépendances ---
if ! python -c "import streamlit" &>/dev/null; then
    echo "📥  Installation des dépendances (requirements.txt)…"
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
fi

# --- Lancement ---
export WEATHER_LOCAL=1
echo "🚀  Démarrage du dashboard neige → http://localhost:8501"
python -m streamlit run snow_app.py
