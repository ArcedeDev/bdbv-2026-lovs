# SPDX-License-Identifier: Apache-2.0
"""One week's registration: forecasts, pins, archives, commitment and manifest.

Public, per week, in data/weekly-forecasts/<R>/: manifest.json, pins.csv, commitment.json
and three numeric archives. forecasts.json (23 quantiles, mean, seed and trajectory digest
for every series, horizon and method) stays private until D5; commitment.json carries its
sha256. Every public value passes privacy_gate before anything is written, and every file
is created exclusively. Nothing here touches either calibration ledger.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from lovs import forecast_scoring as fs
from lovs.forecast import backtest as bt
from lovs.forecast import pins_block8 as p8
from lovs.forecast import pins_block13 as p13
from lovs.forecast.weekly import methods, privacy, promotion_rows, spec, targets, vintage

PUBLIC_FILES = spec.PUBLIC_FILES
PRIVATE_FILES = spec.PRIVATE_FILES
PIN_COLUMNS = ("pin_id", "registration_day", "series", "horizon_days", "target_start", "target_end",
               "target_kind", "threshold", "shape", *(spec.price_column(m) for m in spec.METHOD_IDS))


class RegistrationError(RuntimeError):
    """A week cannot be registered as built; nothing is written."""


def registered_seeds(ledger_path: Path) -> frozenset[int]:
    """Every seed a pin of the operational ledger records (every block, Blocks 14 and 15 included)."""
    return registered_seeds_from_bytes(Path(ledger_path).read_bytes())


def registered_seeds_from_bytes(raw: bytes) -> frozenset[int]:
    ledger = json.loads(raw)
    return frozenset(int(pin["generator"]["seed"]) for block in ledger["blocks"] for pin in block["points"]
                     if isinstance((pin.get("generator") or {}).get("seed"), int))


def weekly_seed(R: dt.date, series_id: str, method: str, taken: frozenset[int] | set[int]) -> int:
    """pins_block13.derived_seed of the week's parts, with a counter stepping past any taken seed."""
    counter = 0
    while True:
        parts = (spec.SPEC_VERSION, R.isoformat(), series_id, method) + ((f"c{counter}",) if counter else ())
        try:
            return p13.derived_seed(*parts, taken=frozenset(taken))
        except p13.RegistrationError:
            counter += 1


@dataclass
class WeekBuild:
    R: dt.date
    cutoff: dt.date
    forecasts: dict
    pins: list[dict]
    dropped: list[dict]
    rows: list[dict]
    floor: dict
    manifest_base: dict
    privacy_context: privacy.PrivacyContext
    seeds: dict = field(default_factory=dict)
    # (series, horizon_days, method) -> the empirical CDF of the cell's draws, kept only when
    # build_week is asked for it (scoring a week's PITs at resolution).
    cdfs: dict = field(default_factory=dict)


def _share_at_or_below(sorted_xs: Sequence[float], t: float) -> float:
    return round(bisect.bisect_right(sorted_xs, t) / len(sorted_xs), 4)


def _cell_ok(series: spec.Series, q: list[list[float]], last: float) -> bool:
    values = [v for _, v in q]
    if len(q) != len(spec.QUANTILE_LEVELS) or tuple(l for l, _ in q) != spec.QUANTILE_LEVELS:
        return False
    if values != sorted(values):
        return False
    lo = last if series.bound == "cumulative" else 0.0
    hi = 100.0 if series.bound == "percent" else (float(spec.LAG_CAP) if series.bound == "lag" else None)
    return values[0] >= lo - 1e-9 and (hi is None or values[-1] <= hi + 1e-9)


