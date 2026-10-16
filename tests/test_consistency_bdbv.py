# SPDX-License-Identifier: Apache-2.0
"""Tests for the BDBV lane adapter (lovs/consistency/bdbv.py).

Every text snippet and promotion below is synthetic: invented French wording
in the style of a SitRep and invented counts.
"""
from __future__ import annotations

import pytest

from lovs.consistency import bdbv, judge
from lovs.consistency.core import INCOMPLETE, MISMATCH, OK, tuple_matches

NBSP = chr(0x00A0)
NNBSP = chr(0x202F)


# ---------------------------------------------------------------------------
# Key messages


def test_key_message_counts_form_with_remainder():
    text = (
        "Situation generale du jour.\n"
        "MESSAGES CLES\n"
        "- Au total, 506 cas suspects sur 513 (98,6 %) ont été investigués ce jour ;\n"
        "- 7 cas suspects restent non investigués en Ituri."
    )
    km = bdbv.extract_key_message(text)
    assert km.status == bdbv.PRESENT
    assert km.figures == {"inv": 506, "val": 513, "pct": 98.6, "not_inv": 7, "province": "Ituri"}
    assert km.decimals == 1
    assert "506 cas suspects sur 513" in km.evidence


def test_key_message_accented_header_and_thousands():
    text = "MESSAGES CLÉS : 1 413 cas suspects sur 1" + NBSP + "420 (99,5" + NNBSP + "%) ont été investigués."
    km = bdbv.extract_key_message(text)
    assert km.status == bdbv.PRESENT
    assert km.figures == {"inv": 1413, "val": 1420, "pct": 99.5}


def test_key_message_slash_form():
    text = "MESSAGES CLES 498/513 suspects ont été investigués au cours des 24 heures (97,1 %)."
    km = bdbv.extract_key_message(text)
    assert km.status == bdbv.PRESENT
    assert km.figures == {"inv": 498, "val": 513, "pct": 97.1}
    assert km.decimals == 1


def test_key_message_rate_near_statement():
    text = "MESSAGES CLES Ce jour, 97,3 % des cas suspects ont été investigués."
    km = bdbv.extract_key_message(text)
    assert km.status == bdbv.PRESENT
    assert km.figures == {"pct": 97.3}
    assert km.decimals == 1


def test_key_message_bare_rate_without_percent_sign():
    text = "MESSAGES CLES Ce jour, 99,9 des cas suspects ont été investigués."
    km = bdbv.extract_key_message(text)
    assert km.status == bdbv.PRESENT
    assert km.figures == {"pct": 99.9}


def test_key_message_integer_rate_has_zero_decimals():
    km = bdbv.extract_key_message("MESSAGES CLES 100 % des cas suspects ont été investigués.")
    assert km.status == bdbv.PRESENT and km.figures == {"pct": 100.0} and km.decimals == 0


def test_key_message_absent_without_region():
    km = bdbv.extract_key_message("506 cas suspects sur 513 (98,6 %) ont été investigués.")
    assert km.status == bdbv.ABSENT and km.figures == {}


def test_key_message_absent_without_investigation_statement():
    km = bdbv.extract_key_message("MESSAGES CLES Le suivi des contacts reste a 88,2 %.")
    assert km.status == bdbv.ABSENT


def test_key_message_unparseable_when_statement_has_no_tuple():
    km = bdbv.extract_key_message("MESSAGES CLES Les cas suspects ont été investigués dans 3 zones de sante.")
    assert km.status == bdbv.UNPARSEABLE
    assert km.figures == {}
    assert "investigu" in km.evidence


@pytest.mark.parametrize("text", [
    "MESSAGES CLES 88,2 % des contacts suivis ; 412 cas suspects ont été investigués.",
    "MESSAGES CLES Les cas suspects ont été investigués ; suivi des contacts : 88,2 %.",
])
def test_key_message_percent_near_contact_follow_up_is_not_taken(text: str):
    km = bdbv.extract_key_message(text)
    assert km.status != bdbv.PRESENT
    assert 88.2 not in km.figures.values()
    assert km.status == bdbv.UNPARSEABLE


def test_extract_body_counts_and_remainder():
    text = (
        "Sur ces 120 cas suspects, 115 (95,8 %) ont été investigués. "
        "5 cas suspects n'ont pas été investigués en Ituri. "
        "MESSAGES CLES 1 cas suspects sur 2 (50,0 %) ont été investigués."
    )
    assert bdbv.extract_body(text) == {"val": 120, "inv": 115, "not_inv": 5, "province": "Ituri"}


