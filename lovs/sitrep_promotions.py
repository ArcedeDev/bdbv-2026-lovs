"""Reviewed INSP SitRep promotion payloads."""
from __future__ import annotations

import json
import pathlib
import re
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


CARE_OCCUPANCY_STATES = {"conflict"}


def _validate_care_occupancy_states(figures: dict[str, Any], path: pathlib.Path) -> None:
    """A printed occupancy rate that the reviewer could not reconcile with the same
    edition's printed beds and patients is withheld as a conflict: never shown as
    the rate and never mislabelled as unreported. The structured state must be
    backed by a reviewed preserved_conflicts line naming the province and rate."""
    tables = figures.get("operational_tables")
    rows = tables.get("care_capacity_by_province") if isinstance(tables, dict) else None
    if not isinstance(rows, list):
        return
    conflicts = [line for line in figures.get("preserved_conflicts") or [] if isinstance(line, str)]
    for row in rows:
        if not isinstance(row, dict) or "occupancy_status" not in row:
            continue
        province = row.get("province")
        if row["occupancy_status"] not in CARE_OCCUPANCY_STATES:
            raise SitRepPromotionError(
                f"{path}: care_capacity_by_province {province!r} occupancy_status must be one of "
                f"{sorted(CARE_OCCUPANCY_STATES)}"
            )
        printed = row.get("occupancy_percent_printed")
        if (
            row.get("occupancy_percent") is not None
            or isinstance(printed, bool)
            or not isinstance(printed, (int, float))
        ):
            raise SitRepPromotionError(
                f"{path}: care_capacity_by_province {province!r} conflict must keep occupancy_percent "
                "null and record the printed rate in occupancy_percent_printed"
            )
        if not any(isinstance(province, str) and province in line and f"{printed}%" in line for line in conflicts):
            raise SitRepPromotionError(
                f"{path}: care_capacity_by_province {province!r} conflict needs a preserved_conflicts "
                f"line naming the province and the printed {printed}%"
            )


# Movement-detail rule (founder ruling of 2026-11-14). Published narrative text
# states that an event was reported and withholds the route, the named places and
# the dates of the movement of ONE identified person or ONE single body. Aggregate
# counts ("4 bodies intercepted at Tshopo points of control") stay.
#
# The lint reads the narrative sections, the challenge findings and the point of
# entry narrative notes of a promotion, one sentence at a time (a semicolon ends a
# sentence here, because a finding chains unrelated reports with semicolons), and
# fails a sentence that names a single person or body together with a route, a
# named control point or a pair of movement and death dates.
#
# It applies from the first edition promoted after the rule landed. SitRep 182 was
# the newest promotion then; earlier editions are pinned byte for byte by a frozen
# tournament round and cannot be edited, so they validate unchanged.
MOVEMENT_DETAIL_LINT_FROM_EDITION = 183

