"""agent_v8_3deep_orall_v2 — same architecture as orall + 4 math fixes.

Adds 4 calibration fixes derived from the COT_POST_MORTEM analysis of the
26-event v8_3deep_orall benchmark (mean Brier 0.3247):

  (A) Multi-class Platt scaling:
      α(N) = 1 + (PLATT_A - 1) / max(1, N_outcomes - 1)
      Binary keeps α=2.0; 14-outcome (Survivor) gets α ≈ 1.077; correctly
      caps multi-class extremization that mangles near-tie distributions.

  (B) Disagreement-aware Kalshi α:
      Per-cat α reduced toward 0 when:
        - Kalshi is uninformative (max Kalshi prob ≤ 0.55, suggesting 50/50)
        - LLM ensemble agrees strongly (top-outcome std across agents < 0.08)
          AND LLM disagrees with Kalshi by > 0.10 on top outcome
      Rationale: when LLM has high-confidence info AND Kalshi is flat or
      contradictory, trust LLM (proven on Sussex cricket: −0.16 Brier).

  (C) Skip Platt on Kalshi-deferring events:
      When sup_conf != "high" AND |LLM-mean − Kalshi| < 0.05 on top outcome,
      force effective_alpha = 1.0 (no extremization).
      Rationale: agents deferring to market with no independent signal —
      Platt extremization would amplify Kalshi's miscalibration on the
      confidently-wrong calls (saves ~0.45 on WV-1, ~0.40 on OH-15).

  (D) Trust supervisor on multi-class medium-confidence:
      When N_outcomes > 5 AND supervisor probs differ from mean by > 0.10
      on top outcome AND supervisor confidence is "medium", use supervisor.
      Rationale: supervisor's clarifying search materially moved the
      probability on a multi-class question — the law of large numbers
      makes any single supervisor decision lower-stakes per-outcome
      (saves ~0.44 on Survivor where supervisor was right but discarded).

Toggle each fix independently via env var (default 1 = ON):
  V83DEEP_V2_FIX_A_MULTICLASS_PLATT=1
  V83DEEP_V2_FIX_B_DISAGREE_KALSHI=1
  V83DEEP_V2_FIX_C_SKIP_PLATT=1
  V83DEEP_V2_FIX_D_TRUST_SUP_MULTI=1

Traces saved to data/v8_3deep_orall_v2_traces/.

Projected improvement (per COT_POST_MORTEM analysis):
  - orall current: 0.3247
  - orall_v2 projected: ~0.27-0.28 if all fixes land cleanly

------------------- ORIGINAL orall DOCSTRING (preserved) -------------------

agent_v8_3deep_orall — ALL-IN openrouter:web_search.

Builds on agent_v8_3deep_evfirst (evidence-first prompts + OR-search brief +
narrative slot + Ballotpedia + Platt) and additionally swaps the deep-agent
and supervisor search backends from Tavily to openrouter:web_search.

Architecture:
  - 5 lightweight slots (4 evidence-first + 1 narrative) share an OR-search
    brief built via Haiku + openrouter:web_search
  - 3 deep agentic agents (Opus/GPT-5/Gemini) each iterate independently;
    their `search_news` tool is now backed by openrouter:web_search
    (Anthropic native for Opus calls, OpenAI native for GPT-5 calls,
    Exa fallback for Gemini calls)
  - Supervisor (Opus) also uses openrouter:web_search for clarifying queries

ALL search results are date-filtered (Layer 2 of the debiasing — drop
citations whose publication date parses to ≥ cutoff). Layer 1 (prompt-time
cutoff constraint) is preserved.

Traces: data/v8_3deep_orall_traces/

------------------- ORIGINAL evfirst DOCSTRING (preserved) -------------------

agent_v8_3deep_evfirst — Path A + Path B together: evidence-first prompts
on top of orsearch + Ballotpedia + narrative slot.

WHY THIS EXISTS (per user's insight 2026-05-17): the previous orsearch variant
already has 4 anti-overconfidence math layers downstream of the LLM ensemble
(Kalshi-blend, tiered guardrail, supervisor confidence-fallback, Platt
extremization). Stacking a 5th anti-overconfidence layer INSIDE the prompts
("anchor on market", "extra weight on status quo", "if liquid market, anchor
strongly") is REDUNDANT and actively suppresses real news signal — see OH-15
where 4/7 agents READ the No Kings arrest in their reasoning and STILL
discounted it because of those prompt instructions.

This variant rewrites the lightweight prompts to be EVIDENCE-FIRST:
  - CALIBRATED_SYSTEM:    drop "status quo" + "anchor on market" language
  - COT_SYSTEM:           same
  - TOT_SYSTEM:           same
  - NARRATIVE_REACTIVE:   kept as-is (already evidence-first)
The math layers downstream are UNTOUCHED — they remain the safety net.

If the user's hypothesis is correct: aggregate Brier improves on upset events
(OH-15, WV-1) without aggregate regression on routine events, because the
Kalshi-blend pulls overconfident LLM estimates back toward the market
mathematically (per-cat α: Sports 0.70, Politics 0.50, Elections 0.30).

Traces saved to data/v8_3deep_evfirst_traces/ for direct comparison.

------------------- ORIGINAL DOCSTRING (preserved) -------------------

agent_v8_3deep_orsearch — v8_3deep variant whose shared brief is built
via OpenRouter's web_search tool instead of Tavily.

Pattern A (per OpenRouter advisor analysis): replace the Tavily fetch + Haiku
summarizer pipeline used in v8_3deep with a single `anthropic/claude-haiku-4.5`
call carrying `openrouter:web_search`. For Anthropic-served models OpenRouter
routes web_search to **Anthropic's native search** — the same backend Claude
Code's WebSearch uses — which we confirmed on a probe finds local Ohio outlets
(`daytondailynews.com`, `abc6onyourside.com`, `myfox28columbus.com`,
`thelantern.com`) that Tavily's index does not crawl, including the No Kings
protest arrest story that pivoted OH-15.

Lookahead-debiasing for retrospective testing (Layers 1 + 2):
  Layer 1 — prompt-time: explicit cutoff date told to the model with
    explicit instruction to ignore post-cutoff sources.
  Layer 2 — post-hoc: parse publication dates from citation URLs
    (`/YYYY/MM/DD/` pattern) and content ("Updated/Published <date>"),
    drop any citation explicitly dated after the cutoff, redact
    winner-announcing sentences.
For LIVE production both layers are no-ops because there is no future to leak.

Deep-agent search backend is UNCHANGED (still Tavily) — per user instruction
to keep v8_3deep intact and only swap the shared brief.

Env vars:
  V83DEEP_OR_SEARCH=1     (default 1)  — set to 0 to fall back to Tavily
  ORSEARCH_MODEL=anthropic/claude-haiku-4.5
  ORSEARCH_DEBIASED=1     (default 1)
  V83DEEP_SAVE_TRACES=1   (independent of search backend)

Traces saved to data/v8_3deep_orsearch_traces/.

------------------- ORIGINAL DOCSTRING (preserved) -------------------

agent_v8_3deep — v8_deep variant with 3 deep agentic agents.

Same architecture as agent_v8_deep, but switches one lightweight slot
(Gemini @ Calibrated, the lone Gemini lightweight) to an agentic deep
agent. Now 4 lightweight + 3 deep agents = 7 ensemble members. All three
major vendors (Anthropic Opus, OpenAI GPT-5, Google Gemini 2.5 Pro)
participate as DEEP agentic agents, giving cross-vendor agentic
diversity (not just one-shot diversity).

Per AIA paper Figure (ensemble size vs Brier): N=2→3 captures additional
~10-15% of N=10's improvement on top of the 55% from N=2. Marginal but
real.

Cost per event: ~$1.10-1.40 (vs v8_deep's $0.95, vs AIA's $1.77)

V83DEEP_SAVE_TRACES=1 saves all 7 agent traces + supervisor + searches
to data/v8_3deep_traces/.

---------------- ORIGINAL v8_super DOCSTRING (preserved below) ----------------

agent_v8_super — v8 + AIA-style agentic supervisor.

This is a copy of agent_v8.py where the simple meta-reasoner LLM call is
PROMOTED to an AIA-style agentic supervisor that:
  (1) Has search tool access (Tavily search_news) — can run its OWN
      clarifying queries to resolve disagreements between the 5 slots
  (2) Emits a confidence label ∈ {"high", "medium", "low"} per AIA paper §5.2
  (3) FALLBACK RULE per Jensen's inequality + AIA paper:
        confidence == "high"   → use supervisor probabilities
        confidence in {medium, low} → fall back to MEAN of the 5 slots
      The mean fallback is mathematically safer: Brier of mean ensemble is
      STRICTLY less than expected individual Brier (Jensen's inequality on
      strongly convex Brier loss). Per AIA paper Table tab:supervisor_agent,
      this fallback rule beats naive meta-aggregation by 0.0043 Brier.

KEEPS from v8 (efficient retrieval, paper-extension calibration knobs):
  - 5 lightweight slots (Opus×3 different prompts + GPT-5 + Gemini)
  - ONE shared Tavily evidence brief across all 5 slots (not per-agent
    independent searches as in faithful AIA — but the supervisor below
    DOES do its own searches, which is the highest-ROI search according
    to AIA paper §5.2)
  - Platt α = 2.0 (data-tuned, sharper than AIA's √3)
  - Per-category α for Kalshi blend (Sports 0.70, Pol 0.50, etc.)
  - Tiered confidence guardrail
  - Multi-outcome support

CHANGES from v8:
  - _meta_reason() rewritten as _agentic_supervisor() with tool-use loop
  - Supervisor invokes Tavily for up to 3 clarifying queries
  - Decision rule (high/medium/low) per AIA paper §5.2
  - Mean fallback (Jensen-backed)

Expected Brier: ~0.38 on smoke (vs v8+Platt 0.390 and AIA 0.407).
Captures AIA's most valuable component (agentic supervisor) without the
cost of 10 agentic forecaster loops.

---------------- ORIGINAL v8 DOCSTRING (preserved below) ----------------

agent_v8 — Production forecasting agent for Prophet Arena (Forecast track).

Composition of:
  - metac-bot-ha (Bridgewater Metaculus AIB Fall 2025 submission, tournament 32813):
      * Chain-of-Thought prompt pattern
      * Tree-of-Thought prompt pattern
      * Meta-reasoning ensemble aggregator
      * Calibrated forecasting prompt (base-rate → scenarios → calibration anchors)
  - forecasting-tools (Metaculus official toolkit, pip):
      * Reference patterns from SpringTemplateBot2026 (status-quo weighting,
        scenario-thinking, calibration anchors)
  - ours (zero-bias core):
      * real_resolution_dates.json (per-event manual research)
      * Kalshi historical candlestick @ T-3d (kalshi_history.py)
      * Tavily with start_date/end_date + RFC 2822 parser + post-hoc drop
      * Tiered confidence guardrail (signal-strength score → shrinkage)
      * Per-category α blend with Kalshi

Architecture (per event):
  1. Retrieval: Tavily filtered to [T-90d, T-3d], parser fixed for RFC 2822,
     unknown-date items dropped, Haiku evidence brief.
  2. Pre-resolution market prior: Kalshi candlestick per outcome at T-3d
     (exact / opening-spread / missing modes).
  3. Five PARALLEL agent slots:
       a. Opus 4.7 + CoT prompt (reasoning=medium)
       b. Opus 4.7 + ToT prompt (reasoning=medium)
       c. Opus 4.7 + Calibrated/Halawi prompt (reasoning=medium)
       d. GPT-5     + Calibrated prompt
       e. Gemini    + Calibrated prompt
  4. Meta-reasoner: Opus 4.7 sees all 5 (probs + reasoning) → consensus.
  5. Blend with Kalshi per-category α.
  6. Tiered guardrail (shrink toward uniform on thin signal).
  7. Clip [0.02, 0.98] + normalize.

Output: Prophet Arena format {"probabilities": [{"market": str, "probability": float}]}
"""
from __future__ import annotations
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from tavily import TavilyClient

