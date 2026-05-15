"""Search for ex-ante filters that boost Jibang's filtered ROI without
destroying the universe. Group by Kalshi event prefix and analyze P&L per
group; identify groups with ambiguous-resolution semantics (polling, niche,
geopolitical, etc.)."""
import os, sys, re
from collections import defaultdict
from datetime import datetime, timezone
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

from main import (
    _build_kalshi_adapter, _fetch_raw_market, _parse_iso,
    get_db, BettingOrder, TradingMarket, TradingMarketLifecycle,
    InventoryPosition,
)
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
GAP_SEC = 3600
START = 475.0


def is_excluded(adapter, ticker):
    if "MENTION" in ticker.upper():
        return True
    market = _fetch_raw_market(adapter, ticker)
    if not market:
        return False
    close_t = _parse_iso(market.get("close_time"))
    actual = (
        _parse_iso(market.get("expected_expiration_time"))
        or _parse_iso(market.get("occurrence_datetime"))
    )
    if close_t and actual and (close_t - actual).total_seconds() > GAP_SEC:
        return True
    return False


def event_prefix(ticker: str) -> str:
    """Extract the Kalshi 'event family' prefix (e.g. KXNFLDRAFTPICK from
    KXNFLDRAFTPICK-26-3-DBAI)."""
    base = ticker.upper().split("-")[0]
    return base


def live_mark(adapter, ticker, side):
    try:
        market = _fetch_raw_market(adapter, ticker)
    except Exception:
        return None
    if not market:
        return None
    yes_bid = market.get("yes_bid")
    no_bid = market.get("no_bid")
    if side == "yes" and yes_bid is not None:
        return float(yes_bid) / 100.0
    if side == "no" and no_bid is not None:
        return float(no_bid) / 100.0
    return None


