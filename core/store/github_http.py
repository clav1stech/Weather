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
import time

import requests

from core.store.base import Asset

_API = "https://api.github.com"

# Cache PROCESSUS de la métadonnée du release, clé (repo, tag), avec TTL court.
# Tous les flux d'un même rerun (flux principal + annexes, chacun instancie son
# propre store) partagent ainsi UN SEUL appel API `releases/tags` — sans quoi la
# limite anonyme de 60 req/h serait vite atteinte (≈5 flux × pages). Les
# téléchargements d'assets, eux, ne comptent pas dans ce quota. TTL court : un
# changement de release est détecté au pas du rerun + TTL (données au rythme ≥
# horaire, aucune urgence à la seconde).
_REL_CACHE = {}          # (repo, tag) -> (t_monotonic, json_or_None)
_REL_TTL = 30.0


def reset_release_cache():
    """Vide la mémoïsation du release — à appeler quand l'utilisateur demande
    explicitement des données fraîches (bouton « Rafraîchir »), le TTL étant
    calibré pour le rythme automatique et non pour une demande manuelle."""
    _REL_CACHE.clear()


class GitHubReleaseHttpStore:
    """`repo` = « owner/nom », `tag` = release porteur des assets."""

    def __init__(self, repo, tag="data-store", token=None, timeout=30):
        self.repo = repo
        self.tag = tag
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.timeout = timeout

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _release(self):
        """JSON du release (métadonnées + assets), ou None si absent (404 →
        magasin pas encore amorcé). Mémoïsé au niveau PROCESSUS avec TTL : tous
        les flux d'un rerun ne déclenchent qu'un appel API, préservant le quota
        anonyme."""
        key = (self.repo, self.tag)
        hit = _REL_CACHE.get(key)
        if hit is not None and time.monotonic() - hit[0] < _REL_TTL:
            return hit[1]
        url = f"{_API}/repos/{self.repo}/releases/tags/{self.tag}"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        data = None if r.status_code == 404 else (r.raise_for_status() or r.json())
        _REL_CACHE[key] = (time.monotonic(), data)
        return data

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
