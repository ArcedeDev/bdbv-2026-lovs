# SPDX-License-Identifier: Apache-2.0
"""Tests for the guarded edition-pin update command."""
from __future__ import annotations

import contextlib
import io
import pathlib
import shutil
import tempfile
import unittest

from tools import update_edition_pins as pins

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PINNED_FILES = sorted({pin.file for pin in pins.PINS})
STALE_FILE = "tests/test_snapshot_contract.py"
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

    def _make_one_pin_stale(self) -> tuple[int, str]:
        """Lower the pinned corridor count by one; return its line number and the stale line."""
        path = self.copy / STALE_FILE
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        hits = [i for i, line in enumerate(lines)
                if line.startswith(CURRENT_LINE_PREFIX) and line.endswith(CURRENT_LINE_SUFFIX)]
        self.assertEqual(1, len(hits))
        current = int(lines[hits[0]][len(CURRENT_LINE_PREFIX):-len(CURRENT_LINE_SUFFIX)])
        lines[hits[0]] = f"{CURRENT_LINE_PREFIX}{current - 1}{CURRENT_LINE_SUFFIX}"
        path.write_text("".join(lines), encoding="utf-8")
        return hits[0] + 1, lines[hits[0]]

    def test_the_dry_run_reports_nothing_stale_on_the_current_tree(self):
        code, out, err = _run([])
        self.assertEqual("", err)
        self.assertEqual(0, code, out)
        self.assertIn(f"{len(pins.PINS)} pins checked", out)

    def test_one_stale_pin_is_reported_alone_and_the_dry_run_writes_nothing(self):
        line_number, stale_line = self._make_one_pin_stale()
        before = self._bytes()
        stale, changed = pins.evaluate(REPO_ROOT, self.copy)
        self.assertEqual(1, len(stale))
        self.assertEqual((STALE_FILE, line_number), (stale[0].pin.file, stale[0].line))
        self.assertEqual("corridor_count of the corridor watchlist", stale[0].pin.pins)
        self.assertEqual([STALE_FILE], list(changed))
        code, out, _ = _run(["--pin-root", str(self.copy)])
        self.assertEqual(1, code)
        self.assertEqual(before, self._bytes())
        removed = [line for line in out.splitlines() if line.startswith("-") and not line.startswith("---")]
        added = [line for line in out.splitlines() if line.startswith("+") and not line.startswith("+++")]
        self.assertEqual(["-" + stale_line.rstrip("\n")], removed)
        self.assertEqual(1, len(added))
        self.assertIn("Dry run: nothing written", out)

    def test_write_fixes_exactly_the_stale_line(self):
        self._make_one_pin_stale()
        code, out, _ = _run(["--pin-root", str(self.copy), "--write"])
        self.assertEqual(0, code, out)
        for file in PINNED_FILES:
            self.assertEqual((REPO_ROOT / file).read_bytes(), (self.copy / file).read_bytes(), file)
        self.assertEqual(0, _run(["--pin-root", str(self.copy)])[0])

    def test_a_pin_whose_anchor_is_gone_is_an_error_and_nothing_is_written(self):
        line_number, _ = self._make_one_pin_stale()
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
