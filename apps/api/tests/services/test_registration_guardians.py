from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.identity.schemas import GuardianCreate, PersonBaseInput, StudentCreate
from app.identity.service import IdentityService
from app.rbac.models import Role, UserRole
from app.registration.consents import registration_terms_digest
from app.registration.delivery import CapturingVerificationSender
from app.registration.finalization_service import RegistrationFinalizationService
from app.registration.guardian_service import (
    GuardianInvitationUnavailable,
    GuardianRegistrationService,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.invitation_retention import (
    REDACTED_GUARDIAN_INVITATION_CIPHERTEXT,
    REDACTED_GUARDIAN_INVITATION_DIGEST,
    REDACTED_GUARDIAN_INVITATION_MASK,
)
from app.registration.models import (
    GuardianRegistrationInvitation,
    RegistrationApplication,
    RegistrationConsentAcceptance,
)
from app.registration.verification_service import registration_advisory_lock_key
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
REQUEST_ID = "registration-guardian-service-test"


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


@pytest.fixture
def guardian_service(db: Session):
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    sender = CapturingVerificationSender(messages=[])
    identity = IdentityService(db, cipher=cipher, lookup=lookup)
    finalization = RegistrationFinalizationService(
        db,
        cipher=cipher,
        lookup=lookup,
        clock=lambda: NOW,
    )
    service = GuardianRegistrationService(
        db,
        cipher=cipher,
        lookup=lookup,
        identity=identity,
        finalization=finalization,
        sender=sender,
        clock=lambda: NOW,
    )
    return service, identity, cipher, lookup, sender


def _minor_application(
    db: Session,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> RegistrationApplication:
    phone = "13800008211"
    identifier = normalize_registration_identifier("phone", phone)
    identity_number = make_cn_id("20120102", 211)
    application = RegistrationApplication(
        primary_purpose="student",
        state="pending_guardian",
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(lookup, identifier),
        verified_identifier_ciphertext=cipher.encrypt(
            phone,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("MinorGuard211Aa"),
        full_name="待监护未成年学员",
        identity_ciphertext=cipher.encrypt(
            identity_number,
            field_name="registration_identity",
        ),
        identity_digest=lookup.digest(identity_number),
        sex="male",
        date_of_birth=date(2012, 1, 2),
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    terms_digest = registration_terms_digest("2026-08")
    assert terms_digest is not None
    db.add(
        RegistrationConsentAcceptance(
            registration_application_id=application.id,
            account_id=None,
            consent_type="registration_terms",
            policy_version="2026-08",
            content_digest=terms_digest,
            accepted_at=NOW,
            request_id=REQUEST_ID,
            source="self_registration",
        )
    )
    db.commit()
    return application


def _guardian_account(
    db: Session,
    identity: IdentityService,
) -> UserAccount:
    guardian = identity.create_guardian(
        GuardianCreate(
            full_name="受邀监护人",
            identity_number=make_cn_id("19800102", 213),
            sex="male",
            date_of_birth=date(1980, 1, 2),
            phone="13800008213",
            email="guardian-8213@example.com",
        )
    )
    accounts = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: NOW.date(),
    )
    created = accounts.create_account(AccountCreate(person_id=guardian.id))
    account = db.get(UserAccount, created.account.id)
    assert account is not None
    account.force_password_change = False
    db.commit()
    return account


def _adult_student_account(
    db: Session,
    identity: IdentityService,
) -> UserAccount:
    student = identity.create_student(
        StudentCreate(
            full_name="成年自助学员",
            identity_number=make_cn_id("19900102", 215),
            sex="male",
            date_of_birth=date(1990, 1, 2),
            phone="13800008215",
            email="student-8215@example.com",
            student_number="REG-ADULT-8215",
        )
    )
    accounts = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: NOW.date(),
    )
    created = accounts.create_account(AccountCreate(person_id=student.id))
    account = db.get(UserAccount, created.account.id)
    assert account is not None
    account.force_password_change = False
    db.commit()
    return account


def test_minor_stays_registration_only_until_guardian_accepts(
    db: Session,
    guardian_service,
) -> None:
    service, identity, cipher, lookup, sender = guardian_service
    application = _minor_application(db, cipher=cipher, lookup=lookup)
    guardian_account = _guardian_account(db, identity)

    invitation = service.invite(
        application.id,
        identifier_kind="phone",
        identifier="13800008213",
        relationship="mother",
        is_primary=True,
        request_id=REQUEST_ID,
    )

    db.expire_all()
    pending = db.get(RegistrationApplication, application.id)
    assert pending is not None and pending.state == "pending_guardian"
    assert pending.finalized_account_id is None
    assert sender.message_for(invitation.id).purpose == "guardian_invitation"

    accepted = service.accept(
        invitation.id,
        guardian_account_id=guardian_account.id,
        consent_version="2026-08",
        request_id=REQUEST_ID,
    )

    db.expire_all()
    finalized = db.get(RegistrationApplication, application.id)
    assert finalized is not None and finalized.state == "finalized"
    assert finalized.finalized_account_id is not None
    assert accepted.student_finalized is True
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.guardian_person_id == guardian_account.person_id,
            GuardianRelationship.student_person_id == finalized.matched_person_id,
        )
    ) == 1
    assert db.scalar(
        select(func.count(RegistrationConsentAcceptance.id)).where(
            RegistrationConsentAcceptance.account_id == guardian_account.id,
            RegistrationConsentAcceptance.consent_type == "guardian_relationship",
        )
    ) == 1
    guardian_role = db.scalar(select(Role).where(Role.key == "guardian"))
    assert guardian_role is not None
    assert db.get(UserRole, (guardian_account.id, guardian_role.id)) is not None
    stored_invitation = db.get(GuardianRegistrationInvitation, invitation.id)
    assert stored_invitation is not None
    assert (
        stored_invitation.invited_identifier_digest
        == REDACTED_GUARDIAN_INVITATION_DIGEST
    )
    assert (
        stored_invitation.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )

    repeated = service.accept(
        invitation.id,
        guardian_account_id=guardian_account.id,
        consent_version="2026-08",
        request_id=REQUEST_ID,
    )
    assert repeated.relationship_id == accepted.relationship_id
    assert repeated.student_finalized is accepted.student_finalized
    assert db.scalar(select(func.count(GuardianRelationship.id))) == 1


