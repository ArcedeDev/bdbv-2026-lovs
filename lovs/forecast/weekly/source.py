# SPDX-License-Identifier: Apache-2.0
"""Read repository files from a directory or from one commit of a bare clone.

The build reads LOVS promotions and evidence packets at the commits it fetched, never a
working tree another session may be editing. A blob-less clone fetches missing blobs in
one batch before reading.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
from pathlib import Path

PROMOTIONS_DIR = "data/sitrep_promotions"
PACKETS_DIR = "data/sitrep-packets"


class DirTree:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def list(self, prefix: str, pattern: str = "*") -> list[str]:
        base = self.root / prefix
        if not base.is_dir():
            return []
        return sorted(f"{prefix}/{p.name}" for p in base.iterdir() if p.is_file() and fnmatch.fnmatch(p.name, pattern))

    def read(self, path: str) -> bytes:
        return (self.root / path).read_bytes()

    def read_many(self, paths: list[str]) -> dict[str, bytes]:
        return {p: self.read(p) for p in paths}


class GitTree:
    """Files of `commit` in a (possibly blob-less) clone, read with one cat-file process."""

    def __init__(self, repo: Path | str, commit: str) -> None:
        self.repo, self.commit = str(repo), commit
        self._blobs: dict[str, str] = {}
        self._cache: dict[str, bytes] = {}

    def _git(self, *args: str, stdin: bytes | None = None, env: dict | None = None) -> bytes:
        out = subprocess.run(["git", "-C", self.repo, *args], input=stdin, capture_output=True, check=False,
                             env={**os.environ, **env} if env else None)
        if out.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[:3])} failed: {out.stderr.decode()[-400:]}")
        return out.stdout

    def list(self, prefix: str, pattern: str = "*") -> list[str]:
        raw = self._git("ls-tree", "-z", f"{self.commit}:{prefix}")
        names = []
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            meta, name = entry.decode().split("\t", 1)
            _, kind, oid = meta.split()
            if kind == "blob" and fnmatch.fnmatch(name, pattern):
                path = f"{prefix}/{name}"
                self._blobs[path] = oid
                names.append(path)
        return sorted(names)

    def prefetch(self, paths: list[str]) -> None:
        prefetch_blobs(self.repo, [self._blobs[p] for p in paths if p in self._blobs])

    def read_many(self, paths: list[str]) -> dict[str, bytes]:
        for p in paths:
            if p not in self._blobs:
                self.list(p.rsplit("/", 1)[0])
        self.prefetch([p for p in paths if p not in self._cache])
        todo = [p for p in paths if p not in self._cache]
        if todo:
            raw = self._git("cat-file", "--batch", stdin="\n".join(self._blobs[p] for p in todo).encode() + b"\n")
            pos = 0
            for p in todo:
                header_end = raw.index(b"\n", pos)
                oid, kind, size = raw[pos:header_end].decode().split()
                start = header_end + 1
                self._cache[p] = raw[start:start + int(size)]
                pos = start + int(size) + 1
        return {p: self._cache[p] for p in paths}

    def read(self, path: str) -> bytes:
        return self.read_many([path])[path]


def prefetch_blobs(repo: Path | str, oids: list[str]) -> int:
    """Fetch, in one request, the blobs a blob-less clone does not hold yet. Returns the count."""
    oids = sorted(set(o for o in oids if o))
    if not oids:
        return 0
    check = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                           input="\n".join(oids).encode() + b"\n", capture_output=True, check=False,
                           env={**os.environ, "GIT_NO_LAZY_FETCH": "1"})
    absent = [line.split()[0] for line in check.stdout.decode().splitlines() if line.endswith(" missing")]
    if absent:
        subprocess.run(["git", "-C", str(repo), "-c", "fetch.negotiationAlgorithm=noop", "fetch", "origin",
                        "--no-tags", "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none",
                        "--stdin"], input="\n".join(absent).encode() + b"\n", capture_output=True, check=True)
    return len(absent)


def read_blobs(repo: Path | str, oids: list[str]) -> dict[str, bytes]:
    """Every blob in `oids`, prefetched in one batch and read with one cat-file process."""
    oids = sorted(set(o for o in oids if o))
    prefetch_blobs(repo, oids)
    if not oids:
        return {}
    raw = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input="\n".join(oids).encode() + b"\n",
                         capture_output=True, check=True).stdout
    out, pos = {}, 0
    for oid in oids:
        header_end = raw.index(b"\n", pos)
        got, _, size = raw[pos:header_end].decode().split()
        start = header_end + 1
        out[got] = raw[start:start + int(size)]
        pos = start + int(size) + 1
    return out


def blob(repo: Path | str, oid: str) -> bytes:
    return read_blobs(repo, [oid])[oid]


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sitrep_key(path: str) -> tuple[int, str]:
    name = path.rsplit("/", 1)[-1]
    digits = name.split("-")[1] if name.count("-") else ""
    return (int(digits) if digits.isdigit() else 0, name)


def load_promotions(tree) -> list[tuple[str, dict, str]]:
    """Reviewed, model-ready promotions as (path, document, sha256), by SitRep then file name."""
    paths = tree.list(PROMOTIONS_DIR, "sitrep-*.json")
    raw = tree.read_many(paths)
    out = []
    for path in sorted(paths, key=_sitrep_key):
        doc = json.loads(raw[path])
        if doc.get("status") != "reviewed" or not (doc.get("review") or {}).get("ready_for_model_use"):
            continue
        out.append((path, doc, sha256(raw[path])))
    return out


def load_packets(tree) -> list[tuple[str, dict, str]]:
    """Model-ready evidence packets as (path, document, sha256), duplicates by file name."""
    paths = [p for p in tree.list(PACKETS_DIR, "sitrep-*.json") if "nopost" not in p]
    raw = tree.read_many(paths)
    out = []
    for path in sorted(paths, key=_sitrep_key):
        doc = json.loads(raw[path])
        if doc.get("ready_for_model_use") and doc.get("data_as_of"):
            out.append((path, doc, sha256(raw[path])))
    return out
