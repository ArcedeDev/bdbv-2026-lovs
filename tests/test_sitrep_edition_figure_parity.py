# SPDX-License-Identifier: Apache-2.0
"""Guard: an edition's figures must not carry the previous edition's values.

Each cycle's promotion and manifest entry are authored by cloning the previous
edition's, so a block the clone step does not rewrite keeps the previous
edition's numbers while the headline, the chain and the rest of the suite stay
correct. These shipped that way:

  * SR107's `figures.lab_indicators_24h` was SR106's, and SR114's
    `operational_tables.lab_indicators_24h` was SR113's;
  * SR106's manifest entry kept SR105's `patients_en_isolement_cte`,
    `source_zone_attributed_confirmed` and `source_published_at`;
  * the SR108 and SR109 manifest entries kept the previous edition's
    `province_operational.national` block, and SR107's zeroed its
    `unclassifiedInIsolation`;
  * SR115's promotion kept SR114's `province_table`, alert and contact rows,
    PoE/PoC block and edition-specific `preserved_conflicts` lines;
  * SR107's `source_published_at` lost its UTC marker, and the SR115 to SR117
    manifest entries carried midnight placeholders for publication times the
    promotions state.

This gate checks the places where one edition states the same quantity twice.
Within a promotion:

  * a lab block's `new_confirmations` is the edition's `new_confirmed_24h`, and
    the top-level and `operational_tables` lab blocks agree on every shared key;
  * the printed province table (Tableau 1) agrees with the zone table's
    province totals (Tableau 2) when the edition prints one, and sums to the
    national headline;
  * the alert rows sum to the alert totals (Tableau 3);
  * the contact rows agree with `province_operational.byProvince`, and the
    national operational block agrees with the contact totals, the isolation
    headline and its own confirmed/suspect split;
  * `preserved_conflicts` lines that restate this edition's figures (the
    24-hour death movement, the A ventiler residual, the DRC-only headline)
    state them.

Between the manifest entry and the promotion:

  * `source_zone_attributed_confirmed` is the named-zone sum and
    `patients_en_isolement_cte` the isolation headline;
  * `source_published_at` is UTC-marked, equals the promotion's `published_at`
    when both carry a time of day, and neither it nor the entry's own
    `published_at` is a midnight placeholder where the promotion states the
    time;
  * the national operational block, the province table and the conflict list
    are the promotion's wherever the entry carries them.

Disagreements the source itself prints, and older ones not yet reviewed, are
listed per edition and check in `KNOWN_EDITION_DISAGREEMENTS` rather than
skipped; a second test fails as soon as a listed check starts passing.
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMOTIONS_DIR = REPO_ROOT / "data" / "sitrep_promotions"
MANIFEST = REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"

CHECKS = (
    "lab_new_confirmations",
    "lab_blocks",
    "province_table_vs_zone_table",
    "province_table_totals",
    "alert_rows",
    "contact_rows",
    "national_block",
    "conflict_lines",
    "source_zone_attributed_confirmed",
    "patients_en_isolement_cte",
    "source_published_at",
    "published_at_placeholder",
    "manifest_mirror",
)

# SitRep number -> {check: why it is listed}. Each entry is a recorded
# disagreement, not a suppression: once the check passes, drop it.
KNOWN_EDITION_DISAGREEMENTS: dict[int, dict[str, str]] = {
    55: {"alert_rows": "rows sum to 900 alerts and 607 investigated against the printed 898 and 605; not reviewed here"},
    60: {
        "contact_rows": "contact rows and byProvince disagree for Ituri and Nord-Kivu; not reviewed here",
        "national_block": "national contacts 8382 seen of 12430 against the 10156 of 12370 contact totals; not reviewed here",
        "manifest_mirror": "the manifest entry carries SR59's national block and conflict list",
    },
    64: {"manifest_mirror": "manifest suspectsInIsolation 460 against 470 and an older conflict list; not reviewed here"},
    58: {"manifest_mirror": "the manifest entry carries an older conflict list; not reviewed here"},
    67: {"manifest_mirror": "the manifest entry carries an older conflict list; not reviewed here"},
    83: {"alert_rows": "printed: Haut-Uele publishes no alert table, so the rows sum to 1283 against the 1288 total"},
    89: {
        "province_table_totals": "printed: the province column sums to 4567 against the national 4566",
        "alert_rows": "printed: validated rows sum to 325 against the 324 the edition prints",
    },
    117: {"province_table_totals": "printed: province rows sum to 84 new confirmations against the national 86"},
}

MIRRORED_PATHS = (("province_operational", "national"), ("province_table",), ("preserved_conflicts",))
DEATH_MOVEMENT = re.compile(r"24-hour death movement is (\d+): (\d+) community plus (\d+) intra-CTE")
A_VENTILER = re.compile(r"The (\d+) Ituri deaths printed as A ventiler")
DRC_ONLY = re.compile(r"\bSR ?(\d+) is DRC-only at ([\d, ]+) confirmed / ([\d, ]+) deaths")
UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _is_count(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _time_of_day(value: object) -> str | None:
    """`YYYY-MM-DDTHH:MM:SS` without a UTC marker, or None for a date-only placeholder."""
    text = str(value or "").removesuffix("Z")
    if len(text) < 19 or text[10] != "T" or text[11:19] == "00:00:00":
        return None
    return text[:19]


def _is_midnight(value: object) -> bool:
    text = str(value or "")
    return len(text) >= 19 and text[10] == "T" and text[11:19] == "00:00:00"


def _province_rows(figures: dict) -> list[dict] | None:
    table = figures.get("province_table")
    rows = table.get("rows") if isinstance(table, dict) else table
    return rows if isinstance(rows, list) and rows else None


def _sum_problems(rows: list[dict], totals: dict, pairs: tuple[tuple[str, str], ...]) -> dict[str, tuple]:
    problems = {}
    for row_key, total_key in pairs:
        values = [row.get(row_key) for row in rows]
        if values and all(_is_count(v) for v in values) and _is_count(totals.get(total_key)):
            if sum(values) != totals[total_key]:
                problems[row_key] = (sum(values), totals[total_key])
    return problems


def _promotion_checks(promotion: dict) -> dict[str, str | None]:
    figures = promotion.get("figures") or {}
    operational = figures.get("operational_tables") or {}
    results: dict[str, str | None] = {}

    lab = figures.get("lab_indicators_24h") or {}
    new_confirmed = promotion.get("new_confirmed_24h", figures.get("new_confirmed_24h"))
    if "new_confirmations" in lab and isinstance(new_confirmed, int):
        results["lab_new_confirmations"] = (
            None
            if lab["new_confirmations"] == new_confirmed
            else f"lab block has {lab['new_confirmations']} new confirmations, the edition {new_confirmed}"
        )
    nested = operational.get("lab_indicators_24h") or {}
    shared = sorted(lab.keys() & nested.keys())
    if shared:
        split = {key: (lab[key], nested[key]) for key in shared if lab[key] != nested[key]}
        results["lab_blocks"] = f"top-level vs operational_tables: {split}" if split else None

    rows = _province_rows(figures)
    zone_table = figures.get("health_zone_table") or {}
    if rows and zone_table.get("zone_attribution_status") == "published":
        zone_totals = {row.get("province"): row for row in zone_table.get("province_totals") or []}
        split = {}
        for row in rows:
            total = zone_totals.get(row.get("province"))
            if total is None:
                split[row.get("province")] = "absent from the zone table's province totals"
                continue
            diff = {
                key: (row.get(key), total.get(key))
                for key in ("confirmed", "confirmed_deaths", "new_confirmed_24h")
                if _is_count(row.get(key)) and _is_count(total.get(key)) and row[key] != total[key]
            }
            if diff:
                split[row.get("province")] = diff
        results["province_table_vs_zone_table"] = f"Tableau 1 vs Tableau 2 province rows: {split}" if split else None
    if rows:
        totals = {
            "confirmed": figures.get("cumul_cas_confirmes_drc"),
            "confirmed_deaths": figures.get("cumul_deces_parmi_confirmes_drc"),
            "new_confirmed_24h": new_confirmed,
        }
        split = _sum_problems(rows, totals, tuple((key, key) for key in totals))
        results["province_table_totals"] = f"province rows sum vs headline: {split}" if split else None

    alert_rows = [
        {
            **row,
            "validated": sum(
                row.get(key) or 0
                for key in ("validated_living", "validated_deaths", "alerts_validated_living", "alerts_validated_deaths")
            ),
        }
        for row in operational.get("alerts_by_province") or []
        if any(_is_count(value) for key, value in row.items() if key != "province")
    ]
    alert_totals = dict(operational.get("alerts_total") or {})
    if alert_rows and alert_totals:
        if "validated_as_suspect" in alert_totals:
            alert_totals["validated"] = alert_totals["validated_as_suspect"]
        elif _is_count(alert_totals.get("alerts_validated_living")) and _is_count(alert_totals.get("alerts_validated_deaths")):
            alert_totals["validated"] = alert_totals["alerts_validated_living"] + alert_totals["alerts_validated_deaths"]
        split = _sum_problems(
            alert_rows,
            alert_totals,
            tuple(
                (key, key)
                for key in ("alerts_reported", "validated", "suspects_investigated", "suspects_transferred", "alerts_investigated")
            ),
        )
        results["alert_rows"] = f"alert rows sum vs alert totals: {split}" if split else None

    by_province = (figures.get("province_operational") or {}).get("byProvince") or {}
    contact_rows = operational.get("contacts_by_province") or []
    if contact_rows and by_province:
        split = {}
        for row in contact_rows:
            block = by_province.get(row.get("province")) or {}
            diff = {
                key: (row.get(key), block.get(block_key))
                for key, block_key in (("contacts_seen", "contactsSeen"), ("contacts_under_follow_up", "contactsUnderFollowUp"))
                if _is_count(row.get(key)) and _is_count(block.get(block_key)) and row[key] != block[block_key]
            }
            if diff:
                split[row.get("province")] = diff
        results["contact_rows"] = f"contact rows vs byProvince: {split}" if split else None

    national = (figures.get("province_operational") or {}).get("national") or {}
    if national:
        contacts = operational.get("contacts_total") or {}
        expected = {
            "contactsSeen": contacts.get("contacts_seen", contacts.get("contacts_seen_24h")),
            "contactsUnderFollowUp": contacts.get("contacts_under_follow_up"),
            "patientsInIsolation": figures.get("patients_en_isolement_hospitalisation"),
        }
        if _is_count(national.get("patientsInIsolation")) and "unclassifiedInIsolation" in national:
            expected["unclassifiedInIsolation"] = national["patientsInIsolation"] - sum(
                national.get(key) or 0 for key in ("confirmedInIsolation", "suspectsInIsolation")
            )
        split = {
            key: (national.get(key), value)
            for key, value in expected.items()
            if _is_count(value) and _is_count(national.get(key)) and national[key] != value
        }
        results["national_block"] = f"national block vs its sources: {split}" if split else None

    reconciliation = zone_table.get("reconciliation") or {}
    stated = []
    for line in figures.get("preserved_conflicts") or []:
        if match := DEATH_MOVEMENT.search(line):
            expected_movement = (
                promotion.get("new_confirmed_deaths_24h", figures.get("new_confirmed_deaths_24h")),
                figures.get("community_deaths_24h"),
                figures.get("cte_deaths_24h"),
            )
            stated.append((tuple(map(int, match.groups())), expected_movement, "death movement"))
        if match := A_VENTILER.search(line):
            stated.append((int(match.group(1)), reconciliation.get("unventilated_confirmed_deaths"), "A ventiler"))
        if match := DRC_ONLY.search(line):
            numbers = tuple(int(re.sub(r"[, ]", "", group)) for group in match.groups())
            expected_headline = (
                promotion.get("sitrep_number"),
                figures.get("cumul_cas_confirmes_drc"),
                figures.get("cumul_deces_parmi_confirmes_drc"),
            )
            stated.append((numbers, expected_headline, "DRC-only headline"))
    if stated:
        wrong = [f"{label} states {got}, the edition {want}" for got, want, label in stated if got != want]
        results["conflict_lines"] = "; ".join(wrong) if wrong else None
    return results


def _manifest_checks(promotion: dict, entry: dict) -> dict[str, str | None]:
    figures = promotion.get("figures") or {}
    content = entry.get("normalized_content") or {}
    results: dict[str, str | None] = {}

    reconciliation = (figures.get("health_zone_table") or {}).get("reconciliation") or {}
    for field, expected in (
        ("source_zone_attributed_confirmed", reconciliation.get("named_zone_confirmed_sum")),
        ("patients_en_isolement_cte", figures.get("patients_en_isolement_hospitalisation")),
    ):
        if field in content and expected is not None:
            results[field] = (
                None if content[field] == expected else f"manifest {content[field]}, promotion {expected}"
            )

    promotion_time = _time_of_day(promotion.get("published_at"))
    source_published_at = content.get("source_published_at")
    if source_published_at is not None:
        problems = []
        if not UTC_TIMESTAMP.fullmatch(str(source_published_at)):
            problems.append(f"{source_published_at!r} is not a UTC-marked timestamp")
        manifest_time = _time_of_day(source_published_at)
        if promotion_time and manifest_time and manifest_time != promotion_time:
            problems.append(f"manifest {source_published_at}, promotion {promotion['published_at']}")
        if promotion_time and _is_midnight(source_published_at):
            problems.append(f"midnight placeholder {source_published_at}, promotion {promotion['published_at']}")
        results["source_published_at"] = "; ".join(problems) or None
    if promotion_time and entry.get("published_at") is not None:
        results["published_at_placeholder"] = (
            f"entry published_at {entry['published_at']} is a midnight placeholder, promotion {promotion['published_at']}"
            if _is_midnight(entry["published_at"])
            else None
        )

    mirrored = []
    for path in MIRRORED_PATHS:
        ours, theirs = figures, content
        for key in path:
            ours = ours.get(key) if isinstance(ours, dict) else None
            theirs = theirs.get(key) if isinstance(theirs, dict) else None
        if ours is None or theirs is None:
            continue
        if isinstance(ours, dict) and isinstance(theirs, dict):
            diff = {key: (theirs[key], ours[key]) for key in ours.keys() & theirs.keys() if ours[key] != theirs[key]}
        elif isinstance(ours, list) and isinstance(theirs, list):
            index = next((i for i, (a, b) in enumerate(zip(theirs, ours)) if a != b), min(len(ours), len(theirs)))
            diff = {} if ours == theirs else {"first differing item": index, "lengths": (len(theirs), len(ours))}
        else:
            diff = {} if ours == theirs else {"value": (theirs, ours)}
        mirrored.append((".".join(path), diff))
    if mirrored:
        split = {path: diff for path, diff in mirrored if diff}
        results["manifest_mirror"] = f"manifest vs promotion: {split}" if split else None
    return results


def edition_checks(promotion: dict, entry: dict | None) -> dict[str, str | None]:
    """Return {check: problem or None} for every check that applies to this edition."""
    results = _promotion_checks(promotion)
    if entry is not None:
        results.update(_manifest_checks(promotion, entry))
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
            known = KNOWN_EDITION_DISAGREEMENTS.get(number, {})
            for check, problem in sorted(self.checks(number).items()):
                applied.add(check)
                if problem and check not in known:
                    offenders.append(f"  SitRep {number} [{check}] {problem}")
        self.assertEqual(set(CHECKS), applied, "a check compared nothing; the gate would pass vacuously")
        if offenders:
            self.fail(
                "An edition states a quantity twice and the two disagree, which means a block "
                "cloned from the previous edition was not rewritten:\n" + "\n".join(offenders)
            )

    def test_known_disagreements_still_disagree(self) -> None:
        for number, checks in sorted(KNOWN_EDITION_DISAGREEMENTS.items()):
            with self.subTest(sitrep=number):
                self.assertIn(number, self.promotions, "listed edition has no promotion")
                self.assertLessEqual(set(checks), set(CHECKS), "listed check does not exist")
                results = self.checks(number)
                repaired = sorted(check for check in checks if not results.get(check))
                self.assertFalse(
                    repaired, f"these checks now pass; drop them from KNOWN_EDITION_DISAGREEMENTS: {repaired}"
                )

    def failing(self, promotion: dict, entry: dict | None) -> set[str]:
        return {check for check, problem in edition_checks(promotion, entry).items() if problem}

    def test_sr106_sr107_clone_defects_are_caught(self) -> None:
        sr107 = copy.deepcopy(self.promotions[107])
        sr107["figures"]["lab_indicators_24h"] = copy.deepcopy(self.promotions[106]["figures"]["lab_indicators_24h"])
        self.assertEqual({"lab_new_confirmations", "lab_blocks"}, self.failing(sr107, None))

        entry = copy.deepcopy(self.manifest[self.promotions[106]["source_id"]])
        sr105 = self.manifest[self.promotions[105]["source_id"]]["normalized_content"]
        stale = ("patients_en_isolement_cte", "source_zone_attributed_confirmed", "source_published_at")
        for field in stale:
            entry["normalized_content"][field] = sr105[field]
        self.assertEqual(set(stale), self.failing(self.promotions[106], entry))

    def test_manifest_clone_defects_are_caught(self) -> None:
        # SR108 and SR109 carried the previous edition's national block.
        for number in (108, 109):
            entry = copy.deepcopy(self.manifest[self.promotions[number]["source_id"]])
            previous = self.promotions[number - 1]["figures"]["province_operational"]["national"]
            entry["normalized_content"]["province_operational"]["national"].update(copy.deepcopy(previous))
            self.assertEqual({"manifest_mirror"}, self.failing(self.promotions[number], entry), number)
        # SR107 zeroed its unclassified count and dropped the UTC marker.
        entry = copy.deepcopy(self.manifest[self.promotions[107]["source_id"]])
        entry["normalized_content"]["province_operational"]["national"]["unclassifiedInIsolation"] = 0
        entry["normalized_content"]["source_published_at"] = "2026-08-31T12:34:30"
        self.assertEqual({"manifest_mirror", "source_published_at"}, self.failing(self.promotions[107], entry))
        # SR115 to SR117 carried midnight placeholders for their post times.
        entry = copy.deepcopy(self.manifest[self.promotions[116]["source_id"]])
        entry["published_at"] = entry["normalized_content"]["source_published_at"] = "2026-09-09T00:00:00Z"
        self.assertEqual(
            {"source_published_at", "published_at_placeholder"}, self.failing(self.promotions[116], entry)
        )

    def test_sr115_promotion_clone_defects_are_caught(self) -> None:
        sr114, sr115 = self.promotions[114]["figures"], copy.deepcopy(self.promotions[115])
        figures = sr115["figures"]
        figures["province_table"] = copy.deepcopy(sr114["province_table"])
        figures["preserved_conflicts"][0] = sr114["preserved_conflicts"][0]
        for key in ("alerts_by_province", "contacts_by_province"):
            figures["operational_tables"][key] = copy.deepcopy(sr114["operational_tables"][key])
        self.assertEqual(
            {"province_table_vs_zone_table", "province_table_totals", "conflict_lines", "alert_rows", "contact_rows"},
            self.failing(sr115, None),
        )
        entry = self.manifest[self.promotions[115]["source_id"]]
        self.assertIn("manifest_mirror", self.failing(sr115, entry))


if __name__ == "__main__":
    unittest.main()
