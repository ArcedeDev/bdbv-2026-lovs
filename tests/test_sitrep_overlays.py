# SPDX-License-Identifier: Apache-2.0
"""Tests for the SitRep19 Phase B generation overlays (lovs.sitrep_overlays).

Covers the death-series projection (deathsConfirmed + deathsBasis), the
province-burden floor, and the headline source clock, each built from the same
reviewed source-of-truth the canonical generator uses. ND-correct: a missing
figure is None ("not reported"), never zero.
"""
from __future__ import annotations

import json
import pathlib
import unittest

from lovs import sitrep_overlays as ov
from lovs import sitrep_promotions


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FULL_MANIFEST = json.loads((REPO_ROOT / "data/bundibugyo-2026/manifest.json").read_text())


def _promotions():
    return sitrep_promotions.reviewed_promotions_by_number()


class TestDeathBasis(unittest.TestCase):
    def test_basis_confirmed_on_cutoff(self):
        # 2026-06-02 is the laboratory-confirmed cutoff; on/after it the basis is
        # 'confirmed'.
        self.assertEqual("confirmed", ov.death_basis_for_date("2026-06-02"))
        self.assertEqual("confirmed", ov.death_basis_for_date("2026-06-03T23:59:59Z"))

    def test_basis_suspected_before_cutoff(self):
        self.assertEqual("suspected", ov.death_basis_for_date("2026-06-01"))
        self.assertEqual("suspected", ov.death_basis_for_date("2026-05-29"))

    def test_basis_empty_date_defaults_suspected(self):
        # An empty/absent date is the broad register by default (never 'confirmed').
        self.assertEqual("suspected", ov.death_basis_for_date(""))
        self.assertEqual("suspected", ov.death_basis_for_date(None))


class TestConfirmedDeathSeries(unittest.TestCase):
    def test_full_series_values_and_basis(self):
        # The apples-to-apples confirmed-death history matches the contract:
        # 26 May 18, 29 May 43, 30 May 43, 31 May 49, 1 Jun 61, 2 Jun 63,
        # 3 Jun 65, 4 Jun 83. The 26 May base (17 DRC + 1 UGA = 18) is composed
        # from the manifest.
        series = ov.confirmed_death_series(FULL_MANIFEST, _promotions())
        as_pairs = [(p["date"], p["deathsConfirmed"]) for p in series]
        self.assertEqual(
            [
                ("2026-05-26", 18),
                ("2026-05-29", 43),
                ("2026-05-30", 43),
                ("2026-05-31", 49),
                ("2026-06-01", 61),
                ("2026-06-02", 63),
                ("2026-06-03", 65),
                ("2026-06-04", 83),
            ],
            as_pairs,
        )

    def test_jun2_point_is_confirmed_basis_63(self):
        # Contract assertion: the Jun-2 row has deathsConfirmed=63 and
        # deathsBasis='confirmed'.
        series = ov.confirmed_death_series(FULL_MANIFEST, _promotions())
        jun2 = next(p for p in series if p["date"] == "2026-06-02")
        self.assertEqual(63, jun2["deathsConfirmed"])
        self.assertEqual("confirmed", jun2["basis"])
        self.assertEqual("inrb-sitrep-019-2026-06-02", jun2["sourceId"])

    def test_pre_jun2_point_is_suspected_basis_with_history_value(self):
        # Contract assertion: a pre-Jun-2 row carries deathsBasis='suspected'
        # with the right deathsConfirmed history value (31 May -> 49).
        series = ov.confirmed_death_series(FULL_MANIFEST, _promotions())
        may31 = next(p for p in series if p["date"] == "2026-05-31")
        self.assertEqual(49, may31["deathsConfirmed"])
        self.assertEqual("suspected", may31["basis"])
        self.assertEqual("inrb-sitrep-017-2026-05-31", may31["sourceId"])

    def test_base_point_omitted_when_components_absent_nd(self):
        # ND-correct: when the manifest does not expose the base death components
        # (e.g. the sanitized public manifest) and no explicit base is supplied,
        # the 26 May base point is omitted, never fabricated as zero.
        sanitized = {"entries": []}
        series = ov.confirmed_death_series(sanitized, _promotions())
        self.assertNotIn("2026-05-26", [p["date"] for p in series])
        # The SitRep-sourced points still appear.
        self.assertEqual(63, next(p for p in series if p["date"] == "2026-06-02")["deathsConfirmed"])

    def test_explicit_base_value_resolves_point(self):
        # The canonical generator passes the reconciled base explicitly so the
        # 26 May point resolves even when the manifest sanitizes the components.
        series = ov.confirmed_death_series({"entries": []}, _promotions(), base_value=18)
        base = next(p for p in series if p["date"] == "2026-05-26")
        self.assertEqual(18, base["deathsConfirmed"])
        self.assertEqual("suspected", base["basis"])


