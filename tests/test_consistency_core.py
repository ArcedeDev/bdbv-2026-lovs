# SPDX-License-Identifier: Apache-2.0
"""Tests for the lane-neutral consistency core (lovs/consistency/core.py).

All figures are synthetic. A second, invented lane ("malaria-demo") runs
through the same primitives to show the core carries no lane knowledge.
"""
from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest

from lovs.consistency import core
from lovs.consistency.core import (
    INCOMPLETE,
    MISMATCH,
    OK,
    UNDEFINED,
    Candidate,
    Finding,
    Observation,
    ResidueEntry,
    carried_forward_lags,
    difference_status,
    find_note,
    number_tokens,
    rate_status,
    round_half_up,
    sum_status,
    tuple_matches,
    write_ledger,
)

EM_DASH = chr(0x2014)
NBSP = chr(0x00A0)


# ---------------------------------------------------------------------------
# Rounding and rate reproduction


def test_round_half_up_differs_from_builtin_round_at_half():
    # Python's round() is banker's / float-representation sensitive.
    assert round(2.675, 2) == 2.67
    assert round_half_up(2.675, 2) == Decimal("2.68")
    assert round(0.125, 2) == 0.12
    assert round_half_up(0.125, 2) == Decimal("0.13")
    assert round(2.5) == 2
    assert round_half_up(2.5, 0) == Decimal("3")
    assert round_half_up(94.65, 1) == Decimal("94.7")


def test_round_half_up_accepts_decimal_and_zero_decimals():
    assert round_half_up(Decimal("10.45"), 1) == Decimal("10.5")
    assert round_half_up(7, 0) == Decimal("7")


def test_rate_status_707_over_747_is_946_not_947():
    # 707 / 747 = 94.645..., so the one-decimal rate is 94.6.
    assert rate_status(707, 747, 94.6, 1) == OK
    assert rate_status(707, 747, 94.7, 1) == MISMATCH


def test_rate_status_ok_mismatch_undefined_incomplete():
    assert rate_status(1, 3, 33.3, 1) == OK
    assert rate_status(2, 3, 66.7, 1) == OK
    assert rate_status(2, 3, 66.6, 1) == MISMATCH
    assert rate_status(5, 0, 0.0, 1) == UNDEFINED
    assert rate_status(None, 10, 50.0, 1) == INCOMPLETE
    assert rate_status(5, None, 50.0, 1) == INCOMPLETE
    assert rate_status(5, 10, None, 1) == INCOMPLETE


def test_rate_status_half_up_at_printed_precision():
    # 1 / 8 = 12.5 exactly: half-up to 0 decimals gives 13.
    assert rate_status(1, 8, 13, 0) == OK
    assert rate_status(1, 8, 12, 0) == MISMATCH
    # Printed value is itself rounded at the printed precision.
    assert rate_status(1, 8, 12.5, 1) == OK


def test_sum_status():
    assert sum_status([1, 2, 3], 6) == OK
    assert sum_status([1, 2, 3], 7) == MISMATCH
    assert sum_status([1, None, 3], 4) == INCOMPLETE
    assert sum_status([], 0) == INCOMPLETE
    assert sum_status([1, 2], None) == INCOMPLETE
    assert sum_status(iter([4, 5]), 9) == OK


def test_difference_status():
    assert difference_status(10, 4, 6) == OK
    assert difference_status(10, 4, 5) == MISMATCH
    assert difference_status(None, 4, 6) == INCOMPLETE
    assert difference_status(10, None, 6) == INCOMPLETE
    assert difference_status(10, 4, None) == INCOMPLETE


# ---------------------------------------------------------------------------
# Tuple matching and carry-forward


RATE = {"pct": ("inv", "val", 1)}


def test_tuple_matches_requires_every_printed_element():
    target = {"inv": 50, "val": 60, "province": "Zone-A"}
    ok, matched = tuple_matches(target, {"inv": 50, "val": 60, "province": "Zone-A", "extra": 1})
    assert ok and sorted(matched) == ["inv", "province", "val"]
    ok, matched = tuple_matches(target, {"inv": 50, "val": 60, "province": "Zone-B"})
    assert not ok and sorted(matched) == ["inv", "val"]
    ok, matched = tuple_matches(target, {"inv": 50, "val": 60})
    assert not ok and sorted(matched) == ["inv", "val"]


def test_tuple_matches_ignores_unprinted_fields():
    ok, matched = tuple_matches({"inv": 50, "val": None}, {"inv": 50, "val": 999})
    assert ok and matched == ["inv"]


def test_tuple_matches_all_none_target_is_not_a_match():
    assert tuple_matches({"inv": None}, {"inv": 1}) == (False, [])


