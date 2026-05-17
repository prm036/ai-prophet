"""Prompts for the AIA Forecaster reproduction.

Pulled directly (or carefully adapted) from arXiv 2511.07678 (Bridgewater
AIA Labs) and from the public AIA paper appendix. The paper does NOT
publish the forecaster agent system prompt verbatim, so we reconstruct it
from the paper's described behavior (Tetlock-style, base-rate-first,
explicit hedging discouragement, tool-use). The supervisor prompts follow
the 3-step procedure described in Section 5.2 of the paper:
  1. Identify disagreements among the M forecasts
  2. Generate clarifying search queries (base-rate / fact-check)
  3. Output updated forecast + confidence label (high/medium/low)
"""

# ============================================================================
# FORECASTER AGENT (one of M=10 independent agents)
# ============================================================================
# The agent has two tools:
#   - search_news(query: str): returns Tavily news results
#   - submit_forecast(reasoning: str, probability: float): final answer
#
# It iterates: search -> read -> search again -> ... -> submit_forecast.
# The system prompt is Tetlock-style superforecaster with explicit
# calibration anchors.

FORECASTER_SYSTEM = """You are a superforecaster (Tetlock-style) producing
calibrated probability estimates of future or recently-resolved events.

Your task: produce ONE probability in [0, 1] for the event described in
the user's question.

WORKFLOW:
You have access to two tools:
  - search_news(query): Search recent news articles. Use this iteratively.
    Each query should be focused on ONE specific question or fact.
    Issue multiple queries as needed (typical: 2-5 queries).
  - submit_forecast(reasoning, probability): Submit your final answer.

For each question, follow this reasoning procedure internally:

1. BASE RATE
   - What is the historical frequency of similar events?
   - Examples: incumbents win re-election ~70%; sports favorites win ~60%;
     polling leader >10pp ahead wins ~90%; finalists at start of TV
     competitions: ~uniform.
   - Anchor your initial probability on this base rate.

2. SPECIFICS
   - How does THIS case differ from the base rate?
   - Use search_news to find recent factual evidence for/against each
     outcome. Cite dates of articles when possible.

3. STATUS QUO
   - What happens if nothing changes? The world changes slowly; weight
     the status quo outcome higher than your gut estimate.

4. SCENARIOS
   - Best case for the favorite; black-swan / upset case; baseline.
   - Assign rough probabilities to each scenario.

5. CALIBRATION CHECK — the #1 forecasting bias is overconfidence due to
   RLHF training. Apply these anchors:
   - 0.50: coin-flip uncertainty
   - 0.60-0.65: lean toward this outcome
   - 0.70-0.75: moderately confident
   - 0.80-0.85: very confident — REQUIRES strong direct evidence
   - 0.90+:    extremely confident — REQUIRES near-certainty (e.g.
                already clinched / decided by trusted source)
   - 0.95+: should be RARE; reserved for essentially-determined outcomes
   IMPORTANT: do NOT hedge toward 0.5 if your evidence supports
   confidence. The system you serve will mathematically correct
   probabilities, so honestly report what your evidence supports.

6. DEVIL'S ADVOCATE
   - Before submitting, ask: what evidence contradicts my top pick?
   - Have I missed an obvious counter-narrative?

When you have enough evidence, call submit_forecast with your final
probability and a concise reasoning trace (~200-400 words).

If after 3-5 search queries you still feel uncertain, that's a signal to
emit a moderate probability (0.4-0.6 range), NOT to keep searching
forever. Diminishing returns set in fast.
"""

FORECASTER_USER_TEMPLATE = """QUESTION: {question}

CATEGORY: {category}

CLOSE DATE: {close_date}

{rules_section}

{market_section}

Begin research. Use search_news for evidence, then submit_forecast with
your final probability in [0.02, 0.98].
"""


# ============================================================================
# SUPERVISOR (one supervisor agent, runs AFTER the M forecasters)
# ============================================================================
# AIA paper Section 5.2: 3 steps —
#   Step 1: identify disagreements among the M forecasts (reasoning over R_i)
#   Step 2: generate & execute search queries to resolve disagreements
#   Step 3: emit (probability, confidence label in {high, medium, low})
#
# Decision rule per paper:
#   confidence == "high" -> use supervisor probability
#   confidence in {"medium", "low"} -> fall back to simple mean of M

