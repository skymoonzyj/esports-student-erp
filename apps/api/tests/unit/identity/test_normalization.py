from __future__ import annotations

from datetime import date

import pytest

from app.identity.normalization import (
    IdentityValidationError,
    normalize_cn_id,
    normalize_phone,
)


def test_cn_id_normalizes_x_and_extracts_birth_date() -> None:
    normalized = normalize_cn_id("11010519491231002x")

    assert normalized.number == "11010519491231002X"
    assert normalized.birth_date == date(1949, 12, 31)
    assert normalized.sex == "female"


def test_cn_id_birth_date_must_match() -> None:
    with pytest.raises(IdentityValidationError) as error:
        normalize_cn_id(
            "11010519491231002X",
            expected_birth_date=date(1949, 12, 30),
        )

    assert error.value.field == "date_of_birth"
    assert error.value.code == "identity_birth_date_mismatch"


@pytest.mark.parametrize(
    "identity_number",
    [
        "11010519491331002X",
        "110105194912310021",
        "11010519491231002?",
        "11010519491231002",
    ],
)
def test_cn_id_rejects_invalid_date_checksum_or_format(identity_number: str) -> None:
    with pytest.raises(IdentityValidationError) as error:
        normalize_cn_id(identity_number)

    assert error.value.field == "identity_number"


@pytest.mark.parametrize("phone", ["12800138000", "1380013800", "138001380000", "138-0013-8000"])
def test_phone_accepts_only_mainland_mobile_numbers(phone: str) -> None:
    with pytest.raises(IdentityValidationError) as error:
        normalize_phone(phone)

    assert error.value.field == "phone"
    assert error.value.code == "invalid_phone"


def test_phone_trims_outer_whitespace() -> None:
    assert normalize_phone(" 13800138000 ") == "13800138000"
