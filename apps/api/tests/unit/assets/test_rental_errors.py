import pytest

from app.asset_management.rental_service import RentalConflict, RentalInvalid
from app.asset_management.router import _raise_asset_error
from app.core.errors import ProblemError


@pytest.mark.parametrize("error", [RentalConflict, RentalInvalid])
def test_unknown_rental_error_never_exposes_exception_text(error):
    with pytest.raises(ProblemError) as raised:
        _raise_asset_error(error("private rental note / database detail"))
    assert "private" not in raised.value.detail
    assert "database" not in raised.value.detail
    assert raised.value.detail != "资产操作失败"
    assert raised.value.code in {"asset_rental_conflict", "asset_rental_invalid"}
