from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import EmailStr, TypeAdapter, ValidationError

_PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
_IDENTITY_PATTERN = re.compile(r"^\d{17}[0-9X]$")
_IDENTITY_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_IDENTITY_CHECKS = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
_EMAIL_ADAPTER = TypeAdapter(EmailStr)


class IdentityValidationError(ValueError):
    def __init__(self, *, field: str, code: str, message: str) -> None:
        self.field = field
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class NormalizedCnId:
    number: str
    birth_date: date
    sex: Literal["male", "female"]


def normalize_phone(value: str) -> str:
    normalized = value.strip()
    if not _PHONE_PATTERN.fullmatch(normalized):
        raise IdentityValidationError(
            field="phone",
            code="invalid_phone",
            message="手机号必须是 11 位中国大陆手机号",
        )
    return normalized


def normalize_email(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    try:
        return str(_EMAIL_ADAPTER.validate_python(normalized))
    except ValidationError as exc:
        raise IdentityValidationError(
            field="email",
            code="invalid_email",
            message="邮箱格式不正确",
        ) from exc


def normalize_cn_id(
    value: str,
    *,
    expected_birth_date: date | None = None,
) -> NormalizedCnId:
    normalized = value.strip().upper()
    if not _IDENTITY_PATTERN.fullmatch(normalized):
        raise IdentityValidationError(
            field="identity_number",
            code="invalid_identity_number",
            message="身份证格式不正确",
        )

    birth_text = normalized[6:14]
    try:
        birth_date = date(
            int(birth_text[0:4]),
            int(birth_text[4:6]),
            int(birth_text[6:8]),
        )
    except ValueError as exc:
        raise IdentityValidationError(
            field="identity_number",
            code="invalid_identity_birth_date",
            message="身份证出生日期不正确",
        ) from exc

    checksum_index = (
        sum(int(digit) * weight for digit, weight in zip(normalized[:17], _IDENTITY_WEIGHTS)) % 11
    )
    if normalized[-1] != _IDENTITY_CHECKS[checksum_index]:
        raise IdentityValidationError(
            field="identity_number",
            code="invalid_identity_checksum",
            message="身份证校验码不正确",
        )

    if expected_birth_date is not None and birth_date != expected_birth_date:
        raise IdentityValidationError(
            field="date_of_birth",
            code="identity_birth_date_mismatch",
            message="出生日期与身份证不一致",
        )

    sex: Literal["male", "female"] = "male" if int(normalized[16]) % 2 else "female"
    return NormalizedCnId(number=normalized, birth_date=birth_date, sex=sex)