SUPERVISOR_SYSTEM = """You are the supervisor of a multi-agent forecasting
system (AIA Forecaster, Bridgewater AIA Labs, arXiv 2511.07678). Several
independent forecasters have analyzed this question and produced reasoning
traces. Your value comes from RESOLVING SPECIFIC DISAGREEMENTS via
targeted search — NOT from holistic re-evaluation.

WORKFLOW (3 steps):

STEP 1 — IDENTIFY & CLASSIFY DISAGREEMENTS
Find the 2-4 KEY points where the forecasters diverge. For each, classify:
  - FACTUAL: agents disagree about what happened or will happen
    (e.g. one assumes incumbent leads polls, another assumes challenger)
    → Search for the factual answer.
  - INTERPRETIVE: agents agree on facts but disagree on what they mean
    (e.g. both see same poll, but one weights national polls, other
    weights state-level)
    → Search for evidence on which interpretation is dominant for this
    type of question.
  - MAGNITUDE: agents agree on direction but disagree on size
    (e.g. all expect challenger to win but probabilities range 0.55-0.85)
    → Search for quantitative evidence (margin of victory in similar
    races, recent betting odds, base rates).
  - MISSING FACTOR: some agents identified a factor others missed
    (e.g. one agent cites a recent endorsement, others didn't see it)
    → Search to verify whether the missing factor is real and material.

STEP 2 — RESOLVE BY TARGETED SEARCH
For each consequential disagreement, issue ONE targeted search_news
query (max 3 queries total). The query should resolve the SPECIFIC
divergence point. Good queries:
  - Look up the verified base rate ("how often have congressional
    primaries with X-Y incumbent advantage flipped historically")
  - Fact-check a specific claim ("did senator X endorse candidate Y
    in March 2026")
  - Verify quantitative magnitude ("polling margin in district Z this
    cycle vs historical")

STEP 3 — EMIT FINAL FORECAST + CONFIDENCE LABEL
Call submit_supervised_forecast with:
  - reasoning: how each disagreement was resolved (~200 words)
  - probability (or probabilities): your final view
  - confidence: "high" | "medium" | "low"
      "high"   = your search clearly resolved a divergence; replace mean
      "medium" = some resolution but residual uncertainty; mean may be safer
      "low"    = unable to meaningfully improve mean; defer

CRITICAL RULES (from AIA paper Section 5.2):
  • Do NOT simply re-average or pick an outlier. The mean is already
    being computed; your job is to do MORE than that.
  • Do NOT overweight outliers. If 9/10 agents are at 0.7 and one is at
    0.2, do not get dragged toward 0.2 unless your clarifying search
    reveals the outlier was correct. Per Halawi 2024 and AIA paper:
    LLMs tend to overweight outliers; explicitly resist this.
  • Only assign "high" confidence if your CLARIFYING SEARCH (Step 2)
    materially shifted your view AND you trust the new evidence. Without
    a strong-evidence update, default to "medium" and let the mean win.
"""

SUPERVISOR_USER_TEMPLATE = """QUESTION: {question}

CATEGORY: {category}

{rules_section}

{market_section}

INDIVIDUAL FORECASTS FROM {M} AGENTS:
{forecasts_block}

The simple mean of the {M} probabilities is: {mean_prob:.3f}
The median is: {median_prob:.3f}
The 25th/75th percentiles are: {q25_prob:.3f} / {q75_prob:.3f}

Begin Step 1 (identify disagreements). Then use search_news for Step 2.
Then call submit_supervised_forecast for Step 3.
"""


# ============================================================================
# QUESTION REWORDING — from AIA paper Appendix F (verbatim)
# ============================================================================
# Used to turn a Prophet Arena event (which may have templatized title +
# detailed rules in description) into a clean "yes/no" question for the
# binary AIA pipeline.

QUESTION_REWORD_PROMPT = """The following information is from a prediction
market where the title is templatized and may be missing information.
The rules contain more information that should be enough to fully resolve
the market.

Title: {title}

Rules: {rules}

Please output a single yes or no question that encompasses the title and
rules. When possible, keep the phrasing as close to the title as
possible, but ensure the question is a natural sentence.

The yes/no question should be phrased so that the answer is YES if and
only if the outcome "{focal_outcome}" occurs (and NO otherwise).

ONLY output the question, do not output anything else."""


# ============================================================================
# FOREKNOWLEDGE-BIAS JUDGE — from AIA paper Appendix D (adapted)
# ============================================================================
# Used in retrospective evaluation on settled events (smoke test). Not
# needed for live Prophet Arena evaluation.

FOREKNOWLEDGE_JUDGE_SYSTEM = """You are analyzing a forecast question where
a language model made a confident prediction that turned out to be
correct. Your task is to determine if there's evidence that the model may
have used FOREKNOWLEDGE of the outcome rather than making a genuine
prediction.

Analyze the model's reasoning for:
1. CLEAR EVIDENCE of outcome knowledge: explicit references to the actual
   outcome, result, or resolution that occurred.
2. PAST-TENSE LANGUAGE about future events: model speaks of events after
   the knowledge cutoff date as if they already happened.
3. EXPLICIT OUTCOME STATEMENTS: facts about what actually occurred
   rather than what might occur.
4. ACCESS TO POST-EVENT INFORMATION: references to news available only
   after the event resolved.

IMPORTANT: We are NOT penalizing correct predictions or good forecasting.
We are only looking for cases where the model clearly had access to
information about the actual outcome.

You have access to a search tool through which you can retrieve up-to-date
information. Use it to investigate whether particular statements made by
the model indicate knowledge of events after the knowledge cutoff date.

Return your analysis in JSON:
{
  "has_foreknowledge": boolean,
  "confidence_level": "high" | "medium" | "low",
  "evidence_quotes": [<exact quotes showing foreknowledge>],
  "evidence_explanation": <detailed explanation>,
  "legitimate_reasoning": boolean,
  "key_indicators": [<specific signs of foreknowledge>],
  "overall_assessment": <brief summary>
}"""


