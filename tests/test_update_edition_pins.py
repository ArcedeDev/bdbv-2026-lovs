# SPDX-License-Identifier: Apache-2.0
"""Tests for the guarded edition-pin update command."""
from __future__ import annotations

import contextlib
import io
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from tools import update_edition_pins as pins

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PINNED_FILES = sorted({pin.file for pin in pins.PINS})
STALE_FILE = "tests/test_snapshot_contract.py"
EDITION_FILE = "tests/test_headline_evidence_chains.py"
PREVIOUS_EDITION = "inrb-sitrep-001-2026-05-15"
CURRENT_LINE_PREFIX = "        self.assertEqual("
CURRENT_LINE_SUFFIX = ', contract["corridor_watchlist"]["corridor_count"])\n'


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pins.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestEditionPinUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.copy = pathlib.Path(self._tmp.name)
        (self.copy / "tests").mkdir()
        for file in PINNED_FILES:
            shutil.copyfile(REPO_ROOT / file, self.copy / file)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _bytes(self) -> dict[str, bytes]:
        return {file: (self.copy / file).read_bytes() for file in PINNED_FILES}

    def _edit_line(self, file: str, prefix: str, suffix: str, change) -> tuple[int, str]:
        """Rewrite the value between prefix and suffix on its one line; return line number and new line."""
        path = self.copy / file
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        hits = [i for i, line in enumerate(lines) if line.startswith(prefix) and line.endswith(suffix)]
        self.assertEqual(1, len(hits))
        lines[hits[0]] = f"{prefix}{change(lines[hits[0]][len(prefix):-len(suffix)])}{suffix}"
        path.write_text("".join(lines), encoding="utf-8")
        return hits[0] + 1, lines[hits[0]]

    def _pin_the_previous_edition(self) -> tuple[int, str]:
        """Set the pinned edition back by one, as the tests stand before a release."""
        return self._edit_line(EDITION_FILE, 'CURRENT_SITREP_SOURCE = "', '"\n', lambda _: PREVIOUS_EDITION)

    def _lower_corridor_count(self) -> tuple[int, str]:
        return self._edit_line(STALE_FILE, CURRENT_LINE_PREFIX, CURRENT_LINE_SUFFIX, lambda v: str(int(v) - 1))

    def test_the_dry_run_reports_nothing_stale_on_the_current_tree(self):
        code, out, err = _run([])
        self.assertEqual("", err)
        self.assertEqual(0, code, out)
        self.assertIn(f"{len(pins.PINS)} pins checked", out)

    def test_one_stale_pin_is_reported_alone_and_the_dry_run_writes_nothing(self):
        line_number, stale_line = self._pin_the_previous_edition()
        before = self._bytes()
        stale, changed = pins.evaluate(REPO_ROOT, self.copy)
        self.assertEqual(1, len(stale))
        self.assertEqual((EDITION_FILE, line_number), (stale[0].pin.file, stale[0].line))
        self.assertIs(pins.EDITION_PIN, stale[0].pin)
        self.assertEqual([EDITION_FILE], list(changed))
        code, out, _ = _run(["--pin-root", str(self.copy)])
        self.assertEqual(1, code)
        self.assertEqual(before, self._bytes())
        removed = [line for line in out.splitlines() if line.startswith("-") and not line.startswith("---")]
        added = [line for line in out.splitlines() if line.startswith("+") and not line.startswith("+++")]
        self.assertEqual(["-" + stale_line.rstrip("\n")], removed)
        self.assertEqual(1, len(added))
        self.assertIn("Dry run: nothing written", out)

    def test_write_fixes_exactly_the_stale_line(self):
        self._pin_the_previous_edition()
        code, out, _ = _run(["--pin-root", str(self.copy), "--write"])
        self.assertEqual(0, code, out)
        for file in PINNED_FILES:
            self.assertEqual((REPO_ROOT / file).read_bytes(), (self.copy / file).read_bytes(), file)
        self.assertEqual(0, _run(["--pin-root", str(self.copy)])[0])

    def test_an_advanced_edition_updates_a_count_that_may_fall(self):
        self._pin_the_previous_edition()
        self._edit_line(STALE_FILE, CURRENT_LINE_PREFIX, CURRENT_LINE_SUFFIX, lambda v: str(int(v) + 5))
        code, out, _ = _run(["--pin-root", str(self.copy), "--write"])
        self.assertEqual(0, code, out)
        self.assertEqual((REPO_ROOT / STALE_FILE).read_bytes(), (self.copy / STALE_FILE).read_bytes())

    def test_a_changed_value_with_no_new_edition_is_refused_as_a_regression(self):
        line_number, _ = self._lower_corridor_count()
        before = self._bytes()
        for argv in (["--pin-root", str(self.copy)], ["--pin-root", str(self.copy), "--write"]):
            code, out, err = _run(argv)
            self.assertEqual(2, code)
            self.assertEqual("", out)
            self.assertIn("nothing advanced", err)
            self.assertIn("regression", err)
            self.assertIn(f"{STALE_FILE}:{line_number}", err)
            self.assertEqual(before, self._bytes())

    def test_a_cumulative_pin_that_would_decrease_is_refused(self):
        self._pin_the_previous_edition()
        prefix = "        self.assertEqual("
        suffix = ', contract["confirmed_case_partition"]["headline_confirmed_total"])\n'
        line_number, _ = self._edit_line(STALE_FILE, prefix, suffix, lambda v: str(int(v) + 1))
        before = self._bytes()
        for argv in (["--pin-root", str(self.copy)], ["--pin-root", str(self.copy), "--write"]):
            code, out, err = _run(argv)
            self.assertEqual(2, code)
            self.assertEqual("", out)
            self.assertIn("would decrease", err)
            self.assertIn(f"{STALE_FILE}:{line_number}", err)
            self.assertEqual(before, self._bytes())

    def test_a_pinned_date_that_would_go_back_is_refused(self):
        self._pin_the_previous_edition()
        self._edit_line("tests/test_cycle_status.py", '        self.assertEqual(status["analytic_data_date"], "',
                        '")\n', lambda _: "2027-01-01")
        code, _, err = _run(["--pin-root", str(self.copy), "--write"])
        self.assertEqual(2, code)
        self.assertIn("would decrease", err)

    def test_a_reader_that_raises_exits_two_and_names_the_pin(self):
        def broken(outputs):
            raise RuntimeError("reader broke")

        bad = pins.Pin(STALE_FILE, "test_contract_captures_current_partition",
                       'self.assertEqual(<<v:int>>, contract["corridor_watchlist"]["corridor_count"])',
                       "a pin with a broken reader", "nowhere", broken)
        with self.assertRaises(pins.PinTableError) as raised:
            pins.evaluate(REPO_ROOT, self.copy, (bad,))
        self.assertIn("a pin with a broken reader", str(raised.exception))
        self.assertIn("RuntimeError", str(raised.exception))
        with mock.patch.object(pins, "PINS", (bad,)):
            code, out, err = _run(["--pin-root", str(self.copy)])
        self.assertEqual((2, ""), (code, out))
        self.assertIn("a pin with a broken reader", err)

    def test_a_pin_whose_anchor_is_gone_is_an_error_and_nothing_is_written(self):
        line_number, _ = self._lower_corridor_count()
        path = self.copy / STALE_FILE
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        del lines[line_number - 1]
        path.write_text("".join(lines), encoding="utf-8")
        before = self._bytes()
        code, _, err = _run(["--pin-root", str(self.copy), "--write"])
        self.assertEqual(2, code)
        self.assertIn("correct the pin table by hand", err)
        self.assertEqual(before, self._bytes())

    def test_numbers_outside_the_table_are_never_touched(self):
        # Open and resolved commitment counts sit in a pinned file, beside pinned
        # lines, and are calibration values: making them wrong must go unnoticed here.
        path = self.copy / "tests/test_public_exports.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn('"open commitments: ', text)
        path.write_text(text.replace('"open commitments: ', '"open commitments: 9'), encoding="utf-8")
        before = self._bytes()
        self.assertEqual(0, _run(["--pin-root", str(self.copy), "--write"])[0])
        self.assertEqual(before, self._bytes())


