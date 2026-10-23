from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.identity.models import GuardianRelationship, Person
from app.personnel_intake.activation_models import AssistedActivationSession
from app.registration.delivery import CapturingVerificationSender
from app.registration.models import RegistrationVerificationChallenge
from app.registration.start_limiter import RegistrationStartLimiter, RegistrationStartRateLimited
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationInvalidOrExpired,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.services.test_assisted_eligibility import approved_minor
from tests.services.test_personnel_approval import platform_users as _platform_users


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def activation(db, sender=None, clock=None):
    from app.personnel_intake.activation_service import AssistedActivationService

    sender = sender or CapturingVerificationSender(messages=[])
    lookup = LookupDigest(bytes(range(32)))
    verify = RegistrationVerificationService(
        db,
        lookup=lookup,
        verification_pepper=bytes(reversed(range(32))),
        sender=sender,
        app_env="test",
        clock=clock,
    )
    return AssistedActivationService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=lookup,
        verification=verify,
        token_pepper=bytes(range(1, 33)),
        clock=clock,
    ), sender


def _verified_start(db, service, sender, email):
    issued = service.start(email, "127.0.2.71")
    row = db.scalar(
        select(AssistedActivationSession).order_by(AssistedActivationSession.created_at.desc())
    )
    # Correction-invalidation setup only; successful finishes use committed fixtures.
    row.state, row.verified_at = "verified", datetime.now(UTC)
    db.flush()
    db.commit()
    return issued.session_token.get_secret_value(), row, sender.code_for(row.challenge_id)


def assert_activation_preserves_person_and_creates_only_verified_email(
    db, result, data, edges, actor_id, expected_roles
):
    """Shared assertions invoked by real committed minor fixtures, never an OTP stub."""
    from app.rbac.models import Role, UserRole
    from app.team_management.models import TeamMembership
    from tests.services.test_housing_reconciliation import _room, _stay

    stay = _stay(
        db,
        person_id=result.person_id,
        room=_room(db),
        actor_id=actor_id,
    )
    stay_before = tuple(getattr(stay, column.key) for column in stay.__table__.columns)
    # All columns and all dependent rows: catches changed facts and added edges,
    # not merely survival of the original IDs. Core rows avoid identity-map reuse.
    guardian_snapshot_query = (
        select(*GuardianRelationship.__table__.columns)
        .where(GuardianRelationship.student_person_id == result.person_id)
        .order_by(GuardianRelationship.id)
    )
    guardian_snapshot_before = list(db.execute(guardian_snapshot_query))
    assert guardian_snapshot_before
    before = (
        db.scalar(select(func.count()).select_from(Person)),
        [edge.id for edge in edges],
        list(
            db.scalars(
                select(TeamMembership.id).where(TeamMembership.person_id == result.person_id)
            )
        ),
    )
    service, sender = activation(db)
    token = service.start(
        data.email.get_secret_value(), "127.0.2.71"
    ).session_token.get_secret_value()
    row = db.scalar(
        select(AssistedActivationSession).where(
            AssistedActivationSession.person_id == result.person_id
        )
    )
    code = sender.code_for(row.challenge_id)
    service.verify(token, SecretStr(code))
    assert service.finish(token, SecretStr(code), SecretStr("SecurePass1")).completed
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    assert (account.account_access_origin, account.assisted_approval_id, account.status) == (
        "assisted_activation",
        result.approval_id,
        "active",
    )
    assert account.login_phone_digest is None
    identifiers = list(
        db.scalars(
            select(AccountLoginIdentifier).where(AccountLoginIdentifier.account_id == account.id)
        )
    )
    assert len(identifiers) == 1 and identifiers[0].kind == "email" and identifiers[0].verified_at
    assert (
        set(db.scalars(select(Role.key).join(UserRole).where(UserRole.user_id == account.id)))
        == expected_roles
    )
    assert before == (
        db.scalar(select(func.count()).select_from(Person)),
        list(
            db.scalars(
                select(GuardianRelationship.id).where(GuardianRelationship.id.in_(before[1]))
            )
        ),
        list(
            db.scalars(
                select(TeamMembership.id).where(TeamMembership.person_id == result.person_id)
            )
        ),
    )
    assert (
        db.scalar(
            select(func.count()).select_from(AuthSession).where(AuthSession.user_id == account.id)
        )
        == 0
    )
    assert row.state == "completed"
    assert list(db.execute(guardian_snapshot_query)) == guardian_snapshot_before
    db.refresh(stay)
    assert tuple(getattr(stay, column.key) for column in stay.__table__.columns) == stay_before
    assert service.finish(token, SecretStr(code), SecretStr("SecurePass1")).completed
    assert (
        db.scalar(
            select(func.count())
            .select_from(UserAccount)
            .where(UserAccount.person_id == result.person_id)
        )
        == 1
    )
    from tests.services.test_account_access_readiness import services

    _, _, auth = services(db)
    logged_in = auth.login(
        identifier=data.email.get_secret_value(),
        password="SecurePass1",
        ip_address="127.0.9.71",
        request_id="activation-minor-login",
    )
    assert logged_in.context.account.id == account.id
    assert (
        auth.authenticate(logged_in.session_token, request_id="activation-minor-auth").person.id
        == result.person_id
    )


