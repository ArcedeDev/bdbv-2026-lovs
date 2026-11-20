# SPDX-License-Identifier: Apache-2.0
"""The calendar of spec v1: registration day, build clock, cutoff, target weeks, values.

R is a Wednesday (UTC). Week k holds data days R+7(k-1)+1 to R+7k, Thursday to Wednesday.
A flow is cum(R+7k) minus cum(R+7(k-1)), read on exactly those Wednesdays, no fallback.
"""
from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from lovs.forecast import opsforecast as of
from lovs.forecast.weekly import spec

UTC = dt.timezone.utc
D = dt.timedelta


class TimingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def registration_day(today: dt.date) -> dt.date:
    """The last Wednesday on or before `today`."""
    return today - D(days=(today.weekday() - 2) % 7)


def check_build_clock(now: dt.datetime) -> dt.date:
    """R if a build may run now (today is R and it is before 20:00Z); else TimingError('missed')."""
    if now.tzinfo is None:
        raise ValueError("the build clock must be timezone-aware")
    now = now.astimezone(UTC)
    R = registration_day(now.date())
    if now.date() != R:
        raise TimingError("missed", f"today {now.date()} is not the registration day {R}")
    if now.time() >= spec.BUILD_LIMIT_UTC:
        raise TimingError("missed", f"{now:%H:%M}Z is not before {spec.BUILD_LIMIT_UTC:%H:%M}Z")
    return R


def deadline(R: dt.date) -> dt.datetime:
    return dt.datetime(R.year, R.month, R.day, 23, 59, 59, tzinfo=UTC)


def window_opens(R: dt.date) -> dt.date:
    return R + D(days=1)


def week_bounds(R: dt.date, k: int) -> tuple[dt.date, dt.date]:
    return R + D(days=7 * (k - 1)), R + D(days=7 * k)


def data_days(R: dt.date, k: int) -> list[dt.date]:
    start, _ = week_bounds(R, k)
    return [start + D(days=i) for i in range(1, 8)]


def lead_days(R: dt.date, cutoff: dt.date, k: int) -> int:
    return 7 * k + (R - cutoff).days


def calendar_days(R: dt.date) -> set[dt.date]:
    """The days a week's own files may name: R, the window opening, every target day,
    settle days and the span limit of each target week."""
    out = {R, window_opens(R)}
    for k in range(1, len(spec.HORIZONS) + 1):
        start, end = week_bounds(R, k)
        out.add(start)
        out.update(data_days(R, k))
        out.add(end + D(days=spec.SETTLE_DAYS))
        out.add(end + D(days=spec.UNSCOREABLE_AFTER_DAYS))
    return out


def choose_cutoff(R: dt.date, published: Mapping[dt.date, dt.date], packet_days: set[dt.date]) -> dt.date:
    """Newest data day with a promotion and a packet on the fetched mains, at most R-3,
    first published before R."""
    latest = R - D(days=spec.CUTOFF_MIN_LAG)
    usable = [d for d, pub in published.items() if d <= latest and d in packet_days and pub < R]
    if not usable:
        raise TimingError("no_cutoff", f"no data day on both mains on or before {latest} published before {R}")
    return max(usable)


# --- observations -----------------------------------------------------------------

def _day(text) -> dt.date:
    return dt.date.fromisoformat(str(text)[:10])


def value_of(series: spec.Series, row: Mapping) -> float | None:
    if series.kind == "lag":
        pub = row.get("published_at")
        if not pub:
            return None
        return float(min(spec.LAG_CAP, (_day(pub) - _day(row["data_as_of"])).days))
    if series.province:
        v = ((row.get("province_split") or {}).get(series.province) or {}).get(series.measure)
    else:
        v = row.get(series.field)
    return None if v is None else float(v)


def observations(series: spec.Series, rows: Sequence[Mapping]) -> list[of.Observation]:
    """Dated, non-null, first-per-day observations of one series from vintage rows."""
    out, seen = [], set()
    for row in rows:
        v, day = value_of(series, row), _day(row["data_as_of"])
        if v is None or day in seen:
            continue
        seen.add(day)
        out.append(of.Observation(day, v))
    out.sort(key=lambda o: o.date)
    return out


