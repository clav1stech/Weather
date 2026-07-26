# -*- coding: utf-8 -*-
"""Client minimal pour les API WCS de modèles Météo-France.

Le module reste config-agnostique : URL, nom du secret, emprise, échéance et
paramètre sont fournis par l'appelant. Il porte les invariants communs aux
collecteurs neige :

* la clé vient exclusivement de l'environnement ou du ``.env`` indiqué et
  n'est jamais incluse dans un message d'erreur ;
* le catalogue dynamique est la seule source de vérité pour le run publié ;
* un ``GetCoverage`` WCS ne produit qu'un champ 2D : ``time`` est obligatoire ;
* 429 et erreurs serveur sont retentés avec une attente bornée ;
* une réponse qui n'est pas un GRIB est refusée avant tout décodage ;
* le GRIB est décodé directement depuis les octets, sans artefact sur disque.
"""

from __future__ import annotations

import os
import re
import time as time_module
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import requests


RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RUN_RE = re.compile(r"___(\d{4}-\d{2}-\d{2}T\d{2}\.\d{2}\.\d{2}Z)")


@dataclass(frozen=True)
class GribPoint:
    """Valeur et métadonnées de la cellule GRIB la plus proche d'un site."""

    value: float
    latitude: float
    longitude: float
    short_name: str
    units: str
    run_date: datetime
    valid_time: datetime
    step_range: str


def load_api_key(env_name, dotenv_path=None):
    """Retourne le secret exact demandé, sans repli vers une autre clé.

    Le parsing ``.env`` volontairement minimal évite une dépendance uniquement
    pour quatre secrets. Une variable d'environnement déjà définie reste
    prioritaire, comme en GitHub Actions.
    """
    key = os.environ.get(env_name, "").strip()
    if not key and dotenv_path and os.path.exists(dotenv_path):
        with open(dotenv_path, encoding="utf-8-sig") as stream:
            for raw_line in stream:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() == env_name:
                    key = value.strip().strip('"').strip("'")
                    break
    if not key:
        raise SystemExit(
            f"Variable d'environnement {env_name} absente ou vide. "
            "Configurer le secret GitHub Actions ou le fichier .env local."
        )
    return key


def _get(session, url, *, key, params, timeout, attempts=3, sleep_fn=None,
         extra_headers=None):
    """GET avec retries bornés ; le header secret n'apparaît jamais en sortie."""
    sleep_fn = sleep_fn or time_module.sleep
    last_response = None
    headers = {"apikey": key, **(extra_headers or {})}
    for attempt in range(attempts):
        try:
            response = session.get(
                url, params=params, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            if attempt + 1 >= attempts:
                raise RuntimeError(
                    f"Erreur réseau WCS après {attempts} tentative(s): "
                    f"{type(exc).__name__}") from exc
            sleep_fn(2 ** attempt)
            continue
        last_response = response
        if response.status_code not in RETRYABLE_STATUS:
            break
        if attempt + 1 < attempts:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = max(float(retry_after), 1.0)
            except (TypeError, ValueError):
                delay = float(2 ** attempt)
            sleep_fn(min(delay, 30.0))
    if last_response is None:
        raise RuntimeError("Aucune réponse WCS reçue.")
    try:
        last_response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"Erreur HTTP WCS {last_response.status_code} après "
            f"{attempts} tentative(s).") from exc
    return last_response


def get_capabilities(session, url, *, key, timeout=60, attempts=3,
                     sleep_fn=None):
    """Lit le catalogue WCS 2.0.1 et renvoie son XML brut."""
    response = _get(
        session, url, key=key,
        params={"service": "WCS", "version": "2.0.1", "language": "fre"},
        timeout=timeout, attempts=attempts, sleep_fn=sleep_fn)
    return response.content


def coverage_ids(capabilities_xml):
    """Liste les ``CoverageId`` d'un catalogue, indépendamment du namespace."""
    try:
        root = ET.fromstring(capabilities_xml)
    except ET.ParseError as exc:
        raise ValueError("Catalogue WCS XML invalide.") from exc
    return [element.text.strip() for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "CoverageId" and element.text]


def coverage_run_date(coverage_id):
    """Extrait le cycle UTC tz-naïf d'un identifiant Météo-France."""
    match = _RUN_RE.search(str(coverage_id))
    if not match:
        raise ValueError(f"CoverageId sans cycle reconnaissable : {coverage_id}")
    return datetime.strptime(match.group(1), "%Y-%m-%dT%H.%M.%SZ")