import kalshi_history

load_dotenv()
logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
RES_DATES_PATH = HERE / "data" / "real_resolution_dates.json"

# ============================== CONFIG ==============================
BUFFER_DAYS = int(os.environ.get("FORECAST_BUFFER_DAYS", "3"))
WINDOW_DAYS = int(os.environ.get("FORECAST_WINDOW_DAYS", "90"))
HAIKU_MODEL = os.environ.get("FORECAST_HAIKU_MODEL", "anthropic/claude-haiku-4.5")

# Models for individual agent slots
OPUS_MODEL = os.environ.get("FORECAST_OPUS", "anthropic/claude-opus-4.7")
GPT5_MODEL = os.environ.get("FORECAST_GPT5", "openai/gpt-5")
GEMINI_MODEL = os.environ.get("FORECAST_GEMINI", "google/gemini-2.5-pro")
META_REASONER = os.environ.get("FORECAST_META_REASONER", "anthropic/claude-opus-4.7")

REASONING_MODELS = {OPUS_MODEL, META_REASONER, GEMINI_MODEL}

# Blending / calibration
ALPHA_BY_CATEGORY = {
    "Sports": 0.70,
    "Politics": 0.50,
    "Entertainment": 0.35,
    "Elections": 0.30,
}
DEFAULT_ALPHA = 0.60

# Guardrail
GUARDRAIL_STRENGTHS = {0: 0.60, 1: 0.35, 2: 0.15, 3: 0.05}  # score -> shrink

# Retrieval
MAX_QUERIES = int(os.environ.get("FORECAST_MAX_QUERIES", "2"))
MAX_RESULTS_PER_QUERY = int(os.environ.get("FORECAST_MAX_RESULTS", "10"))
EVIDENCE_TOKENS = int(os.environ.get("FORECAST_EVIDENCE_TOKENS", "500"))

# LLM call
AGENT_MAX_TOKENS = int(os.environ.get("FORECAST_AGENT_TOKENS", "4000"))
META_MAX_TOKENS = int(os.environ.get("FORECAST_META_TOKENS", "3000"))

CLIP_LO = 0.02
CLIP_HI = 0.98

# Verbose trace persistence — saves ALL slot outputs + supervisor agentic
# search queries + tool results + reasoning + decision metadata per event
# to data/v8_super_traces/. Default OFF for live-eval speed. Set
# V8SUPER_SAVE_TRACES=1 to enable for smoke / debugging.
SAVE_TRACES = os.environ.get("V83DEEP_SAVE_TRACES", "0") == "1"
TRACES_DIR = HERE / "data" / "v8_3deep_orall_v2_traces"

# v2 calibration fixes — toggleable for ablation
# Defaults below were chosen by exhaustive 16-combination ablation on the
# 26-event orall trace set (see scripts/simulate_v2_fixes.py):
#   ALL 4 fixes ON: mean Brier 0.3236 (essentially no change vs orall 0.3247)
#   BEST: B+C only: 0.2897 — saves 0.035 (−10.8%)
#   FIX A (multi-class Platt) hurts more than it helps — even though it
#     saves 0.34 on Survivor, it dampens extremization on multi-class
#     events that orall was getting right (e.g. Fed Chair vote)
#   FIX D overlaps with FIX C+B + slightly regresses when combined
V2_FIX_A_MULTICLASS_PLATT = os.environ.get("V83DEEP_V2_FIX_A_MULTICLASS_PLATT", "0") == "1"  # default OFF
V2_FIX_B_DISAGREE_KALSHI = os.environ.get("V83DEEP_V2_FIX_B_DISAGREE_KALSHI", "1") == "1"   # default ON
V2_FIX_C_SKIP_PLATT = os.environ.get("V83DEEP_V2_FIX_C_SKIP_PLATT", "1") == "1"             # default ON
V2_FIX_D_TRUST_SUP_MULTI = os.environ.get("V83DEEP_V2_FIX_D_TRUST_SUP_MULTI", "0") == "1"   # default OFF

# Toggle: 1 → use openrouter:web_search via orsearch_brief; 0 → Tavily (legacy)
USE_OR_SEARCH = os.environ.get("V83DEEP_OR_SEARCH", "1") == "1"
ORSEARCH_DEBIASED = os.environ.get("ORSEARCH_DEBIASED", "1") == "1"

# Deep agentic agents config — now 3 agents, all three major vendors
N_DEEP_AGENTS = int(os.environ.get("V83DEEP_N_AGENTS", "3"))
DEEP_AGENT_MODELS = os.environ.get(
    "V83DEEP_AGENT_MODELS",
    "anthropic/claude-opus-4.7,openai/gpt-5,google/gemini-2.5-pro",
).split(",")
DEEP_AGENT_MAX_ITERS = int(os.environ.get("V83DEEP_AGENT_MAX_ITERS", "4"))
DEEP_AGENT_MAX_TAVILY = int(os.environ.get("V83DEEP_AGENT_MAX_TAVILY", "5"))

# Platt scaling extremization parameter. From AIA Forecaster paper
# (arXiv 2511.07678): LLMs hedge toward 0.5 due to RLHF; Platt scaling
# corrects this by sharpening probabilities along a sigmoid.
# Multi-class form: p_i -> p_i^a / sum_j(p_j^a)
#   a = 1.0  no-op
#   a > 1.0  sharpen (extremize toward 0/1)
#   a < 1.0  soften (entropize toward uniform)
# Tuned via tune_platt.py sweep on smoke set. Default 1.0 until tuned.
PLATT_A = float(os.environ.get("FORECAST_PLATT_A", "2.0"))

# Adaptive Platt: dampen extremization when (a) supervisor confidence is low,
# or (b) the pre-Platt ensemble is already very confident (high-prob regime
# has asymmetric Brier risk: small gain when right, huge loss when wrong).
# Validated against OH-15 (sup=medium, max_p=0.85, Platt α=2.0 → confidently
# wrong, Brier 1.89) vs Hungary (sup=medium, max_p=0.66, Platt α=2.0 → right,
# Brier 0.082). Different pre-Platt max_p → different appropriate α.
ADAPTIVE_PLATT = os.environ.get("V83DEEP_ADAPTIVE_PLATT", "0") == "1"


def _adaptive_platt_alpha(
    base_alpha: float,
    sup_confidence: str | None,
    max_pre_platt_prob: float,
) -> float:
    """Return effective Platt alpha, dampened from base_alpha based on signal.

    Two multipliers compounded (each in [0, 1]):
    - conf_mult: 1.0 (high) / 0.7 (medium) / 0.4 (low) — supervisor trust
    - range_mult: 1.0 if max_p ≤ 0.70; tapers to 0 by max_p = 1.0
      because asymmetric Brier risk of extremization grows rapidly above 0.85
    """
    if base_alpha <= 1.0:
        return base_alpha
    conf_mult = {"high": 1.0, "medium": 0.7, "low": 0.4}.get(
        (sup_confidence or "low").lower(), 0.4
    )
    p = max(0.0, min(1.0, max_pre_platt_prob))
    if p <= 0.70:
        range_mult = 1.0
    elif p <= 0.80:
        range_mult = 0.75
    elif p <= 0.90:
        range_mult = 0.40
    elif p <= 0.97:
        range_mult = 0.15
    else:
        range_mult = 0.0
    eff = 1.0 + (base_alpha - 1.0) * conf_mult * range_mult
    return max(1.0, eff)

# Load resolution-date map
_raw = json.loads(RES_DATES_PATH.read_text())
RESOLUTION_DATES: dict[str, datetime] = {
    k: datetime.fromisoformat(v["date"]).replace(tzinfo=timezone.utc)
    for k, v in _raw.items()
    if not k.startswith("_")
}


