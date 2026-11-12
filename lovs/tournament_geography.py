# SPDX-License-Identifier: Apache-2.0
"""Reviewed attribution of SitRep zones that the frozen zone layer does not hold.

A tournament target is a frozen GRID3 COD Health Zones v8.0 polygon. When INSP names
a health zone that layer does not hold, for example one created after the layer was
drawn, its rows would stop every tournament build. A reviewed attribution names the
frozen zone whose polygon holds the new zone's published location, and tournament
resolution and candidate building then count the new zone's cases in that frozen
geography. Only tournament code reads this: the public per-zone tables keep the name
INSP prints, and the centroid index is unchanged.

An attribution must name a zone the frozen index does not hold, point at a frozen
zone in the same province, state its basis and its sources, and carry an explicit UTC
review time. Any other unknown name still stops the build. An attribution reviewed
after a build's evidence cutoff is not available to that build, so a row it would map
stops the build too. ``first_sitrep`` records the first SitRep that printed the name;
it documents the attribution and is not enforced.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from lovs import health_zone_centroids as hzc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ATTRIBUTIONS_PATH = REPO_ROOT / "data" / "model-tournament" / "zone-attributions.json"
SCHEMA_VERSION = "bdbv-model-tournament-zone-attributions/v1"
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_FIELDS = {"province", "sitrep_zone", "zone_id", "first_sitrep", "basis", "limitation", "sources",
           "reviewed_by", "reviewed_at"}


class AttributionError(ValueError):
    """An attribution is malformed, unreviewed or contradicts the frozen index."""


def _utc(value: Any, label: str) -> dt.datetime:
    text = str(value or "")
    if not _UTC_RE.fullmatch(text):
        raise AttributionError(f"{label} must be an ISO UTC timestamp ending in Z")
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def validate(doc: Mapping[str, Any], index: hzc.CentroidIndex) -> list[dict[str, Any]]:
    """Return the attributions after checking each against the frozen index."""
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise AttributionError(f"zone attributions must use {SCHEMA_VERSION}")
    rows = doc.get("attributions")
    if not isinstance(rows, list):
        raise AttributionError("attributions must be a list")
    seen: set[tuple[str, str]] = set()
    out = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _FIELDS:
            raise AttributionError(f"an attribution must have exactly the fields {sorted(_FIELDS)}")
        label = f"{row['province']}/{row['sitrep_zone']}"
        key = (hzc.province_key(row["province"]), hzc.per_zone_canonical_id(str(row["sitrep_zone"])))
        if key in seen:
            raise AttributionError(f"{label} is attributed twice")
        seen.add(key)
        try:
            index.resolve_sitrep_row(row["province"], row["sitrep_zone"])
        except hzc.CentroidError:
            pass
        else:
            raise AttributionError(f"{label} is already a frozen zone; an attribution cannot replace it")
        try:
            zone = index.zone(row["zone_id"])
        except hzc.CentroidError as exc:
            raise AttributionError(f"{label}: {exc}") from exc
        if hzc.province_key(zone["province"]) != key[0]:
            raise AttributionError(f"{label} is attributed to {row['zone_id']}, a zone in another province")
        if isinstance(row["first_sitrep"], bool) or not isinstance(row["first_sitrep"], int):
            raise AttributionError(f"{label}: first_sitrep must be an integer")
        for field in ("basis", "limitation", "reviewed_by"):
            if not str(row[field]).strip():
                raise AttributionError(f"{label}: {field} must be stated")
        sources = row["sources"]
        if not isinstance(sources, list) or not sources or any(
                not isinstance(s, dict) or set(s) != {"url", "finding"}
                or not str(s["url"]).startswith("https://") or not str(s["finding"]).strip()
                for s in sources):
            raise AttributionError(f"{label}: sources must be a non-empty list of {{url, finding}} with https URLs")
        _utc(row["reviewed_at"], f"{label}.reviewed_at")
        out.append(dict(row))
    return out


def load(index: hzc.CentroidIndex, path: pathlib.Path = ATTRIBUTIONS_PATH,
         cutoff: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Reviewed attributions available at ``cutoff``; none when the file is absent.

    Every row is validated. A row reviewed after the cutoff is left out, so a table row
    it would map still stops the build.
    """
    if not path.is_file():
        return []
    rows = validate(json.loads(path.read_text(encoding="utf-8")), index)
    if cutoff is None:
        return rows
    return [row for row in rows if _utc(row["reviewed_at"], "reviewed_at") <= cutoff]


class AttributedIndex:
    """The frozen index, plus reviewed attributions for names it does not hold."""

    def __init__(self, index: hzc.CentroidIndex, attributions: list[dict[str, Any]]):
        self._index = index
        self._by_key = {(hzc.province_key(a["province"]), hzc.per_zone_canonical_id(str(a["sitrep_zone"]))): a
                        for a in attributions}
        self.used: dict[str, dict[str, Any]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._index, name)

    def resolve_sitrep_row(self, province: str, zone_name: str) -> str:
        try:
            return self._index.resolve_sitrep_row(province, zone_name)
        except hzc.CentroidError:
            key = (hzc.province_key(province), hzc.per_zone_canonical_id(str(zone_name)))
            attribution = self._by_key.get(key)
            if attribution is None:
                raise
            self.used[f"{attribution['province']}/{attribution['sitrep_zone']}"] = attribution
            return attribution["zone_id"]
