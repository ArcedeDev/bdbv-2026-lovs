# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.insp_per_zone_loader.

Anchored to the published e40bc9e release artifact when the local tarball
fixture is available; otherwise the same arithmetic is exercised against an
inline CSV fixture that mirrors the schema and the post-alias numerics from
the validation probe.
"""
from __future__ import annotations

import io
import pathlib
import tarfile
from datetime import date
from unittest import mock

import pytest

from lovs.insp_per_zone_loader import (
    METHOD_BASIS,
    METRICS,
    CoverageAudit,
    INSPCSVSchemaError,
    INSPLoaderError,
    INSPPerZoneSnapshot,
    NationalMetrics,
    ReconciliationSourceMismatchError,
    ZoneMetrics,
    _normalise_date,
    load_per_zone_snapshot,
)
from lovs.zone_alias_bridge import ZoneAliasBridge


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCAL_E40BC9E_TARBALL = pathlib.Path("/tmp/inrb-e40bc9e/build.tar.gz")


# ---------------------------------------------------------------------------
# Inline fixture: small directory that mirrors the INRB-UMIE schema at as_of=2026-05-26
# ---------------------------------------------------------------------------


def _write_per_zone(dir_path: pathlib.Path, stem: str, metric: str, rows: list[tuple[str, str, int]]) -> None:
    path = dir_path / "build" / "long" / f"{stem}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "nom,date," + metric + "\n"
    for nom, dt, value in rows:
        body += f"{nom},{dt},{value}\n"
    path.write_text(body)


def _write_national(dir_path: pathlib.Path, stem: str, metric: str, value: int, *, as_of: str = "2026-05-26") -> None:
    path = dir_path / "build" / "long" / f"{stem}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "nom,date," + metric + "\n"
    # Mimic the 519-row replication pattern with a small placeholder
    for nom in ("ZoneA", "ZoneB", "Bunia", "Mongbalu"):
        body += f"{nom},{as_of},{value}\n"
    path.write_text(body)


@pytest.fixture
def tiny_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """Directory with INRB-UMIE-shaped CSVs and aliases.csv for 26-May-2026.

    Post 2026-06-02 suspected-retirement: only the laboratory-confirmed
    cumulative metrics are loaded.

    Numerics:
      - confirmed:  Bunia 36, Mongbalu 20, Rwampara 33, Nyakunde 10, Nyankunde 0 (alias-collapsed)
                    => zone_sum 99, national 109, residual 10  (we use small-but-realistic numbers)
      - confirmed_deaths: Bunia 2, Rwampara 2  => zone_sum 4, national 16, residual 12
    """
    d = tmp_path / "fixture"
    _write_per_zone(
        d,
        "insp_sitrep__cumulative_confirmed_cases",
        "cumulative_confirmed_cases",
        [
            ("Bunia", "26/05/2026", 36),
            ("Mongbalu", "26/05/2026", 20),
            ("Rwampara", "26/05/2026", 33),
            ("Nyakunde", "26/05/2026", 10),
            ("Nyankunde", "26/05/2026", 0),
            ("Katwa", "26/05/2026", 0),
            ("ExtraInrbZone", "26/05/2026", 10),  # outside the LOVS bridge
        ],
    )
    # Use ISO dates in this metric to exercise the mixed-format branch
    _write_per_zone(
        d,
        "insp_sitrep__cumulative_confirmed_deaths",
        "cumulative_confirmed_deaths",
        [
            ("Bunia", "2026-05-26", 2),
            ("Rwampara", "2026-05-26", 2),
            ("Katwa", "2026-05-26", 0),
        ],
    )
    # National rollups (single distinct value per file, replicated)
    _write_national(d, "insp_sitrep__national_cumulative_confirmed_cases", "national_cumulative_confirmed_cases", 109)
    _write_national(d, "insp_sitrep__national_cumulative_confirmed_deaths", "national_cumulative_confirmed_deaths", 16, as_of="26/05/2026")
    # Upstream aliases.csv
    (d / "data").mkdir(parents=True, exist_ok=True)
    (d / "data" / "aliases.csv").write_text(
        "observed_name,canonical_nom,source_dataset,notes\n"
        "Nyankunde,Nyakunde,flowminder,Spelling variant noted in flowminder/README.md\n"
        "Mongbwalu,Mongbalu,flowminder,Spelling variant\n"
    )
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNormaliseDate:
    def test_iso(self) -> None:
        assert _normalise_date("2026-05-26") == date(2026, 5, 26)

    def test_dmy(self) -> None:
        assert _normalise_date("26/05/2026") == date(2026, 5, 26)

    def test_empty_raises(self) -> None:
        with pytest.raises(INSPCSVSchemaError, match="empty"):
            _normalise_date("")

    def test_garbage_raises(self) -> None:
        with pytest.raises(INSPCSVSchemaError):
            _normalise_date("yesterday")

    def test_invalid_dmy_raises(self) -> None:
        with pytest.raises(INSPCSVSchemaError, match="invalid DMY"):
            _normalise_date("32/13/2026")

    def test_invalid_iso_raises(self) -> None:
        with pytest.raises(INSPCSVSchemaError, match="invalid ISO"):
            _normalise_date("2026-13-32")


class TestLoadFromInlineFixture:
    def test_returns_snapshot_with_expected_method_basis(self, tiny_fixture: pathlib.Path) -> None:
        snap = load_per_zone_snapshot(tiny_fixture, date(2026, 5, 26))
        assert isinstance(snap, INSPPerZoneSnapshot)
        assert snap.method_basis == METHOD_BASIS
        assert snap.as_of == date(2026, 5, 26)

    def test_alias_collapse_combines_nyakunde_and_nyankunde(
        self, tiny_fixture: pathlib.Path
    ) -> None:
        snap = load_per_zone_snapshot(tiny_fixture, date(2026, 5, 26))
        nyak = snap.by_lovs_zone["nyankunde"]
        # 10 confirmed (from Nyakunde) + 0 (from Nyankunde) = 10
        assert nyak.confirmed == 10
        # collapsed_from records the raw INRB names that were folded in
        assert "Nyankunde" in nyak.inrb_collapsed_from

    def test_national_rollups_match_fixture(self, tiny_fixture: pathlib.Path) -> None:
        snap = load_per_zone_snapshot(tiny_fixture, date(2026, 5, 26))
        assert snap.national == NationalMetrics(
            confirmed=109, confirmed_deaths=16
        )

    def test_unallocated_residual_arithmetic(self, tiny_fixture: pathlib.Path) -> None:
        snap = load_per_zone_snapshot(tiny_fixture, date(2026, 5, 26))
        # zone_sum INCLUDES ExtraInrbZone (10 confirmed), since the residual is
        # national_total - full_inrb_zone_sum to stay honest.
        # confirmed: 109 - (36+20+33+10+0+0+10) = 109 - 109 = 0
        assert snap.unallocated_residual["confirmed"] == 0
        # confirmed_deaths: 16 - (2+2+0) = 12
        assert snap.unallocated_residual["confirmed_deaths"] == 12

    def test_coverage_audit_three_state(self, tiny_fixture: pathlib.Path) -> None:
        snap = load_per_zone_snapshot(tiny_fixture, date(2026, 5, 26))
        audit = snap.coverage_audit
        assert isinstance(audit, CoverageAudit)
        # Bunia, Mongbwalu, Rwampara, Nyankunde have non-zero values -> present_with_data
        assert "bunia" in audit.present_with_data
        assert "mongbwalu" in audit.present_with_data
        assert "rwampara" in audit.present_with_data
        assert "nyankunde" in audit.present_with_data
        # Katwa is in INSP at zero -> present_but_zero
        assert "katwa" in audit.present_but_zero
        # The fixture omits bambu/butembo/goma-cod/kilo/miti-murhesa/nizi from
        # CSV altogether -> structurally_absent
        for z in ("bambu", "butembo", "goma-cod", "kilo", "miti-murhesa", "nizi"):
            assert z in audit.structurally_absent

    def test_loader_default_source_id_is_dir_name(self, tiny_fixture: pathlib.Path) -> None:
        snap = load_per_zone_snapshot(tiny_fixture, date(2026, 5, 26))
        assert snap.source_id == tiny_fixture.name

    def test_explicit_source_id_is_recorded(self, tiny_fixture: pathlib.Path) -> None:
        snap = load_per_zone_snapshot(
            tiny_fixture, date(2026, 5, 26), source_id="custom-id"
        )
        assert snap.source_id == "custom-id"


class TestReconciliationSourceMismatch:
    def test_negative_residual_raises(self, tmp_path: pathlib.Path) -> None:
        d = tmp_path / "f"
        # zone_sum=100, national=50 -> residual=-50
        _write_per_zone(
            d,
            "insp_sitrep__cumulative_confirmed_cases",
            "cumulative_confirmed_cases",
            [("Bunia", "26/05/2026", 100)],
        )
        _write_national(
            d,
            "insp_sitrep__national_cumulative_confirmed_cases",
            "national_cumulative_confirmed_cases",
            50,
        )
        # Other cumulative metric with sane values so it doesn't trip first
        for metric in ("confirmed_deaths",):
            _write_per_zone(
                d,
                f"insp_sitrep__cumulative_{metric}",
                f"cumulative_{metric}",
                [("Bunia", "26/05/2026", 1)],
            )
            _write_national(
                d,
                f"insp_sitrep__national_cumulative_{metric}",
                f"national_cumulative_{metric}",
                1,
            )
        with pytest.raises(ReconciliationSourceMismatchError, match="negative residual"):
            load_per_zone_snapshot(d, date(2026, 5, 26))


class TestTarballRoundTrip:
    def test_loader_reads_synthetic_tarball(self, tiny_fixture: pathlib.Path, tmp_path: pathlib.Path) -> None:
        tarball = tmp_path / "build-fixture.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            for path in tiny_fixture.rglob("*"):
                if path.is_file():
                    tar.add(path, arcname=str(path.relative_to(tiny_fixture)))
        snap = load_per_zone_snapshot(tarball, date(2026, 5, 26))
        assert snap.national.confirmed == 109


class TestRoundTwoLoaderFixes:
    """Tests covering review-round-two fixes in the loader.

    Each test pins a behaviour the round-1 reviewer flagged that was
    addressed before the round-2 verdict.
    """

    def test_zero_row_per_zone_match_raises(self, tmp_path: pathlib.Path) -> None:
        """If the requested as_of has no per-zone rows, the loader must
        refuse rather than silently produce an all-zero snapshot with the
        full national in unallocated_residual."""
        d = tmp_path / "f"
        for metric in ("confirmed_cases", "confirmed_deaths"):
            _write_per_zone(
                d,
                f"insp_sitrep__cumulative_{metric}",
                f"cumulative_{metric}",
                [("Bunia", "26/05/2026", 5)],
            )
            _write_national(
                d,
                f"insp_sitrep__national_cumulative_{metric}",
                f"national_cumulative_{metric}",
                10,
            )
        with pytest.raises(INSPCSVSchemaError, match="no rows at date"):
            # 27-May has no per-zone rows in this fixture
            load_per_zone_snapshot(d, date(2026, 5, 27))

    def test_partial_in_tarball_aliases_merged_with_vendored(
        self, tmp_path: pathlib.Path
    ) -> None:
        """If the artifact ships a partial aliases.csv that omits a
        LOVS-affecting collapse, the vendored backstop must still apply."""
        d = tmp_path / "f"
        # Build a tarball where aliases.csv only declares Mongbwalu->Mongbalu
        # but the per-zone data also contains Nyankunde rows (which should
        # collapse to Nyakunde via the vendored backstop).
        _write_per_zone(
            d,
            "insp_sitrep__cumulative_confirmed_cases",
            "cumulative_confirmed_cases",
            [("Nyakunde", "26/05/2026", 10), ("Nyankunde", "26/05/2026", 0)],
        )
        _write_per_zone(
            d,
            "insp_sitrep__cumulative_confirmed_deaths",
            "cumulative_confirmed_deaths",
            [("Nyakunde", "26/05/2026", 0)],
        )
        # National rollups that match the post-collapse zone sums
        _write_national(d, "insp_sitrep__national_cumulative_confirmed_cases", "national_cumulative_confirmed_cases", 10)
        _write_national(d, "insp_sitrep__national_cumulative_confirmed_deaths", "national_cumulative_confirmed_deaths", 0)
        # Partial in-tarball aliases.csv (omits Nyankunde, declares only Mongbwalu)
        (d / "data").mkdir(parents=True, exist_ok=True)
        (d / "data" / "aliases.csv").write_text(
            "observed_name,canonical_nom,source_dataset,notes\n"
            "Mongbwalu,Mongbalu,flowminder,Spelling variant\n"
        )
        snap = load_per_zone_snapshot(d, date(2026, 5, 26))
        nyak = snap.by_lovs_zone["nyankunde"]
        # Vendored Nyankunde->Nyakunde backstop must still apply
        assert nyak.confirmed == 10, "vendored alias backstop missing"
        assert "Nyankunde" in nyak.inrb_collapsed_from

    def test_coverage_audit_date_scoped(self, tmp_path: pathlib.Path) -> None:
        """A zone present on 14-May but absent on 26-May must classify as
        `structurally_absent` at as_of=2026-05-26, not `present_but_zero`."""
        d = tmp_path / "f"
        # bunia is in EVERY date; bambu is ONLY in 14-May, absent on 26-May
        _write_per_zone(
            d,
            "insp_sitrep__cumulative_confirmed_cases",
            "cumulative_confirmed_cases",
            [
                ("Bunia", "14/05/2026", 5),
                ("Bunia", "26/05/2026", 36),
                ("Bambu", "14/05/2026", 1),
                # No bambu row at 26/05
            ],
        )
        for metric in ("confirmed_deaths",):
            _write_per_zone(
                d,
                f"insp_sitrep__cumulative_{metric}",
                f"cumulative_{metric}",
                [("Bunia", "26/05/2026", 1)],
            )
            _write_national(
                d,
                f"insp_sitrep__national_cumulative_{metric}",
                f"national_cumulative_{metric}",
                1,
            )
        _write_national(d, "insp_sitrep__national_cumulative_confirmed_cases", "national_cumulative_confirmed_cases", 36)
        snap = load_per_zone_snapshot(d, date(2026, 5, 26))
        # bambu was in INSP at 14-May but NOT at 26-May -> structurally_absent
        assert "bambu" in snap.coverage_audit.structurally_absent
        assert "bambu" not in snap.coverage_audit.present_but_zero


class TestForbiddenPathsNotRead:
    def test_loader_does_not_open_calibration_ledger_or_live_output(
        self, tiny_fixture: pathlib.Path
    ) -> None:
        """The POC must never touch the forward-only ledger or live output."""
        forbidden = ("calibration-ledger.json", "live-bdbv-2026-output.json")
        opened_paths: list[str] = []

        original_open = pathlib.Path.open

        def tracking_open(self: pathlib.Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened_paths.append(str(self))
            return original_open(self, *args, **kwargs)

        original_read_text = pathlib.Path.read_text

        def tracking_read_text(self: pathlib.Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened_paths.append(str(self))
            return original_read_text(self, *args, **kwargs)

        with mock.patch.object(pathlib.Path, "open", tracking_open), mock.patch.object(
            pathlib.Path, "read_text", tracking_read_text
        ):
            load_per_zone_snapshot(tiny_fixture, date(2026, 5, 26))

        for p in opened_paths:
            for marker in forbidden:
                assert marker not in p, f"loader opened forbidden path {p!r}"


class TestPathErrors:
    def test_missing_path_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(INSPLoaderError, match="does not exist"):
            load_per_zone_snapshot(tmp_path / "nope", date(2026, 5, 26))

    def test_unsupported_file_type_raises(self, tmp_path: pathlib.Path) -> None:
        bogus = tmp_path / "thing.txt"
        bogus.write_text("hello")
        with pytest.raises(INSPLoaderError, match="neither"):
            load_per_zone_snapshot(bogus, date(2026, 5, 26))


@pytest.mark.skipif(
    not LOCAL_E40BC9E_TARBALL.exists(),
    reason="canonical e40bc9e tarball not locally cached at /tmp/inrb-e40bc9e/build.tar.gz",
)
class TestAgainstRealE40BC9ETarball:
    """Anchor tests against the real INRB-UMIE artifact when available locally.

    These tests skip in CI environments that do not have the tarball cached.
    The asserted numerics are the ones documented in
    `.process/2026-05-28-insp-per-zone-and-pcr-capacity-poc/validation.md`.
    """

    def test_national_totals_at_26may(self) -> None:
        snap = load_per_zone_snapshot(LOCAL_E40BC9E_TARBALL, date(2026, 5, 26))
        assert snap.national == NationalMetrics(
            confirmed=121,
            confirmed_deaths=17,
        )

    def test_unallocated_residuals_at_26may(self) -> None:
        snap = load_per_zone_snapshot(LOCAL_E40BC9E_TARBALL, date(2026, 5, 26))
        assert snap.unallocated_residual["confirmed"] == 10
        assert snap.unallocated_residual["confirmed_deaths"] == 12

    def test_pre_plan_a_lovs_eleven_source_zones_all_present_with_data(self) -> None:
        snap = load_per_zone_snapshot(LOCAL_E40BC9E_TARBALL, date(2026, 5, 26))
        # Every LOVS source zone is structurally covered in INSP; only Katwa
        # is at zero (the others are non-zero). Plan A 2026-05-28 expanded
        # the bridge to 18 zones, so we now subset-check the pre-Plan-A 10
        # rather than asserting equality on the full set.
        assert set(snap.coverage_audit.structurally_absent) == set()
        assert "katwa" in snap.coverage_audit.present_but_zero
        pre_plan_a_with_data = {
            "bambu", "bunia", "butembo", "goma-cod", "kilo",
            "miti-murhesa", "mongbwalu", "nizi", "nyankunde", "rwampara",
        }
        assert pre_plan_a_with_data.issubset(
            set(snap.coverage_audit.present_with_data)
        )

    def test_nyankunde_collapse_records_origin(self) -> None:
        snap = load_per_zone_snapshot(LOCAL_E40BC9E_TARBALL, date(2026, 5, 26))
        zm = snap.by_lovs_zone["nyankunde"]
        # 10 confirmed (Nyakunde row) + 0 (Nyankunde row) = 10
        assert zm.confirmed == 10
        assert "Nyankunde" in zm.inrb_collapsed_from