def test_extract_body_all_investigated_and_none():
    assert bdbv.extract_body("Les 64 cas suspects ont tous été investigués.") == {"val": 64, "inv": 64, "not_inv": 0}
    assert bdbv.extract_body("Rien a signaler.") is None


# ---------------------------------------------------------------------------
# Small parsers


def test_parse_int_with_space_thousands():
    assert bdbv.parse_int("1 413") == 1413
    assert bdbv.parse_int("1" + NBSP + "413") == 1413
    assert bdbv.parse_int("12 345 678") == 12345678
    assert bdbv.parse_int("42") == 42


def test_parse_pct():
    assert bdbv.parse_pct("98,6") == (98.6, 1)
    assert bdbv.parse_pct("98.65") == (98.65, 2)
    assert bdbv.parse_pct("100") == (100.0, 0)


@pytest.mark.parametrize("raw, canonical", [
    ("Ituri", "Ituri"),
    ("ituri", "Ituri"),
    ("Nord Kivu", "Nord-Kivu"),
    ("NORD-KIVU", "Nord-Kivu"),
    ("Haut-Uélé", "Haut-Uele"),
    ("Sud Kivu", "Sud-Kivu"),
    ("Kasai Central", "Kasai-Central"),
])
def test_canonical_province(raw: str, canonical: str):
    assert bdbv.canonical_province(raw) == canonical


# ---------------------------------------------------------------------------
# Promotions and editions


def promotion(number: int = 120, *, total: dict | None = None, rows: list | None = None,
              conflicts: list | None = None, lab: dict | None = None, poe: dict | None = None) -> dict:
    tables: dict = {
        "alerts_total": total if total is not None else {
            "validated_as_suspect": 100, "suspects_investigated": 96, "suspects_transferred": 40,
            "alerts_reported": 300, "alerts_verified_24h": 280,
        },
    }
    if rows is not None:
        tables["alerts_by_province"] = rows
    if lab is not None:
        tables["lab_indicators_24h"] = lab
    if poe is not None:
        tables["poe_poc_national"] = poe
    return {"sitrep_number": number, "figures": {"operational_tables": tables, "preserved_conflicts": conflicts or []}}


ROWS = [
    {"province": "Ituri", "validated_living": 50, "validated_deaths": 2, "suspects_investigated": 48},
    {"province": "Nord Kivu", "validated_living": 30, "validated_deaths": 0, "suspects_investigated": 30},
    {"province": "Tshopo", "validated_living": 18, "suspects_investigated": 18},
]


def test_edition_from_promotion():
    payload = promotion(rows=ROWS + [{"province": 7}, "junk"], conflicts=["note a", 3],
                        lab={"samples_analyzed": 33, "samples_positive": 4},
                        poe={"validated_live_suspects": 2})
    edition = bdbv.edition_from_promotion(payload)
    assert edition is not None
    assert edition.number == 120
    assert edition.total == {"val": 100, "inv": 96, "transferred": 40}
    assert set(edition.provinces) == {"Ituri", "Nord-Kivu", "Tshopo"}
    assert edition.provinces["Ituri"] == {"val": 52, "val_living": 50, "inv": 48, "transferred": None}
    assert edition.provinces["Tshopo"]["val"] == 18
    assert edition.lab == {"tested": 33, "positive": 4}
    assert edition.poe == {"validated_live_suspects": 2}
    assert edition.alerts == {"received": 300, "verified": 280}
    assert edition.preserved_conflicts == ["note a"]


def test_edition_from_promotion_needs_validated_suspects():
    assert bdbv.edition_from_promotion(promotion(total={"alerts_reported": 10})) is None
    assert bdbv.edition_from_promotion(promotion(total={"validated_as_suspect": True})) is None
    assert bdbv.edition_from_promotion({"sitrep_number": 1}) is None


def test_national_figures_province_only_when_exactly_one_short():
    edition = bdbv.edition_from_promotion(promotion(rows=ROWS))
    assert bdbv.national_figures(edition) == {"val": 100, "inv": 96, "not_inv": 4, "province": "Ituri"}
    two_short = [dict(ROWS[0]), dict(ROWS[1], suspects_investigated=29), ROWS[2]]
    edition = bdbv.edition_from_promotion(promotion(rows=two_short))
    assert "province" not in bdbv.national_figures(edition)
    none_short = [dict(ROWS[0], suspects_investigated=52), ROWS[1], ROWS[2]]
    edition = bdbv.edition_from_promotion(promotion(rows=none_short))
    assert "province" not in bdbv.national_figures(edition)


