#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Recompute the edition-dependent test pins from the regenerated outputs.

Some tests pin a figure of the newest edition (the headline count, the newest
SitRep source id, a zone row) so that a release cannot move it silently. Those
pins advance with every edition. This command reads the regenerated outputs,
recomputes each pin listed in PINS, and prints a unified diff.

  python3 tools/update_edition_pins.py            # dry run; exit 1 when a pin is stale
  python3 tools/update_edition_pins.py --write    # rewrite the stale pins

Run it after the pipeline has regenerated the outputs (a failed release check
leaves them regenerated), read the diff against the edition under review, then
rerun the release check.

Only the pins enumerated in PINS are ever read or written, and only the slot
inside each pin's literal anchor text. A pin whose anchor is not found exactly
once in its named test is an error (exit 2), never a guess: the table must be
corrected by hand. Frozen values (a calibration block, a forecast, a tournament
round, a pre-registration) are never listed here, and FROZEN_MARKERS refuses a
table entry that names one. A pin whose assertion changes shape between
editions (a value that becomes None, a new zone row, a disclosed-conflict
sentence) is edited by hand and is not listed either.

Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import difflib
import functools
import json
import pathlib
import re
import sys
from typing import Callable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

MODULE = "<module>"

# Slot kinds a pin anchor may carry, written <<name:kind>> in the anchor text.
SLOT_KINDS = {
    "int": r"\d+",
    "comma_int": r"\d{1,3}(?:,\d{3})*",
    "percent": r"\d+(?:\.\d+)?",
    "date": r"\d{4}-\d{2}-\d{2}",
    "source_id": r"inrb-sitrep-\d{3}-\d{4}-\d{2}-\d{2}",
    "optional_int": r"None|\d+",
}
_SLOT = re.compile(r"<<(\w+):(\w+)>>")

# A table entry that names one of these is refused: these values are frozen at
# pre-commitment and are never advanced by a release.
FROZEN_MARKERS = (
    "calibration", "commitment", "forecast", "tournament", "prereg", "pre_reg",
    "pins_block", "kenya", "nigeria", "resolution", "ledger", "scoring",
)


class PinTableError(RuntimeError):
    """The pin table and the pinned files disagree; a person must look."""


