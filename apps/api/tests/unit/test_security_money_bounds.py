from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.admissions.money import InvalidMoney, parse_cny
from app.procurement.schemas import PurchaseCreate, PurchaseUpdate
from app.procurement.service import ProcurementService, ProcurementServiceError


@pytest.mark.parametrize(
    "raw",
    [
        "1e1000000",
        "1e999999999999999999999999999999",
        "9" * 1000,
        "92233720368547758.08",
        "NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        "1.001",
        "1.000",
    ],
    ids=[
        "extreme-exponent",
        "oversized-exponent",
        "oversized-integer",
        "bigint-plus-one",
        "nan",
        "signaling-nan",
        "infinity",
        "negative-infinity",
        "sub-cent",
        "excess-scale",
    ],
)
def test_cny_rejects_unrepresentable_cents_as_domain_errors(raw: str) -> None:
    with pytest.raises(InvalidMoney):
        parse_cny(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("92233720368547758.07", 9_223_372_036_854_775_807),
        ("9.223372036854775807e16", 9_223_372_036_854_775_807),
        ("0.01", 1),
        ("12.34", 1234),
        ("1e2", 10000),
        ("1.23e2", 12300),
        ("1e-2", 1),
    ],
)
def test_cny_preserves_exact_cents_and_scientific_notation(raw: str, expected: int) -> None:
    assert parse_cny(raw) == expected


def _purchase(**amounts: object) -> dict[str, object]:
    return {
        "purchase_date": date(2026, 10, 25),
        "item_name": "Bounded purchase",
        "settlement_center_id": UUID(int=1),
        "quantity": "1",
        "unit_price_cents": 100,
        **amounts,
    }


@pytest.mark.parametrize("schema", [PurchaseCreate, PurchaseUpdate])
def test_purchase_schema_rejects_quantity_times_price_overflow(
    schema: type[PurchaseCreate] | type[PurchaseUpdate],
) -> None:
    payload: dict[str, object] = {"quantity": "2000000", "unit_price_cents": 9_000_000_000_000}
    if schema is PurchaseCreate:
        payload = _purchase(**payload)
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@pytest.mark.parametrize(
    "raw",
    ["1e1000000", "NaN", "sNaN", "Infinity", "9" * 1000],
    ids=["extreme-exponent", "nan", "signaling-nan", "infinity", "oversized-integer"],
)
def test_purchase_schema_rejects_extreme_quantities(raw: str) -> None:
    with pytest.raises(ValidationError):
        PurchaseCreate.model_validate(_purchase(quantity=raw))


@pytest.mark.parametrize(
    ("quantity", "unit_price", "expected"),
    [
        ("2.500", 1299, 3248),
        ("0.500", 1, 1),
        ("0.499", 1, 0),
        ("999999999.999", 9_223_372_036, 9_223_372_035_990_776_628),
    ],
)
def test_purchase_totals_preserve_half_up_and_large_valid_values(
    quantity: str, unit_price: int, expected: int
) -> None:
    model = PurchaseCreate.model_validate(
        _purchase(quantity=quantity, unit_price_cents=unit_price)
    )
    assert ProcurementService.calculate_total(model.quantity, model.unit_price_cents) == expected


@pytest.mark.parametrize(
    "quantity",
    ["2000000", "1e1000000", "NaN", "sNaN", "Infinity"],
)
def test_purchase_total_helper_rejects_overflow_as_domain_error(quantity: str) -> None:
    with pytest.raises(ProcurementServiceError):
        ProcurementService.calculate_total(Decimal(quantity), 9_000_000_000_000)
