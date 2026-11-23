"""Configuration: paths, constants, taxonomy and shared parsing helpers."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BILLING_DIR = ROOT / "01_Billing_Layouts"
CONTRACT_CATALOG = ROOT / "02_Contract_Catalog.xlsx"
BUDGET_FILE = ROOT / "03_Budget_2026.xlsx"
VOLUMES_FX = ROOT / "04_Volumes_and_FX.xlsx"
OUTPUT_DIR = ROOT / "outputs"
DB_PATH = OUTPUT_DIR / "finance_health.db"

ENTITY_LABEL = {"A": "Entity A", "B": "Entity B", "C": "Entity C"}

# --- Service taxonomy (two-level: cluster -> service) ---
CLUSTER = {
    "SVC-01": "Payment Processing",
    "SVC-02": "Payment Processing",
    "SVC-03": "Payment Processing",
    "SVC-04": "Fraud & Risk",
    "SVC-05": "Fraud & Risk",
    "SVC-06": "Fraud & Risk",
    "SVC-07": "Tokenization",
    "SVC-08": "Tokenization",
    "SVC-09": "Infrastructure",
    "SVC-10": "Infrastructure",
    "SVC-11": "Operations & Support",
    "SVC-12": "Onboarding & Compliance",
    "SVC-13": "Onboarding & Compliance",
    "SVC-14": "Commercial Incentives",
}

# Which operational-volume metric each per-unit service is reconciled against.
SERVICE_METRIC = {
    "SVC-01": "Transactions",
    "SVC-02": "3DS authentications",
    "SVC-03": "DCC transactions",
    "SVC-04": "Transactions",
    "SVC-05": "Device checks",
    "SVC-06": "Chargeback alerts",
    "SVC-08": "Tokens provisioned",
    "SVC-12": "Merchant onboardings",
    "SVC-13": "KYC checks",
    "SVC-14": "Approved transactions",
}

# Fixed-monthly services: quantity must be 1, no volume metric.
FIXED_SERVICES = {"SVC-07", "SVC-09", "SVC-10", "SVC-11"}

# Expected service set per entity (used for completeness checks).
ALL_SERVICES = [f"SVC-{i:02d}" for i in range(1, 15)]
EXPECTED_SERVICES = {
    "A": set(ALL_SERVICES),                                   # 14
    "B": set(ALL_SERVICES),                                   # 14 (SVC-06 excluded Jul-Sep at runtime)
    "C": set(ALL_SERVICES) - {"SVC-03", "SVC-06"},            # 12
}
# CT-1006 (Chargeback Alerts) ends 2026-06-30 -> SVC-06 invalid from July onward.
SVC_06_END_MONTH = (2026, 6)

SPANISH_MONTHS = {
    "ene": "01", "feb": "02", "mar": "03", "abr": "04", "may": "05",
    "jun": "06", "jul": "07", "ago": "08", "sep": "09", "oct": "10",
    "nov": "11", "dic": "12",
}

# Per-entity layout definitions. Column indices are 0-based.
LAYOUTS = {
    "A": {
        "currency": "USD",
        "sheet": "Billing",
        "header_row": 2,
        "cols": {"invoice_no": 0, "line_no": 1, "service_code": 2, "description": 3,
                 "quantity": 4, "unit_price": 5, "amount": 6, "fx": None, "amount_usd": None},
        "total_marker": "TOTAL",
    },
    "B": {
        "currency": "EUR",
        "sheet": None,  # sheet name is the Spanish month abbrev
        "header_row": 0,
        "cols": {"invoice_no": 0, "line_no": 1, "service_code": 2, "description": 3,
                 "quantity": 4, "unit_price": 5, "amount": 6, "fx": 7, "amount_usd": 8},
        "total_marker": "Total",
    },
    "C": {
        "currency": "BRL",
        "sheet": "Detalhe",
        "header_row": 1,
        "cols": {"invoice_no": 0, "line_no": 1, "service_code": 2, "description": 3,
                 "quantity": 4, "unit_price": 5, "amount": 6, "fx": 7, "amount_usd": 8},
        "total_marker": "Total geral",
    },
}


def parse_number(text: str | None) -> Decimal | None:
    """Parse a number string robustly.

    Handles US (dot decimal) and Brazilian (comma decimal + dot thousands)
    formats. In this dataset a comma always means a decimal separator and a dot
    means thousands only when a comma is also present.
    """
    if text is None:
        return None
    s = str(text).strip().replace("\u00a0", "").replace(" ", "")
    if s in ("", "-", ".", "+", "None"):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_int(text: str | None) -> int | None:
    d = parse_number(text)
    return int(d) if d is not None else None


def month_key(month: str) -> tuple[int, int]:
    """'2026-01' -> (2026, 1)."""
    y, m = str(month).split("-")
    return (int(y), int(m))


def date_ym(date_str: str) -> tuple[int, int] | None:
    """'2025-01-01' -> (2025, 1)."""
    if not date_str:
        return None
    parts = str(date_str).split("-")
    return (int(parts[0]), int(parts[1]))
