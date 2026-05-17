# v8_3deep — Prophet Hacks 2026 Forecast-track agent

## 👉 Where the latest agent lives

**Latest shipping agent**: [`agent_v8_3deep_orall.py`](agent_v8_3deep_orall.py)

- Full file path in this fork:
  **`agents/forecast_v8_3deep/agent_v8_3deep_orall.py`**
- Full-stack architecture: 5 lightweight slots (4 evidence-first + 1
  narrative-reactive) + 3 deep agentic agents (Opus 4.7 / GPT-5 / Gemini 2.5 Pro)
  + AIA-style agentic supervisor, every search through OpenRouter's native
  `openrouter:web_search` tool
- Full 26-event benchmark: **mean Brier 0.3247** vs prior best (v8+Platt 0.390) — see
  [`benchmarks/BENCHMARK_RESULTS.md`](benchmarks/BENCHMARK_RESULTS.md)
- Lookahead-audit verified clean: **0/26 events leak post-resolution citations** — see
  [`benchmarks/LOOKAHEAD_AUDIT.md`](benchmarks/LOOKAHEAD_AUDIT.md)
- Per-event chain-of-thought post-mortem: [`benchmarks/COT_POST_MORTEM.md`](benchmarks/COT_POST_MORTEM.md)

**Quick run:**
```bash
# Single event
V83DEEP_AGENT_MODULE=agent_v8_3deep_orall \
  V83DEEP_SAVE_TRACES=1 \
  python scripts/run_v8_3deep_single.py KXOHPRIMARY-15D26

# Full 26-event benchmark (parallel)
ORALL_FETCH_PAGE_DATES=0 \
  python scripts/run_v8_3deep_orall_full.py 4

# As a Prophet Arena agent server
V83DEEP_AGENT_MODULE=agent_v8_3deep_orall \
  uvicorn server:app --host 0.0.0.0 --port 8000
```

---

## Repository layout (fork)

```
akshayg108/ai-prophet/                  (fork of ai-prophet/ai-prophet, branch: forecast/v8_3deep_agents)
├── README.md                           (upstream — unchanged)
├── LICENSE                             (upstream MIT — unchanged)
├── CONTRIBUTING.md                     (upstream — unchanged)
├── .pre-commit-config.yaml             (upstream — unchanged)
├── .gitignore                          (upstream + we added an *.json exception for our agent data)
├── packages/                           (upstream ai-prophet SDK + CLI — unchanged)
│   ├── core/
│   └── cli/
├── skills/                             (upstream — unchanged)
├── docs/                               (upstream — unchanged)
└── agents/                             ← OUR ONLY ADDITION
    └── forecast_v8_3deep/              ← all our work lives here
```

Everything we contributed lives under **`agents/forecast_v8_3deep/`**. The
rest of the repo is upstream `ai-prophet/ai-prophet` — we did not modify
any existing files except `.gitignore` (to allow our agent's sample data
to be tracked).

## Our directory in detail: `agents/forecast_v8_3deep/`

```
agents/forecast_v8_3deep/
│
├── README.md                                ← this document
│
├── ── AGENT VARIANTS (latest → earliest, ablation order) ──
│   ├── agent_v8_3deep_orall.py              ← 🏆 LATEST — full-stack, mean Brier 0.3247
│   ├── agent_v8_3deep_evfirst.py            ← evidence-first prompts (Path A + B)
│   ├── agent_v8_3deep_orsearch.py           ← OR-search brief only (Pattern A)
│   └── agent_v8_3deep.py                    ← baseline (Tavily brief + Ballotpedia)
│
├── ── SUPPORTING MODULES (imported by the agents) ──
│   ├── temporal_debias.py                   ← 6-layer lookahead-clean filter
│   ├── ballotpedia.py                       ← candidate-profile fetcher (HTML scrape, debiased)
│   ├── orsearch_brief.py                    ← Haiku + openrouter:web_search → shared brief
│   ├── aia_prompts.py                       ← verbatim AIA paper prompts
│   ├── kalshi_history.py                    ← Kalshi T-3d candle prices
│   └── server.py                            ← FastAPI /predict + /health endpoints
│
├── ── BENCHMARK RESULTS — orall on the official sample-resolved set ──
│   └── benchmarks/
│       ├── BENCHMARK_RESULTS.md             ← full results, all 26 events by category
│       ├── COT_POST_MORTEM.md               ← per-event CoT deconstruction + suggested improvements
│       └── LOOKAHEAD_AUDIT.md               ← zero-leak audit (26/26 events clean)
│
├── ── SCRIPTS ──
│   └── scripts/
│       ├── run_v8_3deep_single.py           ← run any variant on a single event
│       ├── run_v8_3deep_orall_full.py       ← full 26-event parallel benchmark
│       ├── audit_citation_dates.py          ← lookahead-leak audit runner
│       ├── finalize_benchmark_report.py     ← regenerate BENCHMARK_RESULTS.md from predictions JSON
│       └── simulate_adaptive_platt.py       ← counterfactual Platt-α analysis (no API calls)
│
├── ── DATA ──
│   └── data/
│       ├── sample_resolved_events.json      ← 26-event smoke set (from official sample-resolved/v1.0.0)
│       ├── actuals.json                     ← ground truth (POST-HOC scoring only — agent never reads)
│       ├── real_resolution_dates.json       ← per-event resolution dates for cutoff calc
│       ├── predictions_v8_3deep_orall.json  ← raw predictions from the full benchmark
│       └── sample_traces/
│           ├── v8_3deep/                    (2 traces, baseline-variant smoke)
│           ├── v8_3deep_orall/              (1 trace, orall pre-benchmark debug)
│           └── v8_3deep_orall_full_benchmark/  ← 26 traces from THE benchmark — full per-agent CoT
│
└── ── PAPERS ANALYSIS — research backing the architecture ──
    └── papers_analysis/
        ├── aia_forecaster_analysis.txt              (Bridgewater AIA paper deep-dive)
        ├── kalshibench_analysis.txt                 (KalshiBench 300-event analysis)
        ├── silicon_crowd_analysis.txt               (Wisdom of Silicon Crowd paper)
        ├── AIA_PAPER_TO_CODE_MAPPING.txt            (per-component fidelity audit)
        ├── CATEGORIES_AND_APIS.md                   (best specialized API per category)
        ├── FINAL_ARCHITECTURE_TO_WIN.txt
        ├── AUDIT_RESPONSE.txt
        └── SEARCH_PROVIDERS_ANALYSIS.txt
```

