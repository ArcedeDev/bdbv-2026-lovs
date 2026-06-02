# SPDX-License-Identifier: Apache-2.0
"""Loader for INSP per-health-zone case/death series in INRB-UMIE build artifacts.

Reads the `build/long/insp_sitrep__cumulative_<metric>.csv` files (confirmed
and confirmed_deaths) and their `national_cumulative_<metric>.csv`
counterparts from an INRB-UMIE
GitHub-release tarball (or a pre-extracted directory of the same shape) and
returns a typed `INSPPerZoneSnapshot` in the LOVS source-zone vocabulary.

Two-stage alias resolution:

1. Upstream INRB-UMIE collapse: applies the tarball's own `data/aliases.csv`
   to fold raw spelling variants into INRB canonical Noms. This handles the
   real-world case where the INSP raw table carries both `Nyakunde` and
   `Nyankunde` on overlapping dates; the upstream alias declares them the
   same canonical zone.

2. LOVS bridge: maps INRB canonical Noms to LOVS canonical zone_ids via
   `lovs.zone_alias_bridge.ZoneAliasBridge`.

The ordering matters. Inverting these stages silently loses data on a raw
row whose spelling the LOVS bridge does not recognise. See Spike C in
`.process/2026-05-28-insp-per-zone-and-pcr-capacity-poc/validation.md`.

Reconciliation: for every metric, the loader emits an explicit
`unallocated_residual` field equal to `national_total - sum(zone_attributed)`.
This carries the partition gap forward honestly instead of silently
distributing residuals across zones. National and per-zone CSVs MUST come
from the same source (same tarball, same content hash); the loader raises
`ReconciliationSourceMismatchError` if asked to mix sources.

Stdlib only. No clock, no network. The loader is a pure function of its
inputs.
"""
from __future__ import annotations

import csv
import io
import pathlib
import tarfile
from dataclasses import dataclass, field
from datetime import date
from typing import IO, Iterable, Mapping

from lovs.zone_alias_bridge import ZoneAliasBridge, ZoneAliasBridgeError


METHOD_BASIS = "INRB_UMIE_INSP_per_zone_v1"

METRICS: tuple[str, ...] = (
    "confirmed",
    "confirmed_deaths",
)

# Mapping of LOVS-internal metric name to (per-zone CSV stem, national CSV stem)
# under `build/long/` in the INRB-UMIE release. Only the laboratory-confirmed
# cumulative metrics are loaded: the cumulative suspected tier was retired
# (2026-06-02 suspected-retirement). Suspected counts are now an operational
# point-prevalence axis, national-only, and never summed into confirmed.
_METRIC_FILES: Mapping[str, tuple[str, str]] = {
    "confirmed": (
        "insp_sitrep__cumulative_confirmed_cases.csv",
        "insp_sitrep__national_cumulative_confirmed_cases.csv",
    ),
    "confirmed_deaths": (
        "insp_sitrep__cumulative_confirmed_deaths.csv",
        "insp_sitrep__national_cumulative_confirmed_deaths.csv",
    ),
}

_PER_ZONE_DIR = pathlib.PurePosixPath("build/long")
_ALIASES_CSV_PATH = pathlib.PurePosixPath("data/aliases.csv")


# ---------------------------------------------------------------------------
# Response-state per-zone tables (2026-06-02 surfacing)
# ---------------------------------------------------------------------------
#
# The INRB-UMIE bundle carries per-health-zone response-operations series the
# case loader does not read: contacts under follow-up, contacts seen, patients
# in isolation/care, and hospital escapes. These mirror `_METRIC_FILES` (same
# `build/long/insp_sitrep__*.csv` headerless 3-col Nom,date,value layout parsed
# by the same `_read_long_csv` helper) but they are NOT cumulative attribution
# tables: they are operational point-in-time series with ND gaps, no national
# rollup, and no reconciliation residual. The national operational axis
# (suspected under investigation / in isolation) is owned by the suspected
# retirement and is consumed downstream, never recomputed here.
#
# READ SEMANTICS (load-bearing, differs from the case tables): the value for a
# zone is the latest NON-ND value on-or-before `as_of`, per zone, per metric.
# A zone whose series is entirely ND (or absent) at-or-before `as_of` is null,
# never zero and never backfilled. This is the ND-aware contract: absence is
# never shown as a measured value. Contrast `_parse_int`, which folds ND to 0
# for the cumulative attribution tables where the conservative reading is zero
# and the gap is carried in the disclosed residual.
RESPONSE_METRICS: tuple[str, ...] = (
    "contacts_under_follow_up",
    "contacts_seen",
    "patients_in_care",
    "hospital_escapes",
)

