"""Run agent_v8_3deep on a single event by ticker (for fast smoke testing)."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# Force trace save ON for diagnostic runs
os.environ.setdefault("V83DEEP_SAVE_TRACES", "1")

# Tee stdout/stderr to a log file (so it's captured even if run via &)
LOG_PATH = HERE / "logs" / os.environ.get("V83DEEP_LOG", "v8_3deep_single.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


class _Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, s):
        for st in self.streams: st.write(s); st.flush()
    def flush(self):
        for st in self.streams: st.flush()


def main():
    if len(sys.argv) < 2:
        print("usage: run_v8_3deep_single.py <market_ticker>")
        sys.exit(1)
    ticker = sys.argv[1]

    log_f = open(LOG_PATH, "w")
    sys.stdout = _Tee(sys.stdout, log_f)
    sys.stderr = _Tee(sys.stderr, log_f)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )

    # Allow swapping the agent module via env var (e.g. agent_v8_3deep_orsearch)
    agent_module = os.environ.get("V83DEEP_AGENT_MODULE", "agent_v8_3deep")
    import importlib
    mod = importlib.import_module(agent_module)
    predict = mod.predict
    print(f"  agent module: {agent_module}")

    events = json.load(open(HERE / "data" / "sample_resolved_events.json"))
    by_tk = {e["market_ticker"]: e for e in events}
    if ticker not in by_tk:
        print(f"ticker {ticker} not in sample_resolved_events.json")
        sys.exit(1)
    event = by_tk[ticker]
    print(f"=== RUN v8_3deep on {ticker} ===")
    print(f"  title: {event.get('title')}")
    print(f"  outcomes: {event.get('outcomes')}")
    print(f"  category: {event.get('category')}")

    result = predict(event)
    print(f"\n=== RESULT ===")
    print(json.dumps(result, indent=2))

    # Compute Brier if we have truth
    actuals = json.load(open(HERE / "data" / "actuals.json"))
    truth = actuals.get(ticker)
    if truth:
        # Result might be {"probabilities": [...]} or direct dict
        if isinstance(result, dict) and "probabilities" in result:
            probs = result["probabilities"]
            if isinstance(probs, list):
                pdict = {p.get("market") or p.get("outcome"): p.get("probability") for p in probs}
            else:
                pdict = probs
        else:
            pdict = result if isinstance(result, dict) else {}
        brier = sum((p - (1.0 if o == truth else 0.0)) ** 2 for o, p in pdict.items())
        print(f"\nTRUTH: {truth}")
        print(f"BRIER: {brier:.4f}")


if __name__ == "__main__":
    main()
