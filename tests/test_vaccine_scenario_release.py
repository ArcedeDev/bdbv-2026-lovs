"""Tests for lovs.vaccine_scenario_release (the per-release vaccine scenario artifact).

  * reproducibility: every committed artifact equals a rebuild from its inputs,
    and the stored ring-timing shares equal a rebuild from the stored
    calibration rows;
  * record parity: the reviewed promotion's vaccination rows win, and any
    field they share with the release inputs file must agree;
  * guard rails: assumptions are labelled, no national total, no share of all
    cases unless frontline workers and contacts are both defined, Zaire anchor named.
"""
from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

from lovs import vaccine_scenario_release as rel

DATA = rel.DATA_DIR
SMALL = dict(draws=400, cond_draws=200)
# Haut-Uele's list carries no case share (it is not split into frontline workers and contacts);
# dropping that draw and crediting post-exposure protection to vaccinees only moved this median
# from 0.2181 to 0.2113, and taking each calibration set's onset-to-transmission mean as the serial
# interval minus its incubation mean (drawn across WHO 2014's 9.1 to 11.4 d), with each set
# confirmed at 40,000 draws on seeds separate from the screen, moved it to 0.2211.
HU_E5_P50 = 0.2211


def sr189_inputs() -> dict:
    return rel.load_release_inputs(189)


class CommittedArtifacts(unittest.TestCase):
    def test_every_inputs_file_has_a_matching_artifact(self):
        inputs = sorted(p.stem for p in (DATA / "inputs").glob("sr*.json"))
        artifacts = sorted(p.stem.removeprefix("scenario-") for p in DATA.glob("scenario-sr*.json"))
        self.assertTrue(inputs)
        self.assertEqual(inputs, artifacts)

    def test_every_promoted_vaccination_record_has_an_artifact(self):
        self.assertEqual(rel.sitreps_missing_a_scenario(), [],
                         "a reviewed promotion shows printed vaccination counts with no scenario: add "
                         "data/vaccine-field-effectiveness/inputs/sr<N>.json and run "
                         "python3 -m lovs.vaccine_scenario_release --sitrep <N> (PIPELINE.md, step 4)")

    def test_missing_scenario_check_reads_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            promos, data = pathlib.Path(tmp, "promotions"), pathlib.Path(tmp, "data")
            promos.mkdir(), data.mkdir()

            def promote(n: int, rows: list, not_promoted: list | None = None, where: pathlib.Path = promos) -> None:
                tables = {"vaccination_by_province": rows, "not_promoted_numeric_fields": not_promoted or []}
                doc = {"figures": {"operational_tables": tables}}
                pathlib.Path(where, f"sitrep-{n:03d}-2026-11-2{n % 10}.json").write_text(json.dumps(doc))

            row = [{"province": "Nord-Kivu", "vaccinated_cumulative": 5000}]
            promote(170, row)                                   # before the scenario lane
            promote(190, row)                                   # a record
            promote(191, [])                                    # nothing printed
            promote(192, [], not_promoted=["vaccination_counts"])  # printed but not promoted
            promote(193, [], not_promoted=["alerts_repeat"])    # an unrelated field set aside
            candidates = pathlib.Path(promos, "candidates")
            candidates.mkdir()
            promote(194, row, where=candidates)                 # not yet reviewed
            withheld = {"figures": {"operational_tables": None,
                                    "withheld_source_conflict_fields": ["vaccination_counts"]}}
            pathlib.Path(promos, "sitrep-195-2026-11-25.json").write_text(json.dumps(withheld))  # printed, withheld
            unprinted = {"figures": {"operational_tables": {}, "not_published_fields": ["vaccination_counts"]}}
            pathlib.Path(promos, "sitrep-196-2026-11-26.json").write_text(json.dumps(unprinted))  # not printed
            pathlib.Path(promos, "sitrep-197-2026-11-27.json").write_text(json.dumps({"figures": None}))
            self.assertEqual(rel.sitreps_missing_a_scenario(promos, data), [190, 192, 195])
            for n in (190, 192, 195):
                rel.artifact_path(n, data).write_text("{}")
            self.assertEqual(rel.sitreps_missing_a_scenario(promos, data), [])

    def test_committed_artifacts_equal_a_rebuild(self):
        for path in sorted((DATA / "inputs").glob("sr*.json")):
            n = int(path.stem[2:])
            with self.subTest(sitrep=n):
                built = rel.render(rel.build_artifact(rel.load_release_inputs(n), promoted=rel.promotion_rows(n)))
                self.assertEqual(rel.artifact_path(n).read_text(), built,
                                 f"rebuild with: python3 -m lovs.vaccine_scenario_release --sitrep {n}")

    def test_stored_ring_shares_equal_a_rebuild(self):
        ring = rel.load_ring_timing()
        params = dict(ring["carry_to_bdbv"]["parameters"])
        params["delay_factor"] = tuple(params["delay_factor"])
        self.assertEqual(params, rel.RING_REBUILD)
        rows = ring["calibration"]["accepted_rows"]
        for key, pep in (("protected_share_with_pep", True), ("protected_share_without_pep", False)):
            rebuilt = [round(x, 6) for x in rel.ring_protected_shares(rows, with_pep=pep, **rel.RING_REBUILD)]
            self.assertEqual(ring[key], rebuilt, key)
        self.assertEqual(ring["vaccinated_fraction"],
                         [round(x, 6) for x in rel.ring_vaccinated_fractions(rows, **rel.RING_REBUILD)])

    def test_sr189_values_pinned(self):
        a = json.loads(rel.artifact_path(189).read_text())
        nk = a["values"]["provinces"]["Nord-Kivu"]["uniform_prior_on_strain_match"]
        self.assertAlmostEqual(nk["individual_protection_before_timing"]["p50"], 0.4124, places=4)
        self.assertAlmostEqual(nk["groups"]["frontline_workers"]["share_of_group_infections_averted"]["p50"],
                               0.036, places=4)
        self.assertAlmostEqual(nk["share_of_all_cases_averted"]["p50"], 0.0062, places=4)
        hu = a["values"]["provinces"]["Haut-Uele"]["uniform_prior_on_strain_match"]
        self.assertAlmostEqual(hu["groups"]["pre_registered"]["share_of_group_infections_averted"]["p50"],
                               HU_E5_P50, places=4)


class ArtifactShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = rel.build_artifact(sr189_inputs(), **SMALL)

    def test_model_artifact_fields(self):
        for k in ("id", "label", "artifactType", "subject", "dataDate", "evidenceClass", "values",
                  "methodBasis", "citations", "limitations"):
            self.assertIn(k, self.a)
        self.assertEqual(self.a["id"], "artifact:vaccine-field-effectiveness:sr189")
        self.assertEqual(self.a["artifactType"], rel.ARTIFACT_TYPE)
        self.assertEqual(self.a["dataDate"], "2026-11-19")

    def test_no_national_total_and_strain_match_unidentified(self):
        v = self.a["values"]
        self.assertFalse(v["strain_match_identified"])
        self.assertNotIn("national", json.dumps(v["provinces"]).lower())
        self.assertIn("Zaire ebolavirus only", v["trial_anchor"]["species"])
        self.assertTrue(any("not clinical or vaccine advice" in s for s in self.a["limitations"]))

    def test_share_of_all_cases_only_where_identifiable(self):
        prov = self.a["values"]["provinces"]
        self.assertIsNotNone(prov["Nord-Kivu"]["uniform_prior_on_strain_match"]["share_of_all_cases_averted"])
        for p in ("Haut-Uele", "Tshopo", "Ituri", "Bas-Uele"):
            res = prov[p]
            self.assertIsNone(res["uniform_prior_on_strain_match"]["share_of_all_cases_averted"])
            self.assertTrue(all(r["share_of_all_cases_averted"] is None for r in res["conditional_on_strain_match"]))
            self.assertIn("not computed", res["share_of_all_cases_note"])
            self.assertFalse(res["share_of_all_cases_computed"])
        nk = prov["Nord-Kivu"]
        self.assertTrue(nk["share_of_all_cases_computed"])
        self.assertIn("labelled assumptions", nk["share_of_all_cases_note"])

    def test_conditional_rows_cover_s_and_scale_with_it(self):
        rows = self.a["values"]["provinces"]["Nord-Kivu"]["conditional_on_strain_match"]
        self.assertEqual([r["strain_match"] for r in rows], list(rel.STRAIN_MATCH_VALUES))
        self.assertEqual(rows[0]["groups"]["frontline_workers"]["protection"]["p50"], 0.0)
        p50 = [r["groups"]["frontline_workers"]["protection"]["p50"] for r in rows]
        self.assertEqual(p50, sorted(p50))

    def test_individual_sensitivity_lists_only_upstream_inputs(self):
        for res in self.a["values"]["provinces"].values():
            self.assertLessEqual(set(res["sensitivity_first_order_individual"]), set(rel.UPSTREAM_OF_INDIVIDUAL))

    def test_unassigned_lists_carry_no_case_share(self):
        for p, g in (("Haut-Uele", "pre_registered"), ("Tshopo", "target_list"), ("Bas-Uele", "target_list")):
            cs = [i for i in self.a["values"]["provinces"][p]["groups"][g]["inputs"] if i["name"] == "case_share"]
            self.assertEqual(cs[0]["args"], (0.0,) if isinstance(cs[0]["args"], tuple) else [0.0])
            self.assertIn("not assigned", cs[0]["source"])

    def test_unused_ring_mode_inputs_are_kept_apart(self):
        v = self.a["values"]
        published = {p["name"] for p in v["method_inputs"]}
        unused = {p["name"] for p in v["method_inputs_unused"]}
        self.assertEqual(unused, set(rel.RING_MODE_ONLY))
        self.assertFalse(published & unused)
        for res in v["provinces"].values():
            for key in ("sensitivity_first_order_individual", "sensitivity_first_order_all_cases"):
                self.assertFalse(set(res.get(key, {})) & unused, key)

    def test_group_timing_mode_carries_its_source(self):
        g = self.a["values"]["provinces"]["Nord-Kivu"]["groups"]["frontline_workers"]
        self.assertEqual(g["mode"], "pre_exposure")
        self.assertTrue(g["mode_source"].startswith("ASSUMPTION"))

    def test_province_without_a_group_is_listed_not_computed(self):
        inputs = sr189_inputs()
        del inputs["groups"]["Ituri"]
        a = rel.build_artifact(inputs, **SMALL)
        self.assertEqual(a["values"]["not_computed"]["Ituri"], rel.NOT_ESTIMABLE)
        self.assertNotIn("Ituri", a["values"]["provinces"])

    def test_ituri_contacts_use_a_labelled_assumed_coverage_and_its_record_stays_empty(self):
        g = self.a["values"]["provinces"]["Ituri"]["groups"]["contacts"]
        self.assertEqual(g["mode"], "given")
        cov = [p for p in g["inputs"] if p["name"] == "coverage"][0]
        self.assertEqual(cov["basis"], "assumption")
        self.assertEqual(list(cov["args"]), [0.009, 0.115])
        self.assertTrue(cov["updatable_each_release"])
        for phrase in ("4,326", "12,927", "61/21", "11.5 %", "0.9 %", "343 of 4,370"):
            self.assertIn(phrase, cov["source"])
        rows, _, _ = rel.vaccination_rows(sr189_inputs(), rel.promotion_rows(189))
        self.assertTrue(all(rows["Ituri"].get(f) is None for f in rel.RECORD_FIELDS))
        self.assertNotIn("Ituri", self.a["values"]["not_computed"])

    def test_bas_uele_target_list_is_a_printed_ratio_from_sitrep_186(self):
        g = self.a["values"]["provinces"]["Bas-Uele"]["groups"]["target_list"]
        self.assertEqual(g["mode"], "pre_exposure")
        self.assertTrue(g["mode_source"].startswith("ASSUMPTION"))
        cov = [p for p in g["inputs"] if p["name"] == "coverage"][0]
        lo, hi = cov["args"]
        self.assertAlmostEqual(lo, 2379 / (13202 * 1.1))
        self.assertAlmostEqual(hi, 2379 / (13202 * 0.9))
        for phrase in ("SitRep 186, section 2.6", "PPL, read as frontline workers", "expired"):
            self.assertIn(phrase, cov["source"])

    def test_every_input_has_a_labelled_basis(self):
        v = self.a["values"]
        params = list(v["method_inputs"])
        for res in v["provinces"].values():
            for g in res["groups"].values():
                params.extend(g["inputs"])
        for p in params:
            self.assertIn(p["basis"], ("source", "sitrep", "assumption", "derived"))
            if p["basis"] == "assumption":
                self.assertTrue(p["source"].startswith("ASSUMPTION"), p["name"])
            else:
                self.assertNotIn("ASSUMPTION", p["source"], p["name"])
        self.assertTrue(any(p["basis"] == "sitrep" and p["updatable_each_release"] for p in params))

    def test_nord_kivu_frontline_coverage_carries_its_denominator_range(self):
        g = self.a["values"]["provinces"]["Nord-Kivu"]["groups"]["frontline_workers"]
        cov = [p for p in g["inputs"] if p["name"] == "coverage"][0]
        self.assertEqual(cov["basis"], "assumption")
        lo, hi = cov["args"]
        self.assertAlmostEqual(lo, 4027 / (36070 * 1.25))
        self.assertAlmostEqual(hi, 4027 / (36070 * 0.8))
        self.assertIn("no later SitRep restates", cov["source"])

    def test_protection_field_is_defined_per_mode(self):
        groups = self.a["values"]["provinces"]["Nord-Kivu"]["groups"]
        self.assertIn("per vaccinee", groups["frontline_workers"]["protection_definition"])
        self.assertIn("never vaccinated", groups["contacts"]["protection_definition"])
        self.assertIn("vaccinated_fraction", self.a["values"]["ring_protected_share"])