def test_tuple_matches_rate_field_via_counts():
    target = {"pct": 94.6}
    assert tuple_matches(target, {"inv": 707, "val": 747}, rate_fields=RATE) == (True, ["pct"])
    assert tuple_matches({"pct": 94.7}, {"inv": 707, "val": 747}, rate_fields=RATE)[0] is False
    # A predicted "pct" key is not enough: the rate is reproduced only by counts.
    assert tuple_matches(target, {"pct": 94.6}, rate_fields=RATE)[0] is False
    # Without rate_fields the field is compared exactly.
    assert tuple_matches(target, {"pct": 94.6})[0] is True


def test_tuple_matches_empty_or_missing_predicted():
    assert tuple_matches({"inv": 1}, None) == (False, [])
    assert tuple_matches({"inv": 1}, {}) == (False, [])


def test_carried_forward_lags():
    history = {
        7: {"inv": 40, "val": 44},
        8: {"inv": 41, "val": 45},
        9: {"inv": 40, "val": 44},
    }
    assert carried_forward_lags(10, {"inv": 40, "val": 44}, history, max_lag=3) == [1, 3]
    assert carried_forward_lags(10, {"inv": 40, "val": 44}, history, max_lag=2) == [1]
    assert carried_forward_lags(10, {"inv": 99, "val": 44}, history, max_lag=3) == []
    # Rate fields carry forward through their counts.
    assert carried_forward_lags(10, {"pct": 90.9}, history, max_lag=3, rate_fields=RATE) == [1, 3]


# ---------------------------------------------------------------------------
# Notes


def test_number_tokens():
    assert number_tokens(1413) == {"1413", "1,413", "1 413", "1" + NBSP + "413"}
    assert number_tokens(95.6) == {"95.6", "95,6"}
    assert number_tokens(12.0) == {"12", "12.0", "12,0"}
    assert number_tokens(True) == set()


def test_find_note_matches_grouped_and_spaced_thousands():
    assert find_note(["total revu 1,413 cas"], 1413) == "total revu 1,413 cas"
    assert find_note(["total revu 1 413 cas"], 1413) == "total revu 1 413 cas"
    assert find_note(["total revu 1" + NBSP + "413 cas"], 1413) is not None
    assert find_note(["total revu 1413 cas"], 1413) is not None


def test_find_note_matches_french_decimal_comma():
    assert find_note(["taux imprime 95,6 %"], 95.6) == "taux imprime 95,6 %"
    assert find_note(["taux imprime 95.6 %"], 95.6) is not None


def test_find_note_rejects_substring_hits():
    assert find_note(["612 cas"], 61) is None
    assert find_note(["2612 cas"], 61) is None
    assert find_note(["0,61 cas"], 61) is None
    assert find_note(["61,5 cas"], 61) is None
    assert find_note(["95,61 %"], 95.6) is None
    assert find_note(["61 cas."], 61) == "61 cas."


def test_find_note_requires_all_values_and_returns_first():
    notes = ["imprime 94,7 seulement", "imprime 94,7 contre 94,6 recalcule", "94,7 / 94,6 bis"]
    assert find_note(notes, 94.7, 94.6) == notes[1]
    assert find_note(notes[:1], 94.7, 94.6) is None
    # None values are skipped; no values at all gives no note.
    assert find_note(notes, 94.7, None) == notes[0]
    assert find_note(notes) is None
    assert find_note(notes, None) is None


def test_find_note_whole_valued_rate_printed_with_decimal():
    # A whole-valued rate is matched at printed precision ("95,0") and bare ("95").
    assert find_note(["taux imprime 95,0 % contre 94,6 % recalcule"], 95.0, 94.6) is not None
    assert find_note(["taux imprime 95 % contre 94,6 %"], 95.0, 94.6) is not None


# ---------------------------------------------------------------------------
# Findings and the residue ledger


def _finding(**overrides):
    base = dict(lane="lane-x", edition=3, scope="national", relation="a = b / c", section="Table 1",
                printed=10, expected=11, status=MISMATCH)
    base.update(overrides)
    return Finding(**base)


def test_finding_unexplained():
    assert _finding().unexplained is True
    assert _finding(covered_by="note printing 10 and 11").unexplained is False
    assert _finding(status=OK).unexplained is False
    assert _finding(status=INCOMPLETE).unexplained is False


def test_finding_residue_fields():
    entry = _finding().residue(["rate", "sum"], note="n")
    assert entry == ResidueEntry(lane="lane-x", edition=3, scope="national", field="a = b / c",
                                 section="Table 1", printed=10, relation="expected 11", status=MISMATCH,
                                 candidates_tried=["rate", "sum"], note="n")


