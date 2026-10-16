# SPDX-License-Identifier: Apache-2.0
"""Tests for the registered judge (lovs/consistency/judge.py).

Synthetic editions 1..80; every figure is invented.
"""
from __future__ import annotations

import pathlib
import shutil
from decimal import Decimal

import pytest

from lovs.consistency import judge
from lovs.consistency.core import Candidate, round_half_up

EDITIONS = list(range(1, 81))
TARGET_EDITION = 40
ZONES = ("Zone-A", "Zone-B", "Zone-C", "Zone-D", "Zone-E", "Zone-F")
RATE = {"pct": ("inv", "val", 1)}
GROUPS = [["inv", "val", "pct"], ["not_inv"], ["province"]]


def figures(n: int) -> dict:
    inv = 400 + 3 * n
    return {"inv": inv, "val": inv + 5 + (n % 7), "not_inv": 3 + (n % 11), "province": ZONES[n % 6]}


def printed(n: int) -> dict:
    """The printed 5-field tuple of a figures() row (pct at one decimal)."""
    row = figures(n)
    pct = float(round_half_up(Decimal(row["inv"]) * 100 / Decimal(row["val"]), 1))
    return {**row, "pct": pct}


def carry(n: int) -> dict | None:
    return figures(n - 1) if n - 1 >= 1 else None


TARGET = printed(TARGET_EDITION - 1)


def pool_size(edition: int, seen: frozenset[int] = frozenset()) -> int:
    return sum(1 for j in EDITIONS if abs(j - edition) >= judge.MIN_EDITION_GAP and j not in seen)


# ---------------------------------------------------------------------------
# Registration


def test_verify_judge_returns_pinned_hashes():
    assert judge.verify_judge(1) == judge.JUDGE_SHA256[1]
    assert judge.verify_judge(2) == judge.JUDGE_SHA256[2]
    assert judge.verify_judge() == judge.JUDGE_SHA256[1]
    assert judge.JUDGE_SHA256[1] == "1cfc8a82d568c10d77103016da66d140ee50ae8d2e931fd8d542ff6b1a5329da"
    assert judge.JUDGE_SHA256[2] == "78ff274777751f3c45cfdb5c0cbbbf6529568f913be700e9785e3fe41b487146"


def test_verify_judge_accepts_identical_copy(tmp_path: pathlib.Path):
    copy = tmp_path / "JUDGE_V2.md"
    shutil.copyfile(judge.judge_path(2), copy)
    assert judge.verify_judge(2, copy) == judge.JUDGE_SHA256[2]


@pytest.mark.parametrize("version", [1, 2])
def test_verify_judge_rejects_modified_copy(tmp_path: pathlib.Path, version: int):
    copy = tmp_path / f"JUDGE_V{version}.md"
    copy.write_bytes(judge.judge_path(version).read_bytes() + b"\n")
    with pytest.raises(judge.JudgeMismatch):
        judge.verify_judge(version, copy)


def test_verify_judge_rejects_other_version_file():
    with pytest.raises(judge.JudgeMismatch):
        judge.verify_judge(1, judge.judge_path(2))


@pytest.mark.parametrize("version", [0, 3, 99])
def test_verify_judge_unknown_version(version: int):
    with pytest.raises(judge.JudgeMismatch):
        judge.verify_judge(version)


def test_effective_hypotheses():
    cands = [Candidate("A", "a", carry, hypotheses=3), Candidate("B", "b", carry)]
    assert judge.effective_hypotheses(cands) == 4


# ---------------------------------------------------------------------------
# judge_target


def run(candidates, target=TARGET, **kwargs):
    kwargs.setdefault("editions", EDITIONS)
    kwargs.setdefault("seen", frozenset())
    kwargs.setdefault("rate_fields", RATE)
    return judge.judge_target(TARGET_EDITION, target, candidates, **kwargs)


def test_planted_carry_forward_is_explained_under_field_groups():
    verdict = run([Candidate("CF", "previous edition carried", carry)], m_eff=10, field_groups=GROUPS)
    result = verdict.results[0]
    assert result.reproduces
    assert sorted(result.matched_fields) == ["inv", "not_inv", "pct", "province", "val"]
    assert result.pairs == pool_size(TARGET_EDITION) - 1  # carry(1) has no source edition
    assert result.null_matches == 0
    assert result.tuple_p_hat == pytest.approx(1 / (result.pairs + 1))
    assert set(result.group_null_rates) == {"inv+val+pct", "not_inv", "province"}
    product = 1.0
    for rate in result.group_null_rates.values():
        product *= rate
    assert result.p_hat == pytest.approx(product)
    assert result.p_hat < result.tuple_p_hat
    assert result.p_hat * 10 < judge.FAMILY_ALPHA
    assert set(result.field_null_rates) == {"inv", "val", "not_inv", "province", "pct"}
    assert result.passes_bar
    assert verdict.verdict == judge.EXPLAINED
    assert verdict.accepted == ["CF"]
    assert verdict.edition == TARGET_EDITION and verdict.target == TARGET


def test_same_carry_forward_without_field_groups_misses_the_bar():
    verdict = run([Candidate("CF", "previous edition carried", carry)], m_eff=10)
    result = verdict.results[0]
    assert result.reproduces and result.null_matches == 0
    assert result.p_hat * 10 >= judge.FAMILY_ALPHA
    assert verdict.verdict == judge.UNEXPLAINED


