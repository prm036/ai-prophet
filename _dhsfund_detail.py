"""Detail on the KXDHSFUND markets that lost money."""
import os, sys
from datetime import datetime, timezone
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

from main import (
    _build_kalshi_adapter, _fetch_raw_market,
    get_db, BettingOrder, TradingMarket, TradingMarketLifecycle,
    InventoryPosition,
)
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)

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
            BettingOrder.ticker.like("KXDHSFUND%"),
        )
        .all()
    )
    by_ticker = {}
    for o, m in rows:
        by_ticker.setdefault(o.ticker, []).append((o, m))

    print(f"KXDHSFUND tickers traded: {len(by_ticker)}")
    print()
    for ticker, ords in sorted(by_ticker.items()):
        ords.sort(key=lambda x: x[0].created_at)
        mkt0 = ords[0][1]
        # Pull live Kalshi market metadata for full title + rules
        market = _fetch_raw_market(adapter, ticker)
        title = (market or {}).get("title") or mkt0.title
        sub = (market or {}).get("subtitle") or ""
        rules = (market or {}).get("rules_primary") or (market or {}).get("yes_sub_title") or ""
        close_t = (market or {}).get("close_time")
        exp_t = (market or {}).get("expected_expiration_time") or (market or {}).get("expiration_time")
        result = (market or {}).get("result", "—")
        # P&L replay
        pos = InventoryPosition()
        for o, _ in ords:
            try:
                pos.apply_order(o, ticker=ticker)
            except Exception:
                pass
        side, qty, avg = pos.current_position()
        net = pos.realized_pnl
        # Settlement
        outcome = None
        lc = session.query(TradingMarketLifecycle).filter(
            TradingMarketLifecycle.market_id == mkt0.market_id,
            TradingMarketLifecycle.instance_name == INSTANCE,
        ).first()
        if lc and lc.result:
            r = str(lc.result).lower()
            if r == "yes": outcome = 1.0
            elif r == "no": outcome = 0.0
        if side and qty > 1e-9 and outcome is not None:
            settle = outcome if side == "yes" else 1.0 - outcome
            net += (settle - avg) * qty

        print(f"=== {ticker} ===")
        print(f"  title:    {title}")
        if sub: print(f"  subtitle: {sub}")
        if rules: print(f"  rules:    {rules[:300]}")
        print(f"  close_time: {close_t}    expected/expiration: {exp_t}")
        print(f"  result:   {result}")
        print(f"  orders:   {len(ords)}")
        # Per-order summary
        for o, _ in ords:
            print(f"    {o.created_at}  {o.action} {o.side} qty_filled={float(o.filled_shares or 0):.0f} "
                  f"price={float(o.fill_price or 0):.3f}  status={o.status}")
        # Final position
        print(f"  final position side={side} qty={qty} avg={avg}")
        print(f"  realized P&L: ${pos.realized_pnl:+.2f}    final P&L: ${net:+.2f}")
        print()