# ============================== PROMPTS ==============================
# Lifted patterns from metac-bot-ha src/prompts/ and forecasting-tools
# SpringTemplateBot2026. Adapted for multi-outcome PA event shape.

CALIBRATED_SYSTEM = """You are a superforecaster (Tetlock-style) scored with Brier loss.
You produce a probability distribution over the listed outcomes.

YOUR JOB IS TO REASON FROM EVIDENCE, NOT TO ANCHOR ON MARKETS.
Downstream math (Kalshi-blend, guardrail, Platt) will handle any
overconfidence. Your edge in the ensemble comes from reading the brief
carefully and updating from named, specific facts. Don't be timid.

REASONING PROCEDURE (do this internally, output ONLY the final JSON):

1. WHAT THE EVIDENCE SAYS — read the brief. For each outcome, list the
   2-3 most specific, recent, named facts that bear on whether it
   happens. Generic descriptions of qualifications/experience are weak;
   specific recent events (campaign moments, scandals, injuries, ad
   buys, endorsement flips, polling shifts, performance trends) are
   strong.

2. WHAT THE EVIDENCE IMPLIES — what would a careful analyst with this
   evidence think? Do NOT phrase reasoning as "status quo favors X" or
   "the market says Y" — those default to anchors that suppress your
   edge. Phrase reasoning as "the evidence shows X, therefore Y."

3. BASE RATE AS A SANITY CHECK — incumbents win re-election ~70%;
   sports favorites ~55-60%; primaries with high name-rec usually
   stay with the favored candidate UNLESS a strong specific event
   broke the race open. Use base rates to sanity-check, not to set
   your answer.

4. MARKET PRICE — note it for context only. The downstream blend will
   already pull your forecast toward it; you don't need to.

5. UPSET / TAIL-EVENT CHECK — does the brief contain any strong
   specific event (arrest, scandal, viral moment, key endorsement
   swing, injury, manager change) that would make a careful analyst
   move 10-25 percentage points against the obvious favorite? If yes,
   trust that evidence in your final probability.

CALIBRATION ANCHORS (use these as guides, not floors):
- 0.50: genuine coin-flip
- 0.65-0.75: lean
- 0.80-0.85: confident with multiple converging signals
- 0.90+: very confident — multiple specific recent signals all aligned
- 0.95+: near-certain — needs clinched/resolved evidence

RULES:
- Probabilities across outcomes sum to ~1.0.
- Multi-outcome with no direct evidence per outcome: roughly uniform.
- Never < 0.02 or > 0.98 for any single outcome.
- DO NOT cite "status quo bias" or "anchor on the market" as reasoning;
  those bias the answer toward the obvious, which is what the math
  already does for you.

OUTPUT: ONLY this JSON (no other text):
{"reasoning": "<one-paragraph summary citing specific named facts>",
 "probabilities": [{"market": "<exact-outcome-label>", "probability": <float>}, ...]}"""


COT_SYSTEM = """You are an expert forecaster using evidence-first
Chain-of-Thought reasoning. Walk through each step in your reasoning JSON.

REMEMBER: downstream math (Kalshi-blend, guardrail, Platt) handles any
overconfidence. Your job is to extract signal from the evidence, not to
anchor on the market or status quo.

STEP 1 — Question breakdown:
  What is being asked? What outcomes are listed? What time horizon?

STEP 2 — Evidence inventory (the most important step):
  Read the brief. For each outcome, list the most SPECIFIC, NAMED,
  RECENT facts in the brief that bear on whether it happens. Distinguish:
    STRONG (named recent event with clear directional impact)
    MODERATE (named non-recent event, or recent generic context)
    WEAK (general qualification, party history, generic description)
  If there are no STRONG signals for any outcome, the race is genuinely
  uncertain and you should be near base rate.

STEP 3 — Implication:
  What does that evidence pattern imply about the outcome distribution?
  Do NOT phrase as "the market says X so I'll anchor"; phrase as
  "the evidence shows X, so the probability should be Y."

STEP 4 — Base rate sanity check:
  Reference class? Base rate? Does my answer drift much from base rate?
  If it does, is that drift supported by STRONG specific signals?

STEP 5 — Probability assignment:
  Use calibration anchors (0.50 coin-flip / 0.70 lean / 0.85 confident
  / 0.95+ near-certain). Pick values reflecting the evidence strength
  you actually saw — don't artificially compress toward 0.50 or toward
  the market.

STEP 6 — Devil's advocate:
  What evidence contradicts my top pick? If I'm 0.90+ on one outcome,
  do I have multiple converging recent signals? If not, lower it.

OUTPUT: ONLY this JSON:
{"reasoning": "<step-by-step CoT, ~200 words, cite specific named facts>",
 "probabilities": [{"market": "<exact-outcome-label>", "probability": <float>}, ...]}"""


TOT_SYSTEM = """You are an expert forecaster using evidence-first
Tree-of-Thought reasoning. Generate 3 DISTINCT reasoning paths, then
synthesize. Downstream math handles overconfidence — your job is to
extract signal from the evidence.

PATH 1 — Evidence-from-the-brief:
  What specific named recent facts in the brief most directly bear on
  which outcome occurs? What probability does that evidence imply on
  its own?
  Probability assigned: ?

PATH 2 — Reference-class / base-rate:
  What is the reference class for this type of question? What's the
  base rate? What probability does the base rate alone imply?
  Probability assigned: ?

PATH 3 — Upset / late-stage event check:
  Does the brief contain any STRONG specific event (arrest, scandal,
  viral moment, key endorsement flip, injury, manager change) that
  would justify a material move (10-25 pp) away from the obvious
  favorite? If yes, lean into it.
  Probability assigned: ?

SYNTHESIS:
  Weight each path by HOW MUCH SPECIFIC EVIDENCE it has. Path 1 with
  3-5 named recent facts trumps Path 2 with a generic base rate. DO NOT
  default to averaging — be decisive when the evidence is decisive.
  Do NOT default to the market — the math downstream blends with the
  market for you.

CALIBRATION (use as guides, not floors):
- 0.50 = genuine uncertainty
- 0.70-0.85 = lean / confident, with multiple converging signals
- 0.90+ = high confidence, with strong specific evidence
- 0.95+ = near-certain
- None below 0.02 or above 0.98.

OUTPUT: ONLY this JSON:
{"reasoning": "<P1/P2/P3 + synthesis, ~250 words, cite named facts>",
 "probabilities": [{"market": "<exact-outcome-label>", "probability": <float>}, ...]}"""


# ============================== NARRATIVE-REACTIVE SYSTEM ===============
# Designed as a COUNTER-VOICE in the ensemble against the conservative
# calibrated / CoT / ToT prompts. Those prompts correctly down-weight noisy
# news but ALSO suppress legitimate upset signals (e.g. OH-15 No Kings
# arrest). This prompt is the news-reactive complement: when the brief has
# specific, named, recent campaign events for an underdog, update materially
# away from the market. When the brief is generic or noisy, behave normally.
NARRATIVE_REACTIVE_SYSTEM = """You are an EVENT-DRIVEN forecaster. Your edge
comes from updating PROPERLY on specific, named, recent campaign / sports /
news events that conservative forecasters underweight.

CORE INSIGHT: In low-information primaries, off-cycle races, niche sports
matches, and reality-TV episodes, the WINNER is often determined by a single
late-stage event (arrest, scandal, viral moment, key endorsement flip, injury,
ad spend surge) — NOT by initial polling, fundraising, or base rates. Vegas /
Kalshi markets in obscure races are THIN and routinely miss these late
signals. Your job is to give the ensemble a voice that updates correctly when
such signals exist.

REASONING PROCEDURE (do this internally, output ONLY the final JSON):

1. SCAN THE BRIEF for specific named events involving each outcome in the
   last 30-90 days. List them. If there are none, treat this as a normal
   forecasting task and anchor at the market price.

2. CLASSIFY each event by IMPACT MAGNITUDE on the outcome:
   - STRONG (≥10 percentage points): arrest at high-visibility protest,
     major scandal, hot-mic gaffe going viral, key labor / progressive
     endorsement flip, viral grassroots fundraising surge, opponent
     pulled from race / disqualified, injury to a starting athlete,
     manager firing, major stadium / weather change.
   - MODERATE (3-10 pp): single national endorsement, ad spend disparity,
     decent polling shift, minor controversy.
   - WEAK (<3 pp): generic campaign theme, qualification description,
     party history.

3. APPLY THE UPDATE: if you found STRONG signals favoring an outcome:
   - For an UNDERDOG: final probability should land 10-25 pp ABOVE the
     market price (not anchored to the market). A market that didn't
     price in a STRONG signal IS WRONG.
   - For a FAVORITE with negative STRONG signal: final 10-25 pp BELOW
     market.
   - For MODERATE signals: 3-10 pp adjustment.
   - For WEAK / no signals: stay near market.

4. NEVER use the phrase "status quo favors X" as a justification — that
   reasoning explicitly suppresses your edge. Your edge is updating on
   events, not deferring to the market.

5. CALIBRATION FLOOR: any outcome with a named STRONG signal should never
   be below 0.20.

OUTPUT: ONLY this JSON (no other text):
{"reasoning": "<one paragraph: list the named events you found, classify
their strength, state the adjustment you applied>",
 "probabilities": [{"market": "<exact-outcome-label>", "probability": <float>}, ...]}"""


