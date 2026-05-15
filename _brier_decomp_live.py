"""Apply the Brier ΔS + D = ROI decomposition (matching the paper's
offline backtest in scripts/decomp_full_per_model.py) to the live
trading set.

Per-bet identity (Brier rule):
  ΔS = (q - y)² − (p - y)²        # market Brier − forecaster Brier
  D  = (p - q)²                    # Bregman divergence on YES space
  scaled = 2(p − q)(y − q)         # 2 × profit per unit stake YES at q

Strategy's Brier weight (Kelly-fractional):
  if p > q_yes:        side=YES, w = p − q_yes,   q = q_yes
  elif (1−p) > q_no:   side=NO,  w = (1−p) − q_no, q = q_no
  else:                skip (no edge)
For the chosen side flip to YES-equivalent: (p_eff, q_eff, y_eff).
cost  = w × q_eff
profit = (w − cost) if y_eff == 1 else −cost
We scale the per-bet decomp so that  ΔS + D = profit  exactly:
  scale = profit / scaled_unit
  ΔS += ΔS_unit × scale,   D += D_unit × scale
Final reported as %s of total cost staked, matching the paper:
  ΔS%   = 100 · ΣΔS / Σcost
  D%    = 100 · ΣD  / Σcost
  ROI%  = 100 · Σprofit / Σcost     (= ΔS% + D%)
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
)
from ai_prophet_core.betting.db_schema import BettingPrediction
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
GAP_SEC = 3600


def is_mentions(t): return "MENTION" in t.upper()
def is_topmodel(t): return t.upper().startswith("KXTOPMODEL")


def is_misspec(adapter, t):
    m = _fetch_raw_market(adapter, t)
    if not m: return False
    c = _parse_iso(m.get("close_time"))
    a = _parse_iso(m.get("expected_expiration_time")) or _parse_iso(m.get("occurrence_datetime"))
    return bool(c and a and (c-a).total_seconds() > GAP_SEC)


def live_yes_mid(adapter, ticker):
    """Live YES probability mid from Kalshi (yes_bid+yes_ask)/2."""
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


def is_within_spread(p, q_yes, q_no):
    """Forecast falls inside the bid-ask spread [1-q_no, q_yes]."""
    return (1.0 - q_no) <= p <= q_yes


def brier_weight(p, q_yes, q_no):
    """Brier-Kelly: pick side by edge, weight = edge magnitude."""
    if p > q_yes:
        return p - q_yes, "YES", q_yes
    w = (1.0 - p) - q_no
    if w > 0:
        return w, "NO", q_no
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
    kept_market_ids = set()
    ticker_to_market = {}
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None: continue
        if is_mentions(t) or is_topmodel(t): continue
        if is_misspec(adapter, t): continue
        kept_market_ids.add(m.market_id)
        ticker_to_market[t] = m

    print(f"3-filter markets: {len(kept_market_ids)}")

    # Latest forecast per market
    preds = (
        session.query(BettingPrediction)
        .filter(
            BettingPrediction.instance_name == INSTANCE,
            BettingPrediction.market_id.in_(list(kept_market_ids)),
            BettingPrediction.created_at >= CUTOFF,
        )
        .all()
    )
    latest_by_market = {}
    for p in preds:
        cur = latest_by_market.get(p.market_id)
        if cur is None or p.created_at > cur.created_at:
            latest_by_market[p.market_id] = p
    print(f"Markets with at least one forecast: {len(latest_by_market)}")

    # Outcomes
    lcs = (
        session.query(TradingMarketLifecycle)
        .filter(
            TradingMarketLifecycle.instance_name == INSTANCE,
            TradingMarketLifecycle.market_id.in_(list(kept_market_ids)),
        )
        .all()
    )
    outcome_by = {}
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome_by[lc.market_id] = 1.0
            elif r == "no": outcome_by[lc.market_id] = 0.0

    # Build market_id -> ticker
    mid_to_ticker = {m.market_id: t for t, m in ticker_to_market.items()}

    sum_dS = 0.0
    sum_D = 0.0
    sum_profit = 0.0
    sum_cost = 0.0
    n_used = 0
    n_within_spread = 0
    n_no_edge = 0
    n_no_terminal = 0
    rows_detail = []

    for mid, pred in latest_by_market.items():
        ticker = mid_to_ticker.get(mid)
        p = float(pred.p_yes)
        q_yes = float(pred.yes_ask)
        q_no = float(pred.no_ask)
        # Terminal y in YES-space: realized outcome if resolved, else live YES mid
        if mid in outcome_by:
            y = outcome_by[mid]
        else:
            y = live_yes_mid(adapter, ticker) if ticker else None
            if y is None:
                # fallback: use last_price from TradingMarket
                m_t = ticker_to_market.get(ticker)
                if m_t and m_t.last_price is not None:
                    y = m_t.last_price / 100.0 if m_t.last_price > 1 else m_t.last_price
        if y is None:
            n_no_terminal += 1
            continue

        if is_within_spread(p, q_yes, q_no):
            n_within_spread += 1
            continue

        w, side, q = brier_weight(p, q_yes, q_no)
        if w is None or w <= 0 or side is None:
            n_no_edge += 1
            continue

        # Flip to YES-equivalent for the chosen side
        if side == "YES":
            p_eff, q_eff, y_eff = p, q_yes, y
        else:
            p_eff, q_eff, y_eff = 1.0 - p, q_no, 1.0 - y

        sf_sm_u, d_u, scaled_u = brier_decomp(p_eff, q_eff, y_eff)
        cost = w * q_eff

        # Realized profit from this bet at unit stake = w (Kelly weight):
        # If y_eff = 1, payout = w (each share pays $1), cost = w·q_eff,
        #   profit = w − cost.
        # If y_eff = 0, payout = 0, profit = −cost.
        # For continuous y (open, marked), use payout = w·y_eff:
        #   profit = w·y_eff − cost = w·(y_eff − q_eff)
        profit = w * (y_eff - q_eff)
        # Equivalently 0.5 · scaled_u  (since scaled_u = 2(p-q)(y-q) and w = p-q)
        # Sanity: 0.5 * scaled_u == w * (y_eff - q_eff) ✓ when w = p_eff - q_eff

        if abs(scaled_u) > 1e-12:
            scale = profit / scaled_u
            sum_dS += sf_sm_u * scale
            sum_D += d_u * scale
        sum_profit += profit
        sum_cost += cost
        n_used += 1
        rows_detail.append((ticker, side, w, q_eff, y_eff, profit, sf_sm_u, d_u))

    print()
    print(f"Bets used:       {n_used}")
    print(f"  within spread skipped: {n_within_spread}")
    print(f"  no-edge skipped:       {n_no_edge}")
    print(f"  no terminal y:         {n_no_terminal}")
    print()
    print(f"Σ ΔS    = {sum_dS:+.4f}")
    print(f"Σ D     = {sum_D:+.4f}")
    print(f"Σ profit= {sum_profit:+.4f}")
    print(f"Σ cost  = {sum_cost:+.4f}")
    print()
    if sum_cost > 0:
        dS_pct = sum_dS / sum_cost * 100
        d_pct = sum_D / sum_cost * 100
        roi_pct = sum_profit / sum_cost * 100
        print(f"As percentages of total cost staked:")
        print(f"  ΔS  = {dS_pct:+.4f}%")
        print(f"  D   = {d_pct:+.4f}%")
        print(f"  ROI = {roi_pct:+.4f}%")
        print(f"  Identity check: ΔS + D = {dS_pct + d_pct:+.4f}%   (vs ROI = {roi_pct:+.4f}%)")
    print()
    # Top contributors
    rows_detail.sort(key=lambda r: -abs(r[5]))
    print("Top 10 contributors by |profit|:")
    for ticker, side, w, q, y, prof, dS, d in rows_detail[:10]:
        print(f"  {ticker:<35}  side={side}  w={w:.3f}  q={q:.3f}  y={y:.3f}  profit={prof:+.3f}  dS={dS:+.4f}  D={d:.4f}")
