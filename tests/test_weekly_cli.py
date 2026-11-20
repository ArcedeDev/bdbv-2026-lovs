# SPDX-License-Identifier: Apache-2.0
"""Weekly loop: the build command line (review/WEEKLY-CODE-REVIEW.md I5)."""
from __future__ import annotations

import unittest

from lovs.forecast.weekly import __main__ as cli

REQUIRED = ["build"] + [x for name in ("lovs-repo", "lovs-commit", "mcp-repo", "mcp-commit", "fetched", "arrivals",
                                       "store", "private-root", "report-out", "generator-commit")
                        for x in (f"--{name}", "x")]


class CommandLineTest(unittest.TestCase):
    def test_every_missing_receipt_can_be_added(self) -> None:
        # A skipped week must not lose the receipt of the pushed week before it.
        args = cli.parser().parse_args(REQUIRED + ["--receipt", "a.json", "--receipt", "b.json"])
        self.assertEqual(args.receipt, ["a.json", "b.json"])
        self.assertEqual(cli.parser().parse_args(REQUIRED).receipt, [])


if __name__ == "__main__":
    unittest.main()
