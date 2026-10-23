from datetime import UTC, datetime, timedelta

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import SecretStr, ValidationError


def test_issued_secret_cannot_be_accidentally_encoded_as_public_response():
    from app.personnel_intake.activation_schemas import ActivationIssued, ActivationStart

    now = datetime(2026, 10, 18, tzinfo=UTC)
    issued = ActivationIssued(
        public=ActivationStart(
            accepted=True,
            resend_available_at=now + timedelta(seconds=60),
            expires_at=now + timedelta(minutes=10),
        ),
        session_token=SecretStr("secret-activation-cookie"),
    )
    assert "secret-activation-cookie" not in repr(issued)
    with pytest.raises((TypeError, ValueError)):
        jsonable_encoder(issued)
    assert set(jsonable_encoder(issued.public)) == {"accepted", "resend_available_at", "expires_at"}


@pytest.mark.parametrize("code", ["12345", "1234567", "12345a", "１２３４５６", " 123456"])
def test_activation_codes_are_exactly_six_ascii_digits(code):
    from app.personnel_intake.activation_schemas import ActivationVerifyInput

    with pytest.raises(ValidationError):
        ActivationVerifyInput(code=code)


def test_activation_request_secrets_are_hidden_and_extra_targets_rejected():
    from app.personnel_intake.activation_schemas import (
        ActivationCompleteInput,
        ActivationStartInput,
    )

    value = ActivationCompleteInput(code="123456", password="SecurePass1")
    assert "123456" not in repr(value) and "SecurePass1" not in repr(value)
    email = ActivationStartInput(email="  USER@example.com ")
    assert email.email.get_secret_value() == "user@example.com"
    assert "user@example.com" not in repr(email)
    with pytest.raises(ValidationError):
        ActivationCompleteInput(code="123456", password="SecurePass1", person_id="injected")
