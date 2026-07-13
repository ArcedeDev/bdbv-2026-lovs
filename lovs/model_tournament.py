# SPDX-License-Identifier: Apache-2.0
"""Prospective, recurring model-tournament contract for BDBV snapshots."""
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import pathlib
import re
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from collections import Counter
from typing import Any, Callable, Iterator, Mapping

from lovs import forecast_scoring
from lovs import lovs_evidence
from lovs import release_contract
from lovs import sitrep_promotions


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TOURNAMENT_DIR = REPO_ROOT / "data" / "model-tournament"
REGISTRY_PATH = TOURNAMENT_DIR / "model-registry.json"
SCHEDULE_PATH = TOURNAMENT_DIR / "schedule.json"
CONTROL_PATH = TOURNAMENT_DIR / "control.json"
ROUNDS_DIR = TOURNAMENT_DIR / "rounds"
RESOLUTIONS_DIR = TOURNAMENT_DIR / "resolutions"
SCORES_DIR = TOURNAMENT_DIR / "scores"
EVIDENCE_REGISTRY_PATH = REPO_ROOT / "data" / "evidence-chains.json"

SNAPSHOT_SCHEMA_VERSION = "bdbv-model-tournament-status/v2"
REGISTRY_SCHEMA_VERSION = "bdbv-model-tournament-registry/v2"
SCHEDULE_SCHEMA_VERSION = "bdbv-model-tournament-schedule/v2"
CONTROL_SCHEMA_VERSION = "bdbv-model-tournament-control/v1"
ROUND_SCHEMA_VERSION = "bdbv-model-tournament-round/v2"
RESOLUTION_SCHEMA_VERSION = "bdbv-model-tournament-resolution/v1"
SCORE_SCHEMA_VERSION = "bdbv-model-tournament-score/v1"

OUTPUT_KINDS = frozenset({"probability", "probability_interval", "rank_score", "descriptive_index"})
SCORED_OUTPUT_KINDS = frozenset({"probability", "probability_interval", "rank_score"})
READINESS_STATES = frozenset({
    "active",
    "eligible_when_round_freezes",
    "planned_review_required",
    "research_only",
    "not_eligible",
})
ELIGIBLE_READINESS = frozenset({"active", "eligible_when_round_freezes"})
RESOLUTION_STATES = frozenset({
    "resolved_yes",
    "resolved_no",
    "unscoreable_no_feed",
    "unscoreable_malformed_evidence",
    "unscoreable_surveillance_dark",
    "unscoreable_conflicting_evidence",
})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_APPROVAL_API_RE = re.compile(r"^https://api\.github\.com/repos/([^/]+/[^/]+)/pulls/([1-9][0-9]*)$")
_RESOLUTION_VERDICTS = frozenset({"supported", "derived_supported"})


class TournamentConfigError(ValueError):
    """Raised when a model-tournament artifact violates its contract."""


class TournamentImmutabilityError(TournamentConfigError):
    """Raised when a create-only artifact would be rewritten."""


