"""Compute STAKE-WEIGHTED Brier scores for the live trading set.

For each market we use the per-bet decomposition unit values
  ΔS_unit = (q − y)² − (p − y)²
  D_unit  = (p − q)²
  scaled_unit = 2(p − q)(y − q)   = ΔS_unit + D_unit

and scale by stake_i = actual_profit_i / scaled_unit_i so that the
identity  Σ ΔS_$ + Σ D_$ = Σ profit  holds.

Stake-weighted Brier scores are then
  Forecaster Brier (stake-weighted) = Σ stake_i · (p_i − y_i)² / Σ stake_i
  Market Brier     (stake-weighted) = Σ stake_i · (q_i − y_i)² / Σ stake_i

Their difference times Σ stake exactly equals the dollar ΔS.
"""
import os, sys, math, json
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
    ticker_to_market = {}
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None: continue
        if is_mentions(t) or is_topmodel(t): continue
        if is_misspec(adapter, t): continue
        by_ticker[t].append((o, m))
        ticker_to_market[t] = m

    market_ids = list({m.market_id for m in ticker_to_market.values()})
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

    # Per-market actual P&L
    side_held = {}
    market_pnl = {}
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
        side, qty, avg = pos.current_position()
        if side and qty > 1e-9: side_held[ticker] = side
        elif pos.last_side: side_held[ticker] = pos.last_side
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

    # Per-market cost basis (positive stake weights)
    market_cost = {}
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
        market_cost[ticker] = pos.total_buy_cost  # total dollars committed to BUYs

    # Compute stake-weighted Brier (stake = cost basis)
    sum_stake = 0.0
    sum_stake_fbrier = 0.0
    sum_stake_mbrier = 0.0
    sum_dS = 0.0
    sum_D = 0.0
    sum_profit = 0.0
    n_used = 0
    n_no_pred = 0; n_no_terminal = 0; n_zero_scale = 0

    for ticker, m in ticker_to_market.items():
        pred = latest_by_market.get(m.market_id)
        if pred is None:
            n_no_pred += 1
            continue
        p = float(pred.p_yes); q_yes = float(pred.yes_ask); q_no = float(pred.no_ask)
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

        side = side_held.get(ticker, "yes")
        if side == "yes":
            p_eff, q_eff, y_eff = p, q_yes, y
        else:
            p_eff, q_eff, y_eff = 1.0 - p, q_no, 1.0 - y

        sf_sm_u = (q_eff - y_eff) ** 2 - (p_eff - y_eff) ** 2
        d_u = (p_eff - q_eff) ** 2
        scaled_u = 2.0 * (p_eff - q_eff) * (y_eff - q_eff)

        actual_profit = market_pnl.get(ticker, 0.0)
        if abs(scaled_u) < 1e-12:
            n_zero_scale += 1
            sum_dS += actual_profit  # full attribution to ΔS when D = 0 by construction
            sum_profit += actual_profit
            continue

        scale = actual_profit / scaled_u
        cost = market_cost.get(ticker, 0.0)
        sum_stake += cost
        sum_stake_fbrier += cost * (p_eff - y_eff) ** 2
        sum_stake_mbrier += cost * (q_eff - y_eff) ** 2
        sum_dS += sf_sm_u * scale
        sum_D  += d_u * scale
        sum_profit += actual_profit
        n_used += 1

    fbrier_sw = sum_stake_fbrier / sum_stake if sum_stake else float("nan")
    mbrier_sw = sum_stake_mbrier / sum_stake if sum_stake else float("nan")
    dS_check = (mbrier_sw - fbrier_sw) * sum_stake

    print(f"Markets used in decomposition: {n_used} (no_pred={n_no_pred}, no_terminal={n_no_terminal}, zero_scale={n_zero_scale})")
    print()
    print(f"Σ stake (= Σ profit/scaled_unit):  {sum_stake:.4f}")
    print()
    print(f"Stake-weighted Brier scores:")
    print(f"  Forecaster Brier (stake-weighted): {fbrier_sw:.4f}")
    print(f"  Market Brier     (stake-weighted): {mbrier_sw:.4f}")
    print(f"  Brier gap (mB - fB):               {mbrier_sw - fbrier_sw:+.4f}")
    print()
    print(f"Decomposition (dollars):")
    print(f"  Σ ΔS:        ${sum_dS:+.4f}")
    print(f"  Σ D:         ${sum_D:+.4f}")
    print(f"  Σ profit:    ${sum_profit:+.4f}")
    print(f"  Identity check: ΔS + D = ${sum_dS + sum_D:+.4f}  vs profit = ${sum_profit:+.4f}")
    print(f"  Stake×(mB-fB) sanity: ${dS_check:+.4f} (should equal Σ ΔS = ${sum_dS:.4f})")
    print()
    print(f"As fractions of $200 budget:")
    print(f"  ΔS  = {sum_dS / BUDGET:+.4f}")
    print(f"  D   = {sum_D / BUDGET:+.4f}")
    print(f"  ROI = {sum_profit / BUDGET:+.4f}")
