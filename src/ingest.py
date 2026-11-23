"""Ingestion: parse the 27 billing files + 3 reference workbooks into normalized structures."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .config import (
    BILLING_DIR,
    BUDGET_FILE,
    CONTRACT_CATALOG,
    LAYOUTS,
    ROOT,
    SPANISH_MONTHS,
    VOLUMES_FX,
    parse_int,
    parse_number,
)
from .xlsx_reader import read_xlsx


@dataclass
class BillingLine:
    entity: str
    month: str
    invoice_no: str
    line_no: str
    service_code: str
    description: str
    quantity: Decimal | None
    unit_price_local: Decimal | None
    amount_local: Decimal | None
    currency: str
    fx_rate_in_file: Decimal | None
    amount_usd: Decimal | None
    file: str
    sheet: str
    row: int  # 1-based source row number


@dataclass
class BillingFile:
    entity: str
    month: str
    currency: str
    file: str
    sheet: str
    lines: list[BillingLine] = field(default_factory=list)
    raw_total_local: Decimal | None = None
    raw_total_usd: Decimal | None = None
    header_labels: list[str] = field(default_factory=list)
    title_text: str = ""
    uses_comma_decimal: bool = False


@dataclass
class DataBundle:
    billing_files: list[BillingFile]
    contracts: list[dict]
    pricing: dict
    budget: dict
    volumes: dict
    fx: dict


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def parse_billing_filename(name: str) -> tuple[str, str]:
    """Return (entity, month) from a billing filename."""
    base = name.lower()
    if "entitya" in base:
        m = re.search(r"2026-(\d{2})", base)
        return "A", "2026-" + m.group(1)
    if "entb" in base:
        m = re.search(r"(ene|feb|mar|abr|may|jun|jul|ago|sep)(\d{2})", base)
        return "B", "2026-" + SPANISH_MONTHS[m.group(1)]
    if "entc" in base:
        m = re.search(r"2026_(\d{2})", base)
        return "C", "2026-" + m.group(1)
    raise ValueError(f"Unrecognised billing filename: {name}")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_billing_file(path: Path, entity: str, month: str) -> BillingFile:
    layout = LAYOUTS[entity]
    cols = layout["cols"]
    wb = read_xlsx(str(path))

    sheet_name = layout["sheet"]
    if sheet_name is None or sheet_name not in wb:
        sheet_name = next(iter(wb))
    rows = wb.get(sheet_name, [])
    header_row = layout["header_row"]

    header_labels = rows[header_row] if header_row < len(rows) else []
    title_text = " | ".join(" ".join(r).strip() for r in rows[:header_row]).strip()

    f = BillingFile(
        entity=entity,
        month=month,
        currency=layout["currency"],
        file=_rel(path),
        sheet=sheet_name,
        header_labels=[h.strip() for h in header_labels],
        title_text=title_text,
        uses_comma_decimal=any(("." in c and "," in c) for row in rows for c in row),
    )

    for i, row in enumerate(rows):
        if i <= header_row:
            continue
        first = _cell(row, 0).strip()
        if first.lower() == layout["total_marker"].lower():
            f.raw_total_local = parse_number(_cell(row, cols["amount"]))
            if cols["amount_usd"] is not None:
                f.raw_total_usd = parse_number(_cell(row, cols["amount_usd"]))
            else:
                f.raw_total_usd = f.raw_total_local
            continue

        service_code = _cell(row, cols["service_code"]).strip()
        if not service_code:
            continue

        amount_local = parse_number(_cell(row, cols["amount"]))
        fx = parse_number(_cell(row, cols["fx"])) if cols["fx"] is not None else Decimal("1")
        amount_usd = (
            parse_number(_cell(row, cols["amount_usd"]))
            if cols["amount_usd"] is not None
            else amount_local
        )

        f.lines.append(BillingLine(
            entity=entity,
            month=month,
            invoice_no=_cell(row, cols["invoice_no"]).strip(),
            line_no=_cell(row, cols["line_no"]).strip(),
            service_code=service_code,
            description=_cell(row, cols["description"]).strip(),
            quantity=parse_number(_cell(row, cols["quantity"])),
            unit_price_local=parse_number(_cell(row, cols["unit_price"])),
            amount_local=amount_local,
            currency=layout["currency"],
            fx_rate_in_file=fx,
            amount_usd=amount_usd,
            file=_rel(path),
            sheet=sheet_name,
            row=i + 1,
        ))
    return f


def parse_contracts(wb: dict) -> list[dict]:
    rows = wb["Contracts"]
    out = []
    for row in rows[1:]:
        if not any(row):
            continue
        cid = row[0].strip()
        if not cid:
            continue
        out.append({
            "contract_id": cid,
            "vendor": row[1].strip(),
            "description": row[2].strip(),
            "start_date": row[3].strip(),
            "end_date": row[4].strip(),
            "entities": [e.strip() for e in row[5].split(",") if e.strip()],
            "link": row[6].strip() if len(row) > 6 else "",
        })
    return out


def parse_pricing(wb: dict) -> dict:
    rows = wb["Service_Pricing"]
    out = {}
    for row in rows[1:]:
        if not any(row):
            continue
        sc = row[0].strip()
        if not sc:
            continue
        ent = row[3].strip()
        out[(sc, ent)] = {
            "service_code": sc,
            "service_name": row[1].strip(),
            "contract_id": row[2].strip(),
            "entity": ent,
            "pricing_model": row[4].strip(),
            "price_usd": parse_number(row[5]),
            "billing_unit": row[6].strip(),
        }
    return out


def parse_budget(wb: dict) -> dict:
    rows = wb["Budget_2026"]
    out = {}
    for row in rows[1:]:
        if not any(row):
            continue
        ent = row[0].strip().replace("Entity ", "")
        month = row[1].strip()
        sc = row[2].strip()
        out[(ent, month, sc)] = parse_number(row[4])
    return out


def parse_volumes(wb: dict) -> dict:
    rows = wb["Volumes"]
    out = {}
    for row in rows[1:]:
        if not any(row):
            continue
        ent = row[0].strip().replace("Entity ", "")
        month = row[1].strip()
        metric = row[2].strip()
        out[(ent, month, metric)] = parse_int(row[3])
    return out


def parse_fx(wb: dict) -> dict:
    rows = wb["FX_Rates"]
    out = {}
    for row in rows[1:]:
        if not any(row):
            continue
        month = row[0].strip()
        currency = row[1].strip()
        out[(month, currency)] = parse_number(row[2])
    return out


def discover_billing_files() -> list[Path]:
    return sorted(BILLING_DIR.rglob("*.xlsx"))


def ingest() -> DataBundle:
    billing_files = []
    for path in discover_billing_files():
        entity, month = parse_billing_filename(path.name)
        billing_files.append(parse_billing_file(path, entity, month))

    catalog = read_xlsx(str(CONTRACT_CATALOG))
    budget_wb = read_xlsx(str(BUDGET_FILE))
    volumes_fx_wb = read_xlsx(str(VOLUMES_FX))

    return DataBundle(
        billing_files=billing_files,
        contracts=parse_contracts(catalog),
        pricing=parse_pricing(catalog),
        budget=parse_budget(budget_wb),
        volumes=parse_volumes(volumes_fx_wb),
        fx=parse_fx(volumes_fx_wb),
    )

