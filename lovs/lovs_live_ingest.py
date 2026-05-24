"""LOVS Stage Two: live T1 ingest for the 2026 BDBV outbreak.

Stdlib-only HTTPS fetch pipeline. Targets WHO Disease Outbreak News and
related publicly-published T1 sources for the 2026 BDBV outbreak
(WHO DON602, declared 15 May 2026).

Contract:
 - One-shot ingest per call; not a scheduled connector.
 - Idempotent: re-running on the same target archive produces an identical
   manifest if upstream bytes have not changed.
 - Fail-closed: a fetch or parse error raises ``LiveIngestError``; no
   partial snapshot is persisted.
 - SHA-256 deduplication: identical raw bytes already in the archive cause
   the call to skip rather than re-write.

Stdlib only: ``urllib.request`` for HTTPS, ``re`` and ``html.parser`` for
extraction, ``hashlib`` for content addressing.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import html.parser
import pathlib
import re
import ssl
import time
import urllib.error
import urllib.request

from lovs import lovs_archive


MODEL_VERSION = "lovs_live_ingest-v0.1.0"

USER_AGENT = (
    "bdbv-2026-lovs/0.1.0 "
    "(public-health surveillance validation; "
    "see https://github.com/ArcedeDev/bdbv-2026-lovs/issues for contact)"
)

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_BASE = 2.0

# Fallback CA bundle search paths for environments where Python's default
# verify paths do not resolve a usable certificate file. Order is:
#  1. SSL_CERT_FILE env var (handled by ssl.create_default_context automatically)
#  2. macOS system bundle (/etc/ssl/cert.pem)
#  3. Homebrew openssl@3 (/opt/homebrew/etc/openssl@3/cert.pem)
#  4. Homebrew ca-certificates (/opt/homebrew/etc/ca-certificates/cert.pem)
#  5. Linux distro standard (/etc/ssl/certs/ca-certificates.crt)
_CA_BUNDLE_FALLBACKS: tuple[str, ...] = (
    "/etc/ssl/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
)


class LiveIngestError(RuntimeError):
    """Raised when the live ingest cannot complete cleanly."""


@dataclasses.dataclass(frozen=True)
class IngestTarget:
    """A T1 source to fetch.

    ``parser_name`` selects which extraction function processes the raw bytes.
    Supported parsers: "who_don_html", "cdc_ebola_html", "passthrough_html".
    """

    source_id: str
    source_tier: str
    publisher: str
    url: str
    license: str
    outbreak_id: str
    pathogen: str
    country_scope: tuple[str, ...]
    geography_id: str
    parser_name: str


# Stage Two: WHO DON602 is the anchor T1. The Stage Two cycle attempts this
# target first; additional targets can be added without API changes.
WHO_DON_602_TARGET = IngestTarget(
    source_id="who-don602-2026-05-15-live",
    source_tier="official_who",
    publisher="World Health Organization",
    url="https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON602",
    license="CC-BY-NC-SA-3.0-IGO",
    outbreak_id="bdbv-uga-cod-2026",
    pathogen="BDBV",
    country_scope=("COD", "UGA"),
    geography_id="ituri-bdbv-corridor",
    parser_name="who_don_html",
)

CDC_CURRENT_SITUATION_TARGET = IngestTarget(
    source_id="cdc-current-situation-2026-05-20",
    source_tier="official_cdc",
    publisher="US Centers for Disease Control and Prevention",
    url="https://www.cdc.gov/ebola/situation-summary/index.html",
    license="public-domain-us-gov",
    outbreak_id="bdbv-uga-cod-2026",
    pathogen="BDBV",
    country_scope=("COD", "UGA"),
    geography_id="ituri-nord-kivu-bdbv-corridor",
    parser_name="cdc_ebola_html",
)


def cdc_current_situation_target(publication_date: str) -> IngestTarget:
    """Return a dated CDC Current Situation ingest target."""
    return dataclasses.replace(
        CDC_CURRENT_SITUATION_TARGET,
        source_id=f"cdc-current-situation-{publication_date}",
    )


def _now_utc_iso_z() -> str:
    """Return the current UTC time as an ISO-8601 string ending in Z."""
    dt = datetime.datetime.now(datetime.timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_ssl_context() -> ssl.SSLContext:
    """Build an ssl.SSLContext with a usable CA store.

    On macOS in particular, ``ssl.create_default_context()`` may resolve to a
    cert.pem that does not exist (Python framework install without the
    Install Certificates.command applied). The function tries the default
    first, then a set of well-known fallback bundles. If none resolve a
    usable file, the default context is returned anyway; the subsequent
    connection attempt will raise a clear SSLError that propagates as
    LiveIngestError.
    """
    ctx = ssl.create_default_context()
    default_paths = ssl.get_default_verify_paths()
    if default_paths.cafile and pathlib.Path(default_paths.cafile).exists():
        return ctx
    for candidate in _CA_BUNDLE_FALLBACKS:
        if pathlib.Path(candidate).exists():
            try:
                ctx.load_verify_locations(cafile=candidate)
                return ctx
            except (OSError, ssl.SSLError):
                continue
    return ctx


def _fetch_bytes(
    url: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
) -> bytes:
    """Fetch a URL via stdlib urllib.request with retry-on-error and timeout.

    Raises LiveIngestError if all retries are exhausted.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
    )
    last_error: Exception | None = None
    ctx = _resolve_ssl_context()
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=ctx
            ) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(backoff_base ** attempt)
    raise LiveIngestError(
        f"_fetch_bytes({url!r}): all {retries} attempts failed; last error: {last_error}"
    )


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Minimal HTMLParser that accumulates visible text for downstream regex."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    @property
    def text(self) -> str:
        return " ".join(self._chunks)


