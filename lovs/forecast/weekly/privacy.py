# SPDX-License-Identifier: Apache-2.0
"""privacy_gate: the closed vocabulary every public, archived or rendered value must pass.

Admitted: numbers, booleans and null; ids, codes and hashes from fixed patterns; ISO dates
(and UTC times) of data, publication, registration and build days; province and zone
names from the frozen lists, only under a place key. Rejected: any other string (free
text), a string naming more than one place (a place sequence), a place name outside a
place key, a date outside the calendar (a per-person date), a key outside the
vocabulary, and an archived value that differs from its source at the same path.
Findings name the path and the reason, never the value, so a rejected string is never
echoed into a log.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from lovs.forecast.weekly import promotion_rows, spec

PLACE_KEYS = frozenset({"province", "zone"})
_PLACES = sorted(set(spec.PROVINCE_NAMES) | set(spec.ZONE_NAMES), key=len, reverse=True)
_PLACE_RE = re.compile("|".join(re.escape(p) for p in _PLACES), re.IGNORECASE)

_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
_PATTERNS = (
    re.compile(r"^" + re.escape(spec.ID_PREFIX) + r"\d{8}-q?\d{3}$"),      # weekly ids
    re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$"),          # commits, sha256
    re.compile(r"^\d{3}$"),                                                  # SitRep numbers
    re.compile(r"^withheld:[a-z_]+$"),                                       # basis flag codes
    re.compile(r"^bdbv-weekly-\d{4}-\d{2}-\d{2}$"),                          # branch names
    re.compile(r"^data/weekly-forecasts/[0-9a-z/_.-]+$"),                    # store paths
)
CODES = frozenset(
    {s.id for s in spec.SERIES} | set(spec.METHOD_IDS) | set(spec.PIN_STATUSES) | set(spec.TARGET_STATUSES)
    | set(spec.VALUE_KINDS) | set(spec.PHASES) | set(spec.SKIP_CAUSES) | set(spec.ANOMALY_CODES)
    | set(spec.SKIP_SERIES_CODES) | set(spec.ARCHIVE_SOURCES) | set(spec.PROVINCE_TABLE_FIELDS)
    | set(spec.NATIONAL_TABLE_FIELDS) | {"flow", "endlevel", "mean", "lag", "at_or_below", "active",
                                        "record", "challenger", "green", "amber", "red", "none",
                                        spec.FLAG_ALERTS_PROVINCE_ABSENT, spec.FLAG_LAB_DENOMINATOR_WITHHELD,
                                        spec.SPEC_VERSION, spec.OUTBREAK, spec.REPOSITORY,
                                        spec.EXTRACT_SOURCE_PATH, "sha256", "missed"}
    | {m.split(".", 1)[1] for m in spec.METHOD_IDS}
)
KEYS = frozenset(
    set(spec.VINTAGE_FIELDS) | set(spec.PROVINCE_SPLIT_FIELDS) | set(spec.ZONE_FIELDS)
    | {k for v in spec.PROVINCE_TABLE_FIELDS.values() for k in v}
    | {k for v in spec.NATIONAL_TABLE_FIELDS.values() for k in v}
    | {spec.price_column(m) for m in spec.METHOD_IDS}
    | {spec.score_column(p, m) for p in ("brier", "log") for m in spec.METHOD_IDS}
    | {"sitrep", "data_as_of", "published_at", "province", "zone", "table", "province_split", "flags", "source",
       "pin_id", "forecast_id", "registration_day", "series", "horizon_days", "target_start", "target_end",
       "target_kind", "threshold", "shape", "status", "value_kind", "realised", "outcome", "anomaly",
       "deciding_commit", "decided_at", "spec_version", "spec_sha256", "deadline", "window_opens", "cutoff",
       "generator", "repository", "commit", "fetched", "lovs", "mcp", "fetched_at", "built_at", "inputs",
       "extract", "path", "sha256", "promotions", "packets", "files", "counts", "pins", "forecasts",
       "series_active", "series_skipped", "skipped_series", "reason", "n_paths", "forecasts_sha256",
       "forecasts_bytes", "forecast_count", "pin_count", "private_file", "branch", "branch_created_at",
       "pinning_commit", "on_time", "void", "receipt_recorded_at", "phase", "cause", "updated_at",
       "vintage_rows", "zone_records", "covariate_records", "dropped_rungs", "archives", "outbreak",
       "arrivals", "first_seen", "measure", "value", "level", "rows", "slots", "week", "label"}
)


@dataclass
class PrivacyContext:
    data_days: set[str] = field(default_factory=set)
    publication_days: set[str] = field(default_factory=set)
    registration_days: set[str] = field(default_factory=set)
    build_days: set[str] = field(default_factory=set)
    extra_keys: frozenset[str] = frozenset()

    def allows_day(self, day: str) -> bool:
        return (day in self.data_days or day in self.publication_days or day in self.registration_days
                or day in self.build_days)


class PrivacyError(RuntimeError):
    def __init__(self, findings: list[tuple[str, str]]) -> None:
        self.findings = findings
        shown = "; ".join(f"{p}: {r}" for p, r in findings[:25])
        super().__init__(f"privacy gate: {len(findings)} finding(s): {shown}")


def _places_in(text: str) -> int:
    return len(_PLACE_RE.findall(text))


def _check_string(value: str, key: str | None, path: str, ctx: PrivacyContext, out: list) -> None:
    if key in PLACE_KEYS:
        if value in spec.PROVINCE_NAMES or value in spec.ZONE_NAMES:
            return
        out.append((path, "place_sequence" if _places_in(value) >= 2 else "unknown_place"))
        return
    m = _DATE.match(value) or _TIME.match(value)
    if m:
        try:
            dt.date.fromisoformat(m.group(1))
        except ValueError:
            out.append((path, "free_text"))
            return
        if not ctx.allows_day(m.group(1)):
            out.append((path, "date_outside_calendar"))
        return
    if value in CODES or any(p.match(value) for p in _PATTERNS):
        return
    if value in spec.PROVINCE_NAMES or value in spec.ZONE_NAMES:
        out.append((path, "place_outside_row_key"))
        return
    if _places_in(value) >= 2:
        out.append((path, "place_sequence"))
        return
    out.append((path, "free_text"))


def _walk(obj, key: str | None, path: str, ctx: PrivacyContext, out: list, parent: str | None) -> None:
    if obj is None or isinstance(obj, bool) or isinstance(obj, (int, float)):
        return
    if isinstance(obj, str):
        _check_string(obj, key, path or "$", ctx, out)
        return
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if not isinstance(k, str):
                out.append((here, "key_outside_vocabulary"))
            elif parent == "province_split" and k in spec.PROVINCE_NAMES:
                pass                                         # a province as a row key
            elif parent == "files" and _PATTERNS[-1].match(f"data/weekly-forecasts/{k}"):
                pass                                         # a store file name as a key
            elif parent in ("counts", "series_skipped", "skipped_series", "dropped_rungs") and (
                    k in CODES or k in KEYS):
                pass
            elif k not in KEYS and k not in ctx.extra_keys and k not in CODES:
                out.append((here, "key_outside_vocabulary"))
            _walk(v, k, here, ctx, out, k)
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _walk(v, key, f"{path}[{i}]", ctx, out, parent)
        return
    out.append((path, "unsupported_type"))


def findings(obj, ctx: PrivacyContext) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    _walk(obj, None, "", ctx, out, None)
    return out


def gate(obj, ctx: PrivacyContext) -> None:
    """Raise PrivacyError unless every key and value of `obj` is in the closed vocabulary."""
    found = findings(obj, ctx)
    if found:
        raise PrivacyError(found)


privacy_gate = gate


def csv_rows(text: str) -> list[dict]:
    """CSV cells as JSON-like values: empty is null, numbers are numbers, the rest strings."""
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        out = {}
        for k, v in row.items():
            if v == "":
                out[k] = None
                continue
            try:
                out[k] = float(v) if any(c in v for c in ".eE") else int(v)
            except ValueError:
                out[k] = v
        rows.append(out)
    return rows


def check_archive_values(records: Iterable[Mapping], promotions: Sequence[Mapping],
                         extract_rows: Sequence[Mapping]) -> None:
    """Every archived value equals its source's value at the same path, or the gate fails."""
    promos = {str(p["sitrep_number"]).zfill(3): p for p in promotions}
    extract = {str(r.get("sitrep")).zfill(3): r for r in extract_rows}
    out: list[tuple[str, str]] = []
    for i, rec in enumerate(records):
        sitrep = str(rec.get("sitrep"))
        where = f"record[{i}]"
        if "zone" in rec or "table" in rec:
            promo = promos.get(sitrep)
            fields = spec.ZONE_FIELDS if "zone" in rec else (
                spec.PROVINCE_TABLE_FIELDS.get(rec.get("table")) or spec.NATIONAL_TABLE_FIELDS.get(rec.get("table")))
            if promo is None or fields is None:
                out.append((where, "no_source"))
                continue
            for k in fields:
                if rec.get(k) != promotion_rows.source_value(promo, rec, k):
                    out.append((f"{where}.{k}", "differs_from_source"))
            continue
        src = rec.get("source")
        if src == "promotion" and sitrep in promos:
            expected = promotion_rows.row_from_promotion(promos[sitrep])
        elif src == "extract" and sitrep in extract:
            expected = promotion_rows.row_from_extract(extract[sitrep])
            expected["flags"] = rec.get("flags", [])   # codes derived from the promotion, checked below
            if sitrep in promos and rec.get("flags") != promotion_rows.basis_flags(promos[sitrep]):
                out.append((f"{where}.flags", "differs_from_source"))
            elif sitrep not in promos and rec.get("flags"):
                out.append((f"{where}.flags", "differs_from_source"))
        else:
            out.append((where, "no_source"))
            continue
        for k in (*spec.VINTAGE_FIELDS, "data_as_of", "published_at", "province_split", "flags"):
            if rec.get(k) != expected.get(k):
                out.append((f"{where}.{k}", "differs_from_source"))
    if out:
        raise PrivacyError(out)


def context_for(rows: Sequence[Mapping], promotions: Sequence[Mapping], registration_days: Iterable[dt.date],
                build_days: Iterable[str]) -> PrivacyContext:
    data = {str(r["data_as_of"])[:10] for r in rows} | {str(p.get("data_as_of"))[:10] for p in promotions}
    pub = ({str(r["published_at"])[:10] for r in rows if r.get("published_at")}
           | {str(p.get("published_at"))[:10] for p in promotions if p.get("published_at")})
    return PrivacyContext(data_days=data, publication_days=pub,
                          registration_days={d.isoformat() for d in registration_days},
                          build_days={str(b)[:10] for b in build_days})
