#!/usr/bin/env python3
"""Analyze Haifeng's Kalshi P&L for the last 7 days."""
import os, base64, time
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

API_KEY = os.environ["KALSHI_API_KEY_ID_HAIFENG"]
PRIV = os.environ["KALSHI_PRIVATE_KEY_B64_HAIFENG"]
BASE = os.environ.get("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
key = serialization.load_pem_private_key(base64.b64decode(PRIV), password=None)

def sign(method, endpoint):
    ts = str(int(time.time() * 1000))
    msg = f"{ts}{method}{endpoint}".encode()
    sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                     salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    return {"KALSHI-ACCESS-KEY": API_KEY,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts}

def paginate(endpoint, key_name, min_ts):
    out, cursor = [], None
    while True:
        params = {"limit": 1000, "min_ts": min_ts}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE}{endpoint}", headers=sign("GET", endpoint),
                         params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get(key_name, []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return out

week_ago = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
fills = paginate("/trade-api/v2/portfolio/fills", "fills", week_ago)
settlements = paginate("/trade-api/v2/portfolio/settlements", "settlements", week_ago)
print(f"Fills: {len(fills)}, Settlements: {len(settlements)}")

by_market = defaultdict(lambda: {"buys": 0.0, "sells": 0.0, "fees": 0.0,
                                  "settle_rev": 0.0, "settle_cost_yes": 0.0,
                                  "settle_cost_no": 0.0,
                                  "buy_ct": 0, "sell_ct": 0})

for f in fills:
    t = f["ticker"]
    count = float(f["count_fp"])
    side = f["side"]
    px = float(f["yes_price_dollars"] if side == "yes" else f["no_price_dollars"])
    cash = count * px
    fee = float(f.get("fee_cost", 0))
    rec = by_market[t]
    rec["fees"] += fee
    if f["action"] == "buy":
        rec["buys"] += cash
        rec["buy_ct"] += count
    else:
        rec["sells"] += cash
        rec["sell_ct"] += count

for s in settlements:
    t = s["ticker"]
    rec = by_market[t]
    rec["settle_rev"] += s.get("revenue", 0) / 100.0
    rec["settle_cost_yes"] += float(s.get("yes_total_cost_dollars", 0))
    rec["settle_cost_no"] += float(s.get("no_total_cost_dollars", 0))

# P&L per market = sells + settle_revenue - buys - fees
rows = []
for t, r in by_market.items():
    pnl = r["sells"] + r["settle_rev"] - r["buys"] - r["fees"]
    rows.append((pnl, t, r))

rows.sort(key=lambda x: x[0])
total_pnl = sum(p for p,_,_ in rows)
print(f"\nNet P&L (last 7d, cash flow): ${total_pnl:,.2f}")
print(f"  Buys:        ${sum(r['buys'] for _,_,r in rows):,.2f}")
print(f"  Sells:       ${sum(r['sells'] for _,_,r in rows):,.2f}")
print(f"  Settle rev:  ${sum(r['settle_rev'] for _,_,r in rows):,.2f}")
print(f"  Fees:        ${sum(r['fees'] for _,_,r in rows):,.2f}")

print(f"\n--- Top 20 losing markets ---")
for pnl, t, r in rows[:20]:
    print(f"  ${pnl:>9,.2f}  {t:<50} buy=${r['buys']:>7.0f} sell=${r['sells']:>7.0f} settle=${r['settle_rev']:>7.0f}")

print(f"\n--- Top 10 winners ---")
for pnl, t, r in rows[-10:][::-1]:
    print(f"  ${pnl:>9,.2f}  {t:<50}")

# By day
by_day = defaultdict(float)
for f in fills:
    d = datetime.fromisoformat(f["created_time"].replace("Z","+00:00")).date()
    count = float(f["count_fp"])
    side = f["side"]
    px = float(f["yes_price_dollars"] if side=="yes" else f["no_price_dollars"])
    cash = count * px
    fee = float(f.get("fee_cost", 0))
    if f["action"] == "buy":
        by_day[d] -= cash + fee
    else:
        by_day[d] += cash - fee

by_day_settle = defaultdict(float)
for s in settlements:
    d = datetime.fromisoformat(s["settled_time"].replace("Z","+00:00")).date()
    by_day_settle[d] += s.get("revenue", 0) / 100.0

print(f"\n--- Daily breakdown ---")
all_days = sorted(set(by_day.keys()) | set(by_day_settle.keys()))
for d in all_days:
    flow = by_day[d]
    settle = by_day_settle[d]
    total = flow + settle
    print(f"  {d}  trade_flow=${flow:>9,.2f}  settlements=${settle:>8,.2f}  net=${total:>9,.2f}")

# Event-level (strip suffix) concentration
print(f"\n--- Category concentration (by ticker prefix) ---")
by_prefix = defaultdict(float)
for pnl, t, r in rows:
    prefix = t.split("-")[0]
    by_prefix[prefix] += pnl
for p, v in sorted(by_prefix.items(), key=lambda x: x[1]):
    print(f"  {p:<25} ${v:>10,.2f}")
