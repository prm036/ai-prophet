"""Same-methodology cascade: compute per-market net P&L (realized + open
mark) for all 126 markets ONCE, then sum over each filter subset and
report ROI on the $475.43 baseline. The unfiltered total should match
the live API headline (+$112.06 / +23.57%)."""
import os, sys, json
from datetime import datetime, timezone
from collections import defaultdict
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

import requests
from main import (
    _build_kalshi_adapter, _fetch_raw_market, _parse_iso,
    get_db, BettingOrder, TradingMarket, TradingMarketLifecycle,
    InventoryPosition,
)
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
START = 475.43
GAP_SEC = 3600
API = "https://kalshi-trading-api.onrender.com"


def is_mentions(t): return "MENTION" in t.upper()
def is_topmodel(t): return t.upper().startswith("KXTOPMODEL")


def is_misspec(adapter, t):
    m = _fetch_raw_market(adapter, t)
    if not m: return False
    c = _parse_iso(m.get("close_time"))
    a = _parse_iso(m.get("expected_expiration_time")) or _parse_iso(m.get("occurrence_datetime"))
    return bool(c and a and (c-a).total_seconds() > GAP_SEC)


def live_mark(adapter, ticker, side):
    """Return live YES_bid or NO_bid as a fraction. Retries once."""
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


# Live headline check
balance_r = requests.get(f"{API}/kalshi/balance", params={"instance_name": INSTANCE}, timeout=30).json()
baseline_r = requests.get(f"{API}/display-baseline", params={"instance_name": INSTANCE}, timeout=30).json()
live_nav = float(balance_r["balance"]) + float(balance_r["portfolio_value"])
live_baseline = float(baseline_r["starting_total"])
live_roi = (live_nav - live_baseline) / live_baseline * 100
live_pnl = live_nav - live_baseline
print(f"Live API headline:  NAV ${live_nav:.2f}, baseline ${live_baseline:.2f}, P&L +${live_pnl:.2f}, ROI {live_roi:+.2f}%")

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
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome_by[lc.market_id] = 1.0
            elif r == "no": outcome_by[lc.market_id] = 0.0

    # Per-ticker P&L = realized + open_mark for ALL surviving orders on that
    # ticker. We tag each ticker with: drops MENTIONS / misspec / TOPMODEL,
    # and a per-order tag for "any order within 3h of close".
    by_ticker = defaultdict(list)
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None: continue
        by_ticker[t].append((o, m))

    per_ticker = {}
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        m0 = ords[0][1]
        exp = m0.expiration if m0.expiration.tzinfo else m0.expiration.replace(tzinfo=timezone.utc)

        # Tags
        tag_mentions = is_mentions(ticker)
        tag_topmodel = is_topmodel(ticker)
        tag_misspec = (not tag_mentions) and is_misspec(adapter, ticker)

        # Replay all orders on this ticker (with no filter) to get the
        # current per-market net P&L (realized + open mark).
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
        side, qty, avg = pos.current_position()
        net = pos.realized_pnl
        if side and qty > 1e-9:
            outcome = outcome_by.get(m0.market_id)
            if outcome is not None:
                settle_px = outcome if side == "yes" else 1.0 - outcome
                net += (settle_px - avg) * qty
            else:
                mark = live_mark(adapter, ticker, side)
                if mark is None:
                    last_px = m0.last_price
                    if last_px is not None and last_px > 1.0: last_px /= 100.0
                    if last_px is not None:
                        mark = last_px if side == "yes" else 1.0 - last_px
                if mark is not None:
                    net += (mark - avg) * qty

        # Also compute the "with 3h-cutoff" version: replay only orders
        # placed ≥3h before close
        pos_3h = InventoryPosition()
        n_orders_3h = 0
        for o, _ in ords:
            if (exp - o.created_at).total_seconds() > 3 * 3600:
                try: pos_3h.apply_order(o, ticker=ticker)
                except: pass
                n_orders_3h += 1
        net_3h = pos_3h.realized_pnl
        side3, qty3, avg3 = pos_3h.current_position()
        if side3 and qty3 > 1e-9:
            outcome = outcome_by.get(m0.market_id)
            if outcome is not None:
                settle_px = outcome if side3 == "yes" else 1.0 - outcome
                net_3h += (settle_px - avg3) * qty3
            else:
                mark = live_mark(adapter, ticker, side3)
                if mark is None:
                    last_px = m0.last_price
                    if last_px is not None and last_px > 1.0: last_px /= 100.0
                    if last_px is not None:
                        mark = last_px if side3 == "yes" else 1.0 - last_px
                if mark is not None:
                    net_3h += (mark - avg3) * qty3

        per_ticker[ticker] = {
            "n_orders": len(ords),
            "n_orders_3h_kept": n_orders_3h,
            "net_pnl": net,
            "net_pnl_3h_kept": net_3h,
            "tag_mentions": tag_mentions,
            "tag_misspec": tag_misspec,
            "tag_topmodel": tag_topmodel,
        }

    # Cascade
    def passes(t, drop_mentions=False, drop_misspec=False, drop_topmodel=False):
        d = per_ticker[t]
        if drop_mentions and d["tag_mentions"]: return False
        if drop_misspec and d["tag_misspec"]: return False
        if drop_topmodel and d["tag_topmodel"]: return False
        return True

    stages = [
        ("0. Unfiltered (all 126 markets)",
            False, False, False, False),
        ("1. + drop MENTIONS",
            True, False, False, False),
        ("2. + drop close-time-mismatch (>1h)",
            True, True, False, False),
        ("3. + drop KXTOPMODEL",
            True, True, True, False),
        ("4. + halt 3h pre-close (FULL)",
            True, True, True, True),
    ]

    print()
    print(f"{'Stage':<42} {'mkts':>5} {'P&L':>9} {'NAV':>9} {'ROI/$475':>10}")
    print("-" * 80)
    total_pnl_check = 0.0
    for label, drop_m, drop_mis, drop_tm, apply_3h in stages:
        kept_tickers = [t for t in per_ticker if passes(t, drop_m, drop_mis, drop_tm)]
        if apply_3h:
            net_total = sum(per_ticker[t]["net_pnl_3h_kept"] for t in kept_tickers)
        else:
            net_total = sum(per_ticker[t]["net_pnl"] for t in kept_tickers)
        nav = START + net_total
        roi = net_total / START * 100
        if label.startswith("0."):
            total_pnl_check = net_total
        print(f"{label:<42} {len(kept_tickers):>5} ${net_total:>+8.2f} ${nav:>8.2f} {roi:>+8.2f}%")

    print()
    print(f"Sanity: unfiltered per-market sum (${total_pnl_check:.2f}) vs live API NAV-P&L (${live_pnl:.2f}). "
          f"Diff: ${total_pnl_check - live_pnl:+.2f}")
    print("(Small diff is expected: API NAV uses Kalshi's reported portfolio_value;")
    print(" per-market replay uses live yes_bid/no_bid marks — different valuation.)")