def test_single_common_number_is_not_accepted():
    target = {"val": 500}

    def flat(n: int) -> dict:
        return {"val": 500, "inv": 450 + n}

    def half(n: int) -> dict:
        return {"val": 500 if n % 2 == 0 else 700 + n, "inv": 450}

    for groups in (None, [["inv", "val", "pct"], ["not_inv"], ["province"]]):
        verdict = run([Candidate("FLAT", "constant", flat), Candidate("HALF", "every other", half)],
                      target=target, m_eff=1, field_groups=groups)
        flat_r, half_r = verdict.results
        assert flat_r.reproduces and half_r.reproduces
        assert flat_r.null_matches == flat_r.pairs and flat_r.p_hat == pytest.approx(1.0)
        assert half_r.p_hat > 0.4
        assert not flat_r.passes_bar and not half_r.passes_bar
        assert verdict.verdict == judge.UNEXPLAINED and verdict.accepted == []


def test_two_passing_candidates_are_ambiguous():
    cands = [Candidate("CF1", "carried", carry), Candidate("CF2", "carried again", carry)]
    verdict = run(cands, m_eff=10, field_groups=GROUPS)
    assert verdict.verdict == judge.AMBIGUOUS
    assert verdict.accepted == ["CF1", "CF2"]


def test_default_m_eff_counts_candidate_hypotheses():
    # The same candidate passes alone but fails once its grid is large.
    alone = run([Candidate("CF", "carried", carry)], field_groups=GROUPS)
    assert alone.verdict == judge.EXPLAINED
    p_hat = alone.results[0].p_hat
    big = int(judge.FAMILY_ALPHA / p_hat) + 1
    crowded = run([Candidate("CF", "carried", carry, hypotheses=big)], field_groups=GROUPS)
    assert crowded.results[0].p_hat == pytest.approx(p_hat)
    assert crowded.verdict == judge.UNEXPLAINED


def test_universal_fit_on_control_editions_is_rejected():
    controls = {"CF": [10, 20, 30]}
    targets = {j: printed(j - 1) for j in (10, 20, 30)}
    verdict = run([Candidate("CF", "carried", carry)], m_eff=10, field_groups=GROUPS,
                  control_editions=controls, targets=targets)
    result = verdict.results[0]
    assert result.reproduces
    assert result.p_hat * 10 < judge.FAMILY_ALPHA
    assert result.control_editions == 3 and result.control_matches == 3
    assert not result.passes_bar
    assert verdict.verdict == judge.UNEXPLAINED


def test_control_editions_skip_seen_and_missing_targets():
    controls = {"CF": [10, 20, 30, 50]}
    targets = {10: printed(9), 20: {"inv": -1}, 30: printed(29)}
    verdict = run([Candidate("CF", "carried", carry)], m_eff=10, field_groups=GROUPS,
                  control_editions=controls, targets=targets, seen=frozenset({30}))
    result = verdict.results[0]
    # 30 is seen, 50 has no target: 10 matches, 20 does not.
    assert result.control_editions == 2 and result.control_matches == 1
    assert not result.passes_bar  # 1/2 exceeds p_hat


def test_seen_editions_are_excluded_from_the_null_pool():
    leak = {10, 11, 12}

    def leaky(n: int) -> dict:
        return dict(TARGET) if n == TARGET_EDITION or n in leak else figures(n)

    open_run = run([Candidate("LK", "leaky", leaky)], m_eff=1)
    closed_run = run([Candidate("LK", "leaky", leaky)], m_eff=1, seen=frozenset(leak))
    assert open_run.results[0].pairs == pool_size(TARGET_EDITION)
    assert open_run.results[0].null_matches == 3
    assert closed_run.results[0].pairs == pool_size(TARGET_EDITION, frozenset(leak))
    assert closed_run.results[0].pairs == open_run.results[0].pairs - 3
    assert closed_run.results[0].null_matches == 0


def test_null_pool_respects_min_edition_gap():
    calls: list[int] = []

    def spy(n: int) -> dict:
        calls.append(n)
        return figures(n)

    run([Candidate("SPY", "spy", spy)], m_eff=1)
    pool = [n for n in calls if n != TARGET_EDITION]
    assert pool and all(abs(n - TARGET_EDITION) >= judge.MIN_EDITION_GAP for n in pool)


def test_smallest_p_without_groups_is_one_over_pairs_plus_one():
    verdict = run([Candidate("CF", "carried", carry)], m_eff=1)
    result = verdict.results[0]
    assert result.null_matches == 0
    assert result.p_hat == pytest.approx(judge.smallest_attainable_p(result.pairs))
    assert result.p_hat == pytest.approx(1 / (result.pairs + 1))
    assert result.tuple_p_hat == result.p_hat
    assert result.group_null_rates == {}
    assert judge.smallest_attainable_p(99) == pytest.approx(0.01)


def test_candidate_without_output_does_not_reproduce():
    verdict = run([Candidate("NONE", "nothing", lambda n: None)], m_eff=1)
    result = verdict.results[0]
    assert not result.reproduces and result.pairs == 0
    assert result.p_hat == pytest.approx(1.0)
    assert verdict.verdict == judge.UNEXPLAINED