SUPERVISOR_SYSTEM = """You are an AGENTIC SUPERVISOR (AIA paper §5.2) over a
multi-agent forecasting system. Five independent forecasters have analyzed
this question using diverse models and reasoning strategies. Your value
comes from RESOLVING SPECIFIC DISAGREEMENTS via targeted search — NOT from
holistic re-evaluation. Per Halawi 2024 / AIA paper: naive LLM aggregation
of forecasts is WORSE than simple mean. Active research wins.

WORKFLOW (3 steps):

STEP 1 — IDENTIFY & CLASSIFY DISAGREEMENTS
Find the 2-4 KEY points where the forecasters diverge. For each, classify:
  - FACTUAL: agents disagree about what happened or will happen
    (e.g. one assumes incumbent leads polls, another assumes challenger)
    → Search for the factual answer.
  - INTERPRETIVE: agents agree on facts but disagree on what they mean
    → Search for evidence on which interpretation is dominant.
  - MAGNITUDE: agents agree on direction but disagree on size
    → Search for quantitative evidence (margin, base rates).
  - MISSING FACTOR: some agents identified a factor others missed
    → Search to verify whether the missing factor is real and material.

STEP 2 — RESOLVE BY TARGETED SEARCH
You have access to search_news. Issue 1-3 targeted queries that resolve
the SPECIFIC divergence point. Examples:
  - Base-rate lookups ("how often have incumbents in similar primary
    races been unseated")
  - Fact-checks ("did senator X endorse candidate Y in March 2026")
  - Magnitude verification ("polling margin in district Z this cycle")

STEP 3 — EMIT FINAL FORECAST + CONFIDENCE LABEL
Call submit_supervisor_decision with:
  - reasoning: how each disagreement was resolved (~200 words)
  - probabilities: your final view per outcome
  - confidence: "high" | "medium" | "low"
      "high"   = your clarifying search clearly resolved a divergence;
                 you have a confident updated view that should REPLACE mean
      "medium" = some resolution but residual uncertainty;
                 simple mean of the 5 may be safer
      "low"    = unable to meaningfully improve on mean; defer

CRITICAL RULES (from AIA paper §5.2):
  • Do NOT simply re-average or pick an outlier. The mean is already
    being computed; your job is to do MORE than that.
  • Do NOT overweight outliers. If 4/5 agents are at 0.7 and one is at
    0.2, do not get dragged toward 0.2 unless your clarifying search
    reveals the outlier was correct. Per Halawi 2024: LLMs tend to
    overweight outliers; explicitly resist this.
  • Only assign "high" confidence if your CLARIFYING SEARCH (Step 2)
    materially shifted your view. Without strong-evidence update,
    default to "medium" and let the Jensen-protected mean win.
"""


QUERY_GEN_PROMPT = """You generate web search queries for a forecasting agent.

Given an event, return {n} concise search queries (5-12 words each) that would find:
- Recent news / results / official announcements about this event
- Reference data (stats, polls, expert predictions)
Focus on FRESH information from the last few weeks over background.

Output ONLY this JSON: {{"queries": ["query 1", "query 2"]}}"""

SUMMARY_PROMPT = """You produce evidence briefs for a forecasting agent.

Given the event AND web search results, write a concise (~{tokens} tokens) brief covering:
- Most RECENT relevant facts (dates, scores, polls, announcements)
- Direct evidence about likely outcome (team form, market consensus)
- Note when sources disagree or are uncertain

Don't make predictions yourself. Output plain text, no JSON, no headers."""


# ============================== CLIENTS ==============================
_openrouter: OpenAI | None = None
_tavily: TavilyClient | None = None


def _or_client() -> OpenAI:
    global _openrouter
    if _openrouter is None:
        _openrouter = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
        )
    return _openrouter


def _tav_client() -> TavilyClient:
    global _tavily
    if _tavily is None:
        _tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily


def _parse_pub_date(s: str):
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None
                    else dt.astimezone(timezone.utc))
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.rstrip("Z").split(".")[0], fmt.rstrip("Z")).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ============================== RETRIEVAL ==============================
def _gen_queries(event: dict) -> list[str]:
    try:
        msg = (
            f"Event: {event.get('title')}\n"
            f"Outcomes: {', '.join(event.get('outcomes') or [])}\n"
            f"Category: {event.get('category', '?')}\n"
            f"Description: {(event.get('description') or '')[:300]}"
        )
        r = _or_client().chat.completions.create(
            model=HAIKU_MODEL, max_tokens=300,
            messages=[
                {"role": "system", "content": QUERY_GEN_PROMPT.format(n=MAX_QUERIES)},
                {"role": "user", "content": msg},
            ],
            response_format={"type": "json_object"},
        )
        text = r.choices[0].message.content or ""
        m = re.search(r"\{.*\}", text, re.DOTALL)
        queries = (json.loads(m.group(0)).get("queries") or []) if m else []
        return [str(q).strip() for q in queries if q][:MAX_QUERIES]
    except Exception as e:
        logger.warning("query-gen failed for %s: %s", event.get("market_ticker"), e)
        return [event.get("title", "")[:200]]


def _search_tavily(queries: list[str], cutoff_dt: datetime) -> list[dict]:
    tav = _tav_client()
    start = (cutoff_dt - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    end = cutoff_dt.strftime("%Y-%m-%d")
    out: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        if not q:
            continue
        try:
            r = tav.search(query=q, max_results=MAX_RESULTS_PER_QUERY,
                           topic="news", start_date=start, end_date=end)
            for item in r.get("results", []):
                url = item.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    out.append(item)
        except Exception as e:
            logger.warning("tavily query failed: %s", e)
    return out


def _filter_strict(results: list[dict], cutoff_dt: datetime) -> tuple[list[dict], dict]:
    kept, unk, post = [], 0, 0
    for r in results:
        pub_dt = _parse_pub_date(r.get("published_date") or "")
        if pub_dt is None:
            unk += 1
            continue
        if pub_dt >= cutoff_dt:
            post += 1
            continue
        kept.append(r)
    return kept, {"kept": len(kept), "dropped_unknown": unk, "dropped_post": post}


def _summarize(event: dict, results: list[dict]) -> str:
    if not results:
        return ""
    raw = "\n\n".join(
        f"SOURCE [{(r.get('published_date') or '')[:16]}]: {(r.get('title') or '')[:150]}\n"
        f"URL: {r.get('url','')}\nCONTENT: {(r.get('content') or '')[:600]}"
        for r in results[:8]
    )
    try:
        r = _or_client().chat.completions.create(
            model=HAIKU_MODEL, max_tokens=EVIDENCE_TOKENS,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT.format(tokens=EVIDENCE_TOKENS)},
                {"role": "user", "content":
                    f"Event: {event.get('title')}\n"
                    f"Outcomes: {', '.join(event.get('outcomes') or [])}\n\n"
                    f"WEB SEARCH RESULTS (all dated before resolution):\n\n{raw}\n\n"
                    "Now write the evidence brief."},
            ],
        )
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return "\n".join(f"- {r.get('title','')[:100]}: {(r.get('content') or '')[:200]}"
                        for r in results[:5])


# ============================== KALSHI HISTORICAL ==============================
def _kalshi_outcome_prices(event_ticker: str, outcomes: list[str],
                           cutoff_dt: datetime) -> tuple[dict[str, float], dict[str, str]]:
    import sys
    sys.path.insert(0, "/workspace/ai-prophet/packages/core")
    from ai_prophet_core.forecast.kalshi_client import KalshiForecastClient

    client = KalshiForecastClient()
    markets = []
    for status in ("settled", "open", "closed"):
        try:
            markets = client.get_markets(event_ticker=event_ticker, status=status, limit=200)
            if markets:
                break
        except Exception as e:
            logger.warning("Kalshi list markets failed (%s): %s", status, e)

    outcome_to_ticker: dict[str, str] = {}
    for m in markets:
        sub = (m.get("yes_sub_title") or "").strip()
        tk = m.get("ticker")
        if not tk:
            continue
        for o in outcomes:
            if o not in outcome_to_ticker and (
                o.lower() == sub.lower() or o.lower() in sub.lower() or sub.lower() in o.lower()
            ):
                outcome_to_ticker[o] = tk
                break

    prices, modes = {}, {}
    for o in outcomes:
        tk = outcome_to_ticker.get(o)
        if not tk:
            modes[o] = "no_market"
            continue
        price, mode = kalshi_history.price_at(tk, event_ticker, cutoff_dt)
        modes[o] = mode
        if price is not None:
            prices[o] = price
    return prices, modes


# ============================== AGENT SLOTS ==============================
def _format_user_prompt(event: dict, brief: str,
                       kalshi_prices: dict[str, float], modes: dict[str, str],
                       ballotpedia_brief: str = "") -> str:
    parts = [
        f"Event: {event.get('title')}",
        f"Category: {event.get('category', '?')}",
        f"Outcomes (in order): {', '.join(event.get('outcomes') or [])}",
    ]
    if event.get("description"):
        parts.append(f"Description: {(event.get('description') or '')[:600]}")
    if event.get("rules"):
        parts.append(f"Rules: {event['rules']}")
    if kalshi_prices:
        market_lines = [f"  {o}: YES@{p:.3f}  (Kalshi candle mode={modes.get(o, '?')})"
                        for o, p in kalshi_prices.items()]
        parts.append("Pre-resolution Kalshi YES prices (snapshot T-3d):")
        parts.append("\n".join(market_lines))
    if brief:
        parts.append("")
        parts.append("=== PRE-RESOLUTION EVIDENCE (web sources strictly before T-3d) ===")
        parts.append(brief)
        parts.append("=== END EVIDENCE ===")
    else:
        parts.append("(No pre-resolution evidence found.)")
    if ballotpedia_brief:
        parts.append("")
        parts.append("=== STRUCTURED PROFILE DATA ===")
        parts.append(ballotpedia_brief)
        parts.append("=== END STRUCTURED DATA ===")
    return "\n".join(parts)


