from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.identity.models import (
    GuardianProfile,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.rbac.models import Role, UserRole
from app.registration.consents import registration_terms_digest
from app.registration.finalization_service import (
    GuardianConfirmationRequired,
    RegistrationConflictPendingReview,
    RegistrationFinalizationService,
    RegistrationFinalizationUnavailable,
    clear_registration_application_secrets,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import (
    RegistrationApplication,
    RegistrationConsentAcceptance,
    RegistrationSession,
    StaffIdentityApplication,
)
from app.registration.verification_service import registration_credential_lock_keys
from app.revenue_share.models import ReferralCode, StudentReferralBinding
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
REQUEST_ID = "00000000-0000-4000-8000-000000000601"


def test_pending_reserved_email_cannot_bypass_completion_without_profile_metadata(db):
    from app.personnel_intake.models import AssistedPersonProfile
    from app.registration.matching_service import RegistrationIdentitySnapshot
    from tests.services.test_assisted_activation import activation
    from tests.services.test_profile_claim_binding import registration_application
    from tests.services.test_profile_claims import incomplete_pending_target

    target, _ = incomplete_pending_target(
        db,
        email="claim@example.com",
        phone="13900000981",
        sex="female",
        date_of_birth=date(2000, 1, 2),
    )
    application_id = registration_application(db, target.person_id, email="claim@example.com")
    db.delete(db.get(AssistedPersonProfile, target.person_id))
    db.commit()
    public, _ = activation(db)
    person = db.get(Person, target.person_id)
    application = db.get(RegistrationApplication, application_id)
    snapshot = RegistrationIdentitySnapshot(
        full_name=person.full_name,
        identity_number="PENDING-CLAIM-851",
        identity_digest=person.identity_digest,
        sex="female",
        date_of_birth=date(2000, 1, 2),
        identifier=normalize_registration_identifier("email", "claim@example.com"),
        primary_purpose="student",
        requested_staff_type=None,
        staff_application_reason=None,
        visit_reason=None,
    )
    service = RegistrationFinalizationService(db, cipher=public.cipher, lookup=public.lookup)
    with pytest.raises(RegistrationFinalizationUnavailable):
        service._new_account(application, person, snapshot, datetime.now(UTC))
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id)) is None
    )



def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _crypto() -> tuple[EnvelopeCipher, LookupDigest]:
    return EnvelopeCipher(bytes(range(32))), LookupDigest(bytes(reversed(range(32))))


def _birth_date(identity_number: str) -> date:
    return date.fromisoformat(
        f"{identity_number[6:10]}-{identity_number[10:12]}-{identity_number[12:14]}"
    )


def _application(
    db: Session,
    *,
    full_name: str,
    identity_number: str,
    phone: str,
    purpose: str = "student",
    state: str = "ready_to_finalize",
    requested_staff_type: str | None = None,
    staff_application_reason: str | None = None,
    visit_reason: str | None = None,
    referral_code_id: UUID | None = None,
) -> tuple[RegistrationApplication, str]:
    cipher, lookup = _crypto()
    identifier = normalize_registration_identifier("phone", phone)
    password_hash = PasswordService().hash("Strong601PassAa")
    application = RegistrationApplication(
        primary_purpose=purpose,
        state=state,
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(lookup, identifier),
        verified_identifier_ciphertext=cipher.encrypt(
            identifier.value,
            field_name="registration_identifier",
        ),
        password_hash=password_hash,
        full_name=full_name,
        identity_ciphertext=cipher.encrypt(
            identity_number,
            field_name="registration_identity",
        ),
        identity_digest=lookup.digest(identity_number),
        sex="male",
        date_of_birth=_birth_date(identity_number),
        requested_staff_type=requested_staff_type,
        staff_application_reason=staff_application_reason,
        visit_reason=visit_reason,
        referral_code_id=referral_code_id,
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    terms_digest = registration_terms_digest("2026-08")
    assert terms_digest is not None
    db.add_all(
        [
            RegistrationConsentAcceptance(
                registration_application_id=application.id,
                account_id=None,
                consent_type="registration_terms",
                policy_version="2026-08",
                content_digest=terms_digest,
                accepted_at=NOW,
                request_id=REQUEST_ID,
                source="self_registration",
            ),
            RegistrationSession(
                application_id=application.id,
                token_digest=f"{application.id.hex}token"[:64],
                csrf_digest=f"{application.id.hex}csrf"[:64],
                expires_at=NOW + timedelta(hours=8),
            ),
        ]
    )
    db.flush()
    db.commit()
    return application, password_hash


def _person(
    db: Session,
    *,
    full_name: str,
    identity_number: str,
    phone: str | None,
    email: str | None = None,
) -> Person:
    cipher, lookup = _crypto()
    service = IdentityService(db, cipher=cipher, lookup=lookup)
    summary = service.create_base_person(
        PersonBaseInput(
            full_name=full_name,
            identity_number=identity_number,
            sex="male",
            date_of_birth=_birth_date(identity_number),
            phone=phone,
            email=email,
        )
    )
    person = db.get(Person, summary.id)
    assert person is not None
    return person


def _account(
    db: Session,
    person: Person,
    identifier: str,
    *,
    kind: Literal["phone", "email"] = "phone",
    self_service_primary_purpose: str | None = None,
) -> UserAccount:
    _cipher, lookup = _crypto()
    account = UserAccount(
        person_id=person.id,
        password_hash=PasswordService().hash("Exist602PassAa"),
        status="active",
        force_password_change=False,
        failed_login_count=0,
        self_service_primary_purpose=self_service_primary_purpose,
    )
    db.add(account)
    db.flush()
    db.add(
        AccountLoginIdentifier(
            account_id=account.id,
            kind=kind,
            identifier_digest=login_identifier_digest(
                lookup,
                NormalizedLoginIdentifier(kind, identifier),
            ),
            verified_at=NOW,
            verification_source="test",
            is_primary=True,
        )
    )
    db.flush()
    return account


def _service(
    db: Session,
    *,
    now: datetime = NOW,
) -> RegistrationFinalizationService:
    cipher, lookup = _crypto()
    return RegistrationFinalizationService(
        db,
        cipher=cipher,
        lookup=lookup,
        clock=lambda: now,
    )


def _role_keys(db: Session, account_id) -> set[str]:
    return set(
        db.scalars(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == account_id)
        )
    )