class TestEditionPinTable(unittest.TestCase):
    def test_every_pin_names_its_file_test_value_and_source(self):
        for pin in pins.PINS:
            with self.subTest(pin=pin.label):
                self.assertTrue((REPO_ROOT / pin.file).is_file())
                self.assertTrue(pin.test and pin.pins and pin.source)
        self.assertEqual(len(pins.PINS), len({(pin.file, pin.test, pin.anchor) for pin in pins.PINS}))

    def test_cumulative_pins_are_marked_in_the_table(self):
        by_what = {pin.pins: pin.cumulative for pin in pins.PINS}
        for what in ("source id of the headline edition", "headline confirmed cases",
                     "bunia confirmed in the public zone table", "analytic data date of the current snapshot",
                     "DRC + Uganda and DRC recovered totals"):
            self.assertTrue(by_what[what], what)
        for what in ("corridor_count of the corridor watchlist", "row count of the public zone table",
                     "patients in isolation by province", "sum of the printed province stocks",
                     "contacts seen, under follow-up and the printed rate in the narrative highlight"):
            self.assertFalse(by_what[what], what)
        self.assertTrue(pins.EDITION_PIN.cumulative)
        self.assertIn(pins.EDITION_PIN, pins.PINS)

    def test_the_table_refuses_a_frozen_value(self):
        frozen = (
            ("tests/test_calibration_ledger.py", "test_points", 'self.assertEqual(<<v:int>>, len(points))'),
            ("tests/test_model_tournament.py", "test_round", 'self.assertEqual(<<v:int>>, len(rows))'),
            ("tests/test_pins_block11.py", "test_pins", 'self.assertEqual(<<v:int>>, value)'),
            ("tests/test_public_exports.py", "test_summary", 'self.assertIn("open commitments: <<v:int>>", out)'),
            ("tests/test_kenya_second_case.py", "test_headline", 'self.assertEqual(<<v:int>>, value)'),
        )
        for file, test, anchor in frozen:
            with self.subTest(file=file):
                pin = pins.Pin(file, test, anchor, "a frozen value", "nowhere", lambda outputs: {"v": "1"})
                with self.assertRaises(pins.PinTableError):
                    pins.check_table((pin,))

    def test_the_table_refuses_a_file_outside_the_tests(self):
        pin = pins.Pin("data/snapshot_contract.json", pins.MODULE, '"corridor_count": <<v:int>>',
                       "a data file", "nowhere", lambda outputs: {"v": "1"})
        with self.assertRaises(pins.PinTableError):
            pins.check_table((pin,))

    def test_a_value_that_changes_shape_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "tests").mkdir()
            (root / "tests/test_shape.py").write_text(
                "def test_stock(self):\n    self.assertEqual(15, occupancy)\n", encoding="utf-8"
            )
            pin = pins.Pin("tests/test_shape.py", "test_stock", "self.assertEqual(<<v:int>>, occupancy)",
                           "a stock that became unknown", "nowhere", lambda outputs: {"v": "None"})
            with self.assertRaises(pins.PinTableError) as raised:
                pins.evaluate(REPO_ROOT, root, (pin,))
        self.assertIn("edited by hand", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
