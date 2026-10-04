# SPDX-License-Identifier: Apache-2.0
"""Generator-owned point for every DRC health zone, and the name index onto it.

``data/health-zone-centroids.json`` holds one point per health zone in GRID3 COD
Health Zones v8.0 (519 zones, 26 provinces). The point is the zone's population
peak: the centre of the densest WorldPop R2025A 2026 pixel inside the zone's
polygon. ``tools/gen_health_zone_centroids.py`` computes the file offline from two
hash-pinned inputs; this module is the stdlib half that every runtime caller uses.

This module is the single home for two rules.

ZONE IDS. ``sitrep_key`` is the id ``lovs.sitrep_overlays.per_zone_canonical_id``
gives the zone's name, so a SitRep table row and a GRID3 polygon meet on the same
key. Two GRID3 spellings differ from the SitRep spelling and are declared in
``SITREP_KEY_OVERRIDES``. GRID3 repeats two names across provinces (Bili, Lubunga),
so ``sitrep_key`` alone is not unique; ``zone_id`` is, and ``ZONE_ID_OVERRIDES``
names each repeated zone explicitly instead of deriving a suffix at run time.

MATCHING. A SitRep row is matched on (province, sitrep_key), never on name alone.
An unknown row raises: a spelling the index does not know is a review item, not a
guess.

Stdlib only. No clock, no network.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from lovs.sitrep_overlays import per_zone_canonical_id

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CENTROIDS_PATH = REPO_ROOT / "data" / "health-zone-centroids.json"
SCHEMA_VERSION = "lovs-health-zone-centroids/v1"

EXPECTED_ZONE_COUNT = 519
EXPECTED_PROVINCE_COUNT = 26

# Generous DRC bounding box (the national extent is about 13.46S to 5.39N and
# 12.2E to 31.31E). A point outside it is a projection or column-order error.
DRC_LAT_RANGE = (-13.6, 5.5)
DRC_LON_RANGE = (12.0, 31.5)

# Mean Earth radius (IUGG), km.
EARTH_RADIUS_KM = 6371.0088

# (GRID3 province, GRID3 zonesante) -> the id a SitRep row for that zone produces.
SITREP_KEY_OVERRIDES: dict[tuple[str, str], str] = {
    # GRID3 writes one word; SitRep tables write "Wanie-Rukula".
    ("Tshopo", "Wanierukula"): "wanie-rukula",
    # SitRep tables write "Makiso-Kisangani", which sitrep_overlays aliases to
    # the older gazetteer id; GRID3 writes "Makiso Kisangani".
    ("Tshopo", "Makiso Kisangani"): "makiso-kisangani-cod",
}

# (GRID3 province, GRID3 zonesante) -> zone_id, for names GRID3 repeats across
# provinces. Lubunga (Tshopo) keeps the bare id LOVS has used since it was first
# affected; every other repeated zone carries its province.
ZONE_ID_OVERRIDES: dict[tuple[str, str], str] = {
    ("Tshopo", "Lubunga"): "lubunga",
    ("Kasaï-Central", "Lubunga"): "lubunga-kasai-central",
    ("Bas-Uele", "Bili"): "bili-bas-uele",
    ("Nord-Ubangi", "Bili"): "bili-nord-ubangi",
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ZONE_FIELDS = ("zone_id", "sitrep_key", "name", "province", "lat", "lon", "method")


class CentroidError(ValueError):
    """Raised when the centroid file or a zone lookup violates its contract."""


def province_key(province: str) -> str:
    """Accent- and punctuation-free province key ("Kasaï-Central" == "Kasai Central")."""
    folded = unicodedata.normalize("NFKD", str(province or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def assign_ids(province: str, name: str) -> tuple[str, str]:
    """Return ``(zone_id, sitrep_key)`` for one GRID3 zone."""
    sitrep_key = SITREP_KEY_OVERRIDES.get((province, name)) or per_zone_canonical_id(name)
    zone_id = ZONE_ID_OVERRIDES.get((province, name)) or sitrep_key
    for value, label in ((zone_id, "zone_id"), (sitrep_key, "sitrep_key")):
        if not _ID_RE.fullmatch(value):
            raise CentroidError(f"{province}/{name}: {label} {value!r} is not a lowercase slug")
    return zone_id, sitrep_key


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two WGS84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def render(doc: Mapping[str, Any]) -> str:
    """The one serialization of the centroid file: sorted keys, ASCII, newline-terminated."""
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(doc: Mapping[str, Any]) -> None:
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise CentroidError(f"schema_version must be {SCHEMA_VERSION}")
    inputs = doc.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {"health_zone_polygons", "population_raster"}:
        raise CentroidError("inputs must name health_zone_polygons and population_raster")
    for label, meta in inputs.items():
        if not isinstance(meta, dict):
            raise CentroidError(f"inputs.{label} must be an object")
        for field in ("title", "version", "publisher", "url", "file", "bytes", "sha256", "license"):
            if not meta.get(field):
                raise CentroidError(f"inputs.{label}.{field} is required")
        if not re.fullmatch(r"[0-9a-f]{64}", str(meta["sha256"])):
            raise CentroidError(f"inputs.{label}.sha256 must be a lowercase SHA-256")
        if meta["license"] != "CC-BY-4.0":
            raise CentroidError(f"inputs.{label}.license must be CC-BY-4.0")
    zones = doc.get("zones")
    if not isinstance(zones, list) or len(zones) != EXPECTED_ZONE_COUNT:
        raise CentroidError(f"zones must list exactly {EXPECTED_ZONE_COUNT} health zones")
    if doc.get("zone_count") != len(zones):
        raise CentroidError("zone_count does not match zones")
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    provinces: set[str] = set()
    previous = ""
    for zone in zones:
        if not isinstance(zone, dict) or tuple(sorted(zone)) != tuple(sorted(_ZONE_FIELDS)):
            raise CentroidError(f"zone entries must carry exactly {list(_ZONE_FIELDS)}")
        zone_id = str(zone["zone_id"])
        if (zone_id, str(zone["sitrep_key"])) != assign_ids(zone["province"], zone["name"]):
            raise CentroidError(f"{zone_id}: ids do not follow assign_ids")
        if zone_id in seen_ids:
            raise CentroidError(f"duplicate zone_id {zone_id}")
        if zone_id <= previous:
            raise CentroidError("zones must be sorted by zone_id")
        previous = zone_id
        seen_ids.add(zone_id)
        match_key = (province_key(zone["province"]), str(zone["sitrep_key"]))
        if match_key in seen_keys:
            raise CentroidError(f"duplicate (province, sitrep_key) {match_key}")
        seen_keys.add(match_key)
        provinces.add(match_key[0])
        lat, lon = zone["lat"], zone["lon"]
        for value in (lat, lon):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise CentroidError(f"{zone_id}: lat/lon must be finite numbers")
        if not (DRC_LAT_RANGE[0] <= lat <= DRC_LAT_RANGE[1] and DRC_LON_RANGE[0] <= lon <= DRC_LON_RANGE[1]):
            raise CentroidError(f"{zone_id}: point ({lat}, {lon}) is outside the DRC bounding box")
        if zone["method"] != "population_peak":
            raise CentroidError(f"{zone_id}: method must be population_peak")
    if len(provinces) != EXPECTED_PROVINCE_COUNT or doc.get("province_count") != len(provinces):
        raise CentroidError(f"expected {EXPECTED_PROVINCE_COUNT} provinces")


def load(path: pathlib.Path = CENTROIDS_PATH) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CentroidError(f"centroid file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CentroidError(f"centroid file is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise CentroidError("centroid file must be a JSON object")
    validate(doc)
    return doc


class CentroidIndex:
    """Lookups over one validated centroid document."""

    __slots__ = ("_by_id", "_by_match")

    def __init__(self, doc: Mapping[str, Any]) -> None:
        validate(doc)
        self._by_id = {str(z["zone_id"]): dict(z) for z in doc["zones"]}
        self._by_match = {
            (province_key(z["province"]), str(z["sitrep_key"])): str(z["zone_id"])
            for z in doc["zones"]
        }

    @classmethod
    def load_default(cls, path: pathlib.Path = CENTROIDS_PATH) -> "CentroidIndex":
        return cls(load(path))

    def zone_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_id))

    def zone(self, zone_id: str) -> dict[str, Any]:
        try:
            return dict(self._by_id[zone_id])
        except KeyError as exc:
            raise CentroidError(f"unknown zone_id {zone_id!r}") from exc

    def zones_in_provinces(self, provinces: Iterable[str]) -> tuple[str, ...]:
        wanted = {province_key(p) for p in provinces}
        return tuple(sorted(z for z, row in self._by_id.items() if province_key(row["province"]) in wanted))

    def resolve_sitrep_row(self, province: str, zone_name: str) -> str:
        """Map one SitRep health-zone row to its zone_id, or raise."""
        key = (province_key(province), per_zone_canonical_id(str(zone_name)))
        zone_id = self._by_match.get(key)
        if zone_id is None:
            raise CentroidError(
                f"SitRep row {province}/{zone_name} (key {key[1]!r}) matches no GRID3 zone in that "
                "province; declare the spelling in SITREP_KEY_OVERRIDES after review"
            )
        return zone_id

    def distance_km(self, zone_a: str, zone_b: str) -> float:
        a, b = self.zone(zone_a), self.zone(zone_b)
        return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
