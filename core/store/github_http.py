# -*- coding: utf-8 -*-
"""Lecture SEULE des assets d'un GitHub Release via l'API REST + HTTPS — backend
du DASHBOARD (core/store/base.py), sans dépendre de la CLI `gh`.

Raison d'être : Streamlit Cloud n'a pas `gh` installé. Sur un dépôt PUBLIC, les
assets d'un release sont listables (API REST) et téléchargeables (URL directe)
SANS authentification. L'ÉCRITURE reste au pipeline via `gh`
(core/store/github.py) — ce store lève sur upload/delete.

Un token (GITHUB_TOKEN/GH_TOKEN) est utilisé s'il est présent (relève la limite
de débit de l'API à 5000/h au lieu de 60/h), mais n'est jamais requis."""

import os

import requests

from core.store.base import Asset

_API = "https://api.github.com"


class GitHubReleaseHttpStore:
    """`repo` = « owner/nom », `tag` = release porteur des assets."""

    def __init__(self, repo, tag="data-store", token=None, timeout=30):
        self.repo = repo
        self.tag = tag
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.timeout = timeout
        self._rel_cache = False  # False = pas encore chargé ; None = release absent

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _release(self):
        """JSON du release (métadonnées + assets), ou None si absent (404 →
        magasin pas encore amorcé). Mémoïsé sur l'instance : un cycle
        list()+download() ne fait qu'UN appel API (les instances sont
        éphémères — recréées à chaque rerun —, la détection de changement reste
        donc au pas du rerun)."""
        if self._rel_cache is not False:
            return self._rel_cache
        url = f"{_API}/repos/{self.repo}/releases/tags/{self.tag}"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        if r.status_code == 404:
            self._rel_cache = None
            return None
        r.raise_for_status()
        self._rel_cache = r.json()
        return self._rel_cache

    def list(self, prefix: str = "") -> list[Asset]:
        rel = self._release()
        if not rel:
            return []
        out = []
        for a in rel.get("assets", []):
            name = a.get("name", "")
            if not name.startswith(prefix):
                continue
            # updated_at = jeton de changement (etag) ; size en octets.
            out.append(Asset(name, int(a.get("size", 0)), str(a.get("updated_at", ""))))
        return sorted(out, key=lambda x: x.name)

    def download(self, name: str, dest: str) -> None:
        rel = self._release()
        url = None
        for a in (rel or {}).get("assets", []):
            if a.get("name") == name:
                url = a.get("browser_download_url")
                break
        if not url:
            raise FileNotFoundError(
                f"Asset introuvable sur le release {self.tag} : {name}")
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        # L'URL de download redirige vers un stockage d'objets (non soumis à la
        # limite de débit de l'API) : le gros du trafic n'entame pas le quota.
        with requests.get(url, headers=self._headers(), stream=True,
                          timeout=self.timeout) as r:
            r.raise_for_status()
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            os.replace(tmp, dest)  # remplacement atomique, jamais de fichier partiel

    def upload(self, name: str, src: str) -> None:
        raise NotImplementedError(
            "GitHubReleaseHttpStore est en lecture seule (écriture via gh, "
            "core/store/github.py, côté pipeline).")

    def delete(self, name: str) -> None:
        raise NotImplementedError("GitHubReleaseHttpStore est en lecture seule.")
