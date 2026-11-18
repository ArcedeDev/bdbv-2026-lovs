# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 10: test_weekly_privacy_gate.

The gate admits only a closed vocabulary (ids, codes, hashes, the dates of data,
publication, registration and build days, and province or zone names as row keys) and
rejects everything else. SitRep 180's evidence packet carries movement detail that the
reviewed record withholds; that text is read from a local copy of the private packets at
run time and never written into this repository, and the gate's errors never echo it.
"""
from __future__ import annotations

import copy
import datetime as dt
import glob
import json
import os
import tempfile
import unittest
from pathlib import Path

from lovs.forecast.weekly import privacy, promotion_rows
from tests.weekly_fixtures import R, fixture_inputs, fixture_week

REPO = Path(__file__).resolve().parents[1]
PACKETS = Path(os.environ.get(
    "LOVS_EVIDENCE_PACKETS", REPO.parent / "lovs-evidence-mcp" / "data" / "sitrep-packets"))


def _strings_under(obj, key: str) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                stack = [v]
                while stack:
                    x = stack.pop()
                    if isinstance(x, str):
                        out.append(x)
                    elif isinstance(x, dict):
                        stack.extend(x.values())
                    elif isinstance(x, list):
                        stack.extend(x)
            else:
                out.extend(_strings_under(v, key))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_strings_under(v, key))
    return out


class PrivacyGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.build, cls.public, cls.private, cls.inputs = fixture_week(Path(cls.tmp.name))
        cls.ctx = cls.build.privacy_context

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _fails(self, obj, reason: str | None = None) -> privacy.PrivacyError:
        with self.assertRaises(privacy.PrivacyError) as caught:
            privacy.gate(obj, self.ctx)
        if reason:
            self.assertIn(reason, {r for _, r in caught.exception.findings})
        return caught.exception

    def test_sr180_packet_challenge_text_fails(self) -> None:
        files = sorted(glob.glob(str(PACKETS / "sitrep-180-*.json")))
        if not files:
            self.skipTest("evidence packets not available locally")
        packet = json.loads(Path(files[0]).read_text(encoding="utf-8"))
        texts = _strings_under(packet, "challenges")
        self.assertTrue(texts)
        for text in texts:
            for record in ({"sitrep": "180", "finding": text}, {"sitrep": "180", "province": text},
                           {"sitrep": "180", "data_as_of": text}):
                err = self._fails(record)
                self.assertNotIn(text, str(err))
                self.assertNotIn(text, repr(err.findings))

    def test_free_text_stand_in_fails(self) -> None:
        # Shape of the SR180 case without its content: a sentence in a value slot.
        self._fails({"sitrep": "180", "finding": "a team moved between two sites over three days"},
                    "free_text")

    def test_scope_note_fails(self) -> None:
        promo = self.inputs["promotions"][-1]
        note = promo["figures"]["affected_health_zone_footprint"]["scope_note"]
        self._fails({"sitrep": str(promo["sitrep_number"]), "scope_note": note})
        # The promotion row builder never carries it.
        row = promotion_rows.row_from_promotion(promo)
        self.assertNotIn(note, json.dumps(row))

    def test_two_place_string_fails(self) -> None:
        self._fails({"province": "Ituri, Nord-Kivu"}, "place_sequence")
        self._fails({"zone": "Bunia Beni"}, "place_sequence")
        self._fails({"province": "Ituri to Nord-Kivu"}, "place_sequence")
        self._fails({"sitrep": "185", "note": ["Bunia", "Beni"]})
        privacy.gate({"province": "Ituri"}, self.ctx)
        privacy.gate({"zone": "Bunia", "province": "Ituri"}, self.ctx)

    def test_place_names_only_as_row_keys(self) -> None:
        self._fails({"sitrep": "185", "series": "Ituri"}, "place_outside_row_key")

    def test_non_data_date_fails(self) -> None:
        self._fails({"data_as_of": "2025-12-25"}, "date_outside_calendar")
        self._fails({"published_at": "2026-11-20T10:00:00Z"}, "date_outside_calendar")
        privacy.gate({"data_as_of": "2026-11-15"}, self.ctx)              # a data day
        privacy.gate({"registration_day": R.isoformat()}, self.ctx)       # a registration day
        privacy.gate({"deadline": f"{R.isoformat()}T23:59:59Z"}, self.ctx)
        privacy.gate({"target_end": "2026-12-23"}, self.ctx)              # R + 28, the calendar
        privacy.gate({"built_at": "2026-11-25T12:31:00Z"}, self.ctx)     # a build day

    def test_unknown_key_fails(self) -> None:
        self._fails({"travel_history": 3}, "key_outside_vocabulary")

    def test_error_names_paths_not_values(self) -> None:
        err = self._fails({"rows": [{"finding": "sensitive words here"}]})
        self.assertNotIn("sensitive words here", str(err))
        self.assertIn("rows[0].finding", {p for p, _ in err.findings})

    def test_built_week_passes(self) -> None:
        for path in sorted(self.public.iterdir()):
            if path.suffix == ".json":
                privacy.gate(json.loads(path.read_text(encoding="utf-8")), self.ctx)
            elif path.suffix == ".jsonl":
                for line in path.read_text(encoding="utf-8").splitlines():
                    privacy.gate(json.loads(line), self.ctx)
            elif path.suffix == ".csv":
                privacy.gate(privacy.csv_rows(path.read_text(encoding="utf-8")), self.ctx)

    def test_archived_values_equal_the_promotion(self) -> None:
        records = [json.loads(line) for name in ("zones-delta.jsonl", "province-covariates-delta.jsonl",
                                                  "vintage-delta.jsonl")
                   for line in (self.public / name).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(records)
        privacy.check_archive_values(records, self.inputs["promotions"], self.inputs["extract"])
        tampered = copy.deepcopy(records)
        zone = next(r for r in tampered if "zone" in r)
        zone["confirmed"] = (zone.get("confirmed") or 0) + 1
        with self.assertRaises(privacy.PrivacyError) as caught:
            privacy.check_archive_values(tampered, self.inputs["promotions"], self.inputs["extract"])
        self.assertIn("differs_from_source", {r for _, r in caught.exception.findings})


if __name__ == "__main__":
    unittest.main()