engine = get_db()
adapter = _build_kalshi_adapter(INSTANCE)
with get_session(engine) as session:
    rows = (
        session.query(BettingOrder, TradingMarket)
        .join(TradingMarket, BettingOrder.ticker == TradingMarket.ticker)
        .filter(
            BettingOrder.instance_name == INSTANCE,
            TradingMarket.instance_name == INSTANCE,
            BettingOrder.created_at >= CUTOFF,
            BettingOrder.status.in_(["FILLED", "DRY_RUN"]),
        )
        .all()
    )

    by_ticker = {}
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t:
            continue
        if is_excluded(adapter, t):
            continue
        by_ticker.setdefault(t, []).append((o, m))

    # Per-ticker net P&L (settled or live-marked open)
    per_ticker_pnl = {}
    per_ticker_orders = {}
    per_ticker_title = {}
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try:
                pos.apply_order(o, ticker=ticker)
            except Exception:
                pass
        side, qty, avg = pos.current_position()
        mkt0 = ords[0][1]
        per_ticker_title[ticker] = mkt0.title
        per_ticker_orders[ticker] = len(ords)
        net = pos.realized_pnl
        if side and qty > 1e-9:
            outcome = None
            lc = session.query(TradingMarketLifecycle).filter(
                TradingMarketLifecycle.market_id == mkt0.market_id,
                TradingMarketLifecycle.instance_name == INSTANCE,
            ).first()
            if lc and lc.result:
                r = str(lc.result).lower()
                if r == "yes": outcome = 1.0
                elif r == "no": outcome = 0.0
            if outcome is not None:
                settle = outcome if side == "yes" else 1.0 - outcome
                net += (settle - avg) * qty
            else:
                mark = live_mark(adapter, ticker, side)
                if mark is None:
                    last_px = mkt0.last_price
                    if last_px is not None and last_px > 1.0:
                        last_px /= 100.0
                    if last_px is not None:
                        mark = last_px if side == "yes" else 1.0 - last_px
                if mark is not None:
                    net += (mark - avg) * qty
        per_ticker_pnl[ticker] = net

    # Group by event prefix
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for ticker in per_ticker_pnl:
        by_prefix[event_prefix(ticker)].append(ticker)

    # Aggregate per-prefix
    prefix_stats = []
    for prefix, tickers in by_prefix.items():
        n_tickers = len(tickers)
        n_orders = sum(per_ticker_orders[t] for t in tickers)
        pnl = sum(per_ticker_pnl[t] for t in tickers)
        prefix_stats.append((prefix, n_tickers, n_orders, pnl, tickers))
    prefix_stats.sort(key=lambda r: r[3])  # losers first

    print(f"{'PREFIX':<28}  tickers  orders     P&L   sample title")
    print("-" * 110)
    for prefix, nt, no, pnl, tickers in prefix_stats:
        sample = per_ticker_title.get(tickers[0], "")[:60]
        print(f"  {prefix:<25} {nt:>4}    {no:>4}   {pnl:+8.2f}  {sample}")

    total_pnl = sum(per_ticker_pnl.values())
    total_orders = sum(per_ticker_orders.values())
    total_tickers = len(per_ticker_pnl)
    print(f"\nBaseline (MENTIONS+misspec only): tickers={total_tickers}, orders={total_orders}, "
          f"net P&L=${total_pnl:+.2f}, ROI=${total_pnl/START*100:+.2f}%")

    # ---- Try sample ex-ante filter rules ----
    # Rule A: drop "ambiguous polling / vote-tracking" prefixes
    rule_a_drop = {"KXVOTEHUBTRUMPUPDOWN", "KXVOTESAVEAMERICA"}
    # Rule B: drop "niche / source-of-truth ambiguous" prefixes
    rule_b_drop = {
        "KXMNDAYCARECHARGE",  # specific person daycare charge
        "KXCABLEAVE",          # cable show average viewer source ambiguity
        "KXNETFLIXRANKSHOW",   # ranking source ambiguity
        "KXALBUMRELEASEDATEUZI", "KXALBUMRELEASEDATETRAVIS",  # release-date ambiguity
        "KXSPOTIFYALBUMRELEASEDATEDRAKE",
        "KXKASHOUT",           # specific niche
        "KXLLM1",              # LLM-related event has internal labeling ambiguity
    }
    # Rule C: drop geopolitical "what counts" markets
    rule_c_drop = {
        "KXHORMUZTRAFFIC", "KXHORMUZNORM",  # Strait of Hormuz traffic — methodology/source ambiguity
        "KXFISAEXTEND",                       # FISA extension definition
        "KXDIAZOUT",                          # Diaz out — ambiguous condition
        "KXDEREMEROUT",
        "KXTRUMPPHOTO",
    }
    # Rule D: drop low-volume / single-ticker "long tail" prefixes (≤1 ticker, niche)
    long_tail_prefixes = {p for p, nt, _, _, _ in [(s[0], s[1], s[2], s[3], s[4]) for s in prefix_stats] if nt == 1}

    def roi_excluding(drop_set, label):
        kept_pnl = sum(p for t, p in per_ticker_pnl.items() if event_prefix(t) not in drop_set)
        kept_tickers = sum(1 for t in per_ticker_pnl if event_prefix(t) not in drop_set)
        kept_orders = sum(per_ticker_orders[t] for t in per_ticker_pnl if event_prefix(t) not in drop_set)
        dropped = total_tickers - kept_tickers
        dropped_orders = total_orders - kept_orders
        print(f"  {label:<48}  drop {dropped:>3} tickers / {dropped_orders:>3} orders  "
              f"=> tickers={kept_tickers}, orders={kept_orders}, "
              f"net=${kept_pnl:+8.2f}, ROI={kept_pnl/START*100:+6.2f}%")

    print("\nEx-ante filters:")
    roi_excluding(rule_a_drop, "A. polling/vote-tracking dropped")
    roi_excluding(rule_b_drop, "B. niche/source-ambiguous dropped")
    roi_excluding(rule_c_drop, "C. geopolitical-condition dropped")
    roi_excluding(rule_a_drop | rule_b_drop, "A+B")
    roi_excluding(rule_a_drop | rule_b_drop | rule_c_drop, "A+B+C")
    roi_excluding(long_tail_prefixes, "D. single-ticker long-tail dropped")
    roi_excluding(rule_a_drop | rule_b_drop | rule_c_drop | long_tail_prefixes, "A+B+C+D (all)")
