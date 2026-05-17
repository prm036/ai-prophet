"""Counterfactual analysis: apply v2's 4 math fixes to existing orall traces
without re-running the agent. Re-derives final probs from saved per-agent
probs and re-scores Brier.

Used to verify the fixes deliver projected savings BEFORE spending $60 on
a full re-benchmark.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from agent_v8_3deep_orall_v2 import (  # type: ignore
    ALPHA_BY_CATEGORY, DEFAULT_ALPHA, GUARDRAIL_STRENGTHS,
    CLIP_LO, CLIP_HI, PLATT_A,
)

TRACES = HERE / "data" / "v8_3deep_orall_traces"
ACTUALS = json.loads((HERE / "data" / "actuals.json").read_text())


def brier(probs: dict, truth: str) -> float:
    return sum((p - (1.0 if o == truth else 0.0)) ** 2 for o, p in probs.items())


def simulate_v2(trace: dict, fix_a=True, fix_b=True, fix_c=True, fix_d=True) -> dict:
    """Re-derive final probs from a saved orall trace with v2's 4 fixes."""
    outcomes = trace["outcomes"]
    category = trace["category"]
    kalshi = trace["market_prices"] or {}
    modes = trace["market_modes"] or {}
    agent_results = trace.get("agent_results") or []
    sup = trace.get("supervisor") or {}
    sup_probs = sup.get("final_probabilities")
    sup_conf = sup.get("confidence", "low")

    # Step 1: pick final_llm (supervisor decision or mean)
    mean_probs: dict[str, float] = {}
    if agent_results:
        for o in outcomes:
            vs = [r["probabilities"].get(o, 0.0) for r in agent_results]
            mean_probs[o] = sum(vs) / len(agent_results)
        s = sum(mean_probs.values()) or 1.0
        mean_probs = {k: v / s for k, v in mean_probs.items()}
    else:
        mean_probs = {o: 1.0 / len(outcomes) for o in outcomes}

    used_sup = "mean"
    if sup_conf == "high" and sup_probs:
        final_llm = sup_probs
        used_sup = "sup_high"
    # FIX D: trust supervisor on multi-class medium
    elif fix_d and sup_conf == "medium" and sup_probs and len(outcomes) > 5:
        top_mean = max(mean_probs, key=mean_probs.get)
        top_sup = max(sup_probs, key=sup_probs.get)
        sup_delta = abs(sup_probs.get(top_mean, 0.0) - mean_probs.get(top_mean, 0.0))
        if sup_delta > 0.10 or top_mean != top_sup:
            final_llm = sup_probs
            used_sup = "sup_FIX_D"
        else:
            final_llm = mean_probs
    else:
        final_llm = mean_probs

    # Step 2: per-cat Kalshi α + FIX B
    base_alpha = ALPHA_BY_CATEGORY.get(category, DEFAULT_ALPHA)
    alpha = base_alpha
    kalshi_reason = "base"
    if fix_b and kalshi and agent_results:
        max_kalshi = max(kalshi.values()) if kalshi else 0.5
        top_outcome = max(final_llm, key=final_llm.get)
        agent_top = [r["probabilities"].get(top_outcome, 0.0) for r in agent_results]
        if len(agent_top) >= 2:
            mn = sum(agent_top) / len(agent_top)
            std = (sum((p - mn)**2 for p in agent_top) / len(agent_top)) ** 0.5
        else:
            std = 0.5
        llm_top = final_llm.get(top_outcome, 0.0)
        kalshi_top = kalshi.get(top_outcome, 0.5)
        delta = abs(llm_top - kalshi_top)
        if max_kalshi <= 0.55:
            alpha = base_alpha * 0.4
            kalshi_reason = f"FIX_B kalshi_flat (max={max_kalshi:.2f}) {base_alpha:.2f}→{alpha:.2f}"
        elif std < 0.08 and delta > 0.10:
            alpha = base_alpha * 0.5
            kalshi_reason = f"FIX_B agents_agree+disagree_kalshi {base_alpha:.2f}→{alpha:.2f}"

    blended = {}
    for o in outcomes:
        llm_p = final_llm.get(o, 0.0)
        if o in kalshi:
            kp = max(CLIP_LO, min(CLIP_HI, kalshi[o]))
            blended[o] = alpha * kp + (1 - alpha) * llm_p
        else:
            blended[o] = llm_p

    # Step 3: guardrail
    n_evidence = 10  # assume kept >=10 (typical)
    n_out = len(outcomes)
    n_exact = sum(1 for o in outcomes if modes.get(o) == "exact")
    score = 0
    if n_evidence >= 10: score += 1
    if n_exact >= max(1, n_out / 2): score += 1
    if n_exact == n_out: score += 1
    shrink = GUARDRAIL_STRENGTHS[score]
    if shrink > 0:
        uniform = 1.0 / n_out
        blended = {o: shrink * uniform + (1 - shrink) * blended[o] for o in outcomes}

    # Clip + normalize
    clipped = {k: max(CLIP_LO, min(CLIP_HI, v)) for k, v in blended.items()}
    s = sum(clipped.values()) or 1.0
    final = {k: v / s for k, v in clipped.items()}

    # Step 4: Platt with FIX A + FIX C
    effective_alpha = PLATT_A
    platt_reason = f"base α={effective_alpha:.2f}"
    if fix_a and len(outcomes) > 2:
        effective_alpha = 1.0 + (PLATT_A - 1.0) / max(1, len(outcomes) - 1)
        platt_reason = f"FIX_A N={len(outcomes)} α={effective_alpha:.3f}"
    if fix_c and sup_conf != "high" and kalshi and agent_results:
        top_outcome = max(final_llm, key=final_llm.get)
        delta_top = abs(final_llm.get(top_outcome, 0.0) - kalshi.get(top_outcome, 0.5))
        if delta_top < 0.05:
            old = effective_alpha
            effective_alpha = 1.0
            platt_reason = f"FIX_C SKIP (LLM-Kalshi Δ={delta_top:.3f}, sup={sup_conf})  was {old:.3f}"

    if effective_alpha != 1.0:
        powered = {k: max(v, 1e-9) ** effective_alpha for k, v in final.items()}
        s = sum(powered.values()) or 1.0
        final = {k: v / s for k, v in powered.items()}
        clipped = {k: max(CLIP_LO, min(CLIP_HI, v)) for k, v in final.items()}
        s = sum(clipped.values()) or 1.0
        final = {k: v / s for k, v in clipped.items()}

    return {
        "final": final,
        "kalshi_reason": kalshi_reason,
        "platt_reason": platt_reason,
        "used": used_sup,
    }