class RecordParity(unittest.TestCase):
    def promoted(self, **changes) -> list[dict]:
        rows = copy.deepcopy(sr189_inputs()["vaccination_rows_as_printed"])
        for r in rows:
            r.pop("source", None)
            r.update(changes.get(r["province"], {}))
        return rows

    def test_promotion_record_is_used_when_present(self):
        rows, where, _ = rel.vaccination_rows(sr189_inputs(), self.promoted())
        self.assertIn("promotion", where)
        self.assertEqual(rows["Haut-Uele"]["pre_registered"], 4428)
        self.assertTrue(rows["Haut-Uele"]["source"].startswith("INSP SitRep 189"))

    def test_real_record_schema_denominator_names_are_read(self):
        # Shape of the reviewed SR171 and SR173 rows: the denominator is vaccination_expected, and
        # SR170 carries a zone-level row with geography set.
        rows = self.promoted()
        for r in rows:
            if r["province"] == "Haut-Uele":
                r["vaccination_expected"] = r.pop("pre_registered")
        rows.append({"province": "Nord-Kivu", "geography": "Beni", "vaccinated_cumulative": 1000, "target": 7105})
        got, where, set_aside = rel.vaccination_rows(sr189_inputs(), rows)
        self.assertEqual(got["Haut-Uele"]["pre_registered"], 4428)
        self.assertEqual(got["Haut-Uele"]["denominator_field"], "vaccination_expected")
        self.assertIn("Nord-Kivu (Beni)", set_aside)
        p, _ = rel._coverage_param({"name": "g", "coverage": {"from": "record"}}, got, "Haut-Uele")
        self.assertIn("vaccination_expected (the printed denominator)", p.source)
        self.assertAlmostEqual(p.args[0], 3388 / 4428)

    def test_denominators_that_disagree_are_an_error(self):
        rows = self.promoted(**{"Haut-Uele": {"vaccination_expected": 4400}})
        with self.assertRaises(rel.ReleaseInputError):
            rel.vaccination_rows(sr189_inputs(), rows)

    def test_disagreement_is_an_error(self):
        with self.assertRaises(rel.ReleaseInputError):
            rel.vaccination_rows(sr189_inputs(), self.promoted(**{"Haut-Uele": {"pre_registered": 4400}}))

    def test_province_missing_from_promotion_is_an_error(self):
        rows = [r for r in self.promoted() if r["province"] != "Tshopo"]
        with self.assertRaises(rel.ReleaseInputError):
            rel.vaccination_rows(sr189_inputs(), rows)

    def test_inputs_file_cannot_fill_a_field_the_record_leaves_empty(self):
        with self.assertRaises(rel.ReleaseInputError):
            rel.vaccination_rows(sr189_inputs(), self.promoted(**{"Tshopo": {"coverage_pct": None}}))

    def test_the_reviewed_sitrep_189_record_is_used(self):
        # The merged SitRep 189 promotion holds Nord-Kivu, Tshopo and Haut-Uele; Ituri prints no count.
        promoted = rel.promotion_rows(189)
        self.assertEqual(sorted(r["province"] for r in promoted), ["Haut-Uele", "Nord-Kivu", "Tshopo"])
        rows, where, _ = rel.vaccination_rows(sr189_inputs(), promoted)
        self.assertIn("reviewed SitRep promotion record", where)
        self.assertEqual(rows["Nord-Kivu"]["vaccinated_cumulative"], 4828)
        self.assertTrue(all(rows["Ituri"].get(f) is None for f in rel.RECORD_FIELDS))

    def test_an_empty_inputs_row_the_record_lacks_is_kept_but_a_valued_one_is_refused(self):
        promoted = [r for r in self.promoted() if r["province"] != "Ituri"]
        rows, _, _ = rel.vaccination_rows(sr189_inputs(), promoted)
        self.assertIn("Ituri", rows)
        inputs = sr189_inputs()
        next(r for r in inputs["vaccination_rows_as_printed"] if r["province"] == "Ituri")["vaccinated_cumulative"] = 10
        with self.assertRaises(rel.ReleaseInputError):
            rel.vaccination_rows(inputs, promoted)

    def test_two_promotion_files_for_one_sitrep_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("sitrep-189-2026-11-19.json", "sitrep-189-2026-11-20.json"):
                (pathlib.Path(d) / name).write_text("{}")
            with self.assertRaises(rel.ReleaseInputError):
                rel.promotion_rows(189, pathlib.Path(d))

    def test_promotion_rows_reads_the_reviewed_table(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            self.assertIsNone(rel.promotion_rows(189, p))
            doc = {"figures": {"operational_tables": {"vaccination_by_province": []}}}
            (p / "sitrep-189-2026-11-19.json").write_text(json.dumps(doc))
            self.assertEqual(rel.promotion_rows(189, p), [])
            _, where, _ = rel.vaccination_rows(sr189_inputs(), [])
            self.assertIn("has no per-province vaccination record", where)
            _, where, _ = rel.vaccination_rows(sr189_inputs(), None)
            self.assertIn("no reviewed promotion file", where)
            doc["figures"]["operational_tables"]["vaccination_by_province"] = self.promoted()
            (p / "sitrep-189-2026-11-19.json").write_text(json.dumps(doc))
            self.assertEqual(len(rel.promotion_rows(189, p)), 4)


class InputGuards(unittest.TestCase):
    def test_unlabelled_assumed_coverage_is_rejected(self):
        inputs = sr189_inputs()
        inputs["groups"]["Nord-Kivu"][1]["coverage"]["source"] = "share of contacts vaccinated"
        with self.assertRaises(rel.ReleaseInputError):
            rel.build_artifact(inputs, **SMALL)

    def test_unlabelled_pre_exposure_weight_is_rejected(self):
        inputs = sr189_inputs()
        inputs["groups"]["Tshopo"][0]["pre_exposure_weight"]["source"] = "half"
        with self.assertRaises(rel.ReleaseInputError):
            rel.build_artifact(inputs, **SMALL)

    def test_share_of_all_cases_refused_when_a_group_drops_out(self):
        inputs = sr189_inputs()
        inputs["groups"]["Nord-Kivu"][1]["coverage"] = {"from": "record"}  # Nord-Kivu row has no denominator
        with self.assertRaises(rel.ReleaseInputError):
            rel.build_artifact(inputs, **SMALL)

    def test_ring_timing_is_a_labelled_assumption_and_pep_is_disclosed(self):
        p = rel.ring_timing_param(rel.load_ring_timing())
        self.assertEqual(p.basis, "assumption")
        for phrase in ("stretched", "onset-to-transmission", "post-exposure credit"):
            self.assertIn(phrase, p.source)
        self.assertIn("Ring and mixed-timing rows differ", rel.method_inputs().pep_effect.source)

    def test_promotion_glob_matches_zero_padded_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            doc = {"figures": {"operational_tables": {"vaccination_by_province": [{"province": "X"}]}}}
            (pathlib.Path(d) / "sitrep-082-2026-08-01.json").write_text(json.dumps(doc))
            self.assertEqual(rel.promotion_rows(82, pathlib.Path(d)), [{"province": "X"}])

    def test_pre_exposure_mode_needs_a_labelled_assumption(self):
        inputs = sr189_inputs()
        del inputs["groups"]["Nord-Kivu"][0]["mode_source"]
        with self.assertRaises(rel.ReleaseInputError):
            rel.build_artifact(inputs, **SMALL)

    def test_share_of_all_cases_needs_frontline_and_contact_groups(self):
        # Ituri's contacts group carries a case share, but its frontline workers are not split out.
        inputs = sr189_inputs()
        inputs["report_share_of_all_cases"].append("Ituri")
        with self.assertRaisesRegex(rel.ReleaseInputError, "missing frontline_workers"):
            rel.build_artifact(inputs, **SMALL)

    def test_ituri_note_gives_the_missing_split_as_the_reason(self):
        a = rel.build_artifact(sr189_inputs(), **SMALL)
        prov = a["values"]["provinces"]
        self.assertIn("no frontline workers group is defined", prov["Ituri"]["share_of_all_cases_note"])
        self.assertIn("not split into frontline workers and contacts", prov["Bas-Uele"]["share_of_all_cases_note"])

    def test_printed_numerator_above_the_record_is_an_error(self):
        inputs = sr189_inputs()
        inputs["groups"]["Nord-Kivu"][0]["coverage"]["numerator"] = 5000  # record holds 4,828
        with self.assertRaisesRegex(rel.ReleaseInputError, "exceeds"):
            rel.build_artifact(inputs, promoted=rel.promotion_rows(189), **SMALL)

    def test_numerator_that_differs_from_the_record_needs_a_reason(self):
        inputs = sr189_inputs()
        cov = inputs["groups"]["Nord-Kivu"][0]["coverage"]
        del cov["record_difference"]
        with self.assertRaisesRegex(rel.ReleaseInputError, "record_difference"):
            rel.build_artifact(inputs, promoted=rel.promotion_rows(189), **SMALL)
        # A reason copied from another release, not naming this record's count, is refused too.
        cov["record_difference"] = "the SitRep 188 record holds 4,370 vaccinated in all"
        with self.assertRaisesRegex(rel.ReleaseInputError, "naming 4,828"):
            rel.build_artifact(inputs, promoted=rel.promotion_rows(189), **SMALL)
        # A larger number that merely contains the count does not name it.
        cov["record_difference"] = "a later record holds 14,828 vaccinated in all"
        with self.assertRaisesRegex(rel.ReleaseInputError, "naming 4,828"):
            rel.build_artifact(inputs, promoted=rel.promotion_rows(189), **SMALL)

    def test_assumed_coverage_stops_when_the_record_gains_a_count(self):
        groups = sr189_inputs()["groups"]
        ituri = groups["Ituri"]
        self.assertEqual(ituri[0]["coverage"]["from"], "assumption")
        cov, _ = rel._coverage_param(ituri[0], {}, "Ituri")
        self.assertTrue(cov.updatable_each_release)
        rel._check_assumed_coverage_against_record("Ituri", ituri, {})  # no count: nothing to revisit
        rows = {"Ituri": {"vaccinated_cumulative": 5100}}
        with self.assertRaisesRegex(rel.ReleaseInputError, "naming 5,100"):
            rel._check_assumed_coverage_against_record("Ituri", ituri, rows)
        ituri[0]["coverage"]["record_acknowledged"] = "the record's 5,100 counts all Ituri vaccinees, not contacts"
        rel._check_assumed_coverage_against_record("Ituri", ituri, rows)
        # Nord-Kivu's contacts coverage is assumed too, but its frontline numerator already names the
        # record's 4,828, so the province has accounted for the count.
        rel._check_assumed_coverage_against_record("Nord-Kivu", groups["Nord-Kivu"],
                                                   {"Nord-Kivu": {"vaccinated_cumulative": 4828}})
        with self.assertRaisesRegex(rel.ReleaseInputError, "naming 5,000"):
            rel._check_assumed_coverage_against_record("Nord-Kivu", groups["Nord-Kivu"],
                                                       {"Nord-Kivu": {"vaccinated_cumulative": 5000}})

    def test_reported_province_without_groups_is_an_error(self):
        inputs = sr189_inputs()
        inputs["report_share_of_all_cases"].append("Sud-Kivu")
        with self.assertRaises(rel.ReleaseInputError):
            rel.build_artifact(inputs, **SMALL)

    def test_post_exposure_credit_goes_to_vaccinees_only(self):
        # With full post-exposure credit (pep = ve_post), the protected share must equal the share of
        # infected members still without symptoms at vaccination, never 1.
        row = {"weight": 1.0, "ve_post": 0.9, "pep_effect": 0.9, "onset_days": 5.0, "index_share": 0.6,
               "tau_mean": 3.9}
        kw = dict(n_samples=1, draws=4000, seed=3, delay_factor=(1.0, 1.0))
        share = rel.ring_protected_shares([row], with_pep=True, **kw)[0]
        self.assertLess(share, 0.95)
        import random
        from lovs import vaccine_field_effectiveness as vfe
        rng = random.Random(3)
        rng.choices([row], weights=[1.0], k=1)
        rng.uniform(1.0, 1.0)
        prof = vfe.ring_cohort_profile(
            0.9, rel.Param("d", "gamma_mean_sd", (7.6, 5.5), "derived", "t"),
            rel.Param("o", "fixed", (5.0,), "derived", "t"),
            rel.tau_param(3.9),
            rel.Param("inc", "gamma_mean_sd", rel.BDBV_INCUBATION, "derived", "t"),
            rel.Param("s", "fixed", (0.6,), "derived", "t"), draws=4000, seed=3, pep_effect=0.9)
        self.assertAlmostEqual(share, prof["vaccinee_fraction_of_infected"], places=9)

    def test_ratio_of_printed_values_needs_a_denominator_assumption(self):
        inputs = sr189_inputs()
        del inputs["groups"]["Nord-Kivu"][0]["coverage"]["denominator_assumption"]
        with self.assertRaises(rel.ReleaseInputError):
            rel.build_artifact(inputs, **SMALL)

    def test_zone_rows_in_the_inputs_file_are_listed_as_set_aside(self):
        inputs = sr189_inputs()
        inputs["vaccination_rows_as_printed"].append({"province": "Nord-Kivu", "geography": "Beni",
                                                      "vaccinated_cumulative": 900})
        a = rel.build_artifact(inputs, **SMALL)
        self.assertIn("Nord-Kivu (Beni)", a["values"]["not_computed"])

    def test_promoted_rows_keep_inputs_locators_and_get_a_default_otherwise(self):
        inputs = sr189_inputs()
        rows = copy.deepcopy(inputs["vaccination_rows_as_printed"])
        for r in rows:
            r.pop("source")
            r["source_page"] = 8
        rows.append({"province": "Bas-Uele", "vaccinated_cumulative": 2000, "source_pages": [8, 9]})
        got, _, _ = rel.vaccination_rows(inputs, rows)
        self.assertEqual(got["Tshopo"]["source"], inputs["vaccination_rows_as_printed"][3]["source"])
        self.assertEqual(got["Bas-Uele"]["source"], "INSP SitRep 189, pages 8, 9 (reviewed promotion record)")

    def test_all_cases_sensitivity_excludes_the_indirect_multiplier(self):
        a = rel.build_artifact(sr189_inputs(), **SMALL)
        self.assertNotIn("indirect_multiplier",
                         a["values"]["provinces"]["Nord-Kivu"]["sensitivity_first_order_all_cases"])

    def test_wrong_sitrep_number_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "inputs").mkdir()
            (pathlib.Path(d) / "inputs" / "sr190.json").write_text(json.dumps({"sitrep_number": 189}))
            with self.assertRaises(rel.ReleaseInputError):
                rel.load_release_inputs(190, pathlib.Path(d))

    def test_printed_ratio_is_capped_at_one(self):
        spec = {"name": "g", "coverage": {"from": "printed_ratio", "numerator": 12, "numerator_source": "a",
                                          "denominator": 10, "denominator_source": "b",
                                          "denominator_assumption": "ASSUMPTION: same people",
                                          "denominator_factor_range": [0.8, 1.25]}}
        p, _ = rel._coverage_param(spec, {}, "X")
        self.assertEqual(p.args, (12 / 12.5, 1.0))

    def test_printed_ratio_needs_a_factor_range_around_one(self):
        spec = {"name": "g", "coverage": {"from": "printed_ratio", "numerator": 1, "numerator_source": "a",
                                          "denominator": 10, "denominator_source": "b",
                                          "denominator_assumption": "ASSUMPTION: same people",
                                          "denominator_factor_range": [1.1, 1.3]}}
        with self.assertRaises(rel.ReleaseInputError):
            rel._coverage_param(spec, {}, "X")


