from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.auth.service as auth_service_module
from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService, AuthService
from app.identity.models import Person
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.registration.delivery import CapturingVerificationSender
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import (
    RegistrationApplication,
    RegistrationSession,
    RegistrationVerificationChallenge,
)
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationInvalidOrExpired,
    VerificationTargetMismatch,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
FIELD_KEY = bytes(range(32))
VERIFICATION_KEY = bytes(range(1, 33))
SESSION_KEY = bytes(range(2, 34))
OLD_PASSWORD = "Old2026PassAa"
NEW_PASSWORD = "New2026PassAa"


def _mapped_snapshot(row: object) -> dict[str, object]:
    return {
        column.key: getattr(row, column.key)
        for column in row.__mapper__.columns  # type: ignore[attr-defined]
    }


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _auth(db: Session) -> AuthService:
    return AuthService(
        db,
        cipher=EnvelopeCipher(FIELD_KEY),
        lookup=LookupDigest(FIELD_KEY),
        passwords=PasswordService(),
        session_token_pepper=SESSION_KEY,
        session_ttl_minutes=480,
        clock=lambda: NOW,
    )


def _verification(
    db: Session,
    sender: CapturingVerificationSender,
) -> RegistrationVerificationService:
    return RegistrationVerificationService(
        db,
        lookup=LookupDigest(FIELD_KEY),
        verification_pepper=VERIFICATION_KEY,
        sender=sender,
        app_env="test",
        clock=lambda: NOW,
    )


def _account(
    db: Session,
    *,
    sequence: int,
    status: str = "active",
) -> tuple[UserAccount, str, str]:
    phone = f"139{sequence:08d}"
    email = f"recovery-{sequence}@example.com"
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(FIELD_KEY),
        lookup=LookupDigest(FIELD_KEY),
    )
    person = identity.create_base_person(
        PersonBaseInput(
            full_name=f"密码找回测试人员{sequence}",
            identity_number=make_cn_id("19900102", sequence),
            sex="male",
            date_of_birth=date(1990, 1, 2),
            phone=phone,
            email=email,
        )
    )
    created = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: NOW.date(),
    ).create_account(
        AccountCreate(person_id=person.id, login_phone=phone, email=email)
    )
    account = db.get(UserAccount, created.account.id)
    assert account is not None
    account.password_hash = PasswordService().hash(OLD_PASSWORD)
    account.force_password_change = False
    account.status = status
    db.flush()
    return account, phone, email


def test_password_challenge_is_nonenumerating_for_valid_identifiers(db: Session) -> None:
    sender = CapturingVerificationSender(messages=[])
    active, active_phone, _ = _account(db, sequence=601)
    disabled, disabled_phone, _ = _account(db, sequence=603, status="disabled")
    unverified, _, unverified_email = _account(db, sequence=605)
    assert active.id != disabled.id != unverified.id

    issued = [
        _auth(db).issue_password_reset(
            identifier=value,
            request_source=f"127.0.0.{index}",
            verification=_verification(db, sender),
        )
        for index, value in enumerate(
            [
                active_phone,
                "13999999999",
                disabled_phone,
                unverified_email,
            ],
            start=1,
        )
    ]

    assert all(result.expires_at == NOW + timedelta(minutes=10) for result in issued)
    assert len(sender.messages) == 4
    bound_accounts = [
        db.get(RegistrationVerificationChallenge, result.challenge_id).account_id
        for result in issued
    ]
    assert bound_accounts == [active.id, None, None, None]


