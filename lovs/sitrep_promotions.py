"""Reviewed INSP SitRep promotion payloads."""
from __future__ import annotations

import json
import pathlib
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMOTIONS_DIR = REPO_ROOT / "data" / "sitrep_promotions"
CANDIDATES_DIR = PROMOTIONS_DIR / "candidates"
SCHEMA_VERSION = "sitrep-promotion/v1"

REQUIRED_TOP_LEVEL = {
    "schema_version",
    "status",
    "sitrep_number",
    "source_id",
    "data_as_of",
    "published_at",
    "figures",
    "review",
}
REQUIRED_REVIEW_FIELDS = {
    "ready_for_model_use",
    "source_review_status",
    "reviewed_by",
    "reviewed_at",
    "evidence_chain_id",
}
REQUIRED_FIGURES = {
    15: {
        "cumul_cas_confirmes_drc",
        "cumul_deces_parmi_confirmes_drc",
        "cumul_cas_suspects",
        "gueris",
        "country_scope_confirmed_total",
        "country_scope_confirmed_deaths",
    },
    16: {
        "cumul_cas_confirmes_drc",
        "cas_confirmes_actifs_drc",
        "cumul_deces_parmi_confirmes_drc",
        "cas_suspects_en_cours_investigation",
        "cas_suspects_en_isolement",
        "suspected_active_total",
        "gueris",
        "country_scope_confirmed_total",
        "country_scope_confirmed_deaths",
    },
    17: {
        "cumul_cas_confirmes_drc",
        "cas_confirmes_actifs_drc",
        "cas_confirmes_actifs_drc_pdf_cell_rejected",
        "cumul_deces_parmi_confirmes_drc",
        "cas_suspects_en_cours_investigation",
        "cas_suspects_en_isolement",
        "suspected_active_total",
        "gueris",
        "country_scope_confirmed_total",
        "country_scope_confirmed_active",
        "country_scope_confirmed_deaths",
    },
    18: {
        "cumul_cas_confirmes_drc",
        "cumul_deces_parmi_confirmes_drc",
        "cas_suspects_en_cours_investigation",
        "cas_suspects_en_isolement",
        "suspected_active_total",
        "deaths_suspected_drc",
        "lab_indicators_24h",
        "country_scope_confirmed_total",
        "country_scope_confirmed_deaths",
        "country_scope_probable_total",
        "country_scope_probable_deaths",
    },
    19: {
        "cumul_cas_confirmes_drc",
        "cumul_deces_parmi_confirmes_drc",
        "gueris",
        "patients_en_isolement_hospitalisation",
        "cas_confirmes_en_isolement",
        "cas_suspects_en_isolement",
        "contact_followup_rate_pct",
        "country_scope_confirmed_total",
        "country_scope_confirmed_deaths",
        "health_zone_table",
        "operational_tables",
    },
    20: {
        "cumul_cas_confirmes_drc",
        "cumul_deces_parmi_confirmes_drc",
        "gueris",
        "patients_en_isolement_hospitalisation",
        "cas_confirmes_en_isolement",
        "cas_suspects_en_isolement",
        "contact_followup_rate_pct",
        "country_scope_confirmed_total",
        "country_scope_confirmed_deaths",
        "health_zone_table",
        "operational_tables",
    },
}
REQUIRED_LAB_FIELDS = {"samples_analyzed", "samples_positive"}


class SitRepPromotionError(ValueError):
    """Raised when a promotion payload is incomplete or unsafe for model use."""


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SitRepPromotionError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SitRepPromotionError(f"{path}: payload must be an object")
    return payload