class RingTimingTau(unittest.TestCase):
    ROW = {"weight": 1.0, "ve_post": 0.95, "pep_effect": 0.3, "onset_days": 6.0, "index_share": 0.7}
    KW = dict(n_samples=1, draws=4000, seed=5, delay_factor=(1.0, 1.0))

    def test_tau_mean_is_the_serial_interval_minus_each_sets_incubation(self):
        rows = rel.load_ring_timing()["calibration"]["accepted_rows"]
        self.assertTrue(rows)
        lo, hi = rel.ZAIRE_INCUBATION_MEAN_RANGE
        for r in rows:
            self.assertAlmostEqual(r["tau_mean"] + r["inc_mean"], rel.SERIAL_INTERVAL_ZAIRE, places=9)
            self.assertTrue(lo <= r["inc_mean"] <= hi)
        self.assertEqual(rel.ZAIRE_INCUBATION_MEAN_RANGE, (9.1, 11.4))
        self.assertTrue(rel.tau_param(5.0).source.startswith("ASSUMPTION"))
        self.assertEqual(rel.tau_param(5.0).args, (5.0, rel.TAU_SD))

    def test_the_carry_uses_each_rows_tau(self):
        early = rel.ring_protected_shares([{**self.ROW, "tau_mean": 3.3}], with_pep=False, **self.KW)[0]
        late = rel.ring_protected_shares([{**self.ROW, "tau_mean": 7.3}], with_pep=False, **self.KW)[0]
        self.assertGreater(late, early)

    def test_a_row_without_tau_is_refused(self):
        with self.assertRaises(ValueError):
            rel.ring_protected_shares([dict(self.ROW)], with_pep=True, **self.KW)

    def test_method_text_matches_the_calibration_tool(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "calibrate_vaccine_ring_timing", rel.REPO_ROOT / "tools" / "calibrate_vaccine_ring_timing.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        ring = rel.load_ring_timing()
        method = ring["calibration"]["method"]
        for phrase in (f"{tool.N_SETS:,} parameter sets", f"seed {tool.SEED}", f"{tool.DRAWS:,} cohort draws",
                       f"widened by {tool.SCREEN_MARGIN}", f"{tool.CONFIRM_DRAWS:,} draws"):
            self.assertIn(phrase, method)
        self.assertEqual(ring["calibration"]["result"]["of"], tool.N_SETS)

    def test_known_misfit_text_matches_the_rows(self):
        cal = rel.load_ring_timing()["calibration"]
        rows = cal["accepted_rows"]
        total = sum(r["weight"] for r in rows)
        acc, median = 0.0, None
        for p0, w in sorted((r["trial_late_likelihood"], r["weight"]) for r in rows):
            acc += w
            if acc >= total / 2:
                median = p0
                break
        text = " ".join(cal["known_misfit"])
        self.assertIn(f"largest weight (P of 0 cases) {max(r['weight'] for r in rows):.2f}", text)
        self.assertIn(f"weight-weighted median {median:.2f}", text)
        self.assertIn(f"largest accepted {max(r['early_share_drc'] for r in rows):.3f}", text)
        inc = sum(r["inc_mean"] * r["weight"] for r in rows) / total
        tau = sum(r["tau_mean"] * r["weight"] for r in rows) / total
        self.assertIn(f"weighted incubation mean {inc:.1f} d", text)
        self.assertIn(f"onset-to-transmission ({tau:.1f} d)", text)
        lo = rel.ZAIRE_INCUBATION_MEAN_RANGE[0]
        near = sum(r["weight"] for r in rows if r["inc_mean"] <= lo + 0.2) / total
        self.assertIn(f"{near:.0%} of the weight within 0.2 d of {lo} d", text)


if __name__ == "__main__":
    unittest.main()