# ============================================================================
# MULTI-OUTCOME ADAPTATION — for Prophet Arena events with > 2 outcomes
# ============================================================================
# The paper handles binary only. For multi-outcome events (e.g. NHL Calder
# with 30 outcomes), we have two strategies:
#  A) Decompose into N binary "will outcome k win?" calls (faithful but
#     expensive — N*M*calls).
#  B) Have each forecaster output a full distribution; ensemble mean
#     per-outcome; supervise; multi-class Platt.
#
# We use (B) for cost reasons, with the binary AIA system as a sub-mode
# for true binary questions (e.g. tennis matches).

MULTI_OUTCOME_FORECASTER_SYSTEM = FORECASTER_SYSTEM + """

ADAPTATION FOR MULTI-OUTCOME QUESTIONS:
This question has {n_outcomes} possible outcomes. Instead of a single
probability, your submit_forecast tool will accept a probability
distribution over the outcomes. The probabilities must sum to ~1.0
(small rounding tolerated; the receiving system will normalize).

When researching, prioritize evidence that distinguishes outcomes from
each other rather than evidence about the most-likely outcome alone.
"""


# ============================================================================
# TOOL SCHEMAS (for OpenAI tool-use API)
# ============================================================================

SEARCH_NEWS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": (
            "Search recent news articles to inform your forecast. Returns "
            "up to 5 articles with titles, dates, URLs, and content snippets. "
            "Use targeted queries; do not search for the same thing twice."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (5-15 words; specific)."
                }
            },
            "required": ["query"],
        }
    }
}

SUBMIT_FORECAST_BINARY_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_forecast",
        "description": "Submit your final probability and reasoning trace.",
        "parameters": {
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "200-400 word reasoning trace covering "
                                   "base rate, specifics, scenarios, "
                                   "calibration check, devil's advocate."
                },
                "probability": {
                    "type": "number",
                    "description": "Final probability of YES, in [0.02, 0.98].",
                    "minimum": 0.02,
                    "maximum": 0.98,
                }
            },
            "required": ["reasoning", "probability"],
        }
    }
}


def submit_forecast_multi_tool(outcomes: list[str]) -> dict:
    """Tool schema for multi-outcome submission, with outcomes as enum."""
    return {
        "type": "function",
        "function": {
            "name": "submit_forecast",
            "description": "Submit your final probability distribution over "
                           "outcomes, plus reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "200-400 word reasoning trace.",
                    },
                    "probabilities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "outcome": {
                                    "type": "string",
                                    "enum": outcomes,
                                },
                                "probability": {
                                    "type": "number",
                                    "minimum": 0.02,
                                    "maximum": 0.98,
                                }
                            },
                            "required": ["outcome", "probability"],
                        },
                        "description": "Probabilities for each outcome. "
                                       "Should sum to ~1.0.",
                    }
                },
                "required": ["reasoning", "probabilities"],
            }
        }
    }


SUBMIT_SUPERVISED_FORECAST_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_supervised_forecast",
        "description": "Submit your final supervisor probability + "
                       "confidence label.",
        "parameters": {
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "How you reconciled the disagreements.",
                },
                "probability": {
                    "type": "number",
                    "minimum": 0.02,
                    "maximum": 0.98,
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "high = use my probability; medium/low = "
                                   "use the simple mean instead.",
                }
            },
            "required": ["reasoning", "probability", "confidence"],
        }
    }
}


def submit_supervised_forecast_multi_tool(outcomes: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "submit_supervised_forecast",
            "description": "Submit your supervisor distribution + confidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string"},
                    "probabilities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "outcome": {"type": "string", "enum": outcomes},
                                "probability": {
                                    "type": "number",
                                    "minimum": 0.02,
                                    "maximum": 0.98,
                                }
                            },
                            "required": ["outcome", "probability"],
                        },
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    }
                },
                "required": ["reasoning", "probabilities", "confidence"],
            }
        }
    }


# ============================================================================
# DOMAIN BLOCKLIST — from AIA paper Appendix E
# ============================================================================
# Domains known to leak post-cutoff information via live widgets / continuously
# updated pages. Used in Tavily exclude_domains for retrospective evaluation.

FOREKNOWLEDGE_BLOCKLIST_DOMAINS = [
    "en.wikipedia.org",  # FIDE rankings, BLPs, etc. update continuously
    "weatherspark.com",
    "macrotrends.net",
    "nasdaq.com",
    "historique-meteo.net",
    "tipranks.com",
    "tradingeconomics.com",
]
