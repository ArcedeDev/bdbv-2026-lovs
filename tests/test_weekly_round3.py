# SPDX-License-Identifier: Apache-2.0
"""Weekly loop: the round-3 fix R3-I1 of the v2 pre-registration review (the core covers the code).

The core digest covers the code v2 is judged by: the sha256 of the source bytes of every module that
prices, resolves, scores or gates a week (the import closure of the core modules), read from the file
each module was loaded from. Any later change to one of them, a bug fix included, changes the core
digest, so it is a core revision and ends v2 at that week.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import unittest
from pathlib import Path
from unittest import mock

from lovs.forecast.weekly import spec

REPO = Path(__file__).resolve().parents[1]
ENTRY = ("resolve", "targets", "truth", "promotion_rows", "vintage", "source", "methods", "register", "spec",
         "score", "privacy")


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
        if name in seen:
            continue
        seen.add(name)
        todo += sorted(_lovs_imports(_module_path(name)))
    return seen


class CoreCodeTest(unittest.TestCase):
    def test_the_core_lists_every_module_that_prices_resolves_scores_or_gates_a_week(self) -> None:
        self.assertEqual(set(spec.CODE_MODULES), _closure())
        self.assertEqual(len(spec.CODE_MODULES), 19)        # the review's 16, plus spec, score and privacy

    def test_the_core_records_the_source_digest_of_each_loaded_module(self) -> None:
        code = spec.core_document()["code"]
        self.assertEqual(sorted(code), sorted(spec.CODE_MODULES))
        for name, digest in code.items():
            path = Path(importlib.import_module(name).__file__)
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest(), name)

    def test_any_change_to_a_core_module_changes_the_core_digest(self) -> None:
        core = spec.core_sha256()
        real = spec.module_source
        for name in ("lovs.forecast.weekly.resolve", "lovs.forecast.termination", "lovs.forecast_scoring",
                     "lovs.forecast.weekly.spec"):
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
