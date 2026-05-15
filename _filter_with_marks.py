"""Cascade filtered ROI computation INCLUDING open-position live marks.
Tries both: with and without the 3h-pre-close filter."""
import os, sys, math
from datetime import datetime, timezone, timedelta
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
START_300 = 300.0
START_475 = 475.43
GAP_SEC = 3600

engine = get_db()
adapter = _build_kalshi_adapter(INSTANCE)


def is_mentions(t): return "MENTION" in t.upper()


def is_misspec(t):
    m = _fetch_raw_market(adapter, t)
    if not m: return False
    c = _parse_iso(m.get("close_time"))
    a = _parse_iso(m.get("expected_expiration_time")) or _parse_iso(m.get("occurrence_datetime"))
    return bool(c and a and (c-a).total_seconds() > GAP_SEC)


def live_mark(ticker, side):
    m = _fetch_raw_market(adapter, ticker)
    if not m: return None
    yb, nb = m.get("yes_bid"), m.get("no_bid")
    if side == "yes" and yb is not None: return float(yb) / 100.0
    if side == "no" and nb is not None: return float(nb) / 100.0
    return None


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
    market_ids = list({m.market_id for _, m in rows})
    lcs = (
        session.query(TradingMarketLifecycle)
        .filter(
            TradingMarketLifecycle.instance_name == INSTANCE,
            TradingMarketLifecycle.market_id.in_(market_ids),
        )
        .all()
    )
    outcome_by = {}
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome_by[lc.market_id] = 1.0
            elif r == "no": outcome_by[lc.market_id] = 0.0


def replay(predicate):
    by_ticker = defaultdict(list)
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None: continue
        if not predicate(o, m): continue
        by_ticker[t].append((o, m))

    realized = 0.0; open_mark = 0.0
    n_settled = 0; n_open = 0; n_won = 0; n_lost = 0
    fill_dates = set()
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
            fill_dates.add(o.created_at.astimezone(timezone.utc).date())
        realized += pos.realized_pnl
        side, qty, avg = pos.current_position()
        m0 = ords[0][1]
        outcome = outcome_by.get(m0.market_id)
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
                mark = live_mark(ticker, side)
                if mark is not None:
                    open_mark += (mark - avg) * qty
        else:
            if abs(pos.realized_pnl) > 1e-9:
                n_settled += 1
                if pos.realized_pnl > 0: n_won += 1
                else: n_lost += 1
    net = realized + open_mark
    return {
        "n_markets": len(by_ticker), "n_orders": sum(len(v) for v in by_ticker.values()),
        "n_settled": n_settled, "n_open": n_open, "n_won": n_won, "n_lost": n_lost,
        "realized": realized, "open_mark": open_mark, "net": net,
        "days_traded": len(fill_dates),
    }


def f3h(o, m):
    if m.expiration is None: return False
    exp = m.expiration if m.expiration.tzinfo else m.expiration.replace(tzinfo=timezone.utc)
    return (exp - o.created_at).total_seconds() > 3 * 3600


stages = [
    ("0. Unfiltered", lambda o, m: True),
    ("1. + drop MENTIONS", lambda o, m: not is_mentions(o.ticker)),
    ("2. + drop close-time-mismatch (>1h)",
        lambda o, m: not is_mentions(o.ticker) and not is_misspec(o.ticker)),
    ("3. + drop KXTOPMODEL (NO 3h cutoff)",
        lambda o, m: not is_mentions(o.ticker) and not is_misspec(o.ticker)
                     and not o.ticker.upper().startswith("KXTOPMODEL")),
    ("4. + halt 3h pre-close (FULL FILTER)",
        lambda o, m: not is_mentions(o.ticker) and not is_misspec(o.ticker)
                     and not o.ticker.upper().startswith("KXTOPMODEL") and f3h(o, m)),
]

print(f"{'Stage':<42} {'mkts':>4} {'ords':>4} {'sett':>4} {'open':>4} {'W/L':>7} "
      f"{'realiz':>8} {'open_mk':>8} {'net':>8} {'/300':>8} {'/475':>8} {'days':>5}")
print("-" * 124)
for label, pred in stages:
    s = replay(pred)
    print(f"{label:<42} {s['n_markets']:>4} {s['n_orders']:>4} {s['n_settled']:>4} {s['n_open']:>4} "
          f"{s['n_won']}/{s['n_lost']:<5} ${s['realized']:>+7.2f} ${s['open_mark']:>+7.2f} ${s['net']:>+7.2f} "
          f"{s['net']/START_300*100:>+7.2f}% {s['net']/START_475*100:>+7.2f}% {s['days_traded']:>5}")