def _formal_counts(db: Session) -> tuple[int, int, int, int]:
    return (
        db.scalar(select(func.count(Person.id))) or 0,
        db.scalar(select(func.count(UserAccount.id))) or 0,
        db.scalar(select(func.count(AccountLoginIdentifier.id))) or 0,
        db.scalar(select(func.count(StudentProfile.person_id))) or 0,
    )


def _bind_reviewed_candidate(
    db: Session,
    application: RegistrationApplication,
    candidate: Person,
) -> UserAccount:
    reviewer = _person(
        db,
        full_name="候选审核人员",
        identity_number=make_cn_id("19800102", 699),
        phone="13800006999",
    )
    reviewer_account = _account(db, reviewer, "13800006999")
    candidate_id = candidate.id
    reviewer_account_id = reviewer_account.id
    application.review_decision = "approved"
    application.reviewed_by_user_id = reviewer_account_id
    application.reviewed_candidate_person_id = candidate_id
    application.review_reason = "实名字段复核一致等待监护确认"
    application.reviewed_at = NOW
    db.commit()
    return reviewer_account


def _replace_person_identity(person: Person, identity_number: str) -> None:
    cipher, lookup = _crypto()
    person.identity_ciphertext = cipher.encrypt(
        identity_number,
        field_name="identity_number",
    )
    person.identity_digest = lookup.digest(identity_number)
    person.date_of_birth = _birth_date(identity_number)
    person.sex = "male"


def _assert_reviewed_candidate_conflict(
    db: Session,
    application_id: UUID,
    *,
    expected_code: str,
    before_counts: tuple[int, int, int, int],
) -> None:
    db.expire_all()
    stored = db.get(RegistrationApplication, application_id)
    assert stored is not None
    assert stored.state == "conflict"
    assert stored.conflict_code == expected_code
    assert stored.reviewed_candidate_person_id is None
    assert stored.finalized_account_id is None
    assert stored.finalized_at is None
    assert _formal_counts(db) == before_counts
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == application_id,
            AuditEvent.action == "registration.finalize",
        )
    ) == 0
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == application_id
        )
    )
    assert session is not None and session.revoked_at is None


def test_adult_student_finalization_is_atomic(db: Session) -> None:
    application, password_hash = _application(
        db,
        full_name="新注册学员",
        identity_number=make_cn_id("19900102", 601),
        phone="13800006001",
    )

    result = _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored_application = db.get(RegistrationApplication, application.id)
    account = db.get(UserAccount, result.account_id)
    person = db.get(Person, result.person_id)
    student = db.get(StudentProfile, result.person_id)
    assert stored_application is not None
    assert account is not None
    assert person is not None
    assert student is not None
    assert result.match_kind == "new_person"
    assert account.password_hash == password_hash
    assert account.force_password_change is False
    assert account.self_service_primary_purpose == "student"
    assert student.status == "draft"
    assert re.fullmatch(r"REG-2026\d{8}", student.student_number)
    assert _role_keys(db, account.id) == {"registered_user", "student"}
    assert stored_application.state == "finalized"
    assert stored_application.matched_person_id == person.id
    assert stored_application.finalized_account_id == account.id
    assert stored_application.password_hash is None
    assert stored_application.identity_ciphertext is None
    assert stored_application.verified_identifier_ciphertext is None
    assert db.scalar(select(func.count(AuthSession.id))) == 0
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == application.id
        )
    )
    assert session is not None and session.revoked_at == NOW
    consent = db.scalar(
        select(RegistrationConsentAcceptance).where(
            RegistrationConsentAcceptance.account_id == account.id
        )
    )
    assert consent is not None
    assert consent.registration_application_id is None
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "registration.finalize",
            AuditEvent.resource_id == application.id,
        )
    )
    assert audit is not None and audit.outcome == "allowed"


def test_student_finalization_snapshots_the_registered_inviter(db: Session) -> None:
    inviter = _person(
        db,
        full_name="邀请教练",
        identity_number=make_cn_id("19880102", 607),
        phone=None,
        email="inviter-602@example.com",
    )
    inviter_account = _account(
        db,
        inviter,
        "inviter-602@example.com",
        kind="email",
    )
    referral_code = ReferralCode(
        code="FINALIZE-INV-602",
        label="注册绑定测试",
        source_kind="person",
        referrer_person_id=inviter.id,
        referrer_organization_id=None,
        is_active=True,
        created_by_user_id=inviter_account.id,
    )
    db.add(referral_code)
    db.flush()
    application, _password_hash = _application(
        db,
        full_name="扫码注册学员",
        identity_number=make_cn_id("19910102", 609),
        phone="13800006024",
        referral_code_id=referral_code.id,
    )

    result = _service(db).finalize(application.id, request_id=REQUEST_ID)

    binding = db.scalar(
        select(StudentReferralBinding).where(
            StudentReferralBinding.student_person_id == result.person_id
        )
    )
    assert binding is not None
    assert binding.registration_application_id == application.id
    assert binding.referral_code_id == referral_code.id
    assert binding.referrer_person_id == inviter.id
    assert binding.source_kind == "person"


