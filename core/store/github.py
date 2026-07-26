# -*- coding: utf-8 -*-
"""Magasin adossé aux assets d'un GitHub Release — implémentation de DataStore
(core/store/base.py) via la CLI `gh` (déjà présente en CI, authentifiée par
GITHUB_TOKEN avec contents:write).

Les partitions vivent comme assets d'un release DÉDIÉ (`data-store`, séparé des
releases de version vX.Y.Z, jamais mélangé). Repo PUBLIC → les assets ont une
URL de download directe sans authentification : le dashboard (Streamlit Cloud)
lit librement ; seule l'écriture (CI) requiert le jeton.

Tout passe par `gh` : pas de jeton manipulé ici (gh lit son auth de
l'environnement). Le seul point d'appel système est `_run` — isolé pour être
testable sans réseau (mock)."""

import json
import os
import shutil
import subprocess
import tempfile

from core.store.base import Asset


class GitHubReleaseStore:
    """`tag` = release porteur des assets (ex. « data-store »)."""

    def __init__(self, repo: str, tag: str = "data-store", gh_bin: str = "gh"):
        self.repo = repo          # « owner/nom »
        self.tag = tag
        self.gh = gh_bin

    # --------------------------------------------------------------- primitive
    def _run(self, args: list, capture: bool = True) -> str:
        """Appel `gh <args>` (avec --repo). Renvoie stdout. Lève
        CalledProcessError sur échec. SEUL point d'I/O système du module —
        mocké dans les tests."""
        cmd = [self.gh, *args, "--repo", self.repo]
        res = subprocess.run(cmd, check=True, text=True,
                             capture_output=capture)
        return res.stdout if capture else ""

    # --------------------------------------------------------------- release
    def ensure_release(self) -> None:
        """Crée le release porteur s'il n'existe pas encore (idempotent).
        Publié mais NON « Latest » (les releases de version gardent ce statut) ;
        ses notes disent ce qu'il est. Sur repo public, ses assets sont
        téléchargeables sans auth."""
        try:
            self._run(["release", "view", self.tag, "--json", "tagName"])
            return  # existe déjà
        except subprocess.CalledProcessError:
            pass
        self._run([
            "release", "create", self.tag,
            "--title", "Données — partitions parquet (hors git)",
            "--notes", ("Magasin de données du pipeline météo : partitions "
                        "parquet mensuelles servies comme assets (cf. "
                        "docs/DESIGN_sortie_git.md). Ne pas supprimer."),
            "--latest=false",
        ], capture=False)

    # ------------------------------------------------------------- DataStore
    def list(self, prefix: str = "") -> list[Asset]:
        """Assets du release filtrés par préfixe. Release absent → liste vide
        (flux pas encore amorcé, jamais une erreur)."""
        try:
            out = self._run(["release", "view", self.tag, "--json", "assets"])
        except subprocess.CalledProcessError:
            return []
        assets = json.loads(out or "{}").get("assets", []) or []
        res = []
        for a in assets:
            name = a.get("name", "")
            if not name.startswith(prefix):
                continue
            # updatedAt = jeton de changement (etag) ; size en octets.
            res.append(Asset(name, int(a.get("size", 0)),
                             str(a.get("updatedAt", ""))))
        return sorted(res, key=lambda x: x.name)

    def download(self, name: str, dest: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        try:
            self._run(["release", "download", self.tag,
                       "--pattern", name, "--output", dest, "--clobber"],
                      capture=False)
        except subprocess.CalledProcessError as e:
            raise FileNotFoundError(
                f"Asset introuvable sur le release {self.tag} : {name}") from e

    def upload(self, name: str, src: str) -> None:
        """`gh` nomme l'asset d'après le basename du fichier téléversé : on
        téléverse donc une copie temporaire nommée exactement `name`.
        --clobber remplace un asset homonyme en place."""
        self.ensure_release()
        with tempfile.TemporaryDirectory() as tmp:
            staged = os.path.join(tmp, name)
            shutil.copy2(src, staged)
            self._run(["release", "upload", self.tag, staged, "--clobber"],
                      capture=False)

    def delete(self, name: str) -> None:
        """Retire l'asset (rollback/nettoyage). Absent → no-op idempotent."""
        try:
            self._run(["release", "delete-asset", self.tag, name, "--yes"],
                      capture=False)
        except subprocess.CalledProcessError:
            pass
