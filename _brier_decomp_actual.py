"""Brier ΔS + D decomposition of the LIVE strategy's actual P&L.

Following decomp_full_per_model.py: for each market we pick the side via
Brier-Kelly using the latest forecast (p, q_yes, q_no), compute the
unit-stake Brier decomposition (sf_sm, d, scaled) on (p_eff, q_eff,
y_eff), then SCALE by the agent's ACTUAL realized + open-mark P&L on
that market so that:

  Σ ΔS_market + Σ D_market  =  Σ actual_profit_market  =  $160.67

We then divide by the $200 budget to report:

  ΔS%  +  D%  =  ROI%  =  +80.33%
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
from ai_prophet_core.betting.db_schema import BettingPrediction
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
GAP_SEC = 3600
BUDGET = 200.0


def is_mentions(t): return "MENTION" in t.upper()
def is_topmodel(t): return t.upper().startswith("KXTOPMODEL")


def is_misspec(adapter, t):
    m = _fetch_raw_market(adapter, t)
    if not m: return False
    c = _parse_iso(m.get("close_time"))
    a = _parse_iso(m.get("expected_expiration_time")) or _parse_iso(m.get("occurrence_datetime"))
    return bool(c and a and (c-a).total_seconds() > GAP_SEC)


def live_yes_mid(adapter, ticker):
    for _ in range(2):
        try:
            m = _fetch_raw_market(adapter, ticker)
            if m:
                yb, ya = m.get("yes_bid"), m.get("yes_ask")
                if yb is not None and ya is not None:
                    return (float(yb) + float(ya)) / 200.0
        except Exception:
            pass
    return None


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


def is_within_spread(p, q_yes, q_no):
    return (1.0 - q_no) <= p <= q_yes


def brier_weight(p, q_yes, q_no):
    if p > q_yes: return p - q_yes, "YES", q_yes
    w = (1.0 - p) - q_no
    if w > 0: return w, "NO", q_no
    return 0.0, None, None


def brier_decomp(p, q, y):
    sf_sm = (q - y) ** 2 - (p - y) ** 2
    d = (p - q) ** 2
    scaled = 2.0 * (p - q) * (y - q)
    return sf_sm, d, scaled


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

    # 3-filter set
    by_ticker = defaultdict(list)
    ticker_to_market = {}
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None: continue
        if is_mentions(t) or is_topmodel(t): continue
        if is_misspec(adapter, t): continue
        by_ticker[t].append((o, m))
        ticker_to_market[t] = m

    market_ids = list({m.market_id for _, m in [(o, m) for o, m in rows
                                               if o.ticker in by_ticker]})

    # Outcomes
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

    # Latest forecast per market_id
    preds = (
        session.query(BettingPrediction)
        .filter(
            BettingPrediction.instance_name == INSTANCE,
            BettingPrediction.market_id.in_(market_ids),
            BettingPrediction.created_at >= CUTOFF,
        )
        .all()
    )
    latest_by_market = {}
    for p in preds:
        cur = latest_by_market.get(p.market_id)
        if cur is None or p.created_at > cur.created_at:
            latest_by_market[p.market_id] = p

    # Compute per-market actual P&L (realized + open mark)
    market_pnl = {}
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
        side, qty, avg = pos.current_position()
        m0 = ords[0][1]
        net = pos.realized_pnl
        outcome = outcome_by.get(m0.market_id)
        if side and qty > 1e-9:
            if outcome is not None:
                settle_px = outcome if side == "yes" else 1.0 - outcome
                net += (settle_px - avg) * qty
            else:
                mk = live_mark(adapter, ticker, side)
                if mk is None:
                    lp = m0.last_price
                    if lp is not None and lp > 1.0: lp /= 100.0
                    if lp is not None: mk = lp if side == "yes" else 1.0 - lp
                if mk is not None:
                    net += (mk - avg) * qty
        market_pnl[ticker] = net

    # Determine each market's side held (from the InventoryPosition replay)
    side_held = {}
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
        side, qty, _ = pos.current_position()
        if side and qty > 1e-9:
            side_held[ticker] = side
        elif pos.last_side:
            side_held[ticker] = pos.last_side

    # Decomposition: use agent's actual side (so within-spread markets are
    # not dropped — they still contribute to total P&L).
    sum_dS = 0.0
    sum_D = 0.0
    sum_profit = 0.0
    n_used = 0; n_no_terminal = 0; n_no_pred = 0; n_zero_scale = 0
    detail = []

    for ticker, m in ticker_to_market.items():
        pred = latest_by_market.get(m.market_id)
        if pred is None:
            n_no_pred += 1
            continue
        p = float(pred.p_yes)
        q_yes = float(pred.yes_ask)
        q_no = float(pred.no_ask)
        if m.market_id in outcome_by:
            y = outcome_by[m.market_id]
        else:
            y = live_yes_mid(adapter, ticker)
            if y is None:
                lp = m.last_price
                if lp is not None and lp > 1.0: lp /= 100.0
                y = lp
        if y is None:
            n_no_terminal += 1
            continue

        # Use the side the agent actually held; fall back to Brier-Kelly's
        # recommendation when the agent never held a position.
        side = side_held.get(ticker)
        if side is None:
            _, side, _ = brier_weight(p, q_yes, q_no)
            if side is None:
                # Default to YES if no edge either way
                side = "yes"
            side = side.lower()

        if side == "yes":
            p_eff, q_eff, y_eff = p, q_yes, y
        else:
            p_eff, q_eff, y_eff = 1.0 - p, q_no, 1.0 - y

        sf_sm_u, d_u, scaled_u = brier_decomp(p_eff, q_eff, y_eff)
        actual_profit = market_pnl.get(ticker, 0.0)
        if abs(scaled_u) < 1e-12:
            n_zero_scale += 1
            # Cannot decompose into sf_sm/d since scale is undefined; this
            # happens when forecaster matches market (p=q) or terminal sits
            # exactly at q. Attribute profit entirely to D as the Bregman
            # piece is zero by construction here, so all goes to ΔS.
            sum_dS += actual_profit
            sum_profit += actual_profit
            n_used += 1
            detail.append((ticker, side.upper(), p, q_eff, y, actual_profit, actual_profit, 0.0))
            continue
        scale = actual_profit / scaled_u
        sum_dS += sf_sm_u * scale
        sum_D  += d_u * scale
        sum_profit += actual_profit
        n_used += 1
        detail.append((ticker, side.upper(), p, q_eff, y, actual_profit, sf_sm_u * scale, d_u * scale))

    print(f"=== Brier ΔS + D decomposition (3-filter set) ===")
    print(f"Markets in filter set: {len(by_ticker)}")
    print(f"Used in decomposition:  {n_used}")
    print(f"  no prediction:        {n_no_pred}")
    print(f"  no terminal y:        {n_no_terminal}")
    print(f"  zero-scale fallback:  {n_zero_scale}")
    print()
    print(f"Σ ΔS:        ${sum_dS:+.4f}")
    print(f"Σ D:         ${sum_D:+.4f}")
    print(f"Σ profit:    ${sum_profit:+.4f}")
    print(f"  ΔS + D:    ${sum_dS + sum_D:+.4f}  (should ≈ profit on used bets)")
    print()
    used_profit = sum(d[5] for d in detail)
    print(f"Sanity (used-bets only):")
    print(f"  Σ profit on used bets: ${used_profit:+.4f}")
    print(f"  ΔS + D:                ${sum_dS + sum_D:+.4f}   diff = ${(sum_dS + sum_D) - used_profit:+.6f}")
    print()
    print(f"As fractions of $200 budget (paper convention):")
    print(f"  ΔS  = {sum_dS / BUDGET:+.4f}  ({sum_dS / BUDGET * 100:+.2f}%)")
    print(f"  D   = {sum_D / BUDGET:+.4f}  ({sum_D / BUDGET * 100:+.2f}%)")
    print(f"  ROI = {used_profit / BUDGET:+.4f}  ({used_profit / BUDGET * 100:+.2f}%)")
    print(f"  Identity check: ΔS + D = {(sum_dS + sum_D) / BUDGET:+.4f}  (vs ROI = {used_profit / BUDGET:+.4f})")
    print()
    detail.sort(key=lambda r: -abs(r[5]))
    print("Top 10 markets by |actual profit|:")
    for ticker, side, p, q, y, prof, dS, D in detail[:10]:
        print(f"  {ticker:<35}  side={side}  p={p:.2f} q={q:.2f} y={y:.2f}  profit=${prof:+.2f}  ΔS=${dS:+.2f}  D=${D:+.2f}")
