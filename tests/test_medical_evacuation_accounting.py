import datetime as dt
import json
from pathlib import Path
import unittest
from lovs.forecast.pins_block11 import effective_reports

class MedicalEvacuationAccounting(unittest.TestCase):
    def test_nigeria_swiss_transfer_never_creates_a_confirmation_event(self):
        registry=json.loads((Path(__file__).resolve().parents[1]/'data/international-events.json').read_text())
        reports=effective_reports(registry,dt.date(2026,10,26))
        self.assertFalse(any(row['country']=='CHE' for row in reports))
        nigeria=[row for row in reports if row['country']=='NGA']
        self.assertEqual(len(nigeria),1)
        self.assertEqual(nigeria[0]['cumulative_confirmed'],1)
        transfer=next(row for row in registry['medical_evacuations'] if row.get('id')=='nga-che-2026-10-26')
        self.assertEqual(transfer['case_report_id'],nigeria[0]['report_id'])
        self.assertEqual(transfer['new_confirmed'],0)
        follow=transfer['contact_follow_up']
        self.assertEqual(follow['remaining_in_nigeria']+follow['transferred_to_switzerland'],follow['original_cohort'])
        self.assertIsNone(follow['additional_crew_or_receiving_contacts'])
        self.assertTrue(all(source['published']=='2026-10-26' and len(source['captured_sha256'])==64 for source in transfer['sources']))