def test_exact_unowned_person_is_claimed_without_duplicate(db: Session) -> None:
    identity_number = make_cn_id("19900102", 603)
    person = _person(
        db,
        full_name="待认领学员",
        identity_number=identity_number,
        phone=None,
    )
    application, _password_hash = _application(
        db,
        full_name="待认领学员",
        identity_number=identity_number,
        phone="13800006003",
    )

    result = _service(db).finalize(application.id, request_id=REQUEST_ID)

    assert result.match_kind == "claim_existing"
    assert result.person_id == person.id
    assert db.scalar(
        select(func.count(Person.id)).where(
            Person.identity_digest == person.identity_digest
        )
    ) == 1
    db.refresh(person)
    assert person.phone_digest is not None


def test_existing_account_is_returned_without_duplicate(db: Session) -> None:
    identity_number = make_cn_id("19900102", 605)
    person = _person(
        db,
        full_name="已有正式账号",
        identity_number=identity_number,
        phone="13800006005",
    )
    account = _account(db, person, "13800006005")
    application, _password_hash = _application(
        db,
        full_name="已有正式账号",
        identity_number=identity_number,
        phone="13800006005",
    )

    result = _service(db).finalize(application.id, request_id=REQUEST_ID)

    assert result.match_kind == "existing_account"
    assert result.person_id == person.id
    assert result.account_id == account.id
    assert db.scalar(select(func.count(UserAccount.id))) == 1


def test_reviewed_candidate_binding_finalizes_same_person_and_is_consumed(
    db: Session,
) -> None:
    identity_number = make_cn_id("19900102", 641)
    candidate = _person(
        db,
        full_name="审核固定候选学员",
        identity_number=identity_number,
        phone=None,
    )
    application, _password_hash = _application(
        db,
        full_name="审核固定候选学员",
        identity_number=identity_number,
        phone="13800006041",
    )
    _bind_reviewed_candidate(db, application, candidate)

    result = _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert result.match_kind == "claim_existing"
    assert result.person_id == candidate.id
    assert stored.state == "finalized"
    assert stored.reviewed_candidate_person_id is None


def test_missing_reviewed_candidate_before_completion_becomes_pending_conflict(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_number = make_cn_id("19900102", 641)
    candidate = _person(
        db,
        full_name="完成前复核候选学员",
        identity_number=identity_number,
        phone=None,
    )
    application, _password_hash = _application(
        db,
        full_name="完成前复核候选学员",
        identity_number=identity_number,
        phone="13800006049",
    )
    _bind_reviewed_candidate(db, application, candidate)
    before_counts = _formal_counts(db)
    service = _service(db)
    monkeypatch.setattr(
        service.matching,
        "lock_candidate_people_in_transaction",
        lambda _person_ids: [],
    )

    with pytest.raises(RegistrationConflictPendingReview):
        service.finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "conflict"
    assert stored.conflict_code == "candidate_person_unavailable"
    assert stored.reviewed_candidate_person_id is None
    assert stored.finalized_account_id is None
    assert _formal_counts(db) == before_counts
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == application.id,
            AuditEvent.action == "registration.finalize",
        )
    ) == 0


def test_candidate_lock_set_includes_reviewed_candidate_after_match_drift(
    db: Session,
) -> None:
    identity_number = make_cn_id("19900102", 651)
    candidate = _person(
        db,
        full_name="候选预锁定学员",
        identity_number=identity_number,
        phone=None,
    )
    application, _password_hash = _application(
        db,
        full_name="候选预锁定学员",
        identity_number=identity_number,
        phone="13800006051",
    )
    _bind_reviewed_candidate(db, application, candidate)
    _replace_person_identity(candidate, make_cn_id("19900102", 653))
    db.commit()

    candidate_ids = _service(db).lock_candidate_person_ids_in_transaction(
        application
    )

    assert candidate.id in candidate_ids


def test_reviewed_candidate_binding_blocks_new_person_fallback(
    db: Session,
) -> None:
    identity_number = make_cn_id("19900102", 641)
    candidate = _person(
        db,
        full_name="审核后实名漂移学员",
        identity_number=identity_number,
        phone=None,
    )
    application, _password_hash = _application(
        db,
        full_name="审核后实名漂移学员",
        identity_number=identity_number,
        phone="13800006043",
    )
    _bind_reviewed_candidate(db, application, candidate)
    _replace_person_identity(candidate, make_cn_id("19900102", 643))
    db.commit()
    before_counts = _formal_counts(db)

    with pytest.raises(RegistrationConflictPendingReview):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    _assert_reviewed_candidate_conflict(
        db,
        application.id,
        expected_code="reviewed_candidate_changed",
        before_counts=before_counts,
    )


def test_reviewed_candidate_binding_blocks_replacement_person(
    db: Session,
) -> None:
    identity_number = make_cn_id("19900102", 641)
    reviewed_candidate = _person(
        db,
        full_name="审核后候选替换学员",
        identity_number=identity_number,
        phone=None,
    )
    application, _password_hash = _application(
        db,
        full_name="审核后候选替换学员",
        identity_number=identity_number,
        phone="13800006045",
    )
    _bind_reviewed_candidate(db, application, reviewed_candidate)
    _replace_person_identity(
        reviewed_candidate,
        make_cn_id("19900102", 645),
    )
    replacement = _person(
        db,
        full_name="审核后候选替换学员",
        identity_number=identity_number,
        phone=None,
    )
    db.commit()
    before_counts = _formal_counts(db)

    with pytest.raises(RegistrationConflictPendingReview):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    _assert_reviewed_candidate_conflict(
        db,
        application.id,
        expected_code="reviewed_candidate_changed",
        before_counts=before_counts,
    )
    assert db.scalar(
        select(func.count(UserAccount.id)).where(
            UserAccount.person_id == replacement.id
        )
    ) == 0


