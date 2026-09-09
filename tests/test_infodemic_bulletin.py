"""The COUSP infodemic bulletins: archived here, but neither counted nor republished.

This is the first source family in the corpus that is NOT counts. It is community
feedback, calls to the national 151 line and media monitoring, and it fails in ways
counts never do: a contribution read as a case, a provincial share read as a
prevalence, a retrospective weekly window read as a daily clock.

It is also marked "Usage interne / riposte" by its publisher, on every edition. This
repository is PUBLIC, so it records that the bulletins exist and when they describe,
never what they say. Figures live in the private evidence corpus.
"""

import json
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lovs import source_dates  # noqa: E402

MANIFEST = REPO_ROOT / "data/bundibugyo-2026/manifest.json"
REGISTRY = REPO_ROOT / "data/external_sources/source_registry.json"

# window end (the last day the edition describes) -> archived entry
EDITIONS = {
    "insp-infodemic-media25344-2026-08-17": ("2026-08-08", "2026-08-17"),
    "insp-infodemic-media25459-2026-08-27": ("2026-08-23", "2026-08-27"),
    "insp-infodemic-media25537-2026-09-08": ("2026-08-30", "2026-09-08"),
}


def _entries():
    entries = json.loads(MANIFEST.read_text())["entries"]
    return {e["source_id"]: e for e in entries if e.get("source_id") in EDITIONS}


class TestInfodemicBulletinsAreNotCases(unittest.TestCase):
    def setUp(self):
        self.entries = _entries()
        self.assertEqual(set(EDITIONS), set(self.entries), "all three editions must be archived")

    def test_every_edition_declares_itself_a_non_count_source(self):
        for source_id, entry in self.entries.items():
            with self.subTest(source_id=source_id):
                normalized = entry["normalized_content"]
                self.assertTrue(normalized.get("not_a_case_source"))
                self.assertEqual("context_only_never_counts", normalized.get("model_use"))

    def test_no_edition_advances_the_snapshot_publication_route(self):
        # These publish one to two weeks after the window they describe. If one could
        # trigger a snapshot it would open a public publication state on a day the
        # case series has nothing to say.
        for source_id, entry in self.entries.items():
            with self.subTest(source_id=source_id):
                self.assertFalse(source_dates.source_triggers_snapshot(entry))

    def test_data_date_is_each_editions_own_window_end_not_its_publication_date(self):
        # The SitRep-title lesson, generalised: a document's data clock is what the
        # data describes, never when it was posted. Here they differ by 9 to 10 days,
        # and each edition has its OWN window, so a single family-wide date is wrong.
        for source_id, (data_date, published) in EDITIONS.items():
            with self.subTest(source_id=source_id):
                entry = self.entries[source_id]
                self.assertEqual(data_date, source_dates.source_data_date(entry))
                self.assertEqual(published, source_dates.source_publication_date(entry))
                self.assertLess(data_date, published)

    def test_public_manifest_carries_no_figures_from_an_internal_use_document(self):
        # The bulletins are stamped "Usage interne / riposte" and this repo is public.
        # The entry may say the source exists and when it applies; it may not say what
        # the source found.
        for source_id, entry in self.entries.items():
            with self.subTest(source_id=source_id):
                normalized = entry["normalized_content"]
                self.assertEqual("usage_interne_riposte", normalized.get("classification"))
                self.assertTrue(normalized.get("content_withheld_reason"))
                blob = json.dumps(normalized)
                for leaked in ("contributions_total", "by_theme", "by_province", "verbatim", "convergences"):
                    self.assertNotIn(leaked, blob)

    def test_bytes_are_hash_recorded_and_not_redistributed(self):
        for source_id, entry in self.entries.items():
            with self.subTest(source_id=source_id):
                self.assertTrue(entry.get("content_hash"))
                self.assertEqual("private_restricted_bytes", entry.get("raw_archive_status"))


