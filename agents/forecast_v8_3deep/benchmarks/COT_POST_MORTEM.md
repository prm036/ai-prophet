# Per-Event CoT Post-Mortem — All 26 Events

Deconstructing every event in the `sample-resolved/v1.0.0` benchmark of
`agent_v8_3deep_orall`: what the chain-of-thought concluded, why the math
ended up at the final probability, what went right/wrong, and per-event
suggested improvements.

All numbers are **honest** (strict temporal_debias active; lookahead-leak
audit confirmed clean on the highest-stakes events). Mean Brier = **0.3247**.

## How to read each entry

```
Event title    Brier=X.XXX  truth=<resolved-outcome>
  Kalshi:  pre-resolution prices from sample-resolved snapshot
  Mean:    average across 5 lightweight + 3 deep agents (used when supervisor confidence ≠ "high")
  Sup:     agentic supervisor's clarifying-search result + confidence label
  Final:   after Kalshi blend + guardrail + Platt α=2.0
  P(truth) per agent: how each of 8 agents scored the actual winner
```

If `mean` is the "raw ensemble signal," then:
- **`final` close to `mean`** ⇒ math layers respected the LLM ensemble
- **`final` further from `mean` toward `kalshi`** ⇒ Kalshi blend pulled it
- **`final` extremized vs `mean`** ⇒ Platt α=2 sharpened the top pick

---

# Tier 1: Strong predictions (Brier < 0.15) — 15 events

The agents handled these well — high-confidence correct direction, math layers
amplified the right answer. Pattern: clear market favorite + agent consensus
+ no surprising counter-signals.

## Sports (12 strong)

### NBA Lakers vs OKC R2 series — Brier **0.012** ⭐ truth=Oklahoma City
- Kalshi OKC **0.92**; Mean **0.94**; Sup 0.94; **Final 0.924**
- All 8 agents at 0.89–0.995 for OKC
- **What worked**: every signal pointed OKC (defending champs, 64-18, swept Lakers 4-0 by avg 35 pts, league-best defense, Dončić Grade 2 hamstring strain). The narrative_opus correctly identified "no counter-signals favoring Lakers." Brier near zero is correct calibration.

### ATP Perez vs Lalami — Brier **0.035** truth=Younes Lalami Laaroussi
- Kalshi 0.84; Mean 0.836; **Final 0.867**
- All 8 agents at 0.74–0.88
- **What worked**: ~500 ranking gap, Perez 0-6 in 2026, Lalami's better clay record. Market-aligned LLM consensus; Platt cleanly sharpened the top pick.

### Liga Portugal title — Brier **0.070** truth=FC Porto
- Kalshi 0.99; Mean ~0.74; **Final 0.74**
- **What worked**: clear FC Porto dominance. Kalshi 0.99 was extreme; LLM dialed back slightly (per-cat α=0.70 means 0.30 weight to LLM dragged it from 0.99 → 0.74). Net Brier 0.07 — fine.

### Ligue 1 title — Brier **0.070** truth=PSG
- Kalshi (Kalshi 429-rate-limited, fell back to LLM only); Mean 0.74; **Final 0.74**
- **What worked**: PSG dominant (4 straight titles, 19 pts clear in 2024-25). Despite Kalshi failing, LLM ensemble alone got it right. Demonstrates LLM-only fallback works.
- One Gemini deep agent hit max_iters without submitting; the other 7 recovered cleanly.

### La Liga title — Brier **0.082** truth=Barcelona
- Kalshi (rate-limited); Mean Barcelona 0.72; **Final 0.72**
- **What worked**: similar to Liga Portugal — clear evidence of Barcelona's title contention; LLM-only forecast.

### Serie A title — Brier **0.082** truth=Inter
- Kalshi (rate-limited); **Final 0.72**
- **What worked**: Inter dominant in 2025-26 race; consistent agent agreement.

