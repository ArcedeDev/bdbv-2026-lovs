#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract the per-health-zone table (Table 2) from a standard-format SitRep.

The input is the layout-preserving text of the edition's pages (for example from
`pdftotext -layout`), one string per page. Table 2 starts on the second page after
the "Tableau 2." caption and continues on the third; when it runs further, join the
continuation pages into the third with a form feed before calling.

The output is a draft for the reviewed promotion's `health_zone_table`. It is used
only after a visual review of the primary pages; it never promotes anything by
itself and reads no file under data/.

  python3 tools/extract_sitrep_zone_table.py --text sitrep.txt --date 2026-11-12

The row counts are derived from the parsed rows, so an edition that lists a new
zone needs no change here. The one check is arithmetic: the named zone rows plus
the unallocated row must reconcile to the printed national totals, for confirmed
cases and for confirmed deaths. A table that does not reconcile is refused.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import unicodedata

PROVINCES = (
    "Ituri", "Nord-Kivu", "Haut-Uele", "Tshopo", "Sud-Kivu", "Bas-Uele", "Sud-Ubangi", "Nord-Ubangi",
)
_PROVINCE_SPELLINGS = {"Bas Uele": "Bas-Uele", "Sud Ubangi": "Sud-Ubangi", "Nord Ubangi": "Nord-Ubangi"}
# The row the publisher prints for figures not yet assigned to a zone.
UNALLOCATED_ROW = "A ventiler"
# Zone names the layout wraps onto a second line, in the order the table prints them.
_WRAPPED_ZONE_NAMES = ("Boma Mangbetu", "Makiso-Kisangani", "Wanie-Rukula")
_WRAPPED_ZONE_STEMS = {"Boma": "Boma Mangbetu", "Makiso-": "Makiso-Kisangani", "Wanie-": "Wanie-Rukula"}
_ROW = re.compile(r"^\s*(.*?)\s*(NA|\d+)\s+(\d+)\s+(NA|\d+,\d+%)\s+(.*)$")
_DAY_COLUMNS = ("new_confirmed_24h", "community_deaths_24h", "cte_deaths_24h", "total_confirmed_deaths_24h")


class ZoneTableError(ValueError):
    """The table could not be read, or it does not reconcile to its national totals."""


def canonical(label: str) -> str:
    """Strip accents and normalise the province spellings the editions vary."""
    plain = "".join(c for c in unicodedata.normalize("NFD", label) if not unicodedata.combining(c))
    return _PROVINCE_SPELLINGS.get(plain, plain)


def number(cell: str) -> int | float | None:
    """A printed cell as a number; NA, ND and blank stay null."""
    cell = cell.strip().replace(" ", "").replace("%", "").replace(",", ".")
    if cell in ("NA", "ND", ""):
        return None
    return float(cell) if "." in cell else int(cell)


def _tail_numbers(line: str, start: int) -> list[tuple[int, int]]:
    return [(m.start(), int(m[0])) for m in re.finditer(r"\d+", line) if m.start() >= start]


def reconcile(rows: list[dict], total: dict | None) -> dict:
    """Counts derived from the parsed rows, and the one check the extractor makes."""
    if total is None:
        raise ZoneTableError("Table 2 has no Total row")
    named = [row for row in rows if row["zone"] != UNALLOCATED_ROW]
    unallocated = [row for row in rows if row["zone"] == UNALLOCATED_ROW]
    if len(unallocated) > 1:
        raise ZoneTableError(f"Table 2 prints {len(unallocated)} unallocated rows")
    unallocated_row = unallocated[0] if unallocated else {}
    sums = {
        "confirmed": sum(row["confirmed"] or 0 for row in named),
        "confirmed_deaths": sum(row["confirmed_deaths"] or 0 for row in named),
    }
    for field, named_sum in sums.items():
        unallocated_value = unallocated_row.get(field) or 0
        if named_sum + unallocated_value != total[field]:
            raise ZoneTableError(
                f"Table 2 does not reconcile for {field}: {len(named)} named zone rows sum to {named_sum}, "
                f"the unallocated row carries {unallocated_value}, and the national total is {total[field]}"
            )
    return {
        "named_zone_confirmed_sum": sums["confirmed"],
        "named_zone_confirmed_deaths": sums["confirmed_deaths"],
        "national_confirmed_total": total["confirmed"],
        "national_confirmed_deaths_total": total["confirmed_deaths"],
        "unventilated_confirmed_deaths": unallocated_row.get("confirmed_deaths") or 0,
        "printed_zone_row_count": len(rows),
        "named_zone_row_count": len(named),
        "notified_24h_confirmed": total["new_confirmed_24h"],
        "notified_24h_confirmed_deaths": total["total_confirmed_deaths_24h"],
    }


