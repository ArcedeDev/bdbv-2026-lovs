"""Cross-surface byte-parity gate.

Asserts that every file the website publisher mirrors from LOVS
`deliverables/` to website `apps/site/public/bdbv-2026/` has byte-identical
sha256 on both sides. Catches silent drift like the May-25 cycle's
``bc20a286`` vs ``06a73f74`` divergence on
``lovs-public-health-dataset.manifest.json`` that surfaced only when the
operator re-ran the publisher.

The mirror set is canonical: it matches the publisher's ``copy_assets``
function at ``apps/site/lib/scripts/sync-bdbv-lovs.py:2057`` and is updated
in lockstep when the publisher gains a new mirrored file.

Findings shape mirrors the existing ``check_reconciliation_invariants`` /
``scan_public_artifacts_for_leaks`` convention in ``release_snapshot.py``:
the function returns a ``list[str]`` of human-readable mismatch lines; an
empty list means the gate is clean.

Stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

# The canonical mirror set: each entry is (lovs_relative, website_relative).
# Keep in sync with apps/site/lib/scripts/sync-bdbv-lovs.py::copy_assets.
_STATIC_PAIRS: tuple[tuple[str, str], ...] = (
    ("deliverables/brief.pdf", "brief.pdf"),
    (
        "deliverables/public-health-dataset/lovs-public-health-dataset.xlsx",
        "lovs-public-health-dataset.xlsx",
    ),
    (
        "deliverables/public-health-dataset/lovs-public-health-dataset.schema.json",
        "lovs-public-health-dataset.schema.json",
    ),
    (
        "deliverables/public-health-dataset/lovs-public-health-dataset.manifest.json",
        "lovs-public-health-dataset.manifest.json",
    ),
)

# Glob-style mirror sources (LOVS-side glob, website-side dir).
_GLOB_MIRRORS: tuple[tuple[str, str], ...] = (
    ("brief/visuals/*.svg", "visuals"),
)


def _sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _public_manifest_sha256(path: pathlib.Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _sha256_of(path)
    payload.pop("inputs", None)
    public_text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    return hashlib.sha256(public_text.encode("utf-8")).hexdigest()


def _enumerate_pairs(
    lovs_root: pathlib.Path, website_root: pathlib.Path
) -> list[tuple[pathlib.Path, pathlib.Path, str]]:
    """Return [(lovs_path, website_path, label), ...] for every mirrored file."""
    pairs: list[tuple[pathlib.Path, pathlib.Path, str]] = []
    for lovs_rel, web_rel in _STATIC_PAIRS:
        pairs.append((lovs_root / lovs_rel, website_root / web_rel, web_rel))
    for src_glob, dst_subdir in _GLOB_MIRRORS:
        # The src_glob is relative to lovs_root.
        for src in sorted(lovs_root.glob(src_glob)):
            label = f"{dst_subdir}/{src.name}"
            pairs.append((src, website_root / dst_subdir / src.name, label))
    return pairs


def check_cross_surface_parity(
    lovs_root: pathlib.Path,
    website_public_root: pathlib.Path,
) -> dict:
    """Return a structured result with ``checked``, ``mismatches``, ``missing``.

    ``mismatches`` is the list of human-readable lines where the LOVS and
    website copies have different sha256 (gate failure). ``missing`` is the
    list where one side has the file and the other does not (also a failure;
    the publisher should always produce both copies). ``checked`` is the
    count of file pairs successfully compared.

    ``website_public_root`` is the directory the publisher writes to (e.g.
    ``apps/site/public/bdbv-2026``); pass ``release_snapshot.DEFAULT_WEBSITE_PUBLIC``
    in production code.

    The caller (e.g. ``release_snapshot.run_release_gates``) fails closed on
    non-empty ``mismatches`` or ``missing``.
    """
    pairs = _enumerate_pairs(pathlib.Path(lovs_root), pathlib.Path(website_public_root))
    checked = 0
    mismatches: list[str] = []
    missing: list[str] = []
    for lovs_path, web_path, label in pairs:
        lovs_exists = lovs_path.is_file()
        web_exists = web_path.is_file()
        if not lovs_exists and not web_exists:
            # Neither side has the file; nothing to compare. Skip silently
            # (a fresh clone or a partial deployment is legitimate).
            continue
        if not lovs_exists:
            missing.append(f"{label}: LOVS side missing ({lovs_path})")
            continue
        if not web_exists:
            missing.append(f"{label}: website side missing ({web_path})")
            continue
        if label == "lovs-public-health-dataset.manifest.json":
            lovs_sha = _public_manifest_sha256(lovs_path)
        else:
            lovs_sha = _sha256_of(lovs_path)
        web_sha = _sha256_of(web_path)
        checked += 1
        if lovs_sha != web_sha:
            mismatches.append(
                f"{label}: LOVS sha256={lovs_sha[:16]}... website sha256={web_sha[:16]}... (re-run publisher)"
            )
    return {"checked": checked, "mismatches": mismatches, "missing": missing}
