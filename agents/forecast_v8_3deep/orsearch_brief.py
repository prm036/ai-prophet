"""OpenRouter web_search-based brief builder.

Drop-in replacement for the Tavily-fetch + Haiku-summarize pipeline used in
agent_v8_3deep. Uses anthropic/claude-haiku-4.5 with the openrouter:web_search
tool, which routes to Anthropic's native web search backend for Claude models
— the same backend Claude Code's WebSearch uses.

Lookahead-debiasing for retrospective testing:
  Layer 1 (prompt-time): Tells Haiku to use ONLY sources strictly before cutoff
  Layer 2 (post-hoc): Parses publication dates from citation URLs/content and
                       drops any citation explicitly dated after the cutoff.
                       Conservative — citations without parseable dates are KEPT
                       (Haiku already filtered at layer 1).

For LIVE production (cutoff_dt = now or far-future), debiasing is a no-op.

API:
  build_brief(event, cutoff_dt, *, max_results=8, debiased=True) ->
    (brief_text, metadata_dict)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("orsearch_brief")

_OR_API = "https://openrouter.ai/api/v1/chat/completions"
_OR_MODEL = os.environ.get("ORSEARCH_MODEL", "anthropic/claude-haiku-4.5")
_OR_MAX_TOKENS = int(os.environ.get("ORSEARCH_MAX_TOKENS", "1200"))
_OR_TIMEOUT_S = int(os.environ.get("ORSEARCH_TIMEOUT_S", "90"))


# ---- date parsing for citations ----------------------------------------------

_URL_DATE_RE = re.compile(r'/(\d{4})/(\d{1,2})/(\d{1,2})/')
_MONTH_RE = re.compile(
    r'\b((?:January|February|March|April|May|June|July|August|'
    r'September|October|November|December)\s+\d{1,2},\s*\d{4})\b',
    re.I,
)
_ABBREV_MONTH_RE = re.compile(
    r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s*\d{4})\b',
    re.I,
)


def _try_parse_date(s: str) -> datetime | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def extract_publication_date(citation: dict) -> datetime | None:
    """Extract a probable publication date from a citation. None if unparseable."""
    url = citation.get("url", "") or ""
    content = citation.get("content", "") or ""

    # URL-embedded /YYYY/MM/DD/
    m = _URL_DATE_RE.search(url)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return datetime(y, mo, d, tzinfo=timezone.utc)
        except ValueError:
            pass

    # Content: "Updated/Published Month DD, YYYY"
    for pre in ("Updated ", "Published "):
        i = content.find(pre)
        if i >= 0:
            tail = content[i + len(pre): i + len(pre) + 40]
            for rx in (_MONTH_RE, _ABBREV_MONTH_RE):
                m = rx.search(tail)
                if m:
                    d = _try_parse_date(m.group(1))
                    if d:
                        return d

    # First Month-DD-YYYY date that appears in the first 500 chars (lead/byline)
    head = content[:500]
    for rx in (_MONTH_RE, _ABBREV_MONTH_RE):
        m = rx.search(head)
        if m:
            d = _try_parse_date(m.group(1))
            if d:
                return d
    return None


# ---- main brief builder ------------------------------------------------------


def _build_prompt(event: dict, cutoff_dt: datetime | None, debiased: bool) -> str:
    title = event.get("title", "")
    outcomes = event.get("outcomes", []) or []
    category = event.get("category", "?")
    description = (event.get("description") or "").strip()[:600]

    cutoff_block = ""
    if debiased and cutoff_dt is not None:
        cutoff_str = cutoff_dt.strftime("%Y-%m-%d")
        cutoff_block = (
            f"\n\nCRITICAL DATE CONSTRAINT — RETROSPECTIVE FORECAST MODE:\n"
            f"You are writing this brief AS IF you were a researcher at time "
            f"{cutoff_str}. The forecast was made at this date, before the "
            f"event resolved. You MUST use ONLY sources published STRICTLY "
            f"BEFORE {cutoff_str}.\n"
            f"  - When citing a source dated on or after {cutoff_str}, IGNORE "
            f"it completely. Do not include its content in the brief.\n"
            f"  - When a source contains BOTH pre-cutoff and post-cutoff facts, "
            f"include ONLY the pre-cutoff facts.\n"
            f"  - NEVER reveal the outcome or any post-cutoff developments.\n"
            f"  - If a URL contains a date like /2026/05/15/, treat that as the "
            f"publication date — if it's on or after the cutoff, ignore.\n"
            f"  - Acceptable: candidates' backgrounds, biographies, campaign "
            f"themes, endorsements, pre-cutoff polling/finance, pre-cutoff "
            f"news events, base rates.\n"
            f"  - NOT acceptable: vote totals, primary results, post-event "
            f"analyses, anything that reveals what happened.\n"
        )

    return (
        f"Research task: produce a concise EVIDENCE BRIEF for a forecasting "
        f"agent on the following event.\n\n"
        f"Event: {title}\n"
        f"Category: {category}\n"
        f"Outcomes: {', '.join(outcomes)}\n"
        + (f"Description: {description}\n" if description else "")
        + cutoff_block
        + f"\n\nUse web_search liberally to gather information. Focus on what "
        f"would actually move a forecaster's probability for or against each "
        f"outcome (key actors, recent track record, expert assessments, "
        f"market signals, base rates).\n\n"
        f"Write the brief in <=350 words. Plain prose, no markdown headers. "
        f"Mention sources inline. End with the most important 2-3 facts."
    )


def build_brief(
    event: dict,
    cutoff_dt: datetime | None,
    *,
    max_results: int = 8,
    debiased: bool = True,
    return_filtered_citations: bool = True,
) -> tuple[str, dict]:
    """Build a research brief using openrouter:web_search.

    Returns (brief_text, metadata_dict) where metadata contains:
      - model, max_results, debiased
      - n_citations_total
      - n_citations_kept (post-cutoff filtered out)
      - n_citations_dropped_post_cutoff
      - n_citations_undated
      - citations: list of {url, title, published_at (str or None), kept (bool)}
      - usage: token + cost
      - search_calls: number of search invocations
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return "", {"error": "OPENROUTER_API_KEY not set"}

    user_msg = _build_prompt(event, cutoff_dt, debiased)
    payload = {
        "model": _OR_MODEL,
        "max_tokens": _OR_MAX_TOKENS,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": user_msg}],
        "tools": [{
            "type": "openrouter:web_search",
            "openrouter:web_search": {"engine": "auto", "max_results": max_results},
        }],
    }
    req = urllib.request.Request(
        _OR_API,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get("OR_REFERER", "https://prophethacks.com"),
            "User-Agent": "AI-Prophet-Forecast/1.0",
        },
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=_OR_TIMEOUT_S) as r:
            body = json.loads(r.read())
    except Exception as e:
        logger.warning("orsearch_brief OR call failed: %s", e)
        return "", {"error": repr(e)}
    dt = time.time() - t0

    msg = (body.get("choices") or [{}])[0].get("message", {})
    raw_brief = (msg.get("content") or "").strip()
    annotations = msg.get("annotations", []) or []

    # Categorize citations by date
    cits = []
    n_kept = 0
    n_dropped = 0
    n_undated = 0
    for a in annotations:
        c = a.get("url_citation", a) or {}
        pub_dt = extract_publication_date(c)
        keep = True
        reason = ""
        if debiased and cutoff_dt is not None and pub_dt is not None:
            if pub_dt >= cutoff_dt:
                keep = False
                reason = "post-cutoff"
        cits.append({
            "url": c.get("url", ""),
            "title": c.get("title", ""),
            "published_at": pub_dt.isoformat() if pub_dt else None,
            "kept": keep,
            "reason": reason,
        })
        if keep:
            n_kept += 1
            if pub_dt is None:
                n_undated += 1
        else:
            n_dropped += 1

    # Post-hoc redaction: if any sentence in the brief mentions a post-cutoff
    # date or a known winner phrase, redact. Conservative.
    cleaned_brief = _redact_post_cutoff_facts(raw_brief, cutoff_dt) if debiased else raw_brief

    meta = {
        "model": _OR_MODEL,
        "max_results": max_results,
        "debiased": debiased,
        "n_citations_total": len(cits),
        "n_citations_kept": n_kept,
        "n_citations_dropped_post_cutoff": n_dropped,
        "n_citations_undated": n_undated,
        "citations": cits if return_filtered_citations else [],
        "usage": body.get("usage"),
        "wall_seconds": round(dt, 2),
        "raw_brief_len": len(raw_brief),
        "cleaned_brief_len": len(cleaned_brief),
    }
    return cleaned_brief, meta


