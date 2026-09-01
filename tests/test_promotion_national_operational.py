"""Gate: a promotion's national operational block must agree with its own edition.

This defect shipped. On SitRep 108 the per-province operational block advanced
while `province_operational.national` was left holding SitRep 107's values, so
the promotion carried 82.4% national contact-follow-up coverage and a 619-patient
isolation census under a 2026-08-30 headline. Nothing failed: every existing gate
reads either the headline counts or the per-province rows, and none of them
compared the two halves of the operational block against each other.

A carry-forward is the quietest failure this project has. The number is
well-formed, in range and entirely plausible; it is simply a different day's. The
one thing that gives it away is that it disagrees with figures the SAME promotion
already carries, so that is all this checks - no cross-edition comparison and no
external source, just internal agreement:

  national.contactsSeen          == operational_tables.contacts_total.contacts_seen
  national.contactsUnderFollowUp == operational_tables.contacts_total.contacts_under_follow_up
  national.followUpCoveragePct   == figures.contact_followup_rate_pct
  national.patientsInIsolation   == figures.patients_en_isolement_hospitalisation

These four hold on every promotion in the archive, which is what makes them
usable as a gate rather than as a warning.

DELIBERATELY NOT ASSERTED: that the national row equals the sum of the printed
per-province rows. It does not, on nine editions (SR059, SR063, SR078, SR079,
SR094, SR096 and three others), and those gaps are real: the national row can
count units the edition does not break out by province. Turning a legitimate
publisher behaviour into a failure would train the next person to silence this
gate, which is worse than not having it.

ONE HISTORICAL EXCEPTION, and it is the same defect rather than a false positive.
SR060 fails `contactsUnderFollowUp`, and the diagnosis is unambiguous: its whole
`operational_tables.contacts_total` block is SR059's. The block carries
contacts_seen_24h 10156 and contacts_under_follow_up 12370, which are SR059's
exact values, and its own `data_gap_note` still reads "Tableau 4 (12 July)" on
the 13 July edition. SR060's `national` block is the correct half there (8382
seen, 12430 followed, 67.4%), so the carry-forward sits on the opposite side
from the SitRep 108 case above.

It is listed rather than repaired because published snapshots are immutable; the
figure that reached the surface on that day is part of the record. Listing it
keeps the gate live for every future edition instead of being deleted to get
green, and leaves the defect visible instead of silently corrected.
"""
from __future__ import annotations

import json
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMOTIONS = sorted((REPO_ROOT / "data/sitrep_promotions").glob("sitrep-*.json"))

# (promotion file, field) pairs that are known-bad and immutable. See the module
# docstring: this is a diagnosed historical carry-forward, not a tolerated unknown.
KNOWN_HISTORICAL_CARRY_FORWARD = {
    ("sitrep-060-2026-07-13.json", "contactsUnderFollowUp"),
}


class TestPromotionNationalOperational(unittest.TestCase):
    def test_national_operational_agrees_with_its_own_promotion(self):
        checked = 0
        for path in PROMOTIONS:
            figures = json.loads(path.read_text(encoding="utf-8")).get("figures", {})
            national = (figures.get("province_operational") or {}).get("national")
            if not isinstance(national, dict):
                continue
            label = path.name
            totals = (figures.get("operational_tables") or {}).get("contacts_total") or {}

            pairs = [
                ("contactsSeen", totals.get("contacts_seen"), "operational_tables.contacts_total"),
                ("contactsUnderFollowUp", totals.get("contacts_under_follow_up"),
                 "operational_tables.contacts_total"),
                ("followUpCoveragePct", figures.get("contact_followup_rate_pct"),
                 "figures.contact_followup_rate_pct"),
                ("patientsInIsolation", figures.get("patients_en_isolement_hospitalisation"),
                 "figures.patients_en_isolement_hospitalisation"),
            ]
            for key, expected, source in pairs:
                actual = national.get(key)
                if actual is None or expected is None:
                    continue
                if (label, key) in KNOWN_HISTORICAL_CARRY_FORWARD:
                    self.assertNotEqual(
                        expected, actual,
                        f"{label}.{key} now agrees; drop it from "
                        f"KNOWN_HISTORICAL_CARRY_FORWARD rather than leaving a dead exception.",
                    )
                    continue
                self.assertEqual(
                    expected, actual,
                    f"{label}: province_operational.national.{key} is {actual} but {source} "
                    f"says {expected}. A national block that disagrees with its own edition is "
                    f"almost always a value carried forward from the previous SitRep.",
                )
                checked += 1

        self.assertGreater(checked, 0, "no promotion carried a comparable national block")


if __name__ == "__main__":
    unittest.main()
