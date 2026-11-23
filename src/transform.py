"""Transformation: load validated data into SQLite and build marts with SQL."""
from __future__ import annotations

import sqlite3

from .config import CLUSTER, DB_PATH, OUTPUT_DIR
from .ingest import DataBundle

_SCHEMA = """
CREATE TABLE IF NOT EXISTS taxonomy (
    service_code TEXT PRIMARY KEY,
    cluster TEXT,
    service_name TEXT
);
CREATE TABLE IF NOT EXISTS billing (
    entity TEXT, month TEXT, service_code TEXT, cluster TEXT, service_name TEXT,
    quantity REAL, unit_price_local REAL, amount_local REAL, currency TEXT,
    fx_rate REAL, amount_usd REAL, file TEXT, sheet TEXT, row INTEGER
);
CREATE TABLE IF NOT EXISTS findings (
    rule_id TEXT, severity TEXT, entity TEXT, month TEXT, service_code TEXT,
    file TEXT, sheet TEXT, row INTEGER, message TEXT, usd_impact REAL
);
CREATE TABLE IF NOT EXISTS budget (
    entity TEXT, month TEXT, service_code TEXT, budget_usd REAL
);
"""

_MARTS = """
DROP TABLE IF EXISTS actuals;
CREATE TABLE actuals AS
SELECT entity, month, service_code, cluster, service_name,
       SUM(amount_usd) AS amount_usd, SUM(quantity) AS quantity
FROM billing
GROUP BY entity, month, service_code, cluster, service_name;

DROP TABLE IF EXISTS budget_vs_actual;
CREATE TABLE budget_vs_actual AS
SELECT a.entity, a.month, a.service_code, a.cluster, a.service_name,
       a.amount_usd AS actual_usd,
       COALESCE(b.budget_usd, 0) AS budget_usd,
       a.amount_usd - COALESCE(b.budget_usd, 0) AS variance_usd,
       CASE WHEN b.budget_usd IS NOT NULL AND b.budget_usd != 0
            THEN (a.amount_usd - b.budget_usd) * 100.0 / b.budget_usd
            ELSE NULL END AS variance_pct
FROM actuals a
LEFT JOIN budget b ON b.entity = a.entity AND b.month = a.month AND b.service_code = a.service_code;

DROP TABLE IF EXISTS reconciliation;
CREATE TABLE reconciliation AS
SELECT file, entity, month,
       SUM(amount_local) AS sum_local, 0 AS total_local, 0 AS diff_local,
       SUM(amount_usd) AS sum_usd, 0 AS total_usd, 0 AS diff_usd
FROM billing
GROUP BY file, entity, month;
"""


def _to_float(v) -> float | None:
    return float(v) if v is not None else None


def build_db(bundle: DataBundle, findings: list[dict]) -> sqlite3.Connection:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    for t in ["billing", "taxonomy", "findings", "budget", "actuals", "budget_vs_actual", "reconciliation"]:
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    for stmt in _SCHEMA.split(";"):
        if stmt.strip():
            cur.execute(stmt)

    # taxonomy
    seen = set()
    for sc, cluster in CLUSTER.items():
        name = ""
        for (s, e), p in bundle.pricing.items():
            if s == sc:
                name = p["service_name"]
                break
        cur.execute("INSERT OR REPLACE INTO taxonomy VALUES (?,?,?)", (sc, cluster, name))
        seen.add(sc)

    # billing
    for bf in bundle.billing_files:
        for ln in bf.lines:
            pricing = bundle.pricing.get((ln.service_code, bf.entity))
            service_name = pricing["service_name"] if pricing else ln.description
            cluster = CLUSTER.get(ln.service_code, "Unmapped")
            cur.execute(
                "INSERT INTO billing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (bf.entity, bf.month, ln.service_code, cluster, service_name,
                 _to_float(ln.quantity), _to_float(ln.unit_price_local), _to_float(ln.amount_local),
                 bf.currency, _to_float(ln.fx_rate_in_file), _to_float(ln.amount_usd),
                 bf.file, bf.sheet, ln.row),
            )

    # findings
    for f in findings:
        cur.execute(
            "INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f["rule_id"], f["severity"], f["entity"], f["month"], f["service_code"],
             f["file"], f["sheet"], f["row"], f["message"], _to_float(f["usd_impact"])),
        )

    # budget
    for (entity, month, sc), amt in bundle.budget.items():
        cur.execute("INSERT INTO budget VALUES (?,?,?,?)", (entity, month, sc, _to_float(amt)))

    # marts
    for stmt in _MARTS.split(";"):
        if stmt.strip():
            cur.execute(stmt)

    # fill reconciliation totals from parsed file totals
    for bf in bundle.billing_files:
        cur.execute(
            "UPDATE reconciliation SET total_local=?, diff_local=sum_local-?, total_usd=?, diff_usd=sum_usd-? "
            "WHERE file=? AND entity=? AND month=?",
            (_to_float(bf.raw_total_local), _to_float(bf.raw_total_local),
             _to_float(bf.raw_total_usd), _to_float(bf.raw_total_usd),
             bf.file, bf.entity, bf.month),
        )

    conn.commit()
    return conn
