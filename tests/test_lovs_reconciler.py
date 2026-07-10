# SPDX-License-Identifier: Apache-2.0
"""Tests for the source-cadence count reconciler."""
from __future__ import annotations

import dataclasses
import unittest

from lovs import lovs_archive
from lovs import lovs_reconciler


def _snap(
    source_id: str,
    retrieved_at: str,
    confirmed: int,
    *,
    published_at: str | None = None,
    normalized_content: dict | None = None,
) -> lovs_archive.ArchivedSnapshot:
    content = {"cases_confirmed": confirmed}
    if normalized_content:
        content.update(normalized_content)
    return lovs_archive.ArchivedSnapshot(
        provenance=lovs_archive.ProvenanceRecord(
            source_id=source_id,
            source_tier="official_who",
            publisher="Publisher",
            url=f"https://example.com/{source_id}",
            retrieved_at=retrieved_at,
            published_at=published_at or retrieved_at,
            content_hash="0" * 64,
            license="test",
            extraction_status="success",
            root_provenance_chain=(),
        ),
        outbreak_id="x",
        pathogen="BDBV",
        country_scope=("COD",),
        geography_id="cod",
        raw_bytes_relpath=None,
        raw_archive_status="private_restricted_bytes",
        normalized_content=content,
    )


class TestReconcileCountCadence(unittest.TestCase):

    def test_public_conflict_projection_deduplicates_without_changing_seed_material(self):
        import refresh_pipeline

        count = lovs_reconciler.ReconciledCount(
            minimum=10,
            maximum=12,
            primary_value=12,
            primary_source_id="primary",
            conflicting_source_ids=("b", "a", "a", "primary", ""),
        )

        self.assertEqual(("b", "a", "a", "primary", ""), count.conflicting_source_ids)
        self.assertEqual(
            ("a", "b"), lovs_reconciler.normalized_conflicting_source_ids(count)
        )
        snapshot = lovs_reconciler.OutbreakSnapshot(
            outbreak_id="x",
            as_of="2026-07-08T23:59:59Z",
            pathogen="BDBV",
            country_scope=("COD",),
            reported_counts={"confirmed": count},
            reported_deaths={},
            affected_zones=(),
            sources=(),
            case_definition_version=None,
            source_conflict_notes=(),
            deaths_to_confirmed_tension_flag=False,
            model_version="test",
        )
        seed_before = lovs_reconciler.snapshot_content_seed(snapshot)
        snapshot_before = dataclasses.asdict(snapshot)

        serialized = refresh_pipeline._count_output(count)

        self.assertEqual(["a", "b"], serialized["conflicting_source_ids"])
        self.assertEqual(snapshot_before, dataclasses.asdict(snapshot))
        self.assertEqual(seed_before, lovs_reconciler.snapshot_content_seed(snapshot))

    def test_fresher_lower_count_does_not_become_headline(self):
        archive = lovs_archive.Archive(
            root_path="test",
            snapshots=(
                _snap("older-higher", "2026-05-20T00:00:00Z", 53),
                _snap("newer-lower", "2026-05-21T00:00:00Z", 51),
            ),
        )

        snapshot = lovs_reconciler.reconcile(
            archive, outbreak_id="x", as_of="2026-05-21T23:59:59Z"
        )

        confirmed = snapshot.reported_counts["confirmed"]
        self.assertEqual(confirmed.primary_value, 53)
        self.assertEqual(confirmed.primary_source_id, "older-higher")
        self.assertEqual(confirmed.minimum, 51)
        self.assertEqual(confirmed.maximum, 53)

    def test_zone_attributed_table_orders_by_publication_date_not_retrieval_date(self):
        archive = lovs_archive.Archive(
            root_path="test",
            snapshots=(
                _snap(
                    "older-table-retrieved-late",
                    "2026-05-22T12:00:00Z",
                    10,
                    published_at="2026-05-18T00:00:00Z",
                    normalized_content={
                        "affected_health_zones": {
                            "older-zone": {"confirmed": 10}
                        }
                    },
                ),
                _snap(
                    "newer-table-retrieved-earlier",
                    "2026-05-21T12:00:00Z",
                    12,
                    published_at="2026-05-19T00:00:00Z",
                    normalized_content={
                        "affected_health_zones": {
                            "newer-zone": {"confirmed": 12}
                        }
                    },
                ),
            ),
        )

        snapshot = lovs_reconciler.reconcile(
            archive, outbreak_id="x", as_of="2026-05-22T23:59:59Z"
        )

        self.assertEqual(["newer-zone"], sorted(snapshot.zone_attributed_counts))
        row = snapshot.zone_attributed_counts["newer-zone"]
        self.assertEqual("newer-table-retrieved-earlier", row["source_id"])
        self.assertEqual("2026-05-19T00:00:00Z", row["source_published_at"])


if __name__ == "__main__":
    unittest.main()