def test_reviewed_minor_candidate_survives_until_guardian_acceptance(
    db: Session,
    guardian_service,
) -> None:
    service, identity, cipher, lookup, _sender = guardian_service
    application = _minor_application(db, cipher=cipher, lookup=lookup)
    candidate = identity.create_base_person(
        PersonBaseInput(
            full_name="待监护未成年学员",
            identity_number=make_cn_id("20120102", 211),
            sex="male",
            date_of_birth=date(2012, 1, 2),
            phone=None,
            email=None,
        )
    )
    guardian_account = _guardian_account(db, identity)
    guardian_account_id = guardian_account.id
    candidate_id = candidate.id
    application.review_decision = "approved"
    application.reviewed_by_user_id = guardian_account_id
    application.reviewed_candidate_person_id = candidate_id
    application.review_reason = "实名资料已核验等待监护确认"
    application.reviewed_at = NOW
    db.commit()
    invitation = service.invite(
        application.id,
        identifier_kind="phone",
        identifier="13800008213",
        relationship="mother",
        is_primary=True,
        request_id=REQUEST_ID,
    )

    accepted = service.accept(
        invitation.id,
        guardian_account_id=guardian_account_id,
        consent_version="2026-08",
        request_id=REQUEST_ID,
    )

    db.expire_all()
    finalized = db.get(RegistrationApplication, application.id)
    assert finalized is not None
    assert finalized.state == "finalized"
    assert finalized.matched_person_id == candidate_id
    assert finalized.reviewed_candidate_person_id is None
    assert finalized.finalized_account_id is not None
    assert accepted.student_finalized is True
    assert db.get(StudentProfile, candidate_id) is not None
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.guardian_person_id
            == guardian_account.person_id,
            GuardianRelationship.student_person_id == candidate_id,
        )
    ) == 1


