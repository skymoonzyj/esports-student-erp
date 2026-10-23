from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.credential_service import VerifiedMachineRequest
from app.admissions.errors import PaymentImportConflict, PaymentSourceNotAllowed
from app.admissions.models import (
    Payment,
    PaymentConflict,
    PaymentImportEvent,
    ServiceAccount,
)
from app.admissions.payment_service import PaymentService
from app.admissions.schemas import PaymentImportRequest, PaymentImportResponse
from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.security.encryption import EnvelopeCipher, LookupDigest

LOOKUP_KEY = bytes(reversed(range(32)))


@dataclass(frozen=True)
class ImportScenario:
    service_account: ServiceAccount
    credential_id: UUID
    student: Person
    student_account: UserAccount
    guardian: Person
    guardian_account: UserAccount


def _add_person(
    db: Session,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    name: str,
    phone: str,
) -> Person:
    marker = uuid4().hex
    email = f"{marker}@example.test"
    person = Person(
        full_name=name,
        identity_ciphertext=f"identity-{marker}",
        identity_digest=f"identity-digest-{marker}",
        phone_ciphertext=cipher.encrypt(phone, field_name="people.phone"),
        phone_digest=lookup.digest(phone),
        email=None,
        email_ciphertext=cipher.encrypt(email, field_name="email"),
        email_digest=lookup.digest_domain("person-email", email),
        sex="female",
        date_of_birth=date(1990, 1, 1),
    )
    db.add(person)
    db.flush()
    return person


def _add_account(db: Session, *, person: Person, lookup: LookupDigest, phone: str) -> UserAccount:
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=lookup.digest(phone),
        email_normalized=person.email or f"{uuid4().hex}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(bytes(range(32)))


@pytest.fixture
def lookup() -> LookupDigest:
    return LookupDigest(LOOKUP_KEY)


@pytest.fixture
def import_scenario(
    db: Session,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> ImportScenario:
    admin = _add_person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="付款导入管理员",
        phone="13810000001",
    )
    admin_account = _add_account(db, person=admin, lookup=lookup, phone="13810000001")
    student = _add_person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="付款学员",
        phone="13810000002",
    )
    db.add(
        StudentProfile(
            person_id=student.id,
            student_number=f"PAY-{uuid4().hex[:12]}",
            status="active",
        )
    )
    student_account = _add_account(
        db,
        person=student,
        lookup=lookup,
        phone="13810000002",
    )
    second_student = _add_person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="监护人的第二位学员",
        phone="13810000003",
    )
    db.add(
        StudentProfile(
            person_id=second_student.id,
            student_number=f"PAY-{uuid4().hex[:12]}",
            status="active",
        )
    )
    guardian = _add_person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="付款监护人",
        phone="13810000004",
    )
    guardian_account = _add_account(
        db,
        person=guardian,
        lookup=lookup,
        phone="13810000004",
    )
    for student_id in (student.id, second_student.id):
        db.add(
            GuardianRelationship(
                guardian_person_id=guardian.id,
                student_person_id=student_id,
                relationship="mother",
                is_primary=True,
                valid_from=date(2026, 1, 1),
            )
        )
    service_account = ServiceAccount(
        name="外部付款平台",
        source_system=f"provider-{uuid4().hex}",
        is_active=True,
        created_by_user_id=admin_account.id,
    )
    db.add(service_account)
    db.commit()
    return ImportScenario(
        service_account=service_account,
        credential_id=uuid4(),
        student=student,
        student_account=student_account,
        guardian=guardian,
        guardian_account=guardian_account,
    )


