# SPDX-License-Identifier: Apache-2.0
"""Resolution of registered weeks (DESIGN.md 3): target statuses, pin outcomes, scores.

Every build re-resolves every registered week from arrival-timed truth. The settled value
counts for the record and v2; the first-published value is the real-time score. A target
whose boundary day is not yet on both mains is pending_boundary, and unscoreable_span at
end + 28. Anomaly-flagged targets are scored but sit outside the primary endpoint. An N7
target whose end-day promotion is not on main at settle is censored, anomaly-flagged.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from lovs.forecast.weekly import score, spec, targets, truth

UTC = dt.timezone.utc
D = dt.timedelta
RESULT_COLUMNS = ("pin_id", "registration_day", "series", "horizon_days", "target_start", "target_end",
                  "threshold", "status", "value_kind", "realised", "outcome", "anomaly", "deciding_commit",
                  "decided_at",
                  # brier_<m> then log_<m> for each method in turn, as the v2 pre-registration fixes them
                  *(c for m in spec.METHOD_IDS for c in (spec.score_column("brier", m), spec.score_column("log", m))))


@dataclass(frozen=True)
class TargetStatus:
    series: str
    k: int
    status: str
    value: float | None
    reason: str
    n_days: int = 0
    flags: tuple[str, ...] = field(default=())
    kind: str = "first_published"          # or "settled"


def _cumulative_fall(series: spec.Series, rows_by_day, start: dt.date, end: dt.date) -> bool:
    if series.kind not in ("flow", "endlevel"):
        return False
    days = sorted(d for d in rows_by_day if start - D(days=1) <= d <= end + D(days=1))
    vals = [targets.value_of(series, rows_by_day[d]) for d in days]
    vals = [v for v in vals if v is not None]
    return any(b < a for a, b in zip(vals, vals[1:]))


def _withheld_in_window(series: spec.Series, rows_by_day, start: dt.date, end: dt.date) -> bool:
    if series.province or series.kind == "lag":
        return False
    code = f"withheld:{series.field}"
    return any(code in (rows_by_day[d].get("flags") or []) for d in rows_by_day if start <= d <= end)


def resolve_target(series: spec.Series, rows_by_day: Mapping[dt.date, Mapping], R: dt.date, k: int,
                   now: dt.datetime, view_time: dt.datetime | None = None) -> TargetStatus:
    """`view_time` is when `rows_by_day` stood on main (a settle time for a settled view)."""
    start, end = targets.week_bounds(R, k)
    if now.astimezone(UTC).date() <= end:
        return TargetStatus(series.id, k, "pending", None, "target week has not ended")
    got = targets.realised(series, rows_by_day, R, k)
    view_time = view_time or now
    if (series.kind == "lag" and got.status == "missing_boundary"
            and view_time.astimezone(UTC).date() >= end + D(days=spec.SETTLE_DAYS)):
        # The lag is read from the promotion's own published field, so it never depends on when
        # the promotion reached main. With no end-day promotion on main at settle there is
        # nothing to read: the cell is censored, flagged, outside the primary endpoint and
        # reported, never a lag of the cap (review/WEEKLY-CODE-REVIEW.md R2-3).
        return TargetStatus(series.id, k, "censored", None, "end-day promotion not on main at settle",
                            flags=("lag_censored",))
    if got.status == "ok":
        flags = []
        if _cumulative_fall(series, rows_by_day, start, end):
            flags.append("cumulative_fall")
        if _withheld_in_window(series, rows_by_day, start, end):
            flags.append("withheld_field")
        return TargetStatus(series.id, k, "observed", got.value, "observed", got.n_days, tuple(flags))
    if got.status == "held_basis_break":
        return TargetStatus(series.id, k, "held_basis_break", None, got.reason)
    if now.astimezone(UTC).date() >= end + D(days=spec.UNSCOREABLE_AFTER_DAYS):
        return TargetStatus(series.id, k, "unscoreable_span", None, got.reason, got.n_days)
    return TargetStatus(series.id, k, "pending_boundary", None, got.reason, got.n_days)


def resolve_week_targets(R: dt.date, series_ids: Iterable[str], rows_by_day, now: dt.datetime) -> list[TargetStatus]:
    return [resolve_target(spec.SERIES_BY_ID[sid], rows_by_day, R, k, now)
            for sid in series_ids for k in range(1, len(spec.HORIZONS) + 1)]


def void_decided_before(pin: Mapping, rows_by_day: Mapping[dt.date, Mapping], R: dt.date) -> bool:
    """A level pin already answered NO by published data for a day up to R (N4 only)."""
    s = spec.SERIES_BY_ID[pin["series"]]
    if s.kind != "endlevel":
        return False
    seen = [targets.value_of(s, row) for d, row in rows_by_day.items() if d <= R]
    seen = [v for v in seen if v is not None]
    return bool(seen) and max(seen) > float(pin["threshold"])


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(round(v, 6))
    return v


def resolve_week(pins: Sequence[Mapping], R: dt.date, views: Mapping, now: dt.datetime,
                 deadline_view: Mapping[dt.date, Mapping]) -> tuple[list[dict], list[TargetStatus]]:
    """Result rows (one per pin and value kind available) and target statuses for one week.

    `views` maps "first_published" to (rows_by_day, provenance) and each settle time to the
    same pair as main stood then; `deadline_view` is first-published data as of R 23:59:59Z.
    """
    out, statuses = [], []
    fp_rows, fp_prov = views["first_published"]
    targets_done: dict[tuple[str, int], dict] = {}
    for sid in sorted({p["series"] for p in pins}):
        s = spec.SERIES_BY_ID[sid]
        for k in range(1, len(spec.HORIZONS) + 1):
            _, end = targets.week_bounds(R, k)
            fp = resolve_target(s, fp_rows, R, k, now)
            if s.kind == "lag":
                # The lag is censored in real time: it is scored settled only.
                fp = TargetStatus(s.id, k, "pending", None, "lag series are scored settled only")
            settled = None
            st_time = truth.settle_time(end)
            if now >= st_time and st_time in views:
                settled = replace(resolve_target(s, views[st_time][0], R, k, now, view_time=st_time),
                                  kind="settled")
            statuses.append(settled or fp)
            targets_done[(sid, k)] = {"first_published": (fp, fp_prov),
                                      "settled": (settled, views[st_time][1] if settled else {})}
    for pin in pins:
        k = spec.HORIZONS.index(int(pin["horizon_days"])) + 1
        _, end = targets.week_bounds(R, k)
        if void_decided_before(pin, deadline_view, R):
            out.append(_result(pin, "void_decided_before", "first_published", None, None, False, {}, end))
            continue
        both = targets_done[(pin["series"], k)]
        fp = both["first_published"][0]
        st, st_prov = both["settled"]
        lag = spec.SERIES_BY_ID[pin["series"]].kind == "lag"
        for kind, (status, prov) in (("first_published", both["first_published"]), ("settled", (st, st_prov))):
            if status is None or (lag and kind == "first_published"):
                continue
            anomaly = bool(status.flags)
            if kind == "settled" and fp.status == "observed" and status.status == "observed":
                gap = abs(status.value - fp.value)
                if gap > spec.ANOMALY_REL * max(abs(fp.value), 1e-9) and gap > spec.SERIES_BY_ID[pin["series"]].step:
                    anomaly = True
            if status.status != "observed":
                out.append(_result(pin, status.status, kind, None, None, anomaly, {}, end))
                continue
            outcome = 1 if status.value <= float(pin["threshold"]) else 0
            out.append(_result(pin, "resolved_yes" if outcome else "resolved_no", kind, status.value, outcome,
                               anomaly, prov.get(end) and {"commit": prov[end][0], "at": prov[end][1]}, end))
    return out, statuses


def _on_both(pair, packet_days):
    """A boundary day counts only once its promotion and its packet are both on the mains."""
    rows, prov = pair
    return ({d: r for d, r in rows.items() if d in packet_days}, {d: p for d, p in prov.items() if d in packet_days})


def views_for(view, R: dt.date, now: dt.datetime, packet_days) -> tuple[dict, Mapping[dt.date, Mapping]]:
    """The views one registered week is resolved from: first-published data as of now, main as
    it stood at each settle time already passed, and first-published data at R's deadline."""
    views = {"first_published": _on_both(view.rows(first=True, when=now), packet_days)}
    for k in range(1, len(spec.HORIZONS) + 1):
        st = truth.settle_time(targets.week_bounds(R, k)[1])
        if now >= st:
            views[st] = _on_both(view.rows(when=st), packet_days)
    deadline_rows, _ = view.rows(first=True, when=targets.deadline(R))
    return views, deadline_rows