def test_write_ledger_sorted_deduplicated_and_byte_identical(tmp_path: pathlib.Path):
    entries = [
        _finding(edition=9).residue(["rate"]),
        _finding(edition=2).residue(["rate"]),
        _finding(edition=5, printed="Zone-é").residue(["rate"]),
        _finding(edition=2).residue(["rate"]),
    ]
    path = tmp_path / "nested" / "residue.jsonl"
    assert write_ledger(path, entries) == 3
    first = path.read_bytes()
    lines = first.decode("utf-8").splitlines()
    assert lines == sorted(lines)
    assert first.endswith(b"\n")
    assert "Zone-é" in first.decode("utf-8")
    assert write_ledger(path, list(reversed(entries))) == 3
    assert path.read_bytes() == first
    assert not path.with_suffix(".jsonl.tmp").exists()


def test_write_ledger_empty(tmp_path: pathlib.Path):
    path = tmp_path / "residue.jsonl"
    assert write_ledger(path, []) == 0
    assert path.read_bytes() == b""


def test_write_ledger_rejects_em_dash(tmp_path: pathlib.Path):
    path = tmp_path / "residue.jsonl"
    bad = _finding().residue(["rate"], note="a " + EM_DASH + " b")
    with pytest.raises(ValueError):
        write_ledger(path, [bad])
    assert not path.exists()


def test_observation_key_keeps_sections_apart():
    a = Observation("lane-x", 4, "national", "inv", "Table 1", 12)
    b = Observation("lane-x", 4, "national", "inv", "Key messages", 13)
    assert a.key != b.key
    assert a.key == ("lane-x", 4, "national", "inv", "Table 1")


# ---------------------------------------------------------------------------
# Lane neutrality


def test_second_lane_runs_on_core_only(tmp_path: pathlib.Path):
    lane = "malaria-demo"
    rate = {"positivity_pct": ("rdt_positive", "rdt_tested", 1)}
    weekly = {
        20: {"rdt_positive": 312, "rdt_tested": 1200, "district_a": 200, "district_b": 112},
        21: {"rdt_positive": 330, "rdt_tested": 1250, "district_a": 210, "district_b": 121},
        22: {"rdt_positive": 330, "rdt_tested": 1250, "district_a": 210, "district_b": 121},
    }
    printed_pct = {20: 26.0, 21: 26.4, 22: 26.5}
    observations = [
        Observation(lane, week, "region", "positivity_pct", "Bulletin table", printed_pct[week], f"{printed_pct[week]}", 1)
        for week in weekly
    ]
    findings = []
    for obs in observations:
        row = weekly[obs.edition]
        status = rate_status(row["rdt_positive"], row["rdt_tested"], obs.value, obs.decimals)
        expected = float(round_half_up(Decimal(row["rdt_positive"]) * 100 / Decimal(row["rdt_tested"]), 1))
        findings.append(Finding(lane, obs.edition, obs.scope, "positivity_pct = rdt_positive / rdt_tested",
                                obs.section, obs.value, expected, status))
        parts_status = sum_status([row["district_a"], row["district_b"]], row["rdt_positive"])
        findings.append(Finding(lane, obs.edition, obs.scope, "rdt_positive = sum of districts",
                                obs.section, row["rdt_positive"], row["district_a"] + row["district_b"], parts_status))
    by_week = {(f.edition, f.relation.split(" ")[0]): f.status for f in findings}
    assert by_week[(20, "positivity_pct")] == OK
    assert by_week[(21, "positivity_pct")] == OK
    assert by_week[(22, "positivity_pct")] == MISMATCH  # 330 / 1250 = 26.4
    assert by_week[(21, "rdt_positive")] == MISMATCH  # 210 + 121 = 331
    assert carried_forward_lags(22, {"rdt_positive": 330, "rdt_tested": 1250}, weekly, max_lag=2) == [1]
    assert tuple_matches({"positivity_pct": 26.4}, weekly[22], rate_fields=rate)[0] is True

    candidate = Candidate("M1", "previous week carried", lambda week: weekly.get(week - 1))
    assert candidate.generate(22) == weekly[21]
    assert candidate.hypotheses == 1

    residue = [f.residue(["rate", "sum"]) for f in findings if f.unexplained]
    assert {r.lane for r in residue} == {lane}
    path = tmp_path / "malaria-residue.jsonl"
    assert write_ledger(path, residue) == 3
    assert all('"lane": "malaria-demo"' in line for line in path.read_text(encoding="utf-8").splitlines())


def test_core_source_names_no_lane():
    source = pathlib.Path(core.__file__).read_text(encoding="utf-8")
    assert "bdbv" not in source.lower()
    assert "Ituri" not in source


def test_find_note_ignores_identifiers_dates_and_grouped_numbers():
    assert find_note(["see SR153 of 2026-10-16"], 153) is None
    assert find_note(["see SR153 of 2026-10-16"], 16) is None
    assert find_note(["total 1 234 alerts"], 234) is None
    assert find_note(["total 1 234 alerts"], 1234) == "total 1 234 alerts"
    assert find_note(["26 restent en Ituri"], 26) == "26 restent en Ituri"
    assert find_note(["implying100% verification and18.1% validated"], 18.1) is not None
