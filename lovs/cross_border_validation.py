# SPDX-License-Identifier: Apache-2.0
"""Read-only preparation for a country-level prospective validation review.

No freeze, activation, outcome resolution or scoring path exists here. Source
metadata and hashes require review; they do not independently prove source truth.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import stat
from typing import Any

from lovs import distance_only_frontier_30d as distance
from lovs import model_tournament as T
from lovs.forecast import pins_block11 as countries

ROOT = T.REPO_ROOT
PROTOCOL = pathlib.Path("specs/cross-border-validation/protocol.DRAFT.json")
ROLES = frozenset({"country_universe", "country_points", "source_frontier", "travel_hub", "international_registry"})
GEO_ID = "rank.country_distance_frontier_30d.v1"
HUB_ID = "rank.country_international_arriving_seats_28d.v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_SOURCE_POINTS = 1024
MAX_OBSERVATIONS = 400
REVIEWS = frozenset({"protocol_approval", "current_complete_travel_data_and_license",
    "country_round_adapter_and_candidate_promotion", "independent_public_freeze_witness",
    "independent_adjudicator_and_evidence_grace", "effect_power_dependence_and_stopping_design"})


def _path(root: pathlib.Path, value: str) -> pathlib.Path:
    rel = pathlib.Path(value)
    path = (root / rel).resolve()
    if rel.is_absolute() or ".." in rel.parts or not path.is_relative_to(root.resolve()):
        raise ValueError("input path must remain inside the repository")
    return path


def _bytes(path: pathlib.Path) -> bytes:
    # Nonblocking open prevents a substituted FIFO from stalling the prep cadence.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("review input must be a regular file")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("review input exceeds 4 MiB aggregate-file limit")
    return raw


def _read(path: pathlib.Path) -> Any:
    return json.loads(_bytes(path))


def _point(value: Any) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("point must be [latitude, longitude]")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError("point coordinates must be finite numbers")
    if not -90 <= value[0] <= 90 or not -180 <= value[1] <= 180:
        raise ValueError("point coordinates out of range")
    return float(value[0]), float(value[1])


def _validate_protocol(protocol: dict) -> None:
    if (protocol.get("schema_version") != "bdbv-cross-border-validation-protocol/v1"
            or protocol.get("state") != "draft_review_required"):
        raise ValueError("only review-draft protocol is supported; activation needs a separate reviewed change")
    if ([(m["model_id"], m["output_kind"]) for m in protocol["baseline_models"]]
            != [(GEO_ID, "rank_score"), (HUB_ID, "rank_score")]):
        raise ValueError("protocol baseline contracts do not match reviewed adapters")
    count = protocol["country_universe"]["expected_member_count"]
    age = protocol["maximum_frontier_age_days"]
    if (type(count) is not int or not 1 <= count <= 256
            or type(age) is not int or not 1 <= age <= 30):
        raise ValueError("invalid country count or source-frontier age bound")
    if set(protocol["launch_reviews"]) != REVIEWS:
        raise ValueError("all launch review gates must remain present")


def prepare_baselines(protocol: dict, bundle: dict, *, root: pathlib.Path, as_of: str) -> dict:
    """Build both draft ranks from a complete, byte-bound reviewed input bundle."""
    _validate_protocol(protocol)
    if bundle.get("schema_version") != "bdbv-cross-border-feature-bundle/v1":
        raise ValueError("unsupported feature bundle schema")
    cutoff = T._utc_datetime(bundle["cutoff_utc"], "bundle.cutoff_utc")
    if cutoff.time() != dt.time():
        raise ValueError("draft source cutoff must be UTC midnight for date-only country reports")
    # A daily report has no intra-day clock; only inputs known at its UTC start qualify.
    day = T._date(as_of, "as_of")
    if cutoff > dt.datetime.combine(day, dt.time(), dt.timezone.utc):
        raise ValueError("bundle cutoff is later than the daily as-of boundary")
    payloads, receipts = {}, []
    for receipt in bundle["inputs"]:
        role = receipt["role"]
        if role not in ROLES or role in payloads:
            raise ValueError("input roles must be complete and unique")
        path = _path(root, receipt["path"])
        raw = _bytes(path)
        if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
            raise ValueError(f"{role}: input hash mismatch")
        payload = json.loads(raw)
        if (not isinstance(receipt["source_url"], str) or not receipt["source_url"].startswith("https://")
                or not isinstance(receipt["reviewed_by"], str) or not receipt["reviewed_by"].strip()):
            raise ValueError(f"{role}: source URL and reviewer required")
        for field in ("published_at", "reviewed_at"):
            if T.availability_bound(receipt[field], field) > cutoff:
                raise ValueError(f"{role}: {field} later than cutoff")
        payloads[role] = payload
        receipts.append(dict(receipt))
    if set(payloads) != ROLES:
        raise ValueError("all five reviewed input roles are required")

    universe = payloads["country_universe"]
    expected = protocol["country_universe"]["expected_member_count"]
    if (not isinstance(universe, list) or len(universe) != expected
            or len(set(universe)) != len(universe) or "COD" not in universe
            or any(not isinstance(c, str) or not re.fullmatch(r"[A-Z]{3}", c) for c in universe)):
        raise ValueError("country universe must be complete, unique ISO3 codes including COD")
    country_points = payloads["country_points"]
    if set(country_points) != set(universe):
        raise ValueError("country reference points must cover the entire universe")
    points = {c: _point(p) for c, p in country_points.items()}
    registry = payloads["international_registry"]
    countries.validate_registry(registry)
    last_day = cutoff.date() - dt.timedelta(days=1)
    reports = [r for r in countries.effective_reports(registry, last_day)
               if T._date(r["reported_on"], "reported_on") <= last_day]
    affected = {"COD", *(r["country"] for r in reports)}
    targets = sorted(set(universe) - affected)
    if not targets:
        raise ValueError("no countries remain at risk")
    frontier = payloads["source_frontier"]
    if (len(frontier["points"]) > MAX_SOURCE_POINTS
            or len(frontier["observations"]) > MAX_OBSERVATIONS):
        raise ValueError("source frontier exceeds bounded review envelope")
    if set(frontier["points"]) & set(universe):
        raise ValueError("source-zone IDs must not collide with country IDs")
    points.update({z: _point(p) for z, p in frontier["points"].items()})
    geo = distance.predict(targets, frontier["observations"], cutoff=cutoff.date().isoformat(), points=points)
    age = (cutoff.date() - T._date(geo["frontier_latest_observation"])).days
    if age > protocol["maximum_frontier_age_days"]:
        raise ValueError("source frontier is stale at cutoff")

    hub = payloads["travel_hub"]
    if (hub.get("unit") != "international_scheduled_arriving_seats"
            or hub.get("coverage") != "complete_country_universe"):
        raise ValueError("travel input must be reviewed complete international arriving seats")
    if (T._date(hub["window_end"]) != last_day
            or T._date(hub["window_start"]) != cutoff.date() - dt.timedelta(days=28)):
        raise ValueError("travel input must cover exactly 28 complete pre-cutoff days")
    values = hub["countries"]
    if set(values) != set(universe):
        raise ValueError("travel coverage missing countries; unknown is not zero")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values.values()):
        raise ValueError("scheduled seats must be explicit nonnegative integer counts")
    if set(hub.get("reviewed_zero_countries", [])) != {c for c, v in values.items() if v == 0}:
        raise ValueError("every zero-seat country needs explicit coverage review")
    rows = [{"target_id": c, "geography_rank": geo["predictions"][c], "travel_hub_rank": values[c]} for c in targets]
    preview = {"schema_version": "bdbv-cross-border-baseline-preview/v1", "registered": False,
               "cutoff_utc": bundle["cutoff_utc"], "target_ids": targets,
               "excluded_pre_cutoff_countries": sorted(set(universe) & affected),
               "models": [GEO_ID, HUB_ID], "output_kind": "rank_score", "rows": rows,
               "geography_basis": geo, "input_receipts": sorted(receipts, key=lambda r: r["role"]),
               "protocol_sha256": T.content_hash(protocol)}
    preview["preview_sha256"] = T.content_hash(preview)
    return preview


def prep_status(as_of: str, *, root: pathlib.Path = ROOT, bundle_path: str | None = None) -> dict:
    """Observational cadence metadata. Draft gates can never authorize a release."""
    result = {"schema_version": "bdbv-cross-border-validation-readiness/v1", "as_of": as_of,
              "state": "draft_review_required", "launch_ready": False, "registered": False,
              "owner": "bdbv-snapshot-prep-manager", "blockers": [], "baseline_preview": None}
    try:
        day = T._date(as_of, "as_of")
        protocol = _read(_path(root, str(PROTOCOL)))
        _validate_protocol(protocol)
        result["protocol_sha256"] = T.content_hash(protocol)
        if day < T._date(protocol["created_on"], "created_on"):
            result.update(state="not_available_as_of", blockers=["protocol_did_not_exist_at_as_of"])
            return result
        schedule = T.load_schedule(_path(root, protocol["cadence_schedule"]))
        result["cadence"] = {"cadence_days": schedule["cadence_days"], "horizon_days": 30,
                             "timezone": schedule["timezone"], "window_start_policy": schedule["window_start_policy"]}
        result["blockers"] = ["draft_only_no_activation_path", "reviewed_country_round_adapter_required"]
        # Filled review fields remain proposals. This adapter never verifies approval or public witnesses.
        result["blockers"] += ["review_required:" + gate for gate in protocol["launch_reviews"]]
        result["blockers"].append("reviewed_eligible_challenger_required")
        selected = bundle_path or protocol.get("feature_bundle_path")
        if selected:
            bundle = _read(_path(root, selected))
            result["baseline_preview"] = prepare_baselines(protocol, bundle, root=root, as_of=as_of)
        else:
            result["blockers"].append("reviewed_complete_feature_bundle_required")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError,
            countries.RegistrationError) as exc:
        result["state"] = "invalid_review_inputs"
        if isinstance(exc, OSError):
            detail = exc.strerror or "local input could not be read"
        elif isinstance(exc, countries.RegistrationError):
            detail = "country registry failed semantic validation"
        elif isinstance(exc, distance.DistanceRankError):
            detail = "look-ahead: source observations are not pre-cutoff" if str(exc).startswith("look-ahead:") else "source frontier or points failed validation"
        else:
            detail = str(exc)
        result["blockers"].append(f"input_error:{type(exc).__name__}:{detail[:240]}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--bundle", help="repository-relative reviewed aggregate input bundle; never registers a round")
    args = parser.parse_args(argv)
    status = prep_status(args.as_of, bundle_path=args.bundle)
    print(json.dumps(status, sort_keys=True, indent=2, allow_nan=False))
    return 1 if status["state"] == "invalid_review_inputs" else 0


if __name__ == "__main__":
    raise SystemExit(main())
