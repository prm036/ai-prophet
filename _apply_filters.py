"""Apply the paper's filter chain incrementally to the unfiltered snapshot
and report ROI / Sharpe at each stage. Reads _unfiltered_snapshot.json so
the underlying data is the SAME snapshot, only the filter set changes."""
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
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
START_BUDGET = 300.0
START_HEADLINE = 475.43
GAP_SEC = 3600

with open("_unfiltered_snapshot.json") as f:
    snapshot = json.load(f)
markets_unfiltered = snapshot["markets"]
print(f"Unfiltered base: {len(markets_unfiltered)} markets")
print(f"Live NAV ROI on \${START_HEADLINE}: {snapshot['live_account']['roi_pct']:+.2f}%")
print()

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
    outcome_by = {}; settle_ts_by = {}
    for lc in lcs:
        if lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome_by[lc.market_id] = 1.0
            elif r == "no": outcome_by[lc.market_id] = 0.0
            settle_ts_by[lc.market_id] = lc.updated_at


def is_mentions(t): return "MENTION" in t.upper()


def is_misspec(t):
    m = _fetch_raw_market(adapter, t)
    if not m: return False
    c = _parse_iso(m.get("close_time"))
    a = _parse_iso(m.get("expected_expiration_time")) or _parse_iso(m.get("occurrence_datetime"))
    return bool(c and a and (c-a).total_seconds() > GAP_SEC)


SNAPSHOT_MARK = {m["ticker"]: m.get("live_mark") for m in markets_unfiltered}


def live_mark(ticker, side):
    """Read the mark frozen in _unfiltered_snapshot.json so all filter
    stages compare against the SAME live prices."""
    return SNAPSHOT_MARK.get(ticker)


def replay_filtered(predicate_orders=None):
    """Replay orders subject to per-order predicate. Returns net P&L
    (realized + live mark on still-open) and counts."""
    by_ticker = defaultdict(list)
    for o, m in rows:
        t = o.ticker or m.ticker
        if not t or m.expiration is None: continue
        if predicate_orders and not predicate_orders(o, m):
            continue
        by_ticker[t].append((o, m))

    realized = 0.0; open_mark = 0.0
    n_settled = 0; n_open = 0; n_won = 0; n_lost = 0
    n_total_orders = sum(len(v) for v in by_ticker.values())
    fill_dates = set()
    for ticker, ords in by_ticker.items():
        ords.sort(key=lambda x: x[0].created_at)
        pos = InventoryPosition()
        for o, _ in ords:
            try: pos.apply_order(o, ticker=ticker)
            except: pass
            fill_dates.add(o.created_at.astimezone(timezone.utc).date())
        realized += pos.realized_pnl
        side, qty, avg = pos.current_position()
        m0 = ords[0][1]
        outcome = outcome_by.get(m0.market_id)
        if side and qty > 1e-9:
            if outcome is not None:
                settle = outcome if side == "yes" else 1.0 - outcome
                realized += (settle - avg) * qty
                n_settled += 1
                fp = pos.realized_pnl + (settle - avg) * qty
                if fp > 0: n_won += 1
                else: n_lost += 1
            else:
                n_open += 1
                mark = live_mark(ticker, side)
                if mark is not None:
                    open_mark += (mark - avg) * qty
        else:
            if abs(pos.realized_pnl) > 1e-9:
                n_settled += 1
                if pos.realized_pnl > 0: n_won += 1
                else: n_lost += 1

    net = realized + open_mark
    return {
        "n_markets": len(by_ticker),
        "n_orders": n_total_orders,
        "n_settled": n_settled,
        "n_open": n_open,
        "n_won": n_won,
        "n_lost": n_lost,
        "win_rate_pct": round(n_won/(n_won+n_lost)*100, 2) if (n_won+n_lost) else None,
        "realized": round(realized, 4),
        "open_mark": round(open_mark, 4),
        "net_pnl": round(net, 4),
        "roi_300": round(net / START_BUDGET * 100, 2),
        "roi_475": round(net / START_HEADLINE * 100, 2),
        "days_traded": len(fill_dates),
        "by_ticker_keys": sorted(by_ticker.keys()),
    }

# ── Stage 0: unfiltered ──
s0 = replay_filtered(predicate_orders=lambda o, m: True)

# ── Stage 1: drop MENTIONS ──
s1 = replay_filtered(predicate_orders=lambda o, m: not is_mentions(o.ticker))

# ── Stage 2: + drop close-time-mismatch ──
s2 = replay_filtered(predicate_orders=lambda o, m: not is_mentions(o.ticker) and not is_misspec(o.ticker))

# ── Stage 3: + drop KXTOPMODEL ──
s3 = replay_filtered(predicate_orders=lambda o, m:
    not is_mentions(o.ticker)
    and not is_misspec(o.ticker)
    and not o.ticker.upper().startswith("KXTOPMODEL")
)

# ── Stage 4: + halt 3h pre-close ──
def filter4(o, m):
    if is_mentions(o.ticker): return False
    if is_misspec(o.ticker): return False
    if o.ticker.upper().startswith("KXTOPMODEL"): return False
    if m.expiration is None: return False
    exp = m.expiration if m.expiration.tzinfo else m.expiration.replace(tzinfo=timezone.utc)
    if (exp - o.created_at).total_seconds() <= 3 * 3600: return False
    return True

s4 = replay_filtered(predicate_orders=filter4)

stages = [
    ("0. Unfiltered", s0),
    ("1. + drop MENTIONS", s1),
    ("2. + drop close-time-mismatch (>1h)", s2),
    ("3. + drop KXTOPMODEL", s3),
    ("4. + halt 3h pre-close (FINAL)", s4),
]

print(f"{'Filter stage':<40} {'mkts':>5} {'ords':>5} {'sett':>4} {'open':>4} {'W/L':>7} {'realiz':>8} {'open_mk':>8} {'net':>8} {'/300':>7} {'/475':>7}")
print("-" * 110)
for label, st in stages:
    print(f"{label:<40} {st['n_markets']:>5} {st['n_orders']:>5} {st['n_settled']:>4} {st['n_open']:>4} "
          f"{st['n_won']}/{st['n_lost']:<5} ${st['realized']:>+7.2f} ${st['open_mark']:>+7.2f} ${st['net_pnl']:>+7.2f} "
          f"{st['roi_300']:>+6.2f}% {st['roi_475']:>+6.2f}%")

# ── Detailed final stage ──
print()
print(f"=== Final filter stage details ===")
final = stages[-1][1]
print(f"  Markets: {final['n_markets']}  Orders: {final['n_orders']}")
print(f"  Settled: {final['n_settled']} (W/L {final['n_won']}/{final['n_lost']}, win rate {final['win_rate_pct']}%)")
print(f"  Open: {final['n_open']}")
print(f"  Days traded (distinct fill dates): {final['days_traded']}")
print(f"  Realized P&L: ${final['realized']:+.2f}")
print(f"  Open mark P&L: ${final['open_mark']:+.2f}")
print(f"  Net P&L: ${final['net_pnl']:+.2f}")
print(f"  ROI on $300: {final['roi_300']:+.2f}%")
print(f"  ROI on $475: {final['roi_475']:+.2f}%")

# Save filter cascade
with open("_filter_cascade.json", "w") as f:
    json.dump({label: st for label, st in stages}, f, indent=2, default=str)
print()
print("Saved cascade to _filter_cascade.json")
