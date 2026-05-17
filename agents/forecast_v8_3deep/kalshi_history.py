"""Kalshi candlestick history fetcher — pre-resolution market price snapshots.

Why: settled-market `last_price_dollars` is post-resolution (answer-key). For a
zero-bias smoke test we need a price snapshot from BEFORE the real-world outcome
was known. The /candlesticks endpoint is unauthenticated for settled markets.

Public method:
    price_at(market_ticker, event_ticker, target_dt) -> (yes_price_dollars, mode)

Where mode is "exact" (candle at target_dt), "opening" (target predates market
open; using first candle >=1h after open), or "missing" (no usable price).
"""
from __future__ import annotations
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Literal

logger = logging.getLogger(__name__)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
OPENING_SKIP_HOURS = 1
# Pick interval based on market span: hourly for short markets, daily for long.
# Kalshi limits roughly: 5000 candles per request -> ~7 days of 1m, ~200 days of 60m, ~13 years of 1440m.
SHORT_MARKET_DAYS = 14

Mode = Literal["exact", "opening", "missing"]


def _get(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        logger.warning("Kalshi GET %s -> %s", url, e.code)
        return None
    except Exception as e:
        logger.warning("Kalshi GET %s -> %s", url, e)
        return None


def _to_ts(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _yes_close_dollars(candle: dict) -> float | None:
    """Extract the YES-side close-price-in-dollars from a candle.

    Prefer `price.close_dollars` (last trade in the period). Fall back to
    midpoint of yes_bid/yes_ask close. Returns None if neither available.
    """
    p = candle.get("price") or {}
    v = p.get("close_dollars")
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    yb = (candle.get("yes_bid") or {}).get("close_dollars")
    ya = (candle.get("yes_ask") or {}).get("close_dollars")
    try:
        if yb is not None and ya is not None:
            return (float(yb) + float(ya)) / 2.0
    except (TypeError, ValueError):
        return None
    return None


def _market_meta(market_ticker: str) -> dict | None:
    url = f"{KALSHI_BASE}/markets/{urllib.parse.quote(market_ticker)}"
    data = _get(url)
    return (data or {}).get("market")


def _candlesticks(series_ticker: str, market_ticker: str,
                  start_ts: int, end_ts: int) -> list[dict]:
    span_days = (end_ts - start_ts) / 86400.0
    period_interval = 60 if span_days <= SHORT_MARKET_DAYS else 1440
    url = (f"{KALSHI_BASE}/series/{urllib.parse.quote(series_ticker)}"
           f"/markets/{urllib.parse.quote(market_ticker)}/candlesticks"
           f"?period_interval={period_interval}&start_ts={start_ts}&end_ts={end_ts}")
    data = _get(url)
    candles = ((data or {}).get("candlesticks") or [])
    if not candles and period_interval == 1440:
        # Some long markets may still accept 60m for narrower windows
        url2 = url.replace("period_interval=1440", "period_interval=60")
        data2 = _get(url2)
        candles = ((data2 or {}).get("candlesticks") or [])
    return candles


def price_at(market_ticker: str, event_ticker: str | None,
             target_dt: datetime) -> tuple[float | None, Mode]:
    """Return (yes_price_in_dollars_at_target, mode).

    mode = "exact":   target_dt falls inside the market's life; uses the
                      candle ending closest at-or-before target_dt.
           "opening": target_dt predates market open; uses the first candle
                      whose end_ts is >= open_time + OPENING_SKIP_HOURS.
           "missing": no usable candles (market not yet open / no history).
    """
    meta = _market_meta(market_ticker)
    if not meta:
        return None, "missing"
    series = (event_ticker or market_ticker).split("-")[0]
    try:
        open_dt = datetime.fromisoformat(meta["open_time"].replace("Z", "+00:00"))
        close_dt = datetime.fromisoformat(meta["close_time"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None, "missing"

    target_ts = _to_ts(target_dt)
    open_ts = _to_ts(open_dt)
    close_ts = _to_ts(close_dt)

    candles = _candlesticks(series, market_ticker, open_ts, close_ts)
    if not candles:
        return None, "missing"

    # Sort by end_period_ts just in case
    candles.sort(key=lambda c: c.get("end_period_ts", 0))

    if target_ts >= open_ts:
        # Find candle covering target_ts: the latest candle whose end_period_ts <= target_ts
        eligible = [c for c in candles if c.get("end_period_ts", 0) <= target_ts]
        if eligible:
            price = _yes_close_dollars(eligible[-1])
            if price is not None:
                return price, "exact"
            # Fall through to opening-spread if last-trade close was empty
        # else: no candle yet at target_ts -> opening spread

    # Opening spread: first candle >= 1h after open
    skip_until_ts = open_ts + OPENING_SKIP_HOURS * 3600
    for c in candles:
        if c.get("end_period_ts", 0) >= skip_until_ts:
            price = _yes_close_dollars(c)
            if price is not None:
                return price, "opening"
    # Last resort: very first candle
    for c in candles:
        price = _yes_close_dollars(c)
        if price is not None:
            return price, "opening"
    return None, "missing"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Self-test: cricket market, target = May 5 (3d before real resolution May 8).
    # Market open was May 6 -> target predates open -> mode should be "opening".
    target = datetime(2026, 5, 5, tzinfo=timezone.utc)
    p, mode = price_at("KXCRICKETTESTMATCH-26MAY08PAKBAN-PAK",
                       "KXCRICKETTESTMATCH-26MAY08PAKBAN", target)
    print(f"cricket PAK at 2026-05-05: price={p} mode={mode}")
    p, mode = price_at("KXCRICKETTESTMATCH-26MAY08PAKBAN-BAN",
                       "KXCRICKETTESTMATCH-26MAY08PAKBAN", target)
    print(f"cricket BAN at 2026-05-05: price={p} mode={mode}")
    # Ligue 1 PSG: real resolution May 13, target = May 10 (3d before).
    # Market opened way before -> should be "exact".
    target = datetime(2026, 5, 10, tzinfo=timezone.utc)
    p, mode = price_at("KXLIGUE1-26-PSG", "KXLIGUE1-26", target)
    print(f"Ligue1 PSG at 2026-05-10: price={p} mode={mode}")
    p, mode = price_at("KXLIGUE1-26-LIL", "KXLIGUE1-26", target)
    print(f"Ligue1 LIL at 2026-05-10: price={p} mode={mode}")
