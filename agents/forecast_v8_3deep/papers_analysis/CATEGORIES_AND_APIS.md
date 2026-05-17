# Prophet Arena / KalshiBench Categories → Specialized APIs

Built 2026-05-17 from KalshiBench paper (arxiv via kalshibench_analysis.txt) and
our 26-event smoke. Goal: a complete mapping of every category we might see in
the Forecast-track eval to the highest-ROI data source for it.

## All 13 categories observed in KalshiBench (out of 16-cat full set)

KalshiBench drew 300 questions from a 1,531-question Kalshi sample, with these
categories present in the sampled benchmark (frequency + yes-rate per category):

| # | Category | KalshiBench % | KalshiBench yes-rate | Claude Opus 4.5 Brier on KB |
|---|---|---:|---:|---:|
| 1 | **Sports** | 27.7% (n=83) | 34.9% | 0.193 ⭐ strong |
| 2 | **Politics** | 18.3% (n=55) | 52.7% | (subset of Elections strong) |
| 3 | **Entertainment** | 15.7% (n=47) | 36.2% | 0.187 ⭐ strong |
| 4 | **Companies** | 10.0% (n=30) | 60.0% | (likely strong) |
| 5 | **Elections** | 8.0% (n=24) | 20.8% | 0.172 ⭐ strong |
| 6 | **Mentions** | 6.3% (n=19) | 36.8% | 0.357 ⚠ weak |
| 7 | **Crypto** | 3.7% (n=11) | 27.3% | 0.240 ⚠ WORSE THAN CHANCE |
| 8 | **Climate/Weather** | 3.0% (n=9) | 33.3% | 0.229 ⭐ strong |
| 9 | **Financials** | 2.7% (n=8) | 12.5% | 0.203 ⭐ strong |
| 10 | **World** | 2.0% (n=6) | — | 0.262 ⚠ weak |
| 11 | **Economics** | 1.3% (n=4) | — | 0.326 ⚠ weak |
| 12 | **Social** | 1.0% (n=3) | — | — |
| 13 | **Sci/Tech** | 0.3% (n=1) | — | — |

Plus 3 more categories in the full 16-cat set not seen in their 300-event sample
(likely smaller niches).

## Where each catastrophic failure in OUR smoke comes from

| Event | Category | Our Brier | Why it failed |
|---|---|---:|---|
| OH-15 primary | Elections | 1.92 | No Tavily coverage of Leonard or "No Kings" arrest |
| WV-1 primary | Elections | 1.54 | Same pattern — obscure challenger won |
| Glamorgan v Somerset cricket | Sports | 1.32 | Niche county championship — sparse public data |
| Survivor S50 E7 | Entertainment | 1.32 | Reality TV elimination — fundamentally noisy |

## Specialized APIs ranked per category — what to wire next

### 1. Sports (27.7% of events → biggest aggregate lever)
| API | Free? | What it gives | Why it matters |
|---|---|---|---|
| **The Odds API** (the-odds-api.com) | $25-100/mo, 500 free/mo | Implied probabilities from Vegas/UK books | DIRECT PRIOR — single best signal |
| **ESPN unofficial** (`site.api.espn.com`) | Free, no key | Scoreboards, schedules, standings, head-to-head | Context for low-market sports |
| **ESPN Cricinfo** (cricinfo.com) | Free, scrape | County championship + international cricket | Fixes our cricket failure |
| **API-FOOTBALL** (api-football.com) | Free tier 100/day | Detailed soccer fixtures, standings, lineups | Lower-tier leagues |
| **NBA.com Stats API** | Free, undocumented | Detailed NBA stats | Specific to NBA |
| **NHL Stats API** (`statsapi.web.nhl.com`) | Free | NHL standings, games | Specific to NHL |

**Recommended**: The Odds API ($25/mo) + ESPN unofficial (free) + Cricinfo (free).

### 2. Politics (18.3%)
| API | Free? | What it gives |
|---|---|---|
| **GovTrack** (govtrack.us/api) | Free | US legislative votes, bills, member records |
| **ProPublica Congress API** | Free, key required | Detailed roll-call votes, member positions |
| **OpenSecrets / OpenFEC** | Free | Campaign finance |

For Senate/House vote-count questions (e.g., "How many senators voted for X?"), GovTrack + ProPublica are the canonical sources.

### 3. Entertainment (15.7%)
| API | Free? | What it gives |
|---|---|---|
| **TMDB API** (themoviedb.org) | Free, key required | Movies/TV metadata, popularity, ratings |
| **OMDb API** | Free tier | IMDb-style metadata |
| **Goldderby** (scrape) | Free | Awards prediction columns |
| **EW/Variety** (RSS or scrape) | Free | Industry insider news |

For reality TV (Survivor, Masked Singer) there's no good API — Reddit /r/survivor and fan blogs are the actual signal, hard to access systematically.