def _extract_visible_text(raw_bytes: bytes) -> str:
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(raw_bytes.decode("utf-8", errors="replace"))
    except Exception as e:
        raise LiveIngestError(f"_extract_visible_text: HTML parse failed: {e}") from e
    return extractor.text


_COUNT_PATTERNS: dict[str, re.Pattern[str]] = {
    "cases_suspected": re.compile(
        r"(\d{1,5})\s+suspected\s+(?:and\s+probable\s+)?cases?", re.IGNORECASE
    ),
    "cases_confirmed": re.compile(
        r"(\d{1,5})\s+(?:laboratory[\-\s])?confirmed\s+cases?", re.IGNORECASE
    ),
    "deaths": re.compile(r"(\d{1,5})\s+deaths?", re.IGNORECASE),
}

_MONTHS: dict[str, str] = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def _month_day_year_to_iso(token: str) -> str | None:
    """Convert English 'May 20, 2026' style dates to YYYY-MM-DD."""
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(20\d{2})", token.strip())
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    day = int(match.group(2))
    return f"{match.group(3)}-{month}-{day:02d}"

# Secondary fallback patterns for confirmed counts; tried only if the primary
# `cases_confirmed` pattern misses. Some WHO DON pages report the confirmed
# count indirectly via phrasings like "Four deaths among confirmed cases" or
# "of which N were confirmed".
_CONFIRMED_FALLBACK_NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}
_CONFIRMED_FALLBACK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"((?:" + "|".join(_CONFIRMED_FALLBACK_NUMBER_WORDS.keys()) +
        r"|\d{1,5}))\s+deaths?\s+among\s+(?:the\s+)?confirmed\s+cases?",
        re.IGNORECASE,
    ),
    re.compile(
        r"of\s+(?:which\s+)?(\d{1,5})\s+(?:were|are|have\s+been)\s+confirmed",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\d{1,5})\s+cases?\s+(?:were|are|have\s+been)\s+confirmed",
        re.IGNORECASE,
    ),
)


def _parse_confirmed_fallback(text: str) -> int | None:
    """Look for indirect confirmed-case numbers when the primary pattern misses."""
    for pat in _CONFIRMED_FALLBACK_PATTERNS:
        match = pat.search(text)
        if not match:
            continue
        token = match.group(1).strip().lower()
        if token.isdigit():
            try:
                return int(token)
            except ValueError:
                continue
        if token in _CONFIRMED_FALLBACK_NUMBER_WORDS:
            return _CONFIRMED_FALLBACK_NUMBER_WORDS[token]
    return None

