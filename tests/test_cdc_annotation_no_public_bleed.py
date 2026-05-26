"""Pin the no-public-bleed contract for the SitRep 008/009 manifest annotation.

The 2026-05-25 cdc-fidelity-gate-and-sitrep-annotation change added two new
keys inside ``normalized_content`` of the SitRep 008 and 009 GraphQL manifest
entries: ``verified_per_bulletin_not_cumulative`` and
``verification_provenance``. These are private analytic metadata that record
the per-bulletin (not cumulative) verification finding. The contract is that
neither key may appear in any file scanned by the LOVS-side public artifact
leak scan, because they are scoped to the private manifest only.

The website-side publisher reads ``normalized_content`` selectively and never
serializes the whole dict, so the bleed risk is small. This test makes that
guarantee structural: if a future publisher commit (or any other code path)
ever writes these key names into a public artifact, the test fails.
"""

from __future__ import annotations

import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC_TEXT_GLOBS: tuple[str, ...] = (
    "README.md",
    "NUMBERS_AUDIT.md",
    "CITATIONS.md",
    "brief/brief.html",
    "data/live-bdbv-2026-output.json",
    "data/snapshot_contract.json",
    "data/evidence-chains.json",
    "data/external_sources/*.json",
    "data/external_sources/README.md",
    "deliverables/public-health-dataset/*.csv",
    "deliverables/public-health-dataset/*.json",
    "deliverables/public-health-dataset/*.xlsx",
    "deliverables/brief.pdf",
)
ANNOTATION_KEYS: tuple[str, ...] = (
    "verified_per_bulletin_not_cumulative",
    "verification_provenance",
)


def _public_text_files() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for pattern in PUBLIC_TEXT_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file() and path.suffix in {".md", ".html", ".json", ".csv"}:
                paths.append(path)
    return paths


class TestCdcAnnotationNoPublicBleed(unittest.TestCase):
    def test_annotation_keys_do_not_appear_in_public_text_artifacts(self):
        offenders: list[str] = []
        for path in _public_text_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for key in ANNOTATION_KEYS:
                if key in text:
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(f"{rel}: contains private annotation key {key!r}")
        self.assertEqual(
            offenders,
            [],
            "Private SitRep annotation keys leaked into public artifacts:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
