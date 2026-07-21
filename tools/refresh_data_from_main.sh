#!/usr/bin/env bash
# Rafraîchit la donnée locale depuis origin/main pendant un chantier en branche,
# SANS risque de la committer : chaque data/*.parquet suivi sur origin/main est
# écrasé localement par sa version main puis marqué skip-worktree (invisible
# pour git status / git add). Raison d'être : les jobs CI ne poussent que sur
# main ; une branche de travail ne doit JAMAIS committer de modification de ces
# parquets (binaires : conflit non auto-résoluble au merge, cf. CLAUDE.md
# § Travail en branche). Un NOUVEAU parquet introduit par une branche n'est pas
# concerné (absent d'origin/main) — l'ajouter ici après son merge sur main.
# Usage : bash tools/refresh_data_from_main.sh  (depuis la racine du dépôt)
# Annulation : git update-index --no-skip-worktree data/<fichier>.parquet
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

git fetch origin main

git ls-tree -r --name-only origin/main -- data/ | grep '\.parquet$' | while read -r f; do
    git checkout origin/main -- "$f"
    git update-index --skip-worktree "$f"
    echo "rafraîchi + skip-worktree : $f"
done
