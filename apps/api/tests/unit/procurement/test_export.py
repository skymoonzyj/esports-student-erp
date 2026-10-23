from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.procurement.export_service import render_procurement_export


@pytest.mark.parametrize("amounts", [[], [2500], [10, 20, 33]])
def test_reimbursement_formulas_preserve_exact_cached_totals(amounts: list[int]) -> None:
    records = [
        SimpleNamespace(
            purchase_date=date(2026, 9, 11),
            purchase_channel="offline",
            item_name=f"测试采购{index}",
            quantity=Decimal(1),
            unit_price_cents=cents,
            total_price_cents=cents,
            settlement_center=SimpleNamespace(name="测试结算中心"),
            uses_reserve_fund=False,
            invoice_link=None,
            note=None,
        )
        for index, cents in enumerate(amounts)
    ]
    content, *_ = render_procurement_export(
        records,
        format="xlsx",
        scope="unreimbursed",
        applicant_name="测试人员",
        occurred_at=datetime(2026, 9, 11, tzinfo=UTC),
    )
    formulas = load_workbook(BytesIO(content), data_only=False)
    cached = load_workbook(BytesIO(content), data_only=True)
    try:
        detail_end = 5 + max(1, len(amounts))
        subtotal_row = detail_end + 1
        total_row = subtotal_row + 1
        expected = Decimal(sum(amounts)) / 100
        assert formulas.active[f"F{subtotal_row}"].value == f"=SUM(F6:F{detail_end})"
        assert formulas.active[f"F{total_row}"].value == f"=F{subtotal_row}"
        assert formulas.active["H2"].value == f"=F{total_row}"
        for reference in (f"F{subtotal_row}", f"F{total_row}", "H2"):
            assert Decimal(str(cached.active[reference].value)) == expected
        for index, cents in enumerate(amounts, start=6):
            assert Decimal(str(cached.active[f"F{index}"].value)) == Decimal(cents) / 100
    finally:
        formulas.close()
        cached.close()