# Declaration-date capture: prefer patterns explicitly anchored on declaration
# verbiage. The earlier pattern `(?:on|declared)\s+(\d{1,2}\s+\w+\s+20\d{2})`
# also matched antecedent dates in narrative prose (e.g. an earlier "On 5 May
# 2026, the patient developed symptoms..." would beat the canonical declaration
# date if it appeared first in the page). We now favor declaration-anchored
# phrasings and fall back only if none match.
_DECLARATION_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Strongest: "On <date>, the Ministry/Government ... officially declared"
    # This is the canonical WHO DON declaration sentence shape.
    re.compile(
        r"(?:On\s+)?(\d{1,2}\s+\w+\s+20\d{2})[,\.\s]+(?:[Tt]he\s+)?"
        r"(?:Government|Ministry|Minist[eè]re|MoH|MOH)[^.]{0,200}?"
        r"(?:officially\s+)?declared",
        re.IGNORECASE,
    ),
    # Strong: "Declaration date: <date>" or "Declaration of the outbreak on <date>"
    re.compile(
        r"(?:Declaration(?:\s+of\s+the\s+outbreak)?|Declaration\s+date)[:\s]+(?:on\s+)?(\d{1,2}\s+\w+\s+20\d{2})",
        re.IGNORECASE,
    ),
    # Strong: "<date>, the Ministry ... notified WHO" or "WHO was notified on <date>"
    re.compile(
        r"WHO\s+(?:was\s+)?(?:alerted|notified)\s+(?:on\s+|of\s+)(\d{1,2}\s+\w+\s+20\d{2})",
        re.IGNORECASE,
    ),
    # Fallback: any "declared on <date>" or "on <date>" near declaration verbs.
    # Kept last because page bodies often contain antecedent dates with these
    # phrasings (e.g. previous-outbreak references).
    re.compile(
        r"(?:officially\s+)?declared\s+(?:by\s+\w+(?:\s+\w+){0,5}\s+)?(?:on\s+)?(\d{1,2}\s+\w+\s+20\d{2})",
        re.IGNORECASE,
    ),
)


def _parse_declaration_date(text: str) -> tuple[str, str] | None:
    """Try the declaration-date patterns in priority order; return (full_match, date_token) of the first hit."""
    for pat in _DECLARATION_DATE_PATTERNS:
        match = pat.search(text)
        if match:
            return (match.group(0), match.group(1))
    return None
_ZONE_PATTERN = re.compile(
    r"(Rwampara|Mongbwalu|Bunia|Kampala|Bundibugyo|Beni|Kasese|Ituri)\s+"
    r"(?:Health\s+Zone|District|Province|City)?",
    re.IGNORECASE,
)


def _parse_who_don_html(raw_bytes: bytes) -> dict:
    """Extract a normalized_content dict from a WHO DON HTML page.

    The full raw HTML is preserved in the archive's raw bytes for audit;
    this extraction populates a small dict of structured fields for the
    LOVS Module B reconciler to consume.

    Returns a dict with whatever fields could be reliably parsed. Missing
    fields are simply absent from the dict; downstream Module B handles
    missing-field cases.
    """
    text = _extract_visible_text(raw_bytes)
    normalized: dict[str, object] = {}

    for field, pattern in _COUNT_PATTERNS.items():
        match = pattern.search(text)
        if match:
            try:
                normalized[field] = int(match.group(1))
            except (ValueError, IndexError):
                pass

    # Secondary fallback for cases_confirmed: try indirect patterns
    # ("four deaths among confirmed cases", "of which X were confirmed").
    if "cases_confirmed" not in normalized:
        fallback = _parse_confirmed_fallback(text)
        if fallback is not None:
            normalized["cases_confirmed"] = fallback

    decl_match = _parse_declaration_date(text)
    if decl_match is not None:
        full_match, date_token = decl_match
        normalized["declaration_text"] = full_match
        normalized["declaration_date_raw"] = date_token

    zones_found: list[str] = []
    for match in _ZONE_PATTERN.finditer(text):
        zone = match.group(1).lower()
        if zone not in zones_found:
            zones_found.append(zone)
    if zones_found:
        normalized["affected_zones"] = zones_found

    # A short citable excerpt for the audit trail. We keep this brief to
    # respect copyright; the full raw bytes are stored in the archive for
    # local reproduction only.
    excerpt_words = text.split()[:60]
    if excerpt_words:
        normalized["narrative_excerpt"] = " ".join(excerpt_words)

    return normalized


