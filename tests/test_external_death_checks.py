"""Outside checks on the death-ascertainment band are reported, cited and never fitted."""

import calendar
import copy
import glob
import json
import re
import pathlib
import unittest
from unittest import mock

from lovs import external_death_checks, lovs_convergence

REPO = pathlib.Path(__file__).resolve().parents[1]
BAND = lovs_convergence.DEATH_ASCERTAINMENT_BAND

METHODOLOGY_CONSTANTS = {
    "cfr": {"low_95": 0.26, "central": 0.33, "high_95": 0.4},
    "onset_to_death_gamma": {"alpha": 4.42, "beta_per_day": 0.388, "mean_days": 11.37},
    "central_doubling_time_days": 7.0,
    "observed_doubling_times_days": [5.0, 7.0, 11.0],
}


_TABLES = {}


def _tables():
    """Every reviewed health-zone table in data/sitrep_promotions, by SitRep number."""
    if not _TABLES:
        for path in sorted(glob.glob(str(REPO / "data" / "sitrep_promotions" / "sitrep-*.json"))):
            figures = json.loads(pathlib.Path(path).read_text(encoding="utf-8")).get("figures", {})
            table = figures.get("health_zone_table")
            # A carry-forward table repeats an earlier SitRep's figures; it is not a source.
            if (isinstance(table, dict) and "rows" in table and table.get(
                    "zone_attribution_status") != "not_published_carry_forward_latest_reviewed"):
                number = int(re.search(r"sitrep-(\d+)", path).group(1))
                assert number not in _TABLES, number
                _TABLES[number] = table
    return _TABLES


def _zone_table(sitrep):
    return _tables()[sitrep]


def _zone_value(sitrep, zone, table_key, field):
    rows = [row for row in _zone_table(sitrep).get(table_key, []) if row.get("zone") == zone]
    if len(rows) != 1 or rows[0].get(field) is None:
        raise AssertionError(f"SitRep {sitrep} prints no single {field} for {zone} ({table_key})")
    return rows[0][field]


def _is_named_zone(row):
    return "ventil" not in str(row.get("zone", "")).lower()


def _unassigned(sitrep, province):
    """A province's deaths not assigned to a named zone: its total less its named rows."""
    table = _zone_table(sitrep)
    totals = [p for p in table.get("province_totals", []) if p["province"] == province]
    if len(totals) != 1:
        raise AssertionError(f"SitRep {sitrep} prints no single {province} total")
    named = sum(r.get("confirmed_deaths") or 0 for r in table["rows"]
                if r.get("province") == province and _is_named_zone(r))
    return totals[0]["confirmed_deaths"] - named


# The one-day tables that assigned a province's unassigned deaths to named zones, declared
# after review rather than inferred (Ituri's unassigned count also falls to zero at SitReps
# 33-34 and 59-60, which were a reporting lag, not the A ventiler block). Each pair resolves
# only the run of SitReps around it in which deaths stayed unassigned without a break. A new
# one is added here, by hand.
HARMONISED_TABLES = {"Ituri": [(168, 169)]}


def _resolves(pair, sitrep, province):
    """Whether a harmonised pair resolved the unassigned deaths of a zone-row SitRep."""
    before, after = pair
    if before >= sitrep:
        span = range(sitrep, before + 1)
    elif sitrep > after:
        span = range(after + 1, sitrep + 1)
    else:
        return False
    # A SitRep with no reviewed table is a gap, not a break; every table present must still
    # hold unassigned deaths.
    tables = _tables()
    missing = [n for n in span if n not in tables]
    # Bridge only a lone missing SitRep (172 today); a longer gap could hide a break.
    if any(n + 1 in missing for n in missing):
        return False
    return all(_unassigned(n, province) > 0 for n in span if n in tables)


def _expected_official(zone, province, period_end):
    """The official range the rule gives for a zone at a date, from the reviewed records."""
    dated = [n for n, t in _tables().items() if t.get("date") == period_end]
    if not dated:
        raise AssertionError(f"no reviewed zone table is dated {period_end}")
    sitrep = max(dated)
    if any(after == sitrep for pairs in HARMONISED_TABLES.values() for _, after in pairs):
        raise AssertionError(f"SitRep {sitrep} is a one-day harmonised table, withdrawn later")
    low = _zone_value(sitrep, zone, "rows", "confirmed_deaths")
    if {r["province"] for r in _zone_table(sitrep)["rows"] if r.get("zone") == zone} != {province}:
        raise AssertionError(f"{zone} is not a {province} zone in SitRep {sitrep}")
    unassigned = _unassigned(sitrep, province)
    rows = {"zone_row": {"sitrep": sitrep, "deaths": low}, "harmonised_gain": None}
    if unassigned == 0:
        return rows, {"low": low, "high": low}, unassigned
    pairs = [pair for pair in HARMONISED_TABLES.get(province, [])
             if _resolves(pair, sitrep, province)]
    if len(pairs) != 1:
        raise AssertionError(
            f"no declared harmonised table resolves {province}'s {unassigned} unassigned deaths "
            f"at SitRep {sitrep}")
    before, after = pairs[0]
    gross = (_zone_value(after, zone, "rows", "confirmed_deaths")
             - _zone_value(before, zone, "rows", "confirmed_deaths"))
    that_day = _zone_value(after, zone, "day_rows", "total_confirmed_deaths_24h")
    # A zone's share cannot exceed the deaths unassigned at the zone row's date.
    gain = max(0, min(gross - that_day, unassigned))
    rows["harmonised_gain"] = {"before_sitrep": before, "after_sitrep": after, "gross": gross,
                               "reported_that_day": that_day, "deaths": gain}
    return rows, {"low": low, "high": low + gain}, unassigned


