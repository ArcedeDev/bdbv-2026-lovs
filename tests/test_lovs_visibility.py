# SPDX-License-Identifier: Apache-2.0
"""Tests for Module C visibility priors."""
from __future__ import annotations

import unittest

from lovs import lovs_visibility


class TestVisibilityPriorGrounding(unittest.TestCase):
    def test_rosello_is_default_delay_prior(self):
        self.assertEqual(lovs_visibility.ROSELLO_BDBV_DELAY_GAMMA, lovs_visibility.TOTAL_DELAY_GAMMA)
        self.assertEqual(
            "ec:lovs:grepi:reporting-delay-update:2026-05-23",
            lovs_visibility.TOTAL_DELAY_EVIDENCE_CHAIN_ID,
        )
        self.assertIn("Rosello", lovs_visibility.PRIOR_CITATIONS[0])

    def test_camacho_is_retained_as_sensitivity(self):
        self.assertEqual(
            (0.81, 0.18),
            lovs_visibility.SENSITIVITY_DELAY_GAMMAS["camacho_ebov_zaire"],
        )
        self.assertIn(
            "ec:lovs:module-c:reporting-delay-priors:2026-05-20",
            lovs_visibility.PRIOR_EVIDENCE_CHAIN_IDS,
        )


if __name__ == "__main__":
    unittest.main()
