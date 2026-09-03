# SPDX-License-Identifier: Apache-2.0
"""Guard: a SitRep promotion chain's steps must belong to that SitRep.

Each cycle's evidence chain is hand-authored by cloning the previous cycle's
chain. That is fine for shape, but the step bodies carry the *numbers* a
reviewer checked - the zone sums, the alert reconciliation, the death movement.
When the clone is committed with the previous edition's step ids and findings
still in place, the chain asserts one edition's provenance under another
edition's id, and nothing else notices: the headline is right, the sources block
is right, the suite stays green, and only the audit trail is wrong.

That has already happened three times (SR106, SR108, SR109 all shipped carrying
an earlier SitRep's steps), which is what makes it worth a gate rather than a
per-cycle reminder. The cheap, reliable signal is the step id: every step id in
`ec:lovs:data:inrb-sitrep-NNN-visual-promotion:*` embeds a SitRep number, and it
must be NNN.

The three chains already published with stale steps are listed as explicit
exceptions rather than silently tolerated, so the gate is honest about what it
is not yet enforcing. Removing an entry from that set is a real repair: the step
findings have to be re-derived from that edition before its id will match.
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "data" / "evidence-chains.json"

CHAIN_ID = re.compile(r"^ec:lovs:data:inrb-sitrep-(\d+)-visual-promotion:")
STEP_ID = re.compile(r"inrb-(\d+)-")

# Chains already published with an earlier edition's steps. Each needs its step
# findings re-derived from its own SitRep before it can leave this set; none may
# be added without that same repair being scheduled.
KNOWN_STALE_STEP_PROVENANCE = frozenset(
    {
        "ec:lovs:data:inrb-sitrep-106-visual-promotion:2026-08-28",
        "ec:lovs:data:inrb-sitrep-108-visual-promotion:2026-08-30",
        "ec:lovs:data:inrb-sitrep-109-visual-promotion:2026-08-31",
    }
)


def _sitrep_promotion_chains() -> list[dict]:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return [c for c in payload["chains"] if CHAIN_ID.match(str(c.get("chain_id", "")))]


class TestSitRepChainStepProvenance(unittest.TestCase):
    def test_step_ids_belong_to_their_own_sitrep(self) -> None:
        offenders = {}
        for chain in _sitrep_promotion_chains():
            chain_id = chain["chain_id"]
            expected = CHAIN_ID.match(chain_id).group(1).lstrip("0")
            found = {
                m.group(1).lstrip("0")
                for step in chain.get("steps", [])
                if (m := STEP_ID.search(str(step.get("step_id", ""))))
            }
            stray = found - {expected}
            if stray and chain_id not in KNOWN_STALE_STEP_PROVENANCE:
                offenders[chain_id] = sorted(stray)
        self.assertEqual(
            {},
            offenders,
            "SitRep chains carry another edition's step ids, which means the step "
            f"findings were cloned without being re-derived: {offenders}",
        )

    def test_known_stale_set_does_not_grow_silently(self) -> None:
        # The exception set is a debt register, not a suppression list: every id in
        # it must still exist and must still actually be stale, so a repaired chain
        # cannot quietly stay exempt.
        by_id = {c["chain_id"]: c for c in _sitrep_promotion_chains()}
        for chain_id in sorted(KNOWN_STALE_STEP_PROVENANCE):
            with self.subTest(chain_id=chain_id):
                self.assertIn(chain_id, by_id, "exempted chain is no longer registered")
                expected = CHAIN_ID.match(chain_id).group(1).lstrip("0")
                found = {
                    m.group(1).lstrip("0")
                    for step in by_id[chain_id].get("steps", [])
                    if (m := STEP_ID.search(str(step.get("step_id", ""))))
                }
                self.assertTrue(
                    found - {expected},
                    "this chain's steps now match its own SitRep; drop it from "
                    "KNOWN_STALE_STEP_PROVENANCE",
                )


if __name__ == "__main__":
    unittest.main()
