"""Deterministic release identity derived from one reviewed SitRep receipt."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import pathlib
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from lovs import lovs_convergence
from lovs import sitrep_promotions


SCHEMA_VERSION = "bdbv-release/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PUBLICATION_STATES = {"candidate", "published"}
_RAW_ARCHIVE_DIR = pathlib.Path(__file__).parents[1] / "data" / "bundibugyo-2026" / "raw"
_REQUIRED_RECEIPT_FIELDS = {
    "source_url",
    "sha256",
    "byte_length",
    "page_count",
    "post_id",
    "media_id",
}


class ReleaseContractError(ValueError):
    """Raised when a reviewed promotion cannot identify a reproducible release."""


def _validated_receipt(promotion: Mapping[str, Any]) -> dict[str, Any]:
    receipt = promotion.get("source_receipt")
    if not isinstance(receipt, dict):
        raise ReleaseContractError("reviewed promotion has no structured source_receipt")
    missing = sorted(_REQUIRED_RECEIPT_FIELDS - set(receipt))
    if missing:
        raise ReleaseContractError(f"source_receipt missing fields {missing}")
    source_url = str(receipt.get("source_url") or "")
    parsed_url = urlparse(source_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ReleaseContractError("source_receipt.source_url must be an https URL")
    promotion_url = str(promotion.get("source_url") or "")
    if promotion_url and source_url != promotion_url:
        raise ReleaseContractError("source_receipt.source_url does not match promotion source_url")
    sha256 = str(receipt.get("sha256") or "").lower()
    if not _SHA256_RE.fullmatch(sha256):
        raise ReleaseContractError("source_receipt.sha256 must be 64 lowercase hex characters")
    for field in ("byte_length", "page_count", "post_id", "media_id"):
        value = receipt.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ReleaseContractError(f"source_receipt.{field} must be a positive integer")
    raw_path = _RAW_ARCHIVE_DIR / sha256
    if not raw_path.is_file():
        raise ReleaseContractError(
            "source_receipt.sha256 has no matching local raw archive; "
            "receipt-era releases cannot be constructed without source bytes"
        )
    if raw_path.stat().st_size != receipt["byte_length"]:
        raise ReleaseContractError(
            "source_receipt.byte_length does not match local raw archive"
        )
    with raw_path.open("rb") as raw_fh:
        archived_sha256 = hashlib.file_digest(raw_fh, "sha256").hexdigest()
    if archived_sha256 != sha256:
        raise ReleaseContractError(
            "source_receipt.sha256 does not match local raw archive bytes"
        )
    return {
        "source_id": str(promotion["source_id"]),
        "source_url": source_url,
        "published_at": str(promotion["published_at"]),
        "sha256": sha256,
        "byte_length": receipt["byte_length"],
        "page_count": receipt["page_count"],
        "post_id": receipt["post_id"],
        "media_id": receipt["media_id"],
    }


def build_release_envelope(promotion: dict[str, Any]) -> dict[str, Any]:
    """Build the release envelope without hashing self-referential snapshot bytes."""
    sitrep_promotions.validate_promotion(promotion, require_reviewed=True)
    receipt = _validated_receipt(promotion)
    edition = int(promotion["sitrep_number"])
    snapshot_date = str(promotion["data_as_of"])
    if not _ISO_DAY_RE.fullmatch(snapshot_date):
        raise ReleaseContractError("promotion.data_as_of must be an ISO calendar date")
    try:
        dt.date.fromisoformat(snapshot_date)
    except ValueError as exc:
        raise ReleaseContractError(
            "promotion.data_as_of must be an ISO calendar date"
        ) from exc
    publication_state = str(promotion.get("publication_state") or "")
    if publication_state not in _PUBLICATION_STATES:
        raise ReleaseContractError(
            "promotion.publication_state must be candidate or published"
        )
    release_id = f"bdbv-sr{edition:03d}-{snapshot_date}-{receipt['sha256'][:16]}"
    review = promotion["review"]
    return {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "edition": edition,
        "snapshot_date": snapshot_date,
        "readiness": "reviewed",
        "publication_state": publication_state,
        "source_receipt": receipt,
        "review_receipt": {
            "evidence_chain_id": str(review["evidence_chain_id"]),
            "reviewed_at": str(review["reviewed_at"]),
            "reviewed_by": str(review["reviewed_by"]),
        },
    }


def enrich_snapshot(
    snapshot: Mapping[str, Any], promotion: dict[str, Any]
) -> dict[str, Any]:
    """Add release/estimate contracts without rerunning stochastic model modules."""
    output = copy.deepcopy(dict(snapshot))
    release = build_release_envelope(promotion)
    snapshot_date = str(output.get("as_of") or output.get("data_as_of") or "")[:10]
    if release["snapshot_date"] > snapshot_date:
        raise ReleaseContractError(
            "release source date cannot be later than materialized snapshot as_of"
        )
    convergence = output.get("convergence")
    if not isinstance(convergence, dict):
        raise ReleaseContractError("materialized snapshot has no convergence block")
    output["release"] = release
    lovs_convergence.enrich_estimate_contract(convergence)
    return output


def maybe_enrich_snapshot(
    snapshot: Mapping[str, Any], promotion: dict[str, Any]
) -> dict[str, Any]:
    """Enrich receipt-era releases while preserving older snapshot contracts."""
    if "source_receipt" not in promotion:
        return copy.deepcopy(dict(snapshot))
    return enrich_snapshot(snapshot, promotion)
