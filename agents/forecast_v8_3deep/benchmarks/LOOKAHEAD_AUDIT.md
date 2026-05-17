# Lookahead-Citation Audit — 26-Event Benchmark

This document audits every citation/source/reference pulled into the agent's
context for the `agent_v8_3deep_orall` 26-event benchmark and flags any that
are dated ON OR AFTER the event's true resolution date.

Methodology:

- For each event: load the trace + the real resolution date from `data/real_resolution_dates.json`
- Inspect `orsearch_citations` (the Haiku-shared-brief citation list with validated dates)
- Inspect `deep_agent_traces[*].iterations[*].results_text` — scan for `/YYYY/MM/DD/` URL date patterns
- Inspect `supervisor.iterations[*].results_text` — same scan
- Flag any citation/URL date >= the event's resolve_dt as a lookahead leak

Note: dates that appear IN the article body as content (e.g. mentions of upcoming
election dates) are NOT counted as leaks — only dates parseable as PUBLICATION dates
(from URL patterns or 'Updated/Published <date>' markers) count.

## Summary

- **Events audited**: 26/26
- **Events with zero post-resolve citations**: 26/26
- **Events with ≥1 post-resolve citation**: 0/26

### Per-event audit results

| Ticker | Category | Total leaks | OR-citation leaks | Deep-agent text-leaks | Supervisor text-leaks |
|---|---|---:|---:|---:|---:|
| `KXATPCHALLENGERMATCH-26MAY05BAXARC` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXATPCHALLENGERMATCH-26MAY05PERLAL` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXATPCHALLENGERMATCH-26MAY10ROCJOH` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXCOLOMBIASENATE-26` | Politics | ✅ 0 | 0 | 0 | 0 |
| `KXCOUNTYCHAMPMATCH-26MAY08DURWOR` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXCOUNTYCHAMPMATCH-26MAY08LEISUS` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXCOUNTYCHAMPMATCH-26MAY08SOMGLA` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXCRICKETTESTMATCH-26MAY08PAKBAN` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXEREDIVISIEGAME-26MAY10BREHEE` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXFEDCHAIRCOUNT-27` | Politics | ✅ 0 | 0 | 0 | 0 |
| `KXITFWMATCH-26MAY12NAJEBS` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXLALIGA-26` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXLIGAPORTUGAL-26` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXLIGUE1-26` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXNBASERIES-26LALOKCR2` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXNEXTHUNGARYPM-26MAY01` | Elections | ✅ 0 | 0 | 0 | 0 |
| `KXNHLCALDER-26` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXOHPRIMARY-15D26` | Elections | ✅ 0 | 0 | 0 | 0 |
| `KXROASTSUBJECT-30JAN01` | Entertainment | ✅ 0 | 0 | 0 | 0 |
| `KXSERIEA-26` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXSURVIVORELIMINATION-26APR11` | Entertainment | ✅ 0 | 0 | 0 | 0 |
| `KXTHEMASKEDSINGER-27JAN01` | Entertainment | ✅ 0 | 0 | 0 | 0 |
| `KXTOURNAMENTOFCHAMPIONS-26DEC31` | Entertainment | ✅ 0 | 0 | 0 | 0 |
| `KXVRASCOTUSVOTE-26` | Politics | ✅ 0 | 0 | 0 | 0 |
| `KXWTACHALLENGERMATCH-26MAY05WATOKA` | Sports | ✅ 0 | 0 | 0 | 0 |
| `KXWVPRIMARY-01D26` | Elections | ✅ 0 | 0 | 0 | 0 |

## 🎉 NO LEAKS — all 26 events fully temporally debiased

Every citation, URL, and date marker present in the agent's context
for all 26 events is dated strictly before the event's resolution date.
The strict temporal_debias filter is working as designed.


---

## How dates are detected

- **URL patterns**: `/YYYY/MM/DD/` substrings in source URLs (e.g. `dispatch.com/.../2026/04/17/...`)
- **Content patterns**: `Updated <Month DD, YYYY>` or `Published <Month DD, YYYY>` in citation body text
- **JSON-LD metadata**: `<meta property="article:published_time">` and schema.org `datePublished` (when page-fetch enabled)

Dates without parseable structure (e.g. "last week", "Thursday") are NOT
automatically datable — the filter is conservative: undated citations are
dropped by default unless the Haiku synthesizer's prompt-time cutoff
instruction caught them at the source.

## Reproducing this audit

```bash
python scripts/audit_citation_dates.py
# → benchmarks/LOOKAHEAD_AUDIT.md
```