class Outputs:
    """The regenerated outputs the pins are recomputed from, read once each."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root

    def _json(self, relative: str) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def _csv(self, relative: str) -> list[dict[str, str]]:
        with (self.root / relative).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    @functools.cached_property
    def live(self) -> dict:
        return self._json("data/live-bdbv-2026-output.json")

    @functools.cached_property
    def contract(self) -> dict:
        return self._json("data/snapshot_contract.json")

    @functools.cached_property
    def public_snapshot(self) -> dict:
        return self._json("data/public_snapshot.json")

    @functools.cached_property
    def local_example(self) -> dict:
        return self._json("examples/local_aggregate_input.example.json")

    @functools.cached_property
    def zone_rows(self) -> dict[str, dict[str, str]]:
        return {row["zone_id"]: row for row in self._csv("data/public_zone_counts_2026-05-29.csv")}

    @functools.cached_property
    def snapshot_clocks(self) -> dict[str, dict[str, str]]:
        rows = self._csv("deliverables/public-health-dataset/snapshot_clocks.csv")
        return {row["row_id"]: row for row in rows}

    @functools.cached_property
    def headline_source_id(self) -> str:
        return str(self.live["date_semantics"]["source_clocks"]["headline_count_endpoint"])

    @functools.cached_property
    def promotion(self) -> dict:
        """The reviewed promotion of the edition the headline counts end on."""
        matches = [
            payload
            for path in sorted((self.root / "data" / "sitrep_promotions").glob("sitrep-*.json"))
            if (payload := json.loads(path.read_text(encoding="utf-8"))).get("source_id") == self.headline_source_id
        ]
        if len(matches) != 1:
            raise PinTableError(
                f"expected one reviewed promotion for {self.headline_source_id}, found {len(matches)}"
            )
        return matches[0]

    # Small named readers, so the table rows stay one line each.
    def partition(self, key: str) -> str:
        return str(self.contract["confirmed_case_partition"][key])

    def watchlist(self, key: str) -> str:
        return str(self.contract["corridor_watchlist"][key])

    def composition(self, metric: str) -> dict[str, str]:
        block = self.contract["country_scope_composition"][metric]
        return {"total": str(block["total"]), "drc": str(block["drc"])}

    def headline(self, block: str) -> str:
        return str(self.live[block]["confirmed"]["primary"])

    def public_confirmed(self) -> str:
        return str(self.public_snapshot["reported_counts"]["confirmed"]["primary"])

    def local_zone_confirmed(self) -> str:
        zones = self.local_example["health_zone_counts"]
        return str(sum(int(zone.get("confirmed") or 0) for zone in zones))

    def zone_csv_confirmed(self) -> str:
        return str(sum(int(row["confirmed"] or 0) for row in self.zone_rows.values()))

    def province_stock(self, province: str) -> str:
        current = self.public_snapshot["responseState"]["provinceCurrent"]["byProvince"]
        return str(current[province]["patientsInIsolation"])

    def province_stock_sum(self) -> str:
        current = self.public_snapshot["responseState"]["provinceCurrent"]["byProvince"]
        return str(sum(row["patientsInIsolation"] or 0 for row in current.values()))


@dataclasses.dataclass(frozen=True)
class Pin:
    file: str      # the pinned file, relative to the repository root
    test: str      # the test function holding the pin (a regular expression), or MODULE
    anchor: str    # the literal source text, with a <<name:kind>> slot for each pinned value
    pins: str      # what the pin holds
    source: str    # where the value comes from
    values: Callable[[Outputs], dict[str, str]]

    @property
    def label(self) -> str:
        return f"{self.file}::{self.test}: {self.pins}"


def _one(value: Callable[[Outputs], str]) -> Callable[[Outputs], dict[str, str]]:
    return lambda outputs: {"v": value(outputs)}


_CYCLE = "tests/test_cycle_status.py"
_CHAINS = "tests/test_headline_evidence_chains.py"
_EVIDENCE = "tests/test_lovs_evidence.py"
_EXPORTS = "tests/test_public_exports.py"
_DATASET = "tests/test_public_health_export.py"
_RESPONSE = "tests/test_response_state.py"
_CONTRACT = "tests/test_snapshot_contract.py"

_LIVE = "data/live-bdbv-2026-output.json"
_CONTRACT_JSON = "data/snapshot_contract.json"
_PUBLIC = "data/public_snapshot.json"
_ZONES_CSV = "data/public_zone_counts_2026-05-29.csv"
_LOCAL = "examples/local_aggregate_input.example.json"
_PROMOTION = "the reviewed promotion of the headline edition under data/sitrep_promotions"
_ENDPOINT = f"{_LIVE} date_semantics.source_clocks.headline_count_endpoint"

_ZONE_TEST = "test_zone_counts_publish_source_attributed_health_zone_rows"
# Every zone cell the zone-count test pins. A zone newly listed by an edition is
# added to the test and to this list by hand.
_ZONE_CELLS = (
    ("bunia", "confirmed"), ("dungu", "confirmed"), ("dungu", "confirmed_deaths"),
    ("mahagi-cod", "confirmed"), ("adja", "confirmed"), ("adja", "confirmed_deaths"),
    ("bunia", "confirmed_deaths"), ("drodro", "confirmed"), ("drodro", "confirmed_deaths"),
    ("fataki", "confirmed"), ("fataki", "confirmed_deaths"), ("musienene", "confirmed"),
    ("musienene", "confirmed_deaths"), ("mabalako", "confirmed"), ("nia-nia", "confirmed"),
    ("mutwanga", "confirmed"), ("biena", "confirmed"), ("manguredjipa", "confirmed"),
    ("ganga", "confirmed"), ("mutwanga", "confirmed_deaths"), ("buta", "confirmed"),
    ("tshopo-cod", "confirmed"), ("yakoma", "confirmed"), ("yakoma", "confirmed_deaths"),
    ("watsa", "confirmed"), ("watsa", "confirmed_deaths"),
)


def _zone_cell_pin(zone: str, field: str) -> Pin:
    return Pin(
        _EXPORTS, _ZONE_TEST,
        f'self.assertEqual("<<v:int>>", by_zone["{zone}"]["{field}"])',
        f"{zone} {field} in the public zone table",
        f"{_ZONES_CSV} row {zone}, column {field}",
        _one(lambda o: o.zone_rows[zone][field]),
    )


def _composition_pin(metric: str, uganda: int) -> Pin:
    return Pin(
        _CONTRACT, "test_contract_captures_current_partition",
        f'{{"total": <<total:int>>, "drc": <<drc:int>>, "uganda": {uganda}}},\n'
        "            {\n"
        f'                key: contract["country_scope_composition"]["{metric}"][key]',
        f"DRC + Uganda and DRC {metric} totals",
        f"{_CONTRACT_JSON} country_scope_composition.{metric}",
        lambda o: o.composition(metric),
    )


_STOCK_PROVINCES = (
    "Ituri", "Nord-Kivu", "Haut-Uele", "Tshopo", "Bas-Uele", "Sud-Kivu", "Sud-Ubangi", "Nord-Ubangi",
)

PINS: tuple[Pin, ...] = (
    Pin(_CYCLE, "test_build_with_fixtures",
        'self.assertEqual(status["analytic_data_date"], "<<v:date>>")',
        "analytic data date of the current snapshot", f"{_LIVE} as_of",
        _one(lambda o: str(o.live["as_of"])[:10])),
    Pin(_CHAINS, MODULE,
        'CURRENT_SITREP_SOURCE = "<<v:source_id>>"',
        "source id of the headline edition", _ENDPOINT,
        _one(lambda o: o.headline_source_id)),
    Pin(_EVIDENCE, "test_may22_zone_attributed_corridor_chain_is_registered",
        '"<<headline:int>> = DRC national <<drc:int>> + separately dated historical Uganda anchor 20",',
        "headline and DRC confirmed in the corridor chain text",
        f"{_CONTRACT_JSON} confirmed_case_partition",
        lambda o: {"headline": o.partition("headline_confirmed_total"), "drc": o.partition("drc_confirmed_total")}),
    Pin(_EVIDENCE, "test_may22_zone_attributed_corridor_chain_is_registered",
        '"<<zones:int>> monitored INSP per-zone source zones",\n            "<<corridors:int>>",',
        "source zone and corridor counts in the corridor chain text",
        f"{_CONTRACT_JSON} corridor_watchlist",
        lambda o: {"zones": o.watchlist("source_zone_count"), "corridors": o.watchlist("corridor_count")}),
    Pin(_EXPORTS, "test_public_snapshot_contains_partner_relevant_fields",
        'self.assertEqual("<<v:date>>", snapshot["data_as_of"])',
        "data date of the public snapshot", f"{_PUBLIC} data_as_of",
        _one(lambda o: str(o.public_snapshot["data_as_of"]))),
    Pin(_EXPORTS, _ZONE_TEST,
        "self.assertEqual(<<v:int>>, len(rows))",
        "row count of the public zone table", f"{_ZONES_CSV} row count",
        _one(lambda o: str(len(o.zone_rows)))),
    *(_zone_cell_pin(zone, field) for zone, field in _ZONE_CELLS),
    Pin(_EXPORTS, _ZONE_TEST,
        'self.assertEqual("<<v:source_id>>", by_zone["bunia"]["source_id"])',
        "source id on the public zone rows", f"{_ZONES_CSV} row bunia, column source_id",
        _one(lambda o: o.zone_rows["bunia"]["source_id"])),
    Pin(_EXPORTS, "test_public_summary_consumer_is_read_only_and_grounded",
        'self.assertIn("confirmed cases: <<v:int>>", result.stdout)',
        "headline confirmed printed by the summary example",
        f"{_PUBLIC} reported_counts.confirmed.primary",
        _one(lambda o: o.public_confirmed())),
    Pin(_EXPORTS, "test_public_summary_consumer_is_read_only_and_grounded",
        'self.assertIn("health-zone rows: <<v:int>>", result.stdout)',
        "zone row count printed by the summary example", f"{_ZONES_CSV} row count",
        _one(lambda o: str(len(o.zone_rows)))),
    Pin(_EXPORTS, "test_public_methodology_review_consumer_is_read_only_and_grounded",
        'self.assertIn("confirmed primary: <<v:int>>", result.stdout)',
        "headline confirmed printed by the methodology example",
        f"{_PUBLIC} reported_counts.confirmed.primary",
        _one(lambda o: o.public_confirmed())),
    Pin(_EXPORTS, "test_local_aggregate_review_consumer_is_read_only_and_grounded",
        'self.assertIn("source-attributed confirmed total: <<v:int>>", result.stdout)',
        "zone-attributed confirmed printed by the local aggregate example",
        f"{_LOCAL} health_zone_counts, summed confirmed",
        _one(lambda o: o.local_zone_confirmed())),
    Pin(_EXPORTS, "test_local_aggregate_review_consumer_is_read_only_and_grounded",
        'self.assertIn("headline confirmed total: <<v:int>>", result.stdout)',
        "headline confirmed printed by the local aggregate example",
        f"{_PUBLIC} reported_counts.confirmed.primary",
        _one(lambda o: o.public_confirmed())),
    Pin(_EXPORTS, "test_local_aggregate_review_consumer_is_read_only_and_grounded",
        'self.assertIn("health-zone rows: <<v:int>>", result.stdout)',
        "zone row count printed by the local aggregate example",
        f"{_LOCAL} health_zone_counts length",
        _one(lambda o: str(len(o.local_example["health_zone_counts"])))),
    Pin(_EXPORTS, "test_local_aggregate_review_accepts_explicit_path",
        'self.assertIn("source-attributed confirmed total: <<v:int>>", result.stdout)',
        "zone-attributed confirmed printed for an explicit input path",
        f"{_LOCAL} health_zone_counts, summed confirmed",
        _one(lambda o: o.local_zone_confirmed())),
    Pin(_EXPORTS, "test_public_adaptation_package_is_self_serve_and_safe",
        'self.assertEqual(<<v:int>>, len(local_input["health_zone_counts"]))',
        "zone row count of the local aggregate example", f"{_LOCAL} health_zone_counts length",
        _one(lambda o: str(len(o.local_example["health_zone_counts"])))),
    *(Pin(_DATASET, "test_sitrep_narrative_export_carries_reviewed_sitrep_47_sections",
          f'self.assertEqual(by_section["{section}"], {{"<<v:source_id>>"}})',
          f"edition that publishes the {section} narrative section", _ENDPOINT,
          _one(lambda o: o.headline_source_id))
      for section in ("challenges", "highlights", "care_continuity")),
    Pin(_DATASET, "test_sitrep_narrative_export_carries_reviewed_sitrep_47_sections",
        '"DRC: <<cases:comma_int>> confirmed cases, <<deaths:comma_int>> confirmed deaths",',
        "DRC confirmed cases and deaths in the narrative highlight",
        f"{_PROMOTION}: figures.cumul_cas_confirmes_drc and cumul_deces_parmi_confirmes_drc",
        lambda o: {"cases": f"{o.promotion['figures']['cumul_cas_confirmes_drc']:,}",
                   "deaths": f"{o.promotion['figures']['cumul_deces_parmi_confirmes_drc']:,}"}),
    Pin(_DATASET, "test_sitrep_narrative_export_carries_reviewed_sitrep_47_sections",
        '"<<v:int>> named zones",',
        "named zone count in the narrative highlight",
        f"{_PROMOTION}: figures.health_zone_table.reconciliation.named_zone_row_count",
        _one(lambda o: str(o.promotion["figures"]["health_zone_table"]["reconciliation"]["named_zone_row_count"]))),
    Pin(_DATASET, "test_sitrep_narrative_export_carries_reviewed_sitrep_47_sections",
        'self.assertIn("SR<<edition:int>> alone reports <<cases:int>> new confirmed cases, '
        '<<deaths:int>> confirmed deaths", text)',
        "edition number and its 24-hour confirmed cases and deaths in the narrative highlight",
        f"{_PROMOTION}: sitrep_number, figures.new_confirmed_24h and new_confirmed_deaths_24h",
        lambda o: {"edition": str(o.promotion["sitrep_number"]),
                   "cases": str(o.promotion["figures"]["new_confirmed_24h"]),
                   "deaths": str(o.promotion["figures"]["new_confirmed_deaths_24h"])}),
    Pin(_DATASET, "test_sitrep_narrative_export_carries_reviewed_sitrep_47_sections",
        'self.assertIn("<<seen:int>>/<<followed:int>> (<<rate:percent>>%)", text)',
        "contacts seen, under follow-up and the printed rate in the narrative highlight",
        f"{_PROMOTION}: figures.operational_tables.contacts_total",
        lambda o: (lambda c: {"seen": str(c["contacts_seen_24h"]),
                              "followed": str(c["contacts_under_follow_up"]),
                              "rate": str(c["followup_rate_pct"])})(
            o.promotion["figures"]["operational_tables"]["contacts_total"])),
    Pin(_DATASET, "test_snapshot_clocks_preserve_publication_report_and_retrieval_dates",
        '"<<v:date>>",\n            by_id["snapshot:publication_cutoff"]["date_value"],',
        "publication cutoff clock of the dataset",
        "deliverables/public-health-dataset/snapshot_clocks.csv row snapshot:publication_cutoff",
        _one(lambda o: o.snapshot_clocks["snapshot:publication_cutoff"]["date_value"])),
    Pin(_DATASET, "test_analysis_dependency_audit_exports_model_use_and_holdouts",
        'self.assertIn("<<v:int>>", by_surface["visibility_module_c"]["input_values"])',
        "headline confirmed on the visibility input surface", f"{_LIVE} reported_counts.confirmed.primary",
        _one(lambda o: o.headline("reported_counts"))),
    Pin(_DATASET, "test_analysis_dependency_audit_exports_model_use_and_holdouts",
        'self.assertIn("<<v:int>>", by_surface["death_back_projection_and_grid"]["input_values"])',
        "headline confirmed deaths on the death back-projection surface",
        f"{_LIVE} reported_deaths.confirmed.primary",
        _one(lambda o: o.headline("reported_deaths"))),
    Pin(_DATASET, "test_analysis_dependency_audit_exports_model_use_and_holdouts",
        '"SitRep #<<v:int>>",\n            by_surface["death_back_projection_and_grid"]["clock_basis"],',
        "edition number of the confirmed-death endpoint", f"{_PROMOTION}: sitrep_number",
        _one(lambda o: str(o.promotion["sitrep_number"]))),
    *(Pin(_DATASET, "test_analysis_dependency_audit_exports_model_use_and_holdouts",
          f'self.assertEqual(<<v:int>>, corridor_inputs["{name}"])',
          f"{name} on the corridor watchlist surface", f"{_CONTRACT_JSON} confirmed_case_partition.{key}",
          _one(lambda o, key=key: o.partition(key)))
      for name, key in (("headline_confirmed", "headline_confirmed_total"),
                        ("drc_confirmed", "drc_confirmed_total"),
                        ("zone_attributed_confirmed", "zone_attributed_confirmed_total"))),
    Pin(_DATASET, "test_analysis_dependency_audit_exports_model_use_and_holdouts",
        '"Against the DRC national count of <<v:int>>, 0 DRC confirmed are unallocated",',
        "DRC confirmed in the corridor attribution sentence",
        f"{_CONTRACT_JSON} confirmed_case_partition.drc_confirmed_total",
        _one(lambda o: o.partition("drc_confirmed_total"))),
    Pin(_DATASET, "test_analysis_dependency_audit_exports_model_use_and_holdouts",
        'self.assertIn("<<v:source_id>>", by_surface["corridor_watchlist"]["blocked_by"])',
        "source id of the zone table behind the corridor watchlist", _ENDPOINT,
        _one(lambda o: o.headline_source_id)),
    Pin(_RESPONSE, MODULE,
        "def test_headline_<<cases:int>>_<<deaths:int>>_current(self) -> None:",
        "headline confirmed cases and deaths in the test name", f"{_LIVE} reported_counts and reported_deaths",
        lambda o: {"cases": o.headline("reported_counts"), "deaths": o.headline("reported_deaths")}),
    Pin(_RESPONSE, r"test_headline_\d+_\d+_current",
        'self.assertEqual(live["reported_counts"]["confirmed"]["primary"], <<v:int>>)',
        "headline confirmed cases", f"{_LIVE} reported_counts.confirmed.primary",
        _one(lambda o: o.headline("reported_counts"))),
    Pin(_RESPONSE, r"test_headline_\d+_\d+_current",
        'self.assertEqual(live["reported_deaths"]["confirmed"]["primary"], <<v:int>>)',
        "headline confirmed deaths", f"{_LIVE} reported_deaths.confirmed.primary",
        _one(lambda o: o.headline("reported_deaths"))),
    Pin(_RESPONSE, "test_generated_snapshot_carries_per_zone_layer",
        '{"Ituri": <<Ituri:optional_int>>, "Nord-Kivu": <<NordKivu:optional_int>>, '
        '"Haut-Uele": <<HautUele:optional_int>>, "Tshopo": <<Tshopo:optional_int>>, '
        '"Bas-Uele": <<BasUele:optional_int>>,\n'
        '             "Sud-Kivu": <<SudKivu:optional_int>>, "Sud-Ubangi": <<SudUbangi:optional_int>>, '
        '"Nord-Ubangi": <<NordUbangi:optional_int>>},',
        "patients in isolation by province",
        f"{_PUBLIC} responseState.provinceCurrent.byProvince[*].patientsInIsolation",
        lambda o: {province.replace("-", ""): o.province_stock(province) for province in _STOCK_PROVINCES}),
    Pin(_RESPONSE, "test_generated_snapshot_carries_per_zone_layer",
        "self.assertEqual(<<v:int>>, sum(v for v in stocks.values() if v is not None))",
        "sum of the printed province stocks",
        f"{_PUBLIC} responseState.provinceCurrent.byProvince[*].patientsInIsolation, summed",
        _one(lambda o: o.province_stock_sum())),
    Pin(_RESPONSE, "test_generated_snapshot_clock_distinct_from_headline",
        'self.assertEqual(self.response["data_as_of"], "<<v:date>>")',
        "data date of the response-state block", f"{_PUBLIC} responseState.data_as_of",
        _one(lambda o: str(o.public_snapshot["responseState"]["data_as_of"]))),
    Pin(_RESPONSE, "test_generated_snapshot_clock_distinct_from_headline",
        'self.assertTrue(self.snapshot["as_of"].startswith("<<v:date>>"))',
        "date of the public snapshot", f"{_PUBLIC} as_of",
        _one(lambda o: str(o.public_snapshot["as_of"])[:10])),
    Pin(_CONTRACT, "test_stale_combined_residual_prose_fails_the_narrative_gate",
        'self.assertIn("DRC national count is <<v:int>> confirmed cases", fragments)',
        "DRC confirmed in the required narrative fragment",
        f"{_CONTRACT_JSON} confirmed_case_partition.drc_confirmed_total",
        _one(lambda o: o.partition("drc_confirmed_total"))),
    Pin(_CONTRACT, "test_stale_combined_residual_prose_fails_the_narrative_gate",
        '"headline is <<headline:int>> confirmed cases; the DRC national count is <<drc:int>> confirmed "\n'
        '            "cases. Corridor risk uses <<zone:int>> confirmed cases that are officially "',
        "headline, DRC and zone-attributed confirmed in the current narrative sentence",
        f"{_CONTRACT_JSON} confirmed_case_partition",
        lambda o: {"headline": o.partition("headline_confirmed_total"),
                   "drc": o.partition("drc_confirmed_total"),
                   "zone": o.partition("zone_attributed_confirmed_total")}),
    *(Pin(_CONTRACT, "test_contract_captures_current_partition",
          f'self.assertEqual(<<v:int>>, contract["confirmed_case_partition"]["{key}"])',
          f"{key} of the confirmed-case partition", f"{_CONTRACT_JSON} confirmed_case_partition.{key}",
          _one(lambda o, key=key: o.partition(key)))
      for key in ("headline_confirmed_total", "drc_confirmed_total", "zone_attributed_confirmed_total")),
    *(Pin(_CONTRACT, "test_contract_captures_current_partition",
          f'self.assertEqual(<<v:int>>, contract["corridor_watchlist"]["{key}"])',
          f"{key} of the corridor watchlist", f"{_CONTRACT_JSON} corridor_watchlist.{key}",
          _one(lambda o, key=key: o.watchlist(key)))
      for key in ("source_zone_count", "corridor_count")),
    _composition_pin("confirmed", 20),
    _composition_pin("confirmed_deaths", 2),
    _composition_pin("recovered", 18),
)


def check_table(pins: tuple[Pin, ...] = PINS) -> None:
    """Refuse a table that names a frozen value or a file outside the tests."""
    for pin in pins:
        text = f"{pin.file} {pin.test} {pin.anchor}".lower()
        marker = next((marker for marker in FROZEN_MARKERS if marker in text), None)
        if marker:
            raise PinTableError(f"{pin.label}: names a frozen value ({marker!r}); frozen values are never advanced")
        if not re.fullmatch(r"tests/test_\w+\.py", pin.file):
            raise PinTableError(f"{pin.label}: only test files are pinned")
        if not _SLOT.search(pin.anchor):
            raise PinTableError(f"{pin.label}: the anchor has no slot")


def _anchor_pattern(anchor: str) -> re.Pattern[str]:
    parts: list[str] = []
    position = 0
    for match in _SLOT.finditer(anchor):
        name, kind = match.groups()
        if kind not in SLOT_KINDS:
            raise PinTableError(f"unknown slot kind {kind!r} in anchor {anchor!r}")
        parts.append(re.escape(anchor[position:match.start()]))
        parts.append(f"(?P<{name}>{SLOT_KINDS[kind]})")
        position = match.end()
    parts.append(re.escape(anchor[position:]))
    return re.compile("".join(parts))


def _scope(text: str, pin: Pin) -> tuple[int, int]:
    """The character span of the pin's test function, or the whole file."""
    if pin.test == MODULE:
        return 0, len(text)
    found = [m for m in re.finditer(r"^([ \t]*)def (\w+)\(", text, re.MULTILINE) if re.fullmatch(pin.test, m.group(2))]
    if len(found) != 1:
        raise PinTableError(f"{pin.label}: expected one test named {pin.test!r}, found {len(found)}")
    start = found[0].start()
    indent = len(found[0].group(1))
    following = re.compile(rf"^[ \t]{{0,{indent}}}(?:def|class|@)\b", re.MULTILINE).search(text, found[0].end())
    return start, following.start() if following else len(text)