def history(series: spec.Series, rows: Sequence[Mapping], R: dt.date, cutoff: dt.date) -> list[of.Observation]:
    obs = observations(series, rows)
    if series.kind == "lag":
        # Lags of data days up to R-14 are all observed up to the cap; later ones are censored.
        return [o for o in obs if o.date <= R - D(days=spec.LAG_HISTORY_DAYS)]
    return [o for o in obs if o.date <= cutoff]


def province_active(series: spec.Series, rows: Sequence[Mapping], cutoff: dt.date) -> tuple[bool, str]:
    """Frozen rule: printed in every SitRep of the trailing 28 days, with 20 or more new
    confirmed cases in the province over those days."""
    window = [r for r in rows if cutoff - D(days=spec.PROVINCE_ACTIVE_DAYS) < _day(r["data_as_of"]) <= cutoff]
    if not window:
        return False, "province_inactive"
    printed = [((r.get("province_split") or {}).get(series.province) or {}).get("confirmed") for r in window]
    if any(v is None for v in printed):
        return False, "province_inactive"
    before = [r for r in rows if _day(r["data_as_of"]) <= cutoff - D(days=spec.PROVINCE_ACTIVE_DAYS)]
    base = None
    for r in reversed(before):
        base = ((r.get("province_split") or {}).get(series.province) or {}).get("confirmed")
        if base is not None:
            break
    if base is None or printed[-1] - base < spec.PROVINCE_ACTIVE_MIN_CASES:
        return False, "province_inactive"
    return True, "active"


def active_series(rows: Sequence[Mapping], R: dt.date, cutoff: dt.date) -> dict[str, tuple[bool, str]]:
    out = {}
    for s in spec.SERIES:
        hist = history(s, rows, R, cutoff)
        ref = R - D(days=spec.LAG_HISTORY_DAYS) if s.kind == "lag" else cutoff
        if len(hist) < spec.MIN_HISTORY_OBS:
            out[s.id] = (False, "short_history")
        elif (ref - hist[-1].date).days > spec.RECENCY_DAYS:
            out[s.id] = (False, "no_recent_observation")
        elif s.province:
            out[s.id] = province_active(s, rows, cutoff)
        else:
            out[s.id] = (True, "active")
    return out


# --- realised values ----------------------------------------------------------------

@dataclass(frozen=True)
class Realised:
    value: float | None
    status: str                 # ok | missing_boundary | too_few_days | held_basis_break
    reason: str
    n_days: int = 0
    days: tuple[str, ...] = field(default=())


def _withheld(series: spec.Series, row: Mapping) -> bool:
    field_name = series.field if not series.province else None
    return bool(field_name) and f"withheld:{field_name}" in (row.get("flags") or [])


def realised(series: spec.Series, rows_by_day: Mapping[dt.date, Mapping], R: dt.date, k: int) -> Realised:
    start, end = week_bounds(R, k)
    if series.kind in ("flow", "endlevel", "lag"):
        bounds = (start, end) if series.kind == "flow" else (end,)
        values = []
        for day in bounds:
            row = rows_by_day.get(day)
            if row is None:
                return Realised(None, "missing_boundary", f"no reviewed SitRep for boundary day {day}")
            v = value_of(series, row)
            if v is None:
                if _withheld(series, row):
                    return Realised(None, "held_basis_break", f"{series.field} withheld on {day}")
                return Realised(None, "missing_boundary", f"{series.id} not reported on boundary day {day}")
            values.append(v)
        value = values[1] - values[0] if series.kind == "flow" else values[0]
        return Realised(value, "ok", "observed", days=tuple(d.isoformat() for d in bounds))
    # Means of reported days, at least 5 of 7; N6 drops days whose alert table misses a province.
    vals, used, dropped = [], [], 0
    for day in data_days(R, k):
        row = rows_by_day.get(day)
        if row is None:
            continue
        if series.id == "N6" and spec.FLAG_ALERTS_PROVINCE_ABSENT in (row.get("flags") or []):
            dropped += 1
            continue
        v = value_of(series, row)
        if v is not None:
            vals.append(v)
            used.append(day.isoformat())
    if len(vals) < spec.MEAN_MIN_DAYS:
        return Realised(None, "too_few_days",
                        f"{len(vals)} of 7 days reported ({dropped} dropped for a partial alert table)",
                        n_days=len(vals), days=tuple(used))
    return Realised(statistics.fmean(vals), "ok", "observed", n_days=len(vals), days=tuple(used))