def _result(pin, status, kind, value, outcome, anomaly, prov, end) -> dict:
    row = {"pin_id": pin["pin_id"], "registration_day": pin["registration_day"], "series": pin["series"],
           "horizon_days": int(pin["horizon_days"]), "target_start": pin["target_start"],
           "target_end": pin["target_end"], "threshold": pin["threshold"], "status": status, "value_kind": kind,
           "realised": value, "outcome": outcome, "anomaly": 1 if anomaly else 0,
           "deciding_commit": (prov or {}).get("commit"),
           "decided_at": (prov or {}).get("at").strftime("%Y-%m-%dT%H:%M:%SZ") if (prov or {}).get("at") else None}
    for m in spec.METHOD_IDS:
        p = pin.get(spec.price_column(m))
        p = None if p in (None, "") else float(p)
        if p is not None and outcome is not None:
            sc = score.pin_scores(p, outcome)
            row[spec.score_column("brier", m)] = sc["brier"]
            row[spec.score_column("log", m)] = sc["log"]
        else:
            row[spec.score_column("brier", m)] = None
            row[spec.score_column("log", m)] = None
    return row


def results_csv_bytes(rows: Sequence[Mapping]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=RESULT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in sorted(rows, key=lambda r: (r["registration_day"], r["pin_id"], r["value_kind"])):
        writer.writerow({k: _fmt(r.get(k)) for k in RESULT_COLUMNS})
    return buf.getvalue().encode("ascii")


def quantile_scores(forecasts_doc: Mapping, statuses: Sequence[TargetStatus], R: dt.date,
                    cdfs: Mapping | None, *, pit_unscored: str | None = None) -> list[dict]:
    """Private: WIS, coverage and PIT of every forecast whose target is observed.

    cdfs maps forecast_id to the empirical CDF of the forecast's draws
    (register.verified_cdfs); the PIT is taken from it. None leaves every PIT field None, the
    quantile scores as they are, and pit_unscored (the reason) on each row.
    """
    if (cdfs is None) != (pit_unscored is not None):
        raise ValueError("give the CDFs or the reason the PIT is unscored, not both")
    by_target = {(s.series, s.k): s for s in statuses}
    out = []
    for f in forecasts_doc["forecasts"]:
        k = spec.HORIZONS.index(f["horizon_days"]) + 1
        st = by_target.get((f["series"], k))
        if st is None or st.status != "observed":
            continue
        if cdfs is not None and f["forecast_id"] not in cdfs:
            raise ValueError(f"no draws for {f['forecast_id']}: its PIT cannot be scored")
        u = random.Random(f"{f['forecast_id']}|pit").random()
        out.append({"forecast_id": f["forecast_id"], "series": f["series"], "horizon_days": f["horizon_days"],
                    "method": f["method"], "value_kind": st.kind, "realised": st.value, "flags": list(st.flags),
                    "pit_unscored": pit_unscored,
                    **score.target_scores(f["quantiles"], st.value, u=u, cdf=None if cdfs is None else cdfs[f["forecast_id"]])})
    return out


def read_pins(path: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO(Path(path).read_text(encoding="utf-8"))))