@dataclasses.dataclass(frozen=True)
class StalePin:
    pin: Pin
    slot: str
    line: int
    pinned: str
    current: str


def evaluate(output_root: pathlib.Path = REPO_ROOT, pin_root: pathlib.Path = REPO_ROOT,
             pins: tuple[Pin, ...] = PINS) -> tuple[list[StalePin], dict[str, tuple[str, str]]]:
    """Return the stale pins and, per changed file, its (current, corrected) text."""
    check_table(pins)
    outputs = Outputs(output_root)
    stale: list[StalePin] = []
    originals: dict[str, str] = {}
    edits: dict[str, list[tuple[int, int, str]]] = {}
    for pin in pins:
        if pin.file not in originals:
            path = pin_root / pin.file
            if not path.is_file():
                raise PinTableError(f"{pin.label}: pinned file is missing")
            originals[pin.file] = path.read_text(encoding="utf-8")
        text = originals[pin.file]
        start, end = _scope(text, pin)
        pattern = _anchor_pattern(pin.anchor)
        matches = list(pattern.finditer(text, start, end))
        if len(matches) != 1:
            raise PinTableError(
                f"{pin.label}: expected the anchor once in {pin.test}, found {len(matches)}; "
                "correct the pin table by hand"
            )
        match = matches[0]
        try:
            values = pin.values(outputs)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise PinTableError(f"{pin.label}: cannot read {pin.source} ({type(exc).__name__}: {exc})") from exc
        if set(values) != set(pattern.groupindex):
            raise PinTableError(f"{pin.label}: the source does not supply the anchor's slots")
        for slot, current in values.items():
            kind = dict((name, kind) for name, kind in _SLOT.findall(pin.anchor))[slot]
            if not re.fullmatch(SLOT_KINDS[kind], current):
                raise PinTableError(
                    f"{pin.label}: {pin.source} gives {current!r}, which is not a {kind}; "
                    "the assertion changes shape and is edited by hand"
                )
            pinned = match.group(slot)
            if pinned != current:
                line = text.count("\n", 0, match.start(slot)) + 1
                stale.append(StalePin(pin, slot, line, pinned, current))
                edits.setdefault(pin.file, []).append((match.start(slot), match.end(slot), current))
    changed: dict[str, tuple[str, str]] = {}
    for file, spans in edits.items():
        text = originals[file]
        previous_start = len(text) + 1
        for span_start, span_end, replacement in sorted(set(spans), reverse=True):
            if span_end > previous_start:
                raise PinTableError(f"{file}: two pins overlap; correct the pin table by hand")
            text = text[:span_start] + replacement + text[span_end:]
            previous_start = span_start
        changed[file] = (originals[file], text)
    return stale, changed