def test_national_figures_reproduce_key_message():
    edition = bdbv.edition_from_promotion(promotion(rows=ROWS))
    km = {"inv": 96, "val": 100, "pct": 96.0, "not_inv": 4, "province": "Ituri"}
    assert tuple_matches(km, bdbv.national_figures(edition), rate_fields=bdbv.rate_fields_for(1))[0]
    assert bdbv.rate_fields_for(None) == {"pct": ("inv", "val", 1)}
    assert bdbv.rate_fields_for(0) == {"pct": ("inv", "val", 0)}


# ---------------------------------------------------------------------------
# Candidate family


def _editions() -> dict[int, bdbv.Edition]:
    out = {}
    for n in (9, 10):
        payload = promotion(n, rows=ROWS, total={
            "validated_as_suspect": 100 + n, "suspects_investigated": 90 + n,
            "alerts_reported": 300, "alerts_verified_24h": 280,
        }, lab={"samples_analyzed": 33})
        out[n] = bdbv.edition_from_promotion(payload)
    return out


def test_build_candidates_family_size_per_judge_version():
    eds = _editions()
    v1 = bdbv.build_candidates(eds, {}, {}, judge_version=1)
    v2 = bdbv.build_candidates(eds, {}, {}, judge_version=2)
    assert judge.effective_hypotheses(v1) == 88
    assert judge.effective_hypotheses(v2) == 89
    assert len({c.candidate_id for c in v2}) == 89
    assert "C13" not in {c.candidate_id for c in v1}
    assert [c.candidate_id for c in v2][-1] == "C13"
    assert bdbv.build_candidates(eds, {}, {}) == v1


def test_c13_combines_same_edition_counts_with_previous_key_message_remainder():
    eds = _editions()
    key_messages = {9: {"inv": 1, "val": 2, "pct": 50.0, "not_inv": 4, "province": "Tshopo"}}
    c13 = {c.candidate_id: c for c in bdbv.build_candidates(eds, key_messages, {}, judge_version=2)}["C13"]
    assert c13.generate(10) == {"val": 110, "inv": 100, "not_inv": 4, "province": "Tshopo"}
    # No previous key message: counts only. No table: nothing.
    assert c13.generate(9) == {"val": 109, "inv": 99}
    assert c13.generate(11) is None


def test_other_candidates_on_synthetic_editions():
    eds = _editions()
    bodies = {9: {"val": 7, "inv": 7, "not_inv": 0}}
    by_id = {c.candidate_id: c for c in bdbv.build_candidates(eds, {}, bodies, judge_version=1)}
    assert by_id["C1"].generate(10)["val"] == 110
    assert by_id["C7:lag1"].generate(10) == by_id["C1"].generate(9)
    assert by_id["C8:lag1"].generate(10) == bodies[9]
    assert by_id["C2:Ituri"].generate(10) == {"val": 52, "inv": 48, "not_inv": 4, "province": "Ituri"}
    assert by_id["C2:Bas-Uele"].generate(10) is None
    assert by_id["C5"].generate(10) == {"val": 219, "inv": 199, "not_inv": 20}
    assert by_id["C10:sum"].generate(10) == {"val": 219, "inv": 199, "not_inv": 20}
    assert by_id["C10:mean"].generate(10) == {"val": 110, "inv": 100, "not_inv": 10}  # 109.5 -> 110
    assert by_id["C11:tested-as-investigated"].generate(10) == {"inv": 33}
    assert by_id["C3:verified/received"].generate(10) == {"val": 300, "inv": 280, "not_inv": 20}


def test_field_groups_merge_remainder_only_when_it_is_the_difference():
    assert bdbv.field_groups({"inv": 96, "val": 100, "not_inv": 4}) == [["inv", "val", "pct", "not_inv"], ["province"]]
    assert bdbv.field_groups({"inv": 96, "val": 100, "not_inv": 7}) == [["inv", "val", "pct"], ["not_inv"], ["province"]]
    assert bdbv.field_groups({"pct": 96.0}) == [["inv", "val", "pct"], ["not_inv"], ["province"]]


# ---------------------------------------------------------------------------
# Table findings


def _by_relation(findings, prefix: str):
    hits = [f for f in findings if f.relation.startswith(prefix)]
    assert hits, f"no finding for {prefix}"
    return hits


def test_table_findings_rate_mismatch_flagged():
    payload = promotion(total={"validated_as_suspect": 747, "suspects_investigated": 707, "investigation_rate_pct": 94.7})
    [finding] = _by_relation(bdbv.table_findings(payload), "investigation_rate_pct")
    assert finding.status == MISMATCH
    assert finding.printed == 94.7 and finding.expected == 94.6
    assert finding.relation == "investigation_rate_pct = suspects_investigated / validated_as_suspect"
    assert finding.unexplained and finding.lane == "bdbv" and finding.section == "Tableau 3"