def test_reviewed_candidate_binding_blocks_disabled_existing_account(
    db: Session,
) -> None:
    identity_number = make_cn_id("19900102", 647)
    candidate = _person(
        db,
        full_name="审核后账号停用学员",
        identity_number=identity_number,
        phone="13800006047",
    )
    candidate_account = _account(db, candidate, "13800006047")
    application, _password_hash = _application(
        db,
        full_name="审核后账号停用学员",
        identity_number=identity_number,
        phone="13800006047",
    )
    _bind_reviewed_candidate(db, application, candidate)
    candidate_account.status = "disabled"
    db.commit()
    before_counts = _formal_counts(db)

    with pytest.raises(RegistrationConflictPendingReview):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    _assert_reviewed_candidate_conflict(
        db,
        application.id,
        expected_code="reviewed_candidate_unavailable",
        before_counts=before_counts,
    )


def test_existing_self_service_account_finalization_changes_only_registration_rows(
    db: Session,
) -> None:
    identity_number = make_cn_id("19900102", 625)
    person = _person(
        db,
        full_name="已有自助账号",
        identity_number=identity_number,
        phone=None,
        email="existing625@example.com",
    )
    account = _account(
        db,
        person,
        "existing625@example.com",
        kind="email",
        self_service_primary_purpose="student",
    )
    account_password_hash = account.password_hash
    identifier_ids_before = set(
        db.scalars(
            select(AccountLoginIdentifier.id).where(
                AccountLoginIdentifier.account_id == account.id
            )
        )
    )
    application, _password_hash = _application(
        db,
        full_name="已有自助账号",
        identity_number=identity_number,
        phone="13800006025",
    )

    first = _service(db).finalize(application.id, request_id=REQUEST_ID)
    second = _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored_person = db.get(Person, person.id)
    stored_account = db.get(UserAccount, account.id)
    stored_application = db.get(RegistrationApplication, application.id)
    consent = db.scalar(
        select(RegistrationConsentAcceptance).where(
            RegistrationConsentAcceptance.registration_application_id
            == application.id
        )
    )
    assert stored_person is not None
    assert stored_account is not None
    assert stored_application is not None
    assert first.match_kind == second.match_kind == "existing_account"
    assert stored_person.phone_ciphertext is None
    assert stored_person.phone_digest is None
    assert stored_account.password_hash == account_password_hash
    assert stored_account.self_service_primary_purpose == "student"
    assert _role_keys(db, account.id) == set()
    assert db.get(StudentProfile, person.id) is None
    assert set(
        db.scalars(
            select(AccountLoginIdentifier.id).where(
                AccountLoginIdentifier.account_id == account.id
            )
        )
    ) == identifier_ids_before
    assert consent is not None and consent.account_id is None
    assert stored_application.password_hash is None
    assert stored_application.state == "finalized"


def test_cross_person_identifier_match_enters_conflict(db: Session) -> None:
    identity_number = make_cn_id("19900102", 607)
    _person(
        db,
        full_name="证件本人",
        identity_number=identity_number,
        phone="13800006007",
    )
    _person(
        db,
        full_name="手机号本人",
        identity_number=make_cn_id("19900102", 609),
        phone="13800006009",
    )
    application, _password_hash = _application(
        db,
        full_name="证件本人",
        identity_number=identity_number,
        phone="13800006009",
    )

    with pytest.raises(RegistrationConflictPendingReview):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "conflict"
    assert stored.conflict_code == "split_identifier_match"
    assert db.scalar(select(func.count(UserAccount.id))) == 0
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "registration.finalize"
        )
    ) == 0


def test_finalization_is_idempotent(db: Session) -> None:
    application, _password_hash = _application(
        db,
        full_name="幂等注册学员",
        identity_number=make_cn_id("19900102", 611),
        phone="13800006011",
    )

    first = _service(db).finalize(application.id, request_id=REQUEST_ID)
    second = _service(db).finalize(application.id, request_id=REQUEST_ID)

    assert first.match_kind == second.match_kind == "new_person"
    assert second.person_id == first.person_id
    assert second.account_id == first.account_id
    assert db.scalar(select(func.count(UserAccount.id))) == 1
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "registration.finalize"
        )
    ) == 1


def test_finalized_application_remains_idempotent_after_original_expiry(
    db: Session,
) -> None:
    application, _password_hash = _application(
        db,
        full_name="过期后幂等注册学员",
        identity_number=make_cn_id("19900102", 627),
        phone="13800006027",
    )

    first = _service(db).finalize(application.id, request_id=REQUEST_ID)
    second = _service(db, now=NOW + timedelta(days=31)).finalize(
        application.id,
        request_id=REQUEST_ID,
    )

    assert second == first
    assert db.scalar(select(func.count(UserAccount.id))) == 1
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "registration.finalize"
        )
    ) == 1


