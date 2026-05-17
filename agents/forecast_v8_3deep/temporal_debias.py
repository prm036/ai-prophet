"""Strict temporal debiasing for OR-search results.

Layered defense to keep retrospective smoke tests honest (live eval needs
none of this — there is no future at forecast time).

LAYERS:
  1. URL-date parse: drop citations with `/YYYY/MM/DD/` in URL where date >= cutoff
  2. Page-fetch metadata: HTTP-fetch the URL, parse <meta property="article:published_time">,
     <time datetime="...">, schema.org JSON-LD datePublished — drop if >= cutoff
  3. Content-date scan: search content body for "Updated|Published <date>" markers
  4. Sentence-level redaction: drop any sentence that contains:
       - winner-revealing verbs ("won the primary", "defeated X", "advanced from")
       - vote totals ("23,554 votes", "53%", "X cast")
       - resolution-state language ("nominee", "advanced to general")
       - dates within +/-3 days of cutoff (often the resolution day)
  5. Citation-drop: any citation whose surviving content is <100 chars after redaction
  6. Brief-level redaction: same sentence-level filter applied to any synthesized brief

API:
  - validate_citation(citation: dict, cutoff_dt: datetime, fetch_page=True)
      -> dict with {kept, reason, date_source, cleaned_content}
  - debias_text(text: str, cutoff_dt: datetime, named_entities: list[str] = None)
      -> str  (sentence-redacted)
  - debias_search_block(formatted_block: str, cutoff_dt, named_entities=None)
      -> str
"""

from __future__ import annotations

import gzip
import html as html_lib
import io
import json
import logging
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("temporal_debias")


# ---- date parsing ------------------------------------------------------------

_URL_DATE_RE = re.compile(r'/(\d{4})/(\d{1,2})/(\d{1,2})(?:[/_-]|$)')
_ISO_DATE_RE = re.compile(r'\b(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s]|\b)')
_LONG_DATE_RE = re.compile(
    r'\b((?:January|February|March|April|May|June|July|August|'
    r'September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|'
    r'May|Jun\.?|Jul\.?|Aug\.?|Sept?\.?|Oct\.?|Nov\.?|Dec\.?)'
    r'\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b',
    re.I,
)


def _parse_date_flexible(s: str) -> datetime | None:
    for fmt in (
        "%B %d, %Y", "%b %d, %Y", "%b. %d, %Y",
        "%B %d %Y", "%b %d %Y", "%Y-%m-%d",
    ):
        try:
            d = datetime.strptime(s.replace(".", "").replace(",", ""), fmt)
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_date_from_url(url: str) -> datetime | None:
    if not url:
        return None
    m = _URL_DATE_RE.search(url)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return datetime(y, mo, d, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _extract_date_from_text(text: str) -> datetime | None:
    if not text:
        return None
    # Try labeled (Updated/Published)
    for pre in ("Updated", "Published", "Posted", "Date:"):
        i = text.find(pre)
        if 0 <= i < 1500:
            tail = text[i: i + 80]
            for rx in (_LONG_DATE_RE, _ISO_DATE_RE):
                m = rx.search(tail)
                if m:
                    if rx is _LONG_DATE_RE:
                        d = _parse_date_flexible(m.group(1))
                    else:
                        try:
                            d = datetime(int(m.group(1)), int(m.group(2)),
                                          int(m.group(3)), tzinfo=timezone.utc)
                        except ValueError:
                            d = None
                    if d:
                        return d
    # Fallback: first long-form date in head
    head = text[:1500]
    m = _LONG_DATE_RE.search(head)
    if m:
        return _parse_date_flexible(m.group(1))
    m = _ISO_DATE_RE.search(head)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                             tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# ---- page-fetch metadata -----------------------------------------------------

_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}
_PAGE_TIMEOUT_S = 6  # short — these are best-effort
_PAGE_FETCH_CACHE: dict[str, datetime | None] = {}


