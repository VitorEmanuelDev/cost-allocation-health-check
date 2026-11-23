"""Export: write CSVs and a self-contained HTML executive dashboard."""
from __future__ import annotations

import csv
import html
import sqlite3
from decimal import Decimal

from .config import OUTPUT_DIR


def _money(v) -> str:
    if v is None:
        return ""
    return f"{float(v):,.2f}"


def write_csvs(conn: sqlite3.Connection, findings: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_DIR / "actuals.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["entity", "month", "service_code", "cluster", "service_name", "amount_usd", "quantity"])
        for row in conn.execute("SELECT * FROM actuals ORDER BY entity, month, service_code"):
            w.writerow(row)

    with open(OUTPUT_DIR / "budget_vs_actual.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["entity", "month", "service_code", "cluster", "service_name",
                    "actual_usd", "budget_usd", "variance_usd", "variance_pct"])
        for row in conn.execute("SELECT * FROM budget_vs_actual ORDER BY entity, month, service_code"):
            w.writerow(row)

    with open(OUTPUT_DIR / "reconciliation.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "entity", "month", "sum_local", "total_local", "diff_local",
                    "sum_usd", "total_usd", "diff_usd"])
        for row in conn.execute("SELECT * FROM reconciliation ORDER BY file"):
            w.writerow(row)

    with open(OUTPUT_DIR / "findings.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rule_id", "severity", "entity", "month", "service_code",
                    "file", "sheet", "row", "message", "usd_impact"])
        for f in findings:
            w.writerow([f["rule_id"], f["severity"], f["entity"] or "", f["month"] or "",
                        f["service_code"] or "", f["file"] or "", f["sheet"] or "",
                        f["row"], f["message"],
                        "" if f["usd_impact"] is None else f"{float(f['usd_impact']):.2f}"])