class TestProvinceBurden(unittest.TestCase):
    def test_province_burden_from_sitrep19(self):
        # The always-fresh June-2 province floor from SitRep #019 Table 1:
        # Ituri 341, Nord-Kivu 19, Sud-Kivu 3, asOf 2026-06-02, sourceId
        # inrb-sitrep-019-2026-06-02. confirmedDeaths come from the same table.
        s19 = _promotions()[19]
        burden = ov.province_burden(s19)
        by_province = {row["province"]: row for row in burden}
        self.assertEqual(
            {"Ituri", "Nord-Kivu", "Sud-Kivu"}, set(by_province)
        )
        self.assertEqual(341, by_province["Ituri"]["confirmed"])
        self.assertEqual(19, by_province["Nord-Kivu"]["confirmed"])
        self.assertEqual(3, by_province["Sud-Kivu"]["confirmed"])
        for row in burden:
            self.assertEqual("2026-06-02", row["asOf"])
            self.assertEqual("inrb-sitrep-019-2026-06-02", row["sourceId"])
            self.assertIn("confirmedDeaths", row)

    def test_province_burden_confirmed_deaths_present(self):
        s19 = _promotions()[19]
        by_province = {row["province"]: row for row in ov.province_burden(s19)}
        self.assertEqual(48, by_province["Ituri"]["confirmedDeaths"])
        self.assertEqual(13, by_province["Nord-Kivu"]["confirmedDeaths"])
        self.assertEqual(1, by_province["Sud-Kivu"]["confirmedDeaths"])

    def test_province_burden_nd_when_deaths_absent(self):
        # ND-correct: a province row that does not report a confirmed-death figure
        # carries confirmedDeaths=None (not zero).
        fixture = {
            "data_as_of": "2026-06-02",
            "source_id": "inrb-sitrep-019-2026-06-02",
            "figures": {
                "health_zone_table": {
                    "province_totals": [
                        {"province": "Ituri", "confirmed": 341},  # no confirmed_deaths
                    ]
                }
            },
        }
        burden = ov.province_burden(fixture)
        self.assertEqual(1, len(burden))
        self.assertIsNone(burden[0]["confirmedDeaths"])
        self.assertEqual(341, burden[0]["confirmed"])

    def test_province_burden_empty_when_no_table(self):
        self.assertEqual([], ov.province_burden({"figures": {}, "data_as_of": "x", "source_id": "y"}))


class TestDependencyAuditDerivations(unittest.TestCase):
    """Blocker 4b/4c: the dep-audit clock basis + corridor counts are derived."""

    def _snapshot(self, *, confirmed_value, source_id, as_of="2026-06-02T23:59:59Z"):
        import refresh_pipeline as rp
        from lovs import lovs_reconciler as lr

        rc = lr.ReconciledCount(
            minimum=confirmed_value,
            maximum=confirmed_value,
            primary_value=confirmed_value,
            primary_source_id=source_id,
            conflicting_source_ids=(),
        )

        class _Snap:
            reported_counts = {"confirmed": rc}

        snap = _Snap()
        snap.as_of = as_of
        return rp, snap

    def test_confirmed_endpoint_clause_is_derived_from_source(self):
        # 4b: the confirmable_underlying_trajectory clock basis names the SitRep
        # the headline actually rides (#019/370/2026-06-02), not a hardcoded #017.
        rp, snap = self._snapshot(
            confirmed_value=370, source_id="inrb-sitrep-019-2026-06-02"
        )
        self.assertEqual(
            "SitRep #019 (370 confirmed, data as of 2026-06-02)",
            rp._confirmed_endpoint_clause(snap),
        )

    def test_confirmed_endpoint_clause_moves_with_headline(self):
        # A different headline source moves the clause automatically.
        rp, snap = self._snapshot(
            confirmed_value=328,
            source_id="inrb-sitrep-017-2026-05-31",
            as_of="2026-05-31T23:59:59Z",
        )
        self.assertEqual(
            "SitRep #017 (328 confirmed, data as of 2026-05-31)",
            rp._confirmed_endpoint_clause(snap),
        )

    def test_confirmed_endpoint_clause_falls_back_for_non_sitrep_source(self):
        rp, snap = self._snapshot(
            confirmed_value=128, source_id="ecdc-bdbv-drc-uga-2026-05-27"
        )
        clause = rp._confirmed_endpoint_clause(snap)
        self.assertIn("ecdc-bdbv-drc-uga-2026-05-27", clause)
        self.assertIn("128 confirmed", clause)


