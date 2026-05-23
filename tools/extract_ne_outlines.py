#!/usr/bin/env python3
"""Extract simplified country outlines and lake polygons for the BDBV-2026
viewport from Natural Earth 1:10m public-domain GeoJSON.

Source data:
  - github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_10m_admin_0_countries.geojson
  - github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_10m_lakes.geojson

Natural Earth is public domain (see naturalearthdata.com/about/terms-of-use/).
We do not redistribute the upstream GeoJSON; we extract only the features
clipped to our map viewport and Douglas-Peucker simplified for inline SVG.

Output:
  - data/natural_earth_outlines.json (committed to repo, mirrored to the
    companion website by tools/sync_to_website.py)

Stdlib only. Tested on Python 3.11+.

Usage (one-time, requires network):
  curl -L -o /tmp/ne_10m_countries.geojson \\
    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson
  curl -L -o /tmp/ne_10m_lakes.geojson \\
    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_lakes.geojson
  python tools/extract_ne_outlines.py

The two upstream files are NOT committed; the extracted JSON IS committed.
"""
from __future__ import annotations

import json
import math
import os
import pathlib


# Viewport for the map visual. Lat extended south to -2.5° to include Goma
# (North Kivu, lat -1.68°) which now appears as a spillover-case site.
LON_MIN, LON_MAX = 28.0, 33.5
LAT_MIN, LAT_MAX = -2.5, 3.5
BUFFER_DEG = 0.5

CLIP_LON_MIN = LON_MIN - BUFFER_DEG
CLIP_LON_MAX = LON_MAX + BUFFER_DEG
CLIP_LAT_MIN = LAT_MIN - BUFFER_DEG
CLIP_LAT_MAX = LAT_MAX + BUFFER_DEG

# Douglas-Peucker tolerances in degrees. Borders are simpler than lakes,
# so we use a slightly larger tolerance for borders. Both values are well
# below the visual resolution of the map at our zoom level (~5° viewport
# over ~700px = ~0.007° per pixel).
BORDER_TOLERANCE_DEG = 0.025
LAKE_TOLERANCE_DEG = 0.01

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = DATA_DIR / "natural_earth_outlines.json"
COUNTRIES_PATH = pathlib.Path(os.environ.get("NE_COUNTRIES_JSON", "/tmp/ne_10m_countries.geojson"))
LAKES_PATH = pathlib.Path(os.environ.get("NE_LAKES_JSON", "/tmp/ne_10m_lakes.geojson"))


def in_viewport(lon: float, lat: float) -> bool:
    return CLIP_LON_MIN <= lon <= CLIP_LON_MAX and CLIP_LAT_MIN <= lat <= CLIP_LAT_MAX


def perp_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    fx, fy = ax + t * dx, ay + t * dy
    return math.hypot(px - fx, py - fy)


def douglas_peucker(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = True
    keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        max_d = 0.0
        max_i = -1
        for i in range(first + 1, last):
            d = perp_distance(points[i], points[first], points[last])
            if d > max_d:
                max_d = d
                max_i = i
        if max_d > tolerance and max_i >= 0:
            keep[max_i] = True
            stack.append((first, max_i))
            stack.append((max_i, last))
    return [p for p, k in zip(points, keep) if k]


def clip_to_viewport(ring: list[list[float]]) -> list[list[tuple[float, float]]]:
    """Walk a closed ring, split into segments wherever it exits the viewport."""
    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for lon, lat in ring:
        if in_viewport(lon, lat):
            current.append((float(lon), float(lat)))
        else:
            if current:
                segments.append(current)
                current = []
    if current:
        segments.append(current)
    return segments


def to_pair(p: tuple[float, float]) -> list[float]:
    return [round(p[0], 4), round(p[1], 4)]


def main() -> int:
    if not COUNTRIES_PATH.exists():
        print(
            f"Missing {COUNTRIES_PATH}.\n"
            f"Run: curl -L -o {COUNTRIES_PATH} \\\n"
            f"  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson",
            flush=True,
        )
        return 2
    if not LAKES_PATH.exists():
        print(
            f"Missing {LAKES_PATH}.\n"
            f"Run: curl -L -o {LAKES_PATH} \\\n"
            f"  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_lakes.geojson",
            flush=True,
        )
        return 2

    with COUNTRIES_PATH.open() as fh:
        countries = json.load(fh)
    with LAKES_PATH.open() as fh:
        lakes = json.load(fh)

    drc_segments: list[list[list[float]]] = []
    uga_segments: list[list[list[float]]] = []

    for feat in countries["features"]:
        name = feat["properties"].get("ADMIN")
        if name not in ("Democratic Republic of the Congo", "Uganda"):
            continue
        geom = feat["geometry"]
        polygons = (
            geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        )
        for poly in polygons:
            outer_ring = poly[0]
            for seg in clip_to_viewport(outer_ring):
                if len(seg) < 2:
                    continue
                simplified = douglas_peucker(seg, tolerance=BORDER_TOLERANCE_DEG)
                if len(simplified) < 2:
                    continue
                target = (
                    drc_segments
                    if name == "Democratic Republic of the Congo"
                    else uga_segments
                )
                target.append([to_pair(p) for p in simplified])

    lake_rings: dict[str, list[list[float]]] = {}
    for feat in lakes["features"]:
        name = feat["properties"].get("name") or ""
        slug = None
        if name == "Lake Albert":
            slug = "albert"
        elif name == "Lake Edward":
            slug = "edward"
        elif name == "Lake Kivu":
            slug = "kivu"
        if not slug:
            continue
        ring = [(float(p[0]), float(p[1])) for p in feat["geometry"]["coordinates"][0]]
        simplified = douglas_peucker(ring, tolerance=LAKE_TOLERANCE_DEG)
        if simplified[0] != simplified[-1]:
            simplified.append(simplified[0])
        lake_rings[slug] = [to_pair(p) for p in simplified]

    output = {
        "_meta": {
            "source": "Natural Earth 1:10m, github.com/nvkelso/natural-earth-vector",
            "license": "Public domain (see naturalearthdata.com/about/terms-of-use/)",
            "viewport_lon": [LON_MIN, LON_MAX],
            "viewport_lat": [LAT_MIN, LAT_MAX],
            "clip_buffer_deg": BUFFER_DEG,
            "border_tolerance_deg": BORDER_TOLERANCE_DEG,
            "lake_tolerance_deg": LAKE_TOLERANCE_DEG,
            "generated_by": "tools/extract_ne_outlines.py",
        },
        "countries": {
            "cod": {
                "name": "Democratic Republic of the Congo",
                "iso_a3": "COD",
                "outline_segments": drc_segments,
            },
            "uga": {
                "name": "Uganda",
                "iso_a3": "UGA",
                "outline_segments": uga_segments,
            },
        },
        "lakes": [
            {"slug": slug, "name": f"Lake {slug.title()}", "ring": ring}
            for slug, ring in sorted(lake_rings.items())
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUT_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    os.replace(tmp_path, OUT_PATH)

    print(
        f"DRC segments: {len(drc_segments)} ({sum(len(s) for s in drc_segments)} verts)\n"
        f"Uganda segments: {len(uga_segments)} ({sum(len(s) for s in uga_segments)} verts)\n"
        f"Lake rings: {', '.join(f'{k}={len(v)}' for k, v in lake_rings.items())}\n"
        f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