def test_unknown_and_known_starts_have_same_public_response_and_token_length(db, platform_users):
    _, data, _ = approved_minor(db, platform_users)
    now = datetime.now(UTC)
    service, sender = activation(db, clock=lambda: now)
    known = service.start(data.email.get_secret_value(), "127.0.3.71")
    unknown = service.start("unknown-activation@example.com", "127.0.3.72")
    assert known.public == unknown.public
    assert len(known.session_token.get_secret_value()) == len(
        unknown.session_token.get_secret_value()
    )
    assert len(sender.messages) == 1
    with pytest.raises(VerificationInvalidOrExpired):
        service.verify(unknown.session_token.get_secret_value(), SecretStr("123456"))


def test_private_smtp_failure_returns_accepted_without_live_proof(db, platform_users):
    import smtplib

    from app.audit.models import AuditEvent

    _, data, _ = approved_minor(db, platform_users)

    class FailedSender:
        def send_code(self, **kwargs):
            raise smtplib.SMTPException("synthetic provider failure")

    service, _ = activation(db, FailedSender())
    issued = service.start(data.email.get_secret_value(), "127.0.4.71")
    assert issued.public.accepted
    assert db.scalar(select(func.count()).select_from(AssistedActivationSession)) == 0
    failed_challenges = list(
        db.scalars(
            select(RegistrationVerificationChallenge).where(
                RegistrationVerificationChallenge.purpose == "assisted_activation"
            )
        )
    )
    assert len(failed_challenges) == 1 and failed_challenges[0].consumed_at is not None
    events = list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.action == "profile_activation.delivery_failed")
        )
    )
    assert len(events) == 1 and events[0].outcome == "failed"
    assert data.email.get_secret_value() not in str(events[0].event_metadata)


def test_correction_revokes_old_email_proof_before_finish(db, platform_users):
    from tests.services.test_assisted_eligibility import correction

    result, data, _ = approved_minor(db, platform_users)
    service, sender = activation(db)
    token, row, code = _verified_start(db, service, sender, data.email.get_secret_value())
    correction(db, platform_users, result.person_id, data, email="corrected-activation@example.com")
    with pytest.raises(VerificationInvalidOrExpired):
        service.finish(token, SecretStr(code), SecretStr("SecurePass1"))
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )
    assert db.get(RegistrationVerificationChallenge, row.challenge_id).consumed_at is not None


