"""Sharpe ratio on the 3-filter set (no 3h cutoff), $200 budget basis.
NAV(t) = 200 + cum_realized(t) + open_mark(t at end-of-day)."""
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
START = 200.0
GAP_SEC = 3600


def is_mentions(t): return "MENTION" in t.upper()
def is_topmodel(t): return t.upper().startswith("KXTOPMODEL")


def is_misspec(adapter, t):
    m = _fetch_raw_market(adapter, t)
    if not m: return False
    c = _parse_iso(m.get("close_time"))
    a = _parse_iso(m.get("expected_expiration_time")) or _parse_iso(m.get("occurrence_datetime"))
    return bool(c and a and (c-a).total_seconds() > GAP_SEC)


def live_mark(adapter, ticker, side):
    for _ in range(2):
        try:
            m = _fetch_raw_market(adapter, ticker)
            if m:
                yb, nb = m.get("yes_bid"), m.get("no_bid")
                if side == "yes" and yb is not None: return float(yb) / 100.0
                if side == "no" and nb is not None: return float(nb) / 100.0
        except Exception:
            pass
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
    settle_ts_by = {}
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome_by[lc.market_id] = 1.0
            elif r == "no": outcome_by[lc.market_id] = 0.0
            settle_ts_by[lc.market_id] = lc.updated_at

    surviving = []
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None: continue
        if is_mentions(t): continue
        if is_topmodel(t): continue
        if is_misspec(adapter, t): continue
        surviving.append((o, m))
    surviving.sort(key=lambda x: x[0].created_at)

    market_for_ticker = {o.ticker: m for o, m in surviving}

    # Cache live marks once per open ticker
    open_ticker_marks: dict[str, float] = {}
    def get_mark(ticker, side):
        key = (ticker, side)
        if key not in open_ticker_marks:
            mk = live_mark(adapter, ticker, side)
            if mk is None:
                m0 = market_for_ticker.get(ticker)
                if m0 and m0.last_price is not None:
                    lp = m0.last_price
                    if lp > 1.0: lp /= 100.0
                    mk = lp if side == "yes" else 1.0 - lp
            open_ticker_marks[key] = mk
        return open_ticker_marks[key]

    # Daily NAV series: NAV(t) = START + cum_realized(t) + open_mark(t)
    if not surviving:
        sys.exit(0)
    first_day = surviving[0][0].created_at.astimezone(timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    days = []
    d = first_day
    while d <= today:
        days.append(d); d += timedelta(days=1)

    cum_realized = 0.0
    positions = defaultdict(InventoryPosition)
    settled_set = set()
    next_idx = 0
    nav_series = []

    for d in days:
        day_end = datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)
        # Apply orders up to day end
        while next_idx < len(surviving) and surviving[next_idx][0].created_at <= day_end:
            o, m = surviving[next_idx]
            pos = positions[o.ticker]
            pre = pos.realized_pnl
            try: pos.apply_order(o, ticker=o.ticker)
            except: pass
            cum_realized += pos.realized_pnl - pre
            next_idx += 1
        # Apply settlements that resolved on or before day_end
        for ticker, pos in positions.items():
            mkt = market_for_ticker.get(ticker)
            if mkt is None or mkt.market_id in settled_set: continue
            outcome = outcome_by.get(mkt.market_id)
            if outcome is None: continue
            settle_ts = settle_ts_by.get(mkt.market_id)
            if settle_ts and settle_ts > day_end: continue
            side, qty, avg = pos.current_position()
            if side and qty > 1e-9:
                settle_px = outcome if side == "yes" else 1.0 - outcome
                cum_realized += (settle_px - avg) * qty
            settled_set.add(mkt.market_id)
        # Compute open mark at day_end (use today's mark — the script's
        # conservative proxy for end-of-day mark)
        open_mark = 0.0
        for ticker, pos in positions.items():
            mkt = market_for_ticker.get(ticker)
            if mkt is None or mkt.market_id in settled_set: continue
            side, qty, avg = pos.current_position()
            if not (side and qty > 1e-9): continue
            mk = get_mark(ticker, side)
            if mk is not None:
                open_mark += (mk - avg) * qty
        nav_series.append((d, START + cum_realized + open_mark))

    rets = []
    for i in range(1, len(nav_series)):
        prev = nav_series[i-1][1]; curr = nav_series[i][1]
        if prev > 0: rets.append((curr - prev) / prev)
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    sharpe = math.sqrt(365) * mean / std if std > 0 else None
    final = nav_series[-1][1]
    print(f"3-filter set (no 3h cutoff), $200 budget basis")
    print(f"  Calendar days:        {len(days)}")
    print(f"  Daily return obs:     {n}")
    print(f"  Starting NAV:         ${START:.2f}")
    print(f"  Ending NAV:           ${final:.2f}")
    print(f"  Net P&L:              ${final - START:+.2f}")
    print(f"  ROI:                  {(final - START)/START*100:+.2f}%")
    print(f"  Mean daily return:    {mean*100:+.4f}%")
    print(f"  σ(daily return):      {std*100:.4f}%")
    print(f"  Sharpe (√365):        {sharpe:.3f}" if sharpe is not None else "Sharpe: undefined")
    # Max drawdown
    peak_nav = nav_series[0][1]; max_dd = 0.0
    for _, v in nav_series:
        if v > peak_nav: peak_nav = v
        dd = (v - peak_nav) / peak_nav * 100
        if dd < max_dd: max_dd = dd
    print(f"  Max drawdown:         {max_dd:.2f}%")
    days_traded = len({o.created_at.astimezone(timezone.utc).date() for o, _ in surviving})
    print(f"  Days actually traded: {days_traded}")
