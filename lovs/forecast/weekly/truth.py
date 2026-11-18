# SPDX-License-Identifier: Apache-2.0
"""Truth from LOVS promotions, timed by when each version reached origin/main.

First-published: the version in the first commit that reached LOVS origin/main, timed by
GitHub's activity record for refs/heads/main (each push names the tips before and after
it), else by the merge time of the pull request whose merge commit it is. Settled: the
version on origin/main at target end + 14 days, 23:59:59Z, by the same arrival times
(after Cramer et al. 2022). Commit metadata dates are never read: a version with no
recorded arrival cannot be timed and is not used. Duplicate files for one SitRep are
ordered by arrival, then file name.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from lovs.forecast.weekly import promotion_rows, spec
from lovs.forecast.weekly.source import PROMOTIONS_DIR

UTC = dt.timezone.utc
_LATE = dt.datetime.max.replace(tzinfo=UTC)


def parse_time(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def settle_time(end: dt.date) -> dt.datetime:
    d = end + dt.timedelta(days=spec.SETTLE_DAYS)
    return dt.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=UTC)


def arrival_times(first_parent: Sequence[str], events: Iterable[Mapping],
                  merges: Mapping[str, str]) -> dict[str, dt.datetime | None]:
    """Arrival of each first-parent commit (oldest first) on main.

    A push event covers the commits after its `before` tip up to its `after` tip; a commit
    takes the earliest push that covers it. A commit no recorded push covers takes the
    merge time of the pull request it merged, if any; otherwise it has no arrival.
    """
    index = {c: i for i, c in enumerate(first_parent)}
    arrived: list[dt.datetime | None] = [None] * len(first_parent)
    for event in events:
        hi = index.get(str(event.get("after")))
        if hi is None or not event.get("timestamp"):
            continue
        before = index.get(str(event.get("before")))
        lo = before + 1 if before is not None else 0
        when = parse_time(event["timestamp"])
        for i in range(lo, hi + 1):
            if arrived[i] is None or when < arrived[i]:
                arrived[i] = when
    out = {}
    for i, c in enumerate(first_parent):
        if arrived[i] is None and c in merges:
            arrived[i] = parse_time(merges[c])
        out[c] = arrived[i]
    return out


@dataclass(frozen=True)
class Version:
    path: str
    commit: str
    blob: str
    arrived_at: dt.datetime | None
    order: int


def file_versions(history: Sequence[tuple[str, Sequence[tuple[str, str]]]],
                  arrivals: Mapping[str, dt.datetime | None]) -> dict[str, list[Version]]:
    """Each file's versions on main, in arrival order. `history` is first-parent, oldest first."""
    out: dict[str, list[Version]] = {}
    for order, (commit, changes) in enumerate(history):
        for path, blob in changes:
            out.setdefault(path, []).append(Version(path, commit, blob, arrivals.get(commit), order))
    for path in out:
        out[path].sort(key=lambda v: (v.arrived_at or _LATE, v.order))
    return out


def first_published(versions: Sequence[Version]) -> Version | None:
    timed = [v for v in versions if v.arrived_at is not None and v.blob]
    return timed[0] if timed else None


def as_of(versions: Sequence[Version], when: dt.datetime) -> Version | None:
    standing = [v for v in versions if v.arrived_at is not None and v.arrived_at <= when]
    return standing[-1] if standing and standing[-1].blob else None


def _sitrep_of(path: str) -> int | None:
    parts = path.rsplit("/", 1)[-1].split("-")
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def sitrep_index(versions_by_path: Mapping[str, Sequence[Version]]) -> dict[int, list[Version]]:
    """Per SitRep, the first version of each of its files, by arrival then file name."""
    out: dict[int, list[Version]] = {}
    for path, versions in versions_by_path.items():
        n = _sitrep_of(path)
        if n is None or not versions:
            continue
        out.setdefault(n, []).append(versions[0])
    for n in out:
        out[n].sort(key=lambda v: (v.arrived_at or _LATE, v.path))
    return out


class TruthView:
    """Promotion rows as LOVS origin/main stood at a time, or as first published."""

    def __init__(self, versions_by_path: Mapping[str, Sequence[Version]], load_blob) -> None:
        self.versions = versions_by_path
        self.index = sitrep_index(versions_by_path)
        self.load_blob = load_blob
        self._docs: dict[str, dict] = {}

    def _doc(self, blob: str) -> dict:
        if blob not in self._docs:
            self._docs[blob] = json.loads(self.load_blob(blob))
        return self._docs[blob]

    def rows(self, *, when: dt.datetime | None = None, first: bool = False):
        """({data day: row}, {data day: (commit, arrival)}); reviewed, model-ready rows only."""
        by_day: dict[dt.date, dict] = {}
        prov: dict[dt.date, tuple[str, dt.datetime]] = {}
        for n in sorted(self.index):
            for head in self.index[n]:
                versions = self.versions[head.path]
                v = first_published(versions) if first else as_of(versions, when)
                if v is None:
                    continue
                doc = self._doc(v.blob)
                if doc.get("status") != "reviewed" or not (doc.get("review") or {}).get("ready_for_model_use"):
                    continue
                if first and when is not None and v.arrived_at > when:
                    continue
                day = dt.date.fromisoformat(str(doc["data_as_of"])[:10])
                if day not in by_day:
                    by_day[day] = promotion_rows.row_from_promotion(doc)
                    prov[day] = (v.commit, v.arrived_at)
                break
        return by_day, prov


def git_promotion_history(repo: Path | str, ref: str) -> tuple[list[str], list[tuple[str, list[tuple[str, str]]]]]:
    """First-parent commits of `ref` (oldest first) and the promotion files each changed."""
    chain = subprocess.run(["git", "-C", str(repo), "rev-list", "--first-parent", "--reverse", ref],
                           capture_output=True, text=True, check=True).stdout.split()
    raw = subprocess.run(["git", "-C", str(repo), "log", "--first-parent", "--reverse", "--format=commit %H",
                          "--raw", "--no-abbrev", "--no-renames", ref, "--", PROMOTIONS_DIR],
                         capture_output=True, text=True, check=True).stdout
    history: list[tuple[str, list[tuple[str, str]]]] = []
    for line in raw.splitlines():
        if line.startswith("commit "):
            history.append((line.split()[1], []))
        elif line.startswith(":") and history:
            meta, path = line.split("\t", 1)
            new_blob, status = meta.split()[3], meta.split()[4]
            if path.endswith(".json") and "/candidates/" not in path:
                history[-1][1].append((path, "" if status.startswith("D") else new_blob))
    return chain, history
