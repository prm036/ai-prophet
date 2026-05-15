"""Final filter: remove MENTIONS, close-time-mismatch, KXTOPMODEL, and
within-2h-of-close trades. Compute ROI / $300."""
import os, sys
from datetime import datetime, timezone
from collections import defaultdict
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
DENOM = 300.0
GAP_SEC = 600
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

    raw_total = len(rows)
    surviving = []
    excluded_misspec_or_topmodel = 0
    excluded_late = 0
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t:
            continue
        if is_excluded(adapter, t):
            excluded_misspec_or_topmodel += 1
            continue
        if m.expiration is None:
            continue
        exp = m.expiration if m.expiration.tzinfo else m.expiration.replace(tzinfo=timezone.utc)
        time_to_close = (exp - o.created_at).total_seconds()
        if time_to_close <= LATE_HOURS * 3600:
            excluded_late += 1
            continue
        surviving.append((o, m))

    print(f"Raw filled orders since cutoff: {raw_total}")
    print(f"Excluded (MENTIONS + misspec + KXTOPMODEL): {excluded_misspec_or_topmodel}")
    print(f"Excluded (within {LATE_HOURS}h of close): {excluded_late}")
    print(f"Surviving: {len(surviving)} trades across {len(set(o.ticker for o,_ in surviving))} tickers")

    by_ticker = defaultdict(list)
    for o, m in surviving:
        by_ticker[o.ticker].append((o, m))

    realized = 0.0; unreal = 0.0
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
                realized += (settle - avg) * qty
                n_settled += 1
                fp = pos.realized_pnl + (settle - avg) * qty
                if fp > 0: n_won += 1
                else: n_lost += 1
            else:
                n_open += 1
                mark = live_mark(adapter, ticker, side)
                if mark is None:
                    last_px = mkt0.last_price
                    if last_px is not None and last_px > 1.0:
                        last_px /= 100.0
                    if last_px is not None:
                        mark = last_px if side == "yes" else 1.0 - last_px
                if mark is not None:
                    unreal += (mark - avg) * qty
        else:
            if abs(pos.realized_pnl) > 1e-9:
                n_settled += 1
                if pos.realized_pnl > 0: n_won += 1
                else: n_lost += 1

    net = realized + unreal
    print(f"\nW/L (settled): {n_won}/{n_lost}  open: {n_open}")
    print(f"Realized: ${realized:+.2f}")
    print(f"Unrealized (live-marked open): ${unreal:+.2f}")
    print(f"Net P&L: ${net:+.2f}")
    print(f"\nROI on ${DENOM:.0f} denominator: {net/DENOM*100:+.2f}%")
