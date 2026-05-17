"""Simulate adaptive Platt α on saved v8_3deep traces without re-running the
agent. Pull mean_or_fallback_probs (post-mean, pre-blend), apply the existing
blend + guardrail + new adaptive Platt, compute counterfactual Brier.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from agent_v8_3deep import (  # type: ignore
    ALPHA_BY_CATEGORY,
    DEFAULT_ALPHA,
    GUARDRAIL_STRENGTHS,
    CLIP_LO, CLIP_HI,
    PLATT_A,
    _adaptive_platt_alpha,
)

TRACES_DIR = HERE / "data" / "v8_3deep_traces"

# Truth per event (from manual ground-truth knowledge)
TRUTHS = {
    "KXOHPRIMARY-15D26": "Don Leonard",      # Leonard won (No Kings arrest pivot)
    "KXNEXTHUNGARYPM-26MAY01": "Péter Magyar",  # Magyar won
}


def brier_multi(probs: dict[str, float], truth: str) -> float:
    s = 0.0
    for o, p in probs.items():
        target = 1.0 if o == truth else 0.0
        s += (p - target) ** 2
    return s


def apply_pipeline(trace: dict, alpha_override: float | None = None) -> dict:
    """Re-apply post-ensemble pipeline: blend → guardrail → Platt.

    If alpha_override is given, use that as the Platt alpha (else use adaptive).
    Returns: {final, eff_alpha, max_pre_platt, brier}.
    """
    outcomes = trace["outcomes"]
    category = trace["category"]
    kalshi = trace["market_prices"] or {}
    modes = trace["market_modes"] or {}
    mean_probs = trace["mean_or_fallback_probs"]
    sup_conf = (trace.get("supervisor") or {}).get("confidence", "low")
    # Use the SUPERVISOR-OR-MEAN final from the trace
    used_sup = trace.get("used_supervisor")
    if used_sup and (trace.get("supervisor") or {}).get("final_probabilities"):
        llm = trace["supervisor"]["final_probabilities"]
    else:
        llm = mean_probs

    # 5. Per-cat α Kalshi blend
    alpha = ALPHA_BY_CATEGORY.get(category, DEFAULT_ALPHA)
    blended = {}
    for o in outcomes:
        llm_p = llm.get(o, 0.0)
        if o in kalshi:
            kp = max(CLIP_LO, min(CLIP_HI, kalshi[o]))
            blended[o] = alpha * kp + (1 - alpha) * llm_p
        else:
            blended[o] = llm_p

    # 6. Guardrail — approximate kept-evidence count by assuming "high" (>=10)
    # since v8 traces don't store this directly. Score = 3 if all Kalshi exact,
    # else 2; shrink minimal anyway.
    n_out = len(outcomes)
    n_exact = sum(1 for o in outcomes if modes.get(o) == "exact")
    score = 1  # assume n_evidence >= 10 (will refine if needed)
    if n_exact >= max(1, n_out / 2):
        score += 1
    if n_exact == n_out:
        score += 1
    shrink = GUARDRAIL_STRENGTHS[score]
    if shrink > 0:
        uniform = 1.0 / n_out
        blended = {o: shrink * uniform + (1 - shrink) * blended[o] for o in outcomes}

    # 7. Clip + normalize
    clipped = {k: max(CLIP_LO, min(CLIP_HI, v)) for k, v in blended.items()}
    s = sum(clipped.values()) or 1.0
    final = {k: v / s for k, v in clipped.items()}

    max_pre_platt = max(final.values())

    # 8. Adaptive Platt
    if alpha_override is not None:
        eff_alpha = alpha_override
    else:
        eff_alpha = _adaptive_platt_alpha(PLATT_A, sup_conf, max_pre_platt)

    if eff_alpha != 1.0:
        powered = {k: max(v, 1e-9) ** eff_alpha for k, v in final.items()}
        s = sum(powered.values()) or 1.0
        final = {k: v / s for k, v in powered.items()}
        clipped = {k: max(CLIP_LO, min(CLIP_HI, v)) for k, v in final.items()}
        s = sum(clipped.values()) or 1.0
        final = {k: v / s for k, v in clipped.items()}

    return {
        "final": final,
        "eff_alpha": eff_alpha,
        "max_pre_platt": max_pre_platt,
        "sup_conf": sup_conf,
    }


def main():
    for ticker, truth in TRUTHS.items():
        path = TRACES_DIR / f"{ticker}.json"
        if not path.exists():
            print(f"!! missing trace {path}")
            continue
        trace = json.loads(path.read_text())
        title = trace["title"][:80]
        category = trace["category"]
        print(f"\n=== {ticker} ({category}) ===\n    {title}")
        print(f"    truth: {truth}")
        print(f"    mean fallback probs: { {k: round(v,3) for k,v in trace['mean_or_fallback_probs'].items()} }")
        print(f"    market: { {k: round(v,3) for k,v in trace['market_prices'].items()} }")

        # As-saved baseline (use the trace's final_probs)
        saved_final = trace["final_probs"]
        saved_brier = brier_multi(saved_final, truth)
        print(f"\n  BASELINE (saved final_probs, α={PLATT_A}):")
        print(f"    probs: { {k: round(v,3) for k,v in saved_final.items()} }")
        print(f"    Brier: {saved_brier:.4f}")

        # Adaptive Platt (recomputed via pipeline)
        ad = apply_pipeline(trace, alpha_override=None)
        ad_brier = brier_multi(ad["final"], truth)
        print(f"\n  ADAPTIVE PLATT (sup_conf={ad['sup_conf']}, max_pre={ad['max_pre_platt']:.3f}, eff_α={ad['eff_alpha']:.3f}):")
        print(f"    probs: { {k: round(v,3) for k,v in ad['final'].items()} }")
        print(f"    Brier: {ad_brier:.4f}   Δ vs baseline: {ad_brier - saved_brier:+.4f}")

        # Sanity-check: no Platt (alpha=1.0)
        no_platt = apply_pipeline(trace, alpha_override=1.0)
        np_brier = brier_multi(no_platt["final"], truth)
        print(f"\n  NO PLATT (α=1.0) sanity check:")
        print(f"    probs: { {k: round(v,3) for k,v in no_platt['final'].items()} }")
        print(f"    Brier: {np_brier:.4f}   Δ vs baseline: {np_brier - saved_brier:+.4f}")

        # Original Platt α=2.0 (verify our pipeline reproduces saved)
        orig = apply_pipeline(trace, alpha_override=PLATT_A)
        orig_brier = brier_multi(orig["final"], truth)
        print(f"\n  ORIGINAL PLATT (α={PLATT_A}) re-run (sanity check vs saved):")
        print(f"    probs: { {k: round(v,3) for k,v in orig['final'].items()} }")
        print(f"    Brier: {orig_brier:.4f}   Δ vs saved: {orig_brier - saved_brier:+.4f} (should be ~0)")


if __name__ == "__main__":
    main()