def test_minor_student_moves_to_pending_guardian_without_formal_rows(
    db: Session,
) -> None:
    application, password_hash = _application(
        db,
        full_name="未成年学员",
        identity_number=make_cn_id("20120102", 613),
        phone="13800006013",
        state="identity_completed",
    )

    with pytest.raises(GuardianConfirmationRequired):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "pending_guardian"
    assert stored.password_hash == password_hash
    assert db.scalar(select(func.count(UserAccount.id))) == 0
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == application.id
        )
    )
    assert session is not None and session.revoked_at is None


@pytest.mark.parametrize(
    ("state", "sequence", "phone"),
    [
        ("identity_completed", 621, "13800006021"),
        ("pending_guardian", 623, "13800006023"),
    ],
)
def test_student_turning_eighteen_can_finalize_with_claim_locks_before_row_lock(
    db: Session,
    state: str,
    sequence: int,
    phone: str,
) -> None:
    application, _password_hash = _application(
        db,
        full_name=f"跨生日学员{sequence}",
        identity_number=make_cn_id("20080816", sequence),
        phone=phone,
        state=state,
    )
    identifier_digest = application.verified_identifier_digest
    identity_digest = application.identity_digest
    assert identifier_digest is not None
    assert identity_digest is not None
    expected_lock_keys = set(
        registration_credential_lock_keys(
            {identifier_digest},
            identity_digests={identity_digest},
        )
    )
    statements: list[tuple[str, tuple[object, ...]]] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if isinstance(parameters, dict):
            values = tuple(parameters.values())
        elif isinstance(parameters, (list, tuple)):
            values = tuple(parameters)
        else:
            values = (parameters,)
        statements.append((statement, values))

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        result = _service(db, now=NOW + timedelta(days=1)).finalize(
            application.id,
            request_id=REQUEST_ID,
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    application_row_lock = next(
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "registration_applications" in statement.casefold()
        and "for update" in statement.casefold()
    )
    acquired_claim_keys = {
        value
        for index, (statement, parameters) in enumerate(statements)
        if index < application_row_lock
        and "pg_advisory_xact_lock" in statement.casefold()
        for value in parameters
        if value in expected_lock_keys
    }
    assert acquired_claim_keys == expected_lock_keys
    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None and stored.state == "finalized"
    assert result.match_kind == "new_person"
    assert db.get(StudentProfile, result.person_id) is not None


@pytest.mark.parametrize(
    ("state", "sequence", "phone"),
    [
        ("identity_completed", 629, "13800006029"),
        ("pending_guardian", 631, "13800006031"),
    ],
)
def test_turning_eighteen_concurrent_claim_persists_reviewable_conflict(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    sequence: int,
    phone: str,
) -> None:
    application, password_hash = _application(
        db,
        full_name=f"跨生日并发冲突学员{sequence}",
        identity_number=make_cn_id("20080816", sequence),
        phone=phone,
        state=state,
    )
    before_counts = _formal_counts(db)
    identifier_digest = application.verified_identifier_digest
    identity_digest = application.identity_digest
    assert identifier_digest is not None
    assert identity_digest is not None
    expected_lock_keys = set(
        registration_credential_lock_keys(
            {identifier_digest},
            identity_digests={identity_digest},
        )
    )
    statements: list[tuple[str, tuple[object, ...]]] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if isinstance(parameters, dict):
            values = tuple(parameters.values())
        elif isinstance(parameters, (list, tuple)):
            values = tuple(parameters)
        else:
            values = (parameters,)
        statements.append((statement, values))

    class ConstraintViolation(Exception):
        class diag:
            constraint_name = "uq_account_login_identifiers_identifier_digest"

    def fail_with_concurrent_constraint(
        _service: RegistrationFinalizationService,
        *_args: object,
        **_kwargs: object,
    ) -> UserAccount:
        raise IntegrityError("concurrent claim", {}, ConstraintViolation())

    monkeypatch.setattr(
        RegistrationFinalizationService,
        "_new_account",
        fail_with_concurrent_constraint,
    )

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        with pytest.raises(RegistrationConflictPendingReview):
            _service(db, now=NOW + timedelta(days=1)).finalize(
                application.id,
                request_id=REQUEST_ID,
            )
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "conflict"
    assert stored.conflict_code == "concurrent_claim"
    assert stored.password_hash == password_hash
    assert _formal_counts(db) == before_counts
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == application.id
        )
    ) == 0
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == application.id
        )
    )
    assert session is not None and session.revoked_at is None
    application_row_locks = [
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "registration_applications" in statement.casefold()
        and "for update" in statement.casefold()
    ]
    assert len(application_row_locks) == 2
    marker_lock_keys = {
        value
        for index, (statement, parameters) in enumerate(statements)
        if application_row_locks[0] < index < application_row_locks[1]
        and "pg_advisory_xact_lock" in statement.casefold()
        for value in parameters
        if value in expected_lock_keys
    }
    assert marker_lock_keys == expected_lock_keys


def test_finalizer_observes_committed_conflict_as_pending_review(
    db: Session,
) -> None:
    application, _password_hash = _application(
        db,
        full_name="并发已转冲突学员",
        identity_number=make_cn_id("19900102", 633),
        phone="13800006033",
    )
    injected = False
    bind = db.get_bind()

    def commit_conflict_before_application_row_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal injected
        normalized = " ".join(statement.casefold().split())
        if (
            injected
            or not normalized.startswith("select")
            or "from registration_applications" not in normalized
            or "for update" not in normalized
        ):
            return
        injected = True
        with Session(bind) as concurrent, concurrent.begin():
            result = concurrent.execute(
                update(RegistrationApplication)
                .where(RegistrationApplication.id == application.id)
                .values(state="conflict", conflict_code="split_identifier_match")
            )
            assert result.rowcount == 1

    event.listen(
        bind,
        "before_cursor_execute",
        commit_conflict_before_application_row_lock,
    )
    try:
        with pytest.raises(RegistrationConflictPendingReview):
            _service(db).finalize(application.id, request_id=REQUEST_ID)
    finally:
        event.remove(
            bind,
            "before_cursor_execute",
            commit_conflict_before_application_row_lock,
        )

    assert injected is True
    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "conflict"
    assert stored.conflict_code == "split_identifier_match"
    assert db.scalar(select(1)) == 1


