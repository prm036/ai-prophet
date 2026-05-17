# v8_3deep — Prophet Hacks 2026 Forecast-track agent

A multi-agent forecasting ensemble for the Prophet Arena Forecast track, built
on top of the `ai-prophet` SDK. Implements the AIA Forecaster paper
(arXiv 2511.07678) with several practical extensions (Kalshi-blend, Platt
extremization, tiered guardrail, Ballotpedia profile injection, OpenRouter
native-search backend, strict temporal debiasing).

**📊 Full 26-event benchmark results**: see [`benchmarks/BENCHMARK_RESULTS.md`](benchmarks/BENCHMARK_RESULTS.md) — `agent_v8_3deep_orall` scored **mean Brier 0.3247** vs prior-best `v8+Platt` at **0.390** (−16.7% relative). Per-category, per-event breakdown + methodology + lookahead-debiasing audit all in the benchmark doc.


## TL;DR — what's in here

```
agents/forecast_v8_3deep/
├── agent_v8_3deep.py            # Baseline: Tavily brief + 4 lite + 3 deep + supervisor + Platt
├── agent_v8_3deep_orsearch.py   # + OR-search shared brief (Pattern A)
├── agent_v8_3deep_evfirst.py    # + evidence-first prompts (Path A+B)
├── agent_v8_3deep_orall.py      # All-in: OR-search across all 7 agents + supervisor (Pattern A+B+all-OR-search)
├── temporal_debias.py           # 6-layer lookahead-clean filter for retrospective testing
├── ballotpedia.py               # Candidate-profile fetcher (HTML scrape, lookahead-stripped)
├── orsearch_brief.py            # Haiku + openrouter:web_search → debiased shared brief
├── aia_prompts.py               # Verbatim AIA paper prompts (forecaster + supervisor)
├── kalshi_history.py            # Pre-resolution Kalshi candle prices (T-3d snapshot)
├── server.py                    # FastAPI /predict + /health endpoints
├── data/
│   ├── sample_resolved_events.json   # 26-event smoke set (events as Prophet Arena sends them)
│   ├── actuals.json                  # Ground truth (POST-HOC scoring only — agent never reads)
│   ├── real_resolution_dates.json    # Per-event real resolution dates (for retrospective cutoff)
│   └── sample_traces/                # 3 traces showing agent reasoning + scores
│       ├── v8_3deep/
│       └── v8_3deep_orall/
├── scripts/
│   ├── run_v8_3deep_single.py        # Run any variant on a single event by ticker
│   └── simulate_adaptive_platt.py    # Counterfactual Platt-alpha sweep on saved traces
└── papers_analysis/                  # Background research
    ├── aia_forecaster_analysis.txt        # arXiv 2511.07678 deep-dive
    ├── kalshibench_analysis.txt           # KalshiBench 300-event analysis + per-category yes-rates
    ├── silicon_crowd_analysis.txt         # Wisdom-of-silicon-crowd paper
    ├── AIA_PAPER_TO_CODE_MAPPING.txt      # Component-by-component fidelity audit
    ├── CATEGORIES_AND_APIS.md             # Best specialized API per Prophet Arena category
    ├── FINAL_ARCHITECTURE_TO_WIN.txt
    ├── AUDIT_RESPONSE.txt
    └── SEARCH_PROVIDERS_ANALYSIS.txt
```

## Architecture (final variant — `agent_v8_3deep_orall`)