class TestInfodemicRegistryEntry(unittest.TestCase):
    def setUp(self):
        sources = json.loads(REGISTRY.read_text())["sources"]
        hits = [s for s in sources if s.get("registry_id") == "insp-infodemic-bulletin"]
        self.assertEqual(1, len(hits))
        self.source = hits[0]

    def test_declares_restricted_redistribution_and_the_internal_use_marking(self):
        self.assertEqual("restricted", self.source["redistribution"])
        self.assertEqual("usage_interne_riposte", self.source.get("classification"))

    def test_carries_no_counts_feed(self):
        self.assertNotIn("counts", self.source["feeds"])

    def test_notes_record_why_a_provincial_share_here_is_not_a_prevalence(self):
        notes = self.source["notes"].lower()
        self.assertIn("not a case source", notes)
        self.assertIn("intensity of the collection apparatus", notes)
        self.assertIn("never be joined to the daily snapshot series", notes)


class TestInfodemicSeriesInventory(unittest.TestCase):
    """What we know about the SERIES, so nobody re-hunts a settled question.

    Two of the six windows carry no bytes, for two different reasons, and the
    difference matters: one was announced and then withdrawn, the other was never
    published at all. Recording them as the same kind of hole would lose that.
    """

    def setUp(self):
        sources = json.loads(REGISTRY.read_text())["sources"]
        self.source = [s for s in sources if s.get("registry_id") == "insp-infodemic-bulletin"][0]
        self.inventory = self.source["series_inventory"]
        self.by_window = {e["window"]: e for e in self.inventory["editions"]}

    def test_every_archived_edition_in_the_inventory_is_actually_archived(self):
        entries = _entries()
        for window, edition in self.by_window.items():
            if edition["bytes"] != "archived" or "media_id" not in edition:
                continue
            with self.subTest(window=window):
                match = [s for s in entries if str(edition["media_id"]) in s]
                self.assertTrue(match, f"{window} claims archived but no manifest entry carries its media id")

    def test_the_withdrawn_july_edition_records_its_failed_recovery(self):
        # Searched and settled, so nobody repeats it. The two channels are recorded
        # with DIFFERENT verdicts on purpose: the Internet Archive genuinely never
        # captured it, while archive.today rate-limited us. Rate limiting is not
        # evidence of absence, and recording it as "not found" would be a small lie.
        july = self.by_window["2026-07-27/2026-07-31"]
        recovery = july["recovery_attempt"]
        self.assertEqual("not_recoverable", recovery["outcome"])
        self.assertEqual("never_captured", recovery["internet_archive"]["result"])
        self.assertEqual("inconclusive", recovery["archive_today"]["result"])
        self.assertGreaterEqual(len(recovery["internet_archive"]["checks"]), 5)

    def test_the_withdrawn_july_edition_is_recorded_without_a_hash(self):
        # Announced on 2026-08-10, then withdrawn: the media index still advertises a
        # filesize but every URL 404s. We must not invent a hash for bytes we never held.
        july = self.by_window["2026-07-27/2026-07-31"]
        self.assertEqual("withdrawn_404", july["bytes"])
        self.assertNotIn("sha256", json.dumps(july))
        entries = _entries()
        self.assertFalse([s for s in entries if "25307" in s], "must not archive an edition we cannot fetch")

    def test_the_09_16_august_window_is_recorded_as_never_published(self):
        # Confirmed absent across three channels, not merely unfound. Stated as such so
        # a later reader does not repeat the search.
        gap = self.by_window["2026-08-09/2026-08-16"]
        self.assertEqual("never_published", gap["bytes"])
        self.assertIsNone(gap["published"])
        self.assertIn("CONFIRMED ABSENT", gap["note"])

    def test_the_inventory_records_how_it_was_verified(self):
        method = self.inventory["method"].lower()
        self.assertIn("posts feed", method)
        self.assertIn("media item", method)
        self.assertIn("url probes", method)


if __name__ == "__main__":
    unittest.main()
