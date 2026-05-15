"""Jibang ROI with strict filter: drop MENTIONS + close-time-mismatch markets.
Use live Kalshi prices for marking open positions."""
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
START = 475.0
GAP_SEC = 3600  # 1 hour close-time-vs-actual-event gap

def is_excluded(adapter, ticker):
    """Drop if MENTIONS or close_time exceeds expected_expiration_time / occurrence_datetime by GAP_SEC."""
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


def live_mid(adapter, ticker, side):
    """Mark open position at the live YES bid for YES side / live NO bid for NO side."""
    try:
        market = _fetch_raw_market(adapter, ticker)
    except Exception:
        return None
    if not market:
        return None
    # Kalshi prices in cents
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
    print(f"Total filled orders since cutoff: {len(rows)}")

    by_ticker: dict[str, list] = {}
    n_excluded_orders = 0
    excluded_tickers = set()
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t:
            continue
        if is_excluded(adapter, t):
            n_excluded_orders += 1
            excluded_tickers.add(t)
            continue
        by_ticker.setdefault(t, []).append((o, m))

    print(f"Excluded {len(excluded_tickers)} tickers / {n_excluded_orders} orders (MENTIONS + close-time mismatch)")
    print(f"Surviving: {len(by_ticker)} tickers / {sum(len(v) for v in by_ticker.values())} orders")

    realized = 0.0
    open_marked = 0.0
    n_settled = 0; n_open = 0; n_won = 0; n_lost = 0
    open_details = []

    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try:
                pos.apply_order(o, ticker=ticker)
            except Exception:
                pass
        realized += pos.realized_pnl
        side, qty, avg = pos.current_position()
        mkt0 = ords[0][1]

        outcome = None
        lc = session.query(TradingMarketLifecycle).filter(
            TradingMarketLifecycle.market_id == mkt0.market_id,
            TradingMarketLifecycle.instance_name == INSTANCE,
        ).first()
        if lc and lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome = 1.0
            elif r == "no": outcome = 0.0

        if side and qty > 1e-9:
            if outcome is not None:
                settle = outcome if side == "yes" else 1.0 - outcome
                realized += (settle - avg) * qty
                n_settled += 1
                fp = pos.realized_pnl + (settle - avg) * qty
                if fp > 0: n_won += 1
                else: n_lost += 1
            else:
                n_open += 1
                mark = live_mid(adapter, ticker, side)
                if mark is None:
                    last_px = mkt0.last_price
                    if last_px is not None and last_px > 1.0:
                        last_px /= 100.0
                    mark = last_px if side == "yes" else (1.0 - last_px if last_px is not None else None)
                if mark is not None:
                    pnl_open = (mark - avg) * qty
                    open_marked += pnl_open
                    open_details.append((ticker, side, qty, avg, mark, pnl_open))
        else:
            if abs(pos.realized_pnl) > 1e-9:
                n_settled += 1
                if pos.realized_pnl > 0: n_won += 1
                else: n_lost += 1

    net = realized + open_marked
    print()
    print(f"Settled positions: {n_settled}  W/L = {n_won}/{n_lost} ({n_won/(n_won+n_lost)*100 if (n_won+n_lost) else 0:.1f}%)")
    print(f"Still-open positions: {n_open}")
    if open_details:
        print("Open positions (live-marked):")
        for ticker, side, qty, avg, mark, p in open_details:
            print(f"  {ticker:<35} {side.upper():3} qty={qty:6.1f} avg={avg:.3f} mark={mark:.3f}  P&L=${p:+.2f}")

    print()
    print(f"Realized P&L (incl. settled exits): ${realized:+.2f}")
    print(f"Open position mark-to-market:        ${open_marked:+.2f}")
    print(f"Net P&L (filtered):                  ${net:+.2f}")
    print()
    print(f"ROI on $475 starting capital:")
    print(f"  realized only:  {realized/START*100:+6.2f}%")
    print(f"  net incl. open: {net/START*100:+6.2f}%")
