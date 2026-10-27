"""A blank national census is a source gap, never a zero or a carried stock."""
import copy
import unittest
import refresh_pipeline as pipeline


class UnpublishedIsolationCensus(unittest.TestCase):
    def test_explicit_gap_changes_provenance_without_substituting_a_census(self):
        promotion = copy.deepcopy(pipeline._SITREP_PROMOTIONS_BY_NUMBER[162])
        promotion['figures']['patients_en_isolement_hospitalisation'] = None
        promotion['figures']['not_published_fields'].append('patients_en_isolement_hospitalisation')
        note = pipeline._promotion_note(164, promotion)
        self.assertIn('patients en isolement-hospitalisation not published', note)
        self.assertIn('No zero, prior census or sum of provincial stocks is substituted', note)
        self.assertNotIn('publishes only the total isolation/CTE census', note)
        promotion['figures']['not_published_fields'].remove('patients_en_isolement_hospitalisation')
        with self.assertRaisesRegex(RuntimeError, 'missing integer figure'):
            pipeline._promotion_note(164, promotion)

    def test_existing_published_census_still_requires_an_integer(self):
        promotion = copy.deepcopy(pipeline._SITREP_PROMOTIONS_BY_NUMBER[162])
        for bad in [False, -0.5, '1108']:
            with self.subTest(value=bad):
                promotion['figures']['patients_en_isolement_hospitalisation'] = bad
                with self.assertRaisesRegex(RuntimeError, 'missing integer figure'):
                    pipeline._promotion_note(164, promotion)
