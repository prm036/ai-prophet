"""Read predictions_v8_3deep_orall.json and fill in BENCHMARK_RESULTS.md.

Run AFTER scripts/run_v8_3deep_orall_full.py completes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent.parent
PREDS_PATH = HERE / "data" / "predictions_v8_3deep_orall.json"
REPORT_PATH = HERE / "benchmarks" / "BENCHMARK_RESULTS.md"

# Prior baselines from existing prediction files (computed once, hardcoded)
PRIOR_PER_CAT = {
    "v7":         {"Elections": 0.966, "Entertainment": 0.523, "Politics": 0.343, "Sports": 0.306, "mean": 0.420},
    "v8":         {"Elections": 0.975, "Entertainment": 0.505, "Politics": 0.350, "Sports": 0.288, "mean": 0.408},
    "v8+Platt":   {"Elections": 1.239, "Entertainment": 0.583, "Politics": 0.129, "Sports": 0.232, "mean": 0.390},
    "aia":        {"Elections": 1.171, "Entertainment": 0.516, "Politics": 0.247, "Sports": 0.266, "mean": 0.407},
}

# Best-prior Brier per catastrophic event (from prior smoke runs)
CATASTROPHIC_BEST_PRIOR = {
    "KXOHPRIMARY-15D26":               ("OH-15 primary (Don Leonard)",          "Elections",     1.882, "v8+Platt"),
    "KXWVPRIMARY-01D26":               ("WV-1 primary (Vince George)",          "Elections",     1.536, "aia"),
    "KXCOUNTYCHAMPMATCH-26MAY08SOMGLA": ("Glamorgan v Somerset cricket",        "Sports",        1.321, "aia"),
    "KXSURVIVORELIMINATION-26APR11":    ("Survivor S50 E7 (Dee Valladares)",    "Entertainment", 1.315, "v8+Platt"),
}


def main():
    if not PREDS_PATH.exists():
        print(f"ERROR: {PREDS_PATH} not found. Run benchmark first.")
        return
    data = json.loads(PREDS_PATH.read_text())

    n_ok = data["n_ok"]
    mean = data["mean_brier"]
    wall_min = data["wall_seconds"] / 60.0
    timestamp = data["timestamp"]

    # Per-category orall results
    cats = data["by_category"]
    cat_means = {c: cats[c]["mean_brier"] for c in cats}

    # Find worst / best individual events
    all_events = [p for p in data["predictions"] if p.get("ok") and p.get("brier") is not None]
    worst = max(all_events, key=lambda e: e["brier"])
    best = min(all_events, key=lambda e: e["brier"])

    # Build category table rows
    cat_rows = []
    for cat in sorted(cats, key=lambda c: -cats[c]["mean_brier"]):
        cd = cats[cat]
        worst_ev = cd["events"][0]
        cat_rows.append(
            f"| {cat} | {cd['n']} | {cd['mean_brier']:.3f} | "
            f"{worst_ev['ticker']} (Brier {worst_ev['brier']:.2f}, truth={worst_ev['truth'][:30]}) |"
        )
    cat_table = "\n".join(cat_rows)

    # Catastrophic-event deltas
    by_tk = {e["market_ticker"]: e for e in all_events}
    cat_lines = {}
    for tk, (label, category, best_prior_brier, best_prior_label) in CATASTROPHIC_BEST_PRIOR.items():
        if tk in by_tk:
            orall_brier = by_tk[tk]["brier"]
            delta = orall_brier - best_prior_brier
            sign = "-" if delta < 0 else "+"
            cat_lines[tk] = (f"| {label} | {category} | {best_prior_brier:.3f} ({best_prior_label}) "
                              f"| **{orall_brier:.3f}** | {sign}{abs(delta):.3f} |")
        else:
            cat_lines[tk] = f"| {label} | {category} | {best_prior_brier:.3f} ({best_prior_label}) | (not in run) | — |"

    # Per-cat compare row
    def fmt(v):
        if v is None: return "—"
        return f"{v:.3f}"

    el = cat_means.get("Elections")
    en = cat_means.get("Entertainment")
    po = cat_means.get("Politics")
    sp = cat_means.get("Sports")

    # Total cost estimate
    total_cost = n_ok * 2.20  # midpoint of $1.80-2.40 per event range

    # Build full report
    md = REPORT_PATH.read_text()

    replacements = {
        "{{MEAN_BRIER}}": f"{mean:.4f}",
        "{{WORST_EVENT}}": f"{worst['market_ticker']} (truth={worst.get('truth','?')[:30]})",
        "{{WORST_BRIER}}": f"{worst['brier']:.3f}",
        "{{BEST_EVENT}}": f"{best['market_ticker']} (truth={best.get('truth','?')[:30]})",
        "{{BEST_BRIER}}": f"{best['brier']:.3f}",
        "{{WALL_MIN}}": f"{wall_min:.1f}",
        "{{N_WORKERS}}": "4",
        "{{CAT_TABLE}}": cat_table,
        "{{ELECTIONS}}": fmt(el),
        "{{ENTERTAINMENT}}": fmt(en),
        "{{POLITICS}}": fmt(po),
        "{{SPORTS}}": fmt(sp),
        "{{OH15_BRIER}}": cat_lines["KXOHPRIMARY-15D26"].split("**")[1] if "KXOHPRIMARY-15D26" in by_tk else "—",
        "{{OH15_DELTA}}": cat_lines["KXOHPRIMARY-15D26"].rsplit("|", 2)[1].strip() if "KXOHPRIMARY-15D26" in by_tk else "—",
        "{{WV1_BRIER}}": cat_lines["KXWVPRIMARY-01D26"].split("**")[1] if "KXWVPRIMARY-01D26" in by_tk else "—",
        "{{WV1_DELTA}}": cat_lines["KXWVPRIMARY-01D26"].rsplit("|", 2)[1].strip() if "KXWVPRIMARY-01D26" in by_tk else "—",
        "{{GLAM_BRIER}}": cat_lines["KXCOUNTYCHAMPMATCH-26MAY08SOMGLA"].split("**")[1] if "KXCOUNTYCHAMPMATCH-26MAY08SOMGLA" in by_tk else "—",
        "{{GLAM_DELTA}}": cat_lines["KXCOUNTYCHAMPMATCH-26MAY08SOMGLA"].rsplit("|", 2)[1].strip() if "KXCOUNTYCHAMPMATCH-26MAY08SOMGLA" in by_tk else "—",
        "{{SURV_BRIER}}": cat_lines["KXSURVIVORELIMINATION-26APR11"].split("**")[1] if "KXSURVIVORELIMINATION-26APR11" in by_tk else "—",
        "{{SURV_DELTA}}": cat_lines["KXSURVIVORELIMINATION-26APR11"].rsplit("|", 2)[1].strip() if "KXSURVIVORELIMINATION-26APR11" in by_tk else "—",
        "{{TOTAL_COST}}": f"{total_cost:.0f}",
        "{{TIMESTAMP}}": timestamp,
        "{{COMMIT_HASH}}": "see git log",
    }
    for k, v in replacements.items():
        md = md.replace(k, str(v))

    # Strip the placeholder banner
    md = md.replace("**Status**: PLACEHOLDER — will be filled in when the full run completes.\n\n", "")

    REPORT_PATH.write_text(md)
    print(f"✓ wrote {REPORT_PATH}")
    print()
    print(f"=== SUMMARY ===")
    print(f"  Mean Brier: {mean:.4f}  (n={n_ok})")
    for cat in sorted(cats, key=lambda c: -cats[c]["mean_brier"]):
        cd = cats[cat]
        print(f"    {cat:<14}: {cd['mean_brier']:.3f}  (n={cd['n']}, worst: {cd['events'][0]['ticker']} {cd['events'][0]['brier']:.3f})")
    print()
    print(f"  Compare to v8+Platt mean: 0.390")
    delta = mean - 0.390
    print(f"  orall improvement vs v8+Platt: {'+' if delta > 0 else ''}{delta:.4f} {'WORSE' if delta > 0 else 'BETTER'}")


if __name__ == "__main__":
    main()