def _names(number, text):
    """Whether prose names a count, written with or without a thousands comma."""
    return re.search(rf"(?<![\d,.])({number:,}|{number})(?!\d|[,.]\d)", text) is not None


def _official_problems(check):
    """Why a check's official range is not the one the rule gives; [] if it is."""
    try:
        rows, deaths, unassigned = _expected_official(
            check["zone"], check["province"], check["period_end"])
    except AssertionError as exc:
        return [str(exc)]
    problems = []
    if check["official_rows"] != rows:
        problems.append(f"official rows {check['official_rows']} are not {rows}")
    if check["official_deaths"] != deaths:
        problems.append(f"official range {check['official_deaths']} is not {deaths}")
    if check.get("outside_derived_from_official"):
        if check["outside_derivation"]["sitrep"] != rows["zone_row"]["sitrep"]:
            problems.append("the derived outside figure is not from the zone row's SitRep")
    # The prose basis names the same SitReps and numbers as the structured fields.
    basis = check["comparison_basis"]
    gain = rows["harmonised_gain"]
    sitreps = [rows["zone_row"]["sitrep"]]
    numbers = [deaths["low"], deaths["high"]]
    if gain:
        sitreps += [gain["before_sitrep"], gain["after_sitrep"]]
        numbers += [gain["deaths"], gain["reported_that_day"],
                    _zone_value(gain["after_sitrep"], check["zone"], "rows", "confirmed_deaths"),
                    _zone_value(gain["before_sitrep"], check["zone"], "rows", "confirmed_deaths")]
        if f"the {unassigned} {check['province']} deaths not assigned to a zone" not in basis:
            problems.append("basis does not name the unassigned deaths")
    for number in sitreps:
        if not re.search(rf"SitRep {number}(?!\d)", basis):
            problems.append(f"basis does not name SitRep {number}")
    for number in numbers:
        if not _names(number, basis):
            problems.append(f"basis does not mention {number}")
    if gain and not (f"low: {deaths['low']}" in basis and (
            f"high: {deaths['high']}" in basis or f"high: {deaths['high']:,}" in basis)):
        problems.append("basis does not give the low and high ends in their roles")
    return problems


def _build(**overrides):
    kwargs = dict(
        as_of="2026-06-25", confirmed=200, confirmed_deaths=40,
        contacts_under_follow_up=1000, followup_coverage_pct=80.0,
        methodology_constants=METHODOLOGY_CONSTANTS,
        confirmed_series=[
            {"date": "2026-01-01", "value": 100},
            {"date": "2026-06-25", "value": 200},
        ],
    )
    kwargs.update(overrides)
    return lovs_convergence.build_convergence(**kwargs)


def _record_test_passes(check):
    """Run the record test on one check alone, in its own result, and say if it passes."""
    result = unittest.TestResult()
    with mock.patch.object(external_death_checks, "EXTERNAL_DEATH_CHECKS", (check,)):
        TestCheckRecords("test_each_check_is_complete_and_names_unassigned_deaths").run(result)
    return result.wasSuccessful() and result.testsRun == 1