def test_password_recovery_revokes_every_old_session_and_registration_session(
    db: Session,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, _ = _account(db, sequence=607)
    account.failed_login_count = 4
    account.locked_until = NOW + timedelta(minutes=10)
    auth_sessions = [
        AuthSession(
            user_id=account.id,
            token_digest=f"formal-token-{index}",
            csrf_digest=f"formal-csrf-{index}",
            expires_at=NOW + timedelta(hours=1),
        )
        for index in range(2)
    ]
    db.add_all(auth_sessions)
    application = RegistrationApplication(
        primary_purpose="student",
        state="finalized",
        finalized_account_id=account.id,
        finalization_match_kind="new_person",
        expires_at=NOW + timedelta(days=30),
        finalized_at=NOW,
    )
    db.add(application)
    db.flush()
    registration_session = RegistrationSession(
        application_id=application.id,
        token_digest="registration-token",
        csrf_digest="registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    db.add(registration_session)
    db.commit()
    auth = _auth(db)
    verification = _verification(db, sender)
    challenge = auth.issue_password_reset(
        identifier=phone,
        request_source="127.0.0.1",
        verification=verification,
    )
    code = sender.code_for(challenge.challenge_id)

    auth.complete_password_reset(
        challenge_id=challenge.challenge_id,
        code=code,
        new_password=NEW_PASSWORD,
        request_id="password-reset-service-test",
        verification=verification,
    )

    db.expire_all()
    stored = db.get(UserAccount, account.id)
    assert stored is not None
    assert PasswordService().verify(NEW_PASSWORD, stored.password_hash)
    assert stored.failed_login_count == 0
    assert stored.locked_until is None
    assert stored.force_password_change is False
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == account.id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 0
    assert db.get(RegistrationSession, registration_session.id).revoked_at == NOW
    assert db.get(RegistrationVerificationChallenge, challenge.challenge_id).consumed_at == NOW
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "auth.password_reset",
            AuditEvent.resource_id == account.id,
        )
    )
    assert audit is not None and audit.event_metadata == {}
    assert phone not in str(audit.event_metadata)