@pytest.mark.parametrize(
    ("changed_field", "sequence", "phone"),
    [
        ("verified_identifier_digest", 645, "13800006045"),
        ("expires_at", 647, "13800006047"),
    ],
)
def test_finalizer_rejects_committed_conflict_with_changed_claim_snapshot(
    postgres_engine: Engine,
    changed_field: Literal["verified_identifier_digest", "expires_at"],
    sequence: int,
    phone: str,
) -> None:
    with Session(postgres_engine) as setup:
        application, password_hash = _application(
            setup,
            full_name=f"并发篡改冲突学员{sequence}",
            identity_number=make_cn_id("19900102", sequence),
            phone=phone,
        )
        application_id = application.id
        before_counts = _formal_counts(setup)
        original_identifier_digest = application.verified_identifier_digest
        original_expires_at = application.expires_at
        assert original_identifier_digest is not None
        if changed_field == "verified_identifier_digest":
            _cipher, lookup = _crypto()
            replacement = normalize_registration_identifier("phone", "13800006049")
            changed_value: str | datetime = registration_identifier_digest(
                lookup,
                replacement,
            )
        else:
            changed_value = NOW + timedelta(days=31)
    request_id = f"finalization-stale-conflict-{changed_field}"
    injected = False

    def commit_changed_conflict_before_application_row_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal injected
        normalized = " ".join(statement.casefold().split())
        if (
            injected
            or not normalized.startswith("select")
            or "from registration_applications" not in normalized
            or "for update" not in normalized
        ):
            return
        injected = True
        with Session(postgres_engine) as concurrent, concurrent.begin():
            result = concurrent.execute(
                update(RegistrationApplication)
                .where(RegistrationApplication.id == application_id)
                .values(
                    state="conflict",
                    conflict_code="split_identifier_match",
                    **{changed_field: changed_value},
                )
            )
            assert result.rowcount == 1

    try:
        with Session(postgres_engine) as db:
            connection = db.connection()
            event.listen(
                connection,
                "before_cursor_execute",
                commit_changed_conflict_before_application_row_lock,
            )
            try:
                with pytest.raises(RegistrationFinalizationUnavailable):
                    _service(db).finalize(application_id, request_id=request_id)
            finally:
                event.remove(
                    connection,
                    "before_cursor_execute",
                    commit_changed_conflict_before_application_row_lock,
                )
            assert db.scalar(select(1)) == 1

        assert injected is True
        with Session(postgres_engine) as verification:
            stored = verification.get(RegistrationApplication, application_id)
            assert stored is not None
            assert stored.state == "conflict"
            assert stored.conflict_code == "split_identifier_match"
            assert getattr(stored, changed_field) == changed_value
            if changed_field == "verified_identifier_digest":
                assert stored.expires_at == original_expires_at
            else:
                assert stored.verified_identifier_digest == original_identifier_digest
            assert stored.password_hash == password_hash
            assert _formal_counts(verification) == before_counts
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "registration.finalize",
                    AuditEvent.request_id == request_id,
                )
            ) == 0
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(RegistrationConsentAcceptance).where(
                    RegistrationConsentAcceptance.registration_application_id
                    == application_id
                )
            )
            cleanup.execute(
                delete(RegistrationSession).where(
                    RegistrationSession.application_id == application_id
                )
            )
            cleanup.execute(
                delete(RegistrationApplication).where(
                    RegistrationApplication.id == application_id
                )
            )