def standard_tables(pages: list[str], day: str) -> dict:
    """Parse Table 2 from the page texts of one standard-format edition."""
    if len(pages) < 3 or "Tableau 2." not in pages[1]:
        raise ZoneTableError("expected the Tableau 2. caption on the second page and a third page")
    text = pages[1].split("Tableau 2.")[1] + "\f" + pages[2]
    rows: list[dict] = []
    day_rows: list[dict] = []
    provinces: list[dict] = []
    province = None
    bounds: list[int] | None = None
    wrapped = iter(_WRAPPED_ZONE_NAMES)
    total = None
    for page_number, part in enumerate(text.split("\f"), 2):
        # Each page sets its own column anchors from its first province row.
        for candidate in part.splitlines():
            match = _ROW.match(candidate)
            if match and canonical(match[1].strip()) in PROVINCES:
                anchors = [position for position, _ in _tail_numbers(candidate, match.start(5))]
                if len(anchors) == 4:
                    bounds = anchors
                    break
        for line in part.splitlines():
            match = _ROW.match(line)
            if not match:
                continue
            label = canonical(match[1].strip())
            if not label:
                label = next(wrapped, None)
                if label is None:
                    raise ZoneTableError(f"page {page_number}: an unnamed row has no known wrapped zone name")
            label = _WRAPPED_ZONE_STEMS.get(label, label)
            base = {
                "confirmed": number(match[2]),
                "confirmed_deaths": number(match[3]),
                "cfr_percent": number(match[4]),
                "source_page": page_number,
            }
            tail = _tail_numbers(line, match.start(5))
            is_province = label in PROVINCES and len(tail) == 4
            if is_province:
                province = label
                bounds = [position for position, _ in tail]
            if bounds is None:
                raise ZoneTableError("missing province column anchors")
            values: list[int | None] = [None] * 4
            for position, value in tail:
                values[min(range(4), key=lambda i: abs(position - bounds[i]))] = value
            daily = dict(zip(_DAY_COLUMNS, values))
            if label == "Total":
                total = {**base, **daily}
            elif is_province:
                provinces.append({"province": province, **base, **daily})
            else:
                rows.append({"province": province, **base, "zone": label})
                day_rows.append({"province": province, "zone": label, **daily, "source_page": page_number})
    reconciliation = reconcile(rows, total)
    reconciliation["province_arithmetic"] = []
    for row in provinces:
        name = row["province"]
        zone_rows = [r for r in rows if r["province"] == name and r["zone"] != UNALLOCATED_ROW]
        zone_days = [r for r in day_rows if r["province"] == name and r["zone"] != UNALLOCATED_ROW]
        arithmetic = {
            "province": name,
            "named_zone_cases_sum": sum(r["confirmed"] or 0 for r in zone_rows),
            "named_zone_deaths_sum": sum(r["confirmed_deaths"] or 0 for r in zone_rows),
        }
        for key, column in zip(("daily_cases_sum", "daily_community_sum", "daily_cte_sum", "daily_total_sum"), _DAY_COLUMNS):
            arithmetic[key] = sum(r[column] or 0 for r in zone_days)
        reconciliation["province_arithmetic"].append(arithmetic)
    return {
        "basis": "Literal original Table 2; blank/NA remain null; A ventiler is not a named zone.",
        "date": day,
        "zone_attribution_status": "published",
        "rows": rows,
        "day_rows": day_rows,
        "province_totals": provinces,
        "reconciliation": reconciliation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", type=pathlib.Path, required=True,
                        help="Layout-preserving text of the edition, pages separated by form feeds.")
    parser.add_argument("--date", required=True, help="Data date of the edition, YYYY-MM-DD.")
    args = parser.parse_args(argv)
    pages = [page for page in args.text.read_text(encoding="utf-8").split("\f") if page.strip()]
    try:
        table = standard_tables(pages, args.date)
    except ZoneTableError as exc:
        sys.stderr.write(f"[FAIL] zone table: {exc}\n")
        return 1
    json.dump(table, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
