from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import ProblemError
from app.revenue_share.errors import (
    RevenueShareSettlementOperationConflict,
    RevenueShareSettlementReferenceConflict,
)
from app.revenue_share.router import _raise_revenue_share_error
from app.revenue_share.schemas import RevenueShareSettlementCreate


def test_settlement_requires_valid_client_operation_uuid() -> None:
    payload = {
        "beneficiary_kind": "person",
        "beneficiary_id": str(uuid4()),
        "amount_cents": 100,
        "settled_on": date(2026, 9, 11),
        "reference": "BANK-01",
    }
    for key in (None, "not-a-uuid"):
        with pytest.raises(ValidationError):
            RevenueShareSettlementCreate.model_validate({**payload, "idempotency_key": key})
    with pytest.raises(ValidationError):
        RevenueShareSettlementCreate.model_validate(payload)


@pytest.mark.parametrize(
    "error, expected",
    [
        (RevenueShareSettlementOperationConflict("changed"), "重试内容与原记录不同"),
        (RevenueShareSettlementReferenceConflict("duplicate"), "打款凭证号已登记"),
    ],
)
def test_settlement_conflicts_explain_reason_in_chinese(
    error: RevenueShareSettlementOperationConflict | RevenueShareSettlementReferenceConflict,
    expected: str,
) -> None:
    with pytest.raises(ProblemError) as caught:
        _raise_revenue_share_error(error)
    assert caught.value.status == 409
    assert caught.value.code == error.code
    assert expected in caught.value.detail