def test_reviewed_minor_candidate_drift_keeps_invitation_pending(
    db: Session,
    guardian_service,
) -> None:
    service, identity, cipher, lookup, _sender = guardian_service
    application = _minor_application(db, cipher=cipher, lookup=lookup)
    candidate = identity.create_base_person(
        PersonBaseInput(
            full_name="待监护未成年学员",
            identity_number=make_cn_id("20120102", 211),
            sex="male",
            date_of_birth=date(2012, 1, 2),
            phone=None,
            email=None,
        )
    )
    guardian_account = _guardian_account(db, identity)
    guardian_account_id = guardian_account.id
    candidate_id = candidate.id
    application.review_decision = "approved"
    application.reviewed_by_user_id = guardian_account_id
    application.reviewed_candidate_person_id = candidate_id
    application.review_reason = "实名资料已核验等待监护确认"
    application.reviewed_at = NOW
    db.commit()
    invitation = service.invite(
        application.id,
        identifier_kind="phone",
        identifier="13800008213",
        relationship="mother",
        is_primary=True,
        request_id=REQUEST_ID,
    )
    stored_candidate = db.get(Person, candidate_id)
    assert stored_candidate is not None
    changed_identity = make_cn_id("20120102", 217)
    stored_candidate.identity_ciphertext = cipher.encrypt(
        changed_identity,
        field_name="identity_number",
    )
    stored_candidate.identity_digest = lookup.digest(changed_identity)
    db.commit()

    with pytest.raises(GuardianInvitationUnavailable):
        service.accept(
            invitation.id,
            guardian_account_id=guardian_account_id,
            consent_version="2026-08",
            request_id=REQUEST_ID,
        )

    db.expire_all()
    stored_application = db.get(RegistrationApplication, application.id)
    stored_invitation = db.get(GuardianRegistrationInvitation, invitation.id)
    assert stored_application is not None
    assert stored_application.state == "conflict"
    assert stored_application.conflict_code == "reviewed_candidate_changed"
    assert stored_application.reviewed_candidate_person_id is None
    assert stored_application.finalized_account_id is None
    assert stored_invitation is not None
    assert stored_invitation.status == "pending"
    assert stored_invitation.accepted_at is None
    assert db.get(StudentProfile, candidate_id) is None
    assert db.scalar(
        select(func.count(UserAccount.id)).where(
            UserAccount.person_id == candidate_id
        )
    ) == 0
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.guardian_person_id
            == guardian_account.person_id
        )
    ) == 0


@pytest.mark.parametrize("requires_verification", (False, True))
def test_reviewed_minor_acceptance_reuses_existing_valid_guardian_relationship(
    db: Session,
    guardian_service,
    requires_verification: bool,
) -> None:
    service, identity, cipher, lookup, _sender = guardian_service
    application = _minor_application(db, cipher=cipher, lookup=lookup)
    guardian_account = _guardian_account(db, identity)
    candidate = identity.create_student(
        StudentCreate(
            full_name="待监护未成年学员",
            identity_number=make_cn_id("20120102", 211),
            sex="male",
            date_of_birth=date(2012, 1, 2),
            phone="13800008211",
            email="student-8211@example.com",
            student_number="REG-MINOR-8211",
        )
    )
    existing_relationship = identity.add_guardian(
        candidate.id,
        guardian_account.person_id,
        relationship="mother",
        is_primary=True,
        valid_from=NOW.date(),
        valid_until=None,
    )
    relationship_fact = db.get(
        GuardianRelationship, existing_relationship.relationship_id
    )
    assert relationship_fact is not None
    relationship_fact.requires_verification = requires_verification
    guardian_account_id = guardian_account.id
    candidate_id = candidate.id
    application.review_decision = "approved"
    application.reviewed_by_user_id = guardian_account_id
    application.reviewed_candidate_person_id = candidate_id
    application.review_reason = "实名资料已核验等待监护确认"
    application.reviewed_at = NOW
    db.commit()
    invitation = service.invite(
        application.id,
        identifier_kind="phone",
        identifier="13800008213",
        relationship="mother",
        is_primary=True,
        request_id=REQUEST_ID,
    )

    accepted = service.accept(
        invitation.id,
        guardian_account_id=guardian_account_id,
        consent_version="2026-08",
        request_id=REQUEST_ID,
    )

    db.expire_all()
    finalized = db.get(RegistrationApplication, application.id)
    stored_invitation = db.get(GuardianRegistrationInvitation, invitation.id)
    assert finalized is not None
    assert finalized.state == "finalized"
    assert finalized.matched_person_id == candidate_id
    assert finalized.finalized_account_id is not None
    assert finalized.reviewed_candidate_person_id is None
    assert accepted.relationship_id == existing_relationship.relationship_id
    assert relationship_fact.requires_verification is False
    assert accepted.student_finalized is True
    assert stored_invitation is not None
    assert stored_invitation.status == "accepted"
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.guardian_person_id
            == guardian_account.person_id,
            GuardianRelationship.student_person_id == candidate_id,
        )
    ) == 1