class TestCheckRecords(unittest.TestCase):
    def test_each_check_is_complete_and_names_unassigned_deaths(self):
        self.assertGreaterEqual(len(external_death_checks.EXTERNAL_DEATH_CHECKS), 1)
        ids = set()
        for check in external_death_checks.EXTERNAL_DEATH_CHECKS:
            with self.subTest(check=check["check_id"]):
                self.assertNotIn(check["check_id"], ids)
                ids.add(check["check_id"])
                for key in ("source_name", "zone", "province", "period_end", "period_text",
                            "published", "source", "comparison_basis", "outside_basis"):
                    self.assertTrue(check[key].strip())
                self.assertLessEqual(check["period_end"], check["published"])
                citations = (REPO / "CITATIONS.md").read_text(encoding="utf-8")
                entry = [line for line in citations.splitlines() if check["check_id"] in line]
                self.assertTrue(entry and f"Published {check['published']}" in entry[0],
                                check["check_id"])
                # The served source line names the source the citation names, and the source
                # text names it with its publication date.
                self.assertTrue(entry[0].startswith(f"- {check['source_name']}."), entry[0][:40])
                published = external_death_checks._long_date(check["published"])
                self.assertTrue(check["source"].startswith(f"{check['source_name']}, {published}:"),
                                check["source"])
                # Each kind of source, method and outside figure has its own served wording.
                self.assertIn(check["source_kind"], external_death_checks.SOURCE_KIND_WORDS)
                self.assertIn(check["method"], external_death_checks.METHOD_WORDS)
                self.assertIn(
                    check["outside_high_kind"], external_death_checks.OUTSIDE_HIGH_KIND_WORDS
                )
                self.assertIsInstance(check["outside_low_is_floor"], bool)
                unbaselined = check["outside_unbaselined"]
                if check["outside_high_kind"] == "excess":
                    self.assertIsNone(unbaselined)
                    self.assertEqual(check["outside_deaths"]["low"], check["outside_deaths"]["high"])
                else:
                    self.assertLessEqual(unbaselined["low"], unbaselined["high"])
                    # The high end is the counted low plus the top of what was not counted.
                    self.assertEqual(check["outside_deaths"]["high"],
                                     check["outside_deaths"]["low"] + unbaselined["high"])
                    self.assertIn(f"read here as {unbaselined['low']} to {unbaselined['high']}",
                                  check["outside_basis"])
                self.assertIsInstance(check["outside_derived_from_official"], bool)
                derivation = check["outside_derivation"]
                if check["outside_derived_from_official"]:
                    # A figure derived from the official toll is rebuilt from that row here.
                    toll = _zone_value(derivation["sitrep"], check["zone"], "rows",
                                       "confirmed_deaths")
                    self.assertEqual(round(toll * derivation["ratio"]), outside_low := check[
                        "outside_deaths"]["low"])
                    self.assertIn(f"low: about {outside_low}.", check["outside_basis"])
                    self.assertIn(f"taken here as {toll}", check["outside_basis"])
                else:
                    self.assertIsNone(derivation)
                # A comparison against the zone row alone overstates the gap: the basis must
                # say how deaths not assigned to a zone were handled, at both ends.
                if check["official_rows"]["harmonised_gain"] is not None:
                    self.assertIn("not assigned to a zone", check["comparison_basis"])
                    self.assertIn("low: ", check["comparison_basis"])
                    self.assertIn("high: ", check["comparison_basis"])
                # The flags that change the served line agree with the prose and the dates.
                # The served period is written from period_end by one rule: the whole month
                # when it ends on the month's last day, else early, mid or late in the month.
                year, month_number, day = (int(x) for x in check["period_end"].split("-"))
                month = external_death_checks._MONTHS[month_number - 1]
                if day == calendar.monthrange(year, month_number)[1]:
                    expected_period = f"to {month} {year}"
                else:
                    part = "early" if day <= 10 else "mid" if day <= 20 else "late"
                    expected_period = f"to {part} {month} {year}"
                self.assertEqual(check["period_text"], expected_period)
                # The outside range is the one the prose gives, in its roles.
                basis, outside = check["outside_basis"], check["outside_deaths"]

                def role(words, number):
                    return any(re.search(rf"{words}{about}{n}(?!\d|[,.]\d)", basis)
                               for about in ("", "about ") for n in (f"{number:,}", str(number)))

                self.assertEqual(check["outside_unbaselined"] is not None,
                                 re.search(r"(?<!and )high: ", basis) is not None)
                if check["outside_unbaselined"] is None:
                    self.assertTrue(role("low and high: ", outside["low"]), outside)
                else:
                    self.assertTrue(role("low: ", outside["low"]), outside)
                    self.assertTrue(role("high: ", outside["high"]), outside)
                # Each flag that changes the served line is stated in its own sentence, so
                # leaving a flag wrong means writing a false sentence, not missing a synonym.
                low = f"{outside['low']:,}"
                floor = check["outside_low_is_floor"]
                def stated(sentence):
                    return re.search(rf"(?<![\d,]){sentence}", basis) is not None

                self.assertEqual(stated(f"{low} is a floor"), floor)
                self.assertEqual(stated(f"{low} is not a floor"), not floor)
                derived = check["outside_derived_from_official"]
                self.assertEqual("it is derived from the official toll" in basis, derived)
                self.assertEqual("it is not derived from the official toll" in basis, not derived)
                # A qualifier before the count or before a ratio makes the figure a floor.
                qualifier = r"(more than|over|at least|in excess of|upwards of|exceeding|beyond)"
                if re.search(rf"{qualifier}\s+(about\s+)?({low}|{outside['low']})(?!\d|,\d)",
                             basis) or re.search(rf"{qualifier}\s+\d+% above the official toll",
                                                 basis):
                    self.assertTrue(floor)
                if "gives only a ratio" in basis:
                    self.assertTrue(derived)
                if check["outside_derived_from_official"]:
                    ratio = check["outside_derivation"]["ratio"]
                    self.assertIn(f"{round((ratio - 1) * 100)}% above", basis)
                official = check["official_deaths"]
                outside = check["outside_deaths"]
                for pair in (official, outside):
                    self.assertIsInstance(pair["low"], int)
                    self.assertIsInstance(pair["high"], int)
                    self.assertLessEqual(pair["low"], pair["high"])
                self.assertGreater(outside["low"], 0)

    def test_official_figures_are_the_reviewed_sitrep_rows(self):
        # The official range is read back from the reviewed record, not trusted as typed.
        for check in external_death_checks.EXTERNAL_DEATH_CHECKS:
            with self.subTest(check=check["check_id"]):
                self.assertEqual(_official_problems(check), [])

    def test_declared_harmonised_tables_are_one_day_assignments(self):
        for province, before, after in [(p, b, a) for p, pairs in HARMONISED_TABLES.items()
                                        for b, a in pairs]:
            with self.subTest(province=province, pair=(before, after)):
                self.assertEqual(after, before + 1)
                self.assertGreater(_unassigned(before, province), 0)
                self.assertEqual(_unassigned(after, province), 0)
                # The next SitRep restores the unassigned row: the table was a one-day view.
                self.assertGreater(_unassigned(after + 1, province), 0)

    def test_read_back_refuses_wrong_official_rows(self):
        bunia, mongbwalu = external_death_checks.EXTERNAL_DEATH_CHECKS
        gain = bunia["official_rows"]["harmonised_gain"]
        bad = {
            # The zone row alone, dressed as a gain over a later pair of SitReps.
            "zone row alone": {**bunia, "official_rows": {
                "zone_row": {"sitrep": 173, "deaths": 707},
                "harmonised_gain": {"before_sitrep": 170, "after_sitrep": 171, "gross": 2,
                                    "reported_that_day": 2, "deaths": 0}},
                "official_deaths": {"low": 707, "high": 707}},
            # Ordinary growth over weeks presented as the assignment of unassigned deaths.
            "ordinary growth": {**bunia, "official_rows": {
                "zone_row": {"sitrep": 173, "deaths": 707},
                "harmonised_gain": {"before_sitrep": 170, "after_sitrep": 189, "gross": 72,
                                    "reported_that_day": 0, "deaths": 72}},
                "official_deaths": {"low": 707, "high": 779}},
            # A later zone row for an earlier period.
            "wrong date": {**bunia, "official_rows": {
                "zone_row": {"sitrep": 189, "deaths": 771}, "harmonised_gain": gain},
                "official_deaths": {"low": 771, "high": 1017}},
            # No gain where the zone-row SitRep does have unassigned deaths.
            "gain left out": {**bunia, "official_rows": {
                **bunia["official_rows"], "harmonised_gain": None},
                "official_deaths": {"low": 707, "high": 707}},
            # The harmonised table's own row plus its gain counts the gain twice.
            "double count": {**bunia, "period_end": "2026-10-30", "official_rows": {
                "zone_row": {"sitrep": 169, "deaths": 938}, "harmonised_gain": gain},
                "official_deaths": {"low": 938, "high": 1184}},
            # The withdrawn one-day table used as the zone row.
            "withdrawn table": {**bunia, "period_end": "2026-10-30", "official_rows": {
                "zone_row": {"sitrep": 169, "deaths": 938}, "harmonised_gain": None},
                "official_deaths": {"low": 938, "high": 938}},
            # An early row, with fewer deaths unassigned than the later gain.
            "early row, later gain": {**bunia, "period_end": "2026-08-10", "official_rows": {
                "zone_row": {"sitrep": 88, "deaths": _zone_value(88, "Bunia", "rows",
                                                                 "confirmed_deaths")},
                "harmonised_gain": gain}},
            # Another province's zone given Ituri's gain.
            "wrong province": {**bunia, "zone": "Beni", "province": "Nord-Kivu",
                               "comparison_basis": bunia["comparison_basis"].replace(
                                   "Ituri", "Nord-Kivu")},
            # A derived outside figure built from a later row than the official one.
            "derivation from another row": {**mongbwalu, "outside_derivation": {
                "sitrep": 189, "ratio": 1.25}},
        }
        # A July row that a later pair did not resolve (the lag was absorbed the next day).
        bad["unresolved early row"] = {**bunia, "zone": "Rwampara", "period_end": "2026-07-12",
                                       "official_rows": {
            "zone_row": {"sitrep": 59, "deaths": 89},
            "harmonised_gain": {"before_sitrep": 168, "after_sitrep": 169, "gross": 3,
                                "reported_that_day": 0, "deaths": 3}},
            "official_deaths": {"low": 89, "high": 92}}
        bad["unresolved SitRep 27"] = {**bunia, "period_end": _zone_table(27)["date"]}
        bad["SitRep 33 without zone deaths"] = {**bunia, "period_end": _zone_table(33)["date"]}
        bad["early row, later gain"]["official_deaths"] = {
            "low": bad["early row, later gain"]["official_rows"]["zone_row"]["deaths"],
            "high": bad["early row, later gain"]["official_rows"]["zone_row"]["deaths"] + 246}
        for name, check in bad.items():
            with self.subTest(case=name):
                self.assertNotEqual(_official_problems(check), [])

    def test_record_test_refuses_outside_figures_that_disagree_with_the_prose(self):
        bunia, mongbwalu = external_death_checks.EXTERNAL_DEATH_CHECKS
        bad = {
            "outside count changed": {**bunia, "outside_deaths": {"low": 500, "high": 500}},
            "described graves dropped": {**mongbwalu, "outside_high_kind": "excess",
                                         "outside_unbaselined": None,
                                         "outside_deaths": {"low": 358, "high": 358}},
            "ratio changed": {**mongbwalu, "outside_derivation": {"sitrep": 109, "ratio": 1.6},
                              "outside_deaths": {"low": 458, "high": 858},
                              "outside_basis": mongbwalu["outside_basis"].replace(
                                  "about 358", "about 458")},
            "floor flag dropped": {**bunia, "outside_low_is_floor": False,
                                   "outside_basis": bunia["outside_basis"].replace(
                                       "the floor of", "the lowest reading of")},
            "derived flag dropped": {**mongbwalu, "outside_derived_from_official": False,
                                     "outside_derivation": None,
                                     "outside_basis": mongbwalu["outside_basis"].replace(
                                         "gives only a ratio", "reports just a ratio")},
            "described range reversed": {**mongbwalu,
                                         "outside_unbaselined": {"low": 500, "high": 400}},
            "wrong year": {**bunia, "period_text": "to early November 2025"},
            "floor dropped, worded over": {**bunia, "outside_low_is_floor": False,
                                           "outside_basis": bunia["outside_basis"].replace(
                                               "more than 1,000", "over 1,000")},
            "floor dropped at the ratio reading": {**bunia, "outside_low_is_floor": False,
                                                   "outside_deaths": {"low": 1060, "high": 1060}},
            "derived flag dropped, reworded": {**mongbwalu, "outside_derived_from_official": False,
                                               "outside_derivation": None,
                                               "outside_basis": mongbwalu["outside_basis"].replace(
                                                   "taken here as", "read as")},
            "outside equal to the official row": {**bunia, "outside_low_is_floor": False,
                                                  "outside_deaths": {"low": 707, "high": 707}},
            "outside from a percentage": {**bunia, "outside_deaths": {"low": 50, "high": 50}},
            "wrong publication date": {**bunia, "published": "2026-11-24"},
            "floor dropped, worded exceeding": {**bunia, "outside_low_is_floor": False,
                                                "outside_basis": bunia["outside_basis"].replace(
                                                    "more than 1,000", "exceeding 1,000")},
            "floor dropped, 1,000 or more": {**bunia, "outside_low_is_floor": False,
                                             "outside_basis": bunia["outside_basis"].replace(
                                                 "more than 1,000", "1,000 or more")},
            "ratio given as a floor": {**mongbwalu, "outside_basis": mongbwalu[
                "outside_basis"].replace("25% above the official toll",
                                         "more than 25% above the official toll")},
            "digit dropped": {**bunia, "outside_low_is_floor": False,
                              "outside_deaths": {"low": 100, "high": 100},
                              "outside_basis": bunia["outside_basis"].replace(
                                  "low and high: 1,000", "low and high: 1000")},
            "wrong source name": {**bunia, "source_name": "WHO"},
            "source text from another source": {**bunia,
                                                "source": "Ministry of Health mortality survey"},
            "period after period_end": {**bunia, "period_text": "to late November 2026"},
            "period running past publication": {**bunia,
                                                "period_text": "from November 2026 to March 2027"},
            "floor sentence false, qualifier before a comma": {
                **bunia, "outside_low_is_floor": False,
                "outside_basis": bunia["outside_basis"].replace(
                    "1,000 is a floor", "1,000 is not a floor").replace(
                    "more than 1,000 excess", "more than 1,000, excess")},
            "floor sentence names a larger number": {
                **mongbwalu, "outside_basis": mongbwalu["outside_basis"].replace(
                    "358 is not a floor", "1,358 is not a floor")},
        }
        for name, check in bad.items():
            with self.subTest(case=name):
                self.assertFalse(_record_test_passes(check))
        swapped = {**bunia, "comparison_basis": bunia["comparison_basis"].replace(
            "low: 707", "low: 953 or 707")}
        self.assertNotEqual(_official_problems(swapped), [])

    def test_read_back_accepts_a_zone_with_no_unassigned_deaths(self):
        rows, deaths, unassigned = _expected_official("Beni", "Nord-Kivu", "2026-11-03")
        self.assertEqual(unassigned, 0)
        self.assertIsNone(rows["harmonised_gain"])
        beni = {**external_death_checks.EXTERNAL_DEATH_CHECKS[0], "zone": "Beni",
                "province": "Nord-Kivu", "official_rows": rows, "official_deaths": deaths,
                "comparison_basis": f"low and high: {deaths['low']} confirmed deaths in the "
                                    "SitRep 173 zone row; no Nord-Kivu deaths were unassigned"}
        self.assertEqual(_official_problems(beni), [])
        self.assertTrue(_record_test_passes(beni))

    def test_records_hold_no_coordinates(self):
        # Site and person names cannot be caught by a pattern; they are reviewed at ingest.
        coordinate = re.compile(
            r"-?\d{1,3}\.\d{4,}|-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}|\u00b0"
            r"|\blat(itude)?\b|\blon(gitude)?\b",
            re.IGNORECASE,
        )
        self.assertIsNotNone(coordinate.search("at 12.345, 67.890 on the map"))
        for check in external_death_checks.EXTERNAL_DEATH_CHECKS:
            with self.subTest(check=check["check_id"]):
                text = json.dumps(check, ensure_ascii=False)
                self.assertIsNone(coordinate.search(text), coordinate.search(text))

    def test_every_check_source_is_cited(self):
        citations = (REPO / "CITATIONS.md").read_text(encoding="utf-8")
        self.assertIn("## Outside checks on the death estimate", citations)
        for check in external_death_checks.EXTERNAL_DEATH_CHECKS:
            with self.subTest(check=check["check_id"]):
                self.assertIn(check["check_id"], citations)

    def test_public_text_has_no_em_or_en_dash(self):
        for dash in ("\u2014", "\u2013"):
            for check in external_death_checks.EXTERNAL_DEATH_CHECKS:
                self.assertNotIn(dash, json.dumps(check, ensure_ascii=False))
            self.assertNotIn(dash, external_death_checks.CHECKS_NOTE)