```
              ┌────────────────────────────────────────────────────────────┐
              │ 1. Retrieval: orsearch_brief.py                            │
              │    Haiku-4.5 + openrouter:web_search → debiased shared      │
              │    brief (Anthropic-native indexing finds local sources    │
              │    Tavily misses — e.g. daytondailynews.com, fox28columbus)│
              │                                                            │
              │ 1b. Ballotpedia: candidate profiles (lookahead-stripped)    │
              │                                                            │
              │ 2. Kalshi T-3d snapshot prices (kalshi_history.py)          │
              └─────────────────────────┬──────────────────────────────────┘
                                        ↓
              ┌────────────────────────────────────────────────────────────┐
              │ 3. Ensemble (parallel):                                     │
              │   5 lightweight slots (shared brief)                        │
              │     - calibrated_opus   (evidence-first calibrated)         │
              │     - cot_opus          (evidence-first chain-of-thought)   │
              │     - tot_opus          (evidence-first tree-of-thought)    │
              │     - calibrated_gpt5   (same prompt, GPT-5 model)          │
              │     - narrative_opus    (event-driven counter-voice)        │
              │   3 deep agentic agents (own openrouter:web_search loops)   │
              │     - Opus 4.7, GPT-5, Gemini 2.5 Pro                       │
              └─────────────────────────┬──────────────────────────────────┘
                                        ↓
              ┌────────────────────────────────────────────────────────────┐
              │ 4. Agentic supervisor (own search) → {high|medium|low}     │
              │    Per AIA §5.2: if high → use sup probs; else → mean      │
              │    (Jensen-protected fallback)                              │
              └─────────────────────────┬──────────────────────────────────┘
                                        ↓
              ┌────────────────────────────────────────────────────────────┐
              │ 5. Per-category α blend with Kalshi                         │
              │    Sports 0.70 / Politics 0.50 / Entertainment 0.35 /       │
              │    Elections 0.30                                           │
              └─────────────────────────┬──────────────────────────────────┘
                                        ↓
              ┌────────────────────────────────────────────────────────────┐
              │ 6. Tiered confidence guardrail (signal-strength 0-3)        │
              │    Shrinks toward uniform when evidence is thin             │
              └─────────────────────────┬──────────────────────────────────┘
                                        ↓
              ┌────────────────────────────────────────────────────────────┐
              │ 7. Platt α=2.0 extremization                                │
              │    p^α / (p^α + (1-p)^α)                                    │
              └─────────────────────────┬──────────────────────────────────┘
                                        ↓
                                    Final probs
```

The downstream math layers (5/6/7) are the safety net that lets the LLM
ensemble (3) reason from evidence without aggressive prompt anchoring.

## Variant comparison on the OH-15 catastrophic-miss event

The single hardest event in the 26-event smoke. Don Leonard (Cornell PhD,
OSU professor, arrested at "No Kings" protest March 28) beat Adam Miller
(former state rep, retired Army colonel, Strickland + AFGE-endorsed,
3:1 fundraising advantage). Kalshi had Miller at 0.91 three days pre-primary.

| Variant | Brief backend | Lite prompts | Narrative slot | Deep search | Honest OH-15 Brier |
|---|---|---|---|---|---|
| `agent_v8_3deep` | Tavily | conservative | — | Tavily | **1.907** |
| `agent_v8_3deep_orsearch` | OR-search (Anthropic native) | conservative | — | Tavily | 1.896 |
| `agent_v8_3deep_evfirst` | OR-search | evidence-first | yes | Tavily | 1.902 |
| **`agent_v8_3deep_orall`** | OR-search | evidence-first | yes | **OR-search + temporal_debias** | **1.699** |

The `orall` variant saves **0.21 Brier on the worst event** vs baseline by
giving every agent + supervisor access to local Ohio outlets (Columbus
Dispatch March 29 arrest article, fox28columbus, abc6onyourside Grove City
coverage) that Tavily's index doesn't crawl.

## Lookahead debiasing (retrospective testing only)

For LIVE forecasts on future events, none of this is needed — the future
doesn't exist yet so it can't leak. For retrospective smoke tests on
resolved events, we need explicit defenses.

`temporal_debias.py` applies 6 layers:

