# SPDX-License-Identifier: Apache-2.0
"""Registered judges for mechanism reconstruction (JUDGE_V<n>.md, founder-approved).

A judge is fixed before the run it governs and is never edited by the lane
that runs it. The acceptance rule, the coincidence bar and the gold are read
from the registered JUDGE_V<n>.md, whose sha256 is pinned here. A changed
judge is a new version, with a new hash and a new approval. Earlier versions
stay registered, so their runs can be replayed.
"""
from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from lovs.consistency.core import Candidate, Figures, tuple_matches

JUDGE_SHA256 = {
    1: "1cfc8a82d568c10d77103016da66d140ee50ae8d2e931fd8d542ff6b1a5329da",
    2: "78ff274777751f3c45cfdb5c0cbbbf6529568f913be700e9785e3fe41b487146",
}
FAMILY_ALPHA = 0.01
MIN_EDITION_GAP = 4
EXPLAINED = "EXPLAINED"
AMBIGUOUS = "AMBIGUOUS"
UNEXPLAINED = "UNEXPLAINED"


class JudgeMismatch(RuntimeError):
    """The registered judge file does not match the pinned hash."""


def judge_path(version: int) -> pathlib.Path:
    return pathlib.Path(__file__).with_name(f"JUDGE_V{version}.md")


def verify_judge(version: int = 1, path: pathlib.Path | None = None) -> str:
    if version not in JUDGE_SHA256:
        raise JudgeMismatch(f"judge version {version} is not registered")
    path = path or judge_path(version)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != JUDGE_SHA256[version]:
        raise JudgeMismatch(f"{path.name} sha256 {digest} != registered {JUDGE_SHA256[version]}")
    return digest


@dataclass
class CandidateResult:
    candidate_id: str
    reproduces: bool
    matched_fields: list[str]
    pairs: int = 0
    null_matches: int = 0
    p_hat: float | None = None
    passes_bar: bool = False
    control_editions: int = 0
    control_matches: int = 0
    field_null_rates: dict[str, float] = field(default_factory=dict)
    tuple_p_hat: float | None = None
    group_null_rates: dict[str, float] = field(default_factory=dict)


@dataclass
class TargetVerdict:
    edition: int
    target: dict
    verdict: str
    accepted: list[str]
    results: list[CandidateResult]


def effective_hypotheses(candidates: Sequence[Candidate]) -> int:
    return sum(candidate.hypotheses for candidate in candidates)


def judge_target(
    edition: int,
    target: Figures,
    candidates: Sequence[Candidate],
    *,
    editions: Sequence[int],
    seen: frozenset[int],
    control_editions: Mapping[str, Sequence[int]] | None = None,
    targets: Mapping[int, Figures] | None = None,
    rate_fields: Mapping[str, tuple[str, str, int]] = {},
    m_eff: int | None = None,
    field_groups: Sequence[Sequence[str]] | None = None,
    control_rate_fields: Callable[[int], Mapping[str, tuple[str, str, int]]] | None = None,
) -> TargetVerdict:
    """Apply a registered judge to one printed target tuple.

    Null: pair the target with the same candidate's output at every edition j
    with |edition - j| >= MIN_EDITION_GAP and j not seen. Without
    ``field_groups`` (judge v1), p_hat = (matches + 1) / (pairs + 1) over whole
    tuples. With ``field_groups`` (judge v2), each printed group gets its own
    rate (matches_g + 1) / (pairs + 1) and p_hat is their product; the whole-tuple
    rate is kept as ``tuple_p_hat``. Accept only if p_hat * m_eff <
    FAMILY_ALPHA. A candidate that also reproduces the printed target of its
    control editions above p_hat is a universal fit and is rejected.
    """
    m_eff = m_eff or effective_hypotheses(candidates)
    results: list[CandidateResult] = []
    for candidate in candidates:
        predicted = candidate.generate(edition)
        reproduces, matched = tuple_matches(target, predicted, rate_fields=rate_fields)
        result = CandidateResult(candidate.candidate_id, reproduces, matched)
        pool = [j for j in editions if abs(j - edition) >= MIN_EDITION_GAP and j not in seen]
        outputs = {j: candidate.generate(j) for j in pool}
        outputs = {j: out for j, out in outputs.items() if out}
        result.pairs = len(outputs)
        result.null_matches = sum(
            tuple_matches(target, out, rate_fields=rate_fields)[0] for out in outputs.values()
        )
        result.p_hat = result.tuple_p_hat = (result.null_matches + 1) / (result.pairs + 1)
        if field_groups is not None:
            product = 1.0
            for group in field_groups:
                wanted = {name: target[name] for name in group if target.get(name) is not None}
                if not wanted:
                    continue
                hits = sum(tuple_matches(wanted, out, rate_fields=rate_fields)[0] for out in outputs.values())
                rate = (hits + 1) / (result.pairs + 1)
                result.group_null_rates["+".join(wanted)] = rate
                product *= rate
            result.p_hat = product
        for name, wanted in target.items():
            if wanted is None or not outputs:
                continue
            hits = sum(
                tuple_matches({name: wanted}, out, rate_fields=rate_fields)[0] for out in outputs.values()
            )
            result.field_null_rates[name] = (hits + 1) / (len(outputs) + 1)
        if control_editions and targets is not None:
            for j in control_editions.get(candidate.candidate_id, ()):
                if j in seen or j not in targets:
                    continue
                result.control_editions += 1
                result.control_matches += tuple_matches(
                    targets[j], candidate.generate(j),
                    rate_fields=control_rate_fields(j) if control_rate_fields else rate_fields,
                )[0]
        universal = (
            result.control_editions > 0
            and result.control_matches / result.control_editions > result.p_hat
        )
        result.passes_bar = reproduces and result.p_hat * m_eff < FAMILY_ALPHA and not universal
        results.append(result)
    accepted = [r.candidate_id for r in results if r.passes_bar]
    verdict = EXPLAINED if len(accepted) == 1 else AMBIGUOUS if accepted else UNEXPLAINED
    return TargetVerdict(edition, dict(target), verdict, accepted, results)


def smallest_attainable_p(pairs: int) -> float:
    """The empirical null cannot report a p below 1 / (pairs + 1)."""
    return 1 / (pairs + 1)
