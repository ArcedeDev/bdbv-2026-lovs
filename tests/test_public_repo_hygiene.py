# SPDX-License-Identifier: Apache-2.0
"""Tests for public repository hygiene checks."""
from __future__ import annotations

import json
import re
import unicodedata
import unittest
from unittest import mock

from lovs import public_repo_hygiene


# A local path where a path begins, including after "file://" or a ":" in a path list;
# a URL path after a host ("https://host/home/...") is not one.
LOCAL_PATH = re.compile(r"(?<![\w.-])(?:/Users/|/home/|/private/tmp/|/private/var/|/var/folders/|/tmp/)|-Users-")


class TestPublicRepoHygiene(unittest.TestCase):
    def test_clean_current_tree(self):
        self.assertEqual([], public_repo_hygiene.scan_tracked_files())

    def test_local_path_rule_skips_url_paths(self):
        for text in (
            "saved to /tmp/x.csv", "at /Users/someone/notes", "(/home/someone)", "-Users-someone-",
            "file:///Users/someone/notes", "PYTHONPATH=/opt:/Users/someone/lib",
        ):
            self.assertTrue(LOCAL_PATH.search(text), text)
        for text in ("https://www.who.int/home/news", "https://example.org/tmp/report.html", "value=\"x/home/y\""):
            self.assertIsNone(LOCAL_PATH.search(text), text)

    def test_published_text_carries_no_hidden_characters_or_local_paths(self):
        """A hidden character is invisible to a reader but not to software, so text such as
        an instruction to a language model could ride in a source excerpt; a local path names
        the operator's machine. Neither may reach a published data file or document.

        Hidden means a format, private-use or control character other than tab and newline,
        or a variation selector. Unassigned characters are not checked, because what is
        unassigned depends on the Python version's Unicode data. Code is exempt: it may name
        such a character on purpose, as the byte-order-mark strippers do. Binary files are
        skipped.
        """
        files = [path for path in public_repo_hygiene._tracked_files() if path.suffix != ".py"]
        if not files:
            self.skipTest("not a git checkout: the tracked files cannot be listed")
        found = []
        for path in files:
            rel = path.relative_to(public_repo_hygiene.REPO_ROOT).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for char in set(text):
                code, category = ord(char), unicodedata.category(char)
                if (
                    category in ("Cf", "Co")
                    or (category == "Cc" and char not in "\t\n\r")
                    or 0x180B <= code <= 0x180F
                    or 0xFE00 <= code <= 0xFE0F
                    or 0xE0100 <= code <= 0xE01EF
                ):
                    found.append(f"{rel}: U+{code:04X}")
            found.extend(f"{rel}: {match.group(0)}" for match in LOCAL_PATH.finditer(text))
        self.assertEqual([], sorted(found))

    def test_detects_tool_provenance_marker(self):
        marker = "prepared by " + "co" + "dex"
        self.assertTrue(public_repo_hygiene.contains_marker(marker))

    def test_all_hygiene_scans_are_clean(self):
        self.assertEqual([], public_repo_hygiene.scan_all())

    def test_workflow_ref_is_not_treated_as_repository_content(self):
        marker_ref = "refs/heads/" + "co" + "dex" + "/release"
        with mock.patch.dict("os.environ", {"GITHUB_HEAD_REF": marker_ref}):
            self.assertEqual([], public_repo_hygiene.scan_all())
            self.assertNotEqual([], public_repo_hygiene.scan_environment_refs())