def test_controlled_conflict_does_not_report_review_after_concurrent_expiry(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _password_hash = _application(
        db,
        full_name="并发重置过期学员",
        identity_number=make_cn_id("19900102", 637),
        phone="13800006037",
    )
    before_counts = _formal_counts(db)

    class ConstraintViolation(Exception):
        class diag:
            constraint_name = "uq_account_login_identifiers_identifier_digest"

    def fail_with_concurrent_constraint(
        _service: RegistrationFinalizationService,
        *_args: object,
        **_kwargs: object,
    ) -> UserAccount:
        raise IntegrityError("concurrent claim", {}, ConstraintViolation())

    original_marker = RegistrationFinalizationService._mark_concurrent_conflict

    def expire_before_conflict_marker(
        service: RegistrationFinalizationService,
        application_id: UUID,
    ) -> object:
        with Session(db.get_bind()) as concurrent, concurrent.begin():
            stored = concurrent.get(RegistrationApplication, application_id)
            assert stored is not None
            stored.state = "expired"
            clear_registration_application_secrets(stored)
        return original_marker(service, application_id)

    monkeypatch.setattr(
        RegistrationFinalizationService,
        "_new_account",
        fail_with_concurrent_constraint,
    )
    monkeypatch.setattr(
        RegistrationFinalizationService,
        "_mark_concurrent_conflict",
        expire_before_conflict_marker,
    )

    with pytest.raises(RegistrationFinalizationUnavailable):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "expired"
    assert stored.conflict_code is None
    assert _formal_counts(db) == before_counts
    assert db.scalar(select(1)) == 1


def test_concurrent_conflict_marker_locks_full_claim_before_application_row(
    db: Session,
) -> None:
    application, _password_hash = _application(
        db,
        full_name="并发冲突标记锁顺序学员",
        identity_number=make_cn_id("19900102", 639),
        phone="13800006039",
        state="conflict",
    )
    application.conflict_code = "split_identifier_match"
    db.commit()
    identifier_digest = application.verified_identifier_digest
    identity_digest = application.identity_digest
    assert identifier_digest is not None
    assert identity_digest is not None
    expected_lock_keys = registration_credential_lock_keys(
        {identifier_digest},
        identity_digests={identity_digest},
    )
    statements: list[tuple[str, tuple[object, ...]]] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if isinstance(parameters, dict):
            values = tuple(parameters.values())
        elif isinstance(parameters, (list, tuple)):
            values = tuple(parameters)
        else:
            values = (parameters,)
        statements.append((statement, values))

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        marked = _service(db)._mark_concurrent_conflict(application.id)
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    application_row_locks = [
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "registration_applications" in statement.casefold()
        and "for update" in statement.casefold()
    ]
    assert marked is True
    assert len(application_row_locks) == 1
    application_row_lock = application_row_locks[0]
    acquired_lock_keys = [
        value
        for index, (statement, parameters) in enumerate(statements)
        if index < application_row_lock
        and "pg_advisory_xact_lock" in statement.casefold()
        for value in parameters
        if value in expected_lock_keys
    ]
    assert acquired_lock_keys == expected_lock_keys
    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "conflict"
    assert stored.conflict_code == "split_identifier_match"


def test_concurrent_conflict_marker_rejects_expiry_before_application_row_lock(
    db: Session,
) -> None:
    application, _password_hash = _application(
        db,
        full_name="并发冲突标记重置过期学员",
        identity_number=make_cn_id("19900102", 641),
        phone="13800006041",
        state="conflict",
    )
    application.conflict_code = "split_identifier_match"
    db.commit()
    injected = False
    bind = db.get_bind()

    def expire_before_application_row_lock(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal injected
        normalized = " ".join(statement.casefold().split())
        if (
            injected
            or not normalized.startswith("select")
            or "from registration_applications" not in normalized
            or "for update" not in normalized
        ):
            return
        injected = True
        result = connection.execute(
            update(RegistrationApplication)
            .where(RegistrationApplication.id == application.id)
            .values(
                state="expired",
                verified_identifier_kind=None,
                verified_identifier_digest=None,
                verified_identifier_ciphertext=None,
                password_hash=None,
                full_name=None,
                identity_ciphertext=None,
                identity_digest=None,
                sex=None,
                date_of_birth=None,
                requested_staff_type=None,
                staff_application_reason=None,
                visit_reason=None,
            )
        )
        assert result.rowcount == 1

    event.listen(bind, "before_cursor_execute", expire_before_application_row_lock)
    try:
        marked = _service(db)._mark_concurrent_conflict(application.id)
    finally:
        event.remove(
            bind,
            "before_cursor_execute",
            expire_before_application_row_lock,
        )

    assert injected is True
    assert marked is False
    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "expired"
    assert stored.conflict_code == "split_identifier_match"
    assert stored.password_hash is None
    assert stored.verified_identifier_digest is None
    assert stored.identity_digest is None


def test_concurrent_conflict_marker_rejects_conflict_expired_at_now(
    db: Session,
) -> None:
    application, _password_hash = _application(
        db,
        full_name="并发冲突标记边界过期学员",
        identity_number=make_cn_id("19900102", 643),
        phone="13800006043",
        state="conflict",
    )
    application.conflict_code = "split_identifier_match"
    application.expires_at = NOW
    db.commit()

    assert _service(db)._mark_concurrent_conflict(application.id) is False

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "conflict"
    assert stored.conflict_code == "split_identifier_match"


@pytest.mark.parametrize(
    ("purpose", "expected_roles"),
    [
        ("guardian", {"registered_user", "guardian"}),
        ("visitor", {"registered_user"}),
        ("staff_applicant", {"registered_user"}),
    ],
)
def test_non_student_purpose_creates_only_expected_profile_and_roles(
    db: Session,
    purpose: str,
    expected_roles: set[str],
) -> None:
    sequence = {"guardian": 615, "visitor": 617, "staff_applicant": 619}[purpose]
    application, _password_hash = _application(
        db,
        full_name=f"{purpose}注册人",
        identity_number=make_cn_id("19900102", sequence),
        phone=f"1380000{sequence:04d}",
        purpose=purpose,
        requested_staff_type="coach" if purpose == "staff_applicant" else None,
        staff_application_reason="申请成为教练" if purpose == "staff_applicant" else None,
        visit_reason="探望营期学员" if purpose == "visitor" else None,
    )

    result = _service(db).finalize(application.id, request_id=REQUEST_ID)

    assert _role_keys(db, result.account_id) == expected_roles
    if purpose == "guardian":
        assert db.get(GuardianProfile, result.person_id) is not None
    elif purpose == "visitor":
        visitor = db.get(VisitorProfile, result.person_id)
        assert visitor is not None and visitor.visit_reason == "探望营期学员"
    else:
        assert db.get(StaffProfile, result.person_id) is None
        staff_application = db.scalar(
            select(StaffIdentityApplication).where(
                StaffIdentityApplication.person_id == result.person_id
            )
        )
        assert staff_application is not None
        assert staff_application.status == "pending"


def test_audit_failure_rolls_back_every_formal_write(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, password_hash = _application(
        db,
        full_name="回滚注册学员",
        identity_number=make_cn_id("19900102", 621),
        phone="13800006021",
    )

    def fail_audit(_service: AuditService, **_values: object):
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr(AuditService, "append", fail_audit)

    with pytest.raises(RuntimeError, match="audit persistence failed"):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "ready_to_finalize"
    assert stored.password_hash == password_hash
    assert db.scalar(select(func.count(Person.id))) == 0
    assert db.scalar(select(func.count(UserAccount.id))) == 0
    assert db.scalar(select(func.count(StudentProfile.person_id))) == 0
    consent = db.scalar(
        select(RegistrationConsentAcceptance).where(
            RegistrationConsentAcceptance.registration_application_id
            == application.id
        )
    )
    assert consent is not None and consent.account_id is None
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == application.id
        )
    )
    assert session is not None and session.revoked_at is None


def test_malformed_argon2_hash_rolls_back_every_formal_write(db: Session) -> None:
    application, _password_hash = _application(
        db,
        full_name="损坏密码摘要注册人",
        identity_number=make_cn_id("19900102", 623),
        phone="13800006023",
    )
    application.password_hash = "$argon2id$broken"
    db.commit()
    before_counts = (
        db.scalar(select(func.count(Person.id))),
        db.scalar(select(func.count(UserAccount.id))),
        db.scalar(select(func.count(AccountLoginIdentifier.id))),
        db.scalar(select(func.count(StudentProfile.person_id))),
    )

    with pytest.raises(RegistrationFinalizationUnavailable):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    consent = db.scalar(
        select(RegistrationConsentAcceptance).where(
            RegistrationConsentAcceptance.registration_application_id
            == application.id
        )
    )
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == application.id
        )
    )
    assert stored is not None
    assert stored.state == "ready_to_finalize"
    assert stored.password_hash == "$argon2id$broken"
    assert (
        db.scalar(select(func.count(Person.id))),
        db.scalar(select(func.count(UserAccount.id))),
        db.scalar(select(func.count(AccountLoginIdentifier.id))),
        db.scalar(select(func.count(StudentProfile.person_id))),
    ) == before_counts
    assert consent is not None and consent.account_id is None
    assert session is not None and session.revoked_at is None
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == application.id
        )
    ) == 0


