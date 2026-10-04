# SPDX-License-Identifier: Apache-2.0
"""Build the reviewable forecast candidate for the next BDBV tournament round.

A candidate is the five-field document a pull request approves and
``python3 -m lovs.model_tournament freeze`` later freezes. This module derives it,
deterministically, from one reviewed source snapshot, so the founder reviews an
artifact a script made rather than one a person typed.

UNIVERSE (pre-registered, fixed on 2026-10-04 from data through SitRep 140). Let
``d`` be the source snapshot's data day. A zone is AFFECTED at ``d`` when an
officially integrated row of any reviewed SitRep health-zone table dated on or
before ``d`` prints it with at least one confirmed case. The SCOPE is every GRID3
v8.0 health zone in a province that holds an affected zone. The targets are the
scope minus the affected zones. Two printed source numbers must agree before
anything is built: the affected count and the scope total in the snapshot
promotion's ``affected_health_zone_footprint``. A mismatch fails closed.

Why the affected provinces only: inside them the SitRep prints a per-zone table
that reconciles to the national confirmed total, so a zone missing from it at
window end is a structured negative. Outside them the SitRep says nothing per
zone, and the registry's negative policy makes that absence unscoreable, which
would let a YES score while a NO could not. Uganda and South Sudan targets are
excluded because the INSP table cannot resolve them and their points come from a
different method than the GRID3 population peaks.

ELIGIBILITY. Every scoring-eligible registry model must have an adapter here, and
its registry module, version and output kind must match the adapter. A model
without one stops the build: the only way to leave an eligible model out of a
round is a reviewed registry change, never a quiet omission.

SOURCE-AVAILABILITY CUTOFF. The caller declares a UTC instant. Every input file is
hashed into the build receipt, and the build fails closed if the source data day,
or any used promotion's data day, publication time or review time, is later than
that instant. Promotions dated after the source data day are not inputs and are
not read past their file name.

Stdlib only. The build has no clock: the same inputs give the same bytes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import tempfile
from collections.abc import Mapping
from typing import Any

from lovs import base_rate_30d
from lovs import distance_only_frontier_30d
from lovs import health_zone_centroids as hzc
from lovs import model_tournament as T
from lovs import sitrep_promotions

REPO_ROOT = T.REPO_ROOT
CANDIDATES_DIR = T.TOURNAMENT_DIR / "candidates"
DEFAULT_SOURCE_SNAPSHOT = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
RECEIPT_SCHEMA_VERSION = "bdbv-model-tournament-candidate-receipt/v1"

UNIVERSE_RULE = (
    "Targets are the GRID3 COD Health Zones v8.0 zones in every province holding a zone "
    "affected at the source data day, minus the affected zones. Affected means an officially "
    "integrated row of a reviewed SitRep health-zone table dated on or before the data day "
    "prints at least one confirmed case. The affected count and the scope total must equal "
    "the snapshot promotion's printed affected_health_zone_footprint. Uganda and South Sudan "
    "geographies are excluded."
)
EXCLUSION_NOTE = (
    "Uganda and South Sudan corridor targets are excluded from this round: the INSP "
    "per-zone table cannot resolve them, and their points are not GRID3 population peaks."
)

ADAPTERS: dict[str, dict[str, str]] = {
    "benchmark.base_rate_30d": {
        "implementation_module": "lovs.base_rate_30d",
        "version": "v1",
        "output_kind": "probability",
    },
    distance_only_frontier_30d.MODEL_ID: {
        "implementation_module": "lovs.distance_only_frontier_30d",
        "version": distance_only_frontier_30d.MODEL_VERSION,
        "output_kind": "rank_score",
    },
}

_PROMOTION_NAME_RE = re.compile(r"^sitrep-(\d{3})-(\d{4}-\d{2}-\d{2})\.json$")


class CandidateBuildError(ValueError):
    """Raised when a candidate cannot be built honestly; nothing is written."""


def _utc(value: str, field: str) -> dt.datetime:
    try:
        return T._utc_datetime(value, field)
    except T.TournamentConfigError as exc:
        raise CandidateBuildError(str(exc)) from exc


# A time printed without a UTC offset is read at the latest UTC instant it could
# denote: its wall time in UTC-12, the westernmost offset. A bare date is read as
# 23:59:59 on that date, then the same way.
_NO_OFFSET_SLACK = dt.timedelta(hours=12)


def _instant(value: Any, field: str) -> dt.datetime:
    """The latest UTC instant a publication or review time can denote."""
    text = str(value or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text = f"{text}T23:59:59"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text):
        try:
            wall = dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)
        except ValueError as exc:
            raise CandidateBuildError(f"{field} {value!r} is not a valid time") from exc
        return wall + _NO_OFFSET_SLACK
    return _utc(text, field)


def _guard(label: str, field: str, value: Any, cutoff: dt.datetime) -> None:
    if value in (None, ""):
        raise CandidateBuildError(f"{label}: {field} is missing; cannot prove it predates the cutoff")
    if _instant(value, f"{label}.{field}") > cutoff:
        raise CandidateBuildError(
            f"{label}: {field} {value} is later than the declared source-availability cutoff "
            f"{cutoff.isoformat().replace('+00:00', 'Z')}"
        )


def _input_row(path: pathlib.Path, role: str, **extra: Any) -> dict[str, Any]:
    return {"path": T._relative(path), "role": role, "sha256": hzc.file_sha256(path), **extra}


def _eligible_models(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    eligible = [m for m in registry["models"] if m.get("scoring_eligible") is True]
    for model in eligible:
        adapter = ADAPTERS.get(str(model["model_id"]))
        if adapter is None:
            raise CandidateBuildError(
                f"{model['model_id']} is scoring-eligible but this builder has no target-level "
                "adapter for it; include it by adding a reviewed adapter, or remove it from the "
                "round through a reviewed registry change"
            )
        for field, expected in adapter.items():
            if model.get(field) != expected:
                raise CandidateBuildError(
                    f"{model['model_id']}: registry {field} {model.get(field)!r} does not match the "
                    f"builder adapter {expected!r}"
                )
    return eligible


def _used_promotions(
    promotions_dir: pathlib.Path, data_day: dt.date, cutoff: dt.datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    promotions: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    for path in sorted(promotions_dir.glob("sitrep-*.json")):
        match = _PROMOTION_NAME_RE.fullmatch(path.name)
        if match is None:
            continue
        if dt.date.fromisoformat(match.group(2)) > data_day:
            continue  # not an input: dated after the source data day, so never opened
        promotion = sitrep_promotions.validate_promotion(
            sitrep_promotions._load_json(path), path=path, require_reviewed=True
        )
        if promotion["data_as_of"] != match.group(2):
            raise CandidateBuildError(f"{path.name}: data_as_of {promotion['data_as_of']} differs from its file name")
        review = promotion.get("review") or {}
        label = path.name
        _guard(label, "data_as_of", promotion["data_as_of"], cutoff)
        _guard(label, "published_at", promotion.get("published_at"), cutoff)
        _guard(label, "review.reviewed_at", review.get("reviewed_at"), cutoff)
        promotions.append(promotion)
        inputs.append(_input_row(
            path, "reviewed_sitrep_promotion",
            data_day=promotion["data_as_of"],
            published_at=promotion.get("published_at"),
            reviewed_at=review.get("reviewed_at"),
        ))
    if not promotions:
        raise CandidateBuildError("no reviewed SitRep promotion on or before the source data day")
    return promotions, inputs


def _zone_tables(
    promotions: list[dict[str, Any]], index: hzc.CentroidIndex
) -> list[dict[str, Any]]:
    """One cumulative per-zone table per data day; the latest edition wins a shared day.

    A row the source prints but has not officially integrated is not affected, which
    follows the official footprint. If such a row carries a confirmed case in the
    latest table, the zone would sit in the universe as a target that already has a
    printed case, so the build stops for a person to decide.
    """
    by_day: dict[str, tuple[int, dict[str, int], list[str]]] = {}
    for promotion in promotions:
        table = (promotion.get("figures") or {}).get("health_zone_table") or {}
        rows = table.get("rows") or []
        if not rows:
            continue
        day = str(table.get("date") or promotion["data_as_of"])
        if day > promotion["data_as_of"]:
            raise CandidateBuildError(
                f"SitRep {promotion['sitrep_number']}: table date {day} is after its data day"
            )
        counts: dict[str, int] = {}
        pending: list[str] = []
        for row in rows:
            name = str(row.get("zone") or "")
            if "ventil" in name.lower():
                continue
            confirmed = row.get("confirmed")
            if isinstance(confirmed, bool) or not isinstance(confirmed, int) or confirmed < 1:
                continue
            if row.get("officially_integrated") is False:
                pending.append(name)
                continue
            try:
                zone_id = index.resolve_sitrep_row(str(row.get("province") or ""), name)
            except hzc.CentroidError as exc:
                raise CandidateBuildError(f"SitRep {promotion['sitrep_number']}: {exc}") from exc
            if zone_id in counts:
                raise CandidateBuildError(f"SitRep {promotion['sitrep_number']}: {zone_id} printed twice")
            counts[zone_id] = confirmed
        edition = int(promotion["sitrep_number"])
        if day not in by_day or edition > by_day[day][0]:
            by_day[day] = (edition, counts, pending)
    if by_day:
        latest_day = max(by_day)
        edition, _, pending = by_day[latest_day]
        if pending:
            raise CandidateBuildError(
                f"SitRep {edition} prints confirmed cases in zone(s) not yet officially integrated "
                f"({', '.join(sorted(pending))}); decide whether they are targets before building"
            )
    return [{"date": day, "counts": counts} for day, (_, counts, _) in sorted(by_day.items())]


def _universe(
    tables: list[dict[str, Any]], index: hzc.CentroidIndex, snapshot_promotion: Mapping[str, Any]
) -> dict[str, Any]:
    affected = sorted({zone for table in tables for zone in table["counts"]})
    provinces = sorted({index.zone(z)["province"] for z in affected})
    scope = index.zones_in_provinces(provinces)
    targets = sorted(set(scope) - set(affected))
    printed = (snapshot_promotion.get("figures") or {}).get("affected_health_zone_footprint")
    if not isinstance(printed, dict):
        raise CandidateBuildError(
            "the snapshot promotion prints no affected_health_zone_footprint; the universe cannot "
            "be checked against the source"
        )
    if printed.get("affected") != len(affected):
        raise CandidateBuildError(
            f"{len(affected)} affected zones derived from the reviewed tables, but the source prints "
            f"{printed.get('affected')}"
        )
    if printed.get("total_in_affected_provinces") != len(scope):
        raise CandidateBuildError(
            f"{len(scope)} GRID3 zones in the affected provinces, but the source prints "
            f"{printed.get('total_in_affected_provinces')}"
        )
    return {
        "rule": UNIVERSE_RULE,
        "affected_provinces": provinces,
        "affected_zones": affected,
        "scope_zone_count": len(scope),
        "target_count": len(targets),
        "targets": targets,
        "printed_footprint": {
            "affected": printed.get("affected"),
            "total_in_affected_provinces": printed.get("total_in_affected_provinces"),
            "not_affected_in_affected_provinces": printed.get("not_affected_in_affected_provinces"),
        },
        "exclusions": EXCLUSION_NOTE,
    }


def _predict(
    model_id: str,
    targets: list[str],
    tables: list[dict[str, Any]],
    universe: Mapping[str, Any],
    index: hzc.CentroidIndex,
    model_cutoff: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if model_id == "benchmark.base_rate_30d":
        # The pool must contain its own history: the module subtracts affected and
        # converted zones from it, so it is the whole scope, not the target count.
        result = base_rate_30d.predict(
            targets,
            [{"date": t["date"], "zones": sorted(t["counts"])} for t in tables],
            target_universe_size=int(universe["scope_zone_count"]),
            cutoff=model_cutoff,
        )
        rows = [
            {"model_id": model_id, "target_id": t, "output_kind": "probability",
             "probability": result["predictions"][t]}
            for t in targets
        ]
        diagnostics = {k: v for k, v in result.items() if k != "predictions"}
        return rows, diagnostics
    if model_id == distance_only_frontier_30d.MODEL_ID:
        points = {z: (index.zone(z)["lat"], index.zone(z)["lon"]) for z in index.zone_ids()}
        result = distance_only_frontier_30d.predict(targets, tables, cutoff=model_cutoff, points=points)
        rows = [
            {"model_id": model_id, "target_id": t, "output_kind": "rank_score",
             "rank_score": result["predictions"][t]}
            for t in targets
        ]
        diagnostics = {k: v for k, v in result.items() if k not in {"predictions", "distance_km"}}
        return rows, diagnostics
    raise CandidateBuildError(f"{model_id}: no adapter")  # unreachable after _eligible_models


def build_candidate(
    source_snapshot_path: pathlib.Path,
    source_cutoff_utc: str,
    *,
    registry_path: pathlib.Path = T.REGISTRY_PATH,
    schedule_path: pathlib.Path = T.SCHEDULE_PATH,
    rounds_dir: pathlib.Path = T.ROUNDS_DIR,
    promotions_dir: pathlib.Path = sitrep_promotions.PROMOTIONS_DIR,
    centroids_path: pathlib.Path = hzc.CENTROIDS_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(candidate, receipt)`` or raise; never writes."""
    cutoff = _utc(source_cutoff_utc, "source_cutoff_utc")
    registry = T.load_registry(registry_path)
    schedule = T.load_schedule(schedule_path)
    rounds = T.load_rounds(rounds_dir, schedule)
    eligible = _eligible_models(registry)
    if len(eligible) < int(schedule["minimum_competitors"]):
        raise CandidateBuildError(
            f"{len(eligible)} scoring-eligible model(s); the schedule requires "
            f"{schedule['minimum_competitors']}"
        )

    snapshot = T._read_json(source_snapshot_path)
    release = T._verified_source_release(snapshot)
    data_day = dt.date.fromisoformat(str(release["snapshot_date"]))
    label = T._relative(source_snapshot_path)
    _guard(label, "release.snapshot_date", release["snapshot_date"], cutoff)
    _guard(label, "data_as_of", snapshot.get("data_as_of"), cutoff)
    _guard(label, "release.source_receipt.published_at", (release.get("source_receipt") or {}).get("published_at"), cutoff)
    _guard(label, "release.review_receipt.reviewed_at", (release.get("review_receipt") or {}).get("reviewed_at"), cutoff)

    promotions, promotion_inputs = _used_promotions(promotions_dir, data_day, cutoff)
    snapshot_promotion = next(
        (p for p in reversed(promotions)
         if p["data_as_of"] == data_day.isoformat() and p["source_id"] == release["source_receipt"]["source_id"]),
        None,
    )
    if snapshot_promotion is None:
        raise CandidateBuildError("no reviewed promotion matches the source snapshot's release")

    index = hzc.CentroidIndex.load_default(centroids_path)
    tables = _zone_tables(promotions, index)
    if not tables or tables[-1]["date"] != data_day.isoformat():
        raise CandidateBuildError("the latest reviewed health-zone table is not dated on the source data day")
    universe = _universe(tables, index, snapshot_promotion)
    targets = universe["targets"]
    if not targets:
        raise CandidateBuildError("the universe rule yields no targets")

    round_id = str(T._next_round(schedule, data_day, rounds)["round_id"])
    model_cutoff = (data_day + dt.timedelta(days=1)).isoformat()
    event_definition = str(schedule["next_round_template"]["event_definition"])
    target_events = [
        {
            "target_id": zone_id,
            "geography_id": f"cod-health-zone:{zone_id}",
            "zone_name": index.zone(zone_id)["name"],
            "province": index.zone(zone_id)["province"],
            "event_definition": event_definition,
        }
        for zone_id in targets
    ]
    predictions: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    for model in eligible:
        rows, diag = _predict(str(model["model_id"]), targets, tables, universe, index, model_cutoff)
        predictions.extend(rows)
        diagnostics[str(model["model_id"])] = diag

    candidate = {
        "expected_round_id": round_id,
        "source_release_id": str(release["release_id"]),
        "eligible_model_ids": [str(m["model_id"]) for m in eligible],
        "target_events": target_events,
        "predictions": predictions,
    }
    contract = T._candidate_contract(candidate)
    inputs = [
        _input_row(registry_path, "model_registry"),
        _input_row(schedule_path, "tournament_schedule"),
        _input_row(centroids_path, "health_zone_centroids"),
        _input_row(
            source_snapshot_path, "source_snapshot",
            data_day=release["snapshot_date"],
            published_at=release["source_receipt"].get("published_at"),
            reviewed_at=(release.get("review_receipt") or {}).get("reviewed_at"),
        ),
        *promotion_inputs,
    ]
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "round_id": round_id,
        "candidate_sha256": T.content_hash(contract),
        "source_availability_cutoff_utc": source_cutoff_utc,
        "source_data_day": data_day.isoformat(),
        "source_release_id": candidate["source_release_id"],
        "source_snapshot_content_sha256": T.content_hash(snapshot),
        "model_cutoff_exclusive": model_cutoff,
        "inputs": inputs,
        "universe": {k: v for k, v in universe.items() if k != "targets"},
        "models": diagnostics,
        "matrix": {
            "models": len(eligible),
            "targets": len(targets),
            "predictions": len(predictions),
        },
    }
    return candidate, receipt


