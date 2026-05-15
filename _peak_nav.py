"""Find peak NAV (balance + portfolio_value) across the snapshot window."""
import os, sys
from datetime import datetime, timezone
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, "services/api")
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

from sqlalchemy import desc
from main import get_db, KalshiBalanceSnapshot
from ai_prophet_core.betting.db import get_session

INSTANCE = "Jibang"
CUTOFF = datetime(2026, 3, 24, 23, 0, tzinfo=timezone.utc)

engine = get_db()
with get_session(engine) as session:
    snaps = (
        session.query(KalshiBalanceSnapshot)
        .filter(
            KalshiBalanceSnapshot.instance_name == INSTANCE,
            KalshiBalanceSnapshot.snapshot_ts >= CUTOFF,
        )
        .order_by(KalshiBalanceSnapshot.snapshot_ts.asc())
        .all()
    )
    print(f"{len(snaps)} snapshots since {CUTOFF.date()}")
    if not snaps:
        sys.exit(0)
    first = snaps[0]
    last = snaps[-1]
    print(f"first  {first.snapshot_ts}  bal=${float(first.balance):.2f}  pv=${float(first.portfolio_value or 0):.2f}  total=${float(first.balance) + float(first.portfolio_value or 0):.2f}")
    print(f"last   {last.snapshot_ts}  bal=${float(last.balance):.2f}  pv=${float(last.portfolio_value or 0):.2f}  total=${float(last.balance) + float(last.portfolio_value or 0):.2f}")

    # Peak by total NAV
    peak = max(snaps, key=lambda s: float(s.balance) + float(s.portfolio_value or 0))
    peak_total = float(peak.balance) + float(peak.portfolio_value or 0)
    print(f"\nPEAK total NAV: ${peak_total:.2f} at {peak.snapshot_ts}")
    print(f"  balance=${float(peak.balance):.2f}  portfolio_value=${float(peak.portfolio_value or 0):.2f}")
    print(f"\nROI vs $475 starting:")
    print(f"  current ending: {(float(last.balance)+float(last.portfolio_value or 0) - 475)/475*100:+.2f}%")
    print(f"  peak:           {(peak_total - 475)/475*100:+.2f}%")

    # Find when NAV crossed certain thresholds
    print("\nMilestones:")
    for thresh in (500, 550, 600, 650, 656.90):
        for s in snaps:
            if float(s.balance) + float(s.portfolio_value or 0) >= thresh:
                print(f"  first crossed ${thresh}: {s.snapshot_ts}  total=${float(s.balance) + float(s.portfolio_value or 0):.2f}")
                break
        else:
            print(f"  never reached ${thresh}")