def test_password_recovery_revokes_unfinished_registration_for_verified_contact(
    db: Session,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    _account_row, phone, _ = _account(db, sequence=627)
    normalized = normalize_registration_identifier("phone", phone)
    application = RegistrationApplication(
        primary_purpose="student",
        state="contact_verified",
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(
            LookupDigest(FIELD_KEY),
            normalized,
        ),
        verified_identifier_ciphertext=EnvelopeCipher(FIELD_KEY).encrypt(
            phone,
            field_name="registration_identifier",
        ),
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    registration_session = RegistrationSession(
        application_id=application.id,
        token_digest="unfinished-registration-token",
        csrf_digest="unfinished-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    db.add(registration_session)
    db.commit()
    auth = _auth(db)
    verification = _verification(db, sender)
    challenge = auth.issue_password_reset(
        identifier=phone,
        request_source="127.0.0.8",
        verification=verification,
    )

    auth.complete_password_reset(
        challenge_id=challenge.challenge_id,
        code=sender.code_for(challenge.challenge_id),
        new_password=NEW_PASSWORD,
        request_id="recovery-unfinished-session",
        verification=verification,
    )

    db.expire_all()
    assert db.get(RegistrationSession, registration_session.id).revoked_at == NOW
    stored_application = db.get(RegistrationApplication, application.id)
    assert stored_application is not None
    assert stored_application.state == "expired"
    assert stored_application.verified_identifier_digest is None


def test_password_reset_terminates_all_registration_credentials_for_account_contacts(
    db: Session,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, email = _account(db, sequence=629)
    unrelated_account, unrelated_phone, _ = _account(db, sequence=631)
    lookup = LookupDigest(FIELD_KEY)
    cipher = EnvelopeCipher(FIELD_KEY)
    phone_identifier = normalize_registration_identifier("phone", phone)
    email_identifier = normalize_registration_identifier("email", email)
    unrelated_identifier = normalize_registration_identifier(
        "phone",
        unrelated_phone,
    )
    db.add(
        AccountLoginIdentifier(
            account_id=account.id,
            kind="email",
            identifier_digest=login_identifier_digest(
                lookup,
                normalize_login_identifier(email),
            ),
            verified_at=NOW,
            verification_source="test_verified",
            is_primary=False,
        )
    )
    phone_identity = make_cn_id("19900102", 633)
    phone_application = RegistrationApplication(
        primary_purpose="student",
        state="pending_guardian",
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(
            lookup,
            phone_identifier,
        ),
        verified_identifier_ciphertext=cipher.encrypt(
            phone_identifier.value,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("Reg629PassAa"),
        full_name="手机号待终止申请",
        identity_ciphertext=cipher.encrypt(
            phone_identity,
            field_name="registration_identity",
        ),
        identity_digest=lookup.digest(phone_identity),
        sex="male",
        date_of_birth=date(1990, 1, 2),
        review_decision="approved",
        reviewed_by_user_id=account.id,
        reviewed_candidate_person_id=account.person_id,
        review_reason="资料审核通过",
        reviewed_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    email_application = RegistrationApplication(
        primary_purpose="guardian",
        state="contact_verified",
        verified_identifier_kind="email",
        verified_identifier_digest=registration_identifier_digest(
            lookup,
            email_identifier,
        ),
        verified_identifier_ciphertext=cipher.encrypt(
            email_identifier.value,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("Reg630PassAa"),
        expires_at=NOW + timedelta(days=30),
    )
    finalized_application = RegistrationApplication(
        primary_purpose="student",
        state="finalized",
        matched_person_id=account.person_id,
        finalized_account_id=account.id,
        finalization_match_kind="existing_account",
        finalized_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    rejected_application = RegistrationApplication(
        primary_purpose="guardian",
        state="rejected",
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(
            lookup,
            phone_identifier,
        ),
        verified_identifier_ciphertext=cipher.encrypt(
            phone_identifier.value,
            field_name="registration_identifier",
        ),
        rejected_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    expired_application = RegistrationApplication(
        primary_purpose="visitor",
        state="expired",
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(
            lookup,
            phone_identifier,
        ),
        verified_identifier_ciphertext=cipher.encrypt(
            phone_identifier.value,
            field_name="registration_identifier",
        ),
        expires_at=NOW,
    )
    unrelated_application = RegistrationApplication(
        primary_purpose="student",
        state="contact_verified",
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(
            lookup,
            unrelated_identifier,
        ),
        verified_identifier_ciphertext=cipher.encrypt(
            unrelated_identifier.value,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("Other631PassAa"),
        expires_at=NOW + timedelta(days=30),
    )
    db.add_all(
        [
            phone_application,
            email_application,
            finalized_application,
            rejected_application,
            expired_application,
            unrelated_application,
        ]
    )
    db.flush()
    phone_session = RegistrationSession(
        application_id=phone_application.id,
        token_digest="password-reset-phone-registration-session",
        csrf_digest="password-reset-phone-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    email_session = RegistrationSession(
        application_id=email_application.id,
        token_digest="password-reset-email-registration-session",
        csrf_digest="password-reset-email-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    finalized_session = RegistrationSession(
        application_id=finalized_application.id,
        token_digest="password-reset-finalized-registration-session",
        csrf_digest="password-reset-finalized-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    unrelated_session = RegistrationSession(
        application_id=unrelated_application.id,
        token_digest="password-reset-unrelated-registration-session",
        csrf_digest="password-reset-unrelated-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    rejected_session = RegistrationSession(
        application_id=rejected_application.id,
        token_digest="password-reset-rejected-registration-session",
        csrf_digest="password-reset-rejected-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    expired_session = RegistrationSession(
        application_id=expired_application.id,
        token_digest="password-reset-expired-registration-session",
        csrf_digest="password-reset-expired-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )

    def registration_challenge(
        application: RegistrationApplication,
        *,
        kind: str,
        digest: str,
        marker: str,
    ) -> RegistrationVerificationChallenge:
        return RegistrationVerificationChallenge(
            application_id=application.id,
            account_id=None,
            purpose="registration",
            identifier_kind=kind,
            identifier_digest=digest,
            code_digest=marker * 64,
            request_source_digest=marker.upper() * 64,
            expires_at=NOW + timedelta(minutes=10),
            attempts=0,
            last_sent_at=NOW - timedelta(minutes=2),
        )

    phone_registration_challenge = registration_challenge(
        phone_application,
        kind="phone",
        digest=phone_application.verified_identifier_digest,
        marker="a",
    )
    email_registration_challenge = registration_challenge(
        email_application,
        kind="email",
        digest=email_application.verified_identifier_digest,
        marker="b",
    )
    unrelated_registration_challenge = registration_challenge(
        unrelated_application,
        kind="phone",
        digest=unrelated_application.verified_identifier_digest,
        marker="c",
    )
    rejected_registration_challenge = registration_challenge(
        rejected_application,
        kind="phone",
        digest=rejected_application.verified_identifier_digest,
        marker="d",
    )
    expired_registration_challenge = registration_challenge(
        expired_application,
        kind="phone",
        digest=expired_application.verified_identifier_digest,
        marker="e",
    )
    db.add_all(
        [
            phone_session,
            email_session,
            finalized_session,
            unrelated_session,
            rejected_session,
            expired_session,
            phone_registration_challenge,
            email_registration_challenge,
            unrelated_registration_challenge,
            rejected_registration_challenge,
            expired_registration_challenge,
        ]
    )
    db.commit()
    terminal_application_snapshots = {
        application.id: _mapped_snapshot(application)
        for application in (
            finalized_application,
            rejected_application,
            expired_application,
        )
    }
    auth = _auth(db)
    verification = _verification(db, sender)
    reset_challenge = auth.issue_password_reset(
        identifier=phone,
        request_source="127.0.0.29",
        verification=verification,
    )

    auth.complete_password_reset(
        challenge_id=reset_challenge.challenge_id,
        code=sender.code_for(reset_challenge.challenge_id),
        new_password=NEW_PASSWORD,
        request_id="recovery-terminates-registration-credentials",
        verification=verification,
    )

    db.expire_all()
    for application in (phone_application, email_application):
        stored = db.get(RegistrationApplication, application.id)
        assert stored is not None
        assert stored.state == "expired"
        assert stored.verified_identifier_kind is None
        assert stored.verified_identifier_digest is None
        assert stored.verified_identifier_ciphertext is None
        assert stored.password_hash is None
        assert stored.full_name is None
        assert stored.identity_ciphertext is None
        assert stored.identity_digest is None
        assert stored.review_decision is None
        assert stored.reviewed_by_user_id is None
        assert stored.reviewed_candidate_person_id is None
        assert stored.review_reason is None
        assert stored.reviewed_at is None
    for registration_session in (
        phone_session,
        email_session,
        finalized_session,
        rejected_session,
        expired_session,
    ):
        stored = db.get(RegistrationSession, registration_session.id)
        assert stored is not None and stored.revoked_at == NOW
    for challenge in (
        phone_registration_challenge,
        email_registration_challenge,
        rejected_registration_challenge,
        expired_registration_challenge,
    ):
        stored = db.get(RegistrationVerificationChallenge, challenge.id)
        assert stored is not None
        assert stored.consumed_at is None
        assert stored.expires_at == NOW
    for application_id, before in terminal_application_snapshots.items():
        stored = db.get(RegistrationApplication, application_id)
        assert stored is not None
        assert _mapped_snapshot(stored) == before
    unrelated = db.get(RegistrationApplication, unrelated_application.id)
    assert unrelated is not None and unrelated.state == "contact_verified"
    assert db.get(RegistrationSession, unrelated_session.id).revoked_at is None
    unrelated_verification = db.get(
        RegistrationVerificationChallenge,
        unrelated_registration_challenge.id,
    )
    assert unrelated_verification is not None
    assert unrelated_verification.expires_at == NOW + timedelta(minutes=10)
    assert db.get(UserAccount, unrelated_account.id).status == "active"


def test_decoy_password_reset_consumes_challenge_without_mutating_accounts(
    db: Session,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, _ = _account(db, sequence=609)
    normalized = normalize_registration_identifier("phone", phone)
    identifier_digest = registration_identifier_digest(
        LookupDigest(FIELD_KEY),
        normalized,
    )
    application = RegistrationApplication(
        primary_purpose="student",
        state="contact_verified",
        verified_identifier_kind="phone",
        verified_identifier_digest=identifier_digest,
        verified_identifier_ciphertext=EnvelopeCipher(FIELD_KEY).encrypt(
            normalized.value,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("Decoy609PassAa"),
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    registration_session = RegistrationSession(
        application_id=application.id,
        token_digest="decoy-control-registration-token",
        csrf_digest="decoy-control-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    registration_challenge = RegistrationVerificationChallenge(
        application_id=application.id,
        account_id=None,
        purpose="registration",
        identifier_kind="phone",
        identifier_digest=identifier_digest,
        code_digest="f" * 64,
        request_source_digest="e" * 64,
        expires_at=NOW + timedelta(minutes=10),
        attempts=0,
        last_sent_at=NOW - timedelta(minutes=2),
    )
    auth_session = AuthSession(
        user_id=account.id,
        token_digest="decoy-control-auth-token",
        csrf_digest="decoy-control-auth-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    db.add_all([registration_session, registration_challenge, auth_session])
    db.commit()
    auth = _auth(db)
    verification = _verification(db, sender)
    challenge = auth.issue_password_reset(
        identifier="13999999998",
        request_source="127.0.0.2",
        verification=verification,
    )
    before = {
        "account": _mapped_snapshot(db.get(UserAccount, account.id)),
        "application": _mapped_snapshot(
            db.get(RegistrationApplication, application.id)
        ),
        "registration_session": _mapped_snapshot(
            db.get(RegistrationSession, registration_session.id)
        ),
        "registration_challenge": _mapped_snapshot(
            db.get(
                RegistrationVerificationChallenge,
                registration_challenge.id,
            )
        ),
        "auth_session": _mapped_snapshot(db.get(AuthSession, auth_session.id)),
    }

    auth.complete_password_reset(
        challenge_id=challenge.challenge_id,
        code=sender.code_for(challenge.challenge_id),
        new_password=NEW_PASSWORD,
        request_id="password-reset-decoy",
        verification=verification,
    )

    db.expire_all()
    after = {
        "account": _mapped_snapshot(db.get(UserAccount, account.id)),
        "application": _mapped_snapshot(
            db.get(RegistrationApplication, application.id)
        ),
        "registration_session": _mapped_snapshot(
            db.get(RegistrationSession, registration_session.id)
        ),
        "registration_challenge": _mapped_snapshot(
            db.get(
                RegistrationVerificationChallenge,
                registration_challenge.id,
            )
        ),
        "auth_session": _mapped_snapshot(db.get(AuthSession, auth_session.id)),
    }
    assert after == before
    assert db.get(RegistrationVerificationChallenge, challenge.challenge_id).consumed_at == NOW
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "auth.password_reset"
        )
    ) == 0


def test_unverified_email_is_not_silently_promoted_to_password_recovery(
    db: Session,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, _, email = _account(db, sequence=611)
    assert db.scalar(
        select(AccountLoginIdentifier.id).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "email",
        )
    ) is None

    challenge = _auth(db).issue_password_reset(
        identifier=email,
        request_source="127.0.0.3",
        verification=_verification(db, sender),
    )

    assert db.get(RegistrationVerificationChallenge, challenge.challenge_id).account_id is None


def test_verified_email_can_recover_password(db: Session) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, _, email = _account(db, sequence=619)
    normalized = normalize_login_identifier(email)
    db.add(
        AccountLoginIdentifier(
            account_id=account.id,
            kind="email",
            identifier_digest=login_identifier_digest(
                LookupDigest(FIELD_KEY),
                normalized,
            ),
            verified_at=NOW,
            verification_source="test_verified",
            is_primary=False,
        )
    )
    db.commit()
    auth = _auth(db)
    verification = _verification(db, sender)
    challenge = auth.issue_password_reset(
        identifier=email,
        request_source="127.0.0.31",
        verification=verification,
    )

    auth.complete_password_reset(
        challenge_id=challenge.challenge_id,
        code=sender.code_for(challenge.challenge_id),
        new_password=NEW_PASSWORD,
        request_id="verified-email-password-reset",
        verification=verification,
    )

    db.expire_all()
    stored = db.get(UserAccount, account.id)
    assert stored is not None
    assert PasswordService().verify(NEW_PASSWORD, stored.password_hash)
    assert db.get(
        RegistrationVerificationChallenge,
        challenge.challenge_id,
    ).consumed_at == NOW


def test_password_reset_rejects_a_challenge_from_another_purpose(db: Session) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, _, email = _account(db, sequence=613)
    verification = _verification(db, sender)
    challenge = verification.issue(
        identifier_kind="email",
        identifier=email,
        purpose="add_login_identifier",
        request_source="127.0.0.4",
        account_id=account.id,
    )
    before_hash = account.password_hash

    with pytest.raises(VerificationInvalidOrExpired):
        _auth(db).complete_password_reset(
            challenge_id=challenge.challenge_id,
            code=sender.code_for(challenge.challenge_id),
            new_password=NEW_PASSWORD,
            request_id="wrong-purpose-password-reset",
            verification=verification,
        )

    db.expire_all()
    assert db.get(UserAccount, account.id).password_hash == before_hash
    assert db.get(RegistrationVerificationChallenge, challenge.challenge_id).consumed_at is None


def test_password_reset_audit_failure_rolls_back_password_and_challenge(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, _ = _account(db, sequence=615)
    lookup = LookupDigest(FIELD_KEY)
    normalized = normalize_registration_identifier("phone", phone)
    identifier_digest = registration_identifier_digest(lookup, normalized)
    application = RegistrationApplication(
        primary_purpose="student",
        state="contact_verified",
        verified_identifier_kind="phone",
        verified_identifier_digest=identifier_digest,
        verified_identifier_ciphertext=EnvelopeCipher(FIELD_KEY).encrypt(
            normalized.value,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("Reg615PassAa"),
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    registration_session = RegistrationSession(
        application_id=application.id,
        token_digest="audit-rollback-registration-token",
        csrf_digest="audit-rollback-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    registration_challenge = RegistrationVerificationChallenge(
        application_id=application.id,
        account_id=None,
        purpose="registration",
        identifier_kind="phone",
        identifier_digest=identifier_digest,
        code_digest="d" * 64,
        request_source_digest="e" * 64,
        expires_at=NOW + timedelta(minutes=10),
        attempts=0,
        last_sent_at=NOW - timedelta(minutes=2),
    )
    db.add_all([registration_session, registration_challenge])
    db.commit()
    verification = _verification(db, sender)
    challenge = _auth(db).issue_password_reset(
        identifier=phone,
        request_source="127.0.0.5",
        verification=verification,
    )
    before_hash = account.password_hash
    before_application = {
        column.key: getattr(application, column.key)
        for column in RegistrationApplication.__mapper__.columns
    }

    def fail_audit(_service: AuditService, **_values: object) -> None:
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with pytest.raises(RuntimeError, match="audit persistence failed"):
        _auth(db).complete_password_reset(
            challenge_id=challenge.challenge_id,
            code=sender.code_for(challenge.challenge_id),
            new_password=NEW_PASSWORD,
            request_id="password-reset-audit-failure",
            verification=verification,
        )

    db.expire_all()
    assert db.get(UserAccount, account.id).password_hash == before_hash
    assert db.get(RegistrationVerificationChallenge, challenge.challenge_id).consumed_at is None
    stored_application = db.get(RegistrationApplication, application.id)
    assert stored_application is not None
    assert {
        column.key: getattr(stored_application, column.key)
        for column in RegistrationApplication.__mapper__.columns
    } == before_application
    stored_registration_challenge = db.get(
        RegistrationVerificationChallenge,
        registration_challenge.id,
    )
    assert stored_registration_challenge is not None
    assert stored_registration_challenge.expires_at == NOW + timedelta(minutes=10)
    assert stored_registration_challenge.consumed_at is None
    stored_registration_session = db.get(
        RegistrationSession,
        registration_session.id,
    )
    assert stored_registration_session is not None
    assert stored_registration_session.revoked_at is None


def test_password_reset_secret_cleanup_failure_rolls_back_every_credential(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, _ = _account(db, sequence=635)
    normalized = normalize_registration_identifier("phone", phone)
    identifier_digest = registration_identifier_digest(
        LookupDigest(FIELD_KEY),
        normalized,
    )
    application = RegistrationApplication(
        primary_purpose="student",
        state="ready_to_finalize",
        verified_identifier_kind="phone",
        verified_identifier_digest=identifier_digest,
        verified_identifier_ciphertext=EnvelopeCipher(FIELD_KEY).encrypt(
            normalized.value,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("Clean635PassAa"),
        full_name="秘密清理失败回滚",
        identity_ciphertext=EnvelopeCipher(FIELD_KEY).encrypt(
            make_cn_id("19900102", 637),
            field_name="registration_identity",
        ),
        identity_digest=LookupDigest(FIELD_KEY).digest(
            make_cn_id("19900102", 637)
        ),
        sex="male",
        date_of_birth=date(1990, 1, 2),
        review_decision="approved",
        reviewed_by_user_id=account.id,
        review_reason="等待安全清理",
        reviewed_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    registration_session = RegistrationSession(
        application_id=application.id,
        token_digest="cleanup-failure-registration-token",
        csrf_digest="cleanup-failure-registration-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    registration_challenge = RegistrationVerificationChallenge(
        application_id=application.id,
        account_id=None,
        purpose="registration",
        identifier_kind="phone",
        identifier_digest=identifier_digest,
        code_digest="a" * 64,
        request_source_digest="b" * 64,
        expires_at=NOW + timedelta(minutes=10),
        attempts=0,
        last_sent_at=NOW - timedelta(minutes=2),
    )
    auth_session = AuthSession(
        user_id=account.id,
        token_digest="cleanup-failure-auth-token",
        csrf_digest="cleanup-failure-auth-csrf",
        expires_at=NOW + timedelta(hours=1),
    )
    db.add_all([registration_session, registration_challenge, auth_session])
    db.commit()
    verification = _verification(db, sender)
    reset_challenge = _auth(db).issue_password_reset(
        identifier=phone,
        request_source="127.0.0.35",
        verification=verification,
    )
    before = {
        "account": _mapped_snapshot(db.get(UserAccount, account.id)),
        "application": _mapped_snapshot(
            db.get(RegistrationApplication, application.id)
        ),
        "registration_session": _mapped_snapshot(
            db.get(RegistrationSession, registration_session.id)
        ),
        "registration_challenge": _mapped_snapshot(
            db.get(
                RegistrationVerificationChallenge,
                registration_challenge.id,
            )
        ),
        "auth_session": _mapped_snapshot(db.get(AuthSession, auth_session.id)),
        "reset_challenge": _mapped_snapshot(
            db.get(
                RegistrationVerificationChallenge,
                reset_challenge.challenge_id,
            )
        ),
    }

    def fail_secret_cleanup(_application: RegistrationApplication) -> None:
        raise RuntimeError("registration secret cleanup failed")

    monkeypatch.setattr(
        auth_service_module,
        "clear_registration_application_secrets",
        fail_secret_cleanup,
    )
    with pytest.raises(RuntimeError, match="registration secret cleanup failed"):
        _auth(db).complete_password_reset(
            challenge_id=reset_challenge.challenge_id,
            code=sender.code_for(reset_challenge.challenge_id),
            new_password=NEW_PASSWORD,
            request_id="password-reset-cleanup-failure",
            verification=verification,
        )

    db.expire_all()
    after = {
        "account": _mapped_snapshot(db.get(UserAccount, account.id)),
        "application": _mapped_snapshot(
            db.get(RegistrationApplication, application.id)
        ),
        "registration_session": _mapped_snapshot(
            db.get(RegistrationSession, registration_session.id)
        ),
        "registration_challenge": _mapped_snapshot(
            db.get(
                RegistrationVerificationChallenge,
                registration_challenge.id,
            )
        ),
        "auth_session": _mapped_snapshot(db.get(AuthSession, auth_session.id)),
        "reset_challenge": _mapped_snapshot(
            db.get(
                RegistrationVerificationChallenge,
                reset_challenge.challenge_id,
            )
        ),
    }
    assert after == before
    assert db.scalar(select(1)) == 1


def test_password_reset_identifier_snapshot_change_rolls_back_consumption(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, _ = _account(db, sequence=639)
    verification = _verification(db, sender)
    reset_challenge = _auth(db).issue_password_reset(
        identifier=phone,
        request_source="127.0.0.39",
        verification=verification,
    )
    identifier = db.scalar(
        select(AccountLoginIdentifier).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "phone",
        )
    )
    assert identifier is not None
    before_identifier = _mapped_snapshot(identifier)
    before_account = _mapped_snapshot(account)
    original_lock = auth_service_module.lock_registration_credential_keys

    def change_identifier_after_snapshot(
        session: Session,
        keys: list[int],
    ) -> None:
        original_lock(session, keys)
        stored = session.get(AccountLoginIdentifier, identifier.id)
        assert stored is not None
        stored.identifier_digest = login_identifier_digest(
            LookupDigest(FIELD_KEY),
            normalize_login_identifier("13988888888"),
        )
        session.flush()

    monkeypatch.setattr(
        auth_service_module,
        "lock_registration_credential_keys",
        change_identifier_after_snapshot,
    )
    with pytest.raises(VerificationTargetMismatch):
        _auth(db).complete_password_reset(
            challenge_id=reset_challenge.challenge_id,
            code=sender.code_for(reset_challenge.challenge_id),
            new_password=NEW_PASSWORD,
            request_id="password-reset-identifier-change",
            verification=verification,
        )

    db.expire_all()
    assert _mapped_snapshot(db.get(AccountLoginIdentifier, identifier.id)) == before_identifier
    assert _mapped_snapshot(db.get(UserAccount, account.id)) == before_account
    stored_reset = db.get(
        RegistrationVerificationChallenge,
        reset_challenge.challenge_id,
    )
    assert stored_reset is not None and stored_reset.consumed_at is None
    assert db.scalar(select(1)) == 1


@pytest.mark.parametrize(
    "corruption",
    ["contact_ciphertext", "identifier_digest"],
)
def test_password_reset_integrity_failure_has_no_partial_success(
    db: Session,
    corruption: str,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, _ = _account(
        db,
        sequence=641 if corruption == "contact_ciphertext" else 643,
    )
    verification = _verification(db, sender)
    reset_challenge = _auth(db).issue_password_reset(
        identifier=phone,
        request_source=(
            "127.0.0.41"
            if corruption == "contact_ciphertext"
            else "127.0.0.43"
        ),
        verification=verification,
    )
    person = db.get(Person, account.person_id)
    identifier = db.scalar(
        select(AccountLoginIdentifier).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "phone",
        )
    )
    assert person is not None
    assert identifier is not None
    if corruption == "contact_ciphertext":
        person.phone_ciphertext = "invalid-envelope-ciphertext"
    else:
        identifier.identifier_digest = login_identifier_digest(
            LookupDigest(FIELD_KEY),
            normalize_login_identifier("13977777777"),
        )
    db.commit()
    before = {
        "person": _mapped_snapshot(person),
        "account": _mapped_snapshot(account),
        "identifier": _mapped_snapshot(identifier),
        "reset_challenge": _mapped_snapshot(
            db.get(
                RegistrationVerificationChallenge,
                reset_challenge.challenge_id,
            )
        ),
    }

    with pytest.raises(VerificationTargetMismatch):
        _auth(db).complete_password_reset(
            challenge_id=reset_challenge.challenge_id,
            code=sender.code_for(reset_challenge.challenge_id),
            new_password=NEW_PASSWORD,
            request_id=f"password-reset-integrity-{corruption}",
            verification=verification,
        )

    db.expire_all()
    after = {
        "person": _mapped_snapshot(db.get(Person, account.person_id)),
        "account": _mapped_snapshot(db.get(UserAccount, account.id)),
        "identifier": _mapped_snapshot(
            db.get(AccountLoginIdentifier, identifier.id)
        ),
        "reset_challenge": _mapped_snapshot(
            db.get(
                RegistrationVerificationChallenge,
                reset_challenge.challenge_id,
            )
        ),
    }
    assert after == before
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "auth.password_reset",
            AuditEvent.resource_id == account.id,
        )
    ) == 0
    assert db.scalar(select(1)) == 1


def test_account_disabled_after_challenge_rolls_back_password_reset(db: Session) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, _ = _account(db, sequence=617)
    verification = _verification(db, sender)
    challenge = _auth(db).issue_password_reset(
        identifier=phone,
        request_source="127.0.0.6",
        verification=verification,
    )
    before_hash = account.password_hash
    account.status = "disabled"
    db.commit()

    with pytest.raises(VerificationTargetMismatch):
        _auth(db).complete_password_reset(
            challenge_id=challenge.challenge_id,
            code=sender.code_for(challenge.challenge_id),
            new_password=NEW_PASSWORD,
            request_id="disabled-password-reset",
            verification=verification,
        )

    db.expire_all()
    assert db.get(UserAccount, account.id).password_hash == before_hash
    assert (
        db.get(RegistrationVerificationChallenge, challenge.challenge_id).consumed_at
        is None
    )


def test_deleted_person_uses_decoy_password_recovery_without_account_mutation(
    db: Session,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    account, phone, _ = _account(db, sequence=625)
    person = db.get(Person, account.person_id)
    assert person is not None
    person.deleted_at = NOW
    before_hash = account.password_hash
    db.commit()
    auth = _auth(db)
    verification = _verification(db, sender)

    challenge = auth.issue_password_reset(
        identifier=phone,
        request_source="127.0.0.7",
        verification=verification,
    )
    stored_challenge = db.get(
        RegistrationVerificationChallenge,
        challenge.challenge_id,
    )
    assert stored_challenge is not None
    assert stored_challenge.account_id is None

    auth.complete_password_reset(
        challenge_id=challenge.challenge_id,
        code=sender.code_for(challenge.challenge_id),
        new_password=NEW_PASSWORD,
        request_id="deleted-person-password-reset",
        verification=verification,
    )

    db.expire_all()
    assert db.get(UserAccount, account.id).password_hash == before_hash
    assert db.get(
        RegistrationVerificationChallenge,
        challenge.challenge_id,
    ).consumed_at == NOW
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "auth.password_reset"
        )
    ) == 0
