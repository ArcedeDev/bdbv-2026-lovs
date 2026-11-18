# SPDX-License-Identifier: Apache-2.0
"""Build one week of the weekly loop from fetched commits of LOVS and the evidence packets.

  python3 -m lovs.forecast.weekly build --clock 2026-11-25T12:30:00Z \
      --lovs-repo <bare clone> --lovs-commit <sha> --mcp-repo <bare clone> --mcp-commit <sha> \
      --fetched fetched.json --arrivals arrivals.json --store <dir>/data/weekly-forecasts \
      --private-root <weeks dir> --report-out <dir> --generator-commit <sha>

Reads promotions and packets at the given commits only, never a working tree. Writes the
week's public files under --store/<R>/, the private half under --private-root/<R>/private/,
and the gate, cross-check and data-usefulness reports under --report-out. Prints a JSON
summary. Exits non-zero, writing nothing public, on any timing, cross-check, privacy,
completeness or regeneration failure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

from lovs.forecast.weekly import (export, privacy, promotion_rows, register, resolve, source, spec, targets,
                                  truth, usefulness, vintage)

UTC = dt.timezone.utc


def _iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prior_weeks(store: Path, R: dt.date) -> list[Path]:
    if not store.is_dir():
        return []
    return sorted(p for p in store.iterdir() if p.is_dir() and p.name[:2] == "20" and dt.date.fromisoformat(p.name) < R)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="ascii").splitlines() if x] if path.is_file() else []


def build(args) -> dict:
    t0 = time.monotonic()
    clock = truth.parse_time(args.clock) if args.clock else dt.datetime.now(UTC)
    R = targets.check_build_clock(clock)
    fetched = json.loads(Path(args.fetched).read_text(encoding="utf-8"))
    arrivals_doc = json.loads(Path(args.arrivals).read_text(encoding="utf-8"))
    lovs = source.GitTree(args.lovs_repo, args.lovs_commit)
    mcp = source.GitTree(args.mcp_repo, args.mcp_commit)
    promos = source.load_promotions(lovs)
    packets = source.load_packets(mcp)
    promotions = [doc for _, doc, _ in promos]
    extract = vintage.load_extract(lovs)
    xcheck = vintage.require_cross_check(promotions, [doc for _, doc, _ in packets], extract)
    published = {}
    for p in promotions:
        day = dt.date.fromisoformat(p["data_as_of"])
        pub = dt.date.fromisoformat(str(p["published_at"])[:10])
        published[day] = min(pub, published.get(day, pub))
    packet_days = {dt.date.fromisoformat(doc["data_as_of"][:10]) for _, doc, _ in packets}
    cutoff = targets.choose_cutoff(R, published, packet_days)
    rows = vintage.build_vintage(extract, promotions, R, cutoff)
    store = Path(args.store)
    prior = _prior_weeks(store, R)
    private_root = Path(args.private_root)
    taken = set(register.registered_seeds_from_bytes(lovs.read("data/operational-calibration-ledger.json")))
    for w in prior:
        pf = private_root / w.name / "private" / "forecasts.json"
        if pf.is_file():
            taken |= {f["seed"] for f in json.loads(pf.read_text(encoding="utf-8"))["forecasts"] if f["seed"]}
    inputs = {"extract": {"path": vintage.EXTRACT_RELATIVE, "sha256": vintage.EXTRACT_SHA256},
              "promotions": [{"sitrep": str(d["sitrep_number"]).zfill(3), "sha256": h} for _, d, h in promos
                             if dt.date.fromisoformat(d["data_as_of"]) <= cutoff],
              "packets": [{"sitrep": str(d.get("sitrep_label")).zfill(3), "sha256": h} for _, d, h in packets
                          if dt.date.fromisoformat(d["data_as_of"][:10]) <= cutoff]}
    generator = {"repository": spec.REPOSITORY, "commit": args.generator_commit}
    week = register.build_week(R, rows, cutoff, n_paths=args.n_paths, taken_seeds=frozenset(taken),
                               generator=generator, fetched=fetched, build_time=_iso(clock), inputs=inputs,
                               promotions=promotions,
                               prior_registration_days=[dt.date.fromisoformat(w.name) for w in prior])
    previous = None
    if prior:
        previous = {"vintage-delta.jsonl": register.store_vintage(store, R - dt.timedelta(days=1)),
                    "zones-delta.jsonl": [r for w in prior for r in _jsonl(w / "zones-delta.jsonl")],
                    "province-covariates-delta.jsonl":
                        [r for w in prior for r in _jsonl(w / "province-covariates-delta.jsonl")]}
    public_dir = store / R.isoformat()
    private_dir = private_root / R.isoformat() / "private"
    t_build = time.monotonic()
    files = register.write_week(week, public_dir, private_dir, promotions=promotions, previous_rows=previous,
                                extract_rows=extract)
    regenerated = register.regenerate(public_dir, private_dir / "forecasts.json", store_root=store)
    if hashlib.sha256(regenerated).hexdigest() != files["forecasts.json"]:
        raise register.RegistrationError("the week does not regenerate from its public archive")
    t_regen = time.monotonic()
    # Re-resolve every registered week from arrival-timed truth.
    chain, history = truth.git_promotion_history(args.lovs_repo, args.lovs_commit)
    arrivals = truth.arrival_times(chain, arrivals_doc.get("events", []), arrivals_doc.get("merges", {}))
    versions = truth.file_versions(history, arrivals)
    blobs = source.read_blobs(args.lovs_repo, [v.blob for vs in versions.values() for v in vs]) if prior else {}
    view = truth.TruthView(versions, lambda oid: blobs[oid] if oid in blobs else source.blob(args.lovs_repo, oid))
    results, statuses_all, qscores = [], [], []
    for w in prior:
        wR = dt.date.fromisoformat(w.name)
        pins = resolve.read_pins(w / "pins.csv")
        views = {"first_published": _on_both(view.rows(first=True, when=clock), packet_days)}
        for k in range(1, len(spec.HORIZONS) + 1):
            st = truth.settle_time(targets.week_bounds(wR, k)[1])
            if clock >= st:
                views[st] = _on_both(view.rows(when=st), packet_days)
        deadline_rows, _ = view.rows(first=True, when=targets.deadline(wR))
        rows_w, st_w = resolve.resolve_week(pins, wR, views, clock, deadline_rows)
        results += rows_w
        statuses_all += st_w
        pf = private_root / w.name / "private" / "forecasts.json"
        if pf.is_file():
            qscores += resolve.quantile_scores(json.loads(pf.read_text(encoding="utf-8")), st_w, wR)
    if results:
        ctx = week.privacy_context
        ctx.publication_days |= {r["decided_at"][:10] for r in results if r.get("decided_at")}
        privacy.gate(privacy.csv_rows(resolve.results_csv_bytes(results).decode("ascii")), ctx)
        vintage.write_exclusive(store / "results" / f"{R.isoformat()}.csv", resolve.results_csv_bytes(results))
        vintage.write_exclusive(private_dir / "scores.json", register.json_bytes({"scores": qscores}))
    if args.receipt:
        receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
        privacy.gate(receipt, week.privacy_context)
        vintage.write_exclusive(store / "receipts" / f"{receipt['registration_day']}.json", register.json_bytes(receipt))
    report_dir = Path(args.report_out)
    origins = json.loads(Path(args.origins).read_text(encoding="utf-8")) if args.origins else []
    previous_flags = {f for r in (previous or {}).get("vintage-delta.jsonl", []) for f in r.get("flags") or []}
    statuses_ok = all(s.status in spec.TARGET_STATUSES and s.reason for s in statuses_all)
    dur = usefulness.report(build=week, origins=origins, results=results, previous_flags=previous_flags,
                            quantile_scores=qscores, first_scored={}, statuses_ok=statuses_ok)
    features = export.origin_features(rows, R, cutoff)
    tables = export.write_tables(private_dir / "tables", forecasts=[(week.forecasts, week.manifest_base)],
                                 pins=week.pins, snapshots=[(R.isoformat(), results)], features=features,
                                 quantile_scores=qscores)
    t_end = time.monotonic()
    values = sum(len(f["quantiles"]) for f in week.forecasts["forecasts"])
    summary = {
        "registration_day": R.isoformat(), "cutoff": cutoff.isoformat(), "deadline": _iso(targets.deadline(R)),
        "fetched": fetched, "generator": generator, "spec_sha256": spec.spec_sha256(),
        "series_active": week.forecasts["active_series"],
        "series_skipped": week.manifest_base["series_skipped"],
        "forecasts": len(week.forecasts["forecasts"]), "quantile_values": values, "pins": len(week.pins),
        "dropped_rungs": len(week.dropped), "commitment": files["forecasts.json"],
        "vintage_rows": len(rows), "vintage_sources": {s: sum(1 for r in rows if r["source"] == s)
                                                       for s in spec.ARCHIVE_SOURCES},
        "cross_check": {k: v for k, v in xcheck.items() if k != "mismatches"} | {"mismatches": 0},
        "files": files, "results_rows": len(results), "tables": tables,
        "seconds": {"to_write": round(t_build - t0, 1), "write_and_regenerate": round(t_regen - t_build, 1),
                    "resolve_dur_tables": round(t_end - t_regen, 1), "total": round(t_end - t0, 1)},
    }
    vintage.write_exclusive(report_dir / "dur.json", register.json_bytes(dur))
    vintage.write_exclusive(report_dir / "cross-check.json", register.json_bytes(xcheck))
    vintage.write_exclusive(report_dir / "build-summary.json", register.json_bytes(summary))
    summary["dur"] = {k: {"value": v["value"], "rag": v["rag"]} for k, v in dur["measures"].items()}
    return summary


def _on_both(pair, packet_days):
    """A boundary day counts only once its promotion and its packet are both on the mains."""
    rows, prov = pair
    return ({d: r for d, r in rows.items() if d in packet_days}, {d: p for d, p in prov.items() if d in packet_days})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m lovs.forecast.weekly", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--clock", help="UTC time of the build (default now)")
    for name in ("lovs-repo", "lovs-commit", "mcp-repo", "mcp-commit", "fetched", "arrivals", "store",
                 "private-root", "report-out", "generator-commit"):
        b.add_argument(f"--{name}", required=True)
    b.add_argument("--n-paths", type=int, default=spec.PATHS)
    b.add_argument("--origins", help="JSON list of the last 8 weeks' outcomes, for the DUR")
    b.add_argument("--receipt", help="the previous week's receipt JSON, added under receipts/")
    args = parser.parse_args(argv)
    try:
        summary = build(args)
    except (targets.TimingError, vintage.CrossCheckError, privacy.PrivacyError, register.RegistrationError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        print(json.dumps({"ok": False, "error": code, "detail": str(exc)[:2000]}))
        return 3 if isinstance(exc, targets.TimingError) and exc.code == "missed" else 4
    print(json.dumps({"ok": True, **summary}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