def main():
    target_events = [
        "KXOHPRIMARY-15D26",
        "KXWVPRIMARY-01D26",
        "KXSURVIVORELIMINATION-26APR11",
        "KXEREDIVISIEGAME-26MAY10BREHEE",
        "KXCOUNTYCHAMPMATCH-26MAY08SOMGLA",
        "KXCOUNTYCHAMPMATCH-26MAY08LEISUS",
        "KXCRICKETTESTMATCH-26MAY08PAKBAN",
    ]
    print(f"{'event':<40} {'old_brier':>9} {'new_brier':>9} {'Δ':>8}  notes")
    print("-" * 110)
    total_delta = 0.0
    n = 0
    for tk in target_events:
        path = TRACES / f"{tk}.json"
        if not path.exists():
            print(f"  MISSING TRACE: {tk}")
            continue
        t = json.loads(path.read_text())
        truth = ACTUALS.get(tk)
        if not truth:
            continue
        old_brier = brier(t["final_probs"], truth)
        new = simulate_v2(t)
        new_brier = brier(new["final"], truth)
        delta = new_brier - old_brier
        total_delta += delta
        n += 1
        marker = "✅" if delta < -0.01 else ("⚠️" if delta > 0.01 else "•")
        print(f"  {tk[:38]:<38} {old_brier:9.4f} {new_brier:9.4f} {marker}{abs(delta):>7.4f}  k:{new['kalshi_reason'][:50]} | p:{new['platt_reason'][:30]}")
    print()
    if n > 0:
        print(f"Total Brier delta across {n} catastrophic events: {total_delta:+.4f}")
        print(f"Mean Δ per event:                              {total_delta/n:+.4f}")

    # Now do ALL 26
    print()
    print("=== All 26 events ===")
    total_old = 0.0
    total_new = 0.0
    n_all = 0
    for path in sorted(TRACES.glob("*.json")):
        tk = path.stem
        truth = ACTUALS.get(tk)
        if not truth: continue
        t = json.loads(path.read_text())
        old_b = brier(t["final_probs"], truth)
        new_b = brier(simulate_v2(t)["final"], truth)
        total_old += old_b
        total_new += new_b
        n_all += 1
    print(f"  Events: {n_all}")
    print(f"  Old mean Brier (orall):    {total_old/n_all:.4f}")
    print(f"  New mean Brier (orall_v2): {total_new/n_all:.4f}")
    print(f"  Δ (lower is better):       {(total_new - total_old)/n_all:+.4f}")


if __name__ == "__main__":
    main()