_SINGLE_SUBJECT = re.compile(
    r"""(?ix)
    (?<![\w.,])
    (?:
        (?:one|a|this|that|1) \s+ (?:[\w-]+ \s+){0,2}? (?:body|corpse)
      | the \s+ (?:dead \s+)? (?:body|corpse)
      | (?:one|a|this|that|1) \s+ (?:confirmed|suspect(?:ed)?|probable|positive) \s+ (?:case|patient|traveller|traveler)
      | (?:one|a|this|that|1) \s+ (?:suspect|patient|traveller|traveler)
      | (?:one|a|this|that|1) \s+ contact (?! [-\w] | \s+ (?:follow|trac|cohort|list) )
      | (?:un|le|ce|1) \s+ corps
      | (?:une|la|cette|1) \s+ d[ée]pouille
      | (?:un|ce|1) \s+ cas \s+ (?:confirm[ée]|suspect|probable|positif)
      | (?:une?|cet(?:te)?|1) \s+ (?:suspecte?|contact|patiente?|voyageu(?:r|se))
    )
    \b
    """
)
# A named place: a capitalised word. Edition labels (SR173), road markers (PK 15)
# and acronyms (DRC, CTE) are not places for this rule.
_PLACE = r"[A-ZÀ-Þ][a-zß-ÿ][\w'’-]*"
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre"
)
_DAY = rf"(?:\d{{1,2}}(?:er)?\s+(?i:{_MONTHS})|\d{{4}}-\d{{2}}-\d{{2}})"
_CONTROL_POINT = (
    r"(?i:control\s+point|point\s+of\s+control|point\s+of\s+entry|"
    r"point\s+de\s+contr[ôo]le|point\s+d['’]entr[ée]e|po[ce])"
)
_MOVEMENT_DETAIL = tuple(
    re.compile(pattern)
    for pattern in (
        # from X to Y, de X à Y
        rf"\b(?i:from)\s+{_PLACE}\b[^.;]{{0,40}}?\b(?i:to)\s+{_PLACE}",
        rf"(?<!\w)(?i:de\s+|depuis\s+|d['’]){_PLACE}\b[^.;]{{0,40}}?\s(?i:à|vers|jusqu['’]à)\s+{_PLACE}",
        # through or via X
        rf"\b(?i:through|via|en\s+passant\s+par)\s+{_PLACE}",
        # at the X control point, PoC or PoE, in either word order
        rf"(?<!\w)(?i:at|au|à)\s+(?:(?i:the|le|la)\s+)?{_PLACE}(?:[\s-]+{_PLACE}){{0,2}}\s+{_CONTROL_POINT}\b",
        rf"\b{_CONTROL_POINT}\s+(?:(?i:of|at|de|du)\s+|(?i:d)['’])(?:(?i:the|la|le)\s+)?{_PLACE}",
        # on <date> after a death on <date>
        rf"\b(?i:on)\s+{_DAY}\b[^.;]*?\b(?i:after\s+(?:a|the|his|her)\s+death\s+on)\s+{_DAY}",
        rf"\b(?i:le)\s+{_DAY}\b[^.;]*?(?i:après\s+(?:un|le|son)\s+décès\s+(?:survenu\s+)?(?:le|du))\s+{_DAY}",
    )
)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")


def movement_detail_sentences(text: str) -> list[int]:
    """Indexes (from 1) of the sentences that carry one person's or one body's movement detail."""
    flagged: list[int] = []
    for index, sentence in enumerate(_SENTENCE_END.split(text), 1):
        if _SINGLE_SUBJECT.search(sentence) and any(p.search(sentence) for p in _MOVEMENT_DETAIL):
            flagged.append(index)
    return flagged


def _narrative_texts(figures: dict[str, Any]) -> list[tuple[str, str]]:
    """(location, text) for the narrative and challenge finding text of a promotion."""
    texts: list[tuple[str, str]] = []

    def collect(location: str, value: Any, keys: tuple[str, ...] | None) -> None:
        if isinstance(value, str):
            texts.append((location, value))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                collect(f"{location}[{index}]", item, keys)
        elif isinstance(value, dict):
            for key, item in value.items():
                if keys is None or key in keys or isinstance(item, (list, dict)):
                    collect(f"{location}.{key}", item, keys)

    collect("figures.narrative", figures.get("narrative"), None)
    tables = figures.get("operational_tables")
    if isinstance(tables, dict):
        for index, item in enumerate(tables.get("challenges") or []):
            location = f"figures.operational_tables.challenges[{index}]"
            collect(location, item, ("finding",))
        collect(
            "figures.operational_tables.poe_poc_narrative_by_province",
            tables.get("poe_poc_narrative_by_province"),
            ("scope_note",),
        )
    return texts


def movement_detail_findings(figures: dict[str, Any]) -> list[str]:
    """Locations of sentences that break the movement-detail rule. The sentence text is
    never returned, so a validation log cannot republish what the rule withholds."""
    return [
        f"{location} sentence {index}"
        for location, text in _narrative_texts(figures)
        for index in movement_detail_sentences(text)
    ]


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
    _validate_care_occupancy_states(figures, path)
    if sitrep_number >= MOVEMENT_DETAIL_LINT_FROM_EDITION:
        movement_findings = movement_detail_findings(figures)
        if movement_findings:
            raise SitRepPromotionError(
                f"{path}: movement detail of one person or one body in "
                f"{'; '.join(movement_findings)}. State that the event was reported and withhold "
                "the route, the named places and the dates"
            )
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
