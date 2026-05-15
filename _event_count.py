"""Count distinct events (event_ticker) AND distinct markets (ticker) under
each filter step on the Jibang strategy."""
import os, sys
from datetime import datetime, timezone
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

from main import (
    _build_kalshi_adapter, _fetch_raw_market, _parse_iso,
    get_db, BettingOrder, TradingMarket,
)
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)
GAP_SEC = 3600
LATE_HOURS = 3
DROP_PREFIXES = ("KXTOPMODEL",)


def is_misspec(adapter, ticker):
    if "MENTION" in ticker.upper():
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


def is_topmodel(ticker):
    return any(ticker.upper().startswith(p) for p in DROP_PREFIXES)


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

    def report(label, surviving):
        tickers = {o.ticker for o, _ in surviving}
        events = {m.event_ticker for _, m in surviving if m.event_ticker}
        orders = len(surviving)
        print(f"{label:<55} orders={orders:>4}  tickers(markets)={len(tickers):>3}  events={len(events):>3}")

    # Step 0: raw filled orders
    raw = [(o, m) for o, m in rows]
    report("Raw filled orders since cutoff", raw)

    # Step 1: drop MENTIONS + misspec
    s1 = [(o, m) for o, m in raw if not is_misspec(adapter, o.ticker)]
    report("After MENTIONS + close-time-mismatch filter", s1)

    # Step 2: + drop KXTOPMODEL
    s2 = [(o, m) for o, m in s1 if not is_topmodel(o.ticker)]
    report("+ Drop KXTOPMODEL", s2)

    # Step 3: + halt within 3h of close
    s3 = []
    for o, m in s2:
        if m.expiration is None:
            continue
        exp = m.expiration if m.expiration.tzinfo else m.expiration.replace(tzinfo=timezone.utc)
        if (exp - o.created_at).total_seconds() <= LATE_HOURS * 3600:
            continue
        s3.append((o, m))
    report("+ Halt 3h prior to close (final filter)", s3)

    # Show event distribution at the final stage
    by_event = {}
    for o, m in s3:
        e = m.event_ticker or m.ticker
        by_event.setdefault(e, set()).add(o.ticker)
    print()
    print(f"Top 10 events by markets-per-event in final filtered set:")
    for e, ts in sorted(by_event.items(), key=lambda x: -len(x[1]))[:10]:
        print(f"  {e:<35}  {len(ts)} markets")
