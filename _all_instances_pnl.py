"""Compute realized + unrealized P&L for each instance with MENTIONS+misspec filter."""
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

CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
START = 475.0
GAP_SEC = 3600

def is_misspec(adapter, ticker):
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


for instance in ["Jibang", "Haifeng", "GPT5", "Grok4", "Opus46"]:
    engine = get_db()
    try:
        adapter = _build_kalshi_adapter(instance)
    except Exception as e:
        print(f"{instance}: no adapter ({e})")
        adapter = None
    with get_session(engine) as session:
        rows = (
            session.query(BettingOrder, TradingMarket)
            .join(TradingMarket, BettingOrder.ticker == TradingMarket.ticker)
            .filter(
                BettingOrder.instance_name == instance,
                TradingMarket.instance_name == instance,
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
            if adapter and is_misspec(adapter, t):
                continue
            by_ticker.setdefault(t, []).append((o, m))

        realized = 0.0; unrealized = 0.0
        n_settled = 0; n_open = 0; n_won = 0; n_lost = 0
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
                TradingMarketLifecycle.instance_name == instance,
            ).first()
            if lc and lc.result:
                if str(lc.result).lower() == "yes":
                    outcome = 1.0
                elif str(lc.result).lower() == "no":
                    outcome = 0.0
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
                    last_px = mkt0.last_price
                    if last_px is not None:
                        if last_px > 1.0:
                            last_px /= 100.0
                        mark = last_px if side == "yes" else 1.0 - last_px
                        unrealized += (mark - avg) * qty
            else:
                if abs(pos.realized_pnl) > 1e-9:
                    n_settled += 1
                    if pos.realized_pnl > 0: n_won += 1
                    else: n_lost += 1
        net = realized + unrealized
        n_orders = sum(len(v) for v in by_ticker.values())
        wr = n_won/(n_won+n_lost)*100 if (n_won+n_lost) else 0
        print(f"{instance:<10} tickers={len(by_ticker):3d}  orders={n_orders:4d}  "
              f"settled={n_settled:3d} open={n_open:2d}  W/L={n_won}/{n_lost} ({wr:.1f}%)  "
              f"realized=${realized:8.2f}  unreal=${unrealized:7.2f}  "
              f"net=${net:8.2f}  ROI(/$475)={net/START*100:+6.2f}%")