class TestPublicTreeBoundary(unittest.TestCase):
    """What may ship in the public tree, checked against what does.

    Outside a git checkout, the files on disk are what ships, so these checks still run
    there rather than skip.
    """

    RAW = "data/bundibugyo-2026/raw/"

    def test_every_shipped_raw_archive_is_public_bytes(self):
        """A file ships under raw/ only as the bytes of manifest entries that are all
        public_bytes and name it. Restricted publisher bytes stay in the ignored private
        store (LICENSES.md), so a force-added restricted file fails here."""
        manifest = public_repo_hygiene.REPO_ROOT / "data/bundibugyo-2026/manifest.json"
        entries = json.loads(manifest.read_text(encoding="utf-8"))["entries"]
        shipped = public_repo_hygiene.shipped_paths(self.RAW)
        self.assertTrue(shipped, "no raw archive ships, so this check would pass vacuously")
        refused = []
        for path in shipped:
            name = path[len(self.RAW):]
            naming = [entry for entry in entries if entry.get("content_hash") == name]
            if not naming or any(
                entry.get("raw_archive_status") != "public_bytes"
                or entry.get("raw_bytes_relpath") != f"raw/{name}"
                for entry in naming
            ):
                refused.append(path)
        self.assertEqual([], refused)

    def test_no_internal_or_restricted_path_ships(self):
        """Nothing .gitignore keeps out as internal or restricted ships: pipeline scaffolding
        under .process/ or .specs/ at any depth, which can name local paths, and restricted
        publisher material, which LICENSES.md keeps local: the private store and any file
        named *.restricted.*."""
        shipped = public_repo_hygiene.shipped_paths(".")
        self.assertTrue(shipped, "nothing ships, so this check would pass vacuously")
        internal = {".process", ".specs"}
        refused = [
            path
            for path in shipped
            if internal & set(path.split("/")[:-1])
            or path.startswith("data/bundibugyo-2026/private/")
            or ".restricted." in path.rsplit("/", 1)[-1]
        ]
        self.assertEqual([], refused)


class TestPublicationStateGuard(unittest.TestCase):
    def test_flags_not_for_publication_subjects(self):
        subjects = [
            "Release LOVS snapshot 2026-05-24 (review-only; not published)",
            "Add read-only calibration resolver and cycle-status composer",
            "do not publish: scratch",
            "Prepare May 24 publication surface",
        ]
        flagged = public_repo_hygiene.find_publication_state_markers(subjects)
        self.assertEqual(
            [
                "Release LOVS snapshot 2026-05-24 (review-only; not published)",
                "do not publish: scratch",
            ],
            flagged,
        )

    def test_read_only_is_not_review_only(self):
        # The calibration commit subject uses "read-only"; it must not trip "review-only".
        self.assertEqual(
            [],
            public_repo_hygiene.find_publication_state_markers(
                ["Add read-only calibration resolver"]
            ),
        )

    def test_clean_subjects_pass(self):
        self.assertEqual(
            [],
            public_repo_hygiene.find_publication_state_markers(
                ["Release LOVS snapshot 2026-05-24", "Add calibration resolver"]
            ),
        )

    def test_live_tree_has_no_unpublished_markers(self):
        self.assertEqual([], public_repo_hygiene.scan_new_commit_publication_state())


if __name__ == "__main__":
    unittest.main()


class TestMaintainerCoauthorship(unittest.TestCase):
    """The gate exists to keep TOOL provenance out, not human co-authorship.

    GitHub appends a co-authorship trailer to every squash merge whose commit
    author differs from the merging account. With the generic trailer treated as
    a provenance marker, main went red on its own hygiene gate after each merge
    and the only remedy was pinning another immutable SHA every cycle. A tool
    co-author is still caught by the vendor and product names, which is what the
    gate is actually for.

    The trailer is assembled from parts throughout, so this file does not itself
    carry a literal marker for the tracked-file scan to find.
    """

    TRAILER = "Co-authored" + "-by"

    def test_maintainer_trailer_is_not_a_provenance_marker(self):
        for address in public_repo_hygiene.MAINTAINER_COAUTHOR_ADDRESSES:
            with self.subTest(address=address):
                self.assertFalse(
                    public_repo_hygiene.contains_marker(
                        f"{self.TRAILER}: A Maintainer <{address}>"
                    )
                )

    def test_tool_coauthor_still_fails(self):
        vendor = "anth" + "ropic"
        product = "Clau" + "de"
        self.assertTrue(
            public_repo_hygiene.contains_marker(
                f"{self.TRAILER}: {product} <noreply@{vendor}.com>"
            )
        )

    def test_unknown_third_party_coauthor_still_fails(self):
        self.assertTrue(
            public_repo_hygiene.contains_marker(
                f"{self.TRAILER}: Someone <someone@example.invalid>"
            )
        )

    def test_exemption_does_not_mask_a_marker_elsewhere_in_the_message(self):
        message = (
            f"{self.TRAILER}: A Maintainer <frans@arcede.com>\n"
            + "Generated" + " with a tool"
        )
        self.assertTrue(public_repo_hygiene.contains_marker(message))