def latest_coverage(ids, product, period=None, allowed_run_hours=None):
    """Dernier coverage d'un produit et d'une période de cumul exacts.

    ``product`` est la partie précédant le triple souligné. ``period=None``
    refuse les suffixes de cumul ; une durée comme ``P1D`` ou ``PT1H`` exige
    ce suffixe exact afin de ne jamais confondre cumul horaire et journalier.
    """
    prefix = f"{product}___"
    suffix = f"_{period}" if period else None
    allowed = (None if allowed_run_hours is None
               else frozenset(int(hour) for hour in allowed_run_hours))
    selected = []
    for coverage_id in ids:
        if not coverage_id.startswith(prefix):
            continue
        match = _RUN_RE.search(coverage_id)
        if not match:
            continue
        after_run = coverage_id[match.end():]
        if (period is None and after_run) or (period is not None and after_run != suffix):
            continue
        if allowed is not None and coverage_run_date(coverage_id).hour not in allowed:
            continue
        selected.append(coverage_id)
    if not selected:
        return None
    return max(selected, key=coverage_run_date)


def get_coverage(session, url, *, key, coverage_id, time_value, subsets=(),
                 timeout=60, attempts=3, sleep_fn=None):
    """Télécharge un unique champ 2D GRIB pour une échéance.

    ``time_value`` accepte la seconde relative annoncée par DescribeCoverage
    ou un instant ISO 8601. Les autres dimensions non spatiales (niveau de
    pression/hauteur) doivent être fournies via ``subsets``.
    """
    if time_value is None or str(time_value).strip() == "":
        raise ValueError("subset=time(...) est obligatoire pour GetCoverage.")
    params = [
        ("service", "WCS"),
        ("version", "2.0.1"),
        ("coverageid", coverage_id),
        ("subset", f"time({time_value})"),
        *(("subset", subset) for subset in subsets),
        ("format", "application/wmo-grib"),
    ]
    response = _get(
        session, url, key=key, params=params, timeout=timeout,
        attempts=attempts, sleep_fn=sleep_fn,
        extra_headers={"Accept": "application/octet-stream"})
    payload = response.content
    if not payload.startswith(b"GRIB"):
        content_type = response.headers.get("Content-Type", "inconnu")
        raise ValueError(
            f"Réponse GetCoverage non GRIB ({content_type}, {len(payload)} octets).")
    return payload


def decode_nearest_point(payload, target_lat, target_lon):
    """Décode un GRIB 2D et extrait la cellule la plus proche du site."""
    return decode_nearest_points(
        payload, {"target": (target_lat, target_lon)})["target"]


def decode_nearest_points(payload, targets):
    """Décode une fois un GRIB et extrait la cellule de plusieurs sites.

    ``targets`` est un mapping ``nom -> (latitude, longitude)``. Mutualiser le
    décodage est important pour les flux déterministes : la même petite grille
    WCS couvre village et sommet, sans requête ni lecture GRIB supplémentaire.
    """
    try:
        import eccodes
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement
        raise RuntimeError(
            "Le paquet eccodes est requis pour décoder les GRIB Météo-France.") from exc

    grib = None
    try:
        grib = eccodes.codes_new_from_message(payload)
        if grib is None:
            raise ValueError("GRIB vide ou illisible.")
        latitudes = np.asarray(eccodes.codes_get_array(grib, "latitudes"), dtype=float)
        longitudes = np.asarray(eccodes.codes_get_array(grib, "longitudes"), dtype=float)
        values = np.asarray(eccodes.codes_get_values(grib), dtype=float)
        if not (latitudes.size == longitudes.size == values.size):
            raise ValueError("Coordonnées et valeurs GRIB de tailles incohérentes.")
        run_date = datetime.strptime(
            f"{eccodes.codes_get(grib, 'dataDate'):08d}"
            f"{eccodes.codes_get(grib, 'dataTime'):04d}", "%Y%m%d%H%M")
        valid_date = int(eccodes.codes_get(grib, "validityDate"))
        valid_time = int(eccodes.codes_get(grib, "validityTime"))
        decoded = {}
        for name, (target_lat, target_lon) in targets.items():
            distance = (latitudes - float(target_lat)) ** 2 \
                + (longitudes - float(target_lon)) ** 2
            index = int(np.nanargmin(distance))
            decoded[name] = GribPoint(
                value=float(values[index]), latitude=float(latitudes[index]),
                longitude=float(longitudes[index]),
                short_name=str(eccodes.codes_get(grib, "shortName")),
                units=str(eccodes.codes_get(grib, "units")), run_date=run_date,
                valid_time=datetime.strptime(
                    f"{valid_date:08d}{valid_time:04d}", "%Y%m%d%H%M"),
                step_range=str(eccodes.codes_get(grib, "stepRange")),
            )
        return decoded
    finally:
        if grib is not None:
            eccodes.codes_release(grib)