class TestHeadlineSourceClock(unittest.TestCase):
    def test_clock_derives_from_confirmed_primary(self):
        clock = ov.headline_source_clock("inrb-sitrep-019-2026-06-02")
        self.assertEqual(
            {"headline_count_endpoint": "inrb-sitrep-019-2026-06-02"}, clock
        )

    def test_clock_strips_live_suffix(self):
        # The published clock names the public id, not the -live manifest variant.
        clock = ov.headline_source_clock("inrb-sitrep-019-2026-06-02-live")
        self.assertEqual("inrb-sitrep-019-2026-06-02", clock["headline_count_endpoint"])

    def test_clock_empty_when_no_source(self):
        self.assertEqual({}, ov.headline_source_clock(None))
        self.assertEqual({}, ov.headline_source_clock(""))

    def test_invariant_passes_when_clock_matches(self):
        clock = ov.headline_source_clock("inrb-sitrep-019-2026-06-02")
        # No raise.
        ov.assert_headline_clock_matches_source(clock, "inrb-sitrep-019-2026-06-02")

    def test_invariant_fails_on_mismatch(self):
        # A stale clock that names SitRep #018 while the headline rides #019 FAILs.
        stale_clock = {"headline_count_endpoint": "inrb-sitrep-018-2026-06-01"}
        with self.assertRaises(ValueError) as ctx:
            ov.assert_headline_clock_matches_source(stale_clock, "inrb-sitrep-019-2026-06-02")
        self.assertIn("headline_count_endpoint", str(ctx.exception))

    def test_invariant_ok_when_both_absent(self):
        ov.assert_headline_clock_matches_source({}, None)


class TestMakeBriefMethodologyConstants(unittest.TestCase):
    """Blocker 5: the brief Imperial/CFR/doubling prose interpolates the
    structured methodology constants (single source of truth in
    lovs.lovs_death_back_projection), reproducing the prior literals exactly."""

    def test_methodology_constants_source_of_truth(self):
        import make_brief as mb
        from lovs import lovs_death_back_projection as dbp

        mc = mb._methodology_constants()
        self.assertEqual([dbp.IMPERIAL_REFERENCE_LOW, dbp.IMPERIAL_REFERENCE_HIGH], mc["imperial_reference"])
        self.assertEqual(list(dbp.IMPERIAL_CFR_SCENARIOS), mc["cfr"])
        self.assertEqual(dbp.CENTRAL_DOUBLING_TIME_DAYS, mc["central_doubling_time_days"])
        self.assertEqual(list(dbp.OBSERVED_DOUBLING_TIMES_DAYS), mc["observed_doubling_times_days"])

    def test_interpolated_strings_reproduce_prior_literals(self):
        # The interpolation must render exactly the prior hand-typed prose:
        # 400-900, CFR 26/33/40, Imperial 14-day central, our 7-day central.
        import make_brief as mb

        mc = mb._methodology_constants()
        self.assertEqual("400-900", f"{mc['imperial_reference'][0]}-{mc['imperial_reference'][1]}")
        self.assertEqual("26/33/40", mb._format_cfr_slashes(mc["cfr"]))
        self.assertEqual("14", mb._format_days(mc["imperial_doubling_time_days"]))
        self.assertEqual("7", mb._format_days(mc["central_doubling_time_days"]))

    def test_brief_prose_carries_structured_values(self):
        # The rendered framing paragraph (line ~1197) names the structured
        # values, and does NOT carry a stale literal that disagrees with them.
        import make_brief as mb

        source = pathlib.Path(mb.__file__).read_text(encoding="utf-8")
        # The literals must now be interpolation placeholders, not hardcoded.
        self.assertIn("{imperial_reference_band} total cases in DRC", source)
        self.assertIn("CFR {cfr_slashes}, at Imperial's borrowed {imperial_doubling_days}-day", source)
        self.assertIn("roughly {central_doubling_days} days observed", source)
        # The old hardcoded literal forms are gone from that paragraph.
        self.assertNotIn("400-900 total cases in DRC", source)
        self.assertNotIn("CFR 26/33/40, at Imperial's borrowed 14-day", source)


if __name__ == "__main__":
    unittest.main()
