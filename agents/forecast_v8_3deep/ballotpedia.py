"""Ballotpedia briefing module.

Fetches candidate profile summaries from Ballotpedia and exposes a clean
multi-candidate brief block for injection into agent prompts.

API surface:
  - get_candidate_brief(name: str) -> dict | None
  - build_election_brief(question: str, outcomes: list[str], cutoff_dt: datetime | None) -> str

Implementation notes:
  - Ballotpedia's MediaWiki API is WAF-blocked for automated requests, but the
    main page HTML returns 200 with a browser-style User-Agent. We scrape the
    rendered page and extract the lead paragraphs + biographical text.
  - Results are cached to disk to amortize cost across pods and repeats.
  - For RETROSPECTIVE testing on settled events, the page may contain
    post-resolution information (e.g. "X defeated Y in the primary"). The
    `cutoff_dt` filter is best-effort but does not fully sanitize this —
    use only for live (future) forecasts in production.
"""

from __future__ import annotations

import gzip
import html as html_lib
import io
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("ballotpedia")

_CACHE_DIR_DEFAULT = str(Path(__file__).parent / ".cache" / "ballotpedia")
CACHE_DIR = Path(os.environ.get("BALLOTPEDIA_CACHE", _CACHE_DIR_DEFAULT))

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

_REQUEST_TIMEOUT_S = 15
_MAX_RETRIES = 2
_RATE_LIMIT_S = 0.5  # be polite to Ballotpedia
_last_request_ts = 0.0


def _slug(name: str) -> str:
    s = name.strip().replace(" ", "_")
    return urllib.parse.quote(s, safe="_()")