def _fetch_page_published_date(url: str) -> datetime | None:
    """Best-effort fetch + parse of canonical publication date metadata."""
    if not url or len(url) > 500:
        return None
    if url in _PAGE_FETCH_CACHE:
        return _PAGE_FETCH_CACHE[url]
    try:
        req = urllib.request.Request(url, headers=_PAGE_HEADERS)
        with urllib.request.urlopen(req, timeout=_PAGE_TIMEOUT_S) as r:
            raw = r.read(200_000)
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            body = raw.decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("page fetch failed for %s: %s", url[:80], e)
        _PAGE_FETCH_CACHE[url] = None
        return None
    # <meta property="article:published_time" content="2026-04-19T15:23:00Z">
    for pat in (
        r'<meta[^>]+(?:property|name)=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+(?:property|name)=["\']og:article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+(?:property|name)=["\']datePublished["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+(?:property|name)=["\']publishdate["\'][^>]+content=["\']([^"\']+)["\']',
        r'<time[^>]+datetime=["\']([^"\']+)["\']',
        r'"datePublished"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pat, body, re.I)
        if m:
            raw = m.group(1)[:32]
            try:
                # Various ISO-ish patterns
                d_str = raw.replace("Z", "+00:00")
                d = datetime.fromisoformat(d_str)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                _PAGE_FETCH_CACHE[url] = d
                return d
            except Exception:
                pass
    _PAGE_FETCH_CACHE[url] = None
    return None


# ---- citation-level validation ----------------------------------------------


def validate_citation(
    citation: dict,
    cutoff_dt: datetime,
    *,
    fetch_page: bool = True,
) -> dict:
    """Return {kept, reason, date, date_source} for one citation."""
    url = (citation.get("url") or "")[:500]
    content = (citation.get("content") or "")[:5000]
    # Layer 1: URL date
    d = _extract_date_from_url(url)
    if d is not None:
        return {
            "kept": d < cutoff_dt,
            "reason": "url_date_after_cutoff" if d >= cutoff_dt else "ok_url_date",
            "date": d.isoformat(),
            "date_source": "url",
        }
    # Layer 2: page-fetch metadata
    if fetch_page:
        d = _fetch_page_published_date(url)
        if d is not None:
            return {
                "kept": d < cutoff_dt,
                "reason": "page_meta_after_cutoff" if d >= cutoff_dt else "ok_page_meta",
                "date": d.isoformat(),
                "date_source": "page_meta",
            }
    # Layer 3: text content date
    d = _extract_date_from_text(content)
    if d is not None:
        return {
            "kept": d < cutoff_dt,
            "reason": "content_date_after_cutoff" if d >= cutoff_dt else "ok_content_date",
            "date": d.isoformat(),
            "date_source": "content",
        }
    # No date — STRICT mode: drop. (We default conservative — undated kept could leak.)
    return {
        "kept": False,
        "reason": "no_parseable_date",
        "date": None,
        "date_source": None,
    }


# ---- sentence-level redaction -----------------------------------------------

_RESULT_PHRASE_PATTERNS = [
    # Winner verbs
    r"\bwon\s+(?:the\s+)?(?:Democratic|Republican|Libertarian|primary|election|race|nomination|seat)\b",
    r"\b(?:defeated|edged out|beat|outpolled|trounced)\b\s+[A-Z][a-z]",
    r"\b(?:advanced|advances?)\s+(?:from|to)\s+(?:the\s+)?(?:Democratic|Republican|general|primary)\b",
    r"\blost\s+(?:in\s+)?(?:the\s+)?(?:Democratic|Republican|Libertarian|primary|election|nomination)\b",
    # Result language
    r"\bunofficial\s+(?:results|tally|vote)",
    r"\bofficial\s+(?:results|tally|vote)",
    r"\bfinal\s+(?:results|tally|vote|count)",
    r"\b(?:vote|votes)\s+(?:totals|counted|tallied|cast)",
    r"\b(?:received|got|secured|garnered)\s+\d{1,3}(?:,\d{3})*\s+votes\b",
    r"\b\d{1,3}(?:,\d{3})*\s+votes\b",
    r"\bwith\s+\d{1,3}(?:\.\d+)?%\s+of\s+(?:the\s+)?vote",
    r"\bnow\s+(?:the\s+)?(?:Democratic|Republican|Libertarian)\s+(?:nominee|candidate)\b",
    r"\b(?:the\s+)?winner\s+of\s+the\s+(?:Democratic|Republican|primary|election)\b",
    r"\b(?:projected|called|declared)\s+(?:as\s+)?(?:the\s+)?winner\b",
    # Sports / generic outcome language
    r"\bfinal\s+score\b",
    r"\b(?:beat|defeated|topped|knocked off|edged)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:\d+[-–]\d+|in\s+\d)",
    r"\bwon\s+\d+[-–]\d+",
]
_RESULT_PHRASE_RE = re.compile("|".join(_RESULT_PHRASE_PATTERNS), re.I)


def _entity_winner_pattern(entity: str) -> re.Pattern:
    """For each named entity, drop sentences containing entity + winner verb."""
    e = re.escape(entity)
    return re.compile(
        rf"\b{e}\b[^.!?]*?\b(?:won|wins|winning|defeated|advanced|lost|prevailed|"
        rf"beat|outpolled|edged|secured\s+the\s+(?:nomination|primary)|"
        rf"clinched|topped)\b",
        re.I,
    )


def debias_text(
    text: str,
    cutoff_dt: datetime,
    named_entities: list[str] | None = None,
) -> str:
    """Sentence-level redaction. Drops sentences containing winner-revealing
    language, vote totals, resolution-state phrases, or dates within +/-3
    days of the cutoff (often the resolution day itself).

    Preserves sentences with only pre-cutoff factual content.
    """
    if not text:
        return text
    entities = named_entities or []
    entity_pats = [_entity_winner_pattern(e) for e in entities]

    # Cutoff-near window: drop sentences with explicit dates within +/- 3 days
    window_start = cutoff_dt - timedelta(days=3)
    window_end = cutoff_dt + timedelta(days=30)

    out_parts: list[str] = []
    # Split into sentences (simple: . ! ? followed by space + uppercase)
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z\[])', text)
    for sent in sentences:
        # Generic result phrases
        if _RESULT_PHRASE_RE.search(sent):
            continue
        # Entity-specific winner phrases
        dropped = False
        for pat in entity_pats:
            if pat.search(sent):
                dropped = True
                break
        if dropped:
            continue
        # Date within cutoff window
        date_in_sent = None
        m = _LONG_DATE_RE.search(sent)
        if m:
            date_in_sent = _parse_date_flexible(m.group(1))
        if not date_in_sent:
            m = _ISO_DATE_RE.search(sent)
            if m:
                try:
                    date_in_sent = datetime(int(m.group(1)), int(m.group(2)),
                                              int(m.group(3)), tzinfo=timezone.utc)
                except ValueError:
                    pass
        if date_in_sent and (window_start <= date_in_sent <= window_end):
            # This date is the resolution date itself OR after — drop the sentence
            continue
        out_parts.append(sent)

    cleaned = " ".join(out_parts).strip()
    # Collapse repeated whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ---- top-level: clean a multi-citation search-result block -------------------