def test_missing_consent_rolls_back_every_formal_write(db: Session) -> None:
    application, password_hash = _application(
        db,
        full_name="缺少协议注册人",
        identity_number=make_cn_id("19900102", 629),
        phone="13800006029",
    )
    db.execute(
        delete(RegistrationConsentAcceptance).where(
            RegistrationConsentAcceptance.registration_application_id
            == application.id
        )
    )
    db.commit()
    before_counts = _formal_counts(db)

    with pytest.raises(RegistrationFinalizationUnavailable):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "ready_to_finalize"
    assert stored.password_hash == password_hash
    assert _formal_counts(db) == before_counts
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == application.id
        )
    ) == 0


@pytest.mark.parametrize(
    ("state", "expires_at", "sequence"),
    [
        ("rejected", NOW + timedelta(days=30), 631),
        ("ready_to_finalize", NOW, 633),
    ],
)
def test_rejected_or_expired_application_creates_no_formal_rows(
    db: Session,
    state: str,
    expires_at: datetime,
    sequence: int,
) -> None:
    application, password_hash = _application(
        db,
        full_name="不可开通注册人",
        identity_number=make_cn_id("19900102", sequence),
        phone=f"1380000{sequence:04d}",
        state=state,
    )
    application.expires_at = expires_at
    db.commit()
    before_counts = _formal_counts(db)

    with pytest.raises(RegistrationFinalizationUnavailable):
        _service(db).finalize(application.id, request_id=REQUEST_ID)

    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == state
    assert stored.password_hash == password_hash
    assert _formal_counts(db) == before_counts
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == application.id
        )
    ) == 0


def test_finalization_rejects_identifier_change_before_application_row_lock(
    db: Session,
) -> None:
    application, password_hash = _application(
        db,
        full_name="锁前快照变化注册人",
        identity_number=make_cn_id("19900102", 635),
        phone="13800006035",
    )
    original_digest = application.verified_identifier_digest
    assert original_digest is not None
    cipher, lookup = _crypto()
    replacement = normalize_registration_identifier("phone", "13800006036")
    replacement_digest = registration_identifier_digest(lookup, replacement)
    replacement_ciphertext = cipher.encrypt(
        replacement.value,
        field_name="registration_identifier",
    )
    account_count_before = db.scalar(select(func.count(UserAccount.id)))
    injected = False
    bind = db.get_bind()

    def replace_identifier_before_row_lock(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal injected
        normalized = " ".join(statement.lower().split())
        if (
            injected
            or not normalized.startswith("select")
            or "from registration_applications" not in normalized
            or " for update" not in normalized
        ):
            return
        injected = True
        connection.execute(
            update(RegistrationApplication)
            .where(RegistrationApplication.id == application.id)
            .values(
                verified_identifier_digest=replacement_digest,
                verified_identifier_ciphertext=replacement_ciphertext,
            )
        )

    event.listen(bind, "before_cursor_execute", replace_identifier_before_row_lock)
    try:
        with pytest.raises(RegistrationFinalizationUnavailable):
            _service(db).finalize(
                application.id,
                request_id="finalization-stale-snapshot",
            )
    finally:
        event.remove(bind, "before_cursor_execute", replace_identifier_before_row_lock)

    assert injected is True
    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "ready_to_finalize"
    assert stored.password_hash == password_hash
    assert stored.verified_identifier_digest == original_digest
    assert db.scalar(select(func.count(UserAccount.id))) == account_count_before
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.request_id == "finalization-stale-snapshot"
        )
    ) == 0
