"""Sweep filter configs to find ones producing ROI ≥ 25%."""
import os, sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

from sqlalchemy import func
from main import (
    _build_kalshi_adapter, _fetch_raw_market, _parse_iso, _MISSPEC_CACHE,
    get_db, BettingOrder, TradingMarket, KalshiBalanceSnapshot,
    InventoryPosition, TradingMarketLifecycle,
)
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
START_CAPITAL = 475.0

# Sports / non-event prefixes that should be excluded regardless of API gap
SPORTS_PREFIXES = (
    "KXNBASERIES", "KXNBAGAME", "KXNHLSERIES", "KXNHLGAME", "KXNFL",
    "KXMLB", "KXNCAA", "KXBUNDESLIGAGAME", "KXEPL", "KXMLS",
    "KXATPMATCH", "KXITFMATCH", "KXWTAMATCH", "KXUFC", "KXTRUMPUFC",
    "KXSPRLVL",  # spread/level markets
    "KXNFLDRAFTPICK", "KXNFLDRAFTOU", "KXNFLDRAFTTOP",
    "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBGAME",
)

def is_excluded(adapter, ticker, gap_sec, exclude_sports=False):
    if not ticker:
        return True
    if "MENTION" in ticker.upper():
        return True
    if exclude_sports and any(ticker.upper().startswith(p) for p in SPORTS_PREFIXES):
        return True
    market = _fetch_raw_market(adapter, ticker)
    if not market:
        return False
    close_t = _parse_iso(market.get("close_time"))
    actual = (
        _parse_iso(market.get("expected_expiration_time"))
        or _parse_iso(market.get("occurrence_datetime"))
    )
    if close_t and actual:
        if (close_t - actual).total_seconds() > gap_sec:
            return True
    return False


def run_config(name, gap_sec, exclude_sports):
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
            if is_excluded(adapter, t, gap_sec, exclude_sports):
                continue
            by_ticker.setdefault(t, []).append((o, m))

        realized = 0.0
        unrealized = 0.0
        n_open = 0; n_settled = 0; n_won = 0; n_lost = 0
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
                if str(lc.result).lower() == "yes":
                    outcome = 1.0
                elif str(lc.result).lower() == "no":
                    outcome = 0.0
            if side and qty > 1e-9:
                if outcome is not None:
                    settle = outcome if side == "yes" else 1.0 - outcome
                    realized += (settle - avg) * qty
                    n_settled += 1
                    final_pnl = pos.realized_pnl + (settle - avg) * qty
                    if final_pnl > 0:
                        n_won += 1
                    else:
                        n_lost += 1
                else:
                    n_open += 1
                    last_px = mkt0.last_price
                    if last_px is not None:
                        if last_px > 1.0:
                            last_px /= 100.0
                        mark = last_px if side == "yes" else 1.0 - last_px
                        unrealized += (mark - avg) * qty
            else:
                # Closed without open position
                if abs(pos.realized_pnl) > 1e-9:
                    n_settled += 1
                    if pos.realized_pnl > 0:
                        n_won += 1
                    else:
                        n_lost += 1

        total_orders = sum(len(v) for v in by_ticker.values())
        n_tickers = len(by_ticker)
        net = realized + unrealized
        win_rate = (n_won / (n_won + n_lost) * 100) if (n_won + n_lost) else 0
        print(f"{name:<45} tickers={n_tickers:3d} orders={total_orders:3d} "
              f"settled={n_settled:2d} open={n_open:2d}  W/L={n_won}/{n_lost} ({win_rate:.1f}%)  "
              f"realized=${realized:7.2f}  unreal=${unrealized:7.2f}  "
              f"net=${net:7.2f}  ROI={net/START_CAPITAL*100:6.2f}%")


print(f"{'configuration':<45} {'tickers':>3} {'orders':>6} {'settled':>7} {'open':>4}  W/L  "
      f"realized  unreal     net       ROI")
print("-" * 175)
for gap_min in (1, 5, 15, 30, 60):
    for sports in (False, True):
        label = f"gap≤{gap_min}m, sports={'Y' if sports else 'N'}"
        run_config(label, gap_min * 60, sports)
