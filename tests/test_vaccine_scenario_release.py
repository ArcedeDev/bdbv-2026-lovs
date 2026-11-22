"""Tests for lovs.vaccine_scenario_release (the per-release vaccine scenario artifact).

  * reproducibility: every committed artifact equals a rebuild from its inputs,
    and the stored ring-timing shares equal a rebuild from the stored
    calibration rows;
  * record parity: the reviewed promotion's vaccination rows win, and any
    field they share with the release inputs file must agree;
  * guard rails: assumptions are labelled, no national total, no share of all
    cases where the list's make-up is not printed, Zaire anchor named.
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
# Haut-Uele's list carries no case share (its make-up is not printed); dropping
# that draw and crediting post-exposure protection to vaccinees only moved this median from 0.2181.
HU_E5_P50 = 0.2113


def sr189_inputs() -> dict:
    return rel.load_release_inputs(189)


class CommittedArtifacts(unittest.TestCase):
    def test_every_inputs_file_has_a_matching_artifact(self):
        inputs = sorted(p.stem for p in (DATA / "inputs").glob("sr*.json"))
        artifacts = sorted(p.stem.removeprefix("scenario-") for p in DATA.glob("scenario-sr*.json"))
        self.assertTrue(inputs)
        self.assertEqual(inputs, artifacts)

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
        self.assertAlmostEqual(nk["share_of_all_cases_averted"]["p50"], 0.0053, places=4)
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
        for p in ("Haut-Uele", "Tshopo"):
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
        for p, g in (("Haut-Uele", "pre_registered"), ("Tshopo", "target_list")):
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

    def test_province_without_denominator_is_listed_not_computed(self):
        self.assertIn("Ituri", self.a["values"]["not_computed"])
        self.assertNotIn("Ituri", self.a["values"]["provinces"])

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

    def test_reported_province_without_groups_is_an_error(self):
        inputs = sr189_inputs()
        inputs["report_share_of_all_cases"].append("Ituri")
        with self.assertRaises(rel.ReleaseInputError):
            rel.build_artifact(inputs, **SMALL)

    def test_post_exposure_credit_goes_to_vaccinees_only(self):
        # With full post-exposure credit (pep = ve_post), the protected share must equal the share of
        # infected members still without symptoms at vaccination, never 1.
        row = {"weight": 1.0, "ve_post": 0.9, "pep_effect": 0.9, "onset_days": 5.0, "index_share": 0.6}
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
            rel.Param("tau", "gamma_mean_sd", (3.9, 3.0), "derived", "t"),
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


if __name__ == "__main__":
    unittest.main()
