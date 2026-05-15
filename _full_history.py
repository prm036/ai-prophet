#!/usr/bin/env python3
"""Pull full-history P&L for Jibang (live) and GPT5/Grok4/Opus46 (dry-run)."""
import os, base64, time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import create_engine, text

for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))

BASE = os.environ.get("KALSHI_BASE_URL", "https://api.elections.kalshi.com")

def parse_ts(s):
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = ""; i = 0
        while i < len(rest) and rest[i].isdigit():
            frac += rest[i]; i += 1
        frac = (frac + "000000")[:6]
        s = head + "." + frac + rest[i:]
    return datetime.fromisoformat(s)

def sign_fn(api_key, priv_b64):
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

def paginate(sign, endpoint, key):
    out, cursor = [], None
    while True:
        params = {"limit": 1000}
        if cursor: params["cursor"] = cursor
        r = requests.get(f"{BASE}{endpoint}", headers=sign("GET", endpoint),
                         params=params, timeout=30)
        r.raise_for_status()
        d = r.json(); out.extend(d.get(key, []))
        cursor = d.get("cursor")
        if not cursor: break
    return out

# --- Live: Jibang ---
sign = sign_fn(os.environ["KALSHI_API_KEY_ID_JIBANG"],
               os.environ["KALSHI_PRIVATE_KEY_B64_JIBANG"])
fills = paginate(sign, "/trade-api/v2/portfolio/fills", "fills")
settles = paginate(sign, "/trade-api/v2/portfolio/settlements", "settlements")
print(f"JIBANG  fills={len(fills)}  settles={len(settles)}")
if fills:
    first = min(parse_ts(f["created_time"]) for f in fills)
    last  = max(parse_ts(f["created_time"]) for f in fills)
    print(f"  first fill: {first.date()}   last fill: {last.date()}   days={(last-first).days}")
buys = sells = fees = settle_rev = 0.0
for f in fills:
    cnt = float(f["count_fp"]); side = f["side"]
    px = float(f["yes_price_dollars"] if side=="yes" else f["no_price_dollars"])
    cash = cnt * px; fee = float(f.get("fee_cost", 0))
    fees += fee
    if f["action"] == "buy": buys += cash
    else: sells += cash
for s in settles:
    settle_rev += s.get("revenue", 0) / 100.0
pnl = sells + settle_rev - buys - fees
print(f"  buys=${buys:,.2f} sells=${sells:,.2f} settle=${settle_rev:,.2f} fees=${fees:,.2f}")
print(f"  NET P&L = ${pnl:,.2f}")

# --- Dry-run models from DB ---
print("\nDRY-RUN (from Postgres)")
engine = create_engine(os.environ["DATABASE_URL"].strip('"'))
with engine.connect() as conn:
    for inst in ["GPT5", "Grok4", "Opus46", "Haifeng", "Jibang"]:
        r = conn.execute(text("""
            SELECT
              COUNT(*) FILTER (WHERE status IN ('FILLED','DRY_RUN')) AS n_orders,
              COUNT(DISTINCT ticker) FILTER (WHERE status IN ('FILLED','DRY_RUN')) AS n_markets,
              MIN(created_at) AS first_ord,
              MAX(created_at) AS last_ord
            FROM betting_orders
            WHERE instance_name = :inst
        """), {"inst": inst}).mappings().first()
        p = conn.execute(text("""
            SELECT
              COALESCE(SUM(realized_pnl),0)              AS realized,
              COALESCE(SUM(avg_price * quantity),0)      AS deployed,
              COALESCE(SUM(unrealized_pnl),0)            AS unrealized,
              COUNT(*) FILTER (WHERE quantity > 0)       AS open_pos
            FROM trading_positions
            WHERE instance_name = :inst
        """), {"inst": inst}).mappings().first()
        fr = p["realized"] or 0
        dp = p["deployed"] or 0
        ur = p["unrealized"] or 0
        days = ((p_last := r["last_ord"]) - r["first_ord"]).days if r["first_ord"] and r["last_ord"] else 0
        print(f"  {inst:<8} orders={r['n_orders']:>5} markets={r['n_markets']:>4} "
              f"first={str(r['first_ord'])[:10]} days={days:>3} "
              f"realized=${fr:>9,.2f} unrealized=${ur:>9,.2f} deployed=${dp:>9,.2f}")