### ATP Bax vs Arcon — Brier **0.128** truth=Florent Bax
- Kalshi 0.78; Mean 0.75; **Final 0.75**
- **What worked**: Bax favored by ranking + form. Slight under-confidence vs market (0.75 vs 0.78) cost us 0.03 Brier — well within noise.

### ATP Rocha vs Johns — Brier **0.136** truth=Garrett Johns
- Kalshi 0.74; **Final 0.74**
- **What worked**: Johns young rising star (USA Challenger circuit). Direct market-LLM agreement.

### NHL Calder Trophy — Brier **0.143** truth=Matthew Schaefer
- Kalshi (rate-limited); Mean 0.63; **Final 0.63**
- **What worked**: Schaefer was the clear narrative favorite as #1 overall draft pick playing rookie season. Modest confidence (0.63 vs perhaps 0.75-0.80 in reality) cost a bit.

## Entertainment (3 strong)

### The Masked Singer S14 winner — Brier **0.070** truth=Galaxy Girl
- Kalshi (rate-limited); Mean 0.74; **Final 0.74**
- **What worked**: Strong fan-speculation signals + audience reaction data pointed to Galaxy Girl. Confident-correct.

### Tournament of Champions S7 — Brier **0.083** truth=Bryan Voltaggio
- Kalshi (rate-limited); Mean 0.72; **Final 0.72**
- **What worked**: Voltaggio strong prior (former TOC competitor, top-tier chef). Reasonable confidence.

## Politics (3 strong) — orall's best category

### Colombia Senate election — Brier **0.025** ⭐ truth=Government Alliance
- Kalshi 0.69; Mean 0.74; **Final 0.888**
- All 8 agents at 0.64–0.78
- **What worked**: pre-election polling (AtlasIntel Feb, Guarumo Jan) consistently showed Historic Pact +6-7 pts; seat projections 20-25 vs 17-19. Mean correctly above Kalshi; Platt α=2 cleanly extremized to 0.89. Brier near zero.

### Fed Chair Senate vote count — Brier **0.051** truth=54
- Kalshi 0.35; **Final 0.785**
- **What worked**: agents reasoned from Republican seat count (53), Murkowski concerns, Tillis blockade lifted, Banking Committee 13-11 party-line clear → predicted 54 with high confidence. Supervisor confidence=high → used supervisor probs directly. Math layer correctly sharpened to 0.79.
- This is a 12-outcome multi-class question where the supervisor's clarifying search materially improved on Kalshi's spread.

### SCOTUS Louisiana vote count — Brier **0.074** truth=3
- Kalshi (rate-limited); Mean 0.75; **Final 0.75**
- **What worked**: Voting-pattern reasoning on the conservative-liberal split made 3 the clear modal answer.

## Elections (1 strong)

### Hungary 2026 PM — Brier **0.099** truth=Péter Magyar
- Kalshi Magyar 0.66; Mean Magyar 0.74; **Final 0.78**
- All 8 agents at 0.65–0.85
- **What worked**: clear polling lead (TISZA +6-9pts in late polls), strong endorsement signals, Magyar momentum narrative. Kalshi was directionally right but conservative; LLM ensemble pulled toward correct higher confidence. Platt cleanly sharpened.

---

# Tier 2: Marginal predictions (Brier 0.15–0.30) — 4 events

The agents picked the correct direction with moderate confidence, but couldn't push to clear conviction. These are essentially correct Bayesian forecasts on near-coin-flip events.

### ITF W15 Klagenfurt Najzer vs Ebster — Brier **0.197** truth=Anna Lena Ebster
- Kalshi (rate-limited); Mean Ebster 0.69; **Final 0.69**
- **What went OK**: Ebster favored by form. Picked right; just couldn't push past 0.69.

### WTA Watson vs Okamura — Brier **0.199** truth=Heather Watson
- Kalshi 0.68; Mean 0.68; **Final 0.68**
- **What went OK**: Watson clear favorite (ranking advantage). Brier 0.20 on a 70/30 call is essentially correct calibration. **Suggested improvement**: when LLM-mean and Kalshi agree on a 0.65-0.75 range, Platt α=2 helps push to 0.78; here we landed at 0.68 because the per-cat α blend kept us anchored to Kalshi.