@pytest.mark.parametrize(
    "status,locked", [("active", False), ("disabled", False), ("active", True)]
)
def test_start_never_changes_existing_account_or_sends_mail(db, platform_users, status, locked):
    from tests.services.test_account_access_readiness import assisted_account

    result, data, _ = approved_minor(db, platform_users)
    account = assisted_account(db, result, status=status)
    account.locked_until = datetime.now(UTC) + timedelta(hours=1) if locked else None
    before = (
        account.status,
        account.locked_until,
        account.password_hash,
        account.account_access_origin,
    )
    db.flush()
    service, sender = activation(db)
    assert service.start(data.email.get_secret_value(), "127.0.7.71").public.accepted
    assert not sender.messages
    assert before == (
        account.status,
        account.locked_until,
        account.password_hash,
        account.account_access_origin,
    )


def test_activation_otp_shares_send_budget_with_registration_and_recovery(db):
    from app.registration.verification_service import VerificationRateLimited

    now = datetime.now(UTC)
    sender = CapturingVerificationSender(messages=[])
    verify = RegistrationVerificationService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        verification_pepper=bytes(reversed(range(32))),
        sender=sender,
        app_env="test",
        clock=lambda: now,
    )
    for purpose in (
        "registration",
        "password_reset",
        "assisted_activation",
        "registration",
        "assisted_activation",
    ):
        verify.issue(
            identifier_kind="email",
            identifier="shared-activation@example.com",
            purpose=purpose,
            request_source="127.0.8.71",
        )
        now += timedelta(seconds=60)
    with pytest.raises(VerificationRateLimited):
        verify.issue(
            identifier_kind="email",
            identifier="shared-activation@example.com",
            purpose="password_reset",
            request_source="127.0.8.71",
        )
    assert len(sender.messages) == 5


def test_exhausted_send_budget_is_private_and_issues_no_decoy_mail(db, platform_users):
    _, data, _ = approved_minor(db, platform_users)
    now = datetime.now(UTC)
    service, sender = activation(db, clock=lambda: now)
    for _ in range(5):
        service.verification.issue(
            identifier_kind="email",
            identifier=data.email.get_secret_value(),
            purpose="password_reset",
            request_source="127.0.10.71",
        )
        now += timedelta(seconds=60)
    known = service.start(data.email.get_secret_value(), "127.0.10.72")
    unknown = service.start("unknown-budget@example.com", "127.0.10.73")
    assert known.public == unknown.public
    assert len(sender.messages) == 5
    assert db.scalar(select(func.count()).select_from(AssistedActivationSession)) == 0


def test_corrupt_evidence_is_private_at_start(db, platform_users):
    _, data, _ = approved_minor(db, platform_users)
    now = datetime.now(UTC)
    service, sender = activation(db, clock=lambda: now)
    service.cipher = EnvelopeCipher(b"q" * 32)
    known = service.start(data.email.get_secret_value(), "127.0.11.71")
    unknown = service.start("unknown-corruption@example.com", "127.0.11.72")
    assert known.public == unknown.public and not sender.messages


def test_activation_entry_cooldown_applies_without_any_profile_lookup(db):
    now = datetime(2026, 10, 18, 10, tzinfo=UTC)
    limiter = RegistrationStartLimiter(db, pepper=bytes(range(32)), clock=lambda: now)
    limiter.record_activation(identifier_digest="a" * 64, request_source="127.0.0.71")
    now += timedelta(seconds=59)
    with pytest.raises(RegistrationStartRateLimited):
        limiter.record_activation(identifier_digest="a" * 64, request_source="127.0.0.72")
    now += timedelta(seconds=1)
    limiter.record_activation(identifier_digest="a" * 64, request_source="127.0.0.73")


def test_activation_and_registration_share_entry_identifier_budget(db):
    now = datetime(2026, 10, 18, 10, tzinfo=UTC)
    limiter = RegistrationStartLimiter(db, pepper=bytes(range(32)), clock=lambda: now)
    for index in range(4):
        limiter.record(identifier_digest="b" * 64, request_source=f"127.0.1.{index}")
    limiter.record_activation(identifier_digest="b" * 64, request_source="127.0.1.71")
    now += timedelta(seconds=60)
    with pytest.raises(RegistrationStartRateLimited):
        limiter.record_activation(identifier_digest="b" * 64, request_source="127.0.1.72")