def dry_run_freeze(
    candidate: Mapping[str, Any],
    source_snapshot_path: pathlib.Path,
    *,
    frozen_at: str,
    source_cutoff_utc: str,
    candidate_path: str,
    registry_path: pathlib.Path = T.REGISTRY_PATH,
    schedule_path: pathlib.Path = T.SCHEDULE_PATH,
    control_path: pathlib.Path = T.CONTROL_PATH,
    rounds_dir: pathlib.Path = T.ROUNDS_DIR,
) -> dict[str, Any]:
    """Run the real freeze validation against a synthetic approval; never writes.

    The approval receipt is labelled as a dry run in ``merged_by`` and carries an
    all-zero merge commit. That still passes ``validate_round``; what rejects it as
    a frozen round is the GitHub lookup in ``verify_frozen_round_approval``, which
    ``status`` runs on every persisted round. Keep the manifest out of the rounds
    directory.
    """
    if _utc(frozen_at, "frozen_at") < _utc(source_cutoff_utc, "source_cutoff_utc"):
        raise CandidateBuildError("a freeze cannot precede the source-availability cutoff it relies on")
    schedule = T.load_schedule(schedule_path)
    contract = T._candidate_contract(candidate)
    approval = {
        "approval_api_url": f"https://api.github.com/repos/{schedule['approval_repository']}/pulls/1",
        "merged_at": frozen_at,
        "merged_by": "dry-run-not-an-approval",
        "merge_commit_sha": "0" * 40,
        "candidate_path": candidate_path,
        "candidate_sha256": T.content_hash(contract),
    }
    return T.build_forecast_manifest(
        candidate,
        T._read_json(source_snapshot_path),
        registry=T.load_registry(registry_path),
        schedule=schedule,
        control=T.load_control(control_path),
        existing_rounds=T.load_rounds(rounds_dir, schedule),
        frozen_at=frozen_at,
        approval_receipt=approval,
    )