# ---- post-hoc redaction ------------------------------------------------------

_WINNER_PHRASES = (
    re.compile(r"\b(?:defeated|lost to|defeated by|beat|outpolled|edged out)\b[^.]+\.", re.I),
    re.compile(r"\bwon the (?:\d{4} )?(?:Democratic|Republican|Libertarian|primary)\b[^.]*\.", re.I),
    re.compile(r"\b(?:advanced from|advanced to|emerged from) the (?:Democratic|Republican|Libertarian)?\s*primary\b[^.]*\.", re.I),
    re.compile(r"\bfinal (?:vote|tally|results?)[^.]*\.", re.I),
    re.compile(r"\bwith \d{1,3}(?:,\d{3})* votes\b[^.]*\.", re.I),
    re.compile(r"\breceived (?:approximately )?\d{1,3}(?:,\d{3})*\s*votes\b[^.]*\.", re.I),
)


def _redact_post_cutoff_facts(brief: str, cutoff_dt: datetime | None) -> str:
    """Best-effort redaction of sentences that announce resolved outcomes."""
    out = brief
    for pat in _WINNER_PHRASES:
        out = pat.sub("[redacted]", out)
    # Also redact sentences containing dates strictly after cutoff
    if cutoff_dt is not None:
        keep_lines = []
        for sentence in re.split(r'(?<=[.!?])\s+', out):
            drop = False
            for rx in (_MONTH_RE, _ABBREV_MONTH_RE):
                m = rx.search(sentence)
                if m:
                    d = _try_parse_date(m.group(1))
                    if d and d >= cutoff_dt:
                        drop = True
                        break
            if drop:
                keep_lines.append("[redacted]")
            else:
                keep_lines.append(sentence)
        out = " ".join(keep_lines)
    # Collapse repeated redactions
    out = re.sub(r'(\[redacted\]\s*){2,}', '[redacted] ', out)
    return out.strip()


# ---- CLI test ----------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    # Bootstrap .env so the CLI test works
    if not os.environ.get("OPENROUTER_API_KEY"):
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    event = {
        "title": "Who won the 2026 Democratic primary for Ohio's 15th Congressional District?",
        "outcomes": ["Don Leonard", "Adam Miller"],
        "category": "Elections",
    }
    cutoff = datetime(2026, 5, 2, tzinfo=timezone.utc)
    brief, meta = build_brief(event, cutoff, max_results=8, debiased=True)
    print("=== BRIEF ===")
    print(brief)
    print("\n=== META ===")
    print(json.dumps({k: v for k, v in meta.items() if k != "citations"}, indent=2))
    print("\n=== CITATIONS (post-filter) ===")
    for c in meta["citations"]:
        marker = "KEEP" if c["kept"] else f"DROP[{c['reason']}]"
        print(f"  [{marker}] {c['published_at'] or 'undated'}  {c['url'][:90]}")
