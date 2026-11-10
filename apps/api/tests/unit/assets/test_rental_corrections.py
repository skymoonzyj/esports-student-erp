from datetime import date
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.asset_management.models import Asset
from app.asset_management.rental_service import RentalConflict, validate_asset_rental_update
from app.asset_management.schemas import AssetUpdate


def test_started_rental_cannot_be_moved_into_future_to_remove_ownership_guard(monkeypatch):
    monkeypatch.setattr(
        "app.asset_management.rental_service.business_today", lambda: date(2026, 10, 25)
    )
    asset = Asset(
        id=uuid4(),
        ownership_type="rented",
        rental_started_on=date(2026, 1, 1),
        rental_expires_on=date(2027, 1, 31),
        rental_revision=0,
        rental_returned_on=None,
    )
    db = Mock(spec=Session)
    db.scalar.return_value = None
    with pytest.raises(RentalConflict):
        validate_asset_rental_update(
            db, asset, AssetUpdate(rental_started_on=date(2026, 10, 26), expected_rental_revision=0)
        )
    assert asset.rental_started_on == date(2026, 1, 1)
    assert asset.rental_revision == 0
