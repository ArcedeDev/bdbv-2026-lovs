"""Which way each carried calibration pin was registered: ``yes``, ``no`` or ``none``.

A resolved outcome only says whether a pin went as registered once you know which
outcome was registered as expected. A YES is "as registered" for a pin registered
to resolve YES and "against forecast" for a pin registered to resolve NO. The public
calibration record carries no probability, so this module fixes that side for every
pin carried onto the public surface and emits only the side, never the number:

- Block 4 (registered 2026-07-05, bdbv-2026-cal-016 to -056) follows the
  tier-to-probability mapping fixed in Section 5.3 of the Zenodo pre-registration
  (v2.2.0). ``relative_high`` is YES and ``relative_low`` is NO; ``relative_mid``
  maps to 0.50 and has no side. Every ``direction:*`` pin is worded so that YES is
  the registered direction. Threshold pins take the sign Section 5.3 fixes: four
  expected (YES) and eight guards (NO).
- Blocks 5 to 7 (pinned 2026-09-01, -057 to -087) follow the lean their public
  question registered, written by lovs.forecast.public_register.lean(): "leans YES"
  is YES, "leans NO" is NO, and "is close to even" has no side. The side is the
  registration a reader can see, not a second cut of the same probability.

A pin whose side is not fixed by one of these rules raises. Nothing defaults.
"""
from __future__ import annotations

import functools
import re
from typing import Any, Mapping

from lovs.forecast import public_register

SIDES: tuple[str, ...] = ("yes", "no", "none")

BLOCK4_LEDGER_NUMBERS = range(16, 57)
BLOCK4_TIER_SIDE: Mapping[str, str] = {
    "relative_high": "yes",
    "relative_mid": "none",
    "relative_low": "no",
}
# Section 5.3: a threshold pin maps to p = 0.60 when the threshold event is the expected
# outcome and to p = 0.40 when it is the less likely guard. 028 and 029 take their sign
# from the lean stated in their committed question; 032, 033, 036, 039, 042 and 045 are
# fixed by the pre-registration itself.
BLOCK4_THRESHOLD_EXPECTED = frozenset({30, 31, 44, 47})
BLOCK4_THRESHOLD_GUARD = frozenset({28, 29, 32, 33, 36, 39, 42, 45})

_LEDGER_ID = re.compile(r"bdbv-2026-cal-(\d{3})")


# Every phrase public_register.lean() can write into a Blocks 5-7 public question.
LEAN_SIDE: Mapping[str, str] = {
    "leans YES": "yes",
    "leans NO": "no",
    "is close to even": "none",
}


def _block4_side(number: int, tier: str) -> str:
    if tier in BLOCK4_TIER_SIDE:
        return BLOCK4_TIER_SIDE[tier]
    if tier.startswith("direction:"):
        return "yes"
    if tier.startswith("threshold:"):
        if number in BLOCK4_THRESHOLD_EXPECTED:
            return "yes"
        if number in BLOCK4_THRESHOLD_GUARD:
            return "no"
    raise ValueError(
        f"bdbv-2026-cal-{number:03d} ({tier!r}) has no registered side under Section 5.3 "
        f"of the 2026-07-05 pre-registration; refusing to guess one."
    )


@functools.cache
def _later_block_sides() -> dict[str, tuple[str, str, str]]:
    sides = {}
    for ledger_id, (field, value, lean) in public_register.lean_by_ledger_id().items():
        if lean not in LEAN_SIDE:
            raise ValueError(f"{ledger_id} registered the lean {lean!r}, which maps to no side.")
        sides[ledger_id] = (field, value, LEAN_SIDE[lean])
    return sides


def registered_side(commitment: Mapping[str, Any]) -> str:
    """The registered side of one public commitment row: ``yes``, ``no`` or ``none``.

    Raises ValueError when the row cannot be mapped to a registered side, including a
    Blocks 5 to 7 row that names a different pin than the ledger does at its ledger id.
    """
    ledger_id = str(commitment.get("ledger_id", ""))
    match = _LEDGER_ID.fullmatch(ledger_id)
    if match is None:
        raise ValueError(f"Commitment ledger id {ledger_id!r} is not a public calibration ledger id.")
    number = int(match.group(1))
    if number in BLOCK4_LEDGER_NUMBERS:
        return _block4_side(number, str(commitment.get("public_value_or_tier", "")))
    pinned = _later_block_sides().get(ledger_id)
    if pinned is None:
        raise ValueError(
            f"No registered side is fixed for {ledger_id}; refusing to ship a pin without one."
        )
    field, value, side = pinned
    if commitment.get(field) != value:
        raise ValueError(
            f"{ledger_id} carries {field} {commitment.get(field)!r} but the pinned ledger "
            f"registers {value!r} at that id; refusing to attach a side to the wrong pin."
        )
    return side
