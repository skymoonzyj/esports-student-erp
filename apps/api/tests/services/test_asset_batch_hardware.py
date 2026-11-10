from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.asset_management.schemas import AssetCreate
from app.procurement.schemas import PurchaseCreate

HARDWARE = {
    "cpu_model": "Ryzen 7",
    "memory_capacity": "32GB",
    "gpu_model": "核显",
    "disk_capacity": "1TB SSD + 2TB HDD",
}


@pytest.mark.parametrize("category", ["台式电脑", "笔记本电脑"])
def test_new_computer_requires_complete_hardware(category):
    with pytest.raises(ValidationError):
        AssetCreate(name="电脑", category=category)


@pytest.mark.parametrize(
    "field,limit",
    [("cpu_model", 120), ("memory_capacity", 80), ("gpu_model", 120), ("disk_capacity", 120)],
)
def test_hardware_limits_and_blank_normalization(field, limit):
    with pytest.raises(ValidationError):
        AssetCreate(name="电脑", **{field: "x" * (limit + 1)})
    value = AssetCreate(name="普通资产", **{field: "   "})
    assert getattr(value, field) is None


def test_procurement_hardware_requires_asset_intent_but_defers_completeness_for_replay():
    base = {
        "purchase_date": date(2026, 10, 28),
        "item_name": "采购电脑",
        "quantity": 2,
        "unit_price_cents": 100,
        "settlement_center_id": uuid4(),
    }
    with pytest.raises(ValidationError):
        PurchaseCreate(**base, asset_hardware=HARDWARE)
    # Only the service can distinguish a new purchase from a pre-feature replay.
    assert (
        PurchaseCreate(**base, add_to_assets=True, asset_category="台式电脑").asset_hardware is None
    )
    result = PurchaseCreate(
        **base, add_to_assets=True, asset_category="台式电脑", asset_hardware=HARDWARE
    )
    assert result.asset_hardware.disk_capacity == "1TB SSD + 2TB HDD"


def test_null_hardware_preserves_existing_purchase_fingerprint():
    import hashlib
    import json
    from uuid import UUID

    from app.procurement.service import ProcurementService

    center = UUID("10000000-0000-4000-8000-000000000001")
    input = PurchaseCreate(
        purchase_date=date(2026, 10, 28),
        item_name="采购电脑",
        quantity=2,
        unit_price_cents=100,
        settlement_center_id=center,
    )
    # Exact persisted pre-hardware request contract, independent of model_dump.
    legacy_payload = {
        "purchase_date": "2026-10-28",
        "item_name": "采购电脑",
        "purchase_channel": "offline",
        "product_url": None,
        "quantity": "2",
        "unit_price_cents": 100,
        "settlement_center_id": "10000000-0000-4000-8000-000000000001",
        "uses_reserve_fund": False,
        "is_reimbursed": False,
        "is_settled": False,
        "settled_on": None,
        "add_to_assets": False,
        "asset_category": None,
        "asset_warranty_expires_on": None,
        "note": None,
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    assert ProcurementService._fingerprint(input, center_id=center) == expected