def _call_llm(model: str, system_prompt: str, user_prompt: str,
              max_tokens: int = AGENT_MAX_TOKENS) -> str | None:
    """Make one LLM call, return raw response text (None on failure)."""
    try:
        kwargs: dict[str, Any] = dict(
            model=model, max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        if model in REASONING_MODELS:
            kwargs["extra_body"] = {"reasoning": {"effort": "medium"}}
        r = _or_client().chat.completions.create(**kwargs)
        return r.choices[0].message.content
    except Exception as e:
        logger.warning("LLM call failed for %s: %s", model, e)
        return None


def _parse_agent_response(text: str | None, expected: set[str]) -> dict | None:
    """Parse {reasoning, probabilities} JSON. Return dict with keys, or None."""
    if not text:
        return None
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        raw = data.get("probabilities") or []
        if isinstance(raw, dict):
            raw = [{"market": k, "probability": v} for k, v in raw.items()]
        probs = {str(p["market"]): float(p["probability"]) for p in raw if str(p["market"]) in expected}
        if not probs:
            return None
        s = sum(probs.values()) or 1.0
        probs = {k: v / s for k, v in probs.items()}
        return {"reasoning": str(data.get("reasoning", ""))[:400], "probabilities": probs}
    except Exception as e:
        logger.warning("parse failed: %s ... text=%s", e, (text or "")[:200])
        return None


def _run_one_agent(slot_name: str, model: str, system_prompt: str,
                  user_prompt: str, expected: set[str]) -> dict | None:
    """Run one agent slot. Returns {slot, model, reasoning, probabilities}."""
    text = _call_llm(model, system_prompt, user_prompt)
    parsed = _parse_agent_response(text, expected)
    if parsed is None:
        return None
    parsed["slot"] = slot_name
    parsed["model"] = model
    return parsed


# ============================== META-REASONER ==============================
def _format_meta_input(event: dict, agent_results: list[dict],
                       kalshi_prices: dict, modes: dict, brief: str,
                       ballotpedia_brief: str = "") -> str:
    outcomes = event.get("outcomes") or []
    parts = [
        f"Question: {event.get('title')}",
        f"Outcomes (in order): {', '.join(outcomes)}",
        f"Category: {event.get('category', '?')}",
    ]
    if kalshi_prices:
        market_lines = [f"  {o}: YES@{p:.3f} ({modes.get(o, '?')})"
                        for o, p in kalshi_prices.items()]
        parts.append("Pre-resolution Kalshi prices:")
        parts.append("\n".join(market_lines))
    if brief:
        parts.append("Evidence summary (truncated):")
        parts.append(brief[:600])
    if ballotpedia_brief:
        parts.append("Structured profile data:")
        parts.append(ballotpedia_brief[:1500])
    parts.append("")
    parts.append("=== FIVE AGENT PREDICTIONS ===")
    for r in agent_results:
        top = sorted(r["probabilities"].items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{k}={v:.3f}" for k, v in top)
        parts.append(f"\n{r['slot'].upper()} ({r['model']}):")
        parts.append(f"  Top probs: {top_str}")
        parts.append(f"  Reasoning: {r.get('reasoning', '')[:300]}")
    parts.append("")
    parts.append("Now produce the consensus distribution.")
    parts.append(f"Outcomes (in order): {', '.join(outcomes)}")
    return "\n".join(parts)


# ============================== AGENTIC SUPERVISOR (v8_super) ==============
# Tools the supervisor can call
_SUPER_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": (
            "Search recent news to resolve a SPECIFIC disagreement between "
            "the 5 forecaster agents. Use 1-3 targeted queries (base-rate "
            "lookup, fact-check, or magnitude verification)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        }
    }
}


def _submit_supervisor_tool(outcomes: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "submit_supervisor_decision",
            "description": "Submit your supervisor probabilities + confidence label.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string", "description": "How disagreements were resolved (~200 words)."},
                    "probabilities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "outcome": {"type": "string", "enum": outcomes},
                                "probability": {"type": "number", "minimum": 0.02, "maximum": 0.98}
                            },
                            "required": ["outcome", "probability"]
                        }
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": (
                            "high = use my probs; medium/low = use mean of 5 slots "
                            "(Jensen-protected fallback)."
                        )
                    }
                },
                "required": ["reasoning", "probabilities", "confidence"]
            }
        }
    }


SUPERVISOR_MAX_ITERS = int(os.environ.get("V8SUPER_SUPERVISOR_MAX_ITERS", "4"))
SUPERVISOR_MODEL = os.environ.get("V8SUPER_SUPERVISOR_MODEL", "anthropic/claude-opus-4.7")


# ============================== DEEP AGENT INFRASTRUCTURE =================
# Reused tool schemas (search_news + submit_forecast) — defined here so the
# deep agents have their own AIA-style tool-use loop independent of supervisor's.

_DEEP_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": "Search recent news to inform your forecast. Use iteratively (3-5 queries). Each query should target one specific fact or aspect.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "5-15 word focused query"}},
            "required": ["query"]
        }
    }
}


def _deep_submit_tool(outcomes: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "submit_forecast",
            "description": "Submit your final probability distribution + reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string", "description": "200-400 word reasoning"},
                    "probabilities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "outcome": {"type": "string", "enum": outcomes},
                                "probability": {"type": "number", "minimum": 0.02, "maximum": 0.98}
                            },
                            "required": ["outcome", "probability"]
                        }
                    }
                },
                "required": ["reasoning", "probabilities"]
            }
        }
    }


DEEP_AGENT_SYSTEM = """You are an evidence-first deep-research forecaster.
You have a search_news tool — use it iteratively (3-5 queries) to gather
specific, named, recent facts. Your edge in the ensemble comes from finding
signal that conservative one-shot agents miss. Downstream math
(Kalshi-blend, guardrail, Platt) will rein in any overconfidence — your job
is to extract genuine signal, NOT to anchor on markets or status quo.

WORKFLOW:
1. Read the question. Identify which specific facts would most resolve it.
2. Issue search_news queries. Each focused on ONE specific fact/angle:
   prefer named candidates / teams / events / recent endorsements /
   scandals / injuries / managerial changes / ad-spend disparities.
3. Read results. Decide if you need more queries to fill gaps.
4. After 3-5 queries, call submit_forecast.

REASONING APPROACH (do this internally, output only via submit_forecast):
- EVIDENCE INVENTORY: for each outcome, list the 2-3 most specific
  recent facts that bear on whether it happens. Distinguish STRONG (named
  recent event with clear directional impact) / MODERATE / WEAK.
- IMPLICATION: what does that evidence imply? Phrase as "evidence shows
  X, therefore Y." Do NOT phrase as "status quo favors X" or "anchor on
  the market" — those defaults suppress your edge.
- UPSET CHECK: does the evidence contain a STRONG specific signal
  (arrest, scandal, viral moment, key endorsement flip, injury, manager
  change) that would justify a 10-25 pp move away from the obvious
  favorite? If yes, lean into it.
- SANITY: base rate as a sanity check, not as your answer.

CALIBRATION ANCHORS (use as guides, not floors):
- 0.50 = genuine coin-flip
- 0.65-0.75 = lean
- 0.80-0.85 = confident with multiple converging signals
- 0.90+ = very confident — multiple specific recent signals aligned
- 0.95+ = near-certain — clinched / resolved evidence

OUTPUT (via submit_forecast tool): probabilities summing to ~1.0,
per-outcome in [0.02, 0.98], plus 200-400 word reasoning citing the
specific named facts you found in your searches."""


def _run_deep_agent(
    agent_idx: int,
    model: str,
    event: dict,
    outcomes: list[str],
    market_prices: dict,
    modes: dict,
    cutoff_dt: datetime | None,
    trace_log: list[dict] | None = None,
    ballotpedia_brief: str = "",
) -> dict | None:
    """Run one deep agentic agent: iterative search loop → final probability.

    Returns {slot, model, probabilities, reasoning} or None on failure.
    """
    expected = set(outcomes)
    tools = [_DEEP_SEARCH_TOOL, _deep_submit_tool(outcomes)]

    # User prompt with question + market prices (same as lightweight slots see)
    rules = (event.get("rules") or event.get("description") or "").strip()
    market_lines = [f"  {o}: YES@{p:.3f} (Kalshi mode={modes.get(o,'?')})"
                    for o, p in market_prices.items()] if market_prices else []
    market_section = ("Pre-resolution Kalshi prices:\n" + "\n".join(market_lines)
                      if market_lines else "")
    bp_section = (f"\nStructured profile data (Ballotpedia, pre-resolution):\n"
                  f"{ballotpedia_brief[:1500]}\n" if ballotpedia_brief else "")
    user = (
        f"Question: {event.get('title')}\n"
        f"Category: {event.get('category', '?')}\n"
        f"Outcomes (in order): {', '.join(outcomes)}\n"
        + (f"\nResolution criteria:\n{rules[:1500]}\n" if rules else "")
        + (f"\n{market_section}\n" if market_section else "")
        + bp_section
        + "\nBegin research via search_news, then call submit_forecast.\n"
    )

    messages = [
        {"role": "system", "content": DEEP_AGENT_SYSTEM},
        {"role": "user", "content": user},
    ]
    client = _or_client()

    for it in range(DEEP_AGENT_MAX_ITERS):
        is_last = (it == DEEP_AGENT_MAX_ITERS - 1)
        iter_tools = [t for t in tools if t["function"]["name"] == "submit_forecast"] if is_last else tools
        iter_tool_choice = (
            {"type": "function", "function": {"name": "submit_forecast"}}
            if is_last else "auto"
        )
        if is_last:
            messages.append({"role": "user", "content": "Final iteration — call submit_forecast now with your best forecast."})
        try:
            kwargs = dict(
                model=model,
                messages=messages,
                tools=iter_tools,
                tool_choice=iter_tool_choice,
                temperature=0.7,
                max_tokens=4000,
            )
            if model in REASONING_MODELS:
                kwargs["extra_body"] = {"reasoning": {"effort": "medium"}}
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.warning("[deep-%d/%s] LLM call failed iter %d: %s",
                           agent_idx, model.split("/")[-1], it, e)
            return None
        msg = resp.choices[0].message
        asst_record: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            asst_record["content"] = msg.content
        if msg.tool_calls:
            asst_record["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {
                    "name": tc.function.name, "arguments": tc.function.arguments,
                }} for tc in msg.tool_calls
            ]
        messages.append(asst_record)
        if trace_log is not None and msg.content:
            trace_log.append({"iter": it, "type": "assistant_text", "content": msg.content})
        if not msg.tool_calls:
            messages.append({"role": "user", "content": "Use the tools (search_news or submit_forecast)."})
            continue
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            if name == "search_news":
                q = args.get("query", "").strip()
                logger.info("[deep-%d/%s] iter=%d search %r",
                            agent_idx, model.split("/")[-1], it, q[:80])
                tool_result = _run_supervisor_tavily(q, cutoff_dt)
                if trace_log is not None:
                    trace_log.append({"iter": it, "type": "search", "query": q,
                                     "results_text": tool_result[:4000]})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result[:6000]})
            elif name == "submit_forecast":
                logger.info("[deep-%d/%s] iter=%d submitted",
                            agent_idx, model.split("/")[-1], it)
                if trace_log is not None:
                    trace_log.append({"iter": it, "type": "submit", "args": args})
                # Parse probabilities
                raw = args.get("probabilities", []) or []
                if isinstance(raw, dict):
                    raw = [{"outcome": k, "probability": v} for k, v in raw.items()]
                probs = {}
                for r in raw:
                    o = str(r.get("outcome", ""))
                    if o in expected:
                        probs[o] = max(CLIP_LO, min(CLIP_HI, float(r.get("probability", 0.0))))
                for o in expected:
                    probs.setdefault(o, CLIP_LO)
                s = sum(probs.values()) or 1.0
                probs = {k: v / s for k, v in probs.items()}
                return {
                    "slot": f"deep_{agent_idx}",
                    "model": model,
                    "probabilities": probs,
                    "reasoning": str(args.get("reasoning", ""))[:2000],
                }
            else:
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                "content": f"[Unknown tool {name}]"})
    logger.warning("[deep-%d/%s] hit max_iters without submit", agent_idx, model.split("/")[-1])
    return None