class TestImpliedAscertainment(unittest.TestCase):
    def test_current_checks(self):
        by_id = {
            row["check_id"]: row
            for row in external_death_checks.assess_against_band(BAND)["checks"]
        }
        bunia = by_id["reuters-graves-bunia-2026-11"]
        self.assertEqual(bunia["implied_death_ascertainment"], [0.71, 0.95])  # 707/1000, 953/1000
        self.assertEqual(bunia["relation_to_band"], "partly_above")
        mongbwalu = by_id["reuters-graves-mongbwalu-2026-08"]
        self.assertEqual(mongbwalu["implied_death_ascertainment"], [0.38, 0.86])  # 286/758, 307/358
        self.assertEqual(mongbwalu["relation_to_band"], "spans")
        for row in by_id.values():
            self.assertIs(row["derived"], True)
        for check_id in ("reuters-graves-bunia-2026-11", "reuters-graves-mongbwalu-2026-08"):
            self.assertEqual(by_id[check_id]["published"], "2026-11-23")

    def test_mongbwalu_conditionals_in_its_basis_are_true(self):
        # outside_basis: on the counted cemetery alone 0.80 to 0.86; if all the described
        # graves were excess deaths, 0.38 to 0.55. Both readings come from the record.
        base = dict(external_death_checks.EXTERNAL_DEATH_CHECKS[1])
        counted, extra = base["outside_deaths"]["low"], base["outside_unbaselined"]
        for outside, expected, words in (
            ({"low": counted, "high": counted}, (0.80, 0.86), "the implied range is 0.80 to 0.86"),
            ({"low": counted + extra["low"], "high": counted + extra["high"]}, (0.38, 0.55),
             "it would be 0.38 to 0.55"),
        ):
            check = {**base, "outside_deaths": outside}
            lo, hi = external_death_checks.implied_death_ascertainment(check)
            self.assertEqual((round(lo, 2), round(hi, 2)), expected)
            self.assertIn(words, base["outside_basis"])

    def test_bunia_ratio_reading_in_its_basis_is_true(self):
        # outside_basis: more than 50% above the official 707 gives 0.67 to 0.90.
        bunia = dict(external_death_checks.EXTERNAL_DEATH_CHECKS[0])
        floor = round(1.5 * bunia["official_rows"]["zone_row"]["deaths"])
        lo, hi = external_death_checks.implied_death_ascertainment(
            {**bunia, "outside_deaths": {"low": floor, "high": floor}})
        self.assertEqual((round(lo, 2), round(hi, 2)), (0.67, 0.90))
        self.assertIn("would give 0.67 to 0.90", bunia["outside_basis"])

    def test_implied_range_is_capped_at_one(self):
        check = {"official_deaths": {"low": 90, "high": 90},
                 "outside_deaths": {"low": 60, "high": 100}}
        self.assertEqual(external_death_checks.implied_death_ascertainment(check), (0.9, 1.0))

    def test_malformed_records_name_the_check(self):
        good = external_death_checks.EXTERNAL_DEATH_CHECKS[0]
        bad_records = (
            {k: v for k, v in good.items() if k != "official_deaths"},
            {**good, "outside_deaths": {"low": 0, "high": 10}},
            {**good, "outside_deaths": [1000, 1000]},
            {**good, "official_deaths": {"low": "707", "high": 959}},
            {**good, "official_deaths": {"low": 960, "high": 959}},
        )
        for bad in bad_records:
            with self.subTest(bad=bad.get("official_deaths"), outside=bad.get("outside_deaths")):
                with self.assertRaisesRegex(ValueError, good["check_id"]):
                    external_death_checks.implied_death_ascertainment(bad)
        # A kind with no served wording stops the release build instead of serving a bare line.
        for bad in ({**good, "source_kind": "survey"}, {**good, "outside_high_kind": "rumour"}):
            with self.subTest(kind=(bad["source_kind"], bad["outside_high_kind"])):
                with self.assertRaisesRegex(ValueError, good["check_id"]):
                    external_death_checks.caveat(bad)
        with self.assertRaisesRegex(ValueError, good["check_id"]):
            external_death_checks.label({**good, "method": "mortality_survey"})

    def test_relation_uses_unrounded_values(self):
        # 292 / 556 = 0.5252 rounds to 0.53 but sits below the 0.526 edge.
        check = {"official_deaths": {"low": 100, "high": 292},
                 "outside_deaths": {"low": 556, "high": 600}}
        implied = external_death_checks.implied_death_ascertainment(check)
        self.assertEqual(external_death_checks.relation_to_band(implied, BAND), "below")

    def test_relation_labels(self):
        rel = external_death_checks.relation_to_band
        band = (0.5, 0.6, 0.8)
        self.assertEqual(rel((0.55, 0.75), band), "inside")
        self.assertEqual(rel((0.5, 0.8), band), "inside")
        self.assertEqual(rel((0.2, 0.4), band), "below")
        self.assertEqual(rel((0.85, 0.95), band), "above")
        self.assertEqual(rel((0.4, 0.6), band), "partly_below")
        self.assertEqual(rel((0.7, 0.9), band), "partly_above")
        self.assertEqual(rel((0.4, 0.9), band), "spans")

    def test_source_lines_follow_the_checks(self):
        lines = external_death_checks.source_lines(BAND)
        self.assertEqual(len(lines), len(external_death_checks.EXTERNAL_DEATH_CHECKS))
        self.assertEqual(
            lines[0],
            "Outside check, reported and not fitted: Reuters grave counts (published 23 November "
            "2026), Bunia to early November 2026 (a media estimate of excess deaths; not all from "
            "Ebola; one place, not the national value): implied death ascertainment 0.71-0.95 or "
            "lower, partly above the band at these upper readings",
        )
        self.assertEqual(
            lines[1],
            "Outside check, reported and not fitted: Reuters grave counts (published 23 November "
            "2026), Mongbwalu to August 2026 (a media estimate of excess deaths, up to graves "
            "described but not counted, derived in part from the official toll; not all from "
            "Ebola; one place, not the national value): implied death ascertainment 0.38-0.86, "
            "wider than the band on both sides",
        )
        for line in lines:
            # The served line is often the only text a reader sees: it carries its caveats.
            self.assertIn("published ", line)
            self.assertIn("a media estimate", line)
            self.assertIn("not all from Ebola", line)
            self.assertIn("not the national value", line)

    def test_empty_checks(self):
        report = external_death_checks.assess_against_band(BAND, checks=())
        self.assertEqual(report["checks"], [])
        self.assertEqual(report["band"], [BAND[0], BAND[-1]])
        self.assertEqual(external_death_checks.source_lines(BAND, checks=()), [])


