# SPDX-License-Identifier: Apache-2.0
"""Guard: a SitRep visual-promotion chain must cite the edition its promotion reviewed.

Every reviewed SitRep has two records written in the same cycle: the promotion
(`data/sitrep_promotions/sitrep-NNN-<date>.json`, the reviewed figures and the
official `source_url`) and its evidence chain
(`ec:lovs:data:{inrb,insp}-sitrep-NNN-visual-promotion:<date>`, the public
provenance). The chain is authored by cloning the previous cycle's chain, so
any identifier the clone step fails to rewrite keeps the previous edition's
value while the promotion, the headline and the rest of the suite stay correct.

That shipped for SR126 and SR127: a global replace of '125' and '2026-09-16'
missed the day-first forms, so the chains cited `SitRep_MVEBDB_126_16_09_2026.pdf`
and `SitRep_MVEBDB_127_16_09_2026.pdf` (both HTTP 404) and
`SitRep N°126/MVEBDB/16/09/2026` / `N°127/...16/09/2026`, while their promotions
carried the right URL. Nothing failed. This gate joins each chain to its
promotion and requires every edition-bearing identifier in the chain to agree
with it:

  * the chain_id date is the promotion's `data_as_of`;
  * `sources[0]` is the promotion's source: `source_id` is `src:` plus the
    promotion's `source_id`, `manifest_source_id` is that id or the promotion's
    `source_receipt.staged_source_id` (the same PDF under its WordPress id), and
    `url` is the promotion's `source_url`;
  * the citation names the promotion's `sitrep_number`; a labelled report date
    ("report date", "data cutoff", "Date de rapportage") is its `data_as_of`;
    the day-first title date (`N°128/MVEBDB/19/09/2026`) is `data_as_of` or the
    day after, because INSP titled SR114-SR116 and SR118 by publication day and
    the citation quotes the title as printed;
  * `claim.claim_id`, `claim.artifact` and `claim.locator` name this edition,
    and `claim.artifact` names this promotion's file;
  * any step `source_id` that names a SitRep names this edition;
  * the promotion's source entry in `data/bundibugyo-2026/manifest.json` has the
    promotion's `source_url` and receipt hash, its `source_pdf_url` is its own
    `url`, and its `evidence_chain_id` and `root_provenance_chain` name no other
    visual-promotion chain (SR106's entry kept SR105's in both).

Step ids are owned by `test_sitrep_chain_step_provenance.py`. Free text
(`claim.statement`, `claim.value`, `next_action`, findings) is not gated: it
legitimately names neighbouring editions ("supersede with SitRep 47", "same zone
set as SitRep 55"), so a number there is not evidence of a stale clone.

Chains already published with a mismatch are listed per check in
`KNOWN_SOURCE_MISMATCHES` rather than skipped, and a second test fails as soon
as a listed check starts passing, so a repaired chain cannot stay exempt.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "data" / "evidence-chains.json"
PROMOTIONS_DIR = REPO_ROOT / "data" / "sitrep_promotions"
MANIFEST = REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"

CHAIN_ID = re.compile(
    r"^ec:lovs:data:(?:inrb|insp)-sitrep-0*(\d+)-visual-promotion:(\d{4}-\d{2}-\d{2})$"
)
# `sitrep-128` or `sitrep-128-2026-09-19` inside an id, path or locator.
SITREP_REF = re.compile(r"sitrep-0*(\d+)(?:-(\d{4}-\d{2}-\d{2}))?")

# Citations have used several printed forms over the outbreak:
#   SitRep N°128/MVEBDB/19/09/2026, official WordPress PDF.
#   SitRep N°019/MVB_02/06/2026 (data cutoff 2 June 2026, ...)
#   SitRep N052/MVB (report date 2026-07-05; INSP WordPress post 25100)
#   Rapport de Situation MVE N 088 (Date de rapportage 10 aout 2026, ...)
CITATION_NUMBER = re.compile(r"(?:SitRep|Rapport de Situation MVE)\s+N°?\s*0*(\d+)")
CITATION_DAY_FIRST_DATE = re.compile(r"N°?\s*\d+/MV[A-Z]*[/_](\d{2})/(\d{2})/(\d{4})")
CITATION_LABELLED_DATE = re.compile(
    r"(?:report date|data cutoff(?:/report date)?|Date de rapportage)\s+"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2} [^\W\d_]+ \d{4})"
)
MONTHS = {
    "may": 5, "mai": 5, "june": 6, "juin": 6, "july": 7, "juillet": 7,
    "august": 8, "aout": 8, "août": 8, "september": 9, "septembre": 9,
    "october": 10, "octobre": 10, "november": 11, "novembre": 11,
    "december": 12, "décembre": 12, "decembre": 12,
}

# chain_id -> checks already published failing. Each entry is a debt, not a
# suppression: repair the chain and its entry must go (see the second test).
KNOWN_SOURCE_MISMATCHES: dict[str, frozenset[str]] = {
    # Promotions that predate `source_url`: nothing to compare the chain URL to.
    "ec:lovs:data:inrb-sitrep-019-visual-promotion:2026-06-02": frozenset({"url"}),
    "ec:lovs:data:inrb-sitrep-020-visual-promotion:2026-06-03": frozenset({"url"}),
    "ec:lovs:data:inrb-sitrep-021-visual-promotion:2026-06-04": frozenset({"url"}),
}


def _chains(registry: dict) -> list[dict]:
    return [c for c in registry["chains"] if "-visual-promotion:" in str(c.get("chain_id", ""))]


def _manifest_by_source_id() -> dict[str, dict]:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))["entries"]
    return {entry["source_id"]: entry for entry in entries if entry.get("source_id")}


def _promotions_by_number() -> dict[int, tuple[pathlib.Path, dict]]:
    promotions: dict[int, tuple[pathlib.Path, dict]] = {}
    for path in sorted(PROMOTIONS_DIR.glob("sitrep-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        number = int(payload["sitrep_number"])
        if number in promotions:
            raise AssertionError(f"two promotions for SitRep {number}: {promotions[number][0].name}, {path.name}")
        promotions[number] = (path, payload)
    return promotions


def _citation_date(text: str) -> str | None:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    day, month, year = text.split()
    number = MONTHS.get(month.lower())
    return f"{year}-{number:02d}-{int(day):02d}" if number else None


def _citation_problem(citation: str, sitrep_number: int, data_as_of: str) -> str | None:
    number = CITATION_NUMBER.search(citation)
    if number is None:
        return f"names no SitRep number: {citation!r}"
    if int(number.group(1)) != sitrep_number:
        return f"cites SitRep {int(number.group(1))}, promotion is SitRep {sitrep_number}: {citation!r}"
    title_dates = {f"{y}-{m}-{d}" for d, m, y in CITATION_DAY_FIRST_DATE.findall(citation)}
    report_dates = set()
    for raw in CITATION_LABELLED_DATE.findall(citation):
        parsed = _citation_date(raw)
        if parsed is None:
            return f"report date {raw!r} is not a recognised date: {citation!r}"
        report_dates.add(parsed)
    if not title_dates and not report_dates:
        return f"names no report date: {citation!r}"
    publication_day = (dt.date.fromisoformat(data_as_of) + dt.timedelta(days=1)).isoformat()
    if report_dates - {data_as_of} or title_dates - {data_as_of, publication_day}:
        return (
            f"dated title {sorted(title_dates)} / report {sorted(report_dates)}, promotion "
            f"reports {data_as_of} (a title may carry the publication day {publication_day}): {citation!r}"
        )
    return None


def _foreign_sitrep_refs(value: str, sitrep_number: int, data_as_of: str) -> list[str]:
    return [
        m.group(0)
        for m in SITREP_REF.finditer(value)
        if int(m.group(1)) != sitrep_number or (m.group(2) and m.group(2) != data_as_of)
    ]


def chain_violations(
    chain: dict, promotions: dict[int, tuple[pathlib.Path, dict]], manifest: dict[str, dict]
) -> dict[str, str]:
    """Return {check: problem} for every way `chain` disagrees with its promotion."""
    match = CHAIN_ID.match(str(chain.get("chain_id", "")))
    if match is None:
        return {"chain_id": "does not match ec:lovs:data:{inrb,insp}-sitrep-N-visual-promotion:<date>"}
    number, chain_date = int(match.group(1)), match.group(2)
    if number not in promotions:
        return {"promotion": f"no data/sitrep_promotions file for SitRep {number}"}
    path, promotion = promotions[number]
    data_as_of = promotion["data_as_of"]
    problems: dict[str, str] = {}

    if chain_date != data_as_of:
        problems["chain_date"] = f"chain_id date {chain_date}, promotion data_as_of {data_as_of}"

    sources = chain.get("sources") or []
    if not sources:
        problems["source_id"] = "chain has no sources"
        return problems
    primary = sources[0]
    manifest_ids = {promotion["source_id"], (promotion.get("source_receipt") or {}).get("staged_source_id")}
    if primary.get("source_id") != f"src:{promotion['source_id']}" or primary.get("manifest_source_id") not in manifest_ids:
        problems["source_id"] = (
            f"sources[0] ids {primary.get('source_id')!r} / {primary.get('manifest_source_id')!r}, "
            f"promotion source_id {promotion['source_id']!r}"
        )
    if primary.get("url") != promotion.get("source_url"):
        problems["url"] = f"sources[0].url {primary.get('url')!r}, promotion source_url {promotion.get('source_url')!r}"
    citation_problem = _citation_problem(str(primary.get("citation", "")), number, data_as_of)
    if citation_problem:
        problems["citation"] = citation_problem

    claim = chain.get("claim") or {}
    foreign = {
        field: refs
        for field in ("claim_id", "artifact", "locator")
        if (refs := _foreign_sitrep_refs(str(claim.get(field, "")), number, data_as_of))
    }
    promotion_path = path.relative_to(REPO_ROOT).as_posix()
    if promotion_path not in str(claim.get("artifact", "")):
        foreign.setdefault("artifact", []).append(f"missing {promotion_path}")
    if foreign:
        problems["claim"] = f"names another edition: {foreign}"

    stray_steps = {
        step.get("step_id"): step.get("source_id")
        for step in chain.get("steps") or []
        if _foreign_sitrep_refs(str(step.get("source_id", "")), number, data_as_of)
    }
    if stray_steps:
        problems["steps"] = f"step source_id names another edition: {stray_steps}"

    entry = manifest.get(promotion["source_id"])
    if entry is None:
        problems["manifest"] = f"no manifest entry for {promotion['source_id']!r}"
        return problems
    content = entry.get("normalized_content") or {}
    receipt_hash = (promotion.get("source_receipt") or {}).get("sha256")
    stale = {}
    if promotion.get("source_url") and entry.get("url") != promotion["source_url"]:
        stale["url"] = entry.get("url")
    if receipt_hash and entry.get("content_hash") != receipt_hash:
        stale["content_hash"] = entry.get("content_hash")
    if content.get("source_pdf_url") not in (None, entry.get("url")):
        stale["source_pdf_url"] = content["source_pdf_url"]
    if content.get("evidence_chain_id") not in (None, chain["chain_id"]):
        stale["evidence_chain_id"] = content["evidence_chain_id"]
    foreign_roots = [
        ref for ref in entry.get("root_provenance_chain") or []
        if "-visual-promotion:" in ref and ref != chain["chain_id"]
    ]
    if foreign_roots:
        stale["root_provenance_chain"] = foreign_roots
    if stale:
        problems["manifest"] = f"manifest entry {promotion['source_id']!r} names another source: {stale}"
    return problems


class TestSitRepChainPromotionParity(unittest.TestCase):
    def setUp(self) -> None:
        self.chains = _chains(json.loads(REGISTRY.read_text(encoding="utf-8")))
        self.promotions = _promotions_by_number()
        self.manifest = _manifest_by_source_id()

    def violations(self, chain: dict) -> dict[str, str]:
        return chain_violations(chain, self.promotions, self.manifest)

    def test_chains_agree_with_their_promotion(self) -> None:
        self.assertTrue(self.chains, "no visual-promotion chains found; the gate would pass vacuously")
        offenders = [
            f"  {chain['chain_id']} [{check}] {problem}"
            for chain in self.chains
            for check, problem in sorted(self.violations(chain).items())
            if check not in KNOWN_SOURCE_MISMATCHES.get(chain["chain_id"], frozenset())
        ]
        if offenders:
            self.fail(
                "SitRep chains name a different edition than their promotion, which means "
                "the cloned chain was not fully rewritten for this cycle:\n" + "\n".join(offenders)
            )

    def test_known_mismatches_are_still_mismatched(self) -> None:
        by_id = {c["chain_id"]: c for c in self.chains}
        for chain_id, checks in sorted(KNOWN_SOURCE_MISMATCHES.items()):
            with self.subTest(chain_id=chain_id):
                self.assertIn(chain_id, by_id, "exempted chain is no longer registered")
                repaired = checks - self.violations(by_id[chain_id]).keys()
                self.assertFalse(
                    repaired,
                    f"these checks now pass; drop them from KNOWN_SOURCE_MISMATCHES: {sorted(repaired)}",
                )

    def test_sr126_sr127_defect_is_caught(self) -> None:
        # The shipped defect, reconstructed from the SR127 chain: the SR125 day
        # left in the citation and URL must fail both checks.
        chain = json.loads(json.dumps(
            next(c for c in self.chains if c["chain_id"].startswith("ec:lovs:data:insp-sitrep-127-"))
        ))
        chain["sources"][0]["citation"] = "INSP RDC, SitRep N°127/MVEBDB/16/09/2026, official WordPress PDF."
        chain["sources"][0]["url"] = "https://insp.cd/wp-content/uploads/2026/09/SitRep_MVEBDB_127_16_09_2026.pdf"
        self.assertEqual({"url", "citation"}, self.violations(chain).keys())


if __name__ == "__main__":
    unittest.main()
