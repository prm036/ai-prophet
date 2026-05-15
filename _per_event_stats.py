"""Per-market (per-event) return statistics for the filtered Jibang strategy.

For each market in the filtered set:
  return_m = market_pnl / cost_basis
We then report:
  - mean per-market return
  - std of per-market returns
  - per-market Sharpe = mean / std
  - dollar-weighted mean (Σ pnl / Σ cost)
"""
import os, sys, math
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

    by_ticker = defaultdict(list)
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None:
            continue
        if is_excluded(adapter, t):
            continue
        exp = m.expiration if m.expiration.tzinfo else m.expiration.replace(tzinfo=timezone.utc)
        if (exp - o.created_at).total_seconds() <= LATE_HOURS * 3600:
            continue
        by_ticker[t].append((o, m, exp))

    # Resolve outcomes
    market_ids = list({m.market_id for ords in by_ticker.values() for _, m, _ in ords})
    lcs = (
        session.query(TradingMarketLifecycle)
        .filter(
            TradingMarketLifecycle.instance_name == INSTANCE,
            TradingMarketLifecycle.market_id.in_(market_ids),
        )
        .all()
    )
    outcome_by_market = {}
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome_by_market[lc.market_id] = 1.0
            elif r == "no": outcome_by_market[lc.market_id] = 0.0

    per_event = []  # (return, pnl, cost, ticker)
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        total_buy_cost = 0.0
        for o, _, _ in ords:
            try:
                pos.apply_order(o, ticker=ticker)
            except Exception:
                pass
        # Use total buy cost as the "capital deployed" denominator
        total_buy_cost = pos.total_buy_cost
        side, qty, avg = pos.current_position()
        mkt0 = ords[0][1]
        net = pos.realized_pnl
        outcome = outcome_by_market.get(mkt0.market_id)
        if side and qty > 1e-9:
            if outcome is not None:
                settle = outcome if side == "yes" else 1.0 - outcome
                net += (settle - avg) * qty
            else:
                mark = live_mark(adapter, ticker, side)
                if mark is None:
                    last_px = mkt0.last_price
                    if last_px is not None and last_px > 1.0:
                        last_px /= 100.0
                    if last_px is not None:
                        mark = last_px if side == "yes" else 1.0 - last_px
                if mark is not None:
                    net += (mark - avg) * qty
        if total_buy_cost > 1e-9:
            per_event.append((net / total_buy_cost, net, total_buy_cost, ticker))

    n = len(per_event)
    rets = [r for r, _, _, _ in per_event]
    pnls = [p for _, p, _, _ in per_event]
    costs = [c for _, _, c, _ in per_event]
    mean = sum(rets) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    dollar_weighted = sum(pnls) / sum(costs) if costs else 0.0

    print(f"Number of markets (events): {n}")
    print(f"Total cost basis deployed:  ${sum(costs):.2f}")
    print(f"Total net P&L:              ${sum(pnls):+.2f}")
    print()
    print(f"Equal-weighted per-event return:")
    print(f"  E[r_event]            = {mean*100:+.2f}%")
    print(f"  sqrt(Var(r_event))    = {std*100:.2f}%")
    print(f"  Per-event Sharpe (mean/std) = {mean/std:.3f}" if std > 0 else "")
    print()
    print(f"Dollar-weighted return = Σ pnl / Σ cost = {dollar_weighted*100:+.2f}%")
    print()
    print("Top 5 winners and losers (per-event return):")
    per_event.sort(key=lambda x: x[0])
    for r, p, c, t in per_event[:5]:
        print(f"  {r*100:+7.1f}%  pnl={p:+7.2f}  cost={c:6.2f}  {t}")
    print("  ...")
    for r, p, c, t in per_event[-5:]:
        print(f"  {r*100:+7.1f}%  pnl={p:+7.2f}  cost={c:6.2f}  {t}")