class TestChecksAreNeverFitted(unittest.TestCase):
    """Adding or removing an outside check moves nothing but the check report itself."""

    def _strip(self, block):
        block = copy.deepcopy(block)
        block["true_burden_nowcast"]["estimated_total_deaths"].pop("external_checks")
        for row in block["methodology"]:
            row["sources"] = [
                s for s in row.get("sources", [])
                if not s.startswith("Outside check, reported and not fitted: ")
            ]
        return block

    def _assert_independent(self, **overrides):
        full = _build(**overrides)
        with mock.patch.object(external_death_checks, "EXTERNAL_DEATH_CHECKS", ()):
            empty = _build(**overrides)
        self.assertEqual(
            empty["true_burden_nowcast"]["estimated_total_deaths"]["external_checks"]["checks"],
            [],
        )
        self.assertTrue(
            full["true_burden_nowcast"]["estimated_total_deaths"]["external_checks"]["checks"]
        )
        self.assertEqual(self._strip(full), self._strip(empty))

    def test_with_series(self):
        self._assert_independent()

    def test_without_series(self):
        self._assert_independent(confirmed_series=None)

    def test_band_constants_are_pinned(self):
        self.assertEqual(BAND, (0.526, 0.667, 0.833))
        self.assertEqual(lovs_convergence.TRUE_IFR_BAND, (0.13, 0.15, 0.16))

    def test_only_the_nowcast_reads_the_checks_and_only_inside_functions(self):
        # The independence test patches the module attribute; this catches a direct import
        # of the records, a module-level constant built from them, or another module reading
        # them, none of which that patch would see.
        skipped = {"tests", "node_modules", "venv", "__pycache__"}
        sources = [
            path for path in sorted(REPO.rglob("*.py"))
            if path.name != "external_death_checks.py"
            and not any(part in skipped or part.startswith(".")
                        for part in path.relative_to(REPO).parts[:-1])
        ]
        self.assertIn(REPO / "refresh_pipeline.py", sources)
        self.assertTrue(any(path.parent.name == "tools" for path in sources))
        readers = {}
        for path in sources:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("EXTERNAL_DEATH_CHECKS", text, path)
            self.assertNotIn("from .external_death_checks import", text, path)
            self.assertNotIn("from lovs.external_death_checks import", text, path)
            uses = re.findall(r"^(\s*).*external_death_checks\.(\w+)", text, re.MULTILINE)
            if uses:
                readers[path.relative_to(REPO).as_posix()] = uses
        self.assertEqual(set(readers), {"lovs/lovs_convergence.py"})
        uses = readers["lovs/lovs_convergence.py"]
        self.assertEqual({name for _, name in uses}, {"assess_against_band", "source_lines"})
        # Every call sits inside a function body (indented), never at import time.
        self.assertTrue(all(indent for indent, _ in uses))


