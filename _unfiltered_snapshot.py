"""Snapshot unfiltered Jibang state RIGHT NOW so we have a fixed reference
point. Records: balance, portfolio_value, baseline, NAV, ROI, plus all
filled orders + market-by-market P&L (no filtering applied)."""
import os, sys, json
from datetime import datetime, timezone
from collections import defaultdict
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

import requests
from main import (
    _build_kalshi_adapter, _fetch_raw_market,
    get_db, BettingOrder, TradingMarket, TradingMarketLifecycle,
    InventoryPosition,
)
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
API = "https://kalshi-trading-api.onrender.com"
OUT = "_unfiltered_snapshot.json"

# Pull live API state
balance_r = requests.get(f"{API}/kalshi/balance", params={"instance_name": INSTANCE}, timeout=30).json()
baseline_r = requests.get(f"{API}/display-baseline", params={"instance_name": INSTANCE}, timeout=30).json()
positions_r = requests.get(f"{API}/kalshi/positions", params={"instance_name": INSTANCE}, timeout=30).json()

balance = float(balance_r.get("balance", 0))
portfolio_value = float(balance_r.get("portfolio_value", 0))
nav = balance + portfolio_value
starting_total = float(baseline_r.get("starting_total", 0))
roi_pct = (nav - starting_total) / starting_total * 100 if starting_total else 0

print(f"Snapshot at {datetime.now(timezone.utc).isoformat()}")
print(f"Balance:         ${balance:.2f}")
print(f"Portfolio value: ${portfolio_value:.2f}")
print(f"Total NAV:       ${nav:.2f}")
print(f"Baseline:        ${starting_total:.2f}")
print(f"ROI:             {roi_pct:+.2f}%")

# Pull all orders + per-market P&L (unfiltered)
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

    # Resolve outcomes
    market_ids = list({m.market_id for _, m in rows})
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

    by_ticker = defaultdict(list)
    for o, m in rows:
        if o.ticker and m.expiration is not None:
            by_ticker[o.ticker].append((o, m))

    # Per-market replay
    per_market = []
    total_realized = 0.0
    total_open_mark = 0.0
    n_settled = 0; n_open = 0; n_won = 0; n_lost = 0
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
        side, qty, avg = pos.current_position()
        mkt0 = ords[0][1]
        net = pos.realized_pnl
        outcome = outcome_by_market.get(mkt0.market_id)
        position_state = "closed"
        kalshi_mark = None
        if side and qty > 1e-9:
            if outcome is not None:
                settle = outcome if side == "yes" else 1.0 - outcome
                net += (settle - avg) * qty
                position_state = "settled"
                n_settled += 1
                if net > 0: n_won += 1
                else: n_lost += 1
            else:
                position_state = f"open ({side.upper()} {qty:.0f}@{avg:.2f})"
                n_open += 1
                # Live mark
                m_raw = _fetch_raw_market(adapter, ticker)
                if m_raw:
                    yes_bid = m_raw.get("yes_bid"); no_bid = m_raw.get("no_bid")
                    if side == "yes" and yes_bid is not None:
                        kalshi_mark = float(yes_bid) / 100.0
                    elif side == "no" and no_bid is not None:
                        kalshi_mark = float(no_bid) / 100.0
                if kalshi_mark is not None:
                    net += (kalshi_mark - avg) * qty
                    total_open_mark += (kalshi_mark - avg) * qty
        else:
            if outcome is not None:
                position_state = f"closed ({'YES' if outcome == 1.0 else 'NO'})"
                if abs(pos.realized_pnl) > 1e-9:
                    n_settled += 1
                    if pos.realized_pnl > 0: n_won += 1
                    else: n_lost += 1
        if position_state.startswith("settled") or position_state.startswith("closed"):
            total_realized += pos.realized_pnl + (
                ((outcome if side == "yes" else 1.0 - outcome) - avg) * qty
                if (side and qty > 1e-9 and outcome is not None) else 0.0
            )

        per_market.append({
            "ticker": ticker,
            "market_id": mkt0.market_id,
            "title": mkt0.title,
            "category": mkt0.category,
            "expiration": mkt0.expiration.isoformat() if mkt0.expiration else None,
            "n_orders": len(ords),
            "outcome": ("YES" if outcome == 1.0 else "NO" if outcome == 0.0 else None),
            "position_state": position_state,
            "side_held": side,
            "qty_held": qty,
            "avg_cost": avg,
            "live_mark": kalshi_mark,
            "net_pnl": round(net, 4),
        })

    snapshot = {
        "snapshot_ts": datetime.now(timezone.utc).isoformat(),
        "instance": INSTANCE,
        "cutoff": CUTOFF.isoformat(),
        "live_account": {
            "balance": round(balance, 4),
            "portfolio_value": round(portfolio_value, 4),
            "total_nav": round(nav, 4),
            "starting_baseline": round(starting_total, 4),
            "roi_pct": round(roi_pct, 4),
        },
        "totals": {
            "n_filled_orders": len(rows),
            "n_markets": len(by_ticker),
            "n_settled": n_settled,
            "n_open": n_open,
            "n_won": n_won,
            "n_lost": n_lost,
            "win_rate_pct": round(n_won/(n_won+n_lost)*100, 2) if (n_won+n_lost) else None,
            "sum_per_market_pnl": round(sum(m["net_pnl"] for m in per_market), 4),
        },
        "markets": sorted(per_market, key=lambda x: x["net_pnl"]),
    }

    with open(OUT, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    print(f"\nSnapshot saved to {OUT}")
    print(f"Totals: {len(rows)} orders, {len(by_ticker)} markets, {n_settled} settled (W/L {n_won}/{n_lost}), {n_open} open")
    print(f"Sum of per-market P&L: ${snapshot['totals']['sum_per_market_pnl']:+.2f}")
