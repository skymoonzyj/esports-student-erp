from __future__ import annotations

import csv
from contextlib import nullcontext
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest

from app.revenue_share import router

IDENTITY = UUID("00000000-0000-0000-0000-000000000001")
SECOND_IDENTITY = UUID("00000000-0000-0000-0000-000000000002")


def _export_rows(*names: tuple[str, str]) -> tuple[list[list[str]], list[dict[str, object]]]:
    identities = {"organization": IDENTITY, "person": SECOND_IDENTITY}
    balances = [
        SimpleNamespace(
            beneficiary=SimpleNamespace(kind=kind, id=identities[kind]),
            earned_cents=100,
            settled_cents=0,
            outstanding_cents=100,
        )
        for kind, _ in names
    ]
    names_by_beneficiary = {
        (balance.beneficiary.kind, balance.beneficiary.id): name
        for balance, (_, name) in zip(balances, names, strict=False)
    }
    service = SimpleNamespace(
        balances=lambda: balances,
        beneficiary_names=lambda values: names_by_beneficiary,
    )
    audits: list[dict[str, object]] = []
    request = SimpleNamespace(state=SimpleNamespace(request_id="csv-test"))
    context = SimpleNamespace(account=SimpleNamespace(id=IDENTITY))
    with patch.object(router, "_require_permission"), patch.object(
        router, "RevenueShareService", return_value=service
    ), patch.object(router, "write_transaction", return_value=nullcontext()), patch.object(
        router,
        "AuditService",
        return_value=SimpleNamespace(append=lambda **kwargs: audits.append(kwargs)),
    ):
        result = router.export_finance_summary(
            router.RevenueShareExportRequest(purpose="CSV 安全回归"),
            request,
            context,
            object(),
        )
    rows = list(csv.reader(StringIO(result.body.decode("utf-8-sig"), newline="")))
    return rows, audits


@pytest.mark.parametrize(
    ("kind", "name", "expected"),
    [
        ("organization", "=1+1", "'=1+1"),
        ("organization", "+1+1", "'+1+1"),
        ("organization", "-1+1", "'-1+1"),
        ("organization", "@SUM(1)", "'@SUM(1)"),
        ("organization", "\t=1+1", "'\t=1+1"),
        ("organization", "\r=1+1", "'\r=1+1"),
        ("organization", " =1+1", "' =1+1"),
        ("organization", "\n=1+1", "'\n=1+1"),
        ("organization", "\u3000+1+1", "'\u3000+1+1"),
        ("person", " \t@SUM(1)", "' \t@SUM(1)"),
        ("person", " 张三", " 张三"),
    ],
)
def test_finance_csv_escapes_formula_and_control_prefixes(
    kind: str, name: str, expected: str
) -> None:
    rows, _ = _export_rows((kind, name))

    assert rows[1][1] == expected


def test_finance_csv_escapes_person_and_organization_names_but_preserves_csv_text() -> None:
    rows, audits = _export_rows(
        ("organization", "=机构,含逗号\n第二行"),
        ("person", "张三·教练"),
    )

    assert rows == [
        ["收款对象类型", "收款对象", "累计分成（元）", "已结算（元）", "未结算（元）"],
        ["机构", "'=机构,含逗号\n第二行", "1.00", "0.00", "1.00"],
        ["个人", "张三·教练", "1.00", "0.00", "1.00"],
    ]
    assert audits[0]["action"] == "revenue_share.finance.export"
    assert audits[0]["metadata"] == {"item_count": 2}