# SitRep 189 severity block (data/live-bdbv-2026-output.json, convergence.severity_cfr).
SR189_SEVERITY = {
    "as_of": "2026-11-19",
    "scope": "country",
    "confirmed_cfr_crude_pct": 49.0,
    "confirmed_cfr_delay_adjusted_pct": {"central": 52.6, "low": 51.8, "high": 53.4},
    "confirmed_deaths": 5754,
    "confirmed_cumulative": 11743,
    "resolved_denominator": 10949,
    "onset_to_death_mean_days": 11.4,
    "onset_to_death_band_days": [9, 14],
    "method": "Nishiura 2009 delay-adjusted confirmed CFR",
    "provenance": "lovs",
    "sources": [],
}


class TestSitRep189Shape(unittest.TestCase):
    """SR189's own counts and delay-adjusted CFR give the published figures."""

    def setUp(self):
        with mock.patch.object(lovs_convergence, "delay_adjusted_cfr", return_value=SR189_SEVERITY):
            self.block = _build(
                as_of="2026-11-19", confirmed=11743, confirmed_deaths=5754,
                confirmed_series=[
                    {"date": "2026-05-15", "value": 5000},
                    {"date": "2026-11-19", "value": 11743},
                ],
            )
        self.nc = self.block["true_burden_nowcast"]

    def test_deaths(self):
        d = self.nc["estimated_total_deaths"]
        self.assertEqual([d["low"], d["central"], d["high"]], [6908, 8938, 11743])
        self.assertEqual(d["death_timing_bracket"], {"low": 8627, "high": 9261})

    def test_infections_unchanged(self):
        c = self.nc["estimated_total_cases"]
        self.assertEqual([c["low"], c["central"], c["high"]], [57513, 59589, 61740])

    def test_worked_line(self):
        row = next(
            r for r in self.block["methodology"]
            if r["quantity"].startswith("Estimated total deaths")
        )
        self.assertEqual(
            row["worked_central"],
            "crude 8627 to delay-adjusted 9261 at death ascertainment 0.667; across the band "
            "0.526-0.833, 6908 to 11743; central 8938 (= 59589 x 0.15)",
        )
        self.assertEqual(
            row["result"],
            "6908-11743 (central 8938; from deaths so far if 83.3% of deaths are recorded to "
            "eventual deaths among people infected so far if 52.6% are; an estimate, not a count)",
        )