def build_week(R: dt.date, rows: Sequence[dict], cutoff: dt.date, *, n_paths: int = spec.PATHS,
               taken_seeds: frozenset[int] = frozenset(), generator: Mapping, fetched: Mapping,
               build_time: str, seeds: Mapping[str, int] | None = None, inputs: Mapping | None = None,
               promotions: Sequence[dict] = (), prior_registration_days: Iterable[dt.date] = (),
               arrivals: Sequence[Mapping] = (), keep_cdfs: bool = False) -> WeekBuild:
    """Forecasts and pins for origin R from the vintage at `cutoff`, deterministically."""
    if R.weekday() != 2:
        raise RegistrationError(f"{R} is not a Wednesday")
    if cutoff > R - dt.timedelta(days=spec.CUTOFF_MIN_LAG):
        raise RegistrationError(f"cutoff {cutoff} is after R - {spec.CUTOFF_MIN_LAG}")
    rows = [r for r in rows if vintage.usable(r, R, cutoff)]
    active = targets.active_series(rows, R, cutoff)
    taken = set(taken_seeds)
    used_seeds: dict[str, int] = {}
    series_meta, cells, pins, dropped = [], [], [], []
    floor = {"floored": 0, "steps": 0}
    cdfs: dict = {}
    for s in spec.SERIES:
        ok, reason = active[s.id]
        hist = targets.history(s, rows, R, cutoff) if ok else []
        meta = {"series": s.id, "active": ok, "reason": reason}
        if not ok:
            series_meta.append(meta)
            continue
        a, b = spec.pair(s)
        draws = {}
        for m in spec.ladder_set(s):
            key = f"{s.id}|{m}"
            seed = seeds[key] if seeds is not None else weekly_seed(R, s.id, m, taken)
            taken.add(seed)
            used_seeds[key] = seed
            draws[m] = methods.simulate(s, m, hist, R, seed=seed, n_paths=n_paths)
        draws[spec.M_POOL] = methods.pool(draws[a], draws[b])
        series_cells, series_cdfs = [], {}
        for k, h in enumerate(spec.HORIZONS, start=1):
            start, end = targets.week_bounds(R, k)
            for m in spec.methods_for(s):
                q = methods.quantiles(draws[m].samples[k])
                series_cells.append({
                    "series": s.id, "horizon_days": h, "target_start": start.isoformat(),
                    "target_end": end.isoformat(), "lead_days": targets.lead_days(R, hist[-1].date, k),
                    "method": m, "quantiles": q, "mean": methods.mean(draws[m].samples[k]),
                    "seed": draws[m].seed if m != spec.M_POOL else None,
                    "trajectory_sha256": draws[m].trajectory_sha256, "trajectories_hashed": draws[m].hashed,
                })
                if keep_cdfs:
                    series_cdfs[(s.id, h, m)] = fs.empirical_cdf(draws[m].samples[k])
        if not all(_cell_ok(s, c["quantiles"], hist[-1].value) for c in series_cells):
            meta.update(active=False, reason="cells_incomplete")
            series_meta.append(meta)
            continue
        for m in (a, b):
            floor["floored"] += draws[m].floored
            floor["steps"] += draws[m].steps
        meta.update(last_value=hist[-1].value, last_date=hist[-1].date.isoformat(), n_obs=len(hist))
        series_meta.append(meta)
        cells += series_cells
        cdfs.update(series_cdfs)
        p, d = _series_pins(R, s, draws)
        pins += p
        dropped += d
    for n, c in enumerate(cells, start=1):
        c["forecast_id"] = spec.forecast_id(R, n)
    for n, p in enumerate(pins, start=1):
        p["pin_id"] = spec.pin_id(R, n)
    forecasts = {
        "spec_version": spec.SPEC_VERSION, "spec_sha256": spec.spec_sha256(), "registration_day": R.isoformat(),
        "cutoff": cutoff.isoformat(), "generator": dict(generator), "n_paths": n_paths,
        "quantile_levels": list(spec.QUANTILE_LEVELS),
        "series": series_meta, "active_series": [m["series"] for m in series_meta if m["active"]],
        "forecasts": cells,
    }
    manifest_base = {
        "spec_version": spec.SPEC_VERSION, "spec_sha256": spec.spec_sha256(), "core_sha256": spec.core_sha256(),
        "outbreak": spec.OUTBREAK,
        "registration_day": R.isoformat(), "deadline": targets.deadline(R).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_opens": targets.window_opens(R).isoformat(), "cutoff": cutoff.isoformat(),
        "generator": dict(generator), "fetched": json.loads(json.dumps(fetched)), "built_at": build_time,
        "n_paths": n_paths, "inputs": dict(inputs or {}),
        "series_skipped": {m["series"]: m["reason"] for m in series_meta if not m["active"]},
        "counts": {"series": len(forecasts["active_series"]), "forecasts": len(cells), "pins": len(pins),
                   "dropped_rungs": len(dropped)},
        "arrivals": [{"commit": a["commit"], "arrived_at": a["arrived_at"]} for a in arrivals],
    }
    build_days = {build_time[:10]} | {str(v.get("fetched_at", ""))[:10] for v in fetched.values()}
    ctx = privacy.context_for(rows, promotions, targets.calendar_days(R) | _calendars(prior_registration_days),
                              build_days)
    return WeekBuild(R, cutoff, forecasts, pins, dropped, rows, floor, manifest_base, ctx, used_seeds, cdfs)


