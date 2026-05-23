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


if __name__ == "__main__":
    unittest.main()
