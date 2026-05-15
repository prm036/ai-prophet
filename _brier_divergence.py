"""Compute Brier score + market-vs-model divergence for the filtered
Jibang strategy used in the paper's Section 6 / Appendix."""
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

    # Build the filtered ticker -> market_id map (the same universe used in
    # the paper's headline numbers)
    filtered_market_ids: set[str] = set()
    ticker_to_market: dict[str, TradingMarket] = {}
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None:
            continue
        if is_excluded(adapter, t):
            continue
        exp = m.expiration if m.expiration.tzinfo else m.expiration.replace(tzinfo=timezone.utc)
        if (exp - o.created_at).total_seconds() <= LATE_HOURS * 3600:
            continue
        filtered_market_ids.add(m.market_id)
        ticker_to_market[t] = m

    # Outcomes
    lcs = (
        session.query(TradingMarketLifecycle)
        .filter(
            TradingMarketLifecycle.instance_name == INSTANCE,
            TradingMarketLifecycle.market_id.in_(list(filtered_market_ids)),
        )
        .all()
    )
    outcome_by_market: dict[str, float] = {}
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome_by_market[lc.market_id] = 1.0
            elif r == "no": outcome_by_market[lc.market_id] = 0.0

    # All Jibang forecasts on those markets
    preds = (
        session.query(BettingPrediction)
        .filter(
            BettingPrediction.instance_name == INSTANCE,
            BettingPrediction.market_id.in_(list(filtered_market_ids)),
            BettingPrediction.created_at >= CUTOFF,
        )
        .all()
    )
    print(f"Filtered markets: {len(filtered_market_ids)}")
    print(f"Resolved markets in filtered set: {len(outcome_by_market)}")
    print(f"Forecasts on filtered markets: {len(preds)}")

    # ── Brier on every-forecast basis (all predictions on resolved markets) ──
    bs_model_sum = 0.0
    bs_market_sum = 0.0
    n_forecasts = 0
    abs_div_sum = 0.0
    sq_div_sum = 0.0
    n_div = 0
    for p in preds:
        outcome = outcome_by_market.get(p.market_id)
        if outcome is None:
            # market not yet resolved
            # still count divergence
            d = abs(p.p_yes - p.yes_ask)
            abs_div_sum += d
            sq_div_sum += d * d
            n_div += 1
            continue
        bs_model_sum += (p.p_yes - outcome) ** 2
        bs_market_sum += (p.yes_ask - outcome) ** 2
        n_forecasts += 1
        d = abs(p.p_yes - p.yes_ask)
        abs_div_sum += d
        sq_div_sum += d * d
        n_div += 1

    brier_model = bs_model_sum / n_forecasts if n_forecasts else None
    brier_market = bs_market_sum / n_forecasts if n_forecasts else None
    mean_abs_div = abs_div_sum / n_div if n_div else None
    rms_div = math.sqrt(sq_div_sum / n_div) if n_div else None

    print()
    print("=== Per-forecast (every prediction on a resolved market) ===")
    print(f"  Forecasts on resolved markets:    {n_forecasts}")
    print(f"  Model Brier score:                {brier_model:.4f}")
    print(f"  Market-baseline Brier (yes_ask):  {brier_market:.4f}")
    print(f"  Brier improvement vs market:      {(brier_market - brier_model)/brier_market*100:+.2f}%")
    print()
    print("=== Divergence (across all forecasts in filtered set) ===")
    print(f"  Total forecasts (resolved+open):  {n_div}")
    print(f"  Mean |p_yes − yes_ask|:           {mean_abs_div:.4f}")
    print(f"  RMS  |p_yes − yes_ask|:           {rms_div:.4f}")

    # ── Brier on per-market basis (latest forecast per market only) ──
    latest_pred_by_market: dict[str, BettingPrediction] = {}
    for p in preds:
        if p.market_id not in outcome_by_market:
            continue
        cur = latest_pred_by_market.get(p.market_id)
        if cur is None or p.created_at > cur.created_at:
            latest_pred_by_market[p.market_id] = p

    bs_m = 0.0; bs_b = 0.0; n_m = 0
    div_m = 0.0
    for mid, p in latest_pred_by_market.items():
        out = outcome_by_market[mid]
        bs_m += (p.p_yes - out) ** 2
        bs_b += (p.yes_ask - out) ** 2
        div_m += abs(p.p_yes - p.yes_ask)
        n_m += 1
    if n_m:
        print()
        print("=== Per-market (one forecast per market — latest before resolution) ===")
        print(f"  Resolved markets:                 {n_m}")
        print(f"  Model Brier score:                {bs_m/n_m:.4f}")
        print(f"  Market-baseline Brier (yes_ask):  {bs_b/n_m:.4f}")
        print(f"  Brier improvement vs market:      {(bs_b - bs_m)/bs_b*100:+.2f}%")
        print(f"  Mean |p_yes − yes_ask| at last forecast: {div_m/n_m:.4f}")