def _parse_passthrough_html(raw_bytes: bytes) -> dict:
    """Generic passthrough parser: extract visible text into a bounded excerpt.

    For T1 sources whose page shape does not match the WHO DON template
    (ECDC outbreak pages, news items, sitrep landing pages). The full raw
    HTML is preserved in the archive's raw bytes for audit; this parser
    only populates a short narrative_excerpt so the manifest entry is not
    structurally empty. Downstream Module B is responsible for any deeper
    extraction it needs from the archived raw bytes.
    """
    text = _extract_visible_text(raw_bytes)
    excerpt_words = text.split()[:60]
    return {
        "narrative_excerpt": " ".join(excerpt_words) if excerpt_words else "",
    }


def _parse_cdc_ebola_html(raw_bytes: bytes) -> dict:
    """Extract the CDC Current Situation count tuple without using it as a release date.

    CDC's page is currently the cleanest structured official source for the
    May 19 count tuple, but it is a May 20 page. The parser preserves that
    distinction by recording both ``publication_date`` and ``data_as_of``.
    """
    text = _extract_visible_text(raw_bytes)
    return extract_cdc_current_situation_counts(text)


def extract_cdc_current_situation_counts(text: str) -> dict:
    """Extract CDC Current Situation counts from visible page text.

    CDC changed the page shape on 23 May from one aggregate tuple to separate
    DRC and Uganda bullets. Preserve both country-level values and a
    country-pair confirmed total so downstream gates do not mistake the DRC-only
    confirmed count for the outbreak-wide confirmed endpoint.
    """
    normalized: dict[str, object] = {}

    page_date_match = re.search(r"\b(May\s+\d{1,2},\s+20\d{2})\b", text)
    if page_date_match:
        raw = page_date_match.group(1)
        normalized["publication_date_raw"] = raw
        iso = _month_day_year_to_iso(raw)
        if iso:
            normalized["publication_date"] = iso

    as_of_match = re.search(r"As\s+of\s+(May\s+\d{1,2})", text, re.IGNORECASE)
    if as_of_match:
        raw = as_of_match.group(1)
        normalized["data_as_of_raw"] = raw
        year = str(normalized.get("publication_date", "2026"))[:4]
        iso = _month_day_year_to_iso(f"{raw}, {year}")
        if iso:
            normalized["data_as_of"] = iso

    drc_match = re.search(
        r"DRC\s*:\s*A\s+total\s+of\s+(\d{1,6})\s+suspected\s+cases,\s+"
        r"(\d{1,6})\s+confirmed\s+cases,\s+"
        r"(\d{1,6})\s+suspected\s+deaths,\s+and\s+"
        r"(\d{1,6})\s+confirmed\s+deaths?\s*\.?",
        text,
        re.IGNORECASE,
    )
    uganda_total_match = re.search(
        r"Uganda\s*:\s*A\s+total\s+of\s+(\d{1,6})\s+confirmed\s+cases\s+and\s+"
        r"(\d{1,6})\s+confirmed\s+deaths?\s*\.?",
        text,
        re.IGNORECASE,
    )
    if drc_match:
        normalized["cases_suspected_drc"] = int(drc_match.group(1))
        normalized["cases_confirmed_drc"] = int(drc_match.group(2))
        normalized["deaths_suspected_drc"] = int(drc_match.group(3))
        normalized["deaths_confirmed_drc"] = int(drc_match.group(4))
        normalized["cases_suspected"] = normalized["cases_suspected_drc"]
        normalized["deaths_suspected"] = normalized["deaths_suspected_drc"]
    if uganda_total_match:
        normalized["cases_confirmed_uganda"] = int(uganda_total_match.group(1))
        normalized["deaths_uganda"] = int(uganda_total_match.group(2))
    if drc_match and uganda_total_match:
        total = int(normalized["cases_confirmed_drc"]) + int(normalized["cases_confirmed_uganda"])
        normalized["cases_confirmed_total"] = total
        normalized["cases_confirmed"] = total

    count_match = re.search(
        r"(\d{1,5})\s+suspected\s+cases,\s+(\d{1,5})\s+probable\s+cases,\s+"
        r"(\d{1,5})\s+confirmed\s+cases,\s+and\s+(\d{1,5})\s+suspected\s+deaths",
        text,
        re.IGNORECASE,
    )
    if count_match and "cases_suspected" not in normalized:
        normalized["cases_suspected"] = int(count_match.group(1))
        normalized["cases_probable"] = int(count_match.group(2))
        normalized["cases_confirmed"] = int(count_match.group(3))
        normalized["deaths_suspected"] = int(count_match.group(4))
    else:
        count_match = re.search(
            r"(\d{1,5})\s+suspected\s+cases,\s+"
            r"(\d{1,5})\s+confirmed\s+cases,\s+and\s+"
            r"(\d{1,5})\s+suspected\s+deaths",
            text,
            re.IGNORECASE,
        )
        if count_match and "cases_suspected" not in normalized:
            normalized["cases_suspected"] = int(count_match.group(1))
            normalized["cases_confirmed"] = int(count_match.group(2))
            normalized["deaths_suspected"] = int(count_match.group(3))

    recent_match = re.search(
        r"(\d{1,5})\s+new\s+confirmed\s+cases\s+and\s+(\d{1,5})\s+new\s+suspected\s+cases",
        text,
        re.IGNORECASE,
    )
    if recent_match:
        normalized["new_confirmed_cases_24_to_48h"] = int(recent_match.group(1))
        normalized["new_suspected_cases_24_to_48h"] = int(recent_match.group(2))

    uganda_match = re.search(
        r"include\s+(\d{1,5})\s+confirmed\s+cases\s+including\s+(\d{1,5})\s+death\s+in\s+Uganda",
        text,
        re.IGNORECASE,
    )
    if uganda_match and "cases_confirmed_uganda" not in normalized:
        normalized["cases_confirmed_uganda"] = int(uganda_match.group(1))
        normalized["deaths_uganda"] = int(uganda_match.group(2))

    uganda_new_match = re.search(
        r"Uganda\s+announced\s+(\d{1,6})\s+additional\s+cases",
        text,
        re.IGNORECASE,
    )
    if uganda_new_match:
        normalized["new_confirmed_cases_uganda"] = int(uganda_new_match.group(1))

    zones_match = re.search(
        r"reported\s+in\s+(\d{1,3})\s+health\s+zones\s+in\s+Ituri\s+Province\s+and\s+in\s+Nord-Kivu\s+Province",
        text,
        re.IGNORECASE,
    )
    if zones_match:
        normalized["affected_health_zones_count"] = int(zones_match.group(1))
        normalized["affected_provinces"] = ["Ituri", "Nord-Kivu"]

    if re.search(r"no\s+cases\s+of\s+Ebola\s+disease\s+have\s+been\s+confirmed\s+in\s+the\s+United\s+States", text, re.IGNORECASE):
        normalized["cases_confirmed_united_states"] = 0

    excerpt_words = text.split()[:60]
    if excerpt_words:
        normalized["narrative_excerpt"] = " ".join(excerpt_words)
    return normalized