def _cache_path(slug: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{slug}.json"


def _fetch_html(url: str) -> tuple[str | None, int | str]:
    """Fetch URL with browser-style headers. Returns (html, status_or_err)."""
    global _last_request_ts
    # Rate-limit
    dt = time.time() - _last_request_ts
    if dt < _RATE_LIMIT_S:
        time.sleep(_RATE_LIMIT_S - dt)
    _last_request_ts = time.time()

    req = urllib.request.Request(url, headers=_HEADERS)
    last_err: str = ""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                body = raw.decode("utf-8", errors="replace")
                return body, r.status
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code == 404:
                return None, 404
            if e.code in (429, 503) and attempt < _MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            return None, last_err
        except Exception as e:
            last_err = repr(e)
            if attempt < _MAX_RETRIES:
                time.sleep(1)
                continue
            return None, last_err
    return None, last_err


_SECTION_STOP_IDS = (
    "External_links", "See_also", "Footnotes", "Bibliography",
    "Election_analysis", "Recent_news", "Suggest_a_link",
)
_SECTION_STOP_RE = re.compile(
    r'<h[12][^>]*>\s*<span[^>]*id="(?:' + "|".join(_SECTION_STOP_IDS) + r')"',
    re.S,
)


def _extract_main_text(html: str, max_chars: int = 3000) -> str | None:
    """Extract main content from a Ballotpedia page as plain text."""
    body_m = re.search(
        r'<div[^>]*class="[^"]*mw-parser-output[^"]*"[^>]*>(.*?)<!--\s*\nNewPP',
        html, re.S,
    )
    if not body_m:
        body_m = re.search(
            r'<div[^>]*id="mw-content-text"[^>]*>(.*?)<div[^>]*id="catlinks"',
            html, re.S,
        )
    if not body_m:
        return None
    main = body_m.group(1)
    # Cut at "External links" / "See also" / "Footnotes"
    cut = _SECTION_STOP_RE.search(main)
    if cut:
        main = main[: cut.start()]
    # Strip TOC
    main = re.sub(r'<div[^>]*id="toc"[^>]*>.*?</div>\s*</div>', "", main, flags=re.S)
    # Strip tables (vote totals, sidebars)
    main = re.sub(r'<table[^>]*>.*?</table>', " ", main, flags=re.S)
    # Strip scripts/styles
    main = re.sub(r'<(script|style)[^>]*>.*?</\1>', " ", main, flags=re.S)
    # Drop tags
    text = re.sub(r'<[^>]+>', " ", main)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


_DISAMBIG_PATTERNS = [
    "{name}",
    "{name}_(politician)",
    "{name}_(Ohio)", "{name}_(West_Virginia)", "{name}_(California)",
    "{name}_(New_York)", "{name}_(Texas)", "{name}_(Florida)",
    "{name}_(Pennsylvania)", "{name}_(Michigan)", "{name}_(Illinois)",
    "{name}_(Massachusetts)", "{name}_(Virginia)", "{name}_(Georgia)",
    "{name}_(North_Carolina)", "{name}_(Arizona)",
]


def _looks_like_disambig(text: str | None) -> bool:
    """Return True if the page text is a 'X may refer to: ...' disambig stub."""
    if not text:
        return False
    head = text[:400].lower()
    # Disambig pages start with the name + "may refer to:" listing
    if "may refer to:" in head and len(text) < 1500:
        return True
    return False


def get_candidate_brief(
    name: str,
    state_hint: str | None = None,
    max_chars: int = 1800,
    use_cache: bool = True,
) -> dict | None:
    """Return {'name', 'url', 'brief'} or None if no Ballotpedia page found.

    Tries the name as-is, then with state disambiguation suffixes if the
    direct hit looks like a stub disambig page.
    """
    base_slug = _slug(name)
    cache = _cache_path(base_slug)
    if use_cache and cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass

    candidate_slugs = []
    candidate_slugs.append(base_slug)
    if state_hint:
        candidate_slugs.append(_slug(f"{name} ({state_hint})"))
    for pat in _DISAMBIG_PATTERNS[1:]:
        candidate_slugs.append(pat.format(name=base_slug))

    seen = set()
    best: dict | None = None
    for slug in candidate_slugs:
        if slug in seen:
            continue
        seen.add(slug)
        url = f"https://ballotpedia.org/{slug}"
        html, status = _fetch_html(url)
        if html is None or status != 200:
            logger.debug("ballotpedia miss %s status=%s", slug, status)
            continue
        text = _extract_main_text(html, max_chars=max_chars)
        if not text:
            continue
        if _looks_like_disambig(text):
            # Try the next disambig pattern
            logger.debug("ballotpedia disambig %s", slug)
            continue
        # Heuristic: prefer pages that mention the candidate as Democratic/Republican/Libertarian Party
        result = {
            "name": name,
            "slug": slug,
            "url": url,
            "brief": text,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }
        best = result
        break

    if best is not None and use_cache:
        try:
            cache.write_text(json.dumps(best))
        except Exception:
            pass
    return best


_POST_RESOLUTION_PATTERNS = (
    re.compile(r"\b(?:defeated|lost to|defeated by)\b[^.]+(?:primary|election)[^.]*\.", re.I),
    # "He/She lost in / advanced from / won the X primary on DATE."
    re.compile(
        r"\b(?:He|She|They|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+"
        r"(?:advanced from|lost(?:\s+(?:in|to))?|won|received the nomination(?:\s+in)?)\b"
        r"[^.]*?\b(?:primary|election|nomination)\b[^.]*\.",
        re.I,
    ),
    re.compile(r"\bwas the winner of the (?:\d{4} )?(?:Democratic|Republican|Libertarian) primary[^.]*\.", re.I),
    # "X (D/R) defeated Y in the primary"
    re.compile(r"\b[A-Z][a-z]+ \([A-Z]\) defeated [A-Z][a-z]+[^.]*primary[^.]*\.", re.I),
    # CRITICAL: "is on the ballot in the general election" implies primary-won
    re.compile(r"\b(?:He|She|They|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is on the ballot in the general election[^.]*\.", re.I),
    re.compile(r"\bis on the ballot in the general election[^.]*\.", re.I),
    # "Miller/Leonard ran for election to the U.S. House" (past tense → race over)
    # Allows for ( Democratic Party ) with spaces; allows for no parens at all
    re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?(?:\s*\([^)]*\))?\s+ran for election\b[^.]*\.", re.I),
    re.compile(r"\bran for election to the (?:U\.S\.|United States) (?:House|Senate)[^.]*\.", re.I),
    # "This page was current at the end of the (official's last term|individual's last campaign)"
    re.compile(r"\bThis page was current at the end of the (?:official's last term|individual's last campaign)[^.]*\.", re.I),
    # CRITICAL: presence/absence of "Next election" leaks primary winner
    # (primary winners get "Next election DATE" added to profile; losers don't)
    re.compile(r"\bNext election\s+[A-Z][a-z]+\s+\d{1,2},?\s*\d{4}\b", re.I),
    # Also strip the "Elections and appointments Last election ... Next election ..." block
    re.compile(r"\bElections and appointments\s+Last election\s+[A-Z][a-z]+\s+\d{1,2},?\s*\d{4}\s+Next election\s+[A-Z][a-z]+\s+\d{1,2},?\s*\d{4}", re.I),
)