@pytest.mark.parametrize(
    ("conflict_source", "expected_conflict_code"),
    [
        ("split_match", "split_identifier_match"),
        ("controlled_integrity", "concurrent_claim"),
    ],
)
def test_guardian_acceptance_persists_finalization_conflict(
    db: Session,
    guardian_service,
    monkeypatch: pytest.MonkeyPatch,
    conflict_source: str,
    expected_conflict_code: str,
) -> None:
    service, identity, cipher, lookup, _sender = guardian_service
    application = _minor_application(db, cipher=cipher, lookup=lookup)

    if conflict_source == "split_match":
        identity.create_base_person(
            PersonBaseInput(
                full_name="待监护未成年学员",
                identity_number=make_cn_id("20120102", 211),
                sex="male",
                date_of_birth=date(2012, 1, 2),
                phone="13800008219",
                email=None,
            )
        )
        identity.create_base_person(
            PersonBaseInput(
                full_name="联系方式命中人员",
                identity_number=make_cn_id("19900102", 219),
                sex="male",
                date_of_birth=date(1990, 1, 2),
                phone="13800008211",
                email=None,
            )
        )
    else:
        class ConstraintViolation(Exception):
            class diag:
                constraint_name = "uq_account_login_identifiers_identifier_digest"

        def fail_with_controlled_constraint(
            _service: RegistrationFinalizationService,
            *_args: object,
            **_kwargs: object,
        ) -> UserAccount:
            raise IntegrityError("concurrent claim", {}, ConstraintViolation())

        monkeypatch.setattr(
            RegistrationFinalizationService,
            "_new_account",
            fail_with_controlled_constraint,
        )
    db.commit()
    guardian_account = _guardian_account(db, identity)
    invitation = service.invite(
        application.id,
        identifier_kind="phone",
        identifier="13800008213",
        relationship="mother",
        is_primary=True,
        request_id=REQUEST_ID,
    )

    with pytest.raises(GuardianInvitationUnavailable):
        service.accept(
            invitation.id,
            guardian_account_id=guardian_account.id,
            consent_version="2026-08",
            request_id=REQUEST_ID,
        )

    db.expire_all()
    stored_application = db.get(RegistrationApplication, application.id)
    stored_invitation = db.get(GuardianRegistrationInvitation, invitation.id)
    assert stored_application is not None
    assert stored_application.state == "conflict"
    assert stored_application.conflict_code == expected_conflict_code
    assert stored_application.matched_person_id is None
    assert stored_application.finalized_account_id is None
    assert stored_invitation is not None and stored_invitation.status == "pending"
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.guardian_person_id == guardian_account.person_id
        )
    ) == 0
    assert db.scalar(
        select(func.count(RegistrationConsentAcceptance.id)).where(
            RegistrationConsentAcceptance.account_id == guardian_account.id,
            RegistrationConsentAcceptance.consent_type == "guardian_relationship",
        )
    ) == 0
    assert db.scalar(select(1)) == 1


def test_adult_invitation_never_blocks_account(
    db: Session,
    guardian_service,
) -> None:
    service, identity, _cipher, _lookup, _sender = guardian_service
    adult_account = _adult_student_account(db, identity)

    invitation = service.invite_for_account(
        adult_account.id,
        identifier_kind="email",
        identifier="family-8217@example.com",
        relationship="parent",
        is_primary=True,
        request_id=REQUEST_ID,
    )

    db.expire_all()
    account = db.get(UserAccount, adult_account.id)
    assert invitation.status == "pending"
    assert account is not None and account.status == "active"


def test_outbound_invitation_secret_is_separate_from_management_id_and_stored_as_digest(
    db: Session,
    guardian_service,
) -> None:
    service, identity, _cipher, lookup, sender = guardian_service
    adult_account = _adult_student_account(db, identity)

    invitation = service.invite_for_account(
        adult_account.id,
        identifier_kind="email",
        identifier="family-secret-8218@example.com",
        relationship="parent",
        is_primary=False,
        request_id=REQUEST_ID,
    )

    delivered_secret = sender.message_for(invitation.id).code
    stored = db.get(GuardianRegistrationInvitation, invitation.id)

    assert delivered_secret != invitation.id.hex
    assert len(delivered_secret) >= 43
    assert stored is not None
    assert stored.handoff_token_digest == lookup.digest_domain(
        "guardian-invitation-handoff",
        delivered_secret,
    )
    assert delivered_secret not in repr(stored)


