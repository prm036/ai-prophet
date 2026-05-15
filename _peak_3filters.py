"""Peak simultaneous capital deployed across the 3-filter set
(MENTIONS + close-time-mismatch + KXTOPMODEL excluded; NO 3h cutoff).
Replays all orders chronologically, tracks open cost basis per ticker,
reports max sum of cost bases at any single moment, and recomputes ROI."""
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

    # Build chronological order stream over 3-filter set (NO 3h cutoff)
    filtered = []
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None: continue
        if is_mentions(t): continue
        if is_topmodel(t): continue
        if is_misspec(adapter, t): continue
        filtered.append((o, m, t))
    filtered.sort(key=lambda x: x[0].created_at)
    print(f"3-filter surviving orders: {len(filtered)}")
    print(f"3-filter surviving markets: {len({t for _, _, t in filtered})}")

    # Replay tracking peak deployed (sum of cost basis of all CURRENTLY-open
    # positions). Settlements close out positions and don't count toward
    # deployed.
    positions: dict[str, InventoryPosition] = {}
    settled_set: set = set()
    peak_deployed = 0.0
    peak_ts = None
    peak_tickers_count = 0

    # Sort settlements with their timestamps so we can apply them
    # chronologically
    settlement_events = []  # (settle_ts, market_id, outcome)
    for mid, oc in outcome_by.items():
        ts = settle_ts_by.get(mid)
        if ts: settlement_events.append((ts, mid, oc))
    settlement_events.sort()
    settle_idx = 0

    # market_id <-> ticker map
    market_id_to_ticker = {m.market_id: o.ticker for o, m, _ in filtered}

    def deployed_now(positions, settled_set):
        total = 0.0
        n = 0
        for tk, p in positions.items():
            mid = next((x.market_id for o, x, ttk in filtered if ttk == tk), None)
            if mid in settled_set: continue
            side, qty, avg = p.current_position()
            if side and qty > 1e-9:
                total += qty * avg
                n += 1
        return total, n

    # Walk through orders + settlements in time order
    all_events = []
    for o, m, t in filtered:
        all_events.append((o.created_at, "order", (o, m, t)))
    for ts, mid, oc in settlement_events:
        all_events.append((ts, "settle", (mid, oc)))
    all_events.sort(key=lambda x: x[0])

    for ts, kind, payload in all_events:
        if kind == "order":
            o, m, ticker = payload
            pos = positions.setdefault(ticker, InventoryPosition())
            try: pos.apply_order(o, ticker=ticker)
            except: pass
        else:
            mid, _ = payload
            settled_set.add(mid)

        deployed, n = deployed_now(positions, settled_set)
        if deployed > peak_deployed:
            peak_deployed = deployed
            peak_ts = ts
            peak_tickers_count = n

    print()
    print(f"Peak simultaneous capital deployed: ${peak_deployed:.2f}")
    print(f"Reached at: {peak_ts}")
    print(f"Open tickers at peak: {peak_tickers_count}")

    # Recompute final P&L (realized + open mark) on the same 3-filter set
    by_ticker = defaultdict(list)
    for o, m, t in filtered:
        by_ticker[t].append((o, m))
    realized = 0.0; open_mark = 0.0
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
        realized += pos.realized_pnl
        side, qty, avg = pos.current_position()
        m0 = ords[0][1]
        outcome = outcome_by.get(m0.market_id)
        if side and qty > 1e-9:
            if outcome is not None:
                settle_px = outcome if side == "yes" else 1.0 - outcome
                realized += (settle_px - avg) * qty
            else:
                mark = live_mark(adapter, ticker, side)
                if mark is None:
                    last_px = m0.last_price
                    if last_px is not None and last_px > 1.0: last_px /= 100.0
                    if last_px is not None:
                        mark = last_px if side == "yes" else 1.0 - last_px
                if mark is not None:
                    open_mark += (mark - avg) * qty
    net = realized + open_mark
    budget = math.ceil(peak_deployed / 100.0) * 100

    print()
    print(f"3-filter net P&L: ${net:.2f}  (realized ${realized:.2f} + open mark ${open_mark:.2f})")
    print()
    print(f"Budget = peak deployed rounded up to nearest \$100 = ${budget:.0f}")
    print(f"ROI on \${budget:.0f} budget: {net / budget * 100:+.2f}%")
    print()
    print(f"For reference:")
    print(f"  ROI on \$300 budget: {net / 300 * 100:+.2f}%")
    print(f"  ROI on \$475 baseline: {net / 475.43 * 100:+.2f}%")
    print(f"  ROI on \${peak_deployed:.2f} (raw peak): {net / peak_deployed * 100:+.2f}%")