def render(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _write_atomically(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-snapshot", type=pathlib.Path, default=DEFAULT_SOURCE_SNAPSHOT)
    parser.add_argument("--source-cutoff-utc", required=True,
                        help="latest UTC instant any input may carry, e.g. 2026-10-04T00:00:00Z")
    parser.add_argument("--out-dir", type=pathlib.Path, default=CANDIDATES_DIR)
    parser.add_argument("--registry", type=pathlib.Path, default=T.REGISTRY_PATH)
    parser.add_argument("--dry-run-freeze-at", default=None,
                        help="also run freeze validation at this UTC instant; the manifest goes to a temporary directory")
    args = parser.parse_args(argv)
    errors = (CandidateBuildError, T.TournamentConfigError, base_rate_30d.BaseRateError,
              distance_only_frontier_30d.DistanceRankError, hzc.CentroidError)
    try:
        candidate, receipt = build_candidate(
            args.source_snapshot, args.source_cutoff_utc, registry_path=args.registry
        )
        round_id = candidate["expected_round_id"]
        candidate_path = args.out_dir / f"{round_id}.json"
        manifest = None
        if args.dry_run_freeze_at:
            manifest = dry_run_freeze(
                candidate, args.source_snapshot,
                frozen_at=args.dry_run_freeze_at, source_cutoff_utc=args.source_cutoff_utc,
                candidate_path=T._relative(candidate_path), registry_path=args.registry,
            )
    except errors as exc:
        print(f"candidate build refused: {exc}")
        return 2
    receipt_path = args.out_dir / f"{round_id}.receipt.json"
    _write_atomically(candidate_path, render(candidate))
    _write_atomically(receipt_path, render(receipt))
    print(f"candidate={T._relative(candidate_path)} sha256={receipt['candidate_sha256']} "
          f"models={receipt['matrix']['models']} targets={receipt['matrix']['targets']}")
    print(f"receipt={T._relative(receipt_path)}")
    if manifest is not None:
        out = pathlib.Path(tempfile.mkdtemp(prefix="bdbv-round-dry-run-")) / f"{round_id}.json"
        out.write_text(render(manifest), encoding="utf-8")
        print(f"dry_run_manifest={out} forecast_sha256={manifest['freeze_receipt']['forecast_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
