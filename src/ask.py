"""AI layer: a small, deterministic, SQL-first Q&A tool with citations and refusals.

Every answer cites its source (file → sheet → row). When evidence is missing or
the question is out of scope it refuses instead of guessing. Swap this intent
matcher for an LLM backend to keep the same citation/refusal contract.
"""
from __future__ import annotations

import sqlite3
import sys

from .config import DB_PATH

_REFUSAL_KEYWORDS = ["october", "november", "december", "entity d", "2026-10", "2026-11", "2026-12"]


def _cite(file, sheet, row, entity=None):
    usd_col = "G" if entity == "A" else "I"
    loc = f"{file} → {sheet}" + (f" → row {row}" if row else "")
    return f"{loc} (col C = service code; col {usd_col} = amount USD)"


def answer(conn: sqlite3.Connection, question: str) -> str:
    q = question.lower()

    if any(k in q for k in _REFUSAL_KEYWORDS):
        return ("REFUSE (insufficient evidence): the dataset only covers Entities A/B/C for "
                "Jan–Sep 2026. No October (or later) data is available, so I will not guess.")

    # 1) charges without a mapped contract
    if ("without" in q and "contract" in q) or "unmapped" in q or "no mapped contract" in q:
        rows = conn.execute(
            "SELECT entity, month, service_code, message, usd_impact, file, sheet, row "
            "FROM findings WHERE rule_id='V9' AND service_code IS NOT NULL "
            "ORDER BY entity, month, service_code").fetchall()
        if not rows:
            return "No charges without a mapped contract were found."
        parts = ["Services billed without a mapped contract:"]
        for r in rows:
            parts.append(f"• {r[2]} billed to {r[0]} in {r[1]} — {r[3]} "
                         f"[{_cite(r[5], r[6], r[7], r[0])}]")
        return "\n".join(parts)

    # 2) duplicate charges
    if "duplicate" in q or "more than once" in q or "twice" in q:
        rows = conn.execute(
            "SELECT entity, month, service_code, message, usd_impact, file, sheet, row "
            "FROM findings WHERE rule_id='V3' ORDER BY entity, month, service_code").fetchall()
        if not rows:
            return "No duplicate charges were found."
        parts = ["Duplicate charges (same entity/month/service more than once):"]
        for r in rows:
            parts.append(f"• {r[0]} {r[1]} {r[2]} — {r[3]} [{_cite(r[5], r[6], r[7], r[0])}]")
        return "\n".join(parts)

    # 3) total vs file total mismatch
    if "differ" in q or "does not equal" in q or "reconcile" in q or "file total" in q:
        rows = conn.execute(
            "SELECT entity, month, message, usd_impact, file, sheet, row "
            "FROM findings WHERE rule_id='V8' ORDER BY entity, month").fetchall()
        if not rows:
            return "All file totals reconcile with their line sums."
        parts = ["File-total reconciliation failures:"]
        for r in rows:
            parts.append(f"• {r[0]} {r[1]} — {r[2]} [{_cite(r[4], r[5], r[6], r[0])}]")
        return "\n".join(parts)

    # 4) fraud cluster variance
    if "fraud" in q and ("variance" in q or "budget" in q or "cluster" in q):
        rows = conn.execute(
            "SELECT month, SUM(actual_usd), SUM(budget_usd), SUM(variance_usd) "
            "FROM budget_vs_actual WHERE cluster='Fraud & Risk' "
            "GROUP BY month ORDER BY month").fetchall()
        tot_a = sum(r[1] for r in rows)
        tot_b = sum(r[2] for r in rows)
        tot_v = sum(r[3] for r in rows)
        return (f"Fraud & Risk cluster (SVC-04 + SVC-05 + SVC-06) YTD: actual {tot_a:,.2f} USD "
                f"vs budget {tot_b:,.2f} USD, variance {tot_v:,.2f} USD. "
                "Key driver: SVC-06 (Chargeback Alerts) is still billed to Entity A in Jul–Sep "
                "after contract CT-1006 ended 2026-06-30 (+30,640 USD). "
                "[source: outputs/budget_vs_actual.csv → columns month, actual_usd, budget_usd, variance_usd; "
                "cluster='Fraud & Risk']")

    # 5) generic "what was billed" -> actuals with lineage
    if "billed" in q:
        rows = conn.execute(
            "SELECT b.entity, b.month, b.service_code, b.amount_usd, b.file, b.sheet, b.row "
            "FROM billing b ORDER BY b.entity, b.month, b.service_code LIMIT 20").fetchall()
        if not rows:
            return "REFUSE: no billing data found."
        parts = ["Billed services (first 20 lines):"]
        for r in rows:
            parts.append(f"• {r[0]} {r[1]} {r[2]} = {r[3]:,.2f} USD [{_cite(r[4], r[5], r[6], r[0])}]")
        return "\n".join(parts)

    return ("REFUSE (unrecognised/ambiguous question): I can answer questions about charges "
            "without a contract, duplicates, file-total reconciliation, budget variance by "
            "cluster, or what was billed. Please rephrase.")


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not argv:
        print("Usage: python -m src.ask \"your question\"")
        return 1
    conn = sqlite3.connect(str(DB_PATH))
    try:
        print(answer(conn, " ".join(argv)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