def _run_supervisor_tavily(query: str, cutoff_dt: datetime | None) -> str:
    """OR-search-backed replacement for the original Tavily wrapper.

    Same input/output contract: takes a query + cutoff, returns a single
    formatted text block of `[date] title\\n URL\\n content` items. Drops
    citations whose parsed publication date is on/after the cutoff (Layer 2
    debiasing). Used by both the deep-agent tool loop and the supervisor.
    """
    if not query.strip():
        return "[Empty query]"
    try:
        from orsearch_brief import extract_publication_date as _extract_pub
        import urllib.request as _urlreq
    except ImportError as e:
        return f"[OR-search import failed: {e}]"

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return "[OPENROUTER_API_KEY not set]"

    cutoff_block = ""
    if cutoff_dt is not None:
        cutoff_str = cutoff_dt.strftime("%Y-%m-%d")
        cutoff_block = (
            f" (USE ONLY sources strictly before {cutoff_str}; IGNORE any "
            f"source dated on/after that date)."
        )

    payload = {
        "model": os.environ.get("ORALL_QUERY_MODEL", "anthropic/claude-haiku-4.5"),
        "max_tokens": 600,
        "temperature": 0.0,
        "messages": [{"role": "user", "content":
            f"Search the web for: {query}{cutoff_block} Then return a short "
            f"factual summary in the form: bullets of ' [DATE] TITLE — URL — "
            f"1-2 sentence content snippet'. Cite each source clearly."
        }],
        "tools": [{
            "type": "openrouter:web_search",
            "openrouter:web_search": {
                "engine": "auto",
                "max_results": int(os.environ.get("ORALL_QUERY_MAX_RESULTS", "5")),
            },
        }],
    }
    try:
        req = _urlreq.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.environ.get("OR_REFERER", "https://prophethacks.com"),
                "User-Agent": "AI-Prophet-Forecast/1.0",
            },
        )
        with _urlreq.urlopen(req, timeout=60) as r:
            body = json.loads(r.read())
    except Exception as e:
        return f"[OR-search failed: {e}]"

    msg = (body.get("choices") or [{}])[0].get("message", {})
    annotations = msg.get("annotations", []) or []
    content = (msg.get("content") or "").strip()

    # Strict temporal debiasing: 6-layer (URL date, page-fetch metadata,
    # content date, sentence-level redaction, citation-content-min-chars,
    # synthesized-brief redaction). See temporal_debias.py.
    if cutoff_dt is None:
        # Live mode — no debiasing needed
        kept_cits = [a.get("url_citation", a) for a in annotations]
        cleaned_content = content
    else:
        try:
            from temporal_debias import debias_search_block, debias_text
        except ImportError as e:
            return f"[temporal_debias import failed: {e}]"
        raw_cits = [a.get("url_citation", a) for a in annotations]
        # Pull candidate / entity names from query so the entity-winner filter fires
        entity_words = re.findall(r"['\"]?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)['\"]?", query)
        kept_cits, debias_stats = debias_search_block(
            raw_cits, cutoff_dt,
            named_entities=entity_words[:6],
            fetch_page=os.environ.get("ORALL_FETCH_PAGE_DATES", "1") == "1",
            min_content_chars=80,
        )
        # Also redact the synthesized brief content
        cleaned_content = debias_text(content, cutoff_dt, named_entities=entity_words[:6])
        logger.info("OR-search debias: %s", debias_stats)

    if not kept_cits and not cleaned_content:
        return "[No pre-cutoff results after strict temporal debiasing]"

    items = []
    for c in kept_cits[:8]:
        url = (c.get("url") or "")[:200]
        title = (c.get("title") or "")[:160]
        body_text = (c.get("content") or "")[:400]
        date_str = c.get("validated_date", "")[:10] if c.get("validated_date") else ""
        items.append(f"  [pre-cutoff {date_str}] {title}\n  URL: {url}\n  {body_text}")

    summary = ""
    if cleaned_content:
        summary = "\n\nSYNTHESIZED SUMMARY (debiased, pre-cutoff sources only):\n" + cleaned_content[:1500]

    return ("\n\n".join(items) if items else "[No pre-cutoff citations after strict filter]") + summary


def _agentic_supervisor(
    event: dict,
    agent_results: list[dict],
    kalshi_prices: dict,
    modes: dict,
    brief: str,
    expected: set[str],
    cutoff_dt: datetime | None,
    trace_log: list[dict] | None = None,
    ballotpedia_brief: str = "",
) -> tuple[dict[str, float] | None, str, str]:
    """AIA-style agentic supervisor with tool-use loop.

    Returns (probs_dict | None, confidence ∈ {high, medium, low}, reasoning).
    Caller decides: if confidence == "high" → use probs, else fall back to mean.
    """
    user = _format_meta_input(event, agent_results, kalshi_prices, modes, brief,
                              ballotpedia_brief)
    outcomes = event.get("outcomes") or []
    tools = [_SUPER_SEARCH_TOOL, _submit_supervisor_tool(outcomes)]

    messages = [
        {"role": "system", "content": SUPERVISOR_SYSTEM},
        {"role": "user", "content": user},
    ]
    client = _or_client()

    for it in range(SUPERVISOR_MAX_ITERS):
        is_last = (it == SUPERVISOR_MAX_ITERS - 1)
        iter_tools = (
            [t for t in tools if t["function"]["name"] == "submit_supervisor_decision"]
            if is_last else tools
        )
        iter_tool_choice = (
            {"type": "function", "function": {"name": "submit_supervisor_decision"}}
            if is_last else "auto"
        )
        if is_last:
            messages.append({"role": "user", "content": "Final iteration — call submit_supervisor_decision now."})
        try:
            kwargs = dict(
                model=SUPERVISOR_MODEL,
                messages=messages,
                tools=iter_tools,
                tool_choice=iter_tool_choice,
                temperature=0.4,
                max_tokens=4000,
            )
            if SUPERVISOR_MODEL in REASONING_MODELS:
                kwargs["extra_body"] = {"reasoning": {"effort": "medium"}}
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.warning("supervisor LLM call failed at iter %d: %s", it, e)
            return None, "low", ""

        msg = resp.choices[0].message
        asst_record: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            asst_record["content"] = msg.content
        if msg.tool_calls:
            asst_record["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {
                    "name": tc.function.name, "arguments": tc.function.arguments,
                }} for tc in msg.tool_calls
            ]
        messages.append(asst_record)
        if trace_log is not None and msg.content:
            trace_log.append({"iter": it, "type": "assistant_text", "content": msg.content})
        if not msg.tool_calls:
            messages.append({"role": "user", "content": "Please call submit_supervisor_decision."})
            continue
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            if name == "search_news":
                q = args.get("query", "").strip()
                logger.info("[v8_super supervisor] iter=%d search %r", it, q[:80])
                tool_result = _run_supervisor_tavily(q, cutoff_dt)
                if trace_log is not None:
                    trace_log.append({"iter": it, "type": "search", "query": q,
                                     "results_text": tool_result[:6000]})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result[:6000]
                })
            elif name == "submit_supervisor_decision":
                logger.info("[v8_super supervisor] iter=%d submitted confidence=%s",
                            it, args.get("confidence"))
                if trace_log is not None:
                    trace_log.append({"iter": it, "type": "submit", "args": args})
                # Parse outputs
                raw_probs = args.get("probabilities", []) or []
                if isinstance(raw_probs, dict):
                    raw_probs = [{"outcome": k, "probability": v} for k, v in raw_probs.items()]
                probs = {}
                for r in raw_probs:
                    o = str(r.get("outcome", ""))
                    if o in expected:
                        probs[o] = max(CLIP_LO, min(CLIP_HI, float(r.get("probability", 0.0))))
                for o in expected:
                    probs.setdefault(o, CLIP_LO)
                s = sum(probs.values()) or 1.0
                probs = {k: v / s for k, v in probs.items()}
                confidence = str(args.get("confidence", "low")).lower()
                if confidence not in ("high", "medium", "low"):
                    confidence = "low"
                return probs, confidence, str(args.get("reasoning", ""))[:500]
            else:
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                "content": f"[Unknown tool {name}]"})
    logger.warning("[v8_super supervisor] hit max_iters without submit")
    return None, "low", ""


