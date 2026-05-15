"""Peak simultaneous capital deployed across the filtered ticker set.

Replays all orders chronologically, tracking open cost basis per ticker,
and reports the max sum of cost bases at any single moment.
"""
import os, sys
from datetime import datetime, timezone
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

from main import (
    _build_kalshi_adapter, _fetch_raw_market, _parse_iso,
    get_db, BettingOrder, TradingMarket, InventoryPosition,
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

    # Build chronological order stream over filtered tickers
    filtered = []
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t:
            continue
        if is_excluded(adapter, t):
            continue
        filtered.append((o, m, t))
    filtered.sort(key=lambda x: x[0].created_at)
    print(f"Filtered orders: {len(filtered)}")

    # Replay, tracking per-ticker InventoryPosition; recompute total deployed
    # cost-basis sum after each order; remember max.
    positions: dict[str, InventoryPosition] = {}
    peak_deployed = 0.0
    peak_ts = None
    peak_breakdown: list[tuple[str, str, float, float, float]] = []

    for o, m, ticker in filtered:
        pos = positions.setdefault(ticker, InventoryPosition())
        try:
            pos.apply_order(o, ticker=ticker)
        except Exception:
            pass

        # Sum cost basis of ALL currently-open positions
        deployed = 0.0
        breakdown = []
        for tk, p in positions.items():
            side, qty, avg = p.current_position()
            if side and qty > 1e-9:
                cost = qty * avg
                deployed += cost
                breakdown.append((tk, side, qty, avg, cost))
        if deployed > peak_deployed:
            peak_deployed = deployed
            peak_ts = o.created_at
            peak_breakdown = breakdown

    print(f"\nPeak simultaneous capital deployed (filtered set): ${peak_deployed:.2f}")
    print(f"At: {peak_ts}")
    print(f"\nOpen positions at that moment ({len(peak_breakdown)} tickers):")
    for tk, side, qty, avg, cost in sorted(peak_breakdown, key=lambda r: -r[4]):
        print(f"  {tk:<40} {side.upper():3} qty={qty:6.1f} avg={avg:.3f}  cost=${cost:7.2f}")
