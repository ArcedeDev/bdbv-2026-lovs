# SPDX-License-Identifier: Apache-2.0
"""Prepare pending target evidence for the existing tournament resolution path.

No outcome review is inferred from a source promotion's review. This module never
writes the evidence registry or finalizes a round. Publication, observation and
review clocks remain separate; absent rows never establish negative surveillance.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import pathlib
from collections.abc import Mapping
from typing import Any

from lovs import health_zone_centroids as hzc
from lovs import lovs_evidence as E
from lovs import model_tournament as T
from lovs import sitrep_promotions as P
from lovs import tournament_candidates as C

SCHEMA_VERSION = "bdbv-model-tournament-resolution-draft/v1"
COVERAGE_SCHEMA_VERSION = "bdbv-model-tournament-coverage/v1"


class ResolutionDraftError(ValueError):
    """An input cannot support an honest resolution draft."""


def _reviewed_chain(registry: Mapping[str, Any], chain_id: str,
                    cutoff: dt.datetime) -> Mapping[str, Any]:
    chain = E.chain_by_id(registry, chain_id)
    if chain is None or chain.get("verdict") not in {"supported", "derived_supported"}:
        raise ResolutionDraftError(f"{chain_id}: reviewed supported evidence required")
    C._guard(chain_id, "reviewed_at", chain.get("reviewed_at"), cutoff)
    if not any(s.get("tier") == "T1_PRIMARY" for s in chain["sources"]):
        raise ResolutionDraftError(f"{chain_id}: T1_PRIMARY evidence required")
    return chain


def _coverage(document: Mapping[str, Any] | None, round_doc: Mapping[str, Any],
              registry: Mapping[str, Any], cutoff: dt.datetime) -> dict[str, list[Mapping[str, Any]]]:
    if document is None:
        return {}
    binding = {
        "round_id": round_doc["round_id"],
        "forecast_sha256": round_doc["freeze_receipt"]["forecast_sha256"],
        "resolution_policy_sha256": T.content_hash(round_doc["resolution_policy"]),
    }
    if document.get("schema_version") != COVERAGE_SCHEMA_VERSION or any(
        document.get(k) != v for k, v in binding.items()
    ):
        raise ResolutionDraftError("coverage document does not match frozen round and policy")
    rows = document.get("assessments")
    if not isinstance(rows, list):
        raise ResolutionDraftError("coverage assessments must be a list")
    targets = {row["target_id"] for row in round_doc["target_events"]}
    result: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ResolutionDraftError("coverage assessment must be an object")
        target = row.get("target_id")
        if not isinstance(target, str) or target not in targets or target in result:
            raise ResolutionDraftError("duplicate or unknown coverage target")
        assessment = row.get("assessment")
        if assessment not in round_doc["resolution_policy"]["accepted_coverage_assessments"]:
            raise ResolutionDraftError(f"{target}: unsupported coverage assessment")
        expected = dict(binding, target_id=target, assessment=assessment,
                        window_start=round_doc["window_start"], window_end=round_doc["window_end"])
        if any(row.get(k) != expected[k] for k in ("window_start", "window_end")):
            raise ResolutionDraftError(f"{target}: coverage must span the exact frozen window")
        ids = row.get("evidence_chain_ids")
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)):
            raise ResolutionDraftError(f"{target}: unique coverage evidence ids required")
        chains = [_reviewed_chain(registry, i, cutoff) for i in sorted(ids)]
        for chain in chains:
            if (chain.get("coverage") != expected
                    or chain["claim"]["value"] != assessment):
                raise ResolutionDraftError(f"{target}: evidence does not bind reviewed full-window coverage")
            T._validate_negative_coverage(chain["coverage"], round_doc, target, chain["reviewed_at"])
        result[target] = chains
    return result


def _read_promotions(promotions_dir: pathlib.Path, registry: Mapping[str, Any],
                     index: hzc.CentroidIndex, cutoff: dt.datetime,
                     baseline_inputs: Mapping[str, str] | None = None,
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    records, inputs, diagnostics = [], [], []
    baseline_inputs = baseline_inputs or {}
    seen_paths = set()
    for path in sorted(promotions_dir.glob("sitrep-*.json")):
        match = C._PROMOTION_NAME_RE.fullmatch(path.name)
        if match is None or T._date(match.group(2)) > cutoff.date():
            continue
        promotion = P.validate_promotion(P._load_json(path), path=path, require_reviewed=True)
        if (promotion["data_as_of"] != match.group(2)
                or promotion["sitrep_number"] != int(match.group(1))):
            raise ResolutionDraftError(f"{path.name}: filename and source identity disagree")
        input_row = C._input_row(path, "reviewed_sitrep_promotion")
        seen_paths.add(input_row["path"])
        baseline = input_row["path"] in baseline_inputs
        if baseline and input_row["sha256"] != baseline_inputs[input_row["path"]]:
            raise ResolutionDraftError(f"{path.name}: frozen baseline promotion changed")
        receipt = promotion.get("source_receipt") or {}
        published = C.promotion_publication(promotion)
        for field, value in (("data_as_of", promotion["data_as_of"]),
                             *C._publication_clocks(promotion).items(),
                             ("reviewed_at", promotion["review"]["reviewed_at"])):
            C._guard(path.name, field, value, cutoff)
        source_chain_id = promotion["review"]["evidence_chain_id"]
        try:
            source_chain = _reviewed_chain(registry, source_chain_id, cutoff)
            primary = [s for s in source_chain["sources"] if s["tier"] == "T1_PRIMARY"
                       and s.get("manifest_source_id") == promotion["source_id"]]
        except ResolutionDraftError as exc:
            primary = []
            diagnostics.append(f"{path.name}: {exc}; no outcome authority inferred")
        if not primary:
            diagnostics.append(f"{path.name}: no anchored primary evidence for a new outcome")
        elif receipt.get("source_url") and receipt["source_url"] not in {s["url"] for s in primary}:
            raise ResolutionDraftError(f"{path.name}: receipt URL does not match anchored primary evidence")
        table = promotion["figures"].get("health_zone_table") or {}
        if not isinstance(table, dict):
            raise ResolutionDraftError(f"{path.name}: health-zone table must be an object")
        T._date(table.get("date") or promotion["data_as_of"], "table.date")
        rows = table.get("rows") or []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ResolutionDraftError(f"{path.name}: health-zone rows must be objects")
        # Validate every named row, including zero rows ignored by forecast normalization.
        seen, total, residual, raw_counts, pending = set(), 0, 0, {}, set()
        for row in rows:
            name = str(row.get("zone") or "")
            count = row.get("confirmed")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ResolutionDraftError(f"{path.name}: invalid confirmed count for {name}")
            if "ventil" in name.lower():
                residual += count
                continue
            zone = index.resolve_sitrep_row(str(row.get("province") or ""), name)
            if zone in seen:
                raise ResolutionDraftError(f"{path.name}: duplicate zone {zone}")
            seen.add(zone)
            total += count
            if count:
                raw_counts[zone] = count
                if row.get("officially_integrated") is False:
                    pending.add(zone)
        # Preserve pending positives as uncertainty; the forecast helper refuses
        # such rows when they are the latest table of a new forecast universe.
        normalized = C._zone_tables([promotion], index) if not pending else []
        counts = normalized[0]["counts"] if normalized else raw_counts
        reconciliation = table.get("reconciliation") or {}
        if not isinstance(reconciliation, dict):
            raise ResolutionDraftError(f"{path.name}: reconciliation must be an object")
        published_table = table.get("zone_attribution_status", "published") == "published"
        reconciled = bool(rows) and bool(reconciliation) and published_table
        national = reconciliation.get("national_confirmed_total")
        negative_residual = reconciliation.get("declared_negative_residual") or {}
        if not isinstance(negative_residual, dict):
            raise ResolutionDraftError(f"{path.name}: declared negative residual must be an object")
        adjustment = negative_residual.get("confirmed", 0)
        if isinstance(adjustment, bool) or not isinstance(adjustment, int) or adjustment > 0:
            raise ResolutionDraftError(f"{path.name}: invalid declared negative residual")
        if reconciled and (reconciliation.get("named_zone_confirmed_sum") != total
                           or isinstance(national, bool) or not isinstance(national, int)
                           or national != promotion["figures"].get("cumul_cas_confirmes_drc")
                           or total + residual + adjustment > national):
            raise ResolutionDraftError(f"{path.name}: confirmed table does not reconcile")
        # Residual cases stay unallocated. A disclosed remaining national gap
        # makes this unsuitable for certifying outcomes, but does not erase rows.
        reconciled = reconciled and total + residual + adjustment == national and not pending
        if not reconciled:
            diagnostics.append(f"{path.name}: no reconciled zone table; printed positives remain unresolved evidence")
        try:
            exact_publication = T._utc_datetime(published, "published_at")
        except T.TournamentConfigError:
            exact_publication = None
        records.append({
            "source_id": promotion["source_id"], "data_as_of": promotion["data_as_of"],
            "table_date": table.get("date") or promotion["data_as_of"],
            "published_at": published, "promotion_published_at": promotion["published_at"],
            "publication_clocks": C._publication_clocks(promotion),
            "reviewed_at": promotion["review"]["reviewed_at"],
            "exact_publication": exact_publication, "counts": counts,
            "reconciled": reconciled, "sources": primary, "baseline": baseline,
            "supported": bool(primary), "pending_targets": sorted(pending),
            "evidence_chain_id": source_chain_id,
        })
        inputs.append(input_row)
    if set(baseline_inputs) - seen_paths:
        raise ResolutionDraftError("frozen baseline promotion is missing from resolution inputs")
    return records, inputs, diagnostics


def build_resolution_draft(
    round_doc: Mapping[str, Any], *, evidence_cutoff_utc: str,
    promotions_dir: pathlib.Path = P.PROMOTIONS_DIR,
    evidence_registry_path: pathlib.Path = T.EVIDENCE_REGISTRY_PATH,
    centroids_path: pathlib.Path = hzc.CENTROIDS_PATH,
    coverage_assessments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic pending bundle; no canonical or reviewed writes."""
    T.validate_round(round_doc)
    if round_doc.get("resolution_policy") != T.RESOLUTION_POLICY:
        raise ResolutionDraftError("a supported frozen resolution policy is required")
    cutoff = T._utc_datetime(evidence_cutoff_utc, "evidence_cutoff_utc")
    start, end = T._date(round_doc["window_start"]), T._date(round_doc["window_end"])
    if cutoff.date() <= end:
        raise ResolutionDraftError("resolution draft requires the completed window")
    centroid_inputs = [row for row in round_doc["build_receipt"]["inputs"]
                       if row.get("role") == "health_zone_centroids"]
    if len(centroid_inputs) != 1 or centroid_inputs[0].get("sha256") != hzc.file_sha256(centroids_path):
        raise ResolutionDraftError("centroid input differs from the frozen identity contract")
    registry = E.load_registry(evidence_registry_path)
    E.validate_registry(registry)
    E.validate_source_anchors(registry)
    index = hzc.CentroidIndex.load_default(centroids_path)
    targets = sorted(row["target_id"] for row in round_doc["target_events"])
    for target in round_doc["target_events"]:
        index.zone(target["target_id"])
        if target.get("geography_id") != f"cod-health-zone:{target['target_id']}":
            raise ResolutionDraftError("resolver requires a frozen COD health-zone target")
    coverage = _coverage(coverage_assessments, round_doc, registry, cutoff)
    baseline = {row["path"]: row["sha256"] for row in round_doc["build_receipt"]["inputs"]
                if row.get("role") == "reviewed_sitrep_promotion"}
    records, inputs, diagnostics = _read_promotions(promotions_dir, registry, index, cutoff, baseline)
    inputs.extend([C._input_row(evidence_registry_path, "evidence_registry"),
                   C._input_row(centroids_path, "health_zone_centroids")])
    outcomes, chains = [], []
    for target in targets:
        positives = [r for r in records if target in r["counts"]]
        certain = [r for r in positives if r["exact_publication"] is not None and r["reconciled"] and r["supported"]]
        first = min(certain, key=lambda r: (r["exact_publication"], r["source_id"])) if certain else None
        uncertain = [r for r in positives if not r["reconciled"] or not r["supported"] or r["exact_publication"] is None]
        blocked_feed = [r for r in records if not r["baseline"] and not r["supported"]]
        post_window = [r for r in records if r["reconciled"] and r["supported"] and r["exact_publication"] is not None
                       and r["exact_publication"].date() >= end and T._date(r["data_as_of"]) >= end]
        if first and first["exact_publication"].date() < start:
            status, reason = "unscoreable_conflicting_evidence", "pre_window_detection"
        elif uncertain:
            status, reason = "unscoreable_conflicting_evidence", "ambiguous_publication_or_unreconciled_detection"
        elif first and first["exact_publication"].date() <= end and target in coverage:
            status, reason = "unscoreable_conflicting_evidence", "positive_detection_conflicts_with_negative_coverage"
        elif first and first["exact_publication"].date() <= end:
            status, reason = "resolved_yes", "first_public_detection_in_window"
        elif target in coverage and not blocked_feed:
            status, reason = "resolved_no", "reviewed_target_specific_full_window_coverage"
        elif target in coverage:
            status, reason = "unscoreable_surveillance_dark", "unsupported_new_source_history"
        elif not post_window:
            status, reason = "unscoreable_no_feed", "no_reconciled_end_of_window_feed"
        else:
            status, reason = "unscoreable_surveillance_dark", "missing_target_specific_coverage"
        used = positives if positives else (post_window or records)
        sources: dict[str, dict[str, Any]] = {}
        for source in [s for r in used for s in r["sources"]] + [
            s for chain in coverage.get(target, []) for s in chain["sources"]
        ]:
            previous = sources.get(source["source_id"])
            if previous is not None and any(previous.get(k) != source.get(k) for k in
                    ("tier", "url", "manifest_source_id", "registry_id")):
                raise ResolutionDraftError(f"conflicting source identity {source['source_id']}")
            sources.setdefault(source["source_id"], copy.deepcopy(source))
        evidence_days = [str(r["data_as_of"]) for r in used] + (
            [end.isoformat()] if target in coverage else [])
        evidence_date = max(evidence_days) if evidence_days else cutoff.date().isoformat()
        chain_id = f"ec:lovs:model-tournament:{round_doc['round_id']}:{target}:{cutoff.date()}"
        row = {"target_id": target, "resolution_status": status, "reason": reason,
               "evidence_as_of": evidence_date, "evidence_chain_ids": [chain_id]}
        if status in {"resolved_yes", "resolved_no"}:
            row["outcome"] = int(status == "resolved_yes")
        outcomes.append(row)
        clocks = [{k: r[k] for k in ("source_id", "data_as_of", "table_date", "published_at",
                                     "promotion_published_at", "publication_clocks", "reviewed_at", "evidence_chain_id")}
                  for r in used]
        chains.append({
            "chain_id": chain_id,
            "claim": {"claim_id": f"claim:lovs:model-tournament:{round_doc['round_id']}:{target}",
                      "artifact": f"data/model-tournament/resolutions/{round_doc['round_id']}.json",
                      "locator": f"target_outcomes[target_id={target}].resolution_status",
                      "statement": f"Proposed assessment for {target}: {reason}.", "value": status},
            "verdict": "pending", "reviewed_at": None, "reviewer": None,
            "sources": [sources[k] for k in sorted(sources)],
            "steps": [{"step_id": f"step:tournament:{target}", "kind": "derivation",
                       "finding": f"Proposed {status}: {reason}. Subject to independent target evidence review."}],
            "next_action": "Review target assessment and source coverage before any registry insertion or finalization.",
            "source_clocks": clocks,
            "coverage_evidence_chain_ids": [c["chain_id"] for c in coverage.get(target, [])],
            **({"coverage": copy.deepcopy(coverage[target][0]["coverage"])}
               if status == "resolved_no" else {}),
        })
        if not sources:
            diagnostics.append(f"{target}: no anchored primary source for assessment; draft cannot finalize")
    return {"schema_version": SCHEMA_VERSION, "round_id": round_doc["round_id"],
            "forecast_sha256": round_doc["freeze_receipt"]["forecast_sha256"],
            "resolution_policy_sha256": T.content_hash(round_doc["resolution_policy"]),
            "evidence_cutoff_utc": evidence_cutoff_utc,
            "coverage_assessments_sha256": T.content_hash(coverage_assessments),
            "resolution_candidate": {"resolved_at": evidence_cutoff_utc, "target_outcomes": outcomes},
            "draft_chains": chains, "inputs": inputs, "diagnostics": diagnostics}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=pathlib.Path, required=True)
    parser.add_argument("--evidence-cutoff-utc", required=True)
    parser.add_argument("--coverage-assessments", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        if output.is_relative_to((T.REPO_ROOT / "data").resolve()):
            raise ResolutionDraftError("draft output must be outside canonical data/")
        if output in {args.round.resolve(),
                      args.coverage_assessments.resolve() if args.coverage_assessments else None}:
            raise ResolutionDraftError("draft output cannot replace an input")
        draft = build_resolution_draft(
            T._read_json(args.round), evidence_cutoff_utc=args.evidence_cutoff_utc,
            coverage_assessments=T._read_json(args.coverage_assessments) if args.coverage_assessments else None,
        )
        T._write_create_only(output, draft)
    except (ValueError, OSError) as exc:
        print(f"resolution draft refused: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
