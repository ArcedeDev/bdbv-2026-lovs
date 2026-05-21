# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs_archive.add_restricted_snapshot.

Restricted (private_restricted_bytes) entries record hash-only provenance in
the manifest (raw_bytes_relpath=null) and keep the actual bytes under the
gitignored private/raw/<sha256>. This is the path used to archive third-party
publisher PDFs (WHO/Imperial/IOM) that we cannot redistribute in the public
repo. Uses a synthetic temp archive; no dependency on the live manifest.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest

from lovs import lovs_archive as arch


def _prov(content_hash: str, source_id: str = "restricted-src-1") -> arch.ProvenanceRecord:
    return arch.ProvenanceRecord(
        source_id=source_id,
        source_tier="academic_collab_who",
        publisher="Example Publisher",
        url="https://example.org/report.pdf",
        retrieved_at="2026-05-21T18:30:00Z",
        published_at="2026-05-20T00:00:00Z",
        content_hash=content_hash,
        license="CC-BY-NC-ND-4.0",
        extraction_status="success",
        root_provenance_chain=(),
    )


def _meta(normalized: dict | None = None) -> dict:
    return {
        "outbreak_id": "bdbv-uga-cod-2026",
        "pathogen": "BDBV",
        "country_scope": ["COD", "UGA"],
        "geography_id": "ituri-bdbv-corridor",
        "raw_bytes_relpath": None,
        "raw_archive_status": "private_restricted_bytes",
        "normalized_content": normalized or {"estimate": "400 to 900"},
    }


class TestAddRestrictedSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "manifest.json").write_text(
            json.dumps({"manifest_version": 1, "entries": []}), encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_adds_hash_only_entry_and_private_bytes(self):
        payload = b"%PDF-1.7 restricted bytes"
        h = hashlib.sha256(payload).hexdigest()
        arch.add_restricted_snapshot(self.root, _prov(h), _meta(), payload)

        manifest = json.loads((self.root / "manifest.json").read_text())
        self.assertEqual(len(manifest["entries"]), 1)
        entry = manifest["entries"][0]
        self.assertEqual(entry["raw_archive_status"], "private_restricted_bytes")
        self.assertIsNone(entry["raw_bytes_relpath"])
        self.assertEqual(entry["content_hash"], h)
        # Bytes live in the gitignored private/raw, NOT public raw/.
        self.assertTrue((self.root / "private" / "raw" / h).exists())
        self.assertFalse((self.root / "raw" / h).exists())
        # The whole archive still satisfies the loader contract.
        arch.load_archive(self.root)

    def test_hash_mismatch_raises(self):
        with self.assertRaises(arch.ArchiveContractError):
            arch.add_restricted_snapshot(
                self.root, _prov("0" * 64), _meta(), b"some bytes"
            )

    def test_requires_restricted_status(self):
        payload = b"bytes"
        h = hashlib.sha256(payload).hexdigest()
        bad = _meta()
        bad["raw_archive_status"] = "public_bytes"
        with self.assertRaises(arch.ArchiveContractError):
            arch.add_restricted_snapshot(self.root, _prov(h), bad, payload)

    def test_requires_null_relpath(self):
        payload = b"bytes"
        h = hashlib.sha256(payload).hexdigest()
        bad = _meta()
        bad["raw_bytes_relpath"] = "raw/whatever"
        with self.assertRaises(arch.ArchiveContractError):
            arch.add_restricted_snapshot(self.root, _prov(h), bad, payload)

    def test_idempotent_same_bytes(self):
        payload = b"idempotent"
        h = hashlib.sha256(payload).hexdigest()
        arch.add_restricted_snapshot(self.root, _prov(h), _meta(), payload)
        # Re-adding identical bytes/metadata is a no-op, not an error.
        arch.add_restricted_snapshot(self.root, _prov(h), _meta(), payload)
        manifest = json.loads((self.root / "manifest.json").read_text())
        self.assertEqual(len(manifest["entries"]), 1)

    def test_immutability_on_hash_change(self):
        first = b"first version"
        h1 = hashlib.sha256(first).hexdigest()
        arch.add_restricted_snapshot(self.root, _prov(h1), _meta(), first)
        second = b"second version, same source_id + retrieved_at"
        h2 = hashlib.sha256(second).hexdigest()
        with self.assertRaises(arch.ArchiveImmutabilityError):
            arch.add_restricted_snapshot(self.root, _prov(h2), _meta(), second)


if __name__ == "__main__":
    unittest.main()
