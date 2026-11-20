# SPDX-License-Identifier: Apache-2.0
"""The week's vintage: what the build may condition on, and the cross-check that guards it.

History to SitRep 177 (data 2026-11-07) comes from LOVS's own public extract
data/operational-series-2026-11-07.json, numbers only; later days come from the reviewed
promotions (DESIGN.md 2.8, R3-I1). Before anything is built, every promotion is compared
with the extract and with its evidence packet (row_from_packet) on every shared numeric
field; one difference fails the build. Files are created exclusively, never replaced.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Sequence

from lovs.forecast.weekly import promotion_rows, spec

EXTRACT_RELATIVE = spec.EXTRACT_SOURCE_PATH
EXTRACT_SHA256 = "b9a4be20c2d15df673cdacc5f0c214a8b4ea2d52a25e231d60462b89a8e66748"
EXTRACT_LAST_SITREP = 177


class CrossCheckError(RuntimeError):
    """A promotion disagrees with the extract or with its packet: build nothing."""


def load_extract(tree) -> list[dict]:
    raw = tree.read(EXTRACT_RELATIVE)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXTRACT_SHA256:
        raise CrossCheckError(f"{EXTRACT_RELATIVE} sha256 {digest} is not the pinned {EXTRACT_SHA256}")
    return json.loads(raw)["rows"]


def _day(text) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _sitrep(value) -> int:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else 0


def usable(row: dict, R: dt.date, cutoff: dt.date) -> bool:
    """Data on or before the cutoff, first published before R."""
    data, pub = _day(row.get("data_as_of")), _day(row.get("published_at"))
    return data is not None and pub is not None and data <= cutoff and pub < R


def build_vintage(extract_rows: Sequence[dict], promotions: Sequence[dict], R: dt.date, cutoff: dt.date,
                  *, extract_last_sitrep: int = EXTRACT_LAST_SITREP) -> list[dict]:
    flags_by_sitrep = {int(p["sitrep_number"]): promotion_rows.basis_flags(p) for p in promotions}
    rows: list[dict] = []
    for r in extract_rows:
        if _sitrep(r.get("sitrep")) > extract_last_sitrep or not r.get("ready_for_model_use", True):
            continue
        row = promotion_rows.row_from_extract(r)
        row["flags"] = flags_by_sitrep.get(_sitrep(r.get("sitrep")), [])
        rows.append(row)
    for p in promotions:
        if int(p["sitrep_number"]) > extract_last_sitrep:
            rows.append(promotion_rows.row_from_promotion(p))
    rows = [r for r in rows if usable(r, R, cutoff)]
    rows.sort(key=lambda r: (r["data_as_of"], _sitrep(r["sitrep"])))
    out, seen = [], set()
    for r in rows:
        # Two SitReps on one data day: keep the first, as opsforecast.series does.
        if r["data_as_of"] in seen:
            continue
        seen.add(r["data_as_of"])
        out.append(r)
    return out


def cross_check(promotions: Sequence[dict], packets: Sequence[dict], extract_rows: Sequence[dict]) -> dict:
    """Compare every promotion with its packet and with the extract, field by field."""
    from tools.extract_operational_series import row_from_packet

    by_packet: dict[int, dict] = {}
    for doc in packets:
        by_packet.setdefault(_sitrep(doc.get("sitrep_label")), doc)
    by_extract: dict[int, dict] = {}
    for r in extract_rows:
        by_extract.setdefault(_sitrep(r.get("sitrep")), r)
    report = {"packet_comparisons": 0, "extract_comparisons": 0, "packet_sitreps": 0, "extract_sitreps": 0,
              "mismatches": []}
    for promo in promotions:
        n = int(promo["sitrep_number"])
        mine = promotion_rows.row_from_promotion(promo)
        for against, other in (("packet", by_packet.get(n)), ("extract", by_extract.get(n))):
            if other is None:
                continue
            theirs = row_from_packet(other) if against == "packet" else other
            shared = _shared_count(mine, theirs)
            report[f"{against}_comparisons"] += shared
            report[f"{against}_sitreps"] += 1
            for path in promotion_rows.compare_rows(mine, theirs):
                report["mismatches"].append({"sitrep": str(n).zfill(3), "against": against, "field": path})
    return report


def _shared_count(a: dict, b: dict) -> int:
    n = sum(1 for k in spec.VINTAGE_FIELDS if a.get(k) is not None and b.get(k) is not None)
    pa, pb = a.get("province_split") or {}, b.get("province_split") or {}
    for name in set(pa) & set(pb):
        n += sum(1 for k in spec.PROVINCE_SPLIT_FIELDS
                 if (pa[name] or {}).get(k) is not None and (pb[name] or {}).get(k) is not None)
    return n


def require_cross_check(promotions, packets, extract_rows) -> dict:
    report = cross_check(promotions, packets, extract_rows)
    if report["mismatches"]:
        listed = ", ".join(f"SR{m['sitrep']} {m['field']} ({m['against']})" for m in report["mismatches"][:20])
        raise CrossCheckError(f"{len(report['mismatches'])} cross-check difference(s): {listed}")
    return report


def write_exclusive(path: Path, data: bytes) -> None:
    """Create `path` with `data`; an existing file is an error, never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def row_key(row: dict) -> tuple[str, str]:
    return (str(row["sitrep"]), str(row["data_as_of"]))


def delta(previous: Iterable[dict] | None, rows: Iterable[dict]) -> list[dict]:
    """Rows new or changed since the previous week's vintage; the whole vintage in week 1."""
    before = {row_key(r): r for r in previous or []}
    return [r for r in rows if before.get(row_key(r)) != r]


def reconstruct(deltas: Iterable[Iterable[dict]]) -> list[dict]:
    """Apply vintage deltas in registration order; the newest version of a row wins."""
    rows: dict[tuple[str, str], dict] = {}
    for chunk in deltas:
        for r in chunk:
            rows[row_key(r)] = r
    return sorted(rows.values(), key=lambda r: (r["data_as_of"], _sitrep(r["sitrep"])))


def jsonl_bytes(records: Iterable[dict]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
                   for r in records).encode("ascii")
