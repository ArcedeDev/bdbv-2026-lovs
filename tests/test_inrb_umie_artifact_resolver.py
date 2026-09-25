# SPDX-License-Identifier: Apache-2.0
"""The INRB-UMIE release tarball is downloaded once and then found locally.

`refresh_pipeline.resolve_inrb_umie_artifact_path` saves a download under the
URL's file name. A later run must find that file there, so the release check
works offline once the tarball has been saved. Every run uses a temporary
private store and a stubbed download; nothing touches the network.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import pathlib
import tempfile
import unittest
from unittest import mock

import refresh_pipeline

PAYLOAD = b"INRB-UMIE release tarball bytes for the resolver test"


def _download_returning(payload: bytes) -> mock.MagicMock:
    urlopen = mock.MagicMock()
    urlopen.return_value.__enter__.return_value.read.return_value = payload
    return urlopen


class InrbUmieArtifactResolverTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        # The real manifest entry's URL, with the hash of the test bytes.
        entry = dict(
            refresh_pipeline._manifest_entry(refresh_pipeline.INRB_UMIE_SOURCE_ID),
            content_hash=hashlib.sha256(PAYLOAD).hexdigest(),
        )
        for patcher in (
            mock.patch.object(refresh_pipeline, "PRIVATE_SOURCE_DIR", root / "private" / "sources"),
            mock.patch.object(refresh_pipeline, "INRB_UMIE_ARTIFACT_PATH", root / "absent.tar.gz"),
            mock.patch.object(refresh_pipeline, "_manifest_entry", return_value=entry),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _resolve(self, urlopen: mock.MagicMock) -> pathlib.Path | None:
        with mock.patch.object(refresh_pipeline.urllib.request, "urlopen", urlopen):
            return refresh_pipeline.resolve_inrb_umie_artifact_path()

    def test_a_saved_tarball_is_found_without_the_network(self):
        saved = self._resolve(_download_returning(PAYLOAD))
        self.assertIsNotNone(saved)
        self.assertEqual(PAYLOAD, saved.read_bytes())
        offline = mock.MagicMock(side_effect=AssertionError("the resolver went to the network"))
        self.assertEqual(saved, self._resolve(offline))
        offline.assert_not_called()

    def test_a_saved_tarball_that_fails_the_hash_is_not_used(self):
        saved = self._resolve(_download_returning(PAYLOAD))
        saved.write_bytes(b"tampered")
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertIsNone(self._resolve(mock.MagicMock(side_effect=OSError("network down"))))
        self.assertIn("INRB-UMIE artifact unavailable: network down", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
