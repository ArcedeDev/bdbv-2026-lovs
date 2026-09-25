"""Tests for lovs/lovs_live_ingest.py.

Network is mocked via injectable ``fetch_fn`` to keep the test suite
hermetic and deterministic.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import random
import re
import tempfile
import time
import unittest

from lovs import lovs_archive
from lovs import lovs_live_ingest


_SAMPLE_HTML = b"""
<html><head><title>WHO DON sample</title></head>
<body>
<article>
<h1>Ebola disease caused by Bundibugyo virus</h1>
<p>On 15 May 2026 the Government declared the outbreak.</p>
<p>As of 15 May 2026, 246 suspected cases and 80 deaths.</p>
<p>Four deaths among confirmed cases identified.</p>
<p>Affected: Rwampara Health Zone, Mongbwalu Health Zone, Bunia City. Kampala District (imported cases).</p>
<p>The strain is Bundibugyo virus (BDBV).</p>
</article>
</body></html>
"""

_CDC_HTML = b"""
<html><body>
<h1>Ebola Disease: Current Situation</h1>
<p>May 20, 2026</p>
<ul>
<li>As of May 19, the DRC and Uganda Ministries of Health report the following:</li>
<li>A total of 536 suspected cases, 105 probable cases, 34 confirmed cases, and 134 suspected deaths</li>
<li>In the last 24 to 48 hours, 26 new confirmed cases and 143 new suspected cases were identified.</li>
<li>These numbers include 2 confirmed cases including 1 death in Uganda in people who traveled from DRC.</li>
</ul>
<p>As of May 20, 2026, the Ebola Bundibugyo outbreak in DRC has been reported in 11 health zones in Ituri Province and in Nord-Kivu Province.</p>
<p>To date, no cases of Ebola disease have been confirmed in the United States because of this outbreak.</p>
</body></html>
"""

_CDC_HTML_MAY21 = b"""
<html><body>
<h1>Ebola Disease: Current Situation</h1>
<p>May 21, 2026</p>
<ul>
<li>As of May 21, the DRC and Uganda Ministries of Health report the following:</li>
<li>A total of 575 suspected cases, 51 confirmed cases, and 148 suspected deaths.</li>
<li>These numbers include 2 confirmed cases including 1 death in Uganda in people who traveled from DRC.</li>
</ul>
<p>As of May 20, 2026, the Ebola Bundibugyo outbreak in DRC has been reported in 11 health zones in Ituri Province and in Nord-Kivu Province.</p>
<p>To date, no cases of Ebola disease have been confirmed in the United States because of this outbreak.</p>
</body></html>
"""

_CDC_HTML_MAY23 = b"""
<html><body>
<h1>Ebola Disease: Current Situation</h1>
<p>May 23, 2026</p>
<ul>
<li>As of May 23, the DRC and Uganda Ministries of Health report the following:</li>
<li>A new confirmed case in Sud-Kivu Province; previously, cases had been confirmed in Ituri and Nord-Kivu provinces only.</li>
<li>DRC : A total of 746 suspected cases, 83 confirmed cases, 176 suspected deaths, and 9 confirmed deaths .</li>
<li>Uganda : A total of 5 confirmed cases and 1 confirmed death .</li>
<li>On May 23, Uganda announced 3 additional cases, all with clear links to the previously announced cases in people who traveled from DRC.</li>
</ul>
<p>As of May 23, 2026, the Ebola Bundibugyo outbreak in DRC has been confirmed in Ituri, Nord-Kivu, and Sud-Kivu provinces. Five cases related to the DRC outbreak also have been reported in Uganda's capital of Kampala.</p>
<p>To date, no cases of Ebola disease have been confirmed in the United States because of this outbreak.</p>
</body></html>
"""


def _mock_fetch(url: str) -> bytes:
    return _SAMPLE_HTML


def _fixed_now() -> str:
    return "2026-05-20T15:00:00Z"


class TestIngestOne(unittest.TestCase):

    def test_ingest_one_fetches_and_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            snap = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            self.assertIsNotNone(snap)
            self.assertEqual(snap.outbreak_id, "bdbv-uga-cod-2026")
            self.assertEqual(snap.pathogen, "BDBV")
            self.assertEqual(snap.provenance.retrieved_at, "2026-05-20T15:00:00Z")
            self.assertTrue((root / "manifest.json").exists())

    def test_ingest_one_extracts_case_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            snap = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            self.assertEqual(snap.normalized_content.get("cases_suspected"), 246)
            self.assertEqual(snap.normalized_content.get("deaths"), 80)

    def test_ingest_one_idempotent_on_same_bytes(self):
        """Second call with the same fetched bytes returns None (no new snapshot)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            first = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            self.assertIsNotNone(first)
            second = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=lambda: "2026-05-21T15:00:00Z",
            )
            self.assertIsNone(second)

    def test_ingest_one_raises_on_empty_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            with self.assertRaises(lovs_live_ingest.LiveIngestError):
                lovs_live_ingest.ingest_one(
                    lovs_live_ingest.WHO_DON_602_TARGET,
                    root,
                    fetch_fn=lambda url: b"",
                    now_fn=_fixed_now,
                )

    def test_ingest_one_raises_on_unknown_parser(self):
        bad_target = lovs_live_ingest.IngestTarget(
            source_id="bad",
            source_tier="official_who",
            publisher="WHO",
            url="https://example.com",
            license="CC-BY-NC-SA-3.0-IGO",
            outbreak_id="x",
            pathogen="BDBV",
            country_scope=("COD",),
            geography_id="x",
            parser_name="nonexistent_parser",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            with self.assertRaises(lovs_live_ingest.LiveIngestError):
                lovs_live_ingest.ingest_one(
                    bad_target, root, fetch_fn=_mock_fetch, now_fn=_fixed_now
                )

    def test_ingest_one_writes_raw_bytes_by_content_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            snap = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            raw_path = root / snap.raw_bytes_relpath
            self.assertTrue(raw_path.exists())
            self.assertEqual(raw_path.read_bytes(), _SAMPLE_HTML)


class TestWhoDonParser(unittest.TestCase):

    def test_parses_zones(self):
        normalized = lovs_live_ingest._parse_who_don_html(_SAMPLE_HTML)
        zones = normalized.get("affected_zones", [])
        for zone in ("rwampara", "mongbwalu", "bunia", "kampala"):
            self.assertIn(zone, zones)

    def test_parses_declaration_date(self):
        normalized = lovs_live_ingest._parse_who_don_html(_SAMPLE_HTML)
        self.assertIn("declaration_date_raw", normalized)
        self.assertIn("2026", normalized["declaration_date_raw"])

    def test_parses_short_narrative_excerpt(self):
        normalized = lovs_live_ingest._parse_who_don_html(_SAMPLE_HTML)
        excerpt = normalized.get("narrative_excerpt", "")
        self.assertGreater(len(excerpt), 0)
        # narrative_excerpt must be bounded (the parser caps at 60 words)
        self.assertLessEqual(len(excerpt.split()), 60)

    def test_deaths_among_confirmed_cases_is_a_death_count(self):
        """"Four deaths among confirmed cases" counts deaths, never confirmed cases."""
        normalized = lovs_live_ingest._parse_who_don_html(_SAMPLE_HTML)
        self.assertEqual(normalized.get("deaths_confirmed"), 4)
        self.assertNotIn("cases_confirmed", normalized)

    def test_don602_archive_gives_eight_confirmed_and_four_confirmed_deaths(self):
        """The archived DON602 page confirms eight samples and four deaths among them."""
        archive = (
            pathlib.Path(__file__).resolve().parent.parent
            / "data/bundibugyo-2026/raw/8b7fb1e1c8403b7a6015c804a3cd818c04b649ca23d791fe957e59119818218f"
        )
        normalized = lovs_live_ingest._parse_who_don_html(archive.read_bytes())
        self.assertEqual(
            (8, 4, 246, 80),
            tuple(normalized.get(field) for field in ("cases_confirmed", "deaths_confirmed", "cases_suspected", "deaths")),
        )

    def test_confirmed_counts_read_whole_numbers_and_refuse_near_misses(self):
        deaths = lovs_live_ingest._parse_confirmed_deaths
        confirmed = lovs_live_ingest._parse_confirmed_fallback
        for text, expected in (
            ("Twenty four deaths among confirmed cases", 24),
            ("Twenty-four deaths among confirmed cases", 24),
            ("1,234 deaths among confirmed cases", 1234),
            ("1\u00a0077 deaths among confirmed cases", 1077),
            ("1\u202f077 deaths among confirmed cases", 1077),
            # A plain space between numbers is ambiguous: "1 077" or a count after a 1.
            ("1 077 deaths among confirmed cases", None),
            ("of these, five deaths among confirmed cases", 5),
            ("Twenty\u2011four deaths among confirmed cases", 24),
            ("Twenty\u2013four deaths among confirmed cases", 24),
            # A larger number in words is refused, never read as its last word.
            ("One hundred and twenty deaths among confirmed cases", None),
            ("One hundred and four deaths among confirmed cases", None),
            ("Two hundred and twenty-one deaths among confirmed cases", None),
            ("One hundred and\n        twenty deaths among confirmed cases", None),
            ("One hundred and\u00a0 twenty deaths among confirmed cases", None),
            # One figure per country is ambiguous for a single field.
            ("one death among confirmed cases in Uganda; four deaths among confirmed cases in DRC", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(deaths(text), expected)
        for text, expected in (
            ("of which twenty four samples were confirmed", 24),
            ("of which eight samples analysed were confirmed", 8),
            ("the deaths of two health workers were confirmed", None),
            ("a total of 5 health workers have been confirmed", None),
            ("of which 8 were confirmed negative", None),
            ("of which 8 were confirmed as negative", None),
            ("of which 8 were confirmed to be negative", None),
            ("of which 8 samples were confirmed notably in Bunia", 8),
            ("of which 12 were not confirmed", None),
            ("of which one hundred were confirmed", None),
            ("of which one hundred and twelve were confirmed", None),
            ("1,234 cases were confirmed", 1234),
            ("1\u202f234 cases were confirmed", 1234),
            ("1 234 cases were confirmed", None),
            ("In week 20 146 cases were confirmed", None),
            ("As of 20 May 2026 146 cases were confirmed", 146),
            ("of which\n   eight samples analysed were\n confirmed", 8),
        ):
            with self.subTest(text=text):
                self.assertEqual(confirmed(text), expected)

    def test_confirmed_counts_edge_cases_are_read_or_refused(self):
        """Empty, boundary, Unicode, control-character and repeated input is read or refused, never misread."""
        deaths = lovs_live_ingest._parse_confirmed_deaths
        confirmed = lovs_live_ingest._parse_confirmed_fallback
        count = lovs_live_ingest._count_value
        repeated = "four deaths among confirmed cases. " * 50000
        for fn, text, expected in (
            (deaths, "", None),
            (confirmed, "", None),
            (count, "", None),
            (deaths, "deaths among confirmed cases were reported", None),
            (confirmed, "of which samples were confirmed", None),
            # Boundary counts: up to five digits or grouped thousands, words up to ninety-nine.
            (deaths, "0 deaths among confirmed cases", 0),
            (deaths, "zero deaths among confirmed cases", 0),
            (deaths, "one death among confirmed cases", 1),
            (deaths, "ninety-nine deaths among confirmed cases", 99),
            (deaths, "100 deaths among confirmed cases", 100),
            (deaths, "one hundred deaths among confirmed cases", None),
            (deaths, "99999 deaths among confirmed cases", 99999),
            (deaths, "100000 deaths among confirmed cases", None),
            (confirmed, "100,000 cases were confirmed", 100000),
            (confirmed, "100\u00a0000 cases were confirmed", 100000),
            # The minus sign and the fullwidth hyphen join a compound, and invisible
            # characters are removed, so the text reads as a person sees it.
            (deaths, "Twenty\u2212four deaths among confirmed cases", 24),
            (deaths, "Twenty\uff0dfour deaths among confirmed cases", 24),
            (deaths, "Twenty-\u200bfour deaths among confirmed cases", 24),
            (deaths, "Twenty-\u200ffour deaths among confirmed cases", 24),
            (deaths, "1,\u200f234 deaths among confirmed cases", 1234),
            (confirmed, "1\u200b234 cases were confirmed", 1234),
            (deaths, "\u2067four\u2069 deaths among confirmed cases", 4),
            (deaths, "Twenty-\ufefffour deaths among confirmed cases", 24),
            (deaths, "Twenty\tfour deaths among confirmed cases", 24),
            # An invisible character alone joins two words into none, and a count after a
            # character the rules do not name is refused.
            (deaths, "Twenty\u00adfour deaths among confirmed cases", None),
            (deaths, "Twenty\u2060four deaths among confirmed cases", None),
            (deaths, "Twenty\x00four deaths among confirmed cases", None),
            (deaths, "\x07four deaths among confirmed cases", None),
            (deaths, "1\x1f077 deaths among confirmed cases", None),
            (deaths, "1\u2009077 deaths among confirmed cases", None),
            (deaths, "1\u2007077 deaths among confirmed cases", None),
            # Another script's digits are digits; a superscript is not.
            (deaths, "\U0001d7d0\U0001d7d2 deaths among confirmed cases", 24),
            (deaths, "\u0662\u0664 deaths among confirmed cases", 24),
            (deaths, "\u00b2 deaths among confirmed cases", None),
            (count, "\u00b2", None),
            (count, "twenty eleven", None),
            # The end of a refused compound is never read alone, and a number keeps one
            # separator style.
            (deaths, "One hundred and twenty one deaths among confirmed cases", None),
            (deaths, "One hundred and sixty - two deaths among confirmed cases", None),
            (deaths, "1 ninety nine deaths among confirmed cases", None),
            (deaths, "In week 20 sixty two deaths among confirmed cases", None),
            (deaths, "sixty - two deaths among confirmed cases", 62),
            (confirmed, "of which twenty one samples were confirmed", 21),
            (confirmed, "1\u00a0125,294 cases were confirmed", None),
            # A mention whose figure is refused leaves the page ambiguous for one field.
            (deaths, "one hundred and twelve deaths among confirmed cases in DRC; "
                     "four deaths among confirmed cases in Uganda", None),
            (deaths, repeated, 4),
            (deaths, repeated + "five deaths among confirmed cases", None),
            # Another quantity, or a count of deaths, is not the confirmed total.
            (confirmed, "Of 246 samples tested, of which 80 per cent were confirmed", None),
            (confirmed, "80 deaths, of which four deaths were confirmed", None),
            (confirmed, "246 suspected cases and 80 deaths, of which four were confirmed", None),
            (confirmed, "80 deaths (of which four were confirmed)", None),
            # A count must name what it counts: without a noun, "which" can point at deaths.
            (confirmed, "80 deaths were reported, of which four were confirmed", None),
            (confirmed, "80 fatalities, of which four were confirmed", None),
            (confirmed, "80 deaths. Of these four were confirmed", None),
            (confirmed, "of which 12 were confirmed", None),
            (confirmed, "20 samples were tested, of which 13 new cases were confirmed", None),
            (confirmed, "of which two dozen were confirmed", None),
            (confirmed, "of which one in five were confirmed", None),
            (confirmed, "of these 5 health workers have been confirmed", None),
            (confirmed, "of which 8 samples were confirmed by PCR as negative", None),
            (confirmed, "of which 12 specimens tested have been confirmed", 12),
            (confirmed, "further analysis, of which eight samples analysed were confirmed as Orthoebolavirus by polymerase chain reaction", 8),
            # A day or a year after a month name, a Swiss apostrophe, a direction override that
            # reorders digits, and a number too large to be a count are refused.
            (deaths, "As of 20 May 2026 deaths among confirmed cases stood at four", None),
            (confirmed, "Since 15 May 2026 cases have been confirmed in Ituri", None),
            (deaths, "1'234 deaths among confirmed cases", None),
            (deaths, "\u202e42\u202c deaths among confirmed cases", None),
            (confirmed, "1,000,000,000,000 cases were confirmed", None),
            (confirmed, "1,000,000 cases were confirmed", 1000000),
            (deaths, "1" + ",000" * 1500 + " deaths among confirmed cases", None),
            (count, "1" * 5000, None),
            # A laboratory-confirmed mention counts as a mention that the pattern does not read.
            (deaths, "four deaths among confirmed cases in DRC and one death among laboratory-confirmed cases in Uganda", None),
        ):
            with self.subTest(fn=fn.__name__, text=text[:80]):
                self.assertEqual(expected, fn(text))

    def test_confirmed_count_readers_stay_linear_on_a_10_mb_page(self):
        """Each 10 MB input takes about a second; a pattern that backtracks badly would take hours."""
        mb = 1024 * 1024
        for text in ("1" + ",000" * (10 * mb // 4), "1 " * (5 * mb), "of which 8 " + "a" * (10 * mb)):
            with self.subTest(text=text[:20]):
                start = time.monotonic()
                self.assertIsNone(lovs_live_ingest._parse_confirmed_fallback(text))
                self.assertIsNone(lovs_live_ingest._parse_confirmed_deaths(text))
                self.assertLess(time.monotonic() - start, 60)

    def test_confirmed_counts_are_written_in_the_text(self):
        """Property: a count read is written in the text as that number (in digits, or in words
        up to ninety-nine) and is the count of a phrase built to carry it. A refusal passes.

        A lone no-break space between two numbers reads as a thousands separator, so the
        generated texts never join two numbers with one.
        """
        rng = random.Random(20260925)
        units = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                 "fourteen fifteen sixteen seventeen eighteen nineteen").split()
        tens = "twenty thirty forty fifty sixty seventy eighty ninety".split()

        def words(value):
            if value < 20 or value % 10 == 0:
                return units[value] if value < 20 else tens[value // 10 - 2]
            joiner = rng.choice([" ", "-", "\u2011", " - ", "-\u200b"])
            return tens[value // 10 - 2] + joiner + units[value % 10]

        def token():
            """A count as a page might write it, and the value a careful reader takes (None: refuse)."""
            kind = rng.randrange(6)
            if kind == 0:
                value = rng.choice([0, 1, 8, 99, 100, 999, 1077, 12345, 99999, 100000, rng.randrange(200000)])
                separator = rng.choice([",", "\u00a0", "\u202f", ""])
                if separator and value >= 1000:
                    return f"{value:,}".replace(",", separator), value
                return str(value), value if value <= 99999 else None
            if kind == 1:  # a plain space between digit groups is ambiguous
                value = rng.randrange(1000, 200000)
                return f"{value:,}".replace(",", " "), None
            if kind in (2, 3):
                value = rng.randrange(100)
                text = words(value)
                return (text.capitalize() if rng.random() < 0.3 else text), value
            if kind == 4:  # a larger number in words
                return rng.choice(["one hundred", "two hundred and twenty one", "one thousand and four",
                                   "one hundred and sixty-two"]), None
            value = rng.choice([24, 64])  # a character the rules do not name inside a compound
            return tens[value // 10 - 2] + rng.choice(["\x00", "\u00ad", "\u2060", "\x07"]) + units[value % 10], None

        def phrase():
            text, value = token()
            if rng.random() < 0.5:
                lead = rng.choice(["", "(", "a total of ", "and "])
                ending = rng.choice([" deaths among confirmed cases", " death among the confirmed case"])
                return "deaths", value, lead + text + ending
            negated = rng.random() < 0.2
            tail = rng.choice([" negative", " as negative", " to be negative"]) if negated else ""
            other = False
            if rng.random() < 0.6:
                # Words naming another quantity must make the reader refuse.
                other = rng.random() < 0.2
                gap = rng.choice(["per cent ", "deaths ", "new cases ", "health workers "] if other
                                 else ["", "samples ", "samples analysed ", "specimens tested ", "cases "])
                verb = rng.choice(["were", "are", "have been"])
                sentence = f"of {rng.choice(['which', 'these'])} {text} {gap}{verb} confirmed{tail}"
            else:
                sentence = f"{text} cases were confirmed{tail}"
            return "fallback", None if (negated or other) else value, sentence

        def written(text, value):
            text = re.sub("[\u00ad\u200b\u2060]", "", text)
            forms = {str(value)}
            if value >= 1000:
                forms |= {f"{value:,}".replace(",", separator) for separator in (",", "\u00a0", "\u202f")}
            if any(re.search(r"(?<!\d)" + re.escape(form) + r"(?!\d)", text) for form in forms):
                return True
            if value >= 100:
                return False
            if value < 20 or value % 10 == 0:
                pattern = units[value] if value < 20 else tens[value // 10 - 2]
            else:
                pattern = tens[value // 10 - 2] + r"[\s\-\u2010-\u2015]+" + units[value % 10]
            return re.search(r"(?<![a-z])" + pattern + r"(?![a-z])", text, re.IGNORECASE) is not None

        for _ in range(3000):
            parts = [phrase() for _ in range(rng.randrange(1, 4))]
            text = "".join(sentence + rng.choice([" ", ". ", "; ", "\n", "  ", " \u00a0 "]) for _, _, sentence in parts)
            carried = {kind: [value for k, value, _ in parts if k == kind] for kind in ("deaths", "fallback")}
            deaths = lovs_live_ingest._parse_confirmed_deaths(text)
            confirmed = lovs_live_ingest._parse_confirmed_fallback(text)
            with self.subTest(text=text):
                for value in (deaths, confirmed):
                    self.assertTrue(value is None or written(text, value))
                if deaths is not None:
                    self.assertEqual({deaths}, set(carried["deaths"]))
                if confirmed is not None:
                    self.assertIn(confirmed, carried["fallback"])

    def test_html_parse_ignores_scripts(self):
        with_script = (
            b"<html><body>"
            b"<script>alert(\"This should not affect parsing 999 suspected cases\")</script>"
            b"<p>3 suspected cases observed.</p></body></html>"
        )
        normalized = lovs_live_ingest._parse_who_don_html(with_script)
        self.assertEqual(normalized.get("cases_suspected"), 3)

    def test_declaration_date_prefers_canonical_ministry_sentence(self):
        """Regression: an antecedent 'declared on <date>' for a PREVIOUS outbreak
        must not beat the canonical 'On <date>, the Ministry ... declared' shape.

        Round 1 fix for Stage Two Important finding: the original regex captured
        '5 May 2026' (an alerted-WHO date) or '4 September 2025' (a previous-outbreak
        reference) rather than the canonical 15 May 2026 declaration. The tightened
        priority order in _DECLARATION_DATE_PATTERNS resolves this.
        """
        html = (
            b"<html><body>"
            b"<p>On 5 May 2026, WHO was alerted of an unknown illness.</p>"
            b"<p>The previous outbreak was declared on 4 September 2025.</p>"
            b"<p>On 15 May 2026, the Ministry of Public Health, Hygiene and Social Welfare, DRC, officially declared the 17th Ebola outbreak.</p>"
            b"</body></html>"
        )
        normalized = lovs_live_ingest._parse_who_don_html(html)
        self.assertEqual(normalized.get("declaration_date_raw"), "15 May 2026")
        self.assertIn("Ministry", normalized.get("declaration_text", ""))


class TestCdcCurrentSituationParser(unittest.TestCase):

    def test_parses_cdc_current_situation_tuple(self):
        normalized = lovs_live_ingest._parse_cdc_ebola_html(_CDC_HTML)
        self.assertEqual(normalized["publication_date"], "2026-05-20")
        self.assertEqual(normalized["data_as_of"], "2026-05-19")
        self.assertEqual(normalized["cases_suspected"], 536)
        self.assertEqual(normalized["cases_probable"], 105)
        self.assertEqual(normalized["cases_confirmed"], 34)
        self.assertEqual(normalized["deaths_suspected"], 134)
        self.assertEqual(normalized["new_confirmed_cases_24_to_48h"], 26)
        self.assertEqual(normalized["new_suspected_cases_24_to_48h"], 143)
        self.assertEqual(normalized["cases_confirmed_uganda"], 2)
        self.assertEqual(normalized["deaths_uganda"], 1)
        self.assertEqual(normalized["affected_health_zones_count"], 11)
        self.assertEqual(normalized["affected_provinces"], ["Ituri", "Nord-Kivu"])
        self.assertEqual(normalized["cases_confirmed_united_states"], 0)

    def test_parses_cdc_current_situation_tuple_without_probable(self):
        normalized = lovs_live_ingest._parse_cdc_ebola_html(_CDC_HTML_MAY21)
        self.assertEqual(normalized["publication_date"], "2026-05-21")
        self.assertEqual(normalized["data_as_of"], "2026-05-21")
        self.assertEqual(normalized["cases_suspected"], 575)
        self.assertNotIn("cases_probable", normalized)
        self.assertEqual(normalized["cases_confirmed"], 51)
        self.assertEqual(normalized["deaths_suspected"], 148)
        self.assertEqual(normalized["cases_confirmed_uganda"], 2)
        self.assertEqual(normalized["deaths_uganda"], 1)

    def test_parses_cdc_current_situation_country_split(self):
        normalized = lovs_live_ingest._parse_cdc_ebola_html(_CDC_HTML_MAY23)
        self.assertEqual(normalized["publication_date"], "2026-05-23")
        self.assertEqual(normalized["data_as_of"], "2026-05-23")
        self.assertEqual(normalized["cases_suspected"], 746)
        self.assertEqual(normalized["cases_suspected_drc"], 746)
        self.assertEqual(normalized["cases_confirmed_drc"], 83)
        self.assertEqual(normalized["cases_confirmed_uganda"], 5)
        self.assertEqual(normalized["cases_confirmed_total"], 88)
        self.assertEqual(normalized["cases_confirmed"], 88)
        self.assertEqual(normalized["deaths_suspected"], 176)
        self.assertEqual(normalized["deaths_confirmed_drc"], 9)
        self.assertEqual(normalized["deaths_uganda"], 1)
        self.assertEqual(normalized["new_confirmed_cases_uganda"], 3)

    def test_cdc_target_is_available_for_archive_ingest(self):
        self.assertEqual(
            lovs_live_ingest.CDC_CURRENT_SITUATION_TARGET.parser_name,
            "cdc_ebola_html",
        )
        self.assertEqual(
            lovs_live_ingest.CDC_CURRENT_SITUATION_TARGET.source_tier,
            "official_cdc",
        )

    def test_cdc_target_can_be_dated_without_reusing_source_id(self):
        target = lovs_live_ingest.cdc_current_situation_target("2026-05-21")
        self.assertEqual(target.source_id, "cdc-current-situation-2026-05-21")
        self.assertEqual(target.url, lovs_live_ingest.CDC_CURRENT_SITUATION_TARGET.url)


class TestIngestBdbv2026Convenience(unittest.TestCase):

    def test_returns_only_new_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            first = lovs_live_ingest.ingest_bdbv_2026(
                root, fetch_fn=_mock_fetch, now_fn=_fixed_now
            )
            self.assertEqual(len(first), 1)
            second = lovs_live_ingest.ingest_bdbv_2026(
                root, fetch_fn=_mock_fetch, now_fn=_fixed_now
            )
            self.assertEqual(len(second), 0)

    def test_archive_loadable_after_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            lovs_live_ingest.ingest_bdbv_2026(
                root, fetch_fn=_mock_fetch, now_fn=_fixed_now
            )
            archive = lovs_archive.load_archive(root)
            self.assertEqual(len(archive.snapshots), 1)
            self.assertEqual(archive.snapshots[0].pathogen, "BDBV")


class TestArchiveRestrictedBytes(unittest.TestCase):

    def test_load_archive_allows_private_restricted_hash_only_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "archive"
            root.mkdir()
            manifest = {
                "manifest_version": lovs_archive.MANIFEST_VERSION,
                "entries": [
                    {
                        "source_id": "restricted-source",
                        "source_tier": "official_continental_body",
                        "publisher": "Publisher",
                        "url": "https://example.com/source",
                        "retrieved_at": "2026-05-20T00:00:00Z",
                        "content_hash": "0" * 64,
                        "license": "publisher-terms-not-confirmed",
                        "extraction_status": "partial",
                        "root_provenance_chain": [],
                        "outbreak_id": "x",
                        "pathogen": "BDBV",
                        "country_scope": ["COD"],
                        "geography_id": "x",
                        "raw_archive_status": "private_restricted_bytes",
                        "raw_bytes_relpath": None,
                        "normalized_content": {"cases": 1},
                    }
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            archive = lovs_archive.load_archive(root)
            self.assertEqual(len(archive.snapshots), 1)
            self.assertIsNone(archive.snapshots[0].raw_bytes_relpath)
            self.assertEqual(
                archive.snapshots[0].raw_archive_status,
                "private_restricted_bytes",
            )

    def test_tracked_archive_bytes_match_their_hashes(self):
        """Every redistributed page hashes to its file name and to its manifest entry.

        The public dataset republishes each entry's content_hash as archive_sha256, so a
        changed page, or a mistyped hash on an entry whose bytes are in the repository,
        must fail here rather than publish a false claim. Restricted entries keep their
        bytes out of the repository, so their hashes cannot be checked here.
        """
        root = pathlib.Path(__file__).resolve().parent.parent / "data" / "bundibugyo-2026"
        archive = lovs_archive.load_archive(root)  # checks each public_bytes entry on disk
        self.assertTrue(any(s.raw_archive_status == "public_bytes" for s in archive.snapshots))
        # Only hash-named files: an operator's ignored folder under raw/ is not an archive.
        archives = [p for p in (root / "raw").iterdir() if p.is_file() and re.fullmatch(r"[0-9a-f]{64}", p.name)]
        self.assertTrue(archives)
        for path in sorted(archives):
            with self.subTest(archive=path.name[:16]):
                self.assertEqual(path.name, hashlib.sha256(path.read_bytes()).hexdigest())


class TestNowFnDefault(unittest.TestCase):

    def test_now_iso_z_format(self):
        s = lovs_live_ingest._now_utc_iso_z()
        self.assertTrue(s.endswith("Z"))
        self.assertGreaterEqual(len(s), 20)


class TestPersistStablePerDateCopy(unittest.TestCase):
    """Direct tests of the per-date dropbox-copy helper.

    The helper is the seam the live ingest path and the backfill loop share;
    its contract has to be exact (atomic write, same-bytes ok, different-bytes
    raise) before either caller can be trusted.
    """

    def test_writes_file_under_private_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            written = lovs_live_ingest._persist_stable_per_date_copy(
                root, "cdc-current-situation-2026-05-20", _CDC_HTML
            )
            self.assertTrue(written.exists())
            self.assertEqual(
                written,
                root / "private" / "sources"
                / "cdc-current-situation-2026-05-20.html",
            )
            self.assertEqual(written.read_bytes(), _CDC_HTML)

    def test_idempotent_same_bytes_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first = lovs_live_ingest._persist_stable_per_date_copy(
                root, "cdc-current-situation-2026-05-20", _CDC_HTML
            )
            mtime_before = first.stat().st_mtime_ns
            second = lovs_live_ingest._persist_stable_per_date_copy(
                root, "cdc-current-situation-2026-05-20", _CDC_HTML
            )
            self.assertEqual(first, second)
            # Same-bytes path returns without rewriting; mtime must be unchanged.
            self.assertEqual(first.stat().st_mtime_ns, mtime_before)

    def test_different_bytes_raises_with_both_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            lovs_live_ingest._persist_stable_per_date_copy(
                root, "cdc-current-situation-2026-05-20", _CDC_HTML
            )
            with self.assertRaises(lovs_live_ingest.LiveIngestError) as ctx:
                lovs_live_ingest._persist_stable_per_date_copy(
                    root, "cdc-current-situation-2026-05-20", _CDC_HTML_MAY21
                )
            message = str(ctx.exception)
            self.assertIn("refusing to overwrite", message)
            # Both hashes must be in the error so the operator can investigate.
            self.assertIn("sha256=", message)

    def test_refuses_when_target_path_is_a_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            sources_dir = root / "private" / "sources"
            sources_dir.mkdir(parents=True)
            # Simulate operator confusion: a directory sitting at the per-date
            # path. Helper must refuse rather than write under it.
            (sources_dir / "cdc-current-situation-2026-05-20.html").mkdir()
            with self.assertRaises(lovs_live_ingest.LiveIngestError) as ctx:
                lovs_live_ingest._persist_stable_per_date_copy(
                    root, "cdc-current-situation-2026-05-20", _CDC_HTML
                )
            self.assertIn("not a regular file", str(ctx.exception))

    def test_successful_write_leaves_no_tmp_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            written = lovs_live_ingest._persist_stable_per_date_copy(
                root, "cdc-current-situation-2026-05-20", _CDC_HTML
            )
            self.assertTrue(written.is_file())
            # The mkstemp-based tmp file is process-unique; after the atomic
            # rename publishes the final path, no leftover .tmp siblings should
            # remain in the sources directory.
            sources_dir = written.parent
            tmp_files = [p for p in sources_dir.iterdir() if p.suffix == ".tmp"]
            self.assertEqual(tmp_files, [])

    def test_should_retain_predicate_only_matches_cdc_prefix(self):
        self.assertTrue(
            lovs_live_ingest._should_retain_stable_per_date_copy(
                "cdc-current-situation-2026-05-21"
            )
        )
        self.assertFalse(
            lovs_live_ingest._should_retain_stable_per_date_copy(
                "who-don602-2026-05-15-live"
            )
        )
        self.assertFalse(
            lovs_live_ingest._should_retain_stable_per_date_copy(
                "ecdc-bdbv-drc-uga-2026-05-25-live"
            )
        )


class TestIngestOnePerDateDropboxRetention(unittest.TestCase):
    """End-to-end checks that ``ingest_one`` itself wires the helper correctly."""

    def _cdc_target(self, publication_date: str) -> lovs_live_ingest.IngestTarget:
        return lovs_live_ingest.cdc_current_situation_target(publication_date)

    def test_cdc_target_writes_per_date_dropbox_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            lovs_live_ingest.ingest_one(
                self._cdc_target("2026-05-20"),
                root,
                fetch_fn=lambda url: _CDC_HTML,
                now_fn=_fixed_now,
            )
            self.assertTrue(
                (root / "private" / "sources"
                 / "cdc-current-situation-2026-05-20.html").exists()
            )

    def test_who_don_target_does_not_write_per_date_dropbox_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            sources_dir = root / "private" / "sources"
            # Either the directory was never created, or it has no per-date file
            # for the WHO DON target. Both states are acceptable; what we forbid
            # is a per-date file leaking out for non-CDC source_ids.
            if sources_dir.exists():
                self.assertEqual(list(sources_dir.iterdir()), [])

    def test_idempotent_skip_still_heals_missing_per_date_copy(self):
        """If a prior live ingest stored bytes but the dropbox file was deleted,
        a re-run must restore it even though add_snapshot would have skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            target = self._cdc_target("2026-05-20")
            lovs_live_ingest.ingest_one(
                target,
                root,
                fetch_fn=lambda url: _CDC_HTML,
                now_fn=_fixed_now,
            )
            per_date = (
                root / "private" / "sources"
                / "cdc-current-situation-2026-05-20.html"
            )
            self.assertTrue(per_date.exists())
            per_date.unlink()

            # Second call: bytes identical, so add_snapshot skips. The per-date
            # helper runs before that skip, so the file should be back.
            result = lovs_live_ingest.ingest_one(
                target,
                root,
                fetch_fn=lambda url: _CDC_HTML,
                now_fn=_fixed_now,
            )
            self.assertIsNone(result)  # idempotent archive skip
            self.assertTrue(per_date.exists())
            self.assertEqual(per_date.read_bytes(), _CDC_HTML)


class TestBackfillCdcPerDateCopies(unittest.TestCase):
    """The backfill loop recovers per-date dropbox copies for already-archived CDC entries."""

    def _seed_archive_with_cdc(
        self,
        root: pathlib.Path,
        publication_date: str,
        raw_bytes: bytes,
    ) -> None:
        """Drive a CDC entry into the archive without writing the per-date file
        first (mimics the pre-fix world where May 20-24 had raw/{hash} but no
        private/sources/ entry)."""
        target = lovs_live_ingest.cdc_current_situation_target(publication_date)
        lovs_live_ingest.ingest_one(
            target,
            root,
            fetch_fn=lambda url: raw_bytes,
            now_fn=lambda: f"{publication_date}T15:00:00Z",
        )
        # Simulate the legacy state: per-date file did not exist before this change.
        (root / "private" / "sources" / f"{target.source_id}.html").unlink()

    def test_backfill_copies_missing_per_date_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            self._seed_archive_with_cdc(root, "2026-05-20", _CDC_HTML)
            self._seed_archive_with_cdc(root, "2026-05-21", _CDC_HTML_MAY21)
            result = lovs_live_ingest.backfill_cdc_per_date_copies(root)
            self.assertEqual(result["copied"], 2)
            self.assertEqual(result["already_present"], 0)
            self.assertEqual(result["missing_raw"], [])
            self.assertTrue(
                (root / "private" / "sources"
                 / "cdc-current-situation-2026-05-20.html").exists()
            )
            self.assertTrue(
                (root / "private" / "sources"
                 / "cdc-current-situation-2026-05-21.html").exists()
            )

    def test_backfill_skips_entries_already_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            # First ingest leaves the per-date file in place (post-fix behavior).
            target = lovs_live_ingest.cdc_current_situation_target("2026-05-20")
            lovs_live_ingest.ingest_one(
                target, root,
                fetch_fn=lambda url: _CDC_HTML, now_fn=_fixed_now,
            )
            result = lovs_live_ingest.backfill_cdc_per_date_copies(root)
            self.assertEqual(result["copied"], 0)
            self.assertEqual(result["already_present"], 1)
            self.assertEqual(result["missing_raw"], [])

    def test_backfill_reports_missing_raw_bytes(self):
        """If a manifest entry's raw/{content_hash} file is gone, the backfill
        records the source_id under missing_raw rather than raising."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            self._seed_archive_with_cdc(root, "2026-05-20", _CDC_HTML)
            # Delete the content-addressed bytes to mimic a public-only clone.
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            relpath = next(
                e["raw_bytes_relpath"] for e in manifest["entries"]
                if e["source_id"] == "cdc-current-situation-2026-05-20"
            )
            (root / relpath).unlink()
            result = lovs_live_ingest.backfill_cdc_per_date_copies(root)
            self.assertEqual(result["copied"], 0)
            self.assertEqual(result["already_present"], 0)
            self.assertEqual(
                result["missing_raw"], ["cdc-current-situation-2026-05-20"]
            )

    def test_backfill_ignores_non_cdc_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET, root,
                fetch_fn=_mock_fetch, now_fn=_fixed_now,
            )
            result = lovs_live_ingest.backfill_cdc_per_date_copies(root)
            # No CDC entries → no work; missing_raw stays empty.
            self.assertEqual(result["copied"], 0)
            self.assertEqual(result["already_present"], 0)
            self.assertEqual(result["missing_raw"], [])

    def test_backfill_raises_when_manifest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            root.mkdir()
            with self.assertRaises(lovs_live_ingest.LiveIngestError):
                lovs_live_ingest.backfill_cdc_per_date_copies(root)


class TestLiveIngestCli(unittest.TestCase):
    """CLI dispatch arm for the backfill subcommand."""

    def test_backfill_cdc_returns_zero_and_prints_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            target = lovs_live_ingest.cdc_current_situation_target("2026-05-20")
            lovs_live_ingest.ingest_one(
                target, root,
                fetch_fn=lambda url: _CDC_HTML, now_fn=_fixed_now,
            )
            (root / "private" / "sources" / f"{target.source_id}.html").unlink()

            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = lovs_live_ingest.main(["--backfill-cdc", str(root)])
            self.assertEqual(code, 0)
            stdout = buf.getvalue()
            self.assertIn("copied=1", stdout)
            self.assertIn("already_present=0", stdout)
            self.assertIn("missing_raw=0", stdout)

    def test_no_args_prints_help_and_exits_nonzero(self):
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = lovs_live_ingest.main([])
        self.assertNotEqual(code, 0)
        self.assertIn("--backfill-cdc", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