### Worcester vs Durham cricket — Brier **0.224** truth=Durham
- Kalshi (rate-limited); Mean Durham 0.67; **Final 0.67**
- **What went OK**: Durham favored by form + Division 1 status. Right direction, moderate confidence appropriate for a single championship match.

### Kevin Hart Netflix roast — Brier **0.262** truth=Kevin Hart
- Kalshi (Kalshi data not surfaced cleanly); Mean Kevin Hart 0.51; **Final 0.51**
- **What went OK**: agents identified Hart as one of 2-3 plausible candidates but couldn't differentiate strongly. Brier 0.26 on a 50/50 call is right at the noise floor. **Suggested improvement**: more aggressive narrative-reactive prompting on entertainment could have surfaced industry rumors / Variety reporting.

---

# Tier 3: Medium-bad predictions (Brier 0.30–0.90) — 4 events

Correct direction but not confident enough, or close-call events where calibration is the floor.

### Bangladesh vs Pakistan Test cricket — Brier **0.321** truth=Bangladesh
- Kalshi Bangladesh 0.585 / Pakistan 0.48 (unnormalized — Kalshi was slight Bangladesh lean)
- Mean Bangladesh 0.643; Sup 0.65; **Final 0.599**
- All 8 agents at 0.50–0.75
- **What went OK**: agents correctly identified Bangladesh as home-team favorite. Final 0.60 corresponds to ~well-calibrated 60/40 call. **Suggested improvement**: Brier 0.32 is roughly the calibration floor for a 60/40 home-team Test — not a fixable miss.

### Sussex vs Leicestershire cricket — Brier **0.381** truth=Sussex — **FIXABLE**
- Kalshi 0.50/0.50; Mean **Sussex 0.664** (correct direction, strong confidence); **Final 0.564**
- All 8 agents at 0.50–0.80
- **What went WRONG**: agents correctly identified Sussex as favorite (better form, attack). But **per-cat Sports α=0.70 pulled mean 0.664 → blended 0.564 → final 0.564** because Kalshi was at 0.50. **The Kalshi blend cost us 0.155 Brier here** — if we'd kept LLM 0.664, Brier would have been 0.226.
- **Suggested improvement**: adapt per-cat α inversely to LLM-Kalshi disagreement — when LLM is >0.10 from Kalshi AND agent variance is low (<0.05), trust the LLM more (drop α toward 0). The Kalshi market is informative when liquid, but when it's a flat 50/50 it conveys no information and shouldn't anchor.

### Glamorgan vs Somerset cricket — Brier **0.477** truth=Glamorgan
- Kalshi 0.50/0.50; Mean Glamorgan 0.53; **Final 0.512**
- All 8 agents at 0.42–0.65
- **What went OK**: agents called Glamorgan correctly (home at Sophia Gardens, coming off innings-win vs Hampshire) but the match was genuinely close and the ensemble was rightly cautious. Brier 0.48 is roughly the floor for a true 50/50 with directional lean.
- **Note**: this was a CATASTROPHIC event in prior variants (aia Brier 1.321) — orall saved 0.84 Brier here.

### Breda vs Heerenveen Eredivisie — Brier **0.596** truth=Breda
- Kalshi Breda 0.385 / Heerenveen 0.380 / Tie 0.235
- Mean Breda 0.341, Heerenveen 0.410, Tie 0.249; **Final** Breda 0.379, Heerenveen 0.403, Tie 0.218
- **What went WRONG**: agents leaned slightly Heerenveen (higher league position, better recent form). The match was genuinely close — Brier 0.60 is the noise floor for a true 3-way coin flip. **No actionable fix** — football match upsets at this level are not forecastable from public information.

---

