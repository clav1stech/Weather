# -*- coding: utf-8 -*-
"""Magasin adossé à un dossier local — implémentation de DataStore sur le
système de fichiers (core/store/base.py).

Double usage : backend des tests (aucun réseau) et repli de développement local
(pointer le dashboard/pipeline sur un dossier plutôt que sur GitHub). La
sémantique est identique au backend GitHub — upload atomique, remplacement en
place, etag stable tant que le contenu ne change pas — pour que le code au-dessus
ne fasse aucune différence."""

import hashlib
import os
import shutil

from core.store.base import Asset


class LocalDirStore:
    """Assets = fichiers d'un dossier `root` (créé si absent)."""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.root, name)

    @staticmethod
    def _etag(path: str) -> str:
        """Hash de contenu tronqué — jeton de changement stable : identique tant
        que les octets ne changent pas, différent au moindre re-upload."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    def list(self, prefix: str = "") -> list[Asset]:
        out = []
        for name in sorted(os.listdir(self.root)):
            if not name.startswith(prefix):
                continue
            p = self._path(name)
            if os.path.isfile(p):
                out.append(Asset(name, os.path.getsize(p), self._etag(p)))
        return out

    def download(self, name: str, dest: str) -> None:
        src = self._path(name)
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Asset absent du magasin local : {name}")
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        shutil.copy2(src, dest)

    def upload(self, name: str, src: str) -> None:
        # .tmp adjacent puis os.replace : remplacement atomique, jamais d'asset
        # à moitié écrit (même invariant que core/io/atomic.py).
        dst = self._path(name)
        tmp = dst + ".tmp"
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)

    def delete(self, name: str) -> None:
        p = self._path(name)
        if os.path.isfile(p):  # idempotent : absent → no-op
            os.remove(p)