class TestPublishedDeathRange(unittest.TestCase):
    def test_methodology_row_shows_the_band_and_the_checks(self):
        block = _build()
        row = next(
            r for r in block["methodology"]
            if r["quantity"].startswith("Estimated total deaths")
        )
        d = block["true_burden_nowcast"]["estimated_total_deaths"]
        self.assertTrue(row["result"].startswith(f"{d['low']}-{d['high']} (central {d['central']}; "))
        self.assertTrue(row["result"].endswith("; an estimate, not a count)"))
        self.assertIn("across the band 0.526-0.833, 48 to 152", row["worked_central"])
        self.assertEqual(
            row["sources"][1:], external_death_checks.source_lines(BAND)
        )

    def test_fallback_method_names_the_band(self):
        d = _build(confirmed_series=None)["true_burden_nowcast"]["estimated_total_deaths"]
        self.assertEqual(d["method"], "LOVS death under-ascertainment correction (range spans the "
                                      "death-ascertainment band)")
        self.assertNotIn("death_timing_bracket", d)
        row = next(
            r for r in _build(confirmed_series=None)["methodology"]
            if r["quantity"].startswith("Estimated total deaths")
        )
        self.assertEqual(
            row["result"],
            f"{d['low']}-{d['high']} (central {d['central']}; deaths so far if 83.3% to 52.6% of "
            "deaths are recorded; an estimate, not a count)",
        )


if __name__ == "__main__":
    unittest.main()