## Variant comparison

| Variant | Brief backend | Lite prompts | Narrative slot | Deep search | OH-15 honest Brier | Full-smoke mean Brier |
|---|---|---|---|---|---:|---:|
| `agent_v8_3deep` | Tavily | conservative | — | Tavily | 1.907 | (not benchmarked) |
| `agent_v8_3deep_orsearch` | OR-search | conservative | — | Tavily | 1.896 | (not benchmarked) |
| `agent_v8_3deep_evfirst` | OR-search | evidence-first | yes | Tavily | 1.902 | (not benchmarked) |
| **`agent_v8_3deep_orall`** ⭐ | OR-search | evidence-first | yes | OR-search | 1.905 | **0.3247** |

The three earlier variants are kept for ablation analysis — each demonstrates
the marginal contribution of a single architectural decision (Pattern A
brief swap, evidence-first prompts, all-OR-search). Only `orall` was run
through the full 26-event benchmark.

---

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
ensemble reason from evidence without aggressive prompt anchoring.

## Lookahead debiasing (retrospective testing only)

For LIVE forecasts on future events, none of this is needed — the future
doesn't exist yet so it can't leak. For retrospective smoke tests on
resolved events, we apply 6 layers (full detail in
[`benchmarks/LOOKAHEAD_AUDIT.md`](benchmarks/LOOKAHEAD_AUDIT.md)):

1. URL date parse — drop citations with `/YYYY/MM/DD/` ≥ cutoff
2. Page-fetch metadata — `<meta property="article:published_time">`, JSON-LD `datePublished`
3. Content-date scan — "Updated/Published <Month DD, YYYY>" markers
4. Sentence-level redaction — winner verbs, vote totals, dates in cutoff window
5. Citation min-content filter — drop redacted content < 100 chars
6. Synthesized-brief redaction — same sentence filter on Haiku's summary

Ballotpedia has its own strip — removes "is on the ballot in the general
election" (implies primary-won), "Next election DATE" (only present on
winners' profiles), past-tense "ran for election", and the "page was
current at the end of the official's last term" marker.

**Audit result**: 0/26 events leak post-resolution citations.

## Setup

```bash
# Python 3.10+
pip install fastapi uvicorn anthropic openai litellm tavily-python python-dotenv

# Environment variables (.env or shell)
export OPENROUTER_API_KEY=sk-or-...     # required — drives orsearch_brief + LLM calls
export TAVILY_API_KEY=tvly-...          # required for v8_3deep / orsearch / evfirst
                                         # NOT required for v8_3deep_orall (everything via OR-search)
export KALSHI_API_KEY=...                # optional — improves pre-resolution market price accuracy
```

## Cost per event

| Variant | LLM calls | Search | Total |
|---|---|---|---|
| `v8_3deep` (Tavily) | $1.00-1.30 | $0.02 (Tavily) | **$1.10-1.40** |
| `v8_3deep_orsearch` | $1.00-1.30 | $0.10 (OR brief) | **$1.20-1.50** |
| `v8_3deep_evfirst` | $1.00-1.30 | $0.10 | **$1.20-1.50** |
| `v8_3deep_orall` | $1.00-1.30 | $0.10 brief + $0.50-0.80 deep + supervisor OR-search | **$1.80-2.40** |

For the 200-event Prophet Arena eval window, budget ~$360–480 (well within
the $500 OpenRouter budget).

## Key references

- AIA Forecaster (Bridgewater): arXiv 2511.07678 — see `papers_analysis/aia_forecaster_analysis.txt`
- KalshiBench: see `papers_analysis/kalshibench_analysis.txt`
- Wisdom of Silicon Crowd (Schoenegger et al.): see `papers_analysis/silicon_crowd_analysis.txt`
- Jensen's inequality justifying mean-fallback (Halawi 2024)
- OpenRouter `openrouter:web_search` docs (routes Claude→Anthropic-native, GPT→OpenAI-native, Gemini→Exa)

## License

Inherits MIT from the upstream `ai-prophet/ai-prophet` repo.
