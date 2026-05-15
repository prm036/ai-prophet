"""For tickers in the filtered set with material P&L losses, fetch their
Kalshi resolution rules and inspect for ambiguity."""
import os, sys
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
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t:
            continue
        if is_excluded(adapter, t):
            continue
        by_ticker.setdefault(t, []).append((o, m))

    losers = []
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
        if net < -0.30:
            losers.append((ticker, net, mkt0.market_id))

    losers.sort(key=lambda r: r[1])

    # Skip KXDHSFUND (already analyzed)
    SKIP_PREFIXES = ("KXDHSFUND",)
    print(f"Material losers (excluding KXDHSFUND, already covered):\n")
    for ticker, pnl, market_id in losers:
        if any(ticker.startswith(p) for p in SKIP_PREFIXES):
            continue
        m = _fetch_raw_market(adapter, ticker)
        if not m:
            print(f"=== {ticker}  P&L=${pnl:+.2f}  (no market metadata)")
            continue
        title = m.get("title") or ""
        rules = m.get("rules_primary") or m.get("yes_sub_title") or ""
        result = m.get("result", "—")
        print(f"=== {ticker}  P&L=${pnl:+.2f}  result={result}")
        print(f"  title: {title}")
        if rules:
            print(f"  rules: {rules[:500]}")
        print()
