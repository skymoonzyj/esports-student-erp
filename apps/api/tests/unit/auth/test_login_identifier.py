from typing import cast

from sqlalchemy.orm import Session

from app.auth.schemas import LoginRequest
from app.auth.service import AuthService
from app.security.encryption import LookupDigest
from app.security.passwords import PasswordService


def test_login_request_accepts_new_identifier_and_legacy_phone_alias() -> None:
    new_request = LoginRequest.model_validate(
        {"identifier": "coach@example.com", "password": "Aa1-strong-26"}
    )
    legacy_request = LoginRequest.model_validate(
        {"phone": "13800138000", "password": "Aa1-strong-26"}
    )

    assert new_request.identifier == "coach@example.com"
    assert legacy_request.identifier == "13800138000"


def test_login_identifier_normalizes_phone_and_email_without_exposing_format() -> None:
    service = AuthService(
        cast(Session, None),
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
    )

    phone_kind, phone_value = service._identifier_lookup(" 13800138000 ")
    email_kind, email_value = service._identifier_lookup(" COACH@EXAMPLE.COM ")
    invalid_kind, invalid_value = service._identifier_lookup("not-an-account")

    assert phone_kind == "phone"
    assert phone_value == service.lookup.digest("13800138000")
    assert email_kind == "email"
    assert email_value == "coach@example.com"
    assert invalid_kind == "invalid"
    assert invalid_value != "not-an-account"