def test_table_findings_rate_ok():
    payload = promotion(total={"validated_as_suspect": 747, "suspects_investigated": 707, "investigation_rate_pct": 94.6})
    [finding] = _by_relation(bdbv.table_findings(payload), "investigation_rate_pct")
    assert finding.status == OK and not finding.unexplained


def test_note_printing_both_values_covers_mismatch():
    both = "Tableau 3 imprime 94,7 % ; recalcule 94,6 % a partir des comptes."
    only = "Tableau 3 imprime 94,7 %."
    total = {"validated_as_suspect": 747, "suspects_investigated": 707, "investigation_rate_pct": 94.7}
    [covered] = _by_relation(bdbv.table_findings(promotion(total=total, conflicts=[only, both])), "investigation_rate_pct")
    assert covered.covered_by == both and not covered.unexplained
    [bare] = _by_relation(bdbv.table_findings(promotion(total=total, conflicts=[only])), "investigation_rate_pct")
    assert bare.covered_by is None and bare.unexplained


def test_scope_note_on_total_also_counts_as_disclosure():
    total = {"validated_as_suspect": 747, "suspects_investigated": 707, "investigation_rate_pct": 94.7,
             "scope_note": "le taux imprime 94,7 differe du recalcul 94,6"}
    [finding] = _by_relation(bdbv.table_findings(promotion(total=total)), "investigation_rate_pct")
    assert finding.covered_by == total["scope_note"]


def test_province_sum_mismatch_flagged():
    total = {"validated_as_suspect": 100, "suspects_investigated": 97}
    findings = bdbv.table_findings(promotion(total=total, rows=ROWS))
    [inv_sum] = _by_relation(findings, "suspects_investigated = sum of province rows")
    assert inv_sum.status == MISMATCH and inv_sum.printed == 97 and inv_sum.expected == 96
    [val_sum] = _by_relation(findings, "validated_as_suspect = sum of province")
    assert val_sum.status == OK and val_sum.expected == 100


def test_province_sum_incomplete_when_a_row_lacks_the_field():
    total = {"validated_as_suspect": 100, "suspects_investigated": 96, "suspects_transferred": 40}
    findings = bdbv.table_findings(promotion(total=total, rows=ROWS))
    [transferred] = _by_relation(findings, "suspects_transferred = sum of province rows")
    assert transferred.status == INCOMPLETE and transferred.expected is None and not transferred.unexplained


def test_remainder_and_day_sum_relations():
    total = {"validated_as_suspect": 100, "suspects_investigated": 96, "suspects_not_investigated": 5,
             "suspected_cases_day": 12, "alerts_validated_living": 10, "alerts_validated_deaths": 1}
    findings = bdbv.table_findings(promotion(total=total))
    [rem] = _by_relation(findings, "suspects_not_investigated")
    assert rem.status == MISMATCH and rem.printed == 5 and rem.expected == 4
    [day] = _by_relation(findings, "suspected_cases_day")
    assert day.status == MISMATCH and day.expected == 11


def test_older_schema_uses_alerts_basis():
    total = {"alerts_reported": 400, "alerts_investigated": 380, "investigation_rate_pct": 95.0}
    findings = bdbv.table_findings(promotion(number=40, total=total))
    [finding] = _by_relation(findings, "investigation_rate_pct")
    assert finding.relation == "investigation_rate_pct = alerts_investigated / alerts_reported"
    assert finding.status == OK
    total["investigation_rate_pct"] = 96.0
    [finding] = _by_relation(bdbv.table_findings(promotion(number=40, total=total)), "investigation_rate_pct")
    assert finding.status == MISMATCH and finding.expected == 95.0


def test_rate_with_zero_denominator_is_undefined_and_not_residue():
    total = {"validated_as_suspect": 0, "suspects_investigated": 0, "investigation_rate_pct": 0.0}
    [finding] = _by_relation(bdbv.table_findings(promotion(total=total)), "investigation_rate_pct")
    assert finding.status == "undefined" and finding.expected is None and not finding.unexplained


def test_table_findings_without_total_is_empty():
    assert bdbv.table_findings({"sitrep_number": 3, "figures": {}}) == []
    assert bdbv.table_findings({"sitrep_number": 3, "figures": {"operational_tables": {"alerts_total": []}}}) == []


def test_carried_forward_findings():
    tuples = {5: {"val": 10, "inv": 9}, 6: {"val": 10, "inv": 9}, 7: {"val": 11, "inv": 9}, 8: {"val": 11}}
    findings = bdbv.carried_forward_findings(tuples, section="Tableau 3")
    assert [f.edition for f in findings] == [6]
    assert findings[0].status == MISMATCH and findings[0].relation == "Tableau 3 identical to edition 5"