_RESPONSE_METRIC_FILES: Mapping[str, str] = {
    "contacts_under_follow_up": "insp_sitrep__cumulative_contacts_traced.csv",
    "contacts_seen": "insp_sitrep__contacts_seen.csv",
    "patients_in_care": "insp_sitrep__hospitalised.csv",
    "hospital_escapes": "insp_sitrep__hosp_escaped.csv",
}


# ---------------------------------------------------------------------------
# Source-zone promotion criterion (Plan A 2026-05-28, spec §8.1 v1.2)
# ---------------------------------------------------------------------------
#
# A LOVS zone is promoted to `corridor_watchlist.source_zones` for an INSP
# snapshot if AND ONLY IF:
#   1. It is `present_with_data` in the INSP per-zone tables at the requested
#      as_of (so it carries signal worth tracking), AND
#   2. At least ONE of the following holds:
#      a. cumulative_confirmed >= THRESHOLD_CONFIRMED_LOW (1 by default).
#         The comparison is >= so 1 is the floor (a zone with exactly 1
#         confirmed case promotes). Confirmed cases by health zone are the
#         spread signal: laboratory-confirmed cases are the only cumulative
#         epidemiological metric, so a zone carrying any confirmed case is the
#         descriptive transmission source the watchlist tracks (the cumulative
#         suspected tier was retired 2026-06-02).
#      b. cumulative_confirmed_deaths >= THRESHOLD_CONFIRMED_DEATHS (1).
#         Confirmed deaths are load-bearing even at low count because they
#         carry the trailing-attribution signal explicitly.
#      c. the zone is in `BORDER_INTL_TARGET_ZONES` (proximity to a calibration
#         target zone justifies inclusion even at sub-threshold cumulative
#         counts).
#
# Constants are explicit and named so the criterion is reproducible by any
# consumer reading the same data + constants. A regression test pins the
# constants and refuses unannounced changes.

THRESHOLD_CONFIRMED_LOW = 1
THRESHOLD_CONFIRMED_DEATHS = 1
# Zones whose proximity to an international calibration target (Uganda, Burundi)
# justifies inclusion at sub-threshold cumulative counts. Drawn from
# data/zones.json border-corridor watch entries.
BORDER_INTL_TARGET_ZONES: frozenset[str] = frozenset({
    "mahagi-cod",
    "aru",
    "rimba",
})


def is_source_zone_promotion_eligible(
    zone_metrics: "ZoneMetrics",
    present_in_insp_classification: str,
    *,
    lovs_zone_id: str | None = None,
) -> bool:
    """Deterministic pure-function source-zone promotion test (spec §8.1 v1.2).

    Inputs are the values that downstream consumers can read from the snapshot
    itself, so the criterion is reproducible without re-reading the INRB-UMIE
    raw bytes.

    `present_in_insp_classification` is one of
    {`present_with_data`, `present_but_zero`, `structurally_absent`}.
    Only `present_with_data` passes condition 1.

    Returns True iff condition 1 AND at least one of condition 2a/2b/2c hold.
    """
    if present_in_insp_classification != "present_with_data":
        return False
    if zone_metrics.confirmed >= THRESHOLD_CONFIRMED_LOW:
        return True
    if zone_metrics.confirmed_deaths >= THRESHOLD_CONFIRMED_DEATHS:
        return True
    if lovs_zone_id is not None and lovs_zone_id in BORDER_INTL_TARGET_ZONES:
        return True
    return False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class INSPLoaderError(ValueError):
    """Base class for INSP loader errors."""


class ReconciliationSourceMismatchError(INSPLoaderError):
    """Raised when national and per-zone CSVs are sourced from different bundles.

    Reconciliation arithmetic (national - sum(zone)) is only honest when both
    sides come from the same dated, archived bundle. Mixing bundles produces
    a residual that does not correspond to any real publication state.
    """


class INSPCSVSchemaError(INSPLoaderError):
    """Raised when an INSP CSV does not carry the expected columns or shape."""


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneMetrics:
    """Per-zone integer counts for the cumulative INSP metrics.

    Carries only the laboratory-confirmed cumulative metrics (confirmed and
    confirmed_deaths); the cumulative suspected tier was retired 2026-06-02.

    `inrb_collapsed_from` lists the INRB raw spelling variants that were
    folded into this zone via the upstream `aliases.csv` step (empty when
    the INRB canonical Nom equals the raw row).
    """

    confirmed: int
    confirmed_deaths: int
    inrb_collapsed_from: tuple[str, ...] = field(default_factory=tuple)

    def is_all_zero(self) -> bool:
        return self.confirmed == 0 and self.confirmed_deaths == 0

    def get(self, metric: str) -> int:
        if metric not in METRICS:
            raise KeyError(metric)
        return getattr(self, metric)


