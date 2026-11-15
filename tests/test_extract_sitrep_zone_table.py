# SPDX-License-Identifier: Apache-2.0
"""Tests for the SitRep zone-table extractor.

The fixtures are synthetic: made-up zone names and figures in the layout the
extractor reads. No source page or reviewed table is used here.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from tools import extract_sitrep_zone_table as extractor

PROVINCES = extractor.PROVINCES


def _line(label, confirmed, deaths, cfr, tail=("", "", "", "")):
    cells = "".join(f"{'' if cell is None else cell:>12}" for cell in tail)
    return f"{label:<26}{confirmed:>8}{deaths:>8}{cfr:>10}{cells} "


def _zone_name(index: int) -> str:
    return "Fictive" + chr(ord("a") + index // 26) + chr(ord("a") + index % 26)


def synthetic_pages(named_zones: int, *, unallocated_deaths: int = 7, total_shift: int = 0) -> list[str]:
    """Page texts for a table of ``named_zones`` zone rows plus one unallocated row."""
    lines: list[str] = []
    total = [0, 0, 0, 0, 0, 0]
    for p_index, province in enumerate(PROVINCES):
        zones = [i for i in range(named_zones) if i % len(PROVINCES) == p_index]
        rows = [(_zone_name(i), 10 + i, 2 + i % 3, 1 if i % 4 == 0 else None) for i in zones]
        sums = [sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] or 0 for r in rows)]
        extra_deaths = unallocated_deaths if p_index == 0 else 0
        lines.append(_line(province, sums[0], sums[1] + extra_deaths, "10,0%", (sums[2], 1, 1, 2)))
        for name, confirmed, deaths, new in rows:
            lines.append(_line(name, confirmed, deaths, "20,0%", (new, None, None, None)))
        if p_index == 0:
            lines.append(_line(extractor.UNALLOCATED_ROW, "NA", unallocated_deaths, "NA"))
        for slot, value in enumerate((sums[0], sums[1] + extra_deaths, sums[2], 1, 1, 2)):
            total[slot] += value
    lines.append(_line("Total", total[0] + total_shift, total[1], "10,0%", tuple(total[2:])))
    half = len(lines) // 2
    return [
        "Cover page",
        "Tableau 1. National summary\nTableau 2. Cases and deaths by health zone\n" + "\n".join(lines[:half]),
        "\n".join(lines[half:]),
    ]


class TestZoneTableExtractor(unittest.TestCase):
    def test_a_68_row_and_a_69_row_table_parse_with_the_same_code(self):
        for printed in (68, 69):
            with self.subTest(printed=printed):
                table = extractor.standard_tables(synthetic_pages(printed - 1), "2026-01-01")
                reconciliation = table["reconciliation"]
                self.assertEqual(printed, len(table["rows"]))
                self.assertEqual(printed, len(table["day_rows"]))
                self.assertEqual(printed, reconciliation["printed_zone_row_count"])
                self.assertEqual(printed - 1, reconciliation["named_zone_row_count"])
                self.assertEqual(len(PROVINCES), len(table["province_totals"]))
                self.assertEqual(7, reconciliation["unventilated_confirmed_deaths"])
                self.assertEqual(
                    reconciliation["national_confirmed_deaths_total"],
                    reconciliation["named_zone_confirmed_deaths"] + 7,
                )
                self.assertEqual(
                    reconciliation["national_confirmed_total"], reconciliation["named_zone_confirmed_sum"]
                )

    def test_the_row_count_is_derived_for_any_table_size(self):
        table = extractor.standard_tables(synthetic_pages(11), "2026-01-01")
        self.assertEqual(12, table["reconciliation"]["printed_zone_row_count"])
        self.assertEqual(11, table["reconciliation"]["named_zone_row_count"])

    def test_the_unallocated_row_is_kept_out_of_the_named_zones(self):
        table = extractor.standard_tables(synthetic_pages(68), "2026-01-01")
        unallocated = [row for row in table["rows"] if row["zone"] == extractor.UNALLOCATED_ROW]
        self.assertEqual(1, len(unallocated))
        self.assertIsNone(unallocated[0]["confirmed"])
        self.assertEqual("Ituri", unallocated[0]["province"])

    def test_day_columns_follow_the_province_row_anchors(self):
        table = extractor.standard_tables(synthetic_pages(68), "2026-01-01")
        by_zone = {row["zone"]: row for row in table["day_rows"]}
        self.assertEqual(1, by_zone[_zone_name(0)]["new_confirmed_24h"])
        self.assertIsNone(by_zone[_zone_name(1)]["new_confirmed_24h"])
        self.assertIsNone(by_zone[_zone_name(0)]["cte_deaths_24h"])
        ituri = table["province_totals"][0]
        self.assertEqual(
            (1, 1, 2), (ituri["community_deaths_24h"], ituri["cte_deaths_24h"], ituri["total_confirmed_deaths_24h"])
        )

    def test_a_table_that_does_not_reconcile_for_cases_is_refused(self):
        with self.assertRaises(extractor.ZoneTableError) as raised:
            extractor.standard_tables(synthetic_pages(68, total_shift=1), "2026-01-01")
        self.assertIn("does not reconcile for confirmed:", str(raised.exception))

    def test_a_table_that_does_not_reconcile_for_deaths_is_refused(self):
        pages = synthetic_pages(68)
        row = _line(extractor.UNALLOCATED_ROW, "NA", 7, "NA")
        self.assertIn(row, pages[1])
        pages[1] = pages[1].replace(row, _line(extractor.UNALLOCATED_ROW, "NA", 6, "NA"))
        with self.assertRaises(extractor.ZoneTableError) as raised:
            extractor.standard_tables(pages, "2026-01-01")
        self.assertIn("does not reconcile for confirmed_deaths", str(raised.exception))

    def _wrap(self, pages: list[str], zone_index: int, first: str, second: str) -> None:
        """Print one zone row the way the layout wraps a long name: over three lines."""
        name = _zone_name(zone_index)
        for number, page in enumerate(pages):
            lines = page.splitlines()
            for position, line in enumerate(lines):
                if line.startswith(name + " "):
                    cells = " " * len(name) + line[len(name):]
                    lines[position:position + 1] = [part for part in (first, cells, second) if part is not None]
                    pages[number] = "\n".join(lines)
                    return
        self.fail(f"{name} not in the fixture")

    def test_a_wrapped_zone_name_is_read_from_the_lines_around_its_row(self):
        pages = synthetic_pages(68)
        # In the opposite order to the table of wrapped names: a name is never given by position.
        self._wrap(pages, 8, "Wanie-", "Rukula")
        self._wrap(pages, 3, "Boma", "Mangbetu")
        table = extractor.standard_tables(pages, "2026-01-01")
        zones = [row["zone"] for row in table["rows"]]
        self.assertLess(zones.index("Wanie-Rukula"), zones.index("Boma Mangbetu"))
        self.assertNotIn(_zone_name(3), zones)
        self.assertNotIn(_zone_name(8), zones)
        self.assertEqual(69, table["reconciliation"]["printed_zone_row_count"])

    def test_an_unnamed_row_the_wrapped_names_do_not_cover_is_refused_with_its_row_index(self):
        for first, second in (("Inconnu", "Fictif"), (None, None), ("Boma", None)):
            with self.subTest(first=first, second=second):
                pages = synthetic_pages(68)
                self._wrap(pages, 8, first, second)
                with self.assertRaises(extractor.ZoneTableError) as raised:
                    extractor.standard_tables(pages, "2026-01-01")
                # Row 1 is the first province row, row 2 its first zone and row 3 the wrapped one.
                self.assertIn("table row 3:", str(raised.exception))
                self.assertIn("no name", str(raised.exception))

    def test_a_province_subtotal_that_does_not_match_its_zone_rows_is_refused(self):
        pages = synthetic_pages(68)
        lines = pages[2].splitlines()
        position = next(i for i, line in enumerate(lines) if line.startswith("Nord-Ubangi"))
        match = extractor._ROW.match(lines[position])
        lines[position] = lines[position].replace(f"{match[2]:>8}{match[3]:>8}", f"{int(match[2]) + 1:>8}{match[3]:>8}", 1)
        pages[2] = "\n".join(lines)
        with self.assertRaises(extractor.ZoneTableError) as raised:
            extractor.standard_tables(pages, "2026-01-01")
        self.assertIn("province Nord-Ubangi does not reconcile for confirmed:", str(raised.exception))

    def test_province_subtotals_are_checked_for_deaths_with_the_unallocated_row(self):
        pages = synthetic_pages(68)
        lines = pages[1].splitlines()
        position = next(i for i, line in enumerate(lines) if line.startswith("Ituri"))
        match = extractor._ROW.match(lines[position])
        lines[position] = lines[position].replace(f"{match[2]:>8}{match[3]:>8}", f"{match[2]:>8}{int(match[3]) - 7:>8}", 1)
        pages[1] = "\n".join(lines)
        with self.assertRaises(extractor.ZoneTableError) as raised:
            extractor.standard_tables(pages, "2026-01-01")
        self.assertIn("province Ituri does not reconcile for confirmed_deaths", str(raised.exception))

    def test_a_missing_caption_or_total_row_is_refused(self):
        with self.assertRaises(extractor.ZoneTableError):
            extractor.standard_tables(["a", "no caption here", "c"], "2026-01-01")
        pages = synthetic_pages(20)
        pages[2] = "\n".join(line for line in pages[2].splitlines() if not line.startswith("Total"))
        with self.assertRaises(extractor.ZoneTableError) as raised:
            extractor.standard_tables(pages, "2026-01-01")
        self.assertIn("no Total row", str(raised.exception))

    def test_the_command_prints_the_table_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = pathlib.Path(tmp) / "edition.txt"
            text.write_text("\f".join(synthetic_pages(69)), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = extractor.main(["--text", str(text), "--date", "2026-01-02"])
        self.assertEqual(0, code)
        table = json.loads(out.getvalue())
        self.assertEqual("2026-01-02", table["date"])
        self.assertEqual(70, table["reconciliation"]["printed_zone_row_count"])


if __name__ == "__main__":
    unittest.main()