def _require_string(payload: dict[str, Any], key: str, path: pathlib.Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SitRepPromotionError(f"{path}: {key} must be a non-empty string")
    return value


def validate_promotion(
    payload: dict[str, Any],
    *,
    path: pathlib.Path = pathlib.Path("<memory>"),
    require_reviewed: bool = False,
) -> dict[str, Any]:
    missing = sorted(REQUIRED_TOP_LEVEL - set(payload))
    if missing:
        raise SitRepPromotionError(f"{path}: missing top-level fields {missing}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise SitRepPromotionError(f"{path}: schema_version must be {SCHEMA_VERSION!r}")
    if payload["status"] not in {"reviewed", "candidate"}:
        raise SitRepPromotionError(f"{path}: status must be reviewed or candidate")
    sitrep_number = payload.get("sitrep_number")
    if not isinstance(sitrep_number, int) or sitrep_number <= 0:
        raise SitRepPromotionError(f"{path}: sitrep_number must be a positive integer")
    _require_string(payload, "source_id", path)
    _require_string(payload, "data_as_of", path)
    _require_string(payload, "published_at", path)
    figures = payload.get("figures")
    if not isinstance(figures, dict):
        raise SitRepPromotionError(f"{path}: figures must be an object")
    missing_figures = sorted(REQUIRED_FIGURES.get(sitrep_number, set()) - set(figures))
    if missing_figures and payload["status"] == "reviewed":
        raise SitRepPromotionError(f"{path}: missing reviewed figures {missing_figures}")
    lab = figures.get("lab_indicators_24h")
    if lab is not None:
        if not isinstance(lab, dict):
            raise SitRepPromotionError(f"{path}: lab_indicators_24h must be an object")
        missing_lab = sorted(REQUIRED_LAB_FIELDS - set(lab))
        if missing_lab:
            raise SitRepPromotionError(f"{path}: missing lab fields {missing_lab}")
    review = payload.get("review")
    if not isinstance(review, dict):
        raise SitRepPromotionError(f"{path}: review must be an object")
    missing_review = sorted(REQUIRED_REVIEW_FIELDS - set(review))
    if missing_review:
        raise SitRepPromotionError(f"{path}: missing review fields {missing_review}")
    if payload["status"] == "candidate":
        if review.get("ready_for_model_use") is True:
            raise SitRepPromotionError(f"{path}: candidate payload cannot be model-ready")
        if review.get("source_review_status") == "reviewed":
            raise SitRepPromotionError(f"{path}: candidate payload cannot be reviewed")
    if require_reviewed:
        if payload["status"] != "reviewed":
            raise SitRepPromotionError(f"{path}: candidate payload cannot be used by the model")
        if review.get("ready_for_model_use") is not True:
            raise SitRepPromotionError(f"{path}: review.ready_for_model_use must be true")
        if review.get("source_review_status") != "reviewed":
            raise SitRepPromotionError(f"{path}: review.source_review_status must be reviewed")
        for key in ("reviewed_by", "reviewed_at", "evidence_chain_id"):
            if not isinstance(review.get(key), str) or not review[key].strip():
                raise SitRepPromotionError(f"{path}: review.{key} must be a non-empty string")
    return payload


def load_reviewed_promotions(directory: pathlib.Path = PROMOTIONS_DIR) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        rows.append(validate_promotion(_load_json(path), path=path, require_reviewed=True))
    if not rows:
        raise SitRepPromotionError(f"{directory}: no reviewed SitRep promotion payloads found")
    return sorted(rows, key=lambda row: (row["data_as_of"], row["sitrep_number"]))


def reviewed_promotions_by_number(
    directory: pathlib.Path = PROMOTIONS_DIR,
) -> dict[int, dict[str, Any]]:
    by_number: dict[int, dict[str, Any]] = {}
    for row in load_reviewed_promotions(directory):
        number = int(row["sitrep_number"])
        if number in by_number:
            raise SitRepPromotionError(f"duplicate reviewed SitRep promotion #{number}")
        by_number[number] = row
    return by_number


def candidate_payload_from_sidecar(meta: dict[str, Any]) -> dict[str, Any]:
    normalized = meta.get("normalized_content") or {}
    sitrep_number = normalized.get("sitrep_number")
    pdf_asset = normalized.get("pdf_asset") or normalized.get("latest_pdf") or {}
    latest_post = normalized.get("latest_post") or {}
    if sitrep_number is None:
        sitrep_number = pdf_asset.get("sitrep_number") or latest_post.get("sitrep_number")
    if not isinstance(sitrep_number, int):
        raise SitRepPromotionError("sidecar lacks a parseable SitRep number")
    candidates = normalized.get("publication_date_candidates") or []
    data_as_of = candidates[-1] if candidates else str(latest_post.get("date_day") or "")[:10]
    published_at = meta.get("published_at") or latest_post.get("date") or pdf_asset.get("date") or ""
    if len(published_at) == 10:
        published_at = f"{published_at}T00:00:00Z"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "candidate",
        "sitrep_number": sitrep_number,
        "source_id": meta.get("source_id") or "",
        "registry_id": meta.get("registry_id"),
        "source_url": meta.get("url"),
        "data_as_of": data_as_of,
        "published_at": published_at,
        "figures": {},
        "review": {
            "ready_for_model_use": False,
            "source_review_status": "candidate",
            "reviewed_by": "",
            "reviewed_at": "",
            "evidence_chain_id": "",
            "fail_closed_reasons": [
                "pdf_table_not_extracted",
                "date_semantics_require_review",
                "evidence_chain_missing",
                "review_ready_for_model_use_false",
            ],
        },
    }
