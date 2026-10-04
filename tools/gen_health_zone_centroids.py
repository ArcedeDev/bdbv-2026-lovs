#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Regenerate data/health-zone-centroids.json from its two pinned inputs.

Each DRC health zone is placed at its population peak: the centre of the single
densest WorldPop pixel inside the zone's GRID3 polygon. rasterio.mask counts a
pixel when its centre is inside the polygon; nodata becomes 0; the largest
remaining pixel wins, and a tie goes to the first pixel in row order (the
northernmost, then the westernmost). The peak is always a real pixel inside the
polygon, whereas a population-weighted mean of a concave zone can fall outside it.
A zone with no populated pixel stops the run rather than falling back to another
method.

This is the same peak rule the website applied on 2026-10-04 (commit 57d0522e),
with one input instead of two: GRID3 polygons for all 519 zones, where the website
used its own overlay polygons for 121 of them. Ten website points differ as a
result, by up to about 24 km.

Inputs (both CC BY 4.0, neither committed here; pass their paths):
  --polygons  GRID3 COD Health Zones v8.0, a simplified GeoJSON export with
              properties {province, zonesante}: grid3_health_zones_v8_0_simplified.geojson
  --raster    WorldPop R2025A constrained 2026 COD 100 m v1:
              cod_pop_2026_CN_100m_R2025A_v1.tif

Each input must match the size and sha256 pinned in INPUTS, or nothing is written.
Requires rasterio and numpy, imported only when the generator runs; the rest of the
repository stays stdlib only.

Usage:
  python3 tools/gen_health_zone_centroids.py --polygons PATH --raster PATH \
      [--out data/health-zone-centroids.json]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lovs import health_zone_centroids as hzc  # noqa: E402

INPUTS = {
    "health_zone_polygons": {
        "title": "GRID3 COD - Health Zones v8.0",
        "version": "v8.0 (December 2025 layer)",
        "publisher": "GRID3 (Geo-Referenced Infrastructure and Demographic Data for Development)",
        "url": "https://grid3.org/geospatial-data-drc",
        "arcgis_item": "https://www.arcgis.com/home/item.html?id=7334c30989ea4b91a6463219f722e1b1",
        "metadata_mirror": "https://geo.btaa.org/catalog/21bee45b-081e-4029-befa-29f7936612dd",
        "file": "grid3_health_zones_v8_0_simplified.geojson",
        "bytes": 935514,
        "sha256": "56996ea70e86abccd1830cdaf19ea64f431dfa4f04bf10bbdbfb549554e53b58",
        "license": "CC-BY-4.0",
        "attribution": "Contains GRID3 COD Health Zones v8.0 data, licensed CC BY 4.0.",
        "note": (
            "A simplified GeoJSON export of the v8.0 feature layer (519 features). The export "
            "query was not recorded, so the file is pinned by hash rather than by a "
            "reproducible download."
        ),
    },
    "population_raster": {
        "title": "WorldPop Global 2015-2030 R2025A constrained population estimates, COD, 2026, 100 m",
        "version": "R2025A v1 (2026 estimate)",
        "publisher": "WorldPop, University of Southampton",
        "url": (
            "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2026/COD/v1/"
            "100m/constrained/cod_pop_2026_CN_100m_R2025A_v1.tif"
        ),
        "dataset_page": "https://www.worldpop.org/geodata/summary?id=73056",
        "doi": "10.5258/SOTON/WP00839",
        "file": "cod_pop_2026_CN_100m_R2025A_v1.tif",
        "bytes": 81620347,
        "sha256": "dd0e9a96352bad14d38700d70d9b5c3806a4db6e338558a1c6878338d84508f4",
        "license": "CC-BY-4.0",
        "attribution": "WorldPop (www.worldpop.org), DOI 10.5258/SOTON/WP00839, licensed CC BY 4.0.",
    },
}

DESCRIPTION = (
    "One point per DRC health zone (GRID3 COD Health Zones v8.0, 519 zones, 26 provinces): "
    "the centre of the densest WorldPop R2025A 2026 100 m pixel inside the zone polygon. "
    "Regenerate with tools/gen_health_zone_centroids.py; ids and SitRep name matching live "
    "in lovs/health_zone_centroids.py."
)


def _check_input(label: str, path: pathlib.Path) -> None:
    pin = INPUTS[label]
    size = path.stat().st_size
    digest = hzc.file_sha256(path)
    if size != pin["bytes"] or digest != pin["sha256"]:
        raise SystemExit(
            f"{label}: {path} is {size} bytes, sha256 {digest}; pinned {pin['bytes']} bytes, "
            f"sha256 {pin['sha256']}. Refusing to generate from an unpinned input."
        )


def build(polygons_path: pathlib.Path, raster_path: pathlib.Path) -> dict:
    import numpy as np
    import rasterio
    import rasterio.mask
    import rasterio.transform

    _check_input("health_zone_polygons", polygons_path)
    _check_input("population_raster", raster_path)
    polygons = json.loads(polygons_path.read_text(encoding="utf-8"))
    zones = []
    with rasterio.open(raster_path) as src:
        for feature in polygons["features"]:
            props = feature["properties"]
            province, name = props["province"], props["zonesante"]
            arr, transform = rasterio.mask.mask(src, [feature["geometry"]], crop=True, filled=True, nodata=0)
            band = arr[0].astype("float64")
            band[band < 0] = 0
            if band.max() <= 0:
                raise SystemExit(f"{province}/{name}: no populated pixel inside the polygon")
            rows, cols = np.nonzero(band)
            xs, ys = rasterio.transform.xy(transform, rows, cols)
            peak = int(np.argmax(band[rows, cols]))
            zone_id, sitrep_key = hzc.assign_ids(province, name)
            zones.append({
                "zone_id": zone_id,
                "sitrep_key": sitrep_key,
                "name": name,
                "province": province,
                "lat": round(float(ys[peak]), 4),
                "lon": round(float(xs[peak]), 4),
                "method": "population_peak",
            })
    zones.sort(key=lambda z: z["zone_id"])
    doc = {
        "schema_version": hzc.SCHEMA_VERSION,
        "description": DESCRIPTION,
        "license": "CC-BY-4.0",
        "attribution": [meta["attribution"] for meta in INPUTS.values()],
        "inputs": INPUTS,
        "method": {
            "point": "population_peak",
            "rule": (
                "rasterio.mask with crop=True, filled=True, nodata=0 (a pixel counts when its centre "
                "is inside the polygon); negative values set to 0; argmax over populated pixels in "
                "row-major order, so ties go to the northernmost, then westernmost pixel"
            ),
            "coordinate_precision_decimal_degrees": 4,
            "crs": "EPSG:4326",
        },
        "zone_count": len(zones),
        "province_count": len({hzc.province_key(z["province"]) for z in zones}),
        "zones": zones,
    }
    hzc.validate(doc)
    return doc


def _write_atomically(path: pathlib.Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--polygons", type=pathlib.Path, required=True)
    parser.add_argument("--raster", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, default=hzc.CENTROIDS_PATH)
    args = parser.parse_args(argv)
    doc = build(args.polygons, args.raster)
    _write_atomically(args.out.resolve(), hzc.render(doc))
    print(f"wrote {doc['zone_count']} zones in {doc['province_count']} provinces -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