def debias_search_block(
    citations: list[dict],
    cutoff_dt: datetime,
    *,
    named_entities: list[str] | None = None,
    fetch_page: bool = True,
    min_content_chars: int = 100,
) -> tuple[list[dict], dict]:
    """Apply all layers to a list of citations. Returns (kept_citations, stats).

    Each kept_citation has its `content` field redacted via debias_text.
    Citations whose redacted content shrinks below min_content_chars are dropped.
    """
    kept = []
    n_total = len(citations)
    n_dropped_url = 0
    n_dropped_meta = 0
    n_dropped_content = 0
    n_dropped_undated = 0
    n_dropped_redaction_empty = 0
    for c in citations:
        v = validate_citation(c, cutoff_dt, fetch_page=fetch_page)
        if not v["kept"]:
            r = v["reason"]
            if "url_date" in r: n_dropped_url += 1
            elif "page_meta" in r: n_dropped_meta += 1
            elif "content_date" in r: n_dropped_content += 1
            else: n_dropped_undated += 1
            continue
        # Redact content
        red = debias_text(c.get("content", "") or "", cutoff_dt, named_entities)
        if len(red) < min_content_chars:
            n_dropped_redaction_empty += 1
            continue
        kept_cit = dict(c)
        kept_cit["content"] = red
        kept_cit["validated_date"] = v["date"]
        kept_cit["date_source"] = v["date_source"]
        kept.append(kept_cit)
    stats = {
        "n_total": n_total,
        "n_kept": len(kept),
        "n_dropped_url_date": n_dropped_url,
        "n_dropped_page_meta": n_dropped_meta,
        "n_dropped_content_date": n_dropped_content,
        "n_dropped_undated": n_dropped_undated,
        "n_dropped_redaction_empty": n_dropped_redaction_empty,
    }
    return kept, stats


# ---- CLI smoke test ----------------------------------------------------------
if __name__ == "__main__":
    cutoff = datetime(2026, 5, 2, tzinfo=timezone.utc)
    sample_text = (
        "Don Leonard is a Cornell PhD and OSU professor. He is running for "
        "the U.S. House Ohio District 15. Adam Miller is a former state "
        "representative with military background. Don Leonard defeated Adam "
        "Miller in the Democratic primary on May 5, 2026 with 53% of the vote. "
        "Leonard had 23,554 votes. Miller raised $779,132 through March 31, 2026."
    )
    cleaned = debias_text(sample_text, cutoff, named_entities=["Don Leonard", "Adam Miller"])
    print("INPUT:")
    print(" ", sample_text)
    print("\nCLEANED:")
    print(" ", cleaned)