def _calendars(days: Iterable[dt.date]) -> set[dt.date]:
    out: set[dt.date] = set()
    for d in days:
        out |= targets.calendar_days(d)
    return out


def _series_pins(R: dt.date, s: spec.Series, draws: Mapping[str, methods.Draws]):
    pins, dropped = [], []
    ladder = spec.ladder_set(s)
    for k, h in enumerate(spec.HORIZONS, start=1):
        mix = sorted(x for m in ladder for x in draws[m].samples[k])
        thresholds = sorted({p8.snap(bt.quantile(mix, q), s.step) for q in spec.PIN_LEVELS})
        sorted_by_method = {m: sorted(draws[m].samples[k]) for m in ladder}
        start, end = targets.week_bounds(R, k)
        for t in thresholds:
            prices = {m: _share_at_or_below(sorted_by_method[m], t) for m in ladder}
            a, b = spec.pair(s)
            prices[spec.M_POOL] = round((prices[a] + prices[b]) / 2, 4)
            if len(set(prices.values())) == 1 and prices[a] in (0.0, 1.0):
                dropped.append({"series": s.id, "horizon_days": h, "threshold": t})
                continue
            row = {"pin_id": None, "registration_day": R.isoformat(), "series": s.id, "horizon_days": h,
                   "target_start": start.isoformat(), "target_end": end.isoformat(), "target_kind": s.kind,
                   "threshold": t, "shape": "at_or_below"}
            for m in spec.METHOD_IDS:
                row[spec.price_column(m)] = prices.get(m)
            pins.append(row)
    return pins, dropped


# --- files ---------------------------------------------------------------------------

def forecasts_bytes(doc: Mapping) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=True) + "\n").encode("ascii")


def json_bytes(doc: Mapping) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=True) + "\n").encode("ascii")


def pins_csv_bytes(pins: Sequence[Mapping]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=PIN_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for p in pins:
        writer.writerow({k: ("" if p.get(k) is None else p[k]) for k in PIN_COLUMNS})
    return buf.getvalue().encode("ascii")


def archive_records(build: WeekBuild, promotions: Sequence[dict], previous: Mapping | None = None) -> dict:
    """The three numeric archives, as deltas against the previous registered week."""
    previous = previous or {}
    keep = [p for p in promotions if vintage.usable(
        {"data_as_of": p.get("data_as_of"), "published_at": p.get("published_at")}, build.R, build.cutoff)]
    zones = [r for p in keep for r in promotion_rows.zone_records(p)]
    covs = [r for p in keep for r in promotion_rows.covariate_records(p)]

    def new_only(name: str, records: list[dict], key) -> list[dict]:
        before = {key(r): r for r in previous.get(name, [])}
        return [r for r in records if before.get(key(r)) != r]

    return {
        "vintage-delta.jsonl": vintage.delta(previous.get("vintage-delta.jsonl"), build.rows),
        "zones-delta.jsonl": new_only("zones-delta.jsonl", zones, lambda r: (r["sitrep"], r["province"], r["zone"])),
        "province-covariates-delta.jsonl": new_only(
            "province-covariates-delta.jsonl", covs, lambda r: (r["sitrep"], r["table"], r.get("province"))),
    }


def write_week(build: WeekBuild, public_dir: Path, private_dir: Path, *, promotions: Sequence[dict],
               previous_rows: Mapping | None, extract_rows: Sequence[dict]) -> dict[str, str]:
    """Gate, then create every file exclusively. Returns {file name: sha256}."""
    public_dir, private_dir = Path(public_dir), Path(private_dir)
    archives = archive_records(build, promotions, previous_rows)
    forecasts_raw = forecasts_bytes(build.forecasts)
    commitment = {
        "registration_day": build.R.isoformat(), "spec_version": spec.SPEC_VERSION,
        "forecasts_sha256": hashlib.sha256(forecasts_raw).hexdigest(), "forecasts_bytes": len(forecasts_raw),
        "forecast_count": len(build.forecasts["forecasts"]), "pin_count": len(build.pins),
    }
    public: dict[str, bytes] = {
        "pins.csv": pins_csv_bytes(build.pins),
        "commitment.json": json_bytes(commitment),
        **{name: vintage.jsonl_bytes(recs) for name, recs in archives.items()},
    }
    # The gate runs on every value of every public file before anything is written.
    for name, recs in archives.items():
        for rec in recs:
            privacy.gate(rec, build.privacy_context)
    privacy.check_archive_values([*archives["zones-delta.jsonl"], *archives["province-covariates-delta.jsonl"],
                                  *archives["vintage-delta.jsonl"]], promotions, extract_rows)
    privacy.gate(privacy.csv_rows(public["pins.csv"].decode("ascii")), build.privacy_context)
    privacy.gate(commitment, build.privacy_context)
    manifest = dict(build.manifest_base)
    manifest["files"] = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(public.items())}
    manifest["counts"] = dict(manifest["counts"], vintage_rows=len(archives["vintage-delta.jsonl"]),
                              zone_records=len(archives["zones-delta.jsonl"]),
                              covariate_records=len(archives["province-covariates-delta.jsonl"]))
    public["manifest.json"] = json_bytes(manifest)
    privacy.gate(manifest, build.privacy_context)
    for name in PUBLIC_FILES:
        vintage.write_exclusive(public_dir / name, public[name])
    vintage.write_exclusive(private_dir / "forecasts.json", forecasts_raw)
    return {**manifest["files"], "manifest.json": hashlib.sha256(public["manifest.json"]).hexdigest(),
            "forecasts.json": commitment["forecasts_sha256"]}


