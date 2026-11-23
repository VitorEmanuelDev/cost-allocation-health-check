"""One-command pipeline: ingest -> validate -> transform -> export.

Run:  python pipeline.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from src.config import OUTPUT_DIR
from src.export import write_csvs, write_dashboard
from src.ingest import ingest
from src.transform import build_db
from src.validate import validate


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("Ingesting billing files and reference data ...")
    bundle = ingest()
    n_files = len(bundle.billing_files)
    n_lines = sum(len(f.lines) for f in bundle.billing_files)
    print(f"  {n_files} billing files, {n_lines} service lines parsed.")

    print("Running validation rules V1–V10 ...")
    findings = validate(bundle)
    crit = [f for f in findings if f["severity"] == "Critical"]
    high = [f for f in findings if f["severity"] == "High"]
    print(f"  {len(findings)} findings: {len(crit)} critical, {len(high)} high.")

    print("Building SQL marts and exporting outputs ...")
    conn = build_db(bundle, findings)
    write_csvs(conn, findings)
    write_dashboard(conn, findings)
    conn.close()

    meta = {
        "run_id": datetime.now(timezone.utc).isoformat(),
        "billing_files": n_files,
        "service_lines": n_lines,
        "findings": {"total": len(findings), "critical": len(crit), "high": len(high)},
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "run_metadata.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)

    print("\nOutputs written to outputs/:")
    for name in ["actuals.csv", "budget_vs_actual.csv", "reconciliation.csv",
                 "findings.csv", "dashboard.html", "finance_health.db", "run_metadata.json"]:
        print(f"  - {name}")

    print("\nTop findings:")
    for f in findings[:8]:
        impact = f"{f['usd_impact']:,.2f}" if f["usd_impact"] is not None else "-"
        print(f"  [{f['severity']:<8}] {f['rule_id']} {f['entity'] or '':<2} {f['month'] or '':<7} "
              f"{f['service_code'] or '':<7} {f['message'][:80]}  ({impact} USD)")

    if crit:
        print("\nPipeline finished with CRITICAL findings (see findings.csv).")
        return 1
    print("\nPipeline finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
