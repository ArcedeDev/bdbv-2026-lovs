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
    21: {
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

# Contracts keyed by the edition's declared report_format rather than by its
# number. Keying on the number meant every new edition of an existing layout
# needed its own copy of the same set, which is how SitRep 86 arrived and found
# itself validated against the fifteen-page contract it could never satisfy.
REQUIRED_FIGURES_BY_FORMAT: dict[str, set[str]] = {
    # SitRep 84 introduced the compact six-page executive format. It preserves
    # national/provincial and response-pillar reporting but no longer publishes
    # the per-health-zone case/death rows or the national isolation status split.
    # Those omissions are explicit reviewed data gaps, never zeroes.
    "compact_executive_v1": {
        "cumul_cas_confirmes_drc",
        "cumul_deces_parmi_confirmes_drc",
        "gueris",
        "patients_en_isolement_hospitalisation",
        "contact_followup_rate_pct",
        "country_scope_confirmed_total",
        "country_scope_confirmed_deaths",
        "health_zone_table",
        "operational_tables",
        "report_format",
    },
    # SitRep 86 onward: INSP stopped publishing PDFs to its WordPress library and
    # began posting four-page image packets to its official LinkedIn feed. These
    # are still PRIMARY publisher documents -- same authority, same cover block
    # stating "Situation au ... | Publication du ..." -- just delivered as images
    # rather than a PDF, and they carry no province, per-zone, alert or
    # isolation-split table. What is required is the tier declaration itself, so a
    # packet recovered from a social feed can never be silently presented as a
    # fifteen-page reviewed PDF.
    "primary_social_image_v1": {
        "cumul_cas_confirmes_drc",
        "cumul_deces_parmi_confirmes_drc",
        "gueris",
        "patients_en_isolement_hospitalisation",
        "country_scope_confirmed_total",
        "country_scope_confirmed_deaths",
        "health_zone_table",
        "operational_tables",
        "report_format",
    },
    # SitRep 88 onward: INSP returned to its WordPress library with an eight-page
    # PDF. The image-packet format did NOT become the standard. This layout
    # restores the per-health-zone case/death table the compact format dropped,
    # along with the province table, the alert table and the response pillars, but
    # it does not restore the contact or isolation-status tables: those figures are
    # published only as national prose. So the zone table is required again, while
    # the isolation split stays an explicit gap rather than a zero.
    "standard_full_v2": {
        "cumul_cas_confirmes_drc",
        "cumul_deces_parmi_confirmes_drc",
        "gueris",
        "patients_en_isolement_hospitalisation",
        "contact_followup_rate_pct",
        "country_scope_confirmed_total",
        "country_scope_confirmed_deaths",
        "health_zone_table",
        "province_table",
        "operational_tables",
        "report_format",
    },
}
REQUIRED_LAB_FIELDS = {"samples_analyzed", "samples_positive"}


class SitRepPromotionError(ValueError):
    """Raised when a promotion payload is incomplete or unsafe for model use."""


def required_figures_for(sitrep_number: int, report_format: str | None = None) -> set[str]:
    """Return the reviewed figure contract for a SitRep.

    An edition that declares a report_format is validated against that layout's
    contract; the format is what determines which figures the source can
    actually carry. Otherwise SitRep #019 and later share the same reviewed
    shape unless a future layout change gets an explicit override above. Keeping
    that rule in the validator prevents the generator from accepting an empty
    reviewed payload for a newly published same-layout SitRep.
    """
    if report_format and report_format in REQUIRED_FIGURES_BY_FORMAT:
        return REQUIRED_FIGURES_BY_FORMAT[report_format]
    if sitrep_number in REQUIRED_FIGURES:
        return REQUIRED_FIGURES[sitrep_number]
    if sitrep_number >= 19:
        return REQUIRED_FIGURES[21]
    return set()


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
    declared_format = figures.get("report_format")
    missing_figures = sorted(
        required_figures_for(
            sitrep_number,
            declared_format if isinstance(declared_format, str) else None,
        )
        - set(figures)
    )
    if missing_figures and payload["status"] == "reviewed":
        raise SitRepPromotionError(f"{path}: missing reviewed figures {missing_figures}")
    if sitrep_number >= 84 and payload["status"] == "reviewed":
        # Every post-Tableau-2 edition must name the layout it came from. The
        # set is closed on purpose: an edition that fits neither must be looked
        # at by a person rather than defaulted into whichever contract is
        # loosest.
        if figures.get("report_format") not in REQUIRED_FIGURES_BY_FORMAT:
            raise SitRepPromotionError(
                f"{path}: SitRep {sitrep_number} must declare a known report_format "
                f"(one of {sorted(REQUIRED_FIGURES_BY_FORMAT)})"
            )
        if figures.get("report_format") == "primary_social_image_v1":
            # The delivery channel has to be declared in the payload itself, not
            # merely in a review note: a reader is entitled to know that the
            # publisher document is an image packet recovered from a social feed
            # rather than a PDF from the publisher's own library.
            tier = payload.get("evidence_tier")
            if not isinstance(tier, dict) or tier.get("tier") != "primary_social_image":
                raise SitRepPromotionError(
                    f"{path}: a social-image edition must carry a matching "
                    "evidence_tier block"
                )
            for field in ("official_post_url", "publisher_account", "asset_count"):
                if not tier.get(field):
                    raise SitRepPromotionError(
                        f"{path}: evidence_tier.{field} is required so the packet can "
                        "be traced back to the publisher's own account"
                    )
        # Zone attribution must always be declared, but the declaration a layout owes
        # depends on whether it prints the table. The compact and image-packet
        # formats do not, so they must name the edition they carry from. The
        # eight-page format restored the table at SitRep 88, and forcing a carry
        # declaration on an edition that publishes real rows would relabel published
        # data as carried.
        table = figures.get("health_zone_table")
        publishes_zone_table = figures.get("report_format") == "standard_full_v2"
        expected_status = (
            "published" if publishes_zone_table else "not_published_carry_forward_latest_reviewed"
        )
        if not isinstance(table, dict) or table.get("zone_attribution_status") != expected_status:
            raise SitRepPromotionError(
                f"{path}: SitRep {sitrep_number} must declare zone_attribution_status "
                f"{expected_status!r} for report_format {figures.get('report_format')!r}"
            )
        if publishes_zone_table and not table.get("rows"):
            raise SitRepPromotionError(
                f"{path}: an edition declaring a published zone table must carry its rows"
            )
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
    receipt = payload.get("source_receipt")
    if receipt is not None and not isinstance(receipt, dict):
        raise SitRepPromotionError(f"{path}: source_receipt must be an object when present")
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