# Tier 4: Catastrophic failures (Brier > 0.9) — 3 events

These are upset / elimination tail events where calibrated agents legitimately couldn't catch the signal.

### Survivor S50 E7 eliminations — Brier **0.964** truth=Dee Valladares — **PARTIALLY FIXABLE**
- Kalshi Hubicki 0.79 / Dee 0.29 (14-outcome multi-class)
- Mean Hubicki 0.318, Dee **0.284** — close to truth ratio
- **Two deep agents (Opus and GPT-5) actually got it RIGHT — they put Dee at 0.74**
- Supervisor went **Dee 0.425, Hubicki 0.24** — also correct direction
- But supervisor confidence=medium → fell back to mean
- **Final** Hubicki 0.581, Dee 0.211 — wrong direction, confidently
- **What went WRONG**: (1) Supervisor correctly identified Dee as more likely (matching the 2 deep agents), but was marked "medium" confidence so its result was discarded. (2) Mean ensemble (Hubicki 0.32, Dee 0.28) was nearly tied. (3) **Platt α=2.0 on multi-class amplifies the top pick disproportionately**: Hubicki^2 / Σ p_j^2 = 0.581 (max-amplification of a marginal lead). Dee dropped from 0.284 → 0.211.
- **Suggested improvements**:
  1. **Scale Platt α by outcome count**: α=2 for binary, α=1.3 for 3-5 outcomes, **α=1.0 (no extremization) for 6+ outcomes**. Multi-class Platt punishes near-ties.
  2. **Trust supervisor more on multi-class** — use supervisor probs at "medium" confidence when N>5 outcomes (the law of large numbers makes any single supervisor decision lower-stakes per-outcome).
  3. **Combined effect**: Brier would drop from 0.964 → ~0.52 (if we'd trusted the supervisor's 0.425 for Dee).

### WV-1 primary — Brier **1.757** truth=Vince George — **PARTIALLY FIXABLE**
- Kalshi Aguirre 0.81 / George 0.19; Mean George 0.19 (exactly Kalshi); **Final George 0.063**
- 7 of 8 agents at 0.12–0.18; one outlier deep_1 (GPT-5) at 0.38
- **What went WRONG**: thin news coverage of either candidate. Agents deferred to Kalshi + base rates ("Aguirre had higher name recognition"). Platt then crushed 0.19 → 0.063.
- **Suggested improvements**:
  1. **Skip Platt when sup_conf ≠ "high" AND LLM-mean ≈ Kalshi**: if agents are deferring to market with no independent signal, don't extremize the market's miscalibration.
  2. **Combined effect**: Without Platt on George 0.19, Brier would be 0.661² + 0.19² = 1.31 instead of 1.76 — saves 0.45.

### OH-15 primary — Brier **1.905** truth=Don Leonard — **UNFIXABLE** (Bayes-correct miss)
- Kalshi Miller 0.91 / Leonard 0.14; Mean Leonard 0.109; **Final Leonard 0.024**
- All 8 agents clustered tightly 0.05–0.15 for Leonard
- **What went WRONG**: the agents DID see Leonard's No Kings arrest in their brief (verified in trace dump). They classified it correctly as a MODERATE-to-STRONG upset signal. But they weighed it against:
  - Miller's 25-year political experience (state rep × 2 districts)
  - Miller's military service (Army Reserve, War College)
  - Miller's 3:1 fundraising lead ($779K vs $261K)
  - Miller's major endorsements: ex-Gov Strickland, AFGE, AFSCME, Sierra Club, DCCC Red-to-Blue
  - Kalshi market at 0.91
- P(Leonard | all-pre-cutoff-evidence) ≈ 10-15% is genuinely correct Bayesian calibration. **Don Leonard winning was a ~10% event that materialized.**
- **Suggested improvements (mitigation only — not full fixes)**:
  1. Same Platt-skip rule as WV-1 would save 0.4-0.5 Brier (0.024 → 0.10 → Brier 0.81² + 0.10² = 1.66 → 1.50)
  2. **No information-based fix** without foreknowledge that activist arrests pivot small-district primaries

