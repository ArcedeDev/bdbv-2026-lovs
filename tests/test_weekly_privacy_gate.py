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

from lovs.forecast.weekly import privacy, promotion_rows, spec
from tests.weekly_fixtures import BUILD_TIME, R, fixture_inputs, fixture_week

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
            # Under keys the vocabulary admits, so the text itself must be what fails.
            for key in ("value", "reason", "label", "province", "data_as_of", "sitrep", "path", "flags"):
                err = self._fails({key: text})
                self.assertEqual([p for p, _ in err.findings], [key])
                self.assertNotIn("key_outside_vocabulary", {r for _, r in err.findings})
                self.assertNotIn(text, str(err))
                self.assertNotIn(text, repr(err.findings))
            self.assertNotIn("finding", privacy.KEYS)

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
        last_data_day = max(r["data_as_of"] for r in self.build.rows)
        self._fails({"data_as_of": "2025-12-25"}, "date_outside_calendar")
        self._fails({"published_at": "2026-11-20T10:00:00Z"}, "date_outside_calendar")
        privacy.gate({"data_as_of": last_data_day}, self.ctx)                          # a data day
        privacy.gate({"registration_day": R.isoformat()}, self.ctx)                    # a registration day
        privacy.gate({"deadline": f"{R.isoformat()}T23:59:59Z"}, self.ctx)
        privacy.gate({"target_end": (R + dt.timedelta(days=28)).isoformat()}, self.ctx)  # the calendar
        privacy.gate({"built_at": BUILD_TIME}, self.ctx)                               # a build day

    # The reviewer's probes (review/WEEKLY-CODE-REVIEW.md I1): each must fail closed.
    def test_store_path_and_withheld_patterns_admit_no_free_text(self) -> None:
        day = max(r["data_as_of"] for r in self.build.rows)
        self._fails({"value": f"data/weekly-forecasts/case-travelled-beni-to-kasindi-on-{day}"})
        self._fails({"reason": "data/weekly-forecasts/bunia/mambasa/beni"})
        self._fails({"path": "data/weekly-forecasts/bunia/mambasa/beni"})
        self._fails({"reason": "withheld:body_moved_from_mambasa_to_bunia_by_road"})
        self._fails({"flags": ["withheld:body_moved_from_mambasa_to_bunia_by_road"]})
        self._fails({"files": {"kasindi-to-beni.csv": "0" * 64}}, "key_outside_vocabulary")
        privacy.gate({"flags": ["withheld:confirmed_total"]}, self.ctx)
        privacy.gate({"files": {"pins.csv": "0" * 64}}, self.ctx)
        privacy.gate({"path": f"data/weekly-forecasts/{R.isoformat()}/pins.csv"}, self.ctx)

    def test_patterns_match_the_whole_string(self) -> None:
        day = max(r["data_as_of"] for r in self.build.rows)
        self._fails({"sitrep": "180\n"})
        self._fails({"data_as_of": day + "\n"})
        self._fails({"sitrep": "180 "})
        self._fails({"pin_id": spec.pin_id(R, 1) + "\n"})

    def test_dates_only_under_date_keys(self) -> None:
        day = max(r["data_as_of"] for r in self.build.rows)
        for key in ("value", "reason", "label", "series", "status"):
            self._fails({key: day})
        self._fails({"built_at": day}, "date_outside_calendar")           # a data day is not a build day
        self._fails({"published_at": BUILD_TIME}, "date_outside_calendar")

    def test_ids_and_hashes_only_under_their_keys(self) -> None:
        self._fails({"reason": "a" * 40})
        self._fails({"value": "b" * 64})
        self._fails({"label": spec.pin_id(R, 1)})
        self._fails({"value": "180"})
        privacy.gate({"commit": "a" * 40, "sha256": "b" * 64, "pin_id": spec.pin_id(R, 1), "sitrep": "180"},
                     self.ctx)

    def test_only_ascii_digits_and_bounded_integers(self) -> None:
        self._fails({"sitrep": "\u0661\u0668\u0660"})                    # Arabic-Indic 180
        self._fails({"data_as_of": "\uff12\uff10\uff12\uff16-10-11"})   # full-width digits
        self._fails({"confirmed_total": int.from_bytes(b"Kasindi 11-03", "big")}, "number_out_of_range")
        privacy.gate({"confirmed_total": 123456}, self.ctx)

    def test_non_finite_numbers_fail(self) -> None:
        self._fails({"value": float("nan")}, "non_finite")
        self._fails({"realised": float("inf")}, "non_finite")

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