@dataclass(frozen=True)
class NationalMetrics:
    confirmed: int
    confirmed_deaths: int

    def get(self, metric: str) -> int:
        if metric not in METRICS:
            raise KeyError(metric)
        return getattr(self, metric)


@dataclass(frozen=True)
class CoverageAudit:
    """Three-state classification of how each LOVS source zone is represented in INSP.

    See Spike B in
    `.process/2026-05-28-insp-per-zone-and-pcr-capacity-poc/validation.md`.
    """

    present_with_data: tuple[str, ...]
    present_but_zero: tuple[str, ...]
    structurally_absent: tuple[str, ...]


@dataclass(frozen=True)
class INSPPerZoneSnapshot:
    """A LOVS-canonical view of an INRB-UMIE INSP per-zone snapshot at a date.

    `metric_presence` (Plan A 2026-05-28): per-LOVS-zone set of metrics in
    whose per-zone CSV the zone appears at `as_of`. Load-bearing signal for
    distinguishing `mixed_with_metric_floor` from `partial_per_zone` in the
    scale-resilience invariant (spec §6.7). For example Komanda at
    as_of 2026-05-26 appears in `insp_sitrep__cumulative_confirmed_deaths.csv`
    but not in the confirmed-cases CSV; its entry is therefore
    `{"confirmed_deaths"}` (not `set(METRICS)`).
    """

    as_of: date
    source_id: str
    by_lovs_zone: Mapping[str, ZoneMetrics]
    national: NationalMetrics
    unallocated_residual: Mapping[str, int]
    coverage_audit: CoverageAudit
    metric_presence: Mapping[str, frozenset[str]] = field(default_factory=dict)
    method_basis: str = METHOD_BASIS


@dataclass(frozen=True)
class ZoneResponseMetrics:
    """Per-zone response-operations counts (ND-aware nulls).

    Each field is the latest non-ND value on-or-before the snapshot `as_of` for
    that zone and metric, or `None` when the source reports ND (or nothing) for
    that zone at-or-before `as_of`. A `None` means "not reported", never zero:
    a real reported zero (e.g. Aru contacts-under-follow-up = 0) is `0`, an
    undeclared zone is `None`. Nothing is ever backfilled across the gap.

    `patients_in_care` is a care/isolation census, never a case count and never
    labelled "suspected" (hard rule, spec 2026-06-02).
    """

    contacts_under_follow_up: int | None
    contacts_seen: int | None
    patients_in_care: int | None
    hospital_escapes: int | None

    def get(self, metric: str) -> int | None:
        if metric not in RESPONSE_METRICS:
            raise KeyError(metric)
        return getattr(self, metric)


@dataclass(frozen=True)
class ResponseStateSnapshot:
    """A LOVS-canonical view of the INRB-UMIE per-zone response-operations tables.

    Distinct from `INSPPerZoneSnapshot`: there is no national rollup and no
    reconciliation residual on these tables (the national operational axis is
    the suspected-retirement's `operational_status` block, consumed downstream
    and never recomputed here). This snapshot carries ONLY the per-zone,
    ND-aware response figures.
    """

    as_of: date
    source_id: str
    by_lovs_zone: Mapping[str, ZoneResponseMetrics]
    method_basis: str = METHOD_BASIS


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


def _normalise_date(value: str) -> date:
    """Accept both ISO `YYYY-MM-DD` and DMY `DD/MM/YYYY` per Spike A."""
    s = (value or "").strip()
    if not s:
        raise INSPCSVSchemaError("empty date value")
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return date.fromisoformat(s)
        except ValueError as exc:
            raise INSPCSVSchemaError(f"invalid ISO date {value!r}: {exc}") from exc
    if len(s) == 10 and s[2] == "/" and s[5] == "/":
        try:
            return date(int(s[6:10]), int(s[3:5]), int(s[0:2]))
        except ValueError as exc:
            raise INSPCSVSchemaError(f"invalid DMY date {value!r}: {exc}") from exc
    raise INSPCSVSchemaError(f"unrecognised date format: {value!r}")


