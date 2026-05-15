"""ROI excluding MENTIONS, close-time-mismatch markets, and any trades
placed within 2 days of market close."""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "services", "api"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "services"))

from dotenv import load_dotenv
load_dotenv()

from ai_prophet_core.betting.db import create_db_engine, get_session
from sqlalchemy import func

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
LATE_TRADE_WINDOW = timedelta(seconds=-1)  # disabled — only MENTIONS+misspec
MISSPEC_GAP_SEC = 3600

from main import (
    _build_kalshi_adapter,
    _is_misspecified_market,
    get_db,
    BettingOrder,
    TradingMarket,
    KalshiBalanceSnapshot,
    InventoryPosition,
    TradingMarketLifecycle,
)

engine = get_db()
adapter = _build_kalshi_adapter(INSTANCE)

with get_session(engine) as session:
    # Pull all filled orders + market expiration for the instance
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

    by_ticker_orders: dict[str, list] = {}
    skipped_late = 0
    skipped_misspec = 0
    for order, mkt in rows:
        ticker = order.ticker or mkt.ticker
        if not ticker:
            continue
        # Filter 1: MENTIONS + close-time mismatch
        if _is_misspecified_market(adapter, ticker):
            skipped_misspec += 1
            continue
        # Filter 2: trade within LATE_TRADE_WINDOW of market expiration
        # (disabled when LATE_TRADE_WINDOW is negative)
        if LATE_TRADE_WINDOW.total_seconds() >= 0:
            if mkt.expiration is None:
                continue
            exp = mkt.expiration if mkt.expiration.tzinfo else mkt.expiration.replace(tzinfo=timezone.utc)
            if exp - order.created_at <= LATE_TRADE_WINDOW:
                skipped_late += 1
                continue
        by_ticker_orders.setdefault(ticker, []).append((order, mkt))
    print(f"Skipped misspec orders: {skipped_misspec}")
    print(f"Skipped late-window orders: {skipped_late}")

    print(f"Tickers after filters: {len(by_ticker_orders)}")
    print(f"Orders after filters: {sum(len(v) for v in by_ticker_orders.values())}")

    # Realized P&L via simple FIFO replay per ticker
    total_realized = 0.0
    settled_count = 0
    open_unsettled = 0
    open_unrealized_at_mark = 0.0
    for ticker, ords in by_ticker_orders.items():
        ords.sort(key=lambda o: o[0].created_at)
        pos = InventoryPosition()
        for order, mkt in ords:
            try:
                pnl_impact = pos.apply_order(order, ticker=ticker)
                total_realized += pnl_impact
            except Exception as e:
                print(f"  {ticker}: {e}")

        side, qty, avg = pos.current_position()
        if side and qty > 1e-9:
            mkt0 = ords[0][1]
            outcome = None
            lc = session.query(TradingMarketLifecycle).filter(
                TradingMarketLifecycle.market_id == mkt0.market_id,
                TradingMarketLifecycle.instance_name == INSTANCE,
            ).first()
            if lc and lc.result:
                if str(lc.result).lower() == "yes":
                    outcome = 1.0
                elif str(lc.result).lower() == "no":
                    outcome = 0.0
            if outcome is not None:
                settle = outcome if side == "yes" else 1.0 - outcome
                total_realized += (settle - avg) * qty
                settled_count += 1
            else:
                open_unsettled += 1
                # Mark open position at last_price for indicative ROI
                last_px = mkt0.last_price
                if last_px is not None:
                    if last_px > 1.0:
                        last_px /= 100.0
                    mark = last_px if side == "yes" else 1.0 - last_px
                    open_unrealized_at_mark += (mark - avg) * qty

    print(f"Settled (resolved) positions: {settled_count}")
    print(f"Still-open positions: {open_unsettled}")
    print(f"Indicative unrealized on open positions: ${open_unrealized_at_mark:.2f}")

    print(f"\nRealized P&L (filtered, incl. settled): ${total_realized:.2f}")
    net_pnl = total_realized + open_unrealized_at_mark
    print(f"Net P&L incl. mark on open: ${net_pnl:.2f}")

    # Peak portfolio value from balance snapshots (for denominator)
    peak = (
        session.query(func.max(KalshiBalanceSnapshot.portfolio_value))
        .filter(
            KalshiBalanceSnapshot.instance_name == INSTANCE,
            KalshiBalanceSnapshot.snapshot_ts >= CUTOFF,
        )
        .scalar()
    )
    peak = float(peak or 0.0)
    import math
    bankroll = math.ceil(peak / 100.0) * 100 if peak > 0 else 200
    print(f"Peak portfolio value: ${peak:.2f}")
    print(f"Bankroll (ceil to $100): ${bankroll:.2f}")
    print(f"Filtered realized ROI on $200: {total_realized / bankroll * 100:.2f}%")
    print(f"Filtered realized ROI on $475: {total_realized / 475 * 100:.2f}%")
    print(f"Filtered net ROI (incl. open) on $200: {net_pnl / bankroll * 100:.2f}%")
    print(f"Filtered net ROI (incl. open) on $475: {net_pnl / 475 * 100:.2f}%")

    print("\n=== Per-resolved-ticker realized P&L (filtered set) ===")
    rows_pnl = []
    for ticker, ords in by_ticker_orders.items():
        ords.sort(key=lambda o: o[0].created_at)
        pos = InventoryPosition()
        for order, mkt in ords:
            try:
                pos.apply_order(order, ticker=ticker)
            except Exception:
                pass
        side, qty, avg = pos.current_position()
        mkt0 = ords[0][1]
        lc = session.query(TradingMarketLifecycle).filter(
            TradingMarketLifecycle.market_id == mkt0.market_id,
            TradingMarketLifecycle.instance_name == INSTANCE,
        ).first()
        outcome = None
        if lc and lc.result:
            if str(lc.result).lower() == "yes":
                outcome = 1.0
            elif str(lc.result).lower() == "no":
                outcome = 0.0
        if outcome is None:
            continue
        settle_pnl = pos.realized_pnl
        if side and qty > 1e-9:
            settle = outcome if side == "yes" else 1.0 - outcome
            settle_pnl += (settle - avg) * qty
        rows_pnl.append((ticker, mkt0.title, outcome, settle_pnl))
    rows_pnl.sort(key=lambda r: r[3])
    for ticker, title, outcome, pnl in rows_pnl:
        oc = "YES" if outcome == 1.0 else "NO"
        print(f"  {pnl:+7.2f}  [{oc}]  {ticker:<28}  {title[:80]}")