### 4. Companies (10.0%)
| API | Free? | What it gives |
|---|---|---|
| **SEC EDGAR** (sec.gov/edgar) | Free | Official filings (10-K, 10-Q, 8-K) — earnings, acquisitions, lawsuits |
| **yfinance** (Python lib) | Free | Stock prices, basic financials |
| **Polygon.io** | Free tier | Stocks, options, news |
| **Alpha Vantage** | Free tier 500/day | Fundamentals + technical indicators |
| **Crunchbase** | $29/mo basic | Startup/private company info |

### 5. Elections (8.0%) ← we just integrated Ballotpedia
| API | Free? | What it gives |
|---|---|---|
| **Ballotpedia** (HTML scrape) ✅ | Free | Candidate profiles, education, career, endorsements |
| **OpenFEC** (api.open.fec.gov) | Free | Campaign finance — leading indicator of viability |
| **Daily Kos Elections** (Google Sheets) | Free | Detailed primary tracking, demographics |
| **Sabato's Crystal Ball** (RSS) | Free | Expert race ratings (competitive races only) |

### 6. Mentions ("how many times will X say Y") (6.3%)
| API | Free? | What it gives |
|---|---|---|
| (no good direct API) | — | These are fundamentally hard — depends on speaker style |

Could try **Twitter/X API** ($100/mo basic) or **Brand24** ($79/mo) for media-mention tracking but ROI unclear.

### 7. Crypto (3.7%) ← Claude does WORSE THAN CHANCE here
| API | Free? | What it gives |
|---|---|---|
| **CoinGecko API** | Free | Spot prices, market cap, volume |
| **CoinMarketCap API** | Free tier | Same |
| **Glassnode** | Paid | On-chain metrics |
| **Kalshi market itself** | Free | Implied probability is often the best signal here |

For crypto, the LLMs are bad — best strategy is **anchor heavily on Kalshi prices** (high α blend) since the market is more accurate than any model.

### 8. Climate/Weather (3.0%)
| API | Free? | What it gives |
|---|---|---|
| **OpenWeather One Call** | Free 1000/day | Current + forecast |
| **NOAA Climate Data** | Free | Historical + seasonal forecasts |
| **Climate Reanalyzer** | Free, scrape | Atmospheric data |
| **NWS API** (api.weather.gov) | Free, no key | Official US forecasts |

### 9. Financials / Economics (2.7% + 1.3%)
| API | Free? | What it gives |
|---|---|---|
| **FRED** (Fed economic data, fred.stlouisfed.org) | Free | Fed rate decisions, GDP, CPI, unemployment |
| **BEA** (Bureau of Econ Analysis) | Free | GDP details, trade data |
| **BLS** (Bureau of Labor Stats) | Free | Jobs report, CPI |
| **Treasury Direct** | Free | Auctions, yields |

### 10. World (2.0%)
| API | Free? | What it gives |
|---|---|---|
| **Reuters/AP via Tavily** | (already integrated) | International news |
| **ReliefWeb API** | Free | UN/NGO humanitarian situations |
| **ACLED** (acleddata.com) | Free academic | Armed conflict events |

### 11. Social, Sci/Tech (rare)
- **Reddit API** (free, OAuth) for Social/sentiment
- **Hacker News API** (free) for Sci/Tech
- **arXiv API** (free) for academic papers
- **GitHub API** (free) for software releases

## Recommended integration roadmap given budget+time

### Tonight (5 hours, $69 budget)
1. ✅ **Ballotpedia** (free, fixes 2 Election catastrophes — *just done*)
2. **The Odds API** ($25/mo, fixes 60% of events at margin — *highest aggregate lever*)
3. **ESPN Cricinfo scraper** (free, fixes our cricket failure — 30 min)

### Post-hackathon iteration
4. OpenFEC for political fundraising signal
5. ProPublica Congress for Senate vote questions
6. CoinGecko + market-anchor for Crypto
7. SEC EDGAR for Companies questions
8. FRED for Fed-rate-decision questions

### Skip / not worth tonight
- TMDB (low Brier exposure)
- Climate APIs (only 3% of events)
- Full OpenRouter web_search swap (4-6 hr refactor)
- Social / Sci/Tech APIs (each < 1.5% of events)

## Sanity-check via category yes-rates

The strongest per-category prior we can extract from KalshiBench (free, no API):

```python
KALSHIBENCH_YES_RATES = {
    "Sports":         0.349,
    "Politics":       0.527,
    "Entertainment":  0.362,
    "Companies":      0.600,
    "Elections":      0.208,   # most "did X win" questions are NO
    "Mentions":       0.368,
    "Crypto":         0.273,
    "Climate/Weather": 0.333,
    "Financials":     0.125,
    # World, Economics, Social, Sci/Tech — too few samples
}
```

This is the "uniform-by-category" baseline. We could anchor the model's prior to
this when the search returns no relevant evidence — a cheap, free improvement
that doesn't need any new API at all.