def _parse_int(value: str, *, context: str) -> int:
    s = (value or "").strip()
    # Empty cells (older builds) and the "ND" sentinel (non-determine, used by
    # builds from 2026-05-30 onward) both mean no determined count for this
    # zone/date. For a cumulative attribution table the conservative reading is
    # zero: the count is not fabricated into a zone and instead falls into the
    # disclosed unallocated residual.
    if s == "" or s.upper() == "ND":
        return 0
    try:
        return int(float(s))
    except ValueError as exc:
        raise INSPCSVSchemaError(f"non-integer value at {context}: {value!r}") from exc


def _read_csv_rows(stream: IO[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(stream)
    return list(reader)


def _read_long_csv(text: str, value_column: str = "value") -> list[dict[str, str]]:
    """Parse an INSP long CSV into rows keyed 'nom', 'date', value_column.

    Tolerates two upstream layouts, both positional (column 0 = nom,
    1 = date, 2 = value): the older headered form whose first row is
    'nom,date,<metric>' (builds through 2026-05-28) and the newer headerless
    form whose first row is already data (builds from 2026-05-30 onward). A
    leading UTF-8 BOM is stripped by _decode_text; the lstrip here is a
    belt-and-suspenders guard.
    """
    raw_rows = [r for r in csv.reader(io.StringIO(text)) if len(r) >= 3]
    if not raw_rows:
        return []
    c0 = raw_rows[0][0].strip().lstrip("﻿").lower()
    c1 = raw_rows[0][1].strip().lower()
    headered = c0 == "nom" and c1 == "date"
    data_rows = raw_rows[1:] if headered else raw_rows
    return [
        {
            "nom": r[0].strip().lstrip("﻿"),
            "date": r[1].strip(),
            value_column: r[2].strip(),
        }
        for r in data_rows
    ]


def _decode_text(raw: bytes) -> str:
    # INRB-UMIE CSVs are UTF-8; builds from 2026-05-30 onward prepend a BOM,
    # so decode with utf-8-sig to strip it (a no-op for the older no-BOM files).
    return raw.decode("utf-8-sig")


# ---------------------------------------------------------------------------
# Source-adapter: tarball or directory, same interface
# ---------------------------------------------------------------------------


class _Source:
    """Read a named entry as text from either a tarball or a directory."""

    def read_text(self, relpath: pathlib.PurePosixPath) -> str:
        raise NotImplementedError

    def has(self, relpath: pathlib.PurePosixPath) -> bool:
        raise NotImplementedError


class _TarballSource(_Source):
    def __init__(self, tarball_path: pathlib.Path) -> None:
        self._tarball_path = tarball_path
        self._members: dict[str, tarfile.TarInfo] = {}
        with tarfile.open(tarball_path, "r:*") as tar:
            for m in tar.getmembers():
                if m.isfile():
                    # tar paths may or may not start with './'
                    name = m.name[2:] if m.name.startswith("./") else m.name
                    self._members[name] = m

    def has(self, relpath: pathlib.PurePosixPath) -> bool:
        return str(relpath) in self._members

    def read_text(self, relpath: pathlib.PurePosixPath) -> str:
        key = str(relpath)
        if key not in self._members:
            raise INSPCSVSchemaError(f"tarball missing entry {key!r}")
        with tarfile.open(self._tarball_path, "r:*") as tar:
            member = tar.getmember(self._members[key].name)
            fh = tar.extractfile(member)
            if fh is None:
                raise INSPCSVSchemaError(f"tarball entry {key!r} is not a regular file")
            return _decode_text(fh.read())


class _DirectorySource(_Source):
    def __init__(self, root: pathlib.Path) -> None:
        self._root = root

    def has(self, relpath: pathlib.PurePosixPath) -> bool:
        return (self._root / relpath).exists()

    def read_text(self, relpath: pathlib.PurePosixPath) -> str:
        full = self._root / relpath
        if not full.exists():
            raise INSPCSVSchemaError(f"directory missing entry {relpath!s}")
        return full.read_text(encoding="utf-8-sig")


def _open_source(path: pathlib.Path) -> _Source:
    if not path.exists():
        raise INSPLoaderError(f"source path does not exist: {path}")
    if path.is_dir():
        return _DirectorySource(path)
    if path.is_file() and (path.suffix in (".gz", ".tgz") or path.suffixes[-2:] == [".tar", ".gz"]):
        return _TarballSource(path)
    if path.is_file() and path.suffix == ".tar":
        return _TarballSource(path)
    raise INSPLoaderError(
        f"source path is neither a directory nor a recognised tarball: {path}"
    )


# ---------------------------------------------------------------------------
# Upstream alias collapse (stage 1)
# ---------------------------------------------------------------------------


def _load_upstream_aliases(source: _Source) -> dict[str, str]:
    """Return `{observed_name: canonical_nom}` from the upstream `aliases.csv`.

    Returns an empty dict if the file is absent (older tarballs do not ship
    it). Raises INSPCSVSchemaError if the file is present but malformed.
    """
    if not source.has(_ALIASES_CSV_PATH):
        return {}
    text = source.read_text(_ALIASES_CSV_PATH)
    rows = _read_csv_rows(io.StringIO(text))
    out: dict[str, str] = {}
    for i, row in enumerate(rows, start=2):
        observed = (row.get("observed_name") or "").strip()
        canonical = (row.get("canonical_nom") or "").strip()
        if not observed or not canonical:
            raise INSPCSVSchemaError(
                f"upstream aliases.csv row {i} missing observed_name or canonical_nom"
            )
        if observed in out and out[observed] != canonical:
            raise INSPCSVSchemaError(
                f"upstream aliases.csv row {i}: {observed!r} maps to both "
                f"{out[observed]!r} and {canonical!r}"
            )
        out[observed] = canonical
    return out


def _resolve_inrb_canonical(raw_nom: str, upstream_aliases: Mapping[str, str]) -> str:
    """Apply upstream aliases to obtain the INRB canonical Nom for a raw row."""
    return upstream_aliases.get(raw_nom, raw_nom)


# ---------------------------------------------------------------------------
# CSV readers
# ---------------------------------------------------------------------------


def _read_per_zone_metric_at_date(
    source: _Source,
    relpath: pathlib.PurePosixPath,
    column: str,
    target_date: date,
    upstream_aliases: Mapping[str, str],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Return `{inrb_canonical: int}` and `{inrb_canonical: [raw_names_collapsed]}`."""
    rows = _read_long_csv(source.read_text(relpath), column)
    if not rows:
        return {}, {}
    out: dict[str, int] = {}
    collapsed_from: dict[str, list[str]] = {}
    matched_any_row = False
    for i, r in enumerate(rows, start=2):
        try:
            row_date = _normalise_date(r["date"])
        except INSPCSVSchemaError as exc:
            raise INSPCSVSchemaError(f"{relpath!s} row {i}: {exc}") from exc
        if row_date != target_date:
            continue
        matched_any_row = True
        raw_nom = (r["nom"] or "").strip()
        if not raw_nom:
            continue
        canonical = _resolve_inrb_canonical(raw_nom, upstream_aliases)
        value = _parse_int(r[column], context=f"{relpath!s} row {i} column {column!r}")
        out[canonical] = out.get(canonical, 0) + value
        if canonical != raw_nom:
            collapsed_from.setdefault(canonical, []).append(raw_nom)
    if not matched_any_row:
        # Symmetric with `_read_national_metric_at_date`: refuse rather than
        # silently produce an all-zero snapshot that would inflate the
        # unallocated_residual to 100% and look like a legitimate partition.
        raise INSPCSVSchemaError(
            f"{relpath!s}: no rows at date {target_date.isoformat()!r}; "
            f"verify the as_of is within the file's date range"
        )
    return out, collapsed_from


def _read_national_metric_at_date(
    source: _Source,
    relpath: pathlib.PurePosixPath,
    column: str,
    target_date: date,
) -> int:
    """Return the single national value for the metric at the requested date.

    The CSV repeats the same national value per row (one row per zone in the
    DRC shapefile, value duplicated). The loader asserts the values are
    consistent within the file and returns the (single) distinct value.
    """
    rows = _read_long_csv(source.read_text(relpath), column)
    if not rows:
        raise INSPCSVSchemaError(f"{relpath!s}: empty file")
    distinct: set[int] = set()
    for i, r in enumerate(rows, start=2):
        try:
            row_date = _normalise_date(r["date"])
        except INSPCSVSchemaError as exc:
            raise INSPCSVSchemaError(f"{relpath!s} row {i}: {exc}") from exc
        if row_date != target_date:
            continue
        value = _parse_int(r[column], context=f"{relpath!s} row {i} column {column!r}")
        distinct.add(value)
    if not distinct:
        raise INSPCSVSchemaError(
            f"{relpath!s}: no rows at date {target_date.isoformat()!r}"
        )
    if len(distinct) > 1:
        raise INSPCSVSchemaError(
            f"{relpath!s}: national rollup has multiple distinct values "
            f"{sorted(distinct)!r} at date {target_date.isoformat()!r}"
        )
    return distinct.pop()


def _read_response_metric_latest_nonnd(
    source: _Source,
    relpath: pathlib.PurePosixPath,
    column: str,
    as_of: date,
    upstream_aliases: Mapping[str, str],
) -> dict[str, int]:
    """Return `{inrb_canonical: int}` of latest NON-ND value on-or-before `as_of`.

    ND-aware: ND/empty cells are skipped (not folded to zero), so a zone whose
    only on-or-before-`as_of` rows are ND simply does not appear in the result
    and is reported as null by the caller. A zone is included only when it has
    at least one real (non-ND) value at-or-before `as_of`; its value is the one
    on the most recent such date. Upstream alias collapse (stage 1) folds raw
    spelling variants into the INRB canonical Nom before selection, so e.g. a
    `Nyankunde` row contributes to canonical `Nyakunde`.

    Unlike the cumulative case readers, a file with no rows at `as_of` is NOT
    an error here: these operational series legitimately stop a few days before
    the SitRep cutoff, and the latest-on-or-before rule carries the most recent
    reported state forward as the point-in-time figure (without fabricating a
    value where none was ever reported).
    """
    rows = _read_long_csv(source.read_text(relpath), column)
    best: dict[str, tuple[date, int]] = {}
    for i, r in enumerate(rows, start=2):
        try:
            row_date = _normalise_date(r["date"])
        except INSPCSVSchemaError as exc:
            raise INSPCSVSchemaError(f"{relpath!s} row {i}: {exc}") from exc
        if row_date > as_of:
            continue
        raw_nom = (r["nom"] or "").strip()
        if not raw_nom:
            continue
        cell = (r[column] or "").strip()
        if cell == "" or cell.upper() == "ND":
            # ND-aware: do not fold to zero and do not let a trailing ND row
            # mask an earlier real value; just skip this row.
            continue
        try:
            value = int(float(cell))
        except ValueError as exc:
            raise INSPCSVSchemaError(
                f"non-integer value at {relpath!s} row {i} column {column!r}: {cell!r}"
            ) from exc
        canonical = _resolve_inrb_canonical(raw_nom, upstream_aliases)
        prior = best.get(canonical)
        if prior is None or row_date > prior[0]:
            best[canonical] = (row_date, value)
    return {nom: value for nom, (_, value) in best.items()}


def _zones_present_in_per_zone_table_at_date(
    source: _Source,
    relpath: pathlib.PurePosixPath,
    upstream_aliases: Mapping[str, str],
    target_date: date,
) -> set[str]:
    """Set of INRB canonical Noms appearing in this per-zone CSV AT a specific date.

    Per Spike B in the validation note, the coverage audit's notion of
    "structurally present" is scoped to the requested as_of, not the full
    file history. A zone with rows on 14-May but not on 26-May would have
    been classified `present_but_zero` under a file-wide scan, masking a
    real attribution gap on the as_of date.
    """
    rows = _read_long_csv(source.read_text(relpath))
    out: set[str] = set()
    for r in rows:
        raw_nom = (r.get("nom") or "").strip()
        if not raw_nom:
            continue
        try:
            row_date = _normalise_date(r.get("date", ""))
        except INSPCSVSchemaError:
            continue
        if row_date != target_date:
            continue
        out.add(_resolve_inrb_canonical(raw_nom, upstream_aliases))
    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def load_per_zone_snapshot(
    tarball_or_dir: pathlib.Path,
    as_of: date,
    *,
    bridge: ZoneAliasBridge | None = None,
    source_id: str | None = None,
) -> INSPPerZoneSnapshot:
    """Load the INSP per-zone snapshot from an INRB-UMIE release artifact.

    Args:
        tarball_or_dir: path to either a `build-<tag>.tar.gz` release tarball
            or an already-extracted directory with the same `build/long/...`
            tree.
        as_of: the target date to snapshot at. Must match a date present in
            every metric's per-zone and national CSV.
        bridge: LOVS-to-INRB canonical bridge. Defaults to
            `ZoneAliasBridge.load_default()`.
        source_id: optional source-id tag to record in the snapshot (e.g.
            the manifest source_id for the artifact). If omitted, the
            tarball filename stem is used.

    Returns:
        An immutable `INSPPerZoneSnapshot`.

    Raises:
        INSPLoaderError, INSPCSVSchemaError, ReconciliationSourceMismatchError
        (subclasses of ValueError) on input problems.
        ZoneAliasBridgeError on bridge problems.
    """
    if bridge is None:
        bridge = ZoneAliasBridge.load_default()
    source = _open_source(tarball_or_dir)
    # Stage 1 (upstream INRB-UMIE collapse): merge the bridge's vendored
    # snapshot of `data/aliases.csv` with any aliases shipped inside the
    # artifact (source repo tarball ships it; GitHub-release tarball does
    # not). In-tarball aliases take precedence on key collisions; the
    # vendored copy is a backstop so a partial in-tarball file does not
    # silently lose LOVS-affecting collapse rules. Both directions:
    # in-tarball-only and vendored-only keys are preserved.
    upstream_aliases = dict(bridge.inrb_upstream_aliases())
    in_tarball_aliases = _load_upstream_aliases(source)
    upstream_aliases.update(in_tarball_aliases)

    if source_id is None:
        source_id = tarball_or_dir.name

    by_inrb_metric: dict[str, dict[str, int]] = {}
    collapsed_by_metric: dict[str, dict[str, list[str]]] = {}
    national_by_metric: dict[str, int] = {}

    for metric, (per_zone_file, national_file) in _METRIC_FILES.items():
        per_zone_rel = _PER_ZONE_DIR / per_zone_file
        national_rel = _PER_ZONE_DIR / national_file
        column = per_zone_file.replace("insp_sitrep__", "").replace(".csv", "")
        national_column = national_file.replace("insp_sitrep__", "").replace(".csv", "")
        per_zone, collapsed = _read_per_zone_metric_at_date(
            source, per_zone_rel, column, as_of, upstream_aliases
        )
        national = _read_national_metric_at_date(
            source, national_rel, national_column, as_of
        )
        by_inrb_metric[metric] = per_zone
        collapsed_by_metric[metric] = collapsed
        national_by_metric[metric] = national

    # Build per-LOVS-zone metrics from the per-INRB tables. Only LOVS zones
    # known to the bridge are projected; INRB zones outside the bridge fall
    # into the "unallocated_residual" implicitly (sum-of-zone is full zone-sum,
    # not bridge-filtered, per honest reconciliation).
    by_lovs_zone: dict[str, ZoneMetrics] = {}
    for lovs_id in bridge.all_lovs_ids():
        inrb_nom = bridge.inrb_for(lovs_id)
        if inrb_nom is None:  # pragma: no cover - defended by bridge construction
            continue
        zm = ZoneMetrics(
            confirmed=by_inrb_metric["confirmed"].get(inrb_nom, 0),
            confirmed_deaths=by_inrb_metric["confirmed_deaths"].get(inrb_nom, 0),
            inrb_collapsed_from=tuple(
                sorted(
                    set(
                        collapsed_by_metric["confirmed"].get(inrb_nom, [])
                        + collapsed_by_metric["confirmed_deaths"].get(inrb_nom, [])
                    )
                )
            ),
        )
        by_lovs_zone[lovs_id] = zm

    # Reconciliation residuals use the full INRB-side per-zone sum (not the
    # bridge-filtered sum), so the residual stays honest even when the
    # bridge does not cover every INRB zone.
    unallocated_residual: dict[str, int] = {}
    for metric in METRICS:
        national_total = national_by_metric[metric]
        zone_sum = sum(by_inrb_metric[metric].values())
        residual = national_total - zone_sum
        if residual < 0:
            raise ReconciliationSourceMismatchError(
                f"negative residual for metric {metric!r}: "
                f"national={national_total} - zone_sum={zone_sum} = {residual}. "
                f"This means national and per-zone CSVs cite incompatible totals; "
                f"verify they come from the same source bundle."
            )
        unallocated_residual[metric] = residual

    # Coverage audit (three-state).
    # Use the per-zone confirmed_cases table as the canonical "structurally
    # present" surface: any zone appearing in any of the cumulative metric
    # tables counts as "present_in_table".
    all_inrb_zones_in_tables: set[str] = set()
    per_metric_inrb_presence: dict[str, set[str]] = {}
    for metric, (per_zone_file, _) in _METRIC_FILES.items():
        metric_zones = _zones_present_in_per_zone_table_at_date(
            source, _PER_ZONE_DIR / per_zone_file, upstream_aliases, as_of
        )
        per_metric_inrb_presence[metric] = metric_zones
        all_inrb_zones_in_tables |= metric_zones

    # Plan A 2026-05-28: per-LOVS-zone metric presence (spec §6.7).
    metric_presence: dict[str, frozenset[str]] = {}
    for lovs_id in bridge.all_lovs_ids():
        inrb_nom = bridge.inrb_for(lovs_id)
        if inrb_nom is None:  # pragma: no cover - defended by bridge construction
            continue
        present_in = {
            metric
            for metric, zones in per_metric_inrb_presence.items()
            if inrb_nom in zones
        }
        metric_presence[lovs_id] = frozenset(present_in)

    present_with_data: list[str] = []
    present_but_zero: list[str] = []
    structurally_absent: list[str] = []
    for lovs_id in bridge.all_lovs_ids():
        inrb_nom = bridge.inrb_for(lovs_id)
        if inrb_nom is None or inrb_nom not in all_inrb_zones_in_tables:
            structurally_absent.append(lovs_id)
            continue
        zm = by_lovs_zone.get(lovs_id)
        if zm is None or zm.is_all_zero():
            present_but_zero.append(lovs_id)
            continue
        present_with_data.append(lovs_id)

    audit = CoverageAudit(
        present_with_data=tuple(sorted(present_with_data)),
        present_but_zero=tuple(sorted(present_but_zero)),
        structurally_absent=tuple(sorted(structurally_absent)),
    )

    national = NationalMetrics(
        confirmed=national_by_metric["confirmed"],
        confirmed_deaths=national_by_metric["confirmed_deaths"],
    )

    return INSPPerZoneSnapshot(
        as_of=as_of,
        source_id=source_id,
        by_lovs_zone=by_lovs_zone,
        national=national,
        unallocated_residual=dict(unallocated_residual),
        coverage_audit=audit,
        metric_presence=metric_presence,
    )


def load_response_state(
    tarball_or_dir: pathlib.Path,
    as_of: date,
    *,
    bridge: ZoneAliasBridge | None = None,
    source_id: str | None = None,
) -> ResponseStateSnapshot:
    """Load the per-zone response-operations snapshot from an INRB-UMIE artifact.

    Reads the four `build/long/insp_sitrep__*.csv` response tables enumerated in
    `_RESPONSE_METRIC_FILES` (contacts under follow-up, contacts seen, patients
    in care, hospital escapes) and projects them into the LOVS zone vocabulary.

    ND-aware: a zone with no real (non-ND) value at-or-before `as_of` for a
    metric is `None` for that metric, never zero, never backfilled. A LOVS zone
    in the bridge but absent from a table is likewise `None`. This is the
    surfacing counterpart to `load_per_zone_snapshot`; it shares the same source
    adapter, the same upstream alias collapse, and the same `_read_long_csv`
    primitive, but it carries no national rollup or residual (the national
    operational axis is owned by the suspected retirement and consumed
    downstream, never recomputed here).

    Args mirror `load_per_zone_snapshot`. Raises the same error classes on
    malformed input.
    """
    if bridge is None:
        bridge = ZoneAliasBridge.load_default()
    source = _open_source(tarball_or_dir)
    upstream_aliases = dict(bridge.inrb_upstream_aliases())
    in_tarball_aliases = _load_upstream_aliases(source)
    upstream_aliases.update(in_tarball_aliases)

    if source_id is None:
        source_id = tarball_or_dir.name

    by_inrb_metric: dict[str, dict[str, int]] = {}
    for metric, per_zone_file in _RESPONSE_METRIC_FILES.items():
        per_zone_rel = _PER_ZONE_DIR / per_zone_file
        column = per_zone_file.replace("insp_sitrep__", "").replace(".csv", "")
        by_inrb_metric[metric] = _read_response_metric_latest_nonnd(
            source, per_zone_rel, column, as_of, upstream_aliases
        )

    by_lovs_zone: dict[str, ZoneResponseMetrics] = {}
    for lovs_id in bridge.all_lovs_ids():
        inrb_nom = bridge.inrb_for(lovs_id)
        if inrb_nom is None:  # pragma: no cover - defended by bridge construction
            continue
        zrm = ZoneResponseMetrics(
            contacts_under_follow_up=by_inrb_metric["contacts_under_follow_up"].get(
                inrb_nom
            ),
            contacts_seen=by_inrb_metric["contacts_seen"].get(inrb_nom),
            patients_in_care=by_inrb_metric["patients_in_care"].get(inrb_nom),
            hospital_escapes=by_inrb_metric["hospital_escapes"].get(inrb_nom),
        )
        by_lovs_zone[lovs_id] = zrm

    return ResponseStateSnapshot(
        as_of=as_of,
        source_id=source_id,
        by_lovs_zone=by_lovs_zone,
    )
