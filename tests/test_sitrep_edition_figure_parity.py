# SPDX-License-Identifier: Apache-2.0
"""Guard: an edition's figures must not carry the previous edition's values.

Each cycle's promotion and manifest entry are authored by cloning the previous
edition's, so a block the clone step does not rewrite keeps the previous
edition's numbers while the headline, the chain and the rest of the suite stay
correct. Two shipped that way:

  * SR107's `figures.lab_indicators_24h` was SR106's (82 new confirmations,
    569 samples, 2 025 alerts) while SR107 prints 96, 537 and 1 830, and its
    own `operational_tables.lab_indicators_24h` already carried them;
  * SR106's manifest entry kept SR105's `patients_en_isolement_cte` (795),
    `source_zone_attributed_confirmed` (5863) and `source_published_at`.

This gate checks the places where one edition states the same quantity twice:

  * a lab block's `new_confirmations` is the edition's `new_confirmed_24h`;
  * the top-level and `operational_tables` lab blocks agree on every key they
    share;
  * the manifest entry's `source_zone_attributed_confirmed` is the promotion's
    `health_zone_table.reconciliation.named_zone_confirmed_sum`, and its
    `patients_en_isolement_cte` is the promotion's
    `patients_en_isolement_hospitalisation`;
  * the manifest entry's `source_published_at` is the promotion's
    `published_at` when both carry a time of day. A midnight value is a
    date-only placeholder (SR112 to SR117 carry some) and is not compared.
"""
from __future__ import annotations

import copy
import json
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMOTIONS_DIR = REPO_ROOT / "data" / "sitrep_promotions"
MANIFEST = REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"

CHECKS = (
    "lab_new_confirmations",
    "lab_blocks",
    "source_zone_attributed_confirmed",
    "patients_en_isolement_cte",
    "source_published_at",
)


def _time_of_day(value: object) -> str | None:
    """`YYYY-MM-DDTHH:MM:SS` without a UTC marker, or None for a date-only placeholder."""
    text = str(value or "").removesuffix("Z")
    if len(text) < 19 or text[10] != "T" or text[11:19] == "00:00:00":
        return None
    return text[:19]


def edition_checks(promotion: dict, entry: dict | None) -> dict[str, str | None]:
    """Return {check: problem or None} for every check that applies to this edition."""
    figures = promotion.get("figures") or {}
    results: dict[str, str | None] = {}

    lab = figures.get("lab_indicators_24h") or {}
    new_confirmed = promotion.get("new_confirmed_24h", figures.get("new_confirmed_24h"))
    if "new_confirmations" in lab and isinstance(new_confirmed, int):
        results["lab_new_confirmations"] = (
            None
            if lab["new_confirmations"] == new_confirmed
            else f"lab block has {lab['new_confirmations']} new confirmations, the edition {new_confirmed}"
        )
    nested = (figures.get("operational_tables") or {}).get("lab_indicators_24h") or {}
    shared = sorted(lab.keys() & nested.keys())
    if shared:
        split = {key: (lab[key], nested[key]) for key in shared if lab[key] != nested[key]}
        results["lab_blocks"] = f"top-level vs operational_tables: {split}" if split else None

    if entry is None:
        return results
    content = entry.get("normalized_content") or {}
    reconciliation = (figures.get("health_zone_table") or {}).get("reconciliation") or {}
    for field, expected in (
        ("source_zone_attributed_confirmed", reconciliation.get("named_zone_confirmed_sum")),
        ("patients_en_isolement_cte", figures.get("patients_en_isolement_hospitalisation")),
    ):
        if field in content and expected is not None:
            results[field] = (
                None if content[field] == expected else f"manifest {content[field]}, promotion {expected}"
            )
    manifest_time = _time_of_day(content.get("source_published_at"))
    promotion_time = _time_of_day(promotion.get("published_at"))
    if manifest_time and promotion_time:
        results["source_published_at"] = (
            None
            if manifest_time == promotion_time
            else f"manifest {content['source_published_at']}, promotion {promotion['published_at']}"
        )
    return results


class TestSitRepEditionFigureParity(unittest.TestCase):
    def setUp(self) -> None:
        self.promotions = {
            payload["sitrep_number"]: payload
            for payload in (
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(PROMOTIONS_DIR.glob("sitrep-*.json"))
            )
        }
        entries = json.loads(MANIFEST.read_text(encoding="utf-8"))["entries"]
        self.manifest = {entry["source_id"]: entry for entry in entries if entry.get("source_id")}

    def checks(self, number: int) -> dict[str, str | None]:
        promotion = self.promotions[number]
        return edition_checks(promotion, self.manifest.get(promotion["source_id"]))

    def test_editions_state_their_own_figures(self) -> None:
        applied: set[str] = set()
        offenders = []
        for number in sorted(self.promotions):
            for check, problem in sorted(self.checks(number).items()):
                applied.add(check)
                if problem:
                    offenders.append(f"  SitRep {number} [{check}] {problem}")
        self.assertEqual(set(CHECKS), applied, "a check compared nothing; the gate would pass vacuously")
        if offenders:
            self.fail(
                "An edition states a quantity twice and the two disagree, which means a block "
                "cloned from the previous edition was not rewritten:\n" + "\n".join(offenders)
            )

    def test_sr106_sr107_clone_defects_are_caught(self) -> None:
        sr107 = copy.deepcopy(self.promotions[107])
        sr107["figures"]["lab_indicators_24h"] = copy.deepcopy(self.promotions[106]["figures"]["lab_indicators_24h"])
        failing = {check for check, problem in edition_checks(sr107, None).items() if problem}
        self.assertEqual({"lab_new_confirmations", "lab_blocks"}, failing)

        entry = copy.deepcopy(self.manifest[self.promotions[106]["source_id"]])
        sr105 = self.manifest[self.promotions[105]["source_id"]]["normalized_content"]
        stale = ("patients_en_isolement_cte", "source_zone_attributed_confirmed", "source_published_at")
        for field in stale:
            entry["normalized_content"][field] = sr105[field]
        failing = {check for check, problem in edition_checks(self.promotions[106], entry).items() if problem}
        self.assertEqual(set(stale), failing)


if __name__ == "__main__":
    unittest.main()
