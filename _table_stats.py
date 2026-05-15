"""Compute remaining Table 6 stats for the new filtered Jibang strategy."""
import os, sys, json, math
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
from db_models import ModelRun
from ai_prophet_core.betting.db_schema import BettingPrediction
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
GAP_SEC = 3600
LATE_HOURS = 3
DROP_PREFIXES = ("KXTOPMODEL",)
START = 300.0


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
    surviving_orders = []
    surviving_tickers = set()
    surviving_market_ids = set()
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None:
            continue
        if is_excluded(adapter, t):
            continue
        exp = m.expiration if m.expiration.tzinfo else m.expiration.replace(tzinfo=timezone.utc)
        if (exp - o.created_at).total_seconds() <= LATE_HOURS * 3600:
            continue
        surviving_orders.append((o, m, exp))
        surviving_tickers.add(t)
        surviving_market_ids.add(m.market_id)

    print(f"Filled orders (filtered): {len(surviving_orders)}")
    print(f"Markets traded:           {len(surviving_tickers)}")
    print(f"Unique market_ids:        {len(surviving_market_ids)}")

    shares_total = sum(float(getattr(o, "filled_shares", 0) or 0) for o, _, _ in surviving_orders)
    fees_total = sum(float(getattr(o, "fee_paid", 0) or 0) for o, _, _ in surviving_orders)
    print(f"Shares transacted:        {shares_total:.0f}")
    print(f"Exchange fees paid:       ${fees_total/100.0:.2f}  (raw cents={fees_total:.2f})")
    print(f"Exchange fees paid (USD): ${fees_total:.2f}  (if already in dollars)")

    # LLM forecasts issued = count of BettingPrediction rows for surviving market_ids
    # within the active trading window
    if surviving_orders:
        first_ts = min(o.created_at for o, _, _ in surviving_orders)
        last_ts = max(o.created_at for o, _, _ in surviving_orders)
    else:
        first_ts = last_ts = None
    pred_count = (
        session.query(BettingPrediction)
        .filter(
            BettingPrediction.instance_name == INSTANCE,
            BettingPrediction.market_id.in_(surviving_market_ids),
            BettingPrediction.created_at >= CUTOFF,
        )
        .count()
    )
    print(f"BettingPrediction rows on filtered markets: {pred_count}")

    # Trade signals emitted = count of ModelRun rows with non-skip decisions
    runs = (
        session.query(ModelRun)
        .filter(
            ModelRun.instance_name == INSTANCE,
            ModelRun.market_id.in_(surviving_market_ids),
            ModelRun.timestamp >= CUTOFF,
        )
        .all()
    )
    non_skip = [
        r for r in runs
        if r.decision and r.decision.upper() not in {"SKIP", "HOLD", "CYCLE_SKIPPED", "NONE"}
    ]
    print(f"ModelRun rows on filtered markets: {len(runs)} total, {len(non_skip)} non-skip (trade signals)")

    # Max drawdown from NAV series
    realized_so_far = 0.0
    positions = defaultdict(InventoryPosition)
    settled_set = set()
    market_for_ticker = {o.ticker: m for o, m, _ in surviving_orders}
    market_ids_for_lookup = list({m.market_id for _, m, _ in surviving_orders})
    lcs = (
        session.query(TradingMarketLifecycle)
        .filter(
            TradingMarketLifecycle.instance_name == INSTANCE,
            TradingMarketLifecycle.market_id.in_(market_ids_for_lookup),
        )
        .all()
    )
    outcome_by_market = {}
    settle_ts_by_market = {}
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes":
                outcome_by_market[lc.market_id] = 1.0
            elif r == "no":
                outcome_by_market[lc.market_id] = 0.0
            settle_ts_by_market[lc.market_id] = lc.updated_at

    surviving_orders.sort(key=lambda x: x[0].created_at)
    if not surviving_orders:
        sys.exit(0)
    first_day = surviving_orders[0][0].created_at.astimezone(timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    days = []
    d = first_day
    while d <= today:
        days.append(d); d += timedelta(days=1)

    nav_series = []
    next_idx = 0
    for d in days:
        day_end = datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)
        while next_idx < len(surviving_orders) and surviving_orders[next_idx][0].created_at <= day_end:
            o, m, _ = surviving_orders[next_idx]
            pos = positions[o.ticker]
            pre = pos.realized_pnl
            try: pos.apply_order(o, ticker=o.ticker)
            except Exception: pass
            realized_so_far += pos.realized_pnl - pre
            next_idx += 1
        for ticker, pos in positions.items():
            mkt = market_for_ticker.get(ticker)
            if mkt is None or mkt.market_id in settled_set:
                continue
            outcome = outcome_by_market.get(mkt.market_id)
            if outcome is None: continue
            settle_ts = settle_ts_by_market.get(mkt.market_id)
            if settle_ts and settle_ts > day_end: continue
            side, qty, avg = pos.current_position()
            if side and qty > 1e-9:
                settle_px = outcome if side == "yes" else 1.0 - outcome
                realized_so_far += (settle_px - avg) * qty
            settled_set.add(mkt.market_id)
        nav_series.append((d, START + realized_so_far))

    rets = []
    for i in range(1, len(nav_series)):
        prev = nav_series[i-1][1]; curr = nav_series[i][1]
        if prev > 0:
            rets.append((curr - prev) / prev)
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    sharpe = math.sqrt(365) * mean / std if std > 0 else None
    final_nav = nav_series[-1][1]

    # Max drawdown
    peak = nav_series[0][1]; max_dd = 0.0
    for _, v in nav_series:
        if v > peak: peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd: max_dd = dd

    print()
    print(f"=== Table values ===")
    print(f"Starting capital:        ${START:.2f}")
    print(f"Ending NAV:              ${final_nav:.2f}")
    print(f"ROI:                     {(final_nav-START)/START*100:+.2f}%")
    print(f"Mean daily return:       {mean*100:+.4f}%")
    print(f"Daily return volatility: {std*100:.4f}%")
    print(f"Sharpe ratio (√365):     {sharpe:.3f}")
    print(f"Max drawdown:            {max_dd:.2f}%")
    print()
    print(f"Markets traded:          {len(surviving_tickers)}")
    print(f"LLM forecasts issued:    {pred_count}")
    print(f"Trade signals emitted:   {len(non_skip)}")
    print(f"Filled orders:           {len(surviving_orders)}")
    print(f"Shares transacted:       {shares_total:.0f}")
    print(f"Exchange fees paid:      ${fees_total:.2f}  (raw)")
    # closed markets win rate (settled)
    settled = len(settled_set)
    wins = 0; losses = 0
    for ticker, pos in positions.items():
        mkt = market_for_ticker.get(ticker)
        if mkt is None or mkt.market_id not in settled_set: continue
        # Compute final pnl
        side, qty, avg = pos.current_position()
        outcome = outcome_by_market.get(mkt.market_id)
        net = pos.realized_pnl
        if side and qty > 1e-9 and outcome is not None:
            settle_px = outcome if side == "yes" else 1.0 - outcome
            net += (settle_px - avg) * qty
        if net > 0: wins += 1
        elif net < 0: losses += 1
    print(f"Win rate (closed):       {wins/(wins+losses)*100:.1f}% ({wins}/{wins+losses})  total settled={settled}")