@pytest.fixture
def payment_service(
    db: Session,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> PaymentService:
    return PaymentService(db, cipher=cipher, lookup=lookup)


def _input(
    scenario: ImportScenario,
    *,
    payer_phone: str = "13810000002",
    external_order_id: str = "ORDER-001",
    idempotency_key: str = "IDEMPOTENCY-001",
    amount: str = "12800.00",
    raw_payload: dict[str, object] | None = None,
) -> PaymentImportRequest:
    return PaymentImportRequest(
        source_system=scenario.service_account.source_system,
        external_order_id=external_order_id,
        idempotency_key=idempotency_key,
        payer_phone=payer_phone,
        amount=amount,
        currency="CNY",
        paid_at=datetime(2026, 8, 1, 9, 30, tzinfo=UTC),
        raw_payload=raw_payload or {"channel": "bank", "sequence": 1},
    )


def _body(value: PaymentImportRequest, *, indent: int | None = None) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=indent is None,
        separators=(",", ":") if indent is None else None,
        indent=indent,
    ).encode()


def _machine(scenario: ImportScenario, body: bytes) -> VerifiedMachineRequest:
    return VerifiedMachineRequest(
        service_account_id=scenario.service_account.id,
        credential_id=scenario.credential_id,
        raw_body=body,
    )


def test_identical_import_replay_returns_original_payment(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
    db: Session,
) -> None:
    input = _input(import_scenario)
    body = _body(input)

    first = payment_service.import_payment(input, machine=_machine(import_scenario, body))
    second = payment_service.import_payment(input, machine=_machine(import_scenario, body))

    assert first.created is True
    assert second.created is False
    assert second.payment.id == first.payment.id
    assert second.admission_case.id == first.admission_case.id
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert db.scalar(select(func.count()).select_from(PaymentImportEvent)) == 1


def test_json_whitespace_and_key_order_do_not_create_conflict(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
) -> None:
    input = _input(
        import_scenario,
        raw_payload={"z": 1, "nested": {"b": True, "a": None}},
    )

    first = payment_service.import_payment(
        input,
        machine=_machine(import_scenario, _body(input)),
    )
    second = payment_service.import_payment(
        input,
        machine=_machine(import_scenario, _body(input, indent=2)),
    )

    assert first.created is True
    assert second.created is False
    assert second.payment.id == first.payment.id


@pytest.mark.parametrize(
    ("changed_order", "changed_idempotency", "expected_type"),
    [
        (False, True, "external_order_payload_mismatch"),
        (True, False, "idempotency_payload_mismatch"),
    ],
)
def test_each_idempotency_dimension_preserves_changed_payload_as_conflict(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
    cipher: EnvelopeCipher,
    db: Session,
    changed_order: bool,
    changed_idempotency: bool,
    expected_type: str,
) -> None:
    original = _input(import_scenario)
    payment_service.import_payment(
        original,
        machine=_machine(import_scenario, _body(original)),
    )
    changed = _input(
        import_scenario,
        external_order_id="ORDER-CHANGED" if changed_order else original.external_order_id,
        idempotency_key=(
            "IDEMPOTENCY-CHANGED" if changed_idempotency else original.idempotency_key
        ),
        amount="12999.00",
    )
    changed_body = _body(changed)

    with pytest.raises(PaymentImportConflict) as error:
        payment_service.import_payment(
            changed,
            machine=_machine(import_scenario, changed_body),
        )

    assert error.value.code == "payment_import_conflict"
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    conflict = db.scalar(select(PaymentConflict))
    assert conflict is not None
    assert conflict.status == "open"
    assert conflict.conflict_type == expected_type
    assert changed.payer_phone not in conflict.attempted_raw_payload_ciphertext
    assert (
        cipher.decrypt(
            conflict.attempted_raw_payload_ciphertext,
            field_name="payment_conflicts.attempted_raw_payload",
        )
        == changed_body.decode()
    )


def test_unique_student_payer_creates_ready_case(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
) -> None:
    input = _input(import_scenario)
    result = payment_service.import_payment(
        input,
        machine=_machine(import_scenario, _body(input)),
    )

    assert result.payment.payer_account_id == import_scenario.student_account.id
    assert result.payment.payer_person_id == import_scenario.student.id
    assert result.admission_case.status == "ready"
    assert result.admission_case.student_person_id == import_scenario.student.id
    assert result.event.processing_status == "processed"