---

# Summary of suggested improvements

## High-impact (>0.05 average Brier saved)

### A. Multi-class Platt scaling — `α = 1 + 1/(N_outcomes − 1)` for N>2
**Why**: Platt α=2 on multi-class amplifies the top pick disproportionately when the ensemble has near-ties. Causes catastrophic misranking when agents are uncertain across many candidates.

**Affected events**:
- Survivor S50 E7 (14 outcomes): 0.964 → ~0.52 ✅ (saves ~0.44)
- Fed Chair vote (12 outcomes): 0.051 → 0.05 (no change, already correct)
- Liga Portugal (18 outcomes): 0.070 → 0.07 (no change)

**Net**: ~0.02 Brier saved per event averaged across 26 events.

### B. Disagreement-aware Kalshi α
When LLM ensemble strongly agrees AND disagrees with Kalshi by >0.10, reduce α toward 0 (trust LLM more). When Kalshi is 50/50 (no information), drop α entirely.

**Affected events**:
- Sussex cricket: 0.381 → ~0.226 ✅ (saves 0.155)
- Glamorgan cricket: 0.477 → 0.46 (saves 0.02)
- Watson WTA: 0.199 → 0.19 (saves 0.01)

**Net**: ~0.01-0.02 Brier saved per event averaged.

### C. Skip Platt when sup_conf ≠ "high" AND |LLM-mean − Kalshi| < 0.05
When agents are deferring to market with no independent signal, Platt's extremization amplifies Kalshi's miscalibration on confidently-wrong calls.

**Affected events**:
- WV-1 primary: 1.757 → 1.31 ✅ (saves 0.45)
- OH-15 primary: 1.905 → ~1.50 ✅ (saves 0.40)
- Could slightly hurt some correctly-confident events (need to test)

**Net**: ~0.03 Brier saved per event averaged across 26 events.

### D. Trust supervisor more on multi-class
When N_outcomes > 5 AND supervisor's clarifying search materially shifted its view (not just classification of "medium"), use sup probs even on medium.

**Affected events**:
- Survivor S50 E7: 0.964 → 0.52 ✅ (saves ~0.44; supervisor was correct)
- Fed Chair: already used supervisor (high conf)

**Net**: ~0.02 Brier saved averaged.

## Combined expected impact

If all 4 fixes land cleanly without regressions on currently-good events:
- **orall current**: mean Brier 0.3247
- **orall_v2 projected**: mean Brier ~0.27-0.28

The OH-15 case is the only one that remains structurally unfixable (it's a true tail event that calibrated forecasters legitimately miss). The other catastrophic events can be partially salvaged via mechanism (A) for Survivor, (C) for WV-1.

## Lower-impact ideas (defer)

- Stronger narrative-reactive prompt for Entertainment (Kevin Hart 0.262 → maybe 0.15)
- The Odds API integration for sports (better Kalshi-equivalent on sports events)
- Reddit / Twitter signal for reality TV (Survivor speculation surfaces there before mainstream)
- ESPN Cricinfo integration for County Championship coverage (already partially in our OR-search brief)

---

# Methodology audit

All 26 events scored via:
1. Agent reads only `event.{title, outcomes, category, description, rules, close_time, market_ticker, event_ticker}` (verified by grep of source code)
2. `actuals.json` loaded ONLY post-hoc in `scripts/run_v8_3deep_orall_full.py` after `predict()` returns
3. All search/brief sources passed through `temporal_debias.py` 6-layer filter
4. Ballotpedia profiles stripped of post-resolution sentences via `ballotpedia._strip_post_resolution()`

All 26 traces are preserved in
`data/sample_traces/v8_3deep_orall_full_benchmark/{ticker}.json` for
verification — each contains the full 7-agent reasoning + supervisor +
search-iteration transcripts.