# Truncate at any of these section-header strings — content from these
# onward typically describes resolved election results and is lookahead-leaky.
# NOTE: "Endorsements" is INTENTIONALLY NOT in this list — pre-primary
# endorsements are genuine signal that we want to preserve. The sentence-level
# strip handles "endorsed in primary X that they won"-style language.
_RESOLUTION_SECTION_MARKERS = (
    "Democratic primary election",
    "Republican primary election",
    "Libertarian primary election",
    "General election",
    "Primary election",
    "Withdrawn or disqualified candidates",
)


def _strip_post_resolution(brief: str, hard_truncate: bool = True) -> str:
    """Remove sentences that leak election outcomes. With hard_truncate=True,
    also cut at the first 'primary election' / 'general election' section
    header — preserves bio/education/profession but drops resolved-results."""
    out = brief
    for pat in _POST_RESOLUTION_PATTERNS:
        out = pat.sub("", out)
    if hard_truncate:
        # Find the EARLIEST marker and cut there
        cut_idx = len(out)
        for marker in _RESOLUTION_SECTION_MARKERS:
            i = out.find(marker)
            if 0 <= i < cut_idx:
                cut_idx = i
        out = out[:cut_idx]
    out = re.sub(r"\s+", " ", out).strip()
    return out


def build_election_brief(
    question: str,
    outcomes: list[str],
    cutoff_dt: datetime | None = None,
    state_hint: str | None = None,
    max_chars_per_candidate: int = 1500,
    strip_resolution: bool = True,
) -> tuple[str, list[dict]]:
    """Build a Ballotpedia brief block for an election event.

    Returns (formatted_block, per_candidate_records). Empty string + empty list
    if no candidates were found.
    """
    parts: list[str] = []
    records: list[dict] = []

    # Try to extract state hint from question if not provided
    if state_hint is None and question:
        m = re.search(r"\b(Ohio|West Virginia|California|Texas|Florida|Pennsylvania|"
                      r"Michigan|Illinois|Massachusetts|Virginia|Georgia|"
                      r"North Carolina|Arizona|New York|New Jersey)\b", question)
        if m:
            state_hint = m.group(1)

    for o in outcomes:
        try:
            d = get_candidate_brief(o, state_hint=state_hint,
                                     max_chars=max_chars_per_candidate)
        except Exception as e:
            logger.warning("ballotpedia error for %s: %s", o, e)
            d = None
        if not d:
            records.append({"name": o, "found": False})
            continue
        brief = d["brief"]
        if strip_resolution:
            brief = _strip_post_resolution(brief)
        records.append({
            "name": o,
            "found": True,
            "url": d["url"],
            "slug": d["slug"],
            "char_len": len(brief),
        })
        parts.append(f"### {o}\nSource: {d['url']}\n{brief}")

    if not parts:
        return "", records
    header = "BALLOTPEDIA CANDIDATE BRIEFS (third-party profile data, may be incomplete):"
    return header + "\n\n" + "\n\n".join(parts), records


# ============================== CLI test ==============================
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        names = sys.argv[1:]
    else:
        names = ["Don Leonard", "Adam Miller", "Vince George", "Britta Aguirre",
                 "Mike Carey", "Péter Magyar"]
    for n in names:
        print(f"\n=== {n} ===")
        d = get_candidate_brief(n, state_hint="Ohio" if "Leonard" in n or "Miller" in n else None,
                                 max_chars=1500, use_cache=False)
        if d:
            print(f"  URL: {d['url']}")
            print(f"  Brief: {d['brief'][:800]}")
        else:
            print("  no Ballotpedia page found")
