"""Full 26-event benchmark runner for agent_v8_3deep_orall (strict temporal debias).

Runs all events in parallel processes (workers=4 by default), persists
predictions + traces, computes per-event + per-category + total Brier,
writes JSON + Markdown report to data/predictions_v8_3deep_orall.json
and benchmarks/BENCHMARK_RESULTS.md.

actuals.json is loaded ONLY post-hoc for scoring. The agent itself
never sees it.

Usage:
  python scripts/run_v8_3deep_orall_full.py [N_WORKERS]
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("V83DEEP_SAVE_TRACES", "1")
# Disable page-fetch date validation for speed (defaults to ON in retro mode).
# For live deploy, this would be off anyway since there's no future to leak.
os.environ.setdefault("ORALL_FETCH_PAGE_DATES", "1")

LOG_PATH = HERE / "logs" / "v8_3deep_orall_full_benchmark.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _worker(ticker: str) -> dict:
    """Run agent_v8_3deep_orall on one event by ticker."""
    # Re-import inside worker (each process has its own module state)
    from agent_v8_3deep_orall import predict

    events = json.load(open(HERE / "data" / "sample_resolved_events.json"))
    by_tk = {e["market_ticker"]: e for e in events}
    if ticker not in by_tk:
        return {"ticker": ticker, "ok": False, "error": "not in event set"}

    t0 = time.time()
    try:
        event = by_tk[ticker]
        result = predict(event)
        # Normalize the result to a clean dict
        if isinstance(result, dict) and "probabilities" in result:
            probs = result["probabilities"]
            if isinstance(probs, list):
                pdict = {p.get("market") or p.get("outcome"): p.get("probability") for p in probs}
            else:
                pdict = probs
        else:
            pdict = result if isinstance(result, dict) else {}
        return {
            "ticker": ticker,
            "title": event.get("title", "")[:120],
            "category": event.get("category", "?"),
            "outcomes": event.get("outcomes", []),
            "probabilities": pdict,
            "wall_seconds": round(time.time() - t0, 1),
            "ok": True,
        }
    except Exception as e:
        return {
            "ticker": ticker,
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc()[:1000],
            "wall_seconds": round(time.time() - t0, 1),
        }


def _brier(probs: dict, truth: str) -> float:
    return sum((p - (1.0 if o == truth else 0.0)) ** 2 for o, p in probs.items())


def main():
    n_workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4

    events = json.load(open(HERE / "data" / "sample_resolved_events.json"))
    tickers = [e["market_ticker"] for e in events]
    actuals = json.load(open(HERE / "data" / "actuals.json"))

    print(f"=== v8_3deep_orall full benchmark ===")
    print(f"  N events: {len(tickers)}")
    print(f"  N workers: {n_workers}")
    print(f"  ORALL_FETCH_PAGE_DATES: {os.environ.get('ORALL_FETCH_PAGE_DATES')}")
    print(f"  V83DEEP_SAVE_TRACES: {os.environ.get('V83DEEP_SAVE_TRACES')}")
    print()

    results: list[dict] = []
    t_start = time.time()

    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        future_to_tk = {ex.submit(_worker, tk): tk for tk in tickers}
        n_done = 0
        for f in as_completed(future_to_tk):
            r = f.result()
            n_done += 1
            tk = r["ticker"]
            if r["ok"]:
                truth = actuals.get(tk)
                if truth:
                    r["truth"] = truth
                    r["brier"] = _brier(r["probabilities"], truth)
                else:
                    r["truth"] = None
                    r["brier"] = None
                print(f"  [{n_done}/{len(tickers)}] {tk} ({r['category']:<13}) "
                      f"Brier={r.get('brier', 'N/A'):.3f}  "
                      f"({r['wall_seconds']}s) — truth={truth}")
            else:
                print(f"  [{n_done}/{len(tickers)}] {tk} FAIL: {r.get('error','?')}")
            results.append(r)

    wall = time.time() - t_start
    print()
    print(f"=== ALL DONE in {wall/60:.1f} min ===")

    # Aggregate
    ok = [r for r in results if r.get("ok") and r.get("brier") is not None]
    n = len(ok)
    if n == 0:
        print("NO SUCCESSFUL EVENTS — aborting summary")
        return
    total_brier = sum(r["brier"] for r in ok)
    mean_brier = total_brier / n

    from collections import defaultdict
    by_cat = defaultdict(list)
    for r in ok:
        by_cat[r["category"]].append(r)

    print(f"  Mean Brier: {mean_brier:.4f} (n={n}/{len(tickers)})")
    print()
    print("  By category:")
    for cat, rows in sorted(by_cat.items(), key=lambda x: -sum(r["brier"] for r in x[1])/len(x[1])):
        m = sum(r["brier"] for r in rows) / len(rows)
        w = sorted(rows, key=lambda r: -r["brier"])[0]
        print(f"    {cat:<14} n={len(rows):>2} mean={m:.3f}  worst={w['brier']:.3f}  ({w['ticker']}, truth={w.get('truth','')[:25]})")

    # Save predictions JSON
    out = HERE / "data" / "predictions_v8_3deep_orall.json"
    out.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": "agent_v8_3deep_orall",
        "config": {
            "ORALL_FETCH_PAGE_DATES": os.environ.get("ORALL_FETCH_PAGE_DATES"),
            "V83DEEP_SAVE_TRACES": os.environ.get("V83DEEP_SAVE_TRACES"),
        },
        "n_total": len(tickers),
        "n_ok": n,
        "wall_seconds": round(wall, 1),
        "mean_brier": mean_brier,
        "by_category": {
            cat: {
                "n": len(rs),
                "mean_brier": sum(r["brier"] for r in rs) / len(rs),
                "events": [{"ticker": r["ticker"], "brier": r["brier"], "truth": r["truth"]}
                           for r in sorted(rs, key=lambda r: -r["brier"])],
            }
            for cat, rs in by_cat.items()
        },
        "predictions": [
            {"market_ticker": r["ticker"], "probabilities":
                [{"market": k, "probability": v} for k, v in (r.get("probabilities") or {}).items()],
             "truth": r.get("truth"), "brier": r.get("brier"),
             "category": r.get("category"), "ok": r["ok"],
             "wall_seconds": r.get("wall_seconds")}
            for r in results
        ],
    }, indent=2, default=str))
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
