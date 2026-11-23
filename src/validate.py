"""Validation: apply business rules V1–V10 and produce an evidence-backed findings register."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .config import (
    EXPECTED_SERVICES,
    FIXED_SERVICES,
    SERVICE_METRIC,
    SVC_06_END_MONTH,
    date_ym,
    month_key,
)
from .ingest import DataBundle

EPS = Decimal("0.01")
PRICE_EPS = Decimal("0.0001")


def _f(rule, sev, entity, month, service_code, file, sheet, row, message, usd_impact=None):
    return {
        "rule_id": rule,
        "severity": sev,
        "entity": entity,
        "month": month,
        "service_code": service_code,
        "file": file,
        "sheet": sheet,
        "row": row,
        "message": message,
        "usd_impact": usd_impact,
    }


def _header_ok(header_labels: list[str], entity: str) -> bool:
    joined = " ".join(header_labels).lower()
    if entity == "A":
        return "service" in joined and "usd" in joined
    if entity == "B":
        return ("servicio" in joined or "serv" in joined) and "usd" in joined
    if entity == "C":
        return ("serviço" in joined or "serv" in joined) and "usd" in joined
    return True


def v1_valid_file(bundle: DataBundle) -> list[dict]:
    out = []
    for bf in bundle.billing_files:
        if not _header_ok(bf.header_labels, bf.entity):
            out.append(_f(
                "V1", "Critical", bf.entity, bf.month, None, bf.file, bf.sheet, 0,
                "Header row does not match the expected layout.", None,
            ))
        if bf.entity == "A" and "USD" not in bf.title_text.upper():
            out.append(_f("V1", "Critical", bf.entity, bf.month, None, bf.file, bf.sheet, 0,
                          "Currency token USD missing from title/period row.", None))
        if bf.entity == "C" and "BRL" not in bf.title_text.upper():
            out.append(_f("V1", "Critical", bf.entity, bf.month, None, bf.file, bf.sheet, 0,
                          "Currency token BRL missing from title row.", None))

        if bf.uses_comma_decimal:
            out.append(_f(
                "V1", "Medium", bf.entity, bf.month, None, bf.file, bf.sheet, 0,
                "Brazilian number format detected (comma decimals / dot thousands). "
                "Locale/precision hazard for naive parsers; unit prices are rounded.",
                None,
            ))

        for ln in bf.lines:
            if (ln.amount_usd is not None and ln.amount_usd < 0) or "credit" in ln.description.lower():
                out.append(_f(
                    "V1", "Info", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"Credit note / adjustment line ({ln.description}); preserved and tracked, "
                    "not treated as a duplicate.", ln.amount_usd,
                ))
    return out


def v2_completeness(bundle: DataBundle) -> list[dict]:
    out = []
    for bf in bundle.billing_files:
        expected = set(EXPECTED_SERVICES[bf.entity])
        if bf.entity == "B" and month_key(bf.month) > SVC_06_END_MONTH:
            expected.discard("SVC-06")
        actual = {ln.service_code for ln in bf.lines}
        for svc in sorted(expected - actual):
            budget_usd = bundle.budget.get((bf.entity, bf.month, svc))
            impact = -budget_usd if budget_usd is not None else None
            out.append(_f(
                "V2", "High", bf.entity, bf.month, svc, bf.file, bf.sheet, 0,
                f"Missing expected service {svc} in this billing file.", impact,
            ))
    return out


def v3_duplicates(bundle: DataBundle) -> list[dict]:
    out = []
    groups = defaultdict(list)
    for bf in bundle.billing_files:
        for ln in bf.lines:
            # adjustments/credit notes are not duplicate charges
            if (ln.amount_usd is not None and ln.amount_usd < 0) or "credit" in ln.description.lower():
                continue
            groups[(bf.entity, bf.month, ln.service_code)].append(ln)
    for (entity, month, svc), lines in groups.items():
        if len(lines) > 1:
            lines_sorted = sorted(lines, key=lambda x: x.row)
            extra = lines_sorted[1:]
            impact = sum((l.amount_usd or 0) for l in extra)
            evidence = ", ".join(f"row {l.row}" for l in lines_sorted)
            out.append(_f(
                "V3", "High", entity, month, svc, lines_sorted[0].file, lines_sorted[0].sheet,
                lines_sorted[1].row,
                f"{svc} billed {len(lines)} times in the same month ({evidence}).", impact,
            ))
    return out


def v4_fx_consistency(bundle: DataBundle) -> list[dict]:
    out = []
    # aggregate by file to avoid one finding per line
    per_file = defaultdict(lambda: {"bf": None, "total": Decimal("0"), "impact": Decimal("0"), "ref": None, "file_fx": None})
    for bf in bundle.billing_files:
        if bf.entity == "A":
            continue
        ref = bundle.fx.get((bf.month, bf.currency))
        if ref is None:
            continue
        for ln in bf.lines:
            if ln.fx_rate_in_file is None:
                continue
            diff = ln.fx_rate_in_file - ref
            if abs(diff) > Decimal("1e-9"):
                key = bf.file
                per_file[key]["bf"] = bf
                per_file[key]["ref"] = ref
                per_file[key]["file_fx"] = ln.fx_rate_in_file
                per_file[key]["total"] += (ln.amount_local or 0)
                per_file[key]["impact"] += (ln.amount_local or 0) * diff
    for key, d in per_file.items():
        bf = d["bf"]
        out.append(_f(
            "V4", "Critical", bf.entity, bf.month, None, bf.file, bf.sheet, 0,
            f"FX rate {d['file_fx']} in file differs from Treasury rate {d['ref']} "
            f"for {bf.month} {bf.currency}.", round(d["impact"], 2),
        ))
    return out


def v5_price_consistency(bundle: DataBundle) -> list[dict]:
    out = []
    for bf in bundle.billing_files:
        if bf.uses_comma_decimal:
            continue  # locale file: prices are rounded for display; amounts use full precision
        for ln in bf.lines:
            if (ln.amount_usd is not None and ln.amount_usd < 0) or "credit" in ln.description.lower():
                continue
            pricing = bundle.pricing.get((ln.service_code, bf.entity))
            if pricing is None or pricing["price_usd"] is None:
                continue  # covered by V9
            ref = bundle.fx.get((bf.month, bf.currency), Decimal("1")) if bf.entity != "A" else Decimal("1")
            expected_local = pricing["price_usd"] / ref
            if ln.unit_price_local is None:
                continue
            tol = max(PRICE_EPS, abs(expected_local) * Decimal("0.002"))
            if abs(ln.unit_price_local - expected_local) > tol:
                impact = (ln.unit_price_local - expected_local) * (ln.quantity or 0) * ref
                out.append(_f(
                    "V5", "High", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"Unit price {ln.unit_price_local} differs from catalog/FX derived "
                    f"{expected_local.quantize(Decimal('0.000001'))}.", round(impact, 2),
                ))
    return out


def v6_quantity_reconciliation(bundle: DataBundle) -> list[dict]:
    out = []
    for bf in bundle.billing_files:
        for ln in bf.lines:
            if (ln.amount_usd is not None and ln.amount_usd < 0) or "credit" in ln.description.lower():
                continue
            if ln.service_code in FIXED_SERVICES:
                expected = Decimal("1")
            else:
                metric = SERVICE_METRIC.get(ln.service_code)
                if metric is None:
                    continue  # unknown service; covered by V9
                vol = bundle.volumes.get((bf.entity, bf.month, metric))
                if vol is None:
                    continue  # no volume metric for this entity/service; covered by V9
                expected = Decimal(vol)
            if ln.quantity is None or ln.quantity == expected:
                continue
            delta = ln.quantity - expected
            unit = ln.unit_price_local or 0
            fx = ln.fx_rate_in_file or Decimal("1")
            impact = delta * unit * fx
            out.append(_f(
                "V6", "Critical", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                f"{ln.service_code} quantity {ln.quantity} does not match operational volume "
                f"{expected} (metric: {SERVICE_METRIC.get(ln.service_code, 'fixed')}).",
                round(impact, 2),
            ))
    return out


def v7_amount_computation(bundle: DataBundle) -> list[dict]:
    out = []
    for bf in bundle.billing_files:
        if bf.uses_comma_decimal:
            continue  # display-rounded unit prices (Brazilian locale); amounts use full precision
        for ln in bf.lines:
            if ln.quantity is None or ln.unit_price_local is None or ln.amount_local is None:
                continue
            computed_local = ln.quantity * ln.unit_price_local
            tol = max(EPS, abs(computed_local) * Decimal("0.001"))
            if abs(ln.amount_local - computed_local) > tol:
                out.append(_f(
                    "V7", "Critical", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"Amount (local) {ln.amount_local} != quantity x unit price {computed_local}.", None,
                ))
            if bf.entity != "A" and ln.amount_usd is not None and ln.fx_rate_in_file is not None:
                computed_usd = ln.amount_local * ln.fx_rate_in_file
                tol_usd = max(EPS, abs(computed_usd) * Decimal("0.001"))
                if abs(ln.amount_usd - computed_usd) > tol_usd:
                    out.append(_f(
                        "V7", "Critical", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                        f"Amount (USD) {ln.amount_usd} != amount local x FX {computed_usd}.", None,
                    ))
    return out


def v8_reconciliation(bundle: DataBundle) -> list[dict]:
    out = []
    for bf in bundle.billing_files:
        sum_local = sum((ln.amount_local or 0) for ln in bf.lines)
        sum_usd = sum((ln.amount_usd or 0) for ln in bf.lines)
        local_mismatch = bf.raw_total_local is not None and abs(sum_local - bf.raw_total_local) > EPS
        usd_mismatch = bf.raw_total_usd is not None and abs(sum_usd - bf.raw_total_usd) > EPS
        if local_mismatch or usd_mismatch:
            impact = round((bf.raw_total_usd or 0) - sum_usd, 2)  # + means file TOTAL overstates
            out.append(_f(
                "V8", "Critical", bf.entity, bf.month, None, bf.file, bf.sheet, 0,
                f"File TOTAL does not reconcile with line sum — local: {sum_local.quantize(Decimal('0.01'))} "
                f"vs {bf.raw_total_local.quantize(Decimal('0.01')) if bf.raw_total_local is not None else '?'}; "
                f"USD: {sum_usd.quantize(Decimal('0.01'))} vs "
                f"{bf.raw_total_usd.quantize(Decimal('0.01')) if bf.raw_total_usd is not None else '?'}.",
                impact,
            ))
    return out


def v9_contract_coverage(bundle: DataBundle) -> list[dict]:
    out = []
    contracts = {c["contract_id"]: c for c in bundle.contracts}
    for bf in bundle.billing_files:
        for ln in bf.lines:
            pricing = bundle.pricing.get((ln.service_code, bf.entity))
            if pricing is None:
                out.append(_f(
                    "V9", "High", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"{ln.service_code} has no Service_Pricing row for {bf.entity} "
                    "(no mapped contract / not covered).", ln.amount_usd,
                ))
                continue
            contract = contracts.get(pricing["contract_id"])
            if contract is None:
                out.append(_f(
                    "V9", "High", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"Contract {pricing['contract_id']} referenced by {ln.service_code} not found "
                    "in the contract catalog.", ln.amount_usd,
                ))
                continue
            if bf.entity not in contract["entities"]:
                out.append(_f(
                    "V9", "High", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"Contract {contract['contract_id']} does not cover {bf.entity}.", ln.amount_usd,
                ))
                continue
            m = month_key(bf.month)
            start = date_ym(contract["start_date"])
            end = date_ym(contract["end_date"])
            if end is not None and m > end:
                out.append(_f(
                    "V9", "High", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"Contract {contract['contract_id']} ended {contract['end_date']} but "
                    f"{ln.service_code} is billed in {bf.month}.", ln.amount_usd,
                ))
            elif start is not None and m < start:
                out.append(_f(
                    "V9", "High", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"Contract {contract['contract_id']} starts {contract['start_date']} but "
                    f"{ln.service_code} is billed in {bf.month}.", ln.amount_usd,
                ))
    return out


def v10_budget_coverage(bundle: DataBundle) -> list[dict]:
    out = []
    for bf in bundle.billing_files:
        for ln in bf.lines:
            # informational only for services that are otherwise valid
            if (ln.service_code, bf.entity) not in bundle.pricing:
                continue
            if (bf.entity, bf.month, ln.service_code) not in bundle.budget:
                out.append(_f(
                    "V10", "Info", bf.entity, bf.month, ln.service_code, bf.file, bf.sheet, ln.row,
                    f"{ln.service_code} has no budget for {bf.entity} {bf.month}.", None,
                ))
    return out


def master_data_checks(bundle: DataBundle) -> list[dict]:
    out = []
    ids = sorted({c["contract_id"] for c in bundle.contracts})
    numeric = sorted(int(i.split("-")[1]) for i in ids if i.startswith("CT-") and i.split("-")[1].isdigit())
    if numeric:
        missing = [f"CT-{n:04d}" for n in range(numeric[0], numeric[-1] + 1)
                   if f"CT-{n:04d}" not in set(ids)]
        for cid in missing:
            out.append(_f(
                "V9", "Info", None, None, None, "02_Contract_Catalog.xlsx", "Contracts", 0,
                f"Contract ID {cid} is absent from the catalog sequence. Confirm with contract owner.",
                None,
            ))
    return out


SEV_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Info": 3}


def validate(bundle: DataBundle) -> list[dict]:
    findings = []
    findings += v1_valid_file(bundle)
    findings += v2_completeness(bundle)
    findings += v3_duplicates(bundle)
    findings += v4_fx_consistency(bundle)
    findings += v5_price_consistency(bundle)
    findings += v6_quantity_reconciliation(bundle)
    findings += v7_amount_computation(bundle)
    findings += v8_reconciliation(bundle)
    findings += v9_contract_coverage(bundle)
    findings += v10_budget_coverage(bundle)
    findings += master_data_checks(bundle)
    findings.sort(key=lambda f: (SEV_ORDER[f["severity"]], f["entity"] or "", f["month"] or "",
                                 f["service_code"] or "", f["rule_id"]))
    return findings