def test_guardian_with_multiple_students_stays_pending_without_conflict(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
    db: Session,
) -> None:
    input = _input(import_scenario, payer_phone="13810000004")
    result = payment_service.import_payment(
        input,
        machine=_machine(import_scenario, _body(input)),
    )

    assert result.payment.payer_account_id == import_scenario.guardian_account.id
    assert result.payment.payer_person_id == import_scenario.guardian.id
    assert result.admission_case.status == "pending_match"
    assert result.admission_case.student_person_id is None
    assert result.event.processing_status == "pending_match"
    assert db.scalar(select(func.count()).select_from(PaymentConflict)) == 0


def test_unknown_payer_stays_pending_without_creating_people_or_accounts(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
    db: Session,
) -> None:
    people_before = db.scalar(select(func.count()).select_from(Person))
    accounts_before = db.scalar(select(func.count()).select_from(UserAccount))
    input = _input(import_scenario, payer_phone="13819999999")

    result = payment_service.import_payment(
        input,
        machine=_machine(import_scenario, _body(input)),
    )

    assert result.payment.payer_account_id is None
    assert result.payment.payer_person_id is None
    assert result.admission_case.status == "pending_match"
    assert db.scalar(select(func.count()).select_from(Person)) == people_before
    assert db.scalar(select(func.count()).select_from(UserAccount)) == accounts_before


def test_payment_matching_does_not_fall_back_to_stale_legacy_login_digest(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    db: Session,
) -> None:
    stale_phone = "13810000002"
    current_phone = "13810000999"
    import_scenario.student.phone_ciphertext = cipher.encrypt(
        current_phone,
        field_name="people.phone",
    )
    import_scenario.student.phone_digest = lookup.digest(current_phone)
    db.flush()
    input = _input(import_scenario, payer_phone=stale_phone)

    result = payment_service.import_payment(
        input,
        machine=_machine(import_scenario, _body(input)),
    )

    assert result.payment.payer_account_id is None
    assert result.payment.payer_person_id is None
    assert result.admission_case.status == "pending_match"


def test_payload_is_encrypted_and_response_has_no_sensitive_fields(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
    cipher: EnvelopeCipher,
    db: Session,
) -> None:
    input = _input(import_scenario)
    body = _body(input)
    result = payment_service.import_payment(
        input,
        machine=_machine(import_scenario, body),
    )
    event = db.get(PaymentImportEvent, result.event.id)
    assert event is not None
    assert input.payer_phone not in event.raw_payload_ciphertext
    assert (
        cipher.decrypt(
            event.raw_payload_ciphertext,
            field_name="payment_import_events.raw_payload",
        )
        == body.decode()
    )

    response = PaymentImportResponse.from_result(result)
    dumped = response.model_dump()
    assert set(dumped) == {
        "import_event_id",
        "payment_id",
        "admission_case_id",
        "processing_status",
        "created",
    }
    assert input.payer_phone not in response.model_dump_json()


def test_source_system_must_match_machine_account(
    payment_service: PaymentService,
    import_scenario: ImportScenario,
    db: Session,
) -> None:
    input = _input(import_scenario).model_copy(update={"source_system": "impersonated-source"})

    with pytest.raises(PaymentSourceNotAllowed) as error:
        payment_service.import_payment(
            input,
            machine=_machine(import_scenario, _body(input)),
        )

    assert error.value.code == "payment_source_not_allowed"
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "0"},
        {"amount": "1.001"},
        {"currency": "USD"},
        {"paid_at": "2026-08-01T09:30:00"},
        {"payer_phone": "123"},
    ],
)
def test_import_schema_rejects_non_cny_or_invalid_business_values(
    import_scenario: ImportScenario,
    changes: dict[str, object],
) -> None:
    values = _input(import_scenario).model_dump()
    values.update(changes)

    with pytest.raises(ValidationError):
        PaymentImportRequest.model_validate(values)
