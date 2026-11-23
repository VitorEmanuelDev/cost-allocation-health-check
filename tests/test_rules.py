"""Regression tests for parsing helpers and validation rules (stdlib unittest)."""
from __future__ import annotations

import unittest
from decimal import Decimal

from src.config import parse_number
from src.ingest import BillingFile, BillingLine, DataBundle, parse_billing_filename
from src.validate import v3_duplicates, v8_reconciliation


def _line(service_code, amount_usd, amount_local, quantity, unit_price, row=4):
    return BillingLine(
        entity="A", month="2026-05", invoice_no="INV-A-202605", line_no="1",
        service_code=service_code, description="", quantity=Decimal(quantity),
        unit_price_local=Decimal(unit_price), amount_local=Decimal(amount_local),
        currency="USD", fx_rate_in_file=Decimal("1"), amount_usd=Decimal(amount_usd),
        file="01_Billing_Layouts/Entity_A/EntityA_Billing_2026-05.xlsx",
        sheet="Billing", row=row,
    )


class TestParsing(unittest.TestCase):
    def test_us_decimal(self):
        self.assertEqual(parse_number("0.0032"), Decimal("0.0032"))

    def test_brazilian_comma_decimal(self):
        self.assertEqual(parse_number("0,02"), Decimal("0.02"))

    def test_brazilian_thousands_and_decimal(self):
        self.assertEqual(parse_number("169.329,74"), Decimal("169329.74"))

    def test_integer(self):
        self.assertEqual(parse_number("48200775"), Decimal("48200775"))

    def test_filename_entity_a(self):
        self.assertEqual(parse_billing_filename("EntityA_Billing_2026-03.xlsx"), ("A", "2026-03"))

    def test_filename_entity_b(self):
        self.assertEqual(parse_billing_filename("Facturacion_EntB_abr26.xlsx"), ("B", "2026-04"))

    def test_filename_entity_c(self):
        self.assertEqual(parse_billing_filename("Faturamento_EntC_2026_09.xlsx"), ("C", "2026-09"))


class TestRules(unittest.TestCase):
    def _bundle(self, lines, raw_total_local=None, raw_total_usd=None):
        bf = BillingFile(
            entity="A", month="2026-05", currency="USD",
            file="01_Billing_Layouts/Entity_A/EntityA_Billing_2026-05.xlsx",
            sheet="Billing", lines=lines,
            raw_total_local=Decimal(raw_total_local) if raw_total_local is not None else None,
            raw_total_usd=Decimal(raw_total_usd) if raw_total_usd is not None else None,
        )
        return DataBundle(billing_files=[bf], contracts=[], pricing={}, budget={}, volumes={}, fx={})

    def test_duplicate_detected(self):
        lines = [
            _line("SVC-07", "38000", "38000", "1", "38000", row=10),
            _line("SVC-07", "38000", "38000", "1", "38000", row=18),
        ]
        findings = v3_duplicates(self._bundle(lines))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["usd_impact"], Decimal("38000"))

    def test_reconciliation_mismatch(self):
        lines = [_line("SVC-07", "38000", "38000", "1", "38000", row=4)]
        findings = v8_reconciliation(self._bundle(lines, raw_total_local="50400", raw_total_usd="50400"))
        self.assertTrue(any(f["rule_id"] == "V8" for f in findings))


if __name__ == "__main__":
    unittest.main()