# --- regeneration -----------------------------------------------------------------------

def store_vintage(store_root: Path, R: dt.date) -> list[dict]:
    """The vintage of week R rebuilt from the public deltas of every week up to R."""
    weeks = sorted(p for p in Path(store_root).iterdir() if p.is_dir() and p.name[:2] == "20"
                   and dt.date.fromisoformat(p.name) <= R)
    chunks = []
    for w in weeks:
        raw = (w / "vintage-delta.jsonl").read_text(encoding="ascii")
        chunks.append([json.loads(line) for line in raw.splitlines() if line])
    return vintage.reconstruct(chunks)


def regenerate_build(public_dir: Path, private_forecasts: Path, *, store_root: Path,
                     keep_cdfs: bool = False) -> WeekBuild:
    """Rebuild a registered week from the public archive, the manifest and the private seeds."""
    manifest = json.loads((Path(public_dir) / "manifest.json").read_text(encoding="utf-8"))
    private = json.loads(Path(private_forecasts).read_text(encoding="utf-8"))
    R = dt.date.fromisoformat(manifest["registration_day"])
    cutoff = dt.date.fromisoformat(manifest["cutoff"])
    seeds = {f"{f['series']}|{f['method']}": f["seed"] for f in private["forecasts"] if f["seed"] is not None}
    rows = store_vintage(Path(store_root), R)
    build = build_week(R, rows, cutoff, n_paths=manifest["n_paths"], generator=manifest["generator"],
                       fetched=manifest["fetched"], build_time=manifest["built_at"], seeds=seeds,
                       inputs=manifest["inputs"], keep_cdfs=keep_cdfs)
    return build


def regenerate(public_dir: Path, private_forecasts: Path, *, store_root: Path) -> bytes:
    """Rebuild forecasts.json from the public archive, the manifest and the private seeds."""
    return forecasts_bytes(regenerate_build(public_dir, private_forecasts, store_root=store_root).forecasts)


def verified_cdfs(public_dir: Path, private_forecasts: Path, *, store_root: Path) -> dict:
    """forecast_id -> the empirical CDF of its draws, from a rebuild of the registered week.

    The rebuild must reproduce the registered forecasts.json byte for byte, so the draws are
    the ones the week was priced from; otherwise the week's PITs are not scored.
    """
    build = regenerate_build(public_dir, private_forecasts, store_root=store_root, keep_cdfs=True)
    if forecasts_bytes(build.forecasts) != Path(private_forecasts).read_bytes():
        raise RegistrationError(f"{build.R} does not regenerate from its public archive; its PITs are not scored")
    return {f["forecast_id"]: build.cdfs[(f["series"], f["horizon_days"], f["method"])]
            for f in build.forecasts["forecasts"]}
