"""Compute ROI under several ex-ante targeted filter rules
based on the loss-by-event-prefix analysis."""
import os, sys
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


def event_prefix(ticker):
    return ticker.upper().split("-")[0]


def live_mark(adapter, ticker, side):
    try:
        market = _fetch_raw_market(adapter, ticker)
    except Exception:
        return None
    if not market:
        return None
    yes_bid = market.get("yes_bid"); no_bid = market.get("no_bid")
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
    cat_by_ticker = {}
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t:
            continue
        if is_excluded(adapter, t):
            continue
        by_ticker.setdefault(t, []).append((o, m))
        cat_by_ticker[t] = m.category

    # Per-ticker net P&L (settled or live-marked open)
    per_ticker_pnl = {}
    per_ticker_orders = {}
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

    total_pnl = sum(per_ticker_pnl.values())
    total_orders = sum(per_ticker_orders.values())
    total_tickers = len(per_ticker_pnl)
    print(f"Baseline: {total_tickers} tickers, {total_orders} orders, net=${total_pnl:+.2f}, ROI={total_pnl/START*100:+.2f}%")

    # Ex-ante filter rules with motivated justifications
    RULES = {
        "F1: Drop KXDHSFUND (legislative-deadline ambiguity: CR vs full bill, partial funding)":
            {"KXDHSFUND"},
        "F2: F1 + drop KXTOPMODEL (depends on a single LLM-leaderboard methodology that can shift)":
            {"KXDHSFUND", "KXTOPMODEL"},
        "F3: F2 + drop KXFISAEXTEND (legislative deadline same class as DHSFUND)":
            {"KXDHSFUND", "KXTOPMODEL", "KXFISAEXTEND"},
        "F4: F3 + ranking-source markets (KXNETFLIXRANKSHOW, KXTOPSONG, KXRANKLISTSONGSPOTUSA)":
            {"KXDHSFUND", "KXTOPMODEL", "KXFISAEXTEND",
             "KXNETFLIXRANKSHOW", "KXTOPSONG", "KXRANKLISTSONGSPOTUSA"},
        "F5: F4 + album-release-date markets (date can slip → ambiguous official date)":
            {"KXDHSFUND", "KXTOPMODEL", "KXFISAEXTEND",
             "KXNETFLIXRANKSHOW", "KXTOPSONG", "KXRANKLISTSONGSPOTUSA",
             "KXALBUMRELEASEDATEUZI", "KXALBUMRELEASEDATETRAVIS",
             "KXSPOTIFYALBUMRELEASEDATEDRAKE"},
    }

    print()
    print(f"{'Rule':<88}  {'tickers':>7} {'orders':>6}  {'net':>9}  {'ROI/$475':>9}")
    for label, drop in RULES.items():
        kept_pnl = sum(p for t, p in per_ticker_pnl.items() if event_prefix(t) not in drop)
        kept_tickers = sum(1 for t in per_ticker_pnl if event_prefix(t) not in drop)
        kept_orders = sum(per_ticker_orders[t] for t in per_ticker_pnl if event_prefix(t) not in drop)
        dropped_tickers = total_tickers - kept_tickers
        dropped_orders = total_orders - kept_orders
        print(f"  {label:<86}  -{dropped_tickers:>3}t/-{dropped_orders:>3}o  "
              f"keep={kept_tickers}/{kept_orders}  net=${kept_pnl:+8.2f}  ROI={kept_pnl/START*100:+6.2f}%")