def write_dashboard(conn: sqlite3.Connection, findings: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_actual = conn.execute("SELECT COALESCE(SUM(amount_usd),0) FROM actuals").fetchone()[0]
    # Compare like-for-like: budget only for the Jan–Sep scope that has actuals.
    total_budget = conn.execute("SELECT COALESCE(SUM(budget_usd),0) FROM budget_vs_actual").fetchone()[0]
    variance = total_actual - total_budget
    net_correction = sum(
        (Decimal(str(f["usd_impact"])) for f in findings
         if f["usd_impact"] is not None and f["severity"] in ("Critical", "High")),
        Decimal("0"),
    )

    by_cluster = conn.execute("""
        SELECT a.entity, a.cluster, SUM(a.amount_usd) AS actual,
               SUM(COALESCE(b.budget_usd,0)) AS budget
        FROM actuals a
        LEFT JOIN budget b ON b.entity=a.entity AND b.month=a.month AND b.service_code=a.service_code
        GROUP BY a.entity, a.cluster ORDER BY a.entity, a.cluster
    """).fetchall()

    monthly = conn.execute("""
        SELECT entity, month, SUM(actual_usd), SUM(budget_usd), SUM(variance_usd)
        FROM budget_vs_actual GROUP BY entity, month ORDER BY entity, month
    """).fetchall()

    rec = conn.execute("SELECT * FROM reconciliation ORDER BY file").fetchall()

    def kpi(label, value, cls):
        return (f'<div class="kpi {cls}"><div class="v">{_money(value)}</div>'
                f'<div class="l">{label}</div></div>')

    cards = "".join([
        kpi("Total Actual (USD)", total_actual, "a"),
        kpi("Total Budget (USD)", total_budget, "b"),
        kpi("Variance (Actual-Budget)", variance, "v"),
        kpi("Net correction from findings", net_correction, "n"),
    ])

    fr = "".join(
        f"<tr><td>{html.escape(f['severity'])}</td><td>{html.escape(f['rule_id'])}</td>"
        f"<td>{html.escape(f['entity'] or '')}</td><td>{html.escape(f['month'] or '')}</td>"
        f"<td>{html.escape(f['service_code'] or '')}</td>"
        f"<td class='mono'>{html.escape(f['file'] or '')} → {html.escape(f['sheet'] or '')}</td>"
        f"<td>{f['row']}</td><td>{html.escape(f['message'])}</td>"
        f"<td class='num'>{_money(f['usd_impact'])}</td></tr>"
        for f in findings
    )

    cluster_rows = "".join(
        f"<tr><td>{html.escape(r[0])}</td><td>{html.escape(r[1])}</td>"
        f"<td class='num'>{_money(r[2])}</td><td class='num'>{_money(r[3])}</td>"
        f"<td class='num'>{_money(r[2]-r[3])}</td></tr>"
        for r in by_cluster
    )

    month_rows = "".join(
        f"<tr><td>{html.escape(r[0])}</td><td>{html.escape(r[1])}</td>"
        f"<td class='num'>{_money(r[2])}</td><td class='num'>{_money(r[3])}</td>"
        f"<td class='num'>{_money(r[4])}</td></tr>"
        for r in monthly
    )

    rec_rows = "".join(
        f"<tr><td class='mono'>{html.escape(r[0])}</td><td>{html.escape(r[1])}</td>"
        f"<td>{html.escape(r[2])}</td>"
        f"<td class='num'>{_money(r[3])}</td><td class='num'>{_money(r[4])}</td>"
        f"<td class='num'>{_money(r[5])}</td><td class='num'>{_money(r[6])}</td>"
        f"<td class='num'>{_money(r[7])}</td><td class='num'>{_money(r[8])}</td></tr>"
        for r in rec
    )

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Cost Allocation Health-Check</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1a1a2e;background:#f5f6fa}}
h1{{margin:0 0 4px}} .sub{{color:#555;margin-bottom:18px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}}
.kpi{{flex:1;min-width:180px;background:#fff;border:1px solid #e2e4ea;border-radius:10px;padding:14px 16px}}
.kpi .v{{font-size:22px;font-weight:600}} .kpi .l{{font-size:12px;color:#777;margin-top:4px}}
.kpi.v .v{{color:#c0392b}} .kpi.n .v{{color:#b8860b}}
h2{{font-size:16px;margin:22px 0 8px;border-bottom:2px solid #e2e4ea;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
th,td{{border:1px solid #e2e4ea;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#eef0f6}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.mono{{font-family:Consolas,monospace;font-size:12px}}
</style></head><body>
<h1>Cost Allocation Health-Check</h1>
<div class="sub">Actual vs budget and data-quality findings · reporting currency USD · Jan–Sep 2026</div>
<div class="cards">{cards}</div>
<h2>Findings ({len(findings)})</h2>
<table><tr><th>Severity</th><th>Rule</th><th>Entity</th><th>Month</th><th>Service</th>
<th>Evidence (file → sheet)</th><th>Row</th><th>Message</th><th>USD impact</th></tr>{fr}</table>
<h2>Actual vs Budget by entity × cluster</h2>
<table><tr><th>Entity</th><th>Cluster</th><th>Actual USD</th><th>Budget USD</th><th>Variance USD</th></tr>{cluster_rows}</table>
<h2>Actual vs Budget by entity × month</h2>
<table><tr><th>Entity</th><th>Month</th><th>Actual USD</th><th>Budget USD</th><th>Variance USD</th></tr>{month_rows}</table>
<h2>File-total reconciliation</h2>
<table><tr><th>File</th><th>Entity</th><th>Month</th><th>Sum local</th><th>Total local</th>
<th>Diff local</th><th>Sum USD</th><th>Total USD</th><th>Diff USD</th></tr>{rec_rows}</table>
</body></html>"""

    with open(OUTPUT_DIR / "dashboard.html", "w", encoding="utf-8") as fh:
        fh.write(page)

