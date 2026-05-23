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


def scan_git_metadata() -> list[str]:
    findings: list[str] = []
    refs = _git(["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"])
    if refs.returncode == 0:
        for ref in refs.stdout.splitlines():
            if contains_marker(ref):
                findings.append(f"{ref}: git ref")

    log = _git(["log", "--all", "--format=%H%x00%B%x00END-COMMIT"])
    if log.returncode == 0:
        for record in log.stdout.split("\0END-COMMIT\n"):
            if not record.strip():
                continue
            commit, _, message = record.partition("\0")
            if contains_marker(message):
                findings.append(f"{commit[:12]}: commit message")
    return findings


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
    if findings:
        sys.stderr.write("[FAIL] public repository hygiene gate:\n")
        for finding in findings[:50]:
            sys.stderr.write(f"    {finding}: disallowed automation provenance marker\n")
        if len(findings) > 50:
            sys.stderr.write(f"    ... {len(findings) - 50} additional finding(s)\n")
        return 1
    print("public repository hygiene gate clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