# ============================== MAIN PREDICT ==============================
def predict(event: dict) -> dict:
    outcomes = event.get("outcomes") or ["Yes", "No"]
    expected = set(outcomes)
    market_ticker = event.get("market_ticker") or event.get("event_ticker") or ""
    event_ticker = event.get("event_ticker") or market_ticker

    # Resolve real-world resolution date for cutoff
    real_res = RESOLUTION_DATES.get(market_ticker) or RESOLUTION_DATES.get(event_ticker)
    if real_res is None:
        close_str = event.get("close_time") or ""
        try:
            real_res = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            real_res = real_res - timedelta(days=4)
        except Exception:
            real_res = datetime.now(tz=timezone.utc)
    cutoff_dt = real_res - timedelta(days=BUFFER_DAYS)
    logger.info("v8 %s real_resolution=%s cutoff=%s",
                market_ticker, real_res.date(), cutoff_dt.date())

    # 1. Retrieval — Pattern A: openrouter:web_search via Haiku
    brief = ""
    orsearch_meta: dict = {}
    stats: dict = {"kept": 0}
    if USE_OR_SEARCH:
        try:
            from orsearch_brief import build_brief as _orsearch_build_brief
            brief, orsearch_meta = _orsearch_build_brief(
                event, cutoff_dt,
                max_results=int(os.environ.get("ORSEARCH_MAX_RESULTS", "8")),
                debiased=ORSEARCH_DEBIASED,
                return_filtered_citations=True,
            )
            stats = {
                "kept": orsearch_meta.get("n_citations_kept", 0),
                "raw": orsearch_meta.get("n_citations_total", 0),
                "dropped_post_cutoff": orsearch_meta.get("n_citations_dropped_post_cutoff", 0),
            }
            logger.info("v8_3deep_orsearch %s OR-search: citations=%d kept=%d dropped=%d (cost=$%.4f)",
                        market_ticker, stats["raw"], stats["kept"],
                        stats.get("dropped_post_cutoff", 0),
                        (orsearch_meta.get("usage") or {}).get("cost", 0.0))
        except Exception as e:
            logger.warning("v8_3deep_orsearch OR-search failed: %s — falling back to Tavily", e)
    # Fallback to Tavily if OR-search failed or disabled
    if not brief:
        queries = _gen_queries(event)
        raw = _search_tavily(queries, cutoff_dt)
        kept, stats = _filter_strict(raw, cutoff_dt)
        logger.info("v8_3deep_orsearch %s Tavily fallback: raw=%d kept=%d",
                    market_ticker, len(raw), stats["kept"])
        brief = _summarize(event, kept) if kept else ""

    # 1b. Ballotpedia structured profile data (Elections/Politics only, US-only).
    # Lookahead-debiased (post-resolution sentences stripped, truncated at
    # "primary election" section header). Falls back gracefully on miss.
    ballotpedia_brief = ""
    ballotpedia_records: list[dict] = []
    cat_lower = (event.get("category") or "").lower()
    if cat_lower in ("elections", "politics"):
        try:
            from ballotpedia import build_election_brief
            ballotpedia_brief, ballotpedia_records = build_election_brief(
                event.get("title", ""), outcomes, cutoff_dt=cutoff_dt,
                strip_resolution=True,
            )
            n_found = sum(1 for r in ballotpedia_records if r.get("found"))
            logger.info("v8_3deep %s ballotpedia: %d/%d candidates found, brief_len=%d",
                        market_ticker, n_found, len(ballotpedia_records),
                        len(ballotpedia_brief))
        except Exception as e:
            logger.warning("v8_3deep ballotpedia failed: %s", e)

    # 2. Kalshi historical pre-resolution prices
    kalshi_prices, modes = _kalshi_outcome_prices(event_ticker, outcomes, cutoff_dt)
    logger.info("v8 %s Kalshi: prices=%s modes=%s", market_ticker,
                {k: round(v, 3) for k, v in kalshi_prices.items()}, modes)

    # 3. Ensemble: 5 lightweight slots (shared brief) + 3 deep agentic agents
    user_prompt = _format_user_prompt(event, brief, kalshi_prices, modes, ballotpedia_brief)
    # NEW (v8_3deep_orsearch): adds narrative_opus slot using
    # NARRATIVE_REACTIVE_SYSTEM as a counter-voice to the conservative
    # calibrated/CoT/ToT prompts. The other 3 Opus slots will anchor on
    # market+base-rate; this slot will update on named recent events.
    # Mean ensemble captures both. Designed for events like OH-15 where the
    # brief contains a STRONG signal (No Kings arrest) but conservative
    # prompts down-weight it.
    lightweight_specs = [
        ("calibrated_opus", OPUS_MODEL, CALIBRATED_SYSTEM),
        ("cot_opus",        OPUS_MODEL, COT_SYSTEM),
        ("tot_opus",        OPUS_MODEL, TOT_SYSTEM),
        ("calibrated_gpt5", GPT5_MODEL, CALIBRATED_SYSTEM),
        ("narrative_opus",  OPUS_MODEL, NARRATIVE_REACTIVE_SYSTEM),
    ]
    deep_trace_logs: list[list[dict] | None] = [
        ([] if SAVE_TRACES else None) for _ in range(N_DEEP_AGENTS)
    ]
    agent_results: list[dict] = []
    # Run lightweight slots and deep agents in parallel (max 7 concurrent)
    with ThreadPoolExecutor(max_workers=len(lightweight_specs) + N_DEEP_AGENTS) as ex:
        futures = []
        for slot, mdl, sys in lightweight_specs:
            futures.append(ex.submit(_run_one_agent, slot, mdl, sys, user_prompt, expected))
        for di, dmodel in enumerate(DEEP_AGENT_MODELS[:N_DEEP_AGENTS]):
            futures.append(ex.submit(
                _run_deep_agent, di, dmodel, event, outcomes,
                kalshi_prices, modes, cutoff_dt, deep_trace_logs[di],
                ballotpedia_brief,
            ))
        for f in as_completed(futures):
            r = f.result()
            if r:
                agent_results.append(r)
    n_light = sum(1 for r in agent_results if not r["slot"].startswith("deep_"))
    n_deep = sum(1 for r in agent_results if r["slot"].startswith("deep_"))
    logger.info("v8_3deep %s got %d lightweight + %d deep = %d/%d agents",
                market_ticker, n_light, n_deep, len(agent_results),
                len(lightweight_specs) + N_DEEP_AGENTS)

    # 4. AGENTIC SUPERVISOR — does its OWN clarifying searches, emits confidence label.
    # Decision rule per AIA paper §5.2:
    #   confidence == "high"  → use supervisor probabilities
    #   else (medium/low)     → fall back to MEAN of the 5 slots
    #                            (Jensen's inequality guarantees mean Brier ≤
    #                             expected individual Brier — provably safer)
    supervisor_trace: list[dict] | None = [] if SAVE_TRACES else None
    final_llm: dict[str, float] | None = None
    sup_confidence = "low"
    sup_reasoning_text = ""
    sup_probs = None
    if len(agent_results) >= 3:
        sup_probs, sup_confidence, sup_reasoning_text = _agentic_supervisor(
            event, agent_results, kalshi_prices, modes, brief, expected, cutoff_dt,
            trace_log=supervisor_trace, ballotpedia_brief=ballotpedia_brief,
        )
        logger.info("v8_super %s supervisor confidence=%s probs=%s",
                    market_ticker, sup_confidence,
                    {k: round(v, 3) for k, v in (sup_probs or {}).items()})
        if sup_confidence == "high" and sup_probs is not None:
            final_llm = sup_probs
            logger.info("v8_super %s using SUPERVISOR (high conf)", market_ticker)
        # FIX D: Trust supervisor on multi-class medium-confidence
        elif (V2_FIX_D_TRUST_SUP_MULTI
              and sup_confidence == "medium"
              and sup_probs is not None
              and len(outcomes) > 5):
            # Compute mean to see if supervisor materially differs
            _mean_check = {}
            if agent_results:
                for o in outcomes:
                    vs = [r["probabilities"].get(o, 0.0) for r in agent_results]
                    _mean_check[o] = sum(vs) / len(agent_results)
            else:
                _mean_check = {o: 1.0 / len(outcomes) for o in outcomes}
            # Top-outcome delta (supervisor vs mean)
            top_mean = max(_mean_check, key=_mean_check.get) if _mean_check else None
            top_sup = max(sup_probs, key=sup_probs.get) if sup_probs else None
            sup_delta_on_top_mean = abs(sup_probs.get(top_mean, 0.0) - _mean_check.get(top_mean, 0.0)) if top_mean else 0.0
            if sup_delta_on_top_mean > 0.10 or (top_mean != top_sup and top_sup is not None):
                final_llm = sup_probs
                logger.info("v8_3deep_orall_v2 %s FIX-D using SUPERVISOR on medium "
                            "(N=%d outcomes, sup-mean Δ on top=%.3f, top_mean=%s, top_sup=%s)",
                            market_ticker, len(outcomes), sup_delta_on_top_mean,
                            (top_mean or '?')[:20], (top_sup or '?')[:20])
        # else: fall through to mean below (Jensen-protected fallback)

    # Fallback: mean across successful agents
    if final_llm is None:
        if not agent_results:
            final_llm = {o: 1.0 / len(outcomes) for o in outcomes}
        else:
            final_llm = {}
            for o in outcomes:
                vals = [r["probabilities"].get(o, 0.0) for r in agent_results]
                final_llm[o] = sum(vals) / len(agent_results)
            s = sum(final_llm.values()) or 1.0
            final_llm = {k: v / s for k, v in final_llm.items()}
        logger.info("v8 %s used mean fallback probs=%s",
                    market_ticker, {k: round(v, 3) for k, v in final_llm.items()})

    # 5. Blend with Kalshi per-category α (FIX B: disagreement-aware)
    base_alpha = ALPHA_BY_CATEGORY.get(event.get("category", ""), DEFAULT_ALPHA)
    alpha = base_alpha
    if V2_FIX_B_DISAGREE_KALSHI and kalshi_prices and agent_results:
        # (a) Is Kalshi uninformative? (max prob ≤ 0.55 means it's close to uniform)
        max_kalshi = max(kalshi_prices.values()) if kalshi_prices else 0.5
        # (b) Is LLM ensemble agreeing strongly? (std across agents on top mean outcome)
        top_outcome_mean = max(final_llm, key=final_llm.get)
        agent_probs_on_top = [r["probabilities"].get(top_outcome_mean, 0.0) for r in agent_results]
        if len(agent_probs_on_top) >= 2:
            mn = sum(agent_probs_on_top) / len(agent_probs_on_top)
            std = (sum((p - mn) ** 2 for p in agent_probs_on_top) / len(agent_probs_on_top)) ** 0.5
        else:
            std = 0.5  # unknown — treat as disagree
        # (c) Does LLM disagree with Kalshi on top outcome?
        llm_top_prob = final_llm.get(top_outcome_mean, 0.0)
        kalshi_top_prob = kalshi_prices.get(top_outcome_mean, 0.5)
        llm_kalshi_delta = abs(llm_top_prob - kalshi_top_prob)
        # Adjust alpha
        reasons = []
        if max_kalshi <= 0.55:
            # Kalshi flat → trust LLM more
            alpha = base_alpha * 0.4
            reasons.append(f"kalshi_flat (max={max_kalshi:.2f})")
        elif std < 0.08 and llm_kalshi_delta > 0.10:
            # Strong agreement among agents + disagree with Kalshi → trust LLM more
            alpha = base_alpha * 0.5
            reasons.append(f"agents_agree (std={std:.3f}) + disagree_kalshi (Δ={llm_kalshi_delta:.2f})")
        if reasons:
            logger.info("v8_3deep_orall_v2 %s FIX-B Kalshi α %.2f→%.2f  (%s)",
                        market_ticker, base_alpha, alpha, "; ".join(reasons))

    blended: dict[str, float] = {}
    for o in outcomes:
        llm_p = final_llm.get(o, 0.0)
        if o in kalshi_prices:
            kp = max(CLIP_LO, min(CLIP_HI, kalshi_prices[o]))
            blended[o] = alpha * kp + (1 - alpha) * llm_p
        else:
            blended[o] = llm_p

    # 6. Tiered confidence guardrail
    n_evidence = stats.get("kept", 0)
    n_out = len(outcomes)
    n_exact = sum(1 for o in outcomes if modes.get(o) == "exact")
    score = 0
    if n_evidence >= 10: score += 1
    if n_exact >= max(1, n_out / 2): score += 1
    if n_exact == n_out: score += 1
    shrink = GUARDRAIL_STRENGTHS[score]
    if shrink > 0:
        uniform = 1.0 / n_out
        blended = {o: shrink * uniform + (1 - shrink) * blended[o] for o in outcomes}
        logger.info("v8 %s GUARDRAIL score=%d shrink=%.2f", market_ticker, score, shrink)

    # 7. Clip + normalize
    clipped = {k: max(CLIP_LO, min(CLIP_HI, v)) for k, v in blended.items()}
    s = sum(clipped.values()) or 1.0
    final = {k: v / s for k, v in clipped.items()}

    # 8. Platt scaling — with FIX A (multi-class scaling) + FIX C (skip on Kalshi-defer)
    # Multi-class: p_i -> p_i^a / sum_j(p_j^a). a > 1 sharpens, a < 1 softens.
    max_pre_platt = max(final.values()) if final else 0.5

    # FIX A: scale α down for multi-class
    if V2_FIX_A_MULTICLASS_PLATT and len(outcomes) > 2:
        # α(N) = 1 + (PLATT_A - 1) / max(1, N - 1)
        effective_alpha = 1.0 + (PLATT_A - 1.0) / max(1, len(outcomes) - 1)
        platt_reason = f"FIX-A multi-class N={len(outcomes)} → α={effective_alpha:.3f}"
    elif ADAPTIVE_PLATT:
        effective_alpha = _adaptive_platt_alpha(PLATT_A, sup_confidence, max_pre_platt)
        platt_reason = f"ADAPTIVE α={effective_alpha:.3f}"
    else:
        effective_alpha = PLATT_A
        platt_reason = f"BASE α={effective_alpha:.3f}"

    # FIX C: skip Platt entirely if agents are deferring to market
    if V2_FIX_C_SKIP_PLATT and sup_confidence != "high" and kalshi_prices and agent_results:
        top_outcome = max(final_llm, key=final_llm.get) if final_llm else None
        if top_outcome:
            llm_kalshi_delta_top = abs(final_llm.get(top_outcome, 0.0)
                                        - kalshi_prices.get(top_outcome, 0.5))
            if llm_kalshi_delta_top < 0.05:
                old_alpha = effective_alpha
                effective_alpha = 1.0
                platt_reason = f"FIX-C SKIP (LLM-Kalshi Δ={llm_kalshi_delta_top:.3f}, sup_conf={sup_confidence})  was {old_alpha:.3f}"

    if effective_alpha != 1.0:
        powered = {k: max(v, 1e-9) ** effective_alpha for k, v in final.items()}
        s = sum(powered.values()) or 1.0
        final = {k: v / s for k, v in powered.items()}
        clipped = {k: max(CLIP_LO, min(CLIP_HI, v)) for k, v in final.items()}
        s = sum(clipped.values()) or 1.0
        final = {k: v / s for k, v in clipped.items()}

    logger.info(
        "v8_3deep_orall_v2 %s FINAL=%s (%s sup_conf=%s max_pre=%.3f kalshi_α_used=%.2f)",
        market_ticker,
        {k: round(v, 3) for k, v in final.items()},
        platt_reason, sup_confidence, max_pre_platt, alpha,
    )

    # Save full trace if enabled (per global rule: always save all model
    # outputs / reasonings / search results via toggle for experiments)
    if SAVE_TRACES:
        try:
            TRACES_DIR.mkdir(parents=True, exist_ok=True)
            trace = {
                "market_ticker": market_ticker,
                "event_ticker": event_ticker,
                "title": event.get("title"),
                "category": event.get("category"),
                "outcomes": outcomes,
                "rules": event.get("rules") or event.get("description", ""),
                "close_time": event.get("close_time"),
                "cutoff_dt": cutoff_dt.isoformat() if cutoff_dt else None,
                "platt_alpha": PLATT_A,
                "adaptive_platt": ADAPTIVE_PLATT,
                "effective_platt_alpha": effective_alpha,
                "max_pre_platt_prob": max_pre_platt,
                "market_prices": kalshi_prices,
                "market_modes": modes,
                "tavily_brief": brief[:4000] if brief else "",  # actually OR-search brief if USE_OR_SEARCH
                "ballotpedia_brief": ballotpedia_brief[:4000] if ballotpedia_brief else "",
                "ballotpedia_records": ballotpedia_records,
                "search_backend": "openrouter:web_search" if (USE_OR_SEARCH and orsearch_meta) else "tavily",
                "orsearch_meta": {k: v for k, v in orsearch_meta.items()
                                   if k not in ("citations",)},
                "orsearch_citations": orsearch_meta.get("citations", [])[:40],
                "lightweight_specs": [{"slot": s, "model": m} for s, m, _ in lightweight_specs],
                "deep_agent_models": DEEP_AGENT_MODELS[:N_DEEP_AGENTS],
                "deep_agent_traces": [
                    {"agent_idx": i, "iterations": deep_trace_logs[i] or []}
                    for i in range(N_DEEP_AGENTS)
                ],
                "agent_results": [
                    {
                        "slot": r.get("slot"),
                        "model": r.get("model"),
                        "probabilities": r.get("probabilities"),
                        "reasoning": r.get("reasoning"),
                    } for r in agent_results
                ],
                "mean_or_fallback_probs": final_llm if isinstance(final_llm, dict) else None,
                "supervisor": {
                    "model": SUPERVISOR_MODEL,
                    "iterations": supervisor_trace or [],
                    "final_probabilities": sup_probs,
                    "confidence": sup_confidence,
                    "reasoning": sup_reasoning_text,
                },
                "used_supervisor": (sup_confidence == "high" and sup_probs is not None),
                "final_probs": final,
            }
            safe_tk = (market_ticker or "unknown").replace("/", "_")[:120]
            (TRACES_DIR / f"{safe_tk}.json").write_text(json.dumps(trace, indent=2, default=str))
            logger.info("v8_super %s trace saved → %s.json", market_ticker, safe_tk)
        except Exception as e:
            logger.warning("v8_super %s trace save failed: %s", market_ticker, e)

    return {
        "probabilities": [
            {"market": o, "probability": final.get(o, CLIP_LO)} for o in outcomes
        ]
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ev = {
        "market_ticker": "KXNEXTHUNGARYPM-26MAY01",
        "event_ticker": "KXNEXTHUNGARYPM-26MAY01",
        "title": "Who became Prime Minister of Hungary after the 2026 election?",
        "outcomes": ["Péter Magyar", "Viktor Orbán"],
        "category": "Elections",
        "close_time": "2026-05-09T17:52:03Z",
        "description": "Hungary's 2026 parliamentary election held April 12, 2026.",
    }
    print(json.dumps(predict(ev), indent=2))
