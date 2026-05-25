# SPDX-License-Identifier: Apache-2.0
"""Public repository hygiene checks for LOVS release automation."""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Iterable


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

SKIPPED_PATH_PREFIXES = (
    ".git/",
    "data/bundibugyo-2026/raw/",
)

SKIPPED_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".zip",
    ".xlsx",
}


def _needle(*parts: str) -> str:
    return "".join(parts)


def _word(*parts: str) -> str:
    return rf"\b{re.escape(_needle(*parts))}\b"


PROVENANCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        re.escape(_needle("[", "co", "dex", "]")),
        _word("co", "dex"),
        re.escape(_needle(".co", "dex")),
        _word("clau", "de code"),
        _word("clau", "de"),
        _word("anth", "ropic"),
        _word("open", "ai"),
        _word("chat", "gpt"),
        _word("generated", " with"),
        _word("co-authored", "-by"),
        _word("ai", "-generated"),
        _word("ai", " generated"),
    )
)


def contains_marker(text: str) -> bool:
    return any(pattern.search(text) for pattern in PROVENANCE_PATTERNS)


PUBLICATION_STATE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"review[\s-]*only",
        r"not\s+published",
        r"do\s+not\s+publish",
        r"not\s+for\s+publication",
    )
)


def find_publication_state_markers(subjects: Iterable[str]) -> list[str]:
    """Return commit subjects that carry a not-for-publication marker."""
    findings: list[str] = []
    for subject in subjects:
        text = subject.strip()
        if text and any(pattern.search(text) for pattern in PUBLICATION_STATE_PATTERNS):
            findings.append(text)
    return findings


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _tracked_files() -> list[pathlib.Path]:
    result = _git(["ls-files", "-z"])
    if result.returncode != 0:
        return []
    paths = []
    for raw in result.stdout.split("\0"):
        if not raw:
            continue
        rel = pathlib.PurePosixPath(raw)
        if any(raw.startswith(prefix) for prefix in SKIPPED_PATH_PREFIXES):
            continue
        if rel.suffix.lower() in SKIPPED_SUFFIXES:
            continue
        paths.append(REPO_ROOT / raw)
    return paths


def scan_tracked_files() -> list[str]:
    findings: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if contains_marker(text):
            findings.append(f"{path.relative_to(REPO_ROOT).as_posix()}: file content")
    return findings


def _metadata_ref_scope() -> str:
    return os.environ.get("LOVS_PUBLIC_HYGIENE_REF_SCOPE", "current").strip().lower()


def _refs_for_scope(scope: str) -> list[str]:
    if scope == "all":
        refs = _git(["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"])
        return refs.stdout.splitlines() if refs.returncode == 0 else []
    ref = _git(["symbolic-ref", "--quiet", "HEAD"])
    return [ref.stdout.strip()] if ref.returncode == 0 and ref.stdout.strip() else []


def _log_args_for_scope(scope: str) -> list[str]:
    base = ["log"]
    if scope == "all":
        base.append("--all")
    else:
        base.append("HEAD")
    return [*base, "--format=%H%x00%B%x00END-COMMIT"]


def scan_git_metadata(ref_scope: str | None = None) -> list[str]:
    findings: list[str] = []
    scope = (ref_scope or _metadata_ref_scope()) or "current"
    for ref in _refs_for_scope(scope):
        if contains_marker(ref):
            findings.append(f"{ref}: git ref")

    log = _git(_log_args_for_scope(scope))
    if log.returncode == 0:
        for record in log.stdout.split("\0END-COMMIT\n"):
            if not record.strip():
                continue
            commit, _, message = record.partition("\0")
            if contains_marker(message):
                findings.append(f"{commit[:12]}: commit message")
    return findings


def _resolve_baseline_ref(baseline_ref: str | None) -> str | None:
    candidates: list[str] = []
    explicit = baseline_ref or os.environ.get("LOVS_PUBLISHED_BASELINE_REF", "").strip()
    if explicit:
        candidates.append(explicit)
    candidates.extend(("origin/main", "main"))
    for ref in candidates:
        result = _git(["rev-parse", "--verify", "--quiet", ref])
        if result.returncode == 0 and result.stdout.strip():
            return ref
    return None


def scan_new_commit_publication_state(baseline_ref: str | None = None) -> list[str]:
    """Flag not-for-publication markers in commit subjects ahead of the published baseline.

    Scopes to ``<baseline>..HEAD`` so already-merged history is never re-flagged. Returns
    [] (no-op) when no baseline ref resolves, e.g. a fresh or shallow clone.
    """
    baseline = _resolve_baseline_ref(baseline_ref)
    if baseline is None:
        return []
    log = _git(["log", f"{baseline}..HEAD", "--format=%s"])
    if log.returncode != 0:
        return []
    subjects = [line for line in log.stdout.splitlines() if line.strip()]
    return [
        f"{subject}: not-for-publication marker"
        for subject in find_publication_state_markers(subjects)
    ]


def _walk_json_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_json_strings(item)


def scan_github_event() -> list[str]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return []
    path = pathlib.Path(event_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [f"{path}: unreadable event JSON"]

    findings: list[str] = []
    for value in _walk_json_strings(payload):
        if contains_marker(value):
            findings.append("github event metadata")
            break
    return findings


def scan_environment_refs() -> list[str]:
    findings: list[str] = []
    for name in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME", "GITHUB_REF"):
        value = os.environ.get(name, "")
        if value and contains_marker(value):
            findings.append(f"{name}: workflow ref")
    return findings


def scan_all() -> list[str]:
    findings: list[str] = []
    findings.extend(scan_tracked_files())
    findings.extend(scan_git_metadata())
    findings.extend(scan_github_event())
    findings.extend(scan_environment_refs())
    return sorted(set(findings))


def main() -> int:
    findings = scan_all()
    publication = scan_new_commit_publication_state()
    failed = False
    if findings:
        sys.stderr.write("[FAIL] public repository hygiene gate:\n")
        for finding in findings[:50]:
            sys.stderr.write(f"    {finding}: disallowed automation provenance marker\n")
        if len(findings) > 50:
            sys.stderr.write(f"    ... {len(findings) - 50} additional finding(s)\n")
        failed = True
    if publication:
        sys.stderr.write("[FAIL] publish-state guard (commit subjects ahead of baseline):\n")
        for finding in publication[:50]:
            sys.stderr.write(f"    {finding}\n")
        failed = True
    if failed:
        return 1
    print("public repository hygiene gate clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