_PARSERS: dict[str, callable] = {
    "who_don_html": _parse_who_don_html,
    "cdc_ebola_html": _parse_cdc_ebola_html,
    "passthrough_html": _parse_passthrough_html,
}


def ingest_one(
    target: IngestTarget,
    archive_root: pathlib.Path,
    fetch_fn=_fetch_bytes,
    now_fn=_now_utc_iso_z,
    offline_bytes_path: pathlib.Path | None = None,
    published_at: str | None = None,
) -> lovs_archive.ArchivedSnapshot | None:
    """Fetch a single target and add to the archive.

    Returns the new ArchivedSnapshot, or None if the bytes match an
    already-archived content_hash (idempotent skip).

    Raises LiveIngestError on fetch or parse failure (fail-closed).

    If ``offline_bytes_path`` is provided, the live fetch is skipped and the
    raw bytes are read from that file. The same idempotency, parse, and
    archive contract apply. Useful for environments without HTTPS egress or
    for reproducing an earlier live fetch.

    If ``published_at`` is supplied, the manifest entry records that string
    as ``provenance.published_at``. When omitted, ``published_at`` defaults
    to the same value as ``retrieved_at`` (the existing WHO DON behavior;
    those pages do not expose a separate publication timestamp). Caller is
    responsible for verifying the date matches the upstream document.
    """
    archive_root = pathlib.Path(archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    (archive_root / "raw").mkdir(parents=True, exist_ok=True)

    if offline_bytes_path is not None:
        offline_bytes_path = pathlib.Path(offline_bytes_path)
        if not offline_bytes_path.exists():
            raise LiveIngestError(
                f"ingest_one: offline_bytes_path {offline_bytes_path} not found"
            )
        raw_bytes = offline_bytes_path.read_bytes()
    else:
        raw_bytes = fetch_fn(target.url)
    if not raw_bytes:
        raise LiveIngestError(f"ingest_one({target.source_id}): fetch returned empty bytes")

    content_hash = _sha256_hex(raw_bytes)

    # Idempotent skip: if any existing snapshot has this exact content_hash,
    # do not re-add.
    if (archive_root / "manifest.json").exists():
        existing_archive = lovs_archive.load_archive(archive_root)
        for snap in existing_archive.snapshots:
            if snap.provenance.content_hash == content_hash:
                return None

    parser = _PARSERS.get(target.parser_name)
    if parser is None:
        raise LiveIngestError(
            f"ingest_one: unknown parser {target.parser_name!r}; "
            f"supported: {sorted(_PARSERS)}"
        )
    try:
        normalized = parser(raw_bytes)
    except LiveIngestError:
        raise
    except Exception as e:
        raise LiveIngestError(
            f"ingest_one({target.source_id}): parser {target.parser_name!r} raised: {e}"
        ) from e

    retrieved_at = now_fn()
    raw_relpath = f"raw/{content_hash}"

    provenance = lovs_archive.ProvenanceRecord(
        source_id=target.source_id,
        source_tier=target.source_tier,
        publisher=target.publisher,
        url=target.url,
        retrieved_at=retrieved_at,
        published_at=published_at if published_at is not None else retrieved_at,
        content_hash=content_hash,
        license=target.license,
        extraction_status="success" if normalized else "partial",
        root_provenance_chain=(),
    )

    snapshot_meta = {
        "outbreak_id": target.outbreak_id,
        "pathogen": target.pathogen,
        "country_scope": target.country_scope,
        "geography_id": target.geography_id,
        "raw_bytes_relpath": raw_relpath,
        "raw_archive_status": "public_bytes",
        "normalized_content": normalized,
    }

    lovs_archive.add_snapshot(
        archive_root, provenance, snapshot_meta, raw_bytes
    )

    return lovs_archive.ArchivedSnapshot(
        provenance=provenance,
        outbreak_id=target.outbreak_id,
        pathogen=target.pathogen,
        country_scope=target.country_scope,
        geography_id=target.geography_id,
        raw_bytes_relpath=raw_relpath,
        raw_archive_status="public_bytes",
        normalized_content=normalized,
    )


def ingest_bdbv_2026(
    archive_root: pathlib.Path,
    targets: tuple[IngestTarget, ...] = (WHO_DON_602_TARGET,),
    fetch_fn=_fetch_bytes,
    now_fn=_now_utc_iso_z,
) -> tuple[lovs_archive.ArchivedSnapshot, ...]:
    """Run a one-shot ingest cycle for the BDBV 2026 outbreak.

    Returns the new ArchivedSnapshots that were added in this call (an
    empty tuple if all targets were already archived). Per-target errors
    are not swallowed: a fail-closed live ingest stops the cycle as soon
    as any target fails, so a partial archive is never persisted.

    The default targets are (WHO_DON_602_TARGET,). Tests can pass a custom
    fetch_fn / now_fn to drive deterministic behavior without network I/O.
    """
    new_snapshots: list[lovs_archive.ArchivedSnapshot] = []
    for target in targets:
        snap = ingest_one(target, archive_root, fetch_fn=fetch_fn, now_fn=now_fn)
        if snap is not None:
            new_snapshots.append(snap)
    return tuple(new_snapshots)
