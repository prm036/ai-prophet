#!/usr/bin/env python3
"""Compare Haifeng vs Jibang Kalshi P&L, last 7 days."""
import os, base64, time, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

def parse_ts(s):
    s = s.replace("Z", "+00:00")
    # pad/truncate fractional seconds to 6 digits
    if "." in s:
        head, rest = s.split(".", 1)
        frac = ""
        i = 0
        while i < len(rest) and rest[i].isdigit():
            frac += rest[i]; i += 1
        frac = (frac + "000000")[:6]
        s = head + "." + frac + rest[i:]
    return datetime.fromisoformat(s)

for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

BASE = os.environ.get("KALSHI_BASE_URL", "https://api.elections.kalshi.com")

def make_sign(api_key, priv_b64):
    key = serialization.load_pem_private_key(base64.b64decode(priv_b64), password=None)
    def sign(method, endpoint):
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method}{endpoint}".encode()
        sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                         salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": api_key,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
                "KALSHI-ACCESS-TIMESTAMP": ts}
    return sign

def paginate(sign, endpoint, key_name, min_ts):
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

def analyze(name, api_key, priv_b64, min_ts):
    sign = make_sign(api_key, priv_b64)
    fills = paginate(sign, "/trade-api/v2/portfolio/fills", "fills", min_ts)
    settles = paginate(sign, "/trade-api/v2/portfolio/settlements", "settlements", min_ts)

    by_market = defaultdict(lambda: {"buys":0.0,"sells":0.0,"fees":0.0,"settle_rev":0.0,
                                      "buy_ct":0,"sell_ct":0,"first":None,"last":None})
    for f in fills:
        t = f["ticker"]; rec = by_market[t]
        count = float(f["count_fp"])
        side = f["side"]
        px = float(f["yes_price_dollars"] if side=="yes" else f["no_price_dollars"])
        cash = count * px
        fee = float(f.get("fee_cost", 0))
        rec["fees"] += fee
        ts = f["created_time"]
        if rec["first"] is None or ts < rec["first"]: rec["first"] = ts
        if rec["last"] is None or ts > rec["last"]: rec["last"] = ts
        if f["action"] == "buy":
            rec["buys"] += cash; rec["buy_ct"] += count
        else:
            rec["sells"] += cash; rec["sell_ct"] += count
    for s in settles:
        t = s["ticker"]; by_market[t]["settle_rev"] += s.get("revenue", 0) / 100.0

    rows = []
    for t, r in by_market.items():
        pnl = r["sells"] + r["settle_rev"] - r["buys"] - r["fees"]
        rows.append((pnl, t, r))
    rows.sort(key=lambda x: x[0])

    totals = {
        "pnl": sum(p for p,_,_ in rows),
        "buys": sum(r["buys"] for _,_,r in rows),
        "sells": sum(r["sells"] for _,_,r in rows),
        "settle": sum(r["settle_rev"] for _,_,r in rows),
        "fees": sum(r["fees"] for _,_,r in rows),
        "n_fills": len(fills),
        "n_markets": len(by_market),
    }

    by_day_flow = defaultdict(float)
    by_day_settle = defaultdict(float)
    for f in fills:
        d = parse_ts(f["created_time"]).date()
        count = float(f["count_fp"]); side = f["side"]
        px = float(f["yes_price_dollars"] if side=="yes" else f["no_price_dollars"])
        cash = count * px; fee = float(f.get("fee_cost", 0))
        if f["action"] == "buy": by_day_flow[d] -= cash + fee
        else: by_day_flow[d] += cash - fee
    for s in settles:
        d = parse_ts(s["settled_time"]).date()
        by_day_settle[d] += s.get("revenue", 0) / 100.0

    by_prefix = defaultdict(float)
    for pnl, t, _ in rows:
        by_prefix[t.split("-")[0]] += pnl

    print(f"\n{'='*70}\n{name.upper()}\n{'='*70}")
    print(f"Net P&L (7d, cash-flow):  ${totals['pnl']:>12,.2f}")
    print(f"  Buys     ${totals['buys']:>12,.2f}   Sells     ${totals['sells']:>12,.2f}")
    print(f"  Settle   ${totals['settle']:>12,.2f}   Fees      ${totals['fees']:>12,.2f}")
    print(f"  Fills: {totals['n_fills']}   Markets: {totals['n_markets']}")

    print(f"\nTop 10 losing markets:")
    for pnl, t, r in rows[:10]:
        print(f"  ${pnl:>10,.2f}  {t:<50} buy=${r['buys']:>7.0f} sell=${r['sells']:>7.0f} settle=${r['settle_rev']:>7.0f}")

    print(f"\nTop 5 winners:")
    for pnl, t, r in rows[-5:][::-1]:
        print(f"  ${pnl:>10,.2f}  {t:<50}")

    print(f"\nDaily:")
    for d in sorted(set(by_day_flow) | set(by_day_settle)):
        fl = by_day_flow[d]; st = by_day_settle[d]
        print(f"  {d}  flow=${fl:>10,.2f}  settle=${st:>9,.2f}  net=${fl+st:>10,.2f}")

    print(f"\nBy ticker prefix:")
    for p, v in sorted(by_prefix.items(), key=lambda x: x[1]):
        print(f"  {p:<25} ${v:>10,.2f}")

    return totals, rows, by_prefix

week_ago = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
h = analyze("Haifeng", os.environ["KALSHI_API_KEY_ID_HAIFENG"],
            os.environ["KALSHI_PRIVATE_KEY_B64_HAIFENG"], week_ago)
j = analyze("Jibang", os.environ["KALSHI_API_KEY_ID_JIBANG"],
            os.environ["KALSHI_PRIVATE_KEY_B64_JIBANG"], week_ago)

print(f"\n{'='*70}\nDIFF\n{'='*70}")
print(f"Haifeng P&L: ${h[0]['pnl']:>10,.2f}   Jibang P&L: ${j[0]['pnl']:>10,.2f}")
print(f"Delta:       ${h[0]['pnl']-j[0]['pnl']:>10,.2f}")

# Overlapping markets
h_markets = {t: r for _, t, r in h[1]}
j_markets = {t: r for _, t, r in j[1]}
overlap = set(h_markets) & set(j_markets)
print(f"\nShared markets: {len(overlap)}  |  Haifeng-only: {len(set(h_markets)-overlap)}  |  Jibang-only: {len(set(j_markets)-overlap)}")

# Markets where haifeng lost a lot but jibang didn't
print(f"\nMarkets where Haifeng lost heavily — Jibang comparison:")
h_by_t = {t:(pnl,r) for pnl,t,r in h[1]}
j_by_t = {t:(pnl,r) for pnl,t,r in j[1]}
losers = sorted([(h_by_t[t][0], t) for t in h_by_t if h_by_t[t][0] < -20])[:15]
for pnl, t in losers:
    j_pnl = j_by_t[t][0] if t in j_by_t else None
    j_str = f"${j_pnl:>8,.2f}" if j_pnl is not None else "  (no trade)"
    print(f"  H=${pnl:>9,.2f}  J={j_str}  {t}")