1. **URL-date parse**: drop citations where `/YYYY/MM/DD/` in URL ≥ cutoff
2. **Page-fetch metadata**: HTTP fetch the URL, parse `<meta property="article:published_time">`, `<time datetime="...">`, JSON-LD `datePublished`
3. **Content-date scan**: "Updated/Published <Month DD, YYYY>" in body
4. **Sentence-level redaction**: drop sentences containing winner-revealing verbs (won/defeated/advanced), vote totals, resolution-state language, or explicit dates within ±3 days of cutoff
5. **Citation-content min-chars**: drop citations whose redacted content is <100 chars (mostly winner-statement)
6. **Synthesized-brief redaction**: same sentence-level filter on Haiku's summary

Verified clean on OH-15 — preserves the pre-cutoff arrest story
("Don Leonard arrested at Ohio 'No Kings' protest", dispatch.com 2026-03-29)
while stripping all post-resolution winner statements and vote tallies.

`ballotpedia.py` has its own lookahead strip — removes "is on the ballot in
the general election" (implies primary-won), "Next election DATE" (only
present on primary winners' profiles), past-tense "ran for election" (race
over), and the "This page was current at the end of the official's last
term" marker.

## Setup

```bash
# Python 3.10+
pip install fastapi uvicorn anthropic openai litellm tavily-python python-dotenv

# Environment variables (.env or shell)
export OPENROUTER_API_KEY=sk-or-...     # required — drives orsearch_brief + LLM calls
export TAVILY_API_KEY=tvly-...          # required for v8_3deep / v8_3deep_orsearch / v8_3deep_evfirst
                                         # not required for v8_3deep_orall (everything via OR)
export KALSHI_API_KEY=...                # optional — improves pre-resolution market price accuracy
```

## Running smoke tests

```bash
# Single event on any variant
V83DEEP_AGENT_MODULE=agent_v8_3deep_orall \
  V83DEEP_SAVE_TRACES=1 \
  python scripts/run_v8_3deep_single.py KXOHPRIMARY-15D26

# Counterfactual analysis on saved traces (no API calls)
python scripts/simulate_adaptive_platt.py
```

## Deploying as a Prophet Arena agent

```bash
# Local
V83DEEP_AGENT_MODULE=agent_v8_3deep_orall \
  uvicorn server:app --host 0.0.0.0 --port 8000

# Endpoint contract (per Prophet Arena docs):
#   POST /predict  with body = event JSON
#   returns: {"probabilities": [{"market": "<outcome>", "probability": <float>}, ...]}
# Probabilities don't need to sum to 1 — server normalizes for Brier.
```

Register the public URL at https://prophethacks.com/submit-endpoint.

## Cost per event

| Variant | LLM calls | Search | Total |
|---|---|---|---|
| `v8_3deep` (Tavily) | $1.00-1.30 | $0.02 (Tavily) | **$1.10-1.40** |
| `v8_3deep_orsearch` | $1.00-1.30 | $0.10 (OR brief) | **$1.20-1.50** |
| `v8_3deep_evfirst` | $1.00-1.30 | $0.10 | **$1.20-1.50** |
| `v8_3deep_orall` | $1.00-1.30 | $0.10 brief + $0.50-0.80 deep + supervisor OR-search | **$1.80-2.40** |

For the 200-event Prophet Arena eval window, budget ~$200-500 across all
variants. We used `agent_v8_3deep_orall` for the final deploy.

## Key references

- AIA Forecaster (Bridgewater 2026): arXiv 2511.07678
- KalshiBench (2026): see `papers_analysis/kalshibench_analysis.txt`
- Wisdom of Silicon Crowd (Schoenegger et al.): see `papers_analysis/silicon_crowd_analysis.txt`
- Jensen's inequality justifying mean-fallback (Halawi 2024)
- OpenRouter `openrouter:web_search` docs (routes Claude→Anthropic-native, GPT→OpenAI-native, Gemini→Exa)

## License

Inherits MIT from the upstream `ai-prophet/ai-prophet` repo.
