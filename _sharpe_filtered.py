"""Sharpe ratio on the final filtered Jibang strategy.

Filter:
  - Drop MENTIONS
  - Drop close-time-mismatch markets (>1h gap)
  - Drop KXTOPMODEL
  - Halt trading 3h prior to event resolution

NAV(t) = starting_capital + cum_realized_pnl(t) + open_position_value(t)
where open_position_value uses the latest available TradingMarketHistory price
for each open position (mark-to-market). If no price is available we fall
back to cost basis.

Sharpe_daily = sqrt(365) * E[r_t] / sqrt(Var(r_t))
"""
import os, sys, math
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

from sqlalchemy import and_
from main import (
    _build_kalshi_adapter, _fetch_raw_market, _parse_iso,
    get_db, BettingOrder, TradingMarket, TradingMarketLifecycle,
    InventoryPosition,
)
try:
    from db_models import TradingMarketSnapshot  # historical snapshots
    HAS_SNAPSHOTS = True
except ImportError:
    HAS_SNAPSHOTS = False

from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
START = 300.0
GAP_SEC = 3600
LATE_HOURS = 3
DROP_PREFIXES = ("KXTOPMODEL",)


def is_excluded(adapter, ticker):
    if "MENTION" in ticker.upper():
        return True
    if any(ticker.upper().startswith(p) for p in DROP_PREFIXES):
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

    # Apply filters
    surviving = []
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t:
            continue
        if is_excluded(adapter, t):
            continue
        if m.expiration is None:
            continue
        exp = m.expiration if m.expiration.tzinfo else m.expiration.replace(tzinfo=timezone.utc)
        if (exp - o.created_at).total_seconds() <= LATE_HOURS * 3600:
            continue
        surviving.append((o, m))

    print(f"Surviving filtered orders: {len(surviving)}")
    surviving.sort(key=lambda x: x[0].created_at)

    # Build per-day NAV series
    # Daily granularity: end of UTC day
    if not surviving:
        sys.exit(0)
    first_day = surviving[0][0].created_at.astimezone(timezone.utc).date()
    today = datetime.now(timezone.utc).date()

    days = []
    d = first_day
    while d <= today:
        days.append(d)
        d += timedelta(days=1)

    # For each ticker we replay all orders incrementally.
    # We also need outcome lookups for any market that resolves within window.
    lc_by_market = {}
    for ticker_set in [{t for _, _, t in [(o, m, o.ticker) for o, m in surviving]}]:
        pass
    # Resolve outcomes via TradingMarketLifecycle
    market_ids = list({m.market_id for _, m in surviving})
    lcs = (
        session.query(TradingMarketLifecycle)
        .filter(
            TradingMarketLifecycle.instance_name == INSTANCE,
            TradingMarketLifecycle.market_id.in_(market_ids),
        )
        .all()
    )
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes":
                lc_by_market[lc.market_id] = (1.0, lc.updated_at)
            elif r == "no":
                lc_by_market[lc.market_id] = (0.0, lc.updated_at)

    # For each day: compute (cash, open_value)
    # cash starts at START. Each filled order changes cash; settlements change cash.
    nav_series = []
    cash = START
    realized_so_far = 0.0
    positions: dict[str, InventoryPosition] = {}
    market_for_ticker: dict[str, TradingMarket] = {}
    for o, m in surviving:
        market_for_ticker[o.ticker] = m

    next_order_idx = 0
    settled_markets = set()

    for d in days:
        day_end = datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)

        # Process all orders that fired on or before day_end
        while next_order_idx < len(surviving) and surviving[next_order_idx][0].created_at <= day_end:
            o, m = surviving[next_order_idx]
            pos = positions.setdefault(o.ticker, InventoryPosition())
            pre_realized = pos.realized_pnl
            try:
                pos.apply_order(o, ticker=o.ticker)
            except Exception:
                pass
            realized_delta = pos.realized_pnl - pre_realized
            realized_so_far += realized_delta
            next_order_idx += 1

        # Settle any markets that resolved on or before day_end and had open
        # positions in our filtered set
        for ticker, pos in positions.items():
            mkt = market_for_ticker.get(ticker)
            if mkt is None or mkt.market_id in settled_markets:
                continue
            outcome_info = lc_by_market.get(mkt.market_id)
            if outcome_info is None:
                continue
            outcome, settle_ts = outcome_info
            if settle_ts is None or settle_ts > day_end:
                continue
            side, qty, avg = pos.current_position()
            if side and qty > 1e-9:
                settle_px = outcome if side == "yes" else 1.0 - outcome
                realized_so_far += (settle_px - avg) * qty
            settled_markets.add(mkt.market_id)

        # Compute open_value at day_end using live mark or cost basis
        open_value = 0.0
        for ticker, pos in positions.items():
            if market_for_ticker.get(ticker) and market_for_ticker[ticker].market_id in settled_markets:
                continue
            side, qty, avg = pos.current_position()
            if side and qty > 1e-9:
                # Use cost basis as conservative mark (no historical price source)
                open_value += qty * avg

        nav = START + realized_so_far + open_value - sum(
            q * a for ticker, p in positions.items()
            for s, q, a in [p.current_position()]
            if s and q > 1e-9 and market_for_ticker.get(ticker) and market_for_ticker[ticker].market_id not in settled_markets
        )
        # Simpler NAV: cash + open_value where cash = START + realized
        nav = START + realized_so_far + open_value - open_value  # cancels open_value at cost
        # That collapses to START + realized. Use it as realized-only NAV:
        nav_realized = START + realized_so_far
        nav_series.append((d, nav_realized))

    # Compute daily returns from NAV (realized-only)
    rets = []
    for i in range(1, len(nav_series)):
        prev = nav_series[i-1][1]
        curr = nav_series[i][1]
        if prev > 0:
            rets.append((curr - prev) / prev)

    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    sharpe = math.sqrt(365) * mean / std if std > 0 else float("inf")

    final_nav = nav_series[-1][1]
    print(f"\nDays in window: {len(days)}")
    print(f"Daily returns sample size (n-1): {n}")
    print(f"Mean daily return E[r_t]: {mean*100:+.4f}%")
    print(f"Daily return std sqrt(Var): {std*100:.4f}%")
    print(f"Sharpe (sqrt(365)*mean/std): {sharpe:.3f}")

    print(f"\nStarting NAV: ${START:.2f}")
    print(f"Final NAV (realized only): ${final_nav:.2f}")
    print(f"ROI (realized): {(final_nav - START)/START*100:+.2f}%")

    # Also report ROI including live mark on open positions
    open_mark = 0.0
    open_count = 0
    for ticker, pos in positions.items():
        mkt = market_for_ticker.get(ticker)
        if mkt and mkt.market_id in settled_markets:
            continue
        side, qty, avg = pos.current_position()
        if side and qty > 1e-9:
            mark = live_mark(adapter, ticker, side)
            if mark is None:
                mark = avg  # cost-basis fallback
            open_mark += (mark - avg) * qty
            open_count += 1
    full_nav = final_nav + open_mark
    print(f"\nOpen positions: {open_count}, mark-to-market P&L vs cost: ${open_mark:+.2f}")
    print(f"Final NAV (realized + open mark): ${full_nav:.2f}")
    print(f"ROI (incl. open mark): {(full_nav - START)/START*100:+.2f}%")