def canonical_json(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(doc: Any) -> str:
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()


def _self_hash(doc: Mapping[str, Any], field: str) -> str:
    payload = copy.deepcopy(dict(doc))
    payload.pop(field, None)
    return content_hash(payload)


def forecast_hash(doc: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(doc))
    receipt = payload.get("freeze_receipt")
    if isinstance(receipt, dict):
        receipt.pop("forecast_sha256", None)
    return content_hash(payload)


def _date(value: Any, field: str = "date") -> dt.date:
    text = str(value or "")
    if not _DATE_RE.fullmatch(text):
        raise TournamentConfigError(f"{field} must be an ISO calendar date")
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise TournamentConfigError(f"{field} must be an ISO calendar date") from exc


def _utc_datetime(value: Any, field: str) -> dt.datetime:
    text = str(value or "")
    if not _UTC_DATETIME_RE.fullmatch(text):
        raise TournamentConfigError(f"{field} must be an ISO UTC timestamp ending in Z")
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TournamentConfigError(f"{field} must be an ISO UTC timestamp ending in Z") from exc


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return path.name if path.is_absolute() else str(path)


def _read_json(path: pathlib.Path, *, max_bytes: int = 10_000_000) -> dict[str, Any]:
    label = _relative(path)
    try:
        if path.stat().st_size > max_bytes:
            raise TournamentConfigError(f"tournament artifact exceeds {max_bytes} bytes: {label}")
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TournamentConfigError(f"missing tournament artifact: {label}") from exc
    except json.JSONDecodeError as exc:
        raise TournamentConfigError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(doc, dict):
        raise TournamentConfigError(f"tournament artifact must be a JSON object: {label}")
    return doc


def _safe_id(value: Any, field: str) -> str:
    identifier = _validate_string(value, field)
    if not _SAFE_ID_RE.fullmatch(identifier):
        raise TournamentConfigError(f"{field} must be a lowercase filesystem-safe identifier")
    return identifier


def _artifact_path(directory: pathlib.Path, round_id: str) -> pathlib.Path:
    safe_round_id = _safe_id(round_id, "round_id")
    directory = directory.resolve()
    path = (directory / f"{safe_round_id}.json").resolve()
    if path.parent != directory:
        raise TournamentConfigError("round artifact path escapes its configured directory")
    return path


@contextmanager
def _lifecycle_lock(root: pathlib.Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".lifecycle.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _artifact_ref(path: pathlib.Path, doc: Mapping[str, Any]) -> dict[str, str]:
    return {"path": _relative(path), "sha256": content_hash(doc)}


def _fsync_directory(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_create_only(path: pathlib.Path, doc: Mapping[str, Any]) -> str:
    """Atomically create an immutable JSON artifact; identical replay is a no-op."""
    rendered = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_json(path)
        if canonical_json(existing) == canonical_json(doc):
            return "unchanged"
        raise TournamentImmutabilityError(f"refusing to rewrite immutable artifact: {_relative(path)}")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp_name, path)
        except FileExistsError as exc:
            raise TournamentImmutabilityError(
                f"concurrent create detected for immutable artifact: {_relative(path)}"
            ) from exc
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return "created"


def _write_atomic(path: pathlib.Path, doc: Mapping[str, Any]) -> None:
    rendered = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _as_finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TournamentConfigError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TournamentConfigError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise TournamentConfigError(f"{field} must be finite")
    return result


def _validate_probability(value: Any, field: str) -> float:
    probability = _as_finite_float(value, field)
    if not 0.0 <= probability <= 1.0:
        raise TournamentConfigError(f"{field} must be in [0, 1]")
    return probability


def _validate_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TournamentConfigError(f"{field} must be a non-empty string")
    return value


def _validate_sha(value: Any, field: str) -> str:
    text = str(value or "")
    if not _SHA256_RE.fullmatch(text):
        raise TournamentConfigError(f"{field} must be a lowercase SHA-256")
    return text


def _validate_implementation_module(model_id: str, implementation_module: Any) -> None:
    module = str(implementation_module or "").strip()
    if not module:
        return
    segments = tuple(segment for segment in re.split(r"[./\\]+", module) if segment)
    if "labs" in segments:
        raise TournamentConfigError(f"{model_id}: production registry cannot import labs modules")
    if not segments or segments[0] != "lovs":
        raise TournamentConfigError(
            f"{model_id}: implementation_module must start with production prefix 'lovs'"
        )


def validate_control(doc: Mapping[str, Any]) -> None:
    if doc.get("schema_version") != CONTROL_SCHEMA_VERSION:
        raise TournamentConfigError("tournament control schema_version mismatch")
    if doc.get("state") not in {"enabled", "disabled"}:
        raise TournamentConfigError("tournament control state must be enabled or disabled")
    _utc_datetime(doc.get("updated_at"), "control.updated_at")
    _validate_string(doc.get("updated_by"), "control.updated_by")
    _validate_string(doc.get("reason"), "control.reason")


def validate_registry(doc: Mapping[str, Any]) -> None:
    if doc.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise TournamentConfigError("model registry schema_version mismatch")
    models = doc.get("models")
    if not isinstance(models, list) or not models:
        raise TournamentConfigError("model registry requires a non-empty models list")
    scoring_policy = doc.get("scoring_policy")
    if not isinstance(scoring_policy, dict):
        raise TournamentConfigError("model registry requires scoring_policy")
    minimum = scoring_policy.get("calibration_min_n")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 2:
        raise TournamentConfigError("scoring_policy.calibration_min_n must be an integer >= 2")
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise TournamentConfigError("model registry entries must be objects")
        model_id = _validate_string(model.get("model_id"), "model.model_id")
        if model_id in seen:
            raise TournamentConfigError(f"duplicate model_id: {model_id}")
        seen.add(model_id)
        output_kind = model.get("output_kind")
        readiness = model.get("readiness")
        if output_kind not in OUTPUT_KINDS:
            raise TournamentConfigError(f"{model_id}: unsupported output_kind {output_kind!r}")
        if readiness not in READINESS_STATES:
            raise TournamentConfigError(f"{model_id}: unsupported readiness {readiness!r}")
        _validate_implementation_module(model_id, model.get("implementation_module"))
        scoring_eligible = model.get("scoring_eligible") is True
        if scoring_eligible and readiness not in ELIGIBLE_READINESS:
            raise TournamentConfigError(
                f"{model_id}: readiness {readiness!r} cannot be scoring_eligible"
            )
        if scoring_eligible and output_kind not in SCORED_OUTPUT_KINDS:
            raise TournamentConfigError(f"{model_id}: descriptive output cannot be scoring_eligible")
        if scoring_eligible and not model.get("implementation_module"):
            raise TournamentConfigError(f"{model_id}: scoring-eligible model requires implementation_module")
        if output_kind == "probability" and scoring_eligible:
            if model.get("scoring_transform") != "identity":
                raise TournamentConfigError(f"{model_id}: probability model requires identity transform")
        if output_kind == "probability_interval" and scoring_eligible:
            if model.get("scoring_transform") != "interval_midpoint":
                raise TournamentConfigError(
                    f"{model_id}: probability interval requires preregistered interval_midpoint transform"
                )
        if output_kind == "rank_score" and scoring_eligible:
            if model.get("score_direction") != "higher_is_more_likely":
                raise TournamentConfigError(
                    f"{model_id}: rank model requires higher_is_more_likely direction"
                )


def validate_schedule(doc: Mapping[str, Any]) -> None:
    if doc.get("schema_version") != SCHEDULE_SCHEMA_VERSION:
        raise TournamentConfigError("tournament schedule schema_version mismatch")
    if doc.get("cadence_days") != 30:
        raise TournamentConfigError("tournament schedule cadence_days must be 30")
    _date(doc.get("first_eligible_freeze_date"), "schedule.first_eligible_freeze_date")
    _safe_id(doc.get("round_id_prefix"), "schedule.round_id_prefix")
    minimum = doc.get("minimum_competitors")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 2:
        raise TournamentConfigError("schedule.minimum_competitors must be an integer >= 2")
    if doc.get("window_start_policy") != "next_utc_calendar_day_after_freeze":
        raise TournamentConfigError("schedule.window_start_policy mismatch")
    template = doc.get("next_round_template")
    if not isinstance(template, dict) or template.get("horizon_days") != 30:
        raise TournamentConfigError("schedule next_round_template requires horizon_days=30")
    repository = _validate_string(doc.get("approval_repository"), "schedule.approval_repository")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise TournamentConfigError("schedule.approval_repository must be owner/repository")
    actors = doc.get("approval_merge_actors")
    if not isinstance(actors, list) or not actors or any(not isinstance(v, str) or not v for v in actors):
        raise TournamentConfigError("schedule.approval_merge_actors must be a non-empty string list")
    prefix = _validate_string(
        doc.get("approval_candidate_path_prefix"),
        "schedule.approval_candidate_path_prefix",
    )
    if prefix.startswith("/") or ".." in pathlib.PurePosixPath(prefix).parts or not prefix.endswith("/"):
        raise TournamentConfigError("schedule.approval_candidate_path_prefix must be a relative directory")


def load_control(path: pathlib.Path = CONTROL_PATH) -> dict[str, Any]:
    doc = _read_json(path)
    validate_control(doc)
    return doc


def load_registry(path: pathlib.Path = REGISTRY_PATH) -> dict[str, Any]:
    doc = _read_json(path)
    validate_registry(doc)
    return doc


def load_schedule(path: pathlib.Path = SCHEDULE_PATH) -> dict[str, Any]:
    doc = _read_json(path)
    validate_schedule(doc)
    return doc


def load_evidence_registry(path: pathlib.Path = EVIDENCE_REGISTRY_PATH) -> dict[str, Any]:
    if path.resolve() != EVIDENCE_REGISTRY_PATH.resolve():
        raise TournamentConfigError("resolution evidence must use the canonical LOVS registry")
    if path.stat().st_size > 50_000_000:
        raise TournamentConfigError("evidence registry exceeds 50000000 bytes")
    try:
        doc = lovs_evidence.load_registry(path)
        lovs_evidence.validate_source_anchors(doc, repo_root=REPO_ROOT)
    except (OSError, lovs_evidence.EvidenceChainError) as exc:
        raise TournamentConfigError(f"invalid evidence registry: {exc}") from exc
    return doc


def _evidence_receipts(
    evidence_ids: set[str], evidence_registry: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    try:
        lovs_evidence.validate_registry(dict(evidence_registry))
        lovs_evidence.validate_source_anchors(dict(evidence_registry), repo_root=REPO_ROOT)
    except lovs_evidence.EvidenceChainError as exc:
        raise TournamentConfigError(f"invalid evidence registry: {exc}") from exc
    by_id = {
        str(chain["chain_id"]): chain
        for chain in evidence_registry.get("chains") or []
        if isinstance(chain, dict) and chain.get("chain_id")
    }
    missing = sorted(evidence_ids - set(by_id))
    if missing:
        raise TournamentConfigError(f"unknown evidence_chain_ids: {missing[:5]}")
    return {
        chain_id: {
            "chain_sha256": content_hash(by_id[chain_id]),
            "claim_id": str((by_id[chain_id].get("claim") or {}).get("claim_id") or ""),
            "claim_value": str((by_id[chain_id].get("claim") or {}).get("value") or ""),
            "reviewed_at": str(by_id[chain_id].get("reviewed_at") or ""),
            "verdict": str(by_id[chain_id].get("verdict") or ""),
            "source_tiers": sorted({
                str(source.get("tier"))
                for source in by_id[chain_id].get("sources") or []
                if isinstance(source, dict) and source.get("tier")
            }),
        }
        for chain_id in sorted(evidence_ids)
    }


def _registry_by_model_id(registry_doc: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    validate_registry(registry_doc)
    return {str(model["model_id"]): model for model in registry_doc["models"]}


def _model_contract(model: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "model_id",
        "version",
        "output_kind",
        "scoring_transform",
        "score_direction",
    )
    return {key: model[key] for key in keys if key in model}


def _validate_prediction_value(prediction: Mapping[str, Any], output_kind: str, field: str) -> None:
    if output_kind == "probability":
        _validate_probability(prediction.get("probability"), f"{field}.probability")
    elif output_kind == "probability_interval":
        interval = prediction.get("probability_interval")
        if not isinstance(interval, list) or len(interval) != 2:
            raise TournamentConfigError(f"{field}.probability_interval must be [lower, upper]")
        lo = _validate_probability(interval[0], f"{field}.probability_interval[0]")
        hi = _validate_probability(interval[1], f"{field}.probability_interval[1]")
        if hi < lo:
            raise TournamentConfigError(f"{field}.probability_interval upper < lower")
    elif output_kind == "rank_score":
        _as_finite_float(prediction.get("rank_score"), f"{field}.rank_score")
    else:
        raise TournamentConfigError(f"{field}: unsupported scored output_kind {output_kind!r}")


def validate_round(
    doc: Mapping[str, Any], *, registry_doc: Mapping[str, Any] | None = None
) -> None:
    if doc.get("schema_version") != ROUND_SCHEMA_VERSION:
        raise TournamentConfigError("round schema_version mismatch")
    round_id = _safe_id(doc.get("round_id"), "round.round_id")
    if doc.get("status") != "frozen":
        raise TournamentConfigError(f"{round_id}: persisted forecast status must be frozen")
    if doc.get("horizon_days") != 30:
        raise TournamentConfigError(f"{round_id}: horizon_days must be 30")
    window_start = _date(doc.get("window_start"), f"{round_id}.window_start")
    window_end = _date(doc.get("window_end"), f"{round_id}.window_end")
    if (window_end - window_start).days != 29:
        raise TournamentConfigError(f"{round_id}: inclusive forecast window must contain 30 dates")

    receipt = doc.get("freeze_receipt")
    if not isinstance(receipt, dict):
        raise TournamentConfigError(f"{round_id}: freeze_receipt must be an object")
    frozen_at = _utc_datetime(receipt.get("frozen_at"), f"{round_id}.freeze_receipt.frozen_at")
    if window_start != frozen_at.date() + dt.timedelta(days=1):
        raise TournamentConfigError(f"{round_id}: window_start must be the UTC day after freeze")
    _validate_sha(receipt.get("registry_sha256"), f"{round_id}.freeze_receipt.registry_sha256")
    _validate_sha(receipt.get("schedule_sha256"), f"{round_id}.freeze_receipt.schedule_sha256")
    _validate_sha(receipt.get("candidate_sha256"), f"{round_id}.freeze_receipt.candidate_sha256")
    _validate_sha(receipt.get("source_snapshot_sha256"), f"{round_id}.freeze_receipt.source_snapshot_sha256")
    _validate_sha(receipt.get("source_receipt_sha256"), f"{round_id}.freeze_receipt.source_receipt_sha256")
    _validate_string(receipt.get("source_snapshot_release_id"), f"{round_id}.source_snapshot_release_id")
    source_release = receipt.get("source_release")
    if not isinstance(source_release, dict):
        raise TournamentConfigError(f"{round_id}: freeze_receipt.source_release must be an object")
    if canonical_json(_verified_source_release({"release": source_release})) != canonical_json(source_release):
        raise TournamentConfigError(f"{round_id}: frozen source release is not canonical")
    if source_release.get("release_id") != receipt.get("source_snapshot_release_id"):
        raise TournamentConfigError(f"{round_id}: frozen source release ID mismatch")
    if (source_release.get("source_receipt") or {}).get("sha256") != receipt.get("source_receipt_sha256"):
        raise TournamentConfigError(f"{round_id}: frozen source receipt hash mismatch")
    source_day = _date(receipt.get("source_snapshot_date"), f"{round_id}.source_snapshot_date")
    if source_day > frozen_at.date():
        raise TournamentConfigError(f"{round_id}: source snapshot cannot postdate freeze")
    approval = receipt.get("approval")
    if not isinstance(approval, dict):
        raise TournamentConfigError(f"{round_id}: freeze_receipt.approval must be an object")
    approval_api_url = _validate_string(approval.get("approval_api_url"), f"{round_id}.approval_api_url")
    if _APPROVAL_API_RE.fullmatch(approval_api_url) is None:
        raise TournamentConfigError(f"{round_id}: approval_api_url must identify a GitHub pull request API")
    merged_at = _utc_datetime(approval.get("merged_at"), f"{round_id}.approval.merged_at")
    if merged_at > frozen_at:
        raise TournamentConfigError(f"{round_id}: approval merge cannot postdate freeze")
    _validate_string(approval.get("merged_by"), f"{round_id}.approval.merged_by")
    if not _GIT_OBJECT_RE.fullmatch(str(approval.get("merge_commit_sha") or "")):
        raise TournamentConfigError(f"{round_id}: approval.merge_commit_sha must be a Git object id")
    _validate_string(approval.get("candidate_path"), f"{round_id}.approval.candidate_path")
    if approval.get("candidate_sha256") != receipt.get("candidate_sha256"):
        raise TournamentConfigError(f"{round_id}: approval candidate hash mismatch")
    expected_forecast_hash = forecast_hash(doc)
    if receipt.get("forecast_sha256") != expected_forecast_hash:
        raise TournamentConfigError(f"{round_id}: forecast_sha256 does not match frozen content")

    targets = doc.get("target_events")
    if not isinstance(targets, list) or not targets:
        raise TournamentConfigError(f"{round_id}: target_events must be a non-empty list")
    target_ids: list[str] = []
    for idx, target in enumerate(targets):
        if not isinstance(target, dict):
            raise TournamentConfigError(f"{round_id}: target_events[{idx}] must be an object")
        target_id = _validate_string(target.get("target_id"), f"{round_id}.target_events[{idx}].target_id")
        _validate_string(target.get("geography_id"), f"{round_id}:{target_id}.geography_id")
        _validate_string(target.get("event_definition"), f"{round_id}:{target_id}.event_definition")
        if target_id in target_ids:
            raise TournamentConfigError(f"{round_id}: duplicate target_id {target_id!r}")
        target_ids.append(target_id)

    model_ids = doc.get("eligible_model_ids")
    if not isinstance(model_ids, list) or len(model_ids) < 2:
        raise TournamentConfigError(f"{round_id}: eligible_model_ids requires at least two models")
    if any(not isinstance(value, str) or not value for value in model_ids):
        raise TournamentConfigError(f"{round_id}: eligible_model_ids must contain non-empty strings")
    if len(set(model_ids)) != len(model_ids):
        raise TournamentConfigError(f"{round_id}: duplicate eligible_model_ids")

    contracts = doc.get("model_contracts")
    if not isinstance(contracts, list) or len(contracts) != len(model_ids):
        raise TournamentConfigError(f"{round_id}: model_contracts must cover every eligible model")
    contracts_by_id: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        if not isinstance(contract, dict):
            raise TournamentConfigError(f"{round_id}: model_contract entries must be objects")
        model_id = _validate_string(contract.get("model_id"), f"{round_id}.model_contract.model_id")
        if model_id in contracts_by_id:
            raise TournamentConfigError(f"{round_id}: duplicate model contract {model_id}")
        output_kind = contract.get("output_kind")
        if output_kind not in SCORED_OUTPUT_KINDS:
            raise TournamentConfigError(f"{round_id}:{model_id}: invalid scored output_kind")
        if output_kind == "probability" and contract.get("scoring_transform") != "identity":
            raise TournamentConfigError(f"{round_id}:{model_id}: probability transform must be identity")
        if output_kind == "probability_interval" and contract.get("scoring_transform") != "interval_midpoint":
            raise TournamentConfigError(f"{round_id}:{model_id}: interval transform must be interval_midpoint")
        if output_kind == "rank_score" and contract.get("score_direction") != "higher_is_more_likely":
            raise TournamentConfigError(f"{round_id}:{model_id}: rank direction must be higher_is_more_likely")
        contracts_by_id[model_id] = contract
    if set(contracts_by_id) != set(model_ids):
        raise TournamentConfigError(f"{round_id}: model_contract IDs do not match eligible_model_ids")

    scoring_policy = doc.get("scoring_policy")
    if not isinstance(scoring_policy, dict):
        raise TournamentConfigError(f"{round_id}: scoring_policy must be frozen into the round")
    minimum = scoring_policy.get("calibration_min_n")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 2:
        raise TournamentConfigError(f"{round_id}: scoring_policy.calibration_min_n invalid")

    predictions = doc.get("predictions")
    if not isinstance(predictions, list):
        raise TournamentConfigError(f"{round_id}: predictions must be a list")
    expected_matrix = {(model_id, target_id) for model_id in model_ids for target_id in target_ids}
    observed_matrix: set[tuple[str, str]] = set()
    for idx, prediction in enumerate(predictions):
        if not isinstance(prediction, dict):
            raise TournamentConfigError(f"{round_id}: predictions[{idx}] must be an object")
        model_id = _validate_string(prediction.get("model_id"), f"{round_id}.predictions[{idx}].model_id")
        target_id = _validate_string(prediction.get("target_id"), f"{round_id}.predictions[{idx}].target_id")
        key = (model_id, target_id)
        if key not in expected_matrix:
            raise TournamentConfigError(f"{round_id}: prediction {key!r} is outside frozen matrix")
        if key in observed_matrix:
            raise TournamentConfigError(f"{round_id}: duplicate prediction {key!r}")
        observed_matrix.add(key)
        contract = contracts_by_id[model_id]
        if prediction.get("output_kind") != contract.get("output_kind"):
            raise TournamentConfigError(f"{round_id}:{model_id}: prediction output_kind mismatch")
        _validate_prediction_value(prediction, str(contract["output_kind"]), f"{round_id}:{model_id}:{target_id}")
    if observed_matrix != expected_matrix:
        missing = sorted(expected_matrix - observed_matrix)
        raise TournamentConfigError(f"{round_id}: incomplete common-target prediction matrix: {missing[:5]}")

    candidate_contract = {
        "eligible_model_ids": list(model_ids),
        "expected_round_id": round_id,
        "predictions": copy.deepcopy(doc["predictions"]),
        "source_release_id": receipt["source_snapshot_release_id"],
        "target_events": copy.deepcopy(doc["target_events"]),
    }
    if receipt["candidate_sha256"] != content_hash(candidate_contract):
        raise TournamentConfigError(f"{round_id}: candidate_sha256 does not match frozen candidate")

    if registry_doc is not None:
        registry_models = _registry_by_model_id(registry_doc)
        if receipt.get("registry_sha256") != content_hash(registry_doc):
            raise TournamentConfigError(f"{round_id}: registry_sha256 does not match registry")
        for model_id, contract in contracts_by_id.items():
            model = registry_models.get(model_id)
            if model is None or model.get("scoring_eligible") is not True:
                raise TournamentConfigError(f"{round_id}:{model_id}: model is not scoring-eligible")
            if model.get("readiness") not in ELIGIBLE_READINESS:
                raise TournamentConfigError(f"{round_id}:{model_id}: model readiness is not eligible")
            if _model_contract(model) != contract:
                raise TournamentConfigError(f"{round_id}:{model_id}: frozen model contract differs from registry")


def validate_resolution(
    doc: Mapping[str, Any],
    round_doc: Mapping[str, Any],
    evidence_registry: Mapping[str, Any] | None = None,
) -> None:
    validate_round(round_doc)
    round_id = str(round_doc["round_id"])
    if doc.get("schema_version") != RESOLUTION_SCHEMA_VERSION:
        raise TournamentConfigError(f"{round_id}: resolution schema_version mismatch")
    if doc.get("round_id") != round_id:
        raise TournamentConfigError(f"{round_id}: resolution round_id mismatch")
    if doc.get("forecast_sha256") != round_doc["freeze_receipt"]["forecast_sha256"]:
        raise TournamentConfigError(f"{round_id}: resolution forecast_sha256 mismatch")
    resolved_at = _utc_datetime(doc.get("resolved_at"), f"{round_id}.resolved_at")
    window_end = _date(round_doc["window_end"], f"{round_id}.window_end")
    if resolved_at < dt.datetime.combine(window_end + dt.timedelta(days=1), dt.time(), dt.timezone.utc):
        raise TournamentConfigError(f"{round_id}: resolution cannot finalize before the window closes")
    receipts = doc.get("evidence_receipts")
    if not isinstance(receipts, dict):
        raise TournamentConfigError(f"{round_id}: evidence_receipts must be an object")
    outcomes = doc.get("target_outcomes")
    if not isinstance(outcomes, list):
        raise TournamentConfigError(f"{round_id}: target_outcomes must be a list")
    target_ids = {str(target["target_id"]) for target in round_doc["target_events"]}
    observed: set[str] = set()
    for idx, row in enumerate(outcomes):
        if not isinstance(row, dict):
            raise TournamentConfigError(f"{round_id}: target_outcomes[{idx}] must be an object")
        target_id = _validate_string(row.get("target_id"), f"{round_id}.target_outcomes[{idx}].target_id")
        if target_id not in target_ids or target_id in observed:
            raise TournamentConfigError(f"{round_id}: unknown or duplicate outcome target {target_id!r}")
        observed.add(target_id)
        status = row.get("resolution_status")
        if status not in RESOLUTION_STATES:
            raise TournamentConfigError(f"{round_id}:{target_id}: unsupported resolution_status {status!r}")
        evidence_as_of = _date(row.get("evidence_as_of"), f"{round_id}:{target_id}.evidence_as_of")
        if evidence_as_of > resolved_at.date():
            raise TournamentConfigError(f"{round_id}:{target_id}: evidence_as_of postdates resolution")
        evidence_ids = row.get("evidence_chain_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise TournamentConfigError(f"{round_id}:{target_id}: evidence_chain_ids required")
        if any(not isinstance(value, str) or not value for value in evidence_ids):
            raise TournamentConfigError(f"{round_id}:{target_id}: invalid evidence_chain_ids")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise TournamentConfigError(f"{round_id}:{target_id}: duplicate evidence_chain_ids")
        expected_claim_id = f"claim:lovs:model-tournament:{round_id}:{target_id}"
        for evidence_id in evidence_ids:
            receipt = receipts.get(evidence_id)
            if not isinstance(receipt, dict):
                raise TournamentConfigError(f"{round_id}:{target_id}: missing evidence receipt {evidence_id}")
            _validate_sha(receipt.get("chain_sha256"), f"{round_id}:{evidence_id}.chain_sha256")
            if receipt.get("claim_id") != expected_claim_id:
                raise TournamentConfigError(
                    f"{round_id}:{target_id}: evidence claim_id must be {expected_claim_id}"
                )
            if receipt.get("claim_value") != status:
                raise TournamentConfigError(
                    f"{round_id}:{target_id}: evidence claim value must equal resolution_status"
                )
            if receipt.get("verdict") not in _RESOLUTION_VERDICTS:
                raise TournamentConfigError(f"{round_id}:{target_id}: evidence verdict is not resolution-eligible")
            if "T1_PRIMARY" not in (receipt.get("source_tiers") or []):
                raise TournamentConfigError(f"{round_id}:{target_id}: resolution requires a T1_PRIMARY source")
            reviewed_at_text = str(receipt.get("reviewed_at") or "")
            reviewed_at = (
                _utc_datetime(reviewed_at_text, f"{round_id}:{evidence_id}.reviewed_at")
                if "T" in reviewed_at_text
                else dt.datetime.combine(
                    _date(reviewed_at_text, f"{round_id}:{evidence_id}.reviewed_at"),
                    dt.time.max,
                    dt.timezone.utc,
                )
            )
            if reviewed_at > resolved_at:
                raise TournamentConfigError(f"{round_id}:{target_id}: evidence review postdates resolution")
            if status == "resolved_no" and reviewed_at.date() < window_end:
                raise TournamentConfigError(
                    f"{round_id}:{target_id}: negative resolution evidence predates window end"
                )
        if status == "resolved_yes" and row.get("outcome") != 1:
            raise TournamentConfigError(f"{round_id}:{target_id}: resolved_yes requires outcome 1")
        elif status == "resolved_no" and row.get("outcome") != 0:
            raise TournamentConfigError(f"{round_id}:{target_id}: resolved_no requires outcome 0")
        elif status not in {"resolved_yes", "resolved_no"} and "outcome" in row:
            raise TournamentConfigError(f"{round_id}:{target_id}: unscoreable outcome must be omitted")
        if status == "resolved_no" and evidence_as_of < window_end:
            raise TournamentConfigError(
                f"{round_id}:{target_id}: negative resolution requires end-of-window evidence"
            )
    if observed != target_ids:
        raise TournamentConfigError(f"{round_id}: resolution must cover the complete target universe")
    used_ids = {
        evidence_id
        for row in outcomes
        for evidence_id in row.get("evidence_chain_ids") or []
    }
    if set(receipts) != used_ids:
        raise TournamentConfigError(f"{round_id}: evidence_receipts must exactly match referenced chains")
    if evidence_registry is not None:
        expected_receipts = _evidence_receipts(used_ids, evidence_registry)
        if canonical_json(expected_receipts) != canonical_json(receipts):
            raise TournamentConfigError(f"{round_id}: evidence receipts do not match the reviewed registry")
    if doc.get("resolution_sha256") != _self_hash(doc, "resolution_sha256"):
        raise TournamentConfigError(f"{round_id}: resolution_sha256 does not match content")


def _prediction_value(prediction: Mapping[str, Any], contract: Mapping[str, Any]) -> float:
    kind = str(contract["output_kind"])
    if kind == "probability":
        return _validate_probability(prediction.get("probability"), "probability")
    if kind == "probability_interval":
        if contract.get("scoring_transform") != "interval_midpoint":
            raise TournamentConfigError("probability interval transform is not preregistered")
        lo, hi = prediction["probability_interval"]
        return (_validate_probability(lo, "interval lower") + _validate_probability(hi, "interval upper")) / 2.0
    if kind == "rank_score":
        if contract.get("score_direction") != "higher_is_more_likely":
            raise TournamentConfigError("rank direction is not preregistered")
        return _as_finite_float(prediction.get("rank_score"), "rank_score")
    raise TournamentConfigError(f"unsupported scored output_kind {kind!r}")


def _null_metric(reason: str) -> dict[str, Any]:
    return {"value": None, "reason": reason}


def _metric(value: float, undefined_reason: str) -> dict[str, Any]:
    finite = forecast_scoring.finite_or_none(value)
    return _null_metric(undefined_reason) if finite is None else {"value": round(finite, 6)}


def score_round(round_doc: Mapping[str, Any], resolution_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic score artifact over one shared resolved target set."""
    validate_resolution(resolution_doc, round_doc)
    outcomes_by_target = {
        str(row["target_id"]): int(row["outcome"])
        for row in resolution_doc["target_outcomes"]
        if row["resolution_status"] in {"resolved_yes", "resolved_no"}
    }
    scored_target_ids = sorted(outcomes_by_target)
    resolution_counts = Counter(str(row["resolution_status"]) for row in resolution_doc["target_outcomes"])
    predictions = {
        (str(row["model_id"]), str(row["target_id"])): row
        for row in round_doc["predictions"]
    }
    contracts = {str(row["model_id"]): row for row in round_doc["model_contracts"]}
    calibration_min_n = int(round_doc["scoring_policy"]["calibration_min_n"])
    model_scores: list[dict[str, Any]] = []
    for model_id in round_doc["eligible_model_ids"]:
        contract = contracts[model_id]
        values = tuple(
            _prediction_value(predictions[(model_id, target_id)], contract)
            for target_id in scored_target_ids
        )
        outcomes = tuple(outcomes_by_target[target_id] for target_id in scored_target_ids)
        score: dict[str, Any] = {
            "model_id": model_id,
            "output_kind": contract["output_kind"],
            "n_scored": len(scored_target_ids),
            "n_positive": sum(outcomes),
            "coverage_fraction": round(len(scored_target_ids) / len(round_doc["target_events"]), 6),
        }
        variation_reason = "undefined because scored outcomes contain no class variation"
        if contract["output_kind"] in {"probability", "probability_interval"}:
            score["brier"] = _metric(
                forecast_scoring.mean_brier_score(values, outcomes),
                "undefined because there are no scored probability rows",
            )
            score["brier_skill_score"] = _metric(
                forecast_scoring.brier_skill_score(values, outcomes), variation_reason
            )
            score["roc_auc"] = _metric(forecast_scoring.roc_auc(values, outcomes), variation_reason)
            if len(values) < calibration_min_n:
                score["expected_calibration_error"] = _null_metric(
                    f"undefined below preregistered calibration_min_n={calibration_min_n}"
                )
            else:
                score["expected_calibration_error"] = _metric(
                    forecast_scoring.expected_calibration_error(values, outcomes),
                    "undefined because there are no scored probability rows",
                )
        else:
            score["roc_auc"] = _metric(forecast_scoring.roc_auc(values, outcomes), variation_reason)
        model_scores.append(score)

    artifact: dict[str, Any] = {
        "schema_version": SCORE_SCHEMA_VERSION,
        "round_id": round_doc["round_id"],
        "forecast_sha256": round_doc["freeze_receipt"]["forecast_sha256"],
        "resolution_sha256": resolution_doc["resolution_sha256"],
        "scored_at": resolution_doc["resolved_at"],
        "target_count": len(round_doc["target_events"]),
        "scored_target_count": len(scored_target_ids),
        "scored_target_ids": scored_target_ids,
        "resolution_status_counts": dict(sorted(resolution_counts.items())),
        "comparison_policy": "all models scored on the identical evaluable target subset",
        "uncertainty_policy": "no iid confidence interval; aggregate by round before clustered uncertainty",
        "model_scores": model_scores,
    }
    artifact["score_sha256"] = _self_hash(artifact, "score_sha256")
    return artifact


def validate_score(
    doc: Mapping[str, Any], round_doc: Mapping[str, Any], resolution_doc: Mapping[str, Any]
) -> None:
    expected = score_round(round_doc, resolution_doc)
    if canonical_json(doc) != canonical_json(expected):
        raise TournamentConfigError(f"{round_doc['round_id']}: score artifact is stale or non-deterministic")


def _load_artifact_dir(
    path: pathlib.Path, validator: Any
) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for artifact_path in sorted(path.glob("*.json")):
        doc = _read_json(artifact_path)
        validator(doc)
        artifact_id = str(doc.get("round_id") or "")
        if not artifact_id or artifact_id in result:
            raise TournamentConfigError(f"duplicate or missing round_id in {_relative(artifact_path)}")
        if artifact_path.stem != artifact_id:
            raise TournamentConfigError(f"{_relative(artifact_path)} filename must match round_id")
        result[artifact_id] = doc
    return result


def load_rounds(
    path: pathlib.Path = ROUNDS_DIR,
    schedule: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rounds = _load_artifact_dir(path, validate_round)
    ordered = sorted(rounds.values(), key=lambda row: (str(row["window_start"]), str(row["round_id"])))
    starts = [str(row["window_start"]) for row in ordered]
    if len(starts) != len(set(starts)):
        raise TournamentConfigError("duplicate round window_start")
    if schedule is not None:
        validate_schedule(schedule)
        previous_end: dt.date | None = None
        for ordinal, round_doc in enumerate(ordered, start=1):
            round_id = str(round_doc["round_id"])
            if round_id != _round_id(schedule, ordinal):
                raise TournamentConfigError(f"{round_id}: round ID is out of sequence")
            frozen_day = _utc_datetime(
                round_doc["freeze_receipt"]["frozen_at"], f"{round_id}.frozen_at"
            ).date()
            earliest = (
                previous_end
                if previous_end is not None
                else _date(schedule["first_eligible_freeze_date"], "schedule.first_eligible_freeze_date")
            )
            if frozen_day < earliest:
                raise TournamentConfigError(f"{round_id}: round freezes before its recurring eligibility date")
            start = _date(round_doc["window_start"], f"{round_id}.window_start")
            if previous_end is not None and start <= previous_end:
                raise TournamentConfigError(f"{round_id}: round windows overlap or are out of order")
            previous_end = _date(round_doc["window_end"], f"{round_id}.window_end")
    return ordered


def load_resolutions(
    rounds: list[dict[str, Any]],
    path: pathlib.Path = RESOLUTIONS_DIR,
    evidence_registry: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    by_round = {str(row["round_id"]): row for row in rounds}
    return _load_artifact_dir(
        path,
        lambda doc: validate_resolution(
            doc,
            by_round.get(str(doc.get("round_id")))
            or (_ for _ in ()).throw(TournamentConfigError("resolution references unknown round")),
            evidence_registry,
        ),
    )


def load_scores(
    rounds: list[dict[str, Any]],
    resolutions: Mapping[str, dict[str, Any]],
    path: pathlib.Path = SCORES_DIR,
) -> dict[str, dict[str, Any]]:
    by_round = {str(row["round_id"]): row for row in rounds}
    def validator(doc: Mapping[str, Any]) -> None:
        round_id = str(doc.get("round_id") or "")
        if round_id not in by_round or round_id not in resolutions:
            raise TournamentConfigError("score references unknown or unresolved round")
        validate_score(doc, by_round[round_id], resolutions[round_id])
    return _load_artifact_dir(path, validator)


def _round_id(schedule: Mapping[str, Any], ordinal: int) -> str:
    return f"{schedule['round_id_prefix']}-{ordinal:03d}"


def _next_round(
    schedule: Mapping[str, Any], as_of_day: dt.date, rounds: list[dict[str, Any]]
) -> dict[str, Any]:
    eligible_day = (
        _date(rounds[-1]["window_end"], "round.window_end")
        if rounds
        else _date(schedule["first_eligible_freeze_date"], "schedule.first_eligible_freeze_date")
    )
    days_until = max(0, (eligible_day - as_of_day).days)
    template = schedule["next_round_template"]
    return {
        "round_id": _round_id(schedule, len(rounds) + 1),
        "status": "scheduled" if days_until > 0 else "ready_for_freeze_review",
        "first_eligible_freeze_date": eligible_day.isoformat(),
        "days_until_freeze": days_until,
        "horizon_days": 30,
        "requires_review_before_freeze": True,
        "minimum_competitors": schedule["minimum_competitors"],
        "target_universe_policy": template.get("target_universe_policy"),
        "event_definition": template.get("event_definition"),
        "window_start_policy": schedule["window_start_policy"],
    }


def _round_summary(
    round_doc: Mapping[str, Any], status: str, resolution: Mapping[str, Any] | None, score: Mapping[str, Any] | None
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "round_id": round_doc["round_id"],
        "status": status,
        "window_start": round_doc["window_start"],
        "window_end": round_doc["window_end"],
        "horizon_days": round_doc["horizon_days"],
        "target_count": len(round_doc["target_events"]),
        "model_count": len(round_doc["eligible_model_ids"]),
        "prediction_count": len(round_doc["predictions"]),
        "forecast_sha256": round_doc["freeze_receipt"]["forecast_sha256"],
    }
    if resolution is not None:
        summary["resolution_sha256"] = resolution["resolution_sha256"]
    if score is not None:
        summary["score_sha256"] = score["score_sha256"]
        summary["evaluated_target_count"] = score["scored_target_count"]
    return summary


def _round_groups(
    rounds: list[dict[str, Any]],
    resolutions: Mapping[str, dict[str, Any]],
    scores: Mapping[str, dict[str, Any]],
    as_of_day: dt.date,
) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    groups = {
        "frozen": [],
        "active": [],
        "awaiting_resolution": [],
        "resolved": [],
        "evaluated": [],
    }
    latest_status: str | None = None
    for round_doc in rounds:
        round_id = str(round_doc["round_id"])
        resolution = resolutions.get(round_id)
        score = scores.get(round_id)
        if score is not None:
            status = "evaluated"
        elif resolution is not None:
            status = "resolved"
        elif as_of_day < _date(round_doc["window_start"], f"{round_id}.window_start"):
            status = "frozen"
        elif as_of_day <= _date(round_doc["window_end"], f"{round_id}.window_end"):
            status = "active"
        else:
            status = "awaiting_resolution"
        groups[status].append(_round_summary(round_doc, status, resolution, score))
        latest_status = status
    return groups, latest_status


def _model_summary(model: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "model_id", "label", "family", "version", "output_kind", "readiness",
        "scoring_eligible", "scoring_transform", "score_direction", "status_note",
    )
    return {key: model[key] for key in keys if key in model}


def snapshot_status(
    as_of: str,
    *,
    registry_path: pathlib.Path = REGISTRY_PATH,
    schedule_path: pathlib.Path = SCHEDULE_PATH,
    control_path: pathlib.Path = CONTROL_PATH,
    rounds_dir: pathlib.Path = ROUNDS_DIR,
    resolutions_dir: pathlib.Path = RESOLUTIONS_DIR,
    scores_dir: pathlib.Path = SCORES_DIR,
) -> dict[str, Any]:
    """Project the durable tournament lifecycle into one daily snapshot contract."""
    as_of_day = _date(str(as_of)[:10], "snapshot.as_of")
    try:
        registry = load_registry(registry_path)
        schedule = load_schedule(schedule_path)
        control = load_control(control_path)
        evidence_registry = load_evidence_registry()
        all_rounds = load_rounds(rounds_dir, schedule)
        for round_doc in all_rounds:
            verify_frozen_round_approval(round_doc, schedule)
        all_resolutions = load_resolutions(all_rounds, resolutions_dir, evidence_registry)
        all_scores = load_scores(all_rounds, all_resolutions, scores_dir)
        control_day = _utc_datetime(control["updated_at"], "control.updated_at").date()
        if control_day > as_of_day:
            raise TournamentConfigError("control state did not yet exist at snapshot.as_of")
        rounds = [
            row for row in all_rounds
            if _utc_datetime(row["freeze_receipt"]["frozen_at"], "round.frozen_at").date() <= as_of_day
        ]
        round_ids = {str(row["round_id"]) for row in rounds}
        resolutions = {
            round_id: row for round_id, row in all_resolutions.items()
            if round_id in round_ids
            and _utc_datetime(row["resolved_at"], "resolution.resolved_at").date() <= as_of_day
        }
        scores = {
            round_id: row for round_id, row in all_scores.items()
            if round_id in resolutions
            and _utc_datetime(row["scored_at"], "score.scored_at").date() <= as_of_day
        }
        groups, latest_status = _round_groups(rounds, resolutions, scores, as_of_day)
        next_round = _next_round(schedule, as_of_day, rounds)
    except Exception as exc:
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "evaluated_as_of": as_of_day.isoformat(),
            "status": "invalid",
            "diagnostics": [{
                "severity": "error",
                "code": "model_tournament_artifact_invalid",
                "message": str(exc),
            }],
        }

    models = registry["models"]
    by_readiness = Counter(str(model.get("readiness")) for model in models)
    status = "disabled" if control["state"] == "disabled" else (
        next_round["status"]
        if next_round["status"] == "ready_for_freeze_review"
        else latest_status or next_round["status"]
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "outbreak_id": registry.get("outbreak_id"),
        "evaluated_as_of": as_of_day.isoformat(),
        "status": status,
        "control": dict(control),
        "cadence": {
            "cadence_days": schedule["cadence_days"],
            "timezone": schedule.get("timezone", "UTC"),
            "release_gate": schedule.get("release_gate"),
            "window_start_policy": schedule["window_start_policy"],
        },
        "artifacts": {
            "registry": _artifact_ref(registry_path, registry),
            "schedule": _artifact_ref(schedule_path, schedule),
            "control": _artifact_ref(control_path, control),
            "rounds_dir": _relative(rounds_dir),
            "resolutions_dir": _relative(resolutions_dir),
            "scores_dir": _relative(scores_dir),
        },
        "model_registry": {
            "model_count": len(models),
            "eligible_model_count": sum(model.get("scoring_eligible") is True for model in models),
            "by_readiness": dict(sorted(by_readiness.items())),
            "models": [_model_summary(model) for model in models],
        },
        "scoring_policy": registry["scoring_policy"],
        "next_eligible_round": next_round,
        "rounds": {"count": len(rounds), **groups},
        "honesty_notes": list(registry.get("honesty_notes") or []),
    }


def _candidate_contract(candidate: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "expected_round_id",
        "source_release_id",
        "eligible_model_ids",
        "target_events",
        "predictions",
    }
    extras = sorted(set(candidate) - allowed)
    if extras:
        raise TournamentConfigError(f"forecast candidate has unsupported fields: {extras}")
    contract = {key: copy.deepcopy(candidate.get(key)) for key in sorted(allowed)}
    _safe_id(contract.get("expected_round_id"), "candidate.expected_round_id")
    _validate_string(contract.get("source_release_id"), "candidate.source_release_id")
    models = contract.get("eligible_model_ids")
    targets = contract.get("target_events")
    predictions = contract.get("predictions")
    if not isinstance(models, list) or len(models) > 100:
        raise TournamentConfigError("candidate.eligible_model_ids must contain at most 100 models")
    if not isinstance(targets, list) or not targets or len(targets) > 10_000:
        raise TournamentConfigError("candidate.target_events must contain 1 to 10000 targets")
    if not isinstance(predictions, list) or len(predictions) > 1_000_000:
        raise TournamentConfigError("candidate.predictions must contain at most 1000000 rows")
    return contract


def _verified_source_release(source_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    release = source_snapshot.get("release")
    if not isinstance(release, dict) or release.get("publication_state") != "published":
        raise TournamentConfigError("source snapshot requires a published release envelope")
    snapshot_date = str(release.get("snapshot_date") or "")
    matches = [
        promotion
        for promotion in sitrep_promotions.load_reviewed_promotions()
        if str(promotion.get("data_as_of")) == snapshot_date
        and promotion.get("publication_state") == "published"
    ]
    for promotion in reversed(matches):
        try:
            expected = release_contract.build_release_envelope(promotion)
        except release_contract.ReleaseContractError:
            continue
        if canonical_json(expected) == canonical_json(release):
            return expected
    raise TournamentConfigError(
        "source snapshot release does not match a byte-verified reviewed promotion"
    )


def _github_payload(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "bdbv-model-tournament"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise TournamentConfigError(f"GitHub approval lookup returned HTTP {response.status}")
            payload = json.loads(response.read(2_000_001))
    except (OSError, ValueError) as exc:
        raise TournamentConfigError(f"GitHub approval lookup failed: {exc}") from exc
    return payload


def _github_json(url: str) -> dict[str, Any]:
    payload = _github_payload(url)
    if not isinstance(payload, dict):
        raise TournamentConfigError("GitHub approval response must be a JSON object")
    return payload


def _github_list(url: str) -> list[dict[str, Any]]:
    payload = _github_payload(url)
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise TournamentConfigError("GitHub approval response must be a JSON object list")
    return payload


def verify_github_pr_approval(
    approval_api_url: str,
    candidate_path: pathlib.Path,
    candidate: Mapping[str, Any],
    schedule: Mapping[str, Any],
    frozen_time: dt.datetime,
) -> dict[str, Any]:
    """Verify that the exact candidate was merged by an approved actor before freeze."""
    match = _APPROVAL_API_RE.fullmatch(approval_api_url)
    repository = str(schedule["approval_repository"])
    if match is None or match.group(1).casefold() != repository.casefold():
        raise TournamentConfigError("approval URL must be a pull request API URL for the configured repository")
    pr = _github_json(approval_api_url)
    if pr.get("merged_at") is None or pr.get("merge_commit_sha") is None:
        raise TournamentConfigError("approval pull request must be merged")
    merged_at = _utc_datetime(pr["merged_at"], "approval.merged_at")
    if merged_at > frozen_time:
        raise TournamentConfigError("approval pull request merge time is later than freeze time")
    merged_by = str((pr.get("merged_by") or {}).get("login") or "")
    if merged_by not in set(schedule["approval_merge_actors"]):
        raise TournamentConfigError("approval pull request was not merged by an approved actor")
    full_name = str((pr.get("base") or {}).get("repo", {}).get("full_name") or "")
    if full_name.casefold() != repository.casefold():
        raise TournamentConfigError("approval pull request base repository mismatch")
    try:
        relative_path = candidate_path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise TournamentConfigError("approved candidate must be inside the generator repository") from exc
    if not relative_path.startswith(str(schedule["approval_candidate_path_prefix"])):
        raise TournamentConfigError("approved candidate is outside the configured candidate directory")
    files = _github_list(f"{approval_api_url}/files?per_page=100")
    if len(files) >= 100:
        raise TournamentConfigError("approval pull request file list is too large to verify safely")
    changed_paths = {
        str(row.get("filename") or "")
        for row in files
        if row.get("status") in {"added", "modified", "renamed"}
    }
    if relative_path not in changed_paths:
        raise TournamentConfigError("approval pull request did not change the candidate artifact")
    commit_sha = str(pr["merge_commit_sha"])
    contents_url = (
        f"https://api.github.com/repos/{repository}/contents/"
        f"{urllib.parse.quote(relative_path, safe='/')}?ref={urllib.parse.quote(commit_sha)}"
    )
    remote = _github_json(contents_url)
    if remote.get("encoding") != "base64" or not isinstance(remote.get("content"), str):
        raise TournamentConfigError("approved candidate content response is not base64 JSON")
    try:
        remote_candidate = json.loads(base64.b64decode(remote["content"]).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise TournamentConfigError("approved remote candidate is not valid JSON") from exc
    local_contract = _candidate_contract(candidate)
    if canonical_json(_candidate_contract(remote_candidate)) != canonical_json(local_contract):
        raise TournamentConfigError("local candidate differs from the merged approval candidate")
    return {
        "approval_api_url": approval_api_url,
        "merged_at": pr["merged_at"],
        "merged_by": merged_by,
        "merge_commit_sha": commit_sha,
        "candidate_path": relative_path,
        "candidate_sha256": content_hash(local_contract),
    }


def verify_frozen_round_approval(
    round_doc: Mapping[str, Any], schedule: Mapping[str, Any]
) -> None:
    """Revalidate a persisted round against its merged GitHub candidate."""
    receipt = round_doc["freeze_receipt"]
    approval = receipt["approval"]
    candidate_path = REPO_ROOT / str(approval["candidate_path"])
    candidate = {
        "expected_round_id": round_doc["round_id"],
        "source_release_id": receipt["source_snapshot_release_id"],
        "eligible_model_ids": round_doc["eligible_model_ids"],
        "target_events": round_doc["target_events"],
        "predictions": round_doc["predictions"],
    }
    verified = verify_github_pr_approval(
        str(approval["approval_api_url"]),
        candidate_path,
        candidate,
        schedule,
        _utc_datetime(receipt["frozen_at"], "freeze_receipt.frozen_at"),
    )
    if canonical_json(verified) != canonical_json(approval):
        raise TournamentConfigError(f"{round_doc['round_id']}: remote approval receipt drift")


def build_forecast_manifest(
    candidate: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    *,
    registry: Mapping[str, Any],
    schedule: Mapping[str, Any],
    control: Mapping[str, Any],
    existing_rounds: list[dict[str, Any]],
    frozen_at: str,
    approval_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    validate_registry(registry)
    validate_schedule(schedule)
    validate_control(control)
    if control["state"] != "enabled":
        raise TournamentConfigError("cannot freeze a round while tournament control is disabled")
    frozen_time = _utc_datetime(frozen_at, "frozen_at")
    next_round = _next_round(schedule, frozen_time.date(), existing_rounds)
    if frozen_time.date() < _date(next_round["first_eligible_freeze_date"], "eligible freeze date"):
        raise TournamentConfigError("round freeze is earlier than the next eligible freeze date")
    expected_round_id = str(next_round["round_id"])
    candidate_contract = _candidate_contract(candidate)
    if candidate_contract["expected_round_id"] != expected_round_id:
        raise TournamentConfigError("candidate expected_round_id does not match recurring schedule")

    model_ids = candidate_contract.get("eligible_model_ids")
    if not isinstance(model_ids, list) or len(model_ids) < int(schedule["minimum_competitors"]):
        raise TournamentConfigError("candidate does not meet schedule.minimum_competitors")
    registry_models = _registry_by_model_id(registry)
    contracts: list[dict[str, Any]] = []
    for model_id in model_ids:
        model = registry_models.get(str(model_id))
        if model is None or model.get("scoring_eligible") is not True:
            raise TournamentConfigError(f"{model_id}: model is not scoring-eligible")
        if model.get("readiness") not in ELIGIBLE_READINESS:
            raise TournamentConfigError(f"{model_id}: model readiness is not eligible")
        contracts.append(_model_contract(model))

    release = _verified_source_release(source_snapshot)
    source_release_id = _validate_string(release.get("release_id"), "source release_id")
    if candidate_contract["source_release_id"] != source_release_id:
        raise TournamentConfigError("candidate source_release_id does not match source snapshot")
    source_snapshot_date = _date(release.get("snapshot_date"), "source snapshot_date")
    window_start = frozen_time.date() + dt.timedelta(days=1)
    window_end = window_start + dt.timedelta(days=29)
    artifact: dict[str, Any] = {
        "schema_version": ROUND_SCHEMA_VERSION,
        "round_id": expected_round_id,
        "status": "frozen",
        "horizon_days": 30,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "eligible_model_ids": list(model_ids),
        "model_contracts": contracts,
        "scoring_policy": copy.deepcopy(registry["scoring_policy"]),
        "target_events": candidate_contract["target_events"],
        "predictions": candidate_contract["predictions"],
        "freeze_receipt": {
            "frozen_at": frozen_at,
            "forecast_sha256": "",
            "candidate_sha256": content_hash(candidate_contract),
            "registry_sha256": content_hash(registry),
            "schedule_sha256": content_hash(schedule),
            "source_snapshot_sha256": content_hash(source_snapshot),
            "source_snapshot_release_id": source_release_id,
            "source_snapshot_date": source_snapshot_date.isoformat(),
            "source_receipt_sha256": release["source_receipt"]["sha256"],
            "source_release": copy.deepcopy(release),
            "approval": copy.deepcopy(dict(approval_receipt)),
        },
    }
    artifact["freeze_receipt"]["forecast_sha256"] = forecast_hash(artifact)
    validate_round(artifact, registry_doc=registry)
    return artifact


def build_resolution(
    candidate: Mapping[str, Any],
    round_doc: Mapping[str, Any],
    evidence_registry: Mapping[str, Any],
) -> dict[str, Any]:
    outcomes = copy.deepcopy(candidate.get("target_outcomes"))
    evidence_ids = {
        str(evidence_id)
        for row in outcomes or []
        if isinstance(row, dict)
        for evidence_id in row.get("evidence_chain_ids") or []
    }
    artifact: dict[str, Any] = {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "round_id": round_doc["round_id"],
        "forecast_sha256": round_doc["freeze_receipt"]["forecast_sha256"],
        "resolved_at": candidate.get("resolved_at"),
        "target_outcomes": outcomes,
        "evidence_receipts": _evidence_receipts(evidence_ids, evidence_registry),
    }
    artifact["resolution_sha256"] = _self_hash(artifact, "resolution_sha256")
    validate_resolution(artifact, round_doc, evidence_registry)
    return artifact


def set_control(
    state: str, *, updated_by: str, reason: str, updated_at: str | None = None,
    path: pathlib.Path = CONTROL_PATH,
) -> dict[str, Any]:
    artifact = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "state": state,
        "updated_at": updated_at or _utc_now(),
        "updated_by": updated_by,
        "reason": reason,
    }
    validate_control(artifact)
    _write_atomic(path, artifact)
    return artifact


def _paths(root: pathlib.Path) -> dict[str, pathlib.Path]:
    return {
        "registry": root / "model-registry.json",
        "schedule": root / "schedule.json",
        "control": root / "control.json",
        "rounds": root / "rounds",
        "resolutions": root / "resolutions",
        "scores": root / "scores",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=TOURNAMENT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--as-of", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--candidate", type=pathlib.Path, required=True)
    freeze.add_argument("--source-snapshot", type=pathlib.Path, required=True)
    freeze.add_argument("--approval-pr-api", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--round-id", required=True)
    resolve.add_argument("--candidate", type=pathlib.Path, required=True)
    score = sub.add_parser("score")
    score.add_argument("--round-id", required=True)
    control = sub.add_parser("control")
    control.add_argument("--state", choices=("enabled", "disabled"), required=True)
    control.add_argument("--updated-by", required=True)
    control.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    paths = _paths(args.root)
    if args.command == "status":
        status = snapshot_status(
            args.as_of,
            registry_path=paths["registry"], schedule_path=paths["schedule"],
            control_path=paths["control"], rounds_dir=paths["rounds"],
            resolutions_dir=paths["resolutions"], scores_dir=paths["scores"],
        )
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0 if status["status"] != "invalid" else 2
    with _lifecycle_lock(args.root):
        if args.command == "control":
            artifact = set_control(
                args.state, updated_by=args.updated_by, reason=args.reason,
                path=paths["control"],
            )
            print(json.dumps(artifact, sort_keys=True))
            return 0

        registry = load_registry(paths["registry"])
        schedule = load_schedule(paths["schedule"])
        control = load_control(paths["control"])
        evidence_registry = load_evidence_registry()
        rounds = load_rounds(paths["rounds"], schedule)
        rounds_by_id = {str(row["round_id"]): row for row in rounds}
        if args.command == "freeze":
            candidate = _read_json(args.candidate)
            frozen_at = _utc_now()
            approval = verify_github_pr_approval(
                args.approval_pr_api,
                args.candidate,
                candidate,
                schedule,
                _utc_datetime(frozen_at, "frozen_at"),
            )
            artifact = build_forecast_manifest(
                candidate, _read_json(args.source_snapshot),
                registry=registry, schedule=schedule, control=control,
                existing_rounds=rounds, frozen_at=frozen_at,
                approval_receipt=approval,
            )
            path = _artifact_path(paths["rounds"], str(artifact["round_id"]))
            result = _write_create_only(path, artifact)
            print(f"forecast_{result}={_relative(path)} sha256={artifact['freeze_receipt']['forecast_sha256']}")
            return 0
        round_doc = rounds_by_id.get(args.round_id)
        if round_doc is None:
            raise TournamentConfigError(f"unknown round_id: {args.round_id}")
        verify_frozen_round_approval(round_doc, schedule)
        if args.command == "resolve":
            artifact = build_resolution(
                _read_json(args.candidate), round_doc, evidence_registry
            )
            path = _artifact_path(paths["resolutions"], args.round_id)
            result = _write_create_only(path, artifact)
            print(f"resolution_{result}={_relative(path)} sha256={artifact['resolution_sha256']}")
            return 0
        resolutions = load_resolutions(rounds, paths["resolutions"], evidence_registry)
        resolution = resolutions.get(args.round_id)
        if resolution is None:
            raise TournamentConfigError(f"round has no resolution artifact: {args.round_id}")
        artifact = score_round(round_doc, resolution)
        path = _artifact_path(paths["scores"], args.round_id)
        result = _write_create_only(path, artifact)
        print(f"score_{result}={_relative(path)} sha256={artifact['score_sha256']}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
