# SPDX-License-Identifier: Apache-2.0
"""Weekly loop: the fixes R3-I1 and R4-I1 of the v2 pre-registration review (the core covers the code).

The core digest covers the code v2 is judged by: the sha256 of the source bytes of every module that
prices, resolves, scores or gates a week (the import closure of the core modules) and of the build
driver, which picks the SitRep versions and arrival times, sets the cutoff and writes the results files.
Each is read from the file the import system would load, never by running it. Any later change to one
of them, a bug fix included, changes the core digest, so it is a core revision and ends v2 at that week.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lovs.forecast.weekly import spec

REPO = Path(__file__).resolve().parents[1]
ENTRY = ("resolve", "targets", "truth", "promotion_rows", "vintage", "source", "methods", "register", "spec",
         "score", "privacy", "__main__")
# The build driver imports these two; they write training tables and reports v2 never reads.
OUTSIDE = {"lovs.forecast.weekly.export", "lovs.forecast.weekly.usefulness"}
DRIVER = "lovs.forecast.weekly.__main__"
CORE = "from lovs.forecast.weekly import spec; import sys; print(spec.core_sha256(), '%s' in sys.modules)" % DRIVER


def _module_path(name: str) -> Path:
    path = REPO / (name.replace(".", "/") + ".py")
    return path if path.is_file() else REPO / name.replace(".", "/") / "__init__.py"


def _lovs_imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "lovs":
            for alias in node.names:
                sub = f"{node.module}.{alias.name}"
                out.add(sub if (REPO / (sub.replace(".", "/") + ".py")).is_file() else node.module)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names if a.name.split(".")[0] == "lovs"}
    return out


def _closure() -> set[str]:
    seen, todo = set(), [f"lovs.forecast.weekly.{m}" for m in ENTRY]
    while todo:
        name = todo.pop()
        if name in seen or name in OUTSIDE:
            continue
        seen.add(name)
        todo += sorted(_lovs_imports(_module_path(name)))
    return seen


class CoreCodeTest(unittest.TestCase):
    def test_the_core_lists_every_module_that_prices_resolves_scores_or_gates_a_week(self) -> None:
        self.assertEqual(set(spec.CODE_MODULES), _closure())
        self.assertEqual(len(spec.CODE_MODULES), 20)        # the review's 16, spec, score, privacy and the driver
        self.assertIn(DRIVER, spec.CODE_MODULES)

    def test_the_core_records_the_source_digest_of_each_loaded_module(self) -> None:
        code = spec.core_document()["code"]
        self.assertEqual(sorted(code), sorted(spec.CODE_MODULES))
        for name, digest in code.items():
            path = Path(importlib.util.find_spec(name).origin)
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest(), name)

    def _core_of(self, root: Path) -> tuple[str, bool]:
        env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
        out = subprocess.run([sys.executable, "-B", "-c", CORE], cwd=root, env=env, capture_output=True, text=True,
                             check=True).stdout.split()
        return out[0], out[1] == "True"

    def test_a_byte_changed_in_the_build_driver_changes_the_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(REPO / "lovs", root / "lovs", ignore=shutil.ignore_patterns("__pycache__"))
            self.assertEqual(self._core_of(root), (spec.core_sha256(), False))     # hashed, never imported
            driver = root / "lovs" / "forecast" / "weekly" / "__main__.py"
            data = bytearray(driver.read_bytes())
            data[data.index(b"Build one week")] ^= 0x20                            # one byte, in the docstring
            driver.write_bytes(bytes(data))
            changed, imported = self._core_of(root)
            self.assertNotEqual(changed, spec.core_sha256())
            self.assertFalse(imported)

    def test_any_change_to_a_core_module_changes_the_core_digest(self) -> None:
        core = spec.core_sha256()
        real = spec.module_source
        for name in ("lovs.forecast.weekly.resolve", "lovs.forecast.termination", "lovs.forecast_scoring",
                     "lovs.forecast.weekly.spec", DRIVER):
            changed = lambda n, name=name: real(n) + (b"\n" if n == name else b"")  # noqa: E731
            with mock.patch.object(spec, "module_source", changed):
                self.assertNotEqual(spec.core_sha256(), core, name)
        self.assertEqual(spec.core_sha256(), core)

    def test_a_new_candidate_still_leaves_the_core_alone(self) -> None:
        core = spec.core_sha256()
        with mock.patch.object(spec, "CANDIDATES", ({"id": "cand.example", "classes": ["flow"]},)):
            self.assertEqual(spec.core_sha256(), core)


if __name__ == "__main__":
    unittest.main()