def unified_diff(changed: dict[str, tuple[str, str]]) -> str:
    chunks: list[str] = []
    for file in sorted(changed):
        before, after = changed[file]
        chunks.extend(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{file}", tofile=f"b/{file}",
        ))
    return "".join(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="Rewrite the stale pins. Without it nothing is written.")
    parser.add_argument("--list", action="store_true", help="Print the pin table and exit.")
    parser.add_argument("--output-root", type=pathlib.Path, default=REPO_ROOT,
                        help="Repository holding the regenerated outputs (default: this repository).")
    parser.add_argument("--pin-root", type=pathlib.Path, default=REPO_ROOT,
                        help="Repository holding the pinned test files (default: this repository).")
    args = parser.parse_args(argv)
    if args.list:
        for pin in PINS:
            print(f"{pin.file} | {pin.test} | {pin.pins} | {pin.source}")
        return 0
    try:
        stale, changed = evaluate(args.output_root, args.pin_root)
    except PinTableError as exc:
        sys.stderr.write(f"[FAIL] edition pins: {exc}\n")
        return 2
    if not stale:
        print(f"Edition pins current ({len(PINS)} pins checked).")
        return 0
    sys.stdout.write(unified_diff(changed))
    print(f"\n{len(stale)} stale value(s) in {len({item.pin for item in stale})} of {len(PINS)} pins:")
    for item in stale:
        print(f"    {item.pin.file}:{item.line} {item.pin.pins}: {item.pinned} -> {item.current} ({item.pin.source})")
    if not args.write:
        print("Dry run: nothing written. Read the diff, then rerun with --write.")
        return 1
    for file, (_, after) in changed.items():
        (args.pin_root / file).write_text(after, encoding="utf-8")
    print(f"Wrote {len(changed)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
