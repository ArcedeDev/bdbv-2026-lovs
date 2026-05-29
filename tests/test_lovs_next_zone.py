# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import unittest

from lovs import lovs_next_zone
from lovs import lovs_reconciler
from lovs import lovs_visibility


def _count(value: int) -> lovs_reconciler.ReconciledCount:
    return lovs_reconciler.ReconciledCount(value, value, value, "test-source", ())


def _visibility() -> lovs_visibility.VisibilityPosterior:
    return lovs_visibility.VisibilityPosterior(
        outbreak_id="test",
        geography_id="test",
        as_of="2026-05-22T23:59:59Z",
        visibility_grade="medium",
        reporting_completeness=lovs_visibility.IntervalProportion(0.5, 0.7, 0.3, 0.9),
        publication_latency_days=lovs_visibility.IntervalDays(1, 2, 0, 4),
        confirmation_backlog=lovs_visibility.IntervalCount(1, 2, 0, 4),
        uncertainty_drivers=(),
        missing_data_requests=(),
        priors_cited=(),
        model_version="test",
        provenance_ids=(),
        status="ok",
    )


class TestLovsNextZone(unittest.TestCase):
    def test_zone_attributed_counts_drive_source_load(self):
        snapshot = lovs_reconciler.OutbreakSnapshot(
            outbreak_id="test",
            as_of="2026-05-22T23:59:59Z",
            pathogen="BDBV",
            country_scope=("COD",),
            reported_counts={"confirmed": _count(84)},
            reported_deaths=None,
            affected_zones=("low", "high"),
            sources=("test-source",),
            case_definition_version=None,
            source_conflict_notes=(),
            deaths_to_confirmed_tension_flag=False,
            model_version="test",
            zone_attributed_counts={
                "low": {"confirmed": 1},
                "high": {"confirmed": 19},
            },
        )

        corridors = lovs_next_zone.next_zone_risk(
            snapshot=snapshot,
            visibility=_visibility(),
            candidate_targets=("target",),
            horizon_days=30,
            n_samples=50,
            seed=1,
        )

        by_source = {c.source_geography_id: c for c in corridors}
        self.assertGreater(
            by_source["high"].risk_visibility_adjusted.upper_50,
            by_source["low"].risk_visibility_adjusted.upper_50,
        )
        drivers = " ".join(d for c in corridors for d in c.drivers)
        self.assertIn("zone-attributed confirmed count 19", drivers)
        self.assertNotIn("aggregate confirmed count 84", drivers)

    def test_zero_confirmed_source_yields_no_corridor(self):
        """A zero-confirmed source has no observed transmission source.

        INSP-monitored zones that carry suspected cases but zero
        laboratory-confirmed cases (e.g. the 2026-05-28 ingest's rimba,
        bambu, ...) are retained in affected_zones for map/affected-zone
        presence, but the corridor model is confirmed-driven: a zero-confirmed
        source produces zero hazard, so it must emit NO corridor rather than a
        vacuous [0,0] band.
        """
        snapshot = lovs_reconciler.OutbreakSnapshot(
            outbreak_id="test",
            as_of="2026-05-22T23:59:59Z",
            pathogen="BDBV",
            country_scope=("COD",),
            reported_counts={"confirmed": _count(20)},
            reported_deaths=None,
            affected_zones=("zero_confirmed", "nonzero_confirmed"),
            sources=("test-source",),
            case_definition_version=None,
            source_conflict_notes=(),
            deaths_to_confirmed_tension_flag=False,
            model_version="test",
            zone_attributed_counts={
                "zero_confirmed": {"confirmed": 0},
                "nonzero_confirmed": {"confirmed": 19},
            },
        )

        corridors = lovs_next_zone.next_zone_risk(
            snapshot=snapshot,
            visibility=_visibility(),
            candidate_targets=("target",),
            horizon_days=30,
            n_samples=50,
            seed=1,
        )

        sources = {c.source_geography_id for c in corridors}
        self.assertIn("nonzero_confirmed", sources)
        self.assertNotIn("zero_confirmed", sources)
        # And no corridor should carry a degenerate [0,0] adjusted-50 band.
        for c in corridors:
            self.assertFalse(
                c.risk_visibility_adjusted.lower_50 == 0.0
                and c.risk_visibility_adjusted.upper_50 == 0.0,
                f"corridor {c.source_geography_id}->{c.target_geography_id} "
                "has a degenerate [0,0] band",
            )


if __name__ == "__main__":
    unittest.main()