def test_resend_revokes_previous_invitation_and_revoke_preserves_history(
    db: Session,
    guardian_service,
) -> None:
    service, _identity, cipher, lookup, sender = guardian_service
    application = _minor_application(db, cipher=cipher, lookup=lookup)
    first = service.invite(
        application.id,
        identifier_kind="email",
        identifier="family-8219@example.com",
        relationship="parent",
        is_primary=False,
        request_id=REQUEST_ID,
    )
    assert first.expires_at == NOW + timedelta(days=7)

    second = service.resend(
        application.id,
        first.id,
        request_id=REQUEST_ID,
    )
    revoked = service.revoke(application.id, second.id, request_id=REQUEST_ID)

    db.expire_all()
    first_row = db.get(GuardianRegistrationInvitation, first.id)
    second_row = db.get(GuardianRegistrationInvitation, second.id)
    assert first.id != second.id
    assert second.expires_at == NOW + timedelta(days=7)
    assert first_row is not None and first_row.status == "revoked"
    assert second_row is not None and second_row.status == "revoked"
    assert first_row.invited_identifier_digest == REDACTED_GUARDIAN_INVITATION_DIGEST
    assert (
        first_row.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )
    assert second_row.invited_identifier_digest == REDACTED_GUARDIAN_INVITATION_DIGEST
    assert (
        second_row.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )
    assert revoked.status == "revoked"
    assert revoked.masked_identifier == REDACTED_GUARDIAN_INVITATION_MASK
    assert len(sender.messages) == 2
    history = service.list_for_application(application.id)
    assert [item.status for item in history] == ["revoked", "revoked"]
    assert [item.masked_identifier for item in history] == [
        REDACTED_GUARDIAN_INVITATION_MASK,
        REDACTED_GUARDIAN_INVITATION_MASK,
    ]


def test_listing_expires_and_clears_pending_invitation_identifier(
    db: Session,
    guardian_service,
) -> None:
    service, _identity, cipher, lookup, _sender = guardian_service
    application = _minor_application(db, cipher=cipher, lookup=lookup)
    invitation = service.invite(
        application.id,
        identifier_kind="email",
        identifier="family-expired@example.com",
        relationship="parent",
        is_primary=False,
        request_id=REQUEST_ID,
    )
    stored = db.get(GuardianRegistrationInvitation, invitation.id)
    assert stored is not None
    stored.expires_at = NOW
    db.commit()

    history = service.list_for_application(application.id)

    db.expire_all()
    stored = db.get(GuardianRegistrationInvitation, invitation.id)
    assert stored is not None
    assert stored.status == "expired"
    assert stored.invited_identifier_digest == REDACTED_GUARDIAN_INVITATION_DIGEST
    assert (
        stored.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )
    assert history[0].masked_identifier == REDACTED_GUARDIAN_INVITATION_MASK


def test_acceptance_locks_guardian_relationship_before_person_and_account_rows(
    db: Session,
    guardian_service,
) -> None:
    service, identity, cipher, lookup, _sender = guardian_service
    application = _minor_application(db, cipher=cipher, lookup=lookup)
    guardian_account = _guardian_account(db, identity)
    invitation = service.invite(
        application.id,
        identifier_kind="phone",
        identifier="13800008213",
        relationship="mother",
        is_primary=True,
        request_id=REQUEST_ID,
    )
    application_identifier_digest = application.verified_identifier_digest
    assert application_identifier_digest is not None
    bind = db.get_bind()
    assert isinstance(bind, Connection)
    lock_statements: list[tuple[str, tuple[object, ...]]] = []

    def record_lock_statement(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if "for update" in normalized or "pg_advisory_xact_lock" in normalized:
            if isinstance(parameters, dict):
                values = tuple(parameters.values())
            elif isinstance(parameters, (list, tuple)):
                values = tuple(parameters)
            else:
                values = (parameters,)
            lock_statements.append((normalized, values))

    event.listen(bind, "before_cursor_execute", record_lock_statement)
    try:
        service.accept(
            invitation.id,
            guardian_account_id=guardian_account.id,
            consent_version="2026-08",
            request_id=REQUEST_ID,
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_lock_statement)

    def first_position(fragment: str) -> int:
        return next(
            index
            for index, (statement, _parameters) in enumerate(lock_statements)
            if fragment in statement
        )

    application_position = first_position("from registration_applications")
    invitation_position = first_position("from guardian_registration_invitations")
    people_position = first_position("from people")
    advisory_positions = [
        index
        for index, (statement, _parameters) in enumerate(lock_statements)
        if "pg_advisory_xact_lock" in statement
    ]
    application_identifier_lock = registration_advisory_lock_key(
        "registration-application-identifier",
        application_identifier_digest,
    )
    application_identifier_lock_position = next(
        index
        for index, (statement, parameters) in enumerate(lock_statements)
        if "pg_advisory_xact_lock" in statement
        and application_identifier_lock in parameters
    )
    assert advisory_positions[0] < application_position
    assert application_identifier_lock_position < application_position
    assert application_position < invitation_position
    assert any(
        invitation_position < position < people_position
        for position in advisory_positions
    )
    assert first_position("from people") < first_position("from user_accounts")
