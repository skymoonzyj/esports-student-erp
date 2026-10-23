"""Raw passport transport must survive before typed matching normalization."""

import json
from datetime import date
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.identity.document_identity import normalize_document_identity
from app.personnel_intake.codec import normalize_payload, normalize_pending_payload
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.schemas import IntakeInput, PendingIntakeInput, PendingProfileCorrection

TODAY = date(2026, 10, 21)


def pending_input(model, raw, **changes):
    fields = {
        "full_name": "  外国人员  ",
        "identity_number": raw,
        "document_type": "passport",
        "document_issuing_country": " br ",
        "phone": " +55 11 99999 0000 ",
        "email": " PERSON@Example.COM ",
        **changes,
    }
    if model is PendingIntakeInput:
        return model(kind="staff", **fields)
    return model(expected_version=1, reason="  资料核对  ", idempotency_key=uuid4(), **fields)


def normalize_input(value):
    if isinstance(value, PendingProfileCorrection):
        value = PendingIntakeInput(
            kind="staff",
            **value.model_dump(
                exclude={"expected_version", "reason", "idempotency_key", "identity_confirmed"}
            ),
        )
    return normalize_pending_payload(value, today=TODAY)


@pytest.mark.parametrize("model", [PendingIntakeInput, PendingProfileCorrection])
@pytest.mark.parametrize("secret", [False, True])
@pytest.mark.parametrize(
    "raw,matching", [(" zz-rst-0069 ", "ZZ-RST-0069"), (" Ab  0069 ", "AB  0069")]
)
def test_passport_create_and_correction_preserve_original_text(model, secret, raw, matching):
    # Wrapping neither ordinary strings nor SecretStr may erase source whitespace/case.
    value = pending_input(model, SecretStr(raw) if secret else raw)
    assert value.identity_number.get_secret_value() == raw
    payload = normalize_input(value)
    assert payload.identity_number == raw
    document = normalize_document_identity(
        payload.document_type,
        payload.identity_number,
        issuing_country=payload.document_issuing_country,
        sex=payload.sex,
        date_of_birth=payload.date_of_birth,
        allow_pending=True,
    )
    assert document.number == matching
    assert value.full_name == payload.full_name == "外国人员"
    assert value.document_issuing_country == "br"
    assert payload.document_issuing_country == "BR"
    assert payload.phone == "+55 11 99999 0000"
    assert payload.email == "person@example.com"
    if isinstance(value, PendingProfileCorrection):
        assert value.reason == "资料核对"


@pytest.mark.parametrize("model", [PendingIntakeInput, PendingProfileCorrection])
def test_passport_json_validation_preserves_original_text(model):
    raw = " zz-rst-0069 "
    fields = {"full_name": " 外国人员 ", "identity_number": raw, "document_type": "passport"}
    if model is PendingIntakeInput:
        fields["kind"] = "staff"
    else:
        fields.update(expected_version=1, reason=" 资料核对 ", idempotency_key=str(uuid4()))
    value = model.model_validate_json(json.dumps(fields))
    assert value.identity_number.get_secret_value() == raw
    assert normalize_input(value).identity_number == raw


@pytest.mark.parametrize("model", [PendingIntakeInput, PendingProfileCorrection])
@pytest.mark.parametrize("secret", [False, True])
def test_passport_transport_preserves_blank_contact_and_required_field_validation(model, secret):
    blank = SecretStr("   ") if secret else "   "
    value = pending_input(model, " original-0069 ", phone=blank, email=blank)
    assert value.phone is value.email is None
    assert normalize_input(value).phone is None
    with pytest.raises(ValidationError):
        pending_input(model, " original-0069 ", full_name="   ")
    with pytest.raises(PersonnelIntakeError):
        normalize_input(pending_input(model, blank))
    with pytest.raises(PersonnelIntakeError):
        normalize_input(pending_input(model, " original-0069 ", document_issuing_country="   "))
    if model is PendingProfileCorrection:
        with pytest.raises(ValidationError):
            value.model_validate({**value.model_dump(), "reason": "   "})


@pytest.mark.parametrize("secret", [False, True])
def test_pending_passport_exception_does_not_change_cn_or_legacy_normalization(secret):
    raw = " 11010519491231002X "
    fields = {
        "full_name": "  中国人员  ",
        "identity_number": SecretStr(raw) if secret else raw,
        "phone": "13900000001",
        "sex": "female",
        "date_of_birth": date(1949, 12, 31),
        "kind": "student",
    }
    pending_source = PendingIntakeInput(**fields)
    strict_source = IntakeInput(**fields)
    expected_source = raw if secret else "11010519491231002X"
    assert pending_source.identity_number.get_secret_value() == expected_source
    assert strict_source.identity_number.get_secret_value() == expected_source
    pending = normalize_pending_payload(pending_source, today=TODAY)
    strict = normalize_payload(strict_source, today=TODAY)
    assert pending.identity_number == strict.identity_number == "11010519491231002X"
    assert pending.full_name == strict.full_name == "中国人员"
    with pytest.raises(PersonnelIntakeError):
        normalize_pending_payload(
            PendingIntakeInput(**{**fields, "phone": "+55 11 99999 0000"}), today=TODAY
        )
