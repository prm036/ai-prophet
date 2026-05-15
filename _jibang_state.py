#!/usr/bin/env python3
"""Check Jibang's account state + order history."""
import os, base64, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

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

def run(name, ak_var, pk_var):
    sign = make_sign(os.environ[ak_var], os.environ[pk_var])
    print(f"\n=== {name} ===")
    # Balance
    r = requests.get(f"{BASE}/trade-api/v2/portfolio/balance",
                     headers=sign("GET","/trade-api/v2/portfolio/balance"), timeout=30)
    print("Balance:", r.json())
    # Orders (last 7d)
    week_ago = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    cursor = None; orders = []; statuses = {}
    while True:
        params={"limit":1000,"min_ts":week_ago}
        if cursor: params["cursor"] = cursor
        r = requests.get(f"{BASE}/trade-api/v2/portfolio/orders",
                         headers=sign("GET","/trade-api/v2/portfolio/orders"),
                         params=params, timeout=30)
        r.raise_for_status()
        d = r.json()
        orders.extend(d.get("orders", []))
        cursor = d.get("cursor")
        if not cursor: break
    print(f"Orders last 7d: {len(orders)}")
    for o in orders:
        st = o.get("status","?")
        statuses[st] = statuses.get(st, 0) + 1
    print("By status:", statuses)
    # Positions (paginate; new API returns position_fp as string + *_dollars fields)
    def _fp(v):
        try: return float(v)
        except (TypeError, ValueError): return 0.0
    cursor=None; positions=[]
    while True:
        params={"limit":200,"count_filter":"position"}
        if cursor: params["cursor"]=cursor
        r = requests.get(f"{BASE}/trade-api/v2/portfolio/positions",
                         headers=sign("GET","/trade-api/v2/portfolio/positions"),
                         params=params, timeout=30)
        d = r.json()
        positions.extend(d.get("market_positions", []))
        cursor = d.get("cursor")
        if not cursor: break
    mp = [p for p in positions if _fp(p.get("position_fp")) != 0]
    total_exp = sum(_fp(p.get("market_exposure_dollars")) for p in mp)
    print(f"Open positions: {len(mp)}  total exposure: ${total_exp:.2f}")
    for p in sorted(mp, key=lambda x: -_fp(x.get("market_exposure_dollars")))[:20]:
        print(f"  {p.get('ticker'):<40}  pos={_fp(p.get('position_fp')):>+7.2f}  exp=${_fp(p.get('market_exposure_dollars')):.2f}  resting={p.get('resting_orders_count',0)}")
    # Show a handful of recent orders
    print("Recent 8 orders:")
    for o in orders[:8]:
        print(f"  {o.get('created_time','')[:19]}  {o.get('status'):<10} {o.get('action')} {o.get('side')} {o.get('ticker'):<40} "
              f"yes_px={o.get('yes_price')} no_px={o.get('no_price')} cnt={o.get('count')} filled={o.get('filled_count')}")

run("Jibang", "KALSHI_API_KEY_ID_JIBANG", "KALSHI_PRIVATE_KEY_B64_JIBANG")
run("Haifeng", "KALSHI_API_KEY_ID_HAIFENG", "KALSHI_PRIVATE_KEY_B64_HAIFENG")
