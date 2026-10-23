from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import BigInteger, case, cast, func

from app.housing.models import HousingBuilding

_ASCII_LETTERS_PATTERN = r"^[A-Z]+$"
_ASCII_DIGITS_PATTERN = r"^[0-9]+$"


def building_code_sort_key(
    code: str,
    *,
    display_order: int = 0,
    stable_id: str = "",
) -> tuple[int, str, int, int, str, str]:
    """Sort letter-only building codes before numeric codes using natural order."""

    normalized = unicodedata.normalize("NFKC", code).strip().upper()
    if normalized.isascii() and normalized.isalpha():
        return (0, normalized, 0, display_order, normalized, stable_id)
    if normalized.isascii() and normalized.isdigit():
        return (1, "", int(normalized), display_order, normalized, stable_id)
    return (2, "", 0, display_order, normalized, stable_id)


def building_order_expressions() -> tuple[Any, ...]:
    """Return the PostgreSQL equivalent of :func:`building_code_sort_key`."""

    normalized = func.upper(func.btrim(HousingBuilding.code))
    is_alpha = normalized.op("~")(_ASCII_LETTERS_PATTERN)
    is_numeric = normalized.op("~")(_ASCII_DIGITS_PATTERN)
    return (
        case((is_alpha, 0), (is_numeric, 1), else_=2),
        case((is_alpha, normalized), else_=""),
        case((is_numeric, cast(normalized, BigInteger)), else_=0),
        HousingBuilding.display_order,
        normalized,
        HousingBuilding.id,
    )
