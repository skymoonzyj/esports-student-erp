from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.identity.models import Person
from app.personnel_intake.activation_models import AssistedActivationSession
from app.personnel_intake.claims.models import ProfileClaim, ProfileClaimDecision
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from app.rbac.models import Role, UserRole
from app.registration.verification_service import VerificationInvalidOrExpired
from tests.services.test_assisted_activation import activation
from tests.services.test_personnel_intake import actor
from tests.services.test_profile_claim_review import decision, pending, reviews
from tests.services.test_profile_claims import claim_db as _claim_db


@pytest.fixture
def claim_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


def approved(db):
    target, users, claim, now = pending(db)
    operation = decision()
    reviews(db).approve(actor(users, "admin"), claim.id, operation)
    return target, users, claim, now, operation


@pytest.mark.parametrize("reserved", [None, "claim@example.com"])
def test_pending_claim_revoke_preserves_original_reserved_email_cipher_and_existing_person_stay(
    claim_db, reserved
):
    from app.housing.models import HousingStay
    from app.personnel_intake.claims.models import ProfileClaimBinding
    from app.personnel_intake.claims.schemas import ClaimReasonInput
    from tests.services.test_housing_reconciliation import _room, _stay
    from tests.services.test_profile_claims import (
        claims,
        incomplete_pending_target,
        pending_claim_input,
        verify_new,
    )

    target, users = incomplete_pending_target(claim_db, email=reserved, sex="female")
    person = claim_db.get(Person, target.person_id)
    person.nickname, person.profile_notes = "原有昵称", "原有备注"
    original_email = (person.email_ciphertext, person.email_digest)
    stay = _stay(
        claim_db, person_id=person.id, room=_room(claim_db), actor_id=users["accounts"]["admin"].id
    )
    claim_db.commit()
    public, sender = claims(claim_db)
    verify_new(public, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    service, who = reviews(claim_db), actor(users, "admin")
    approved = service.approve(who, claim.id, decision())
    claim_db.refresh(person)
    if reserved:
        assert (person.email_ciphertext, person.email_digest) == original_email
    before_person = {
        c.key: getattr(person, c.key)
        for c in Person.__table__.columns
        if c.key not in {"email_ciphertext", "email_digest", "updated_at"}
    }
    before_stay = tuple(getattr(stay, c.key) for c in HousingStay.__table__.columns)
    bound_email = (person.email_ciphertext, person.email_digest)
    binding = claim_db.scalar(
        select(ProfileClaimBinding).where(ProfileClaimBinding.claim_id == claim.id)
    )
    assert binding.email_binding_origin == ("preserved" if reserved else "introduced")
    result = service.revoke(
        who,
        claim.id,
        ClaimReasonInput(
            expected_version=approved.version,
            idempotency_key=uuid4(),
            reason="撤销本次验证，不改写原始联系事实",
        ),
    )
    claim_db.refresh(person)
    claim_db.refresh(stay)
    claim_db.refresh(binding)
    assert result.status == "revoked" and binding.revoked_at is not None
    assert {
        c.key: getattr(person, c.key)
        for c in Person.__table__.columns
        if c.key not in {"email_ciphertext", "email_digest", "updated_at"}
    } == before_person
    assert tuple(getattr(stay, c.key) for c in HousingStay.__table__.columns) == before_stay
    assert (person.email_ciphertext, person.email_digest) == (
        bound_email if reserved else (None, None)
    )


def test_preserved_pending_email_requires_new_reviewed_binding_after_revocation(claim_db):
    from app.personnel_intake.claims.binding import require_activation_binding_in_transaction
    from app.personnel_intake.claims.errors import ProfileClaimError
    from app.personnel_intake.claims.models import ProfileClaimBinding
    from app.personnel_intake.claims.schemas import ClaimReasonInput
    from tests.services.test_profile_claims import (
        claims,
        incomplete_pending_target,
        pending_claim_input,
        verify_new,
    )

    target, users = incomplete_pending_target(claim_db, email="claim@example.com")
    now = [datetime.now(UTC)]
    public, sender = claims(claim_db, clock=lambda: now[0])
    original_token, _ = verify_new(public, sender, pending_claim_input())
    first = claim_db.scalar(select(ProfileClaim))
    who, service = actor(users, "admin"), reviews(claim_db, clock=lambda: now[0])
    approved = service.approve(who, first.id, decision())
    now[0] = datetime.now(UTC) + timedelta(seconds=1)
    service.revoke(
        who,
        first.id,
        ClaimReasonInput(
            expected_version=approved.version, idempotency_key=uuid4(), reason="撤销证明保留原邮箱"
        ),
    )
    assert public.status(original_token).status == "closed"
    email_digest = public.lookup.digest_domain(
        "registration-verification-email", "claim@example.com"
    )
    with pytest.raises(ProfileClaimError):
        require_activation_binding_in_transaction(
            claim_db,
            person_id=target.person_id,
            email_digest=email_digest,
            cipher=public.cipher,
            lookup=public.lookup,
        )
    now[0] += timedelta(seconds=61)
    _, status = verify_new(public, sender, pending_claim_input())
    assert status.status == "waiting_review"
    second = claim_db.scalar(select(ProfileClaim).where(ProfileClaim.id != first.id))
    assert second.id != first.id and first.status == "revoked"
    service.approve(who, second.id, decision())
    binding = claim_db.scalar(
        select(ProfileClaimBinding).where(ProfileClaimBinding.revoked_at.is_(None))
    )
    assert binding.claim_id == second.id and binding.email_binding_origin == "preserved"
    assert public.status(original_token).status == "closed"


def verified_activation(db, *, person_id, now=None):
    now = now or datetime.now(UTC) + timedelta(seconds=61)
    service, sender = activation(db, clock=lambda: now)
    token = service.start("claim@example.com", "127.0.90.1").session_token.get_secret_value()
    row = db.scalar(
        select(AssistedActivationSession)
        .where(AssistedActivationSession.person_id == person_id)
        .order_by(AssistedActivationSession.created_at.desc())
    )
    assert row is not None
    code = SecretStr(sender.code_for(row.challenge_id))
    service.verify(token, code)
    return service, token, code, row


def test_pending_or_approved_claim_without_fresh_activation_code_cannot_open_account(claim_db):
    target, users, claim, now = pending(claim_db)
    original_person_id = target.person_id
    service, sender = activation(claim_db, clock=lambda: now[0] + timedelta(seconds=61))
    service.start("claim@example.com", "127.0.90.2")
    assert not sender.messages
    reviews(claim_db).approve(actor(users, "admin"), claim.id, decision())
    service, token, code, session = verified_activation(
        claim_db, person_id=target.person_id, now=now[0] + timedelta(seconds=122)
    )
    # The first claim OTP and a registration-purpose challenge cannot prove
    # this new activation target. Guess accounting remains real/committed.
    with pytest.raises(VerificationInvalidOrExpired):
        service.finish(
            token,
            SecretStr("999999" if code.get_secret_value() != "999999" else "888888"),
            SecretStr("SecurePass1"),
        )
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == original_person_id))
        is None
    )
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    claim_db.refresh(claim)
    account = claim_db.scalar(
        select(UserAccount).where(UserAccount.person_id == original_person_id)
    )
    assert account.person_id == original_person_id
    assert claim.status == "completed" and claim.version == 3
    assert claim.completed_account_id == account.id and claim.completed_at == session.completed_at
    identifiers = list(
        claim_db.scalars(
            select(AccountLoginIdentifier).where(AccountLoginIdentifier.account_id == account.id)
        )
    )
    assert len(identifiers) == 1 and identifiers[0].kind == "email" and identifiers[0].verified_at
    assert set(
        claim_db.scalars(select(Role.key).join(UserRole).where(UserRole.user_id == account.id))
    ) == {"student"}
    assert (
        claim_db.scalar(
            select(func.count()).select_from(AuthSession).where(AuthSession.user_id == account.id)
        )
        == 0
    )
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    claim_db.refresh(claim)
    assert claim.version == 3


def test_claim_requirement_cannot_be_bypassed_by_setting_current_email(claim_db):
    from tests.services.test_personnel_approval import approvals, platform_users
    from tests.services.test_personnel_intake import intake

    users = platform_users.__wrapped__(claim_db)
    target = approvals(claim_db).direct_create(
        actor(users, "admin"), intake(users, email="claim@example.com"), uuid4()
    )
    profile = claim_db.get(AssistedPersonProfile, target.person_id)
    profile.activation_requirement = "reviewed_claim"
    claim_db.commit()
    service, sender = activation(claim_db)
    service.start("claim@example.com", "127.0.90.3")
    assert not sender.messages
    assert (
        claim_db.scalar(
            select(AssistedActivationSession.id).where(
                AssistedActivationSession.person_id == target.person_id
            )
        )
        is None
    )


def registration_application(db, person_id, *, email="other@example.com"):
    from app.registration.consents import registration_terms_digest
    from app.registration.models import RegistrationApplication, RegistrationConsentAcceptance
    from app.security.passwords import PasswordService

    service, _ = activation(db)
    person = db.get(Person, person_id)
    application = RegistrationApplication(
        primary_purpose="student",
        state="ready_to_finalize",
        verified_identifier_kind="email",
        verified_identifier_digest=service.lookup.digest_domain(
            "registration-verification-email", email
        ),
        verified_identifier_ciphertext=service.cipher.encrypt(
            email, field_name="registration_identifier"
        ),
        password_hash=PasswordService().hash("SecurePass1"),
        full_name=person.full_name,
        identity_ciphertext=service.cipher.encrypt(
            service.cipher.decrypt(person.identity_ciphertext, field_name="identity_number"),
            field_name="registration_identity",
        ),
        identity_digest=person.identity_digest,
        sex=person.sex,
        date_of_birth=person.date_of_birth,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db.add(application)
    db.flush()
    db.add(
        RegistrationConsentAcceptance(
            registration_application_id=application.id,
            consent_type="registration_terms",
            policy_version="2026-08",
            content_digest=registration_terms_digest("2026-08"),
            accepted_at=datetime.now(UTC),
            request_id=str(uuid4()),
            source="self_registration",
        )
    )
    db.commit()
    return application.id


def test_ordinary_registration_cannot_claim_deferred_profile(claim_db):
    from app.registration.finalization_service import (
        RegistrationFinalizationError,
        RegistrationFinalizationService,
    )

    target, _, _, _ = pending(claim_db)
    application_id = registration_application(claim_db, target.person_id)
    service, _ = activation(claim_db)
    before = claim_db.scalar(select(func.count()).select_from(Person))
    with pytest.raises(RegistrationFinalizationError):
        RegistrationFinalizationService(
            claim_db, cipher=service.cipher, lookup=service.lookup
        ).finalize(application_id, request_id=str(uuid4()))
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )
    assert claim_db.scalar(select(func.count()).select_from(Person)) == before


def test_admin_cannot_open_reviewed_profile_as_legacy(claim_db):
    from app.auth.schemas import AccountCreate
    from app.auth.service import AccountService, PersonNotReady
    from app.identity.service import IdentityService
    from app.security.passwords import PasswordService

    target, _, _, _, _ = approved(claim_db)
    service, _ = activation(claim_db)
    with pytest.raises(PersonNotReady):
        AccountService(
            claim_db,
            identity_service=IdentityService(
                claim_db, cipher=service.cipher, lookup=service.lookup
            ),
            passwords=PasswordService(),
        ).create_account(AccountCreate(person_id=target.person_id))
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )


def test_approve_activate_then_old_approve_replay_returns_current_completed_without_effects(
    claim_db,
):
    target, users, claim, _, operation = approved(claim_db)
    service, token, code, _ = verified_activation(claim_db, person_id=target.person_id)
    service.finish(token, code, SecretStr("SecurePass1"))
    counts = lambda: tuple(
        claim_db.scalar(select(func.count()).select_from(model))
        for model in (UserAccount, UserRole, AssistedApproval, ProfileClaimDecision, AuditEvent)
    )
    before = counts()
    result = reviews(claim_db).approve(actor(users, "admin"), claim.id, operation)
    assert result.status == "completed" and result.version == 3
    assert before == counts()


def test_untrusted_profile_change_invalidates_pending_and_old_activation(claim_db):
    from app.identity.schemas import PersonUpdate
    from app.identity.service import IdentityService

    target, users, claim, _ = pending(claim_db)
    service, _ = activation(claim_db)
    IdentityService(claim_db, cipher=service.cipher, lookup=service.lookup).update_person(
        target.person_id,
        PersonUpdate(full_name="普通更正新姓名"),
        actor_user_id=users["accounts"]["admin"].id,
        request_id=str(uuid4()),
    )
    claim_db.refresh(claim)
    assert claim.status == "invalidated" and claim.version == 2


def test_ordinary_correction_cannot_change_reviewed_binding_email(claim_db):
    from app.identity.schemas import PersonUpdate
    from app.identity.service import IdentityService
    from app.personnel_intake.errors import CredentialReservationConflict

    target, users, _, _, _ = approved(claim_db)
    service, _ = activation(claim_db)
    with pytest.raises(CredentialReservationConflict):
        IdentityService(claim_db, cipher=service.cipher, lookup=service.lookup).update_person(
            target.person_id,
            PersonUpdate(email="changed@example.com"),
            actor_user_id=users["accounts"]["admin"].id,
            request_id=str(uuid4()),
        )


def test_approved_binding_routes_fact_edits_through_trusted_correction_but_allows_notes(claim_db):
    from app.identity.schemas import PersonUpdate
    from app.identity.service import IdentityService
    from app.personnel_intake.errors import CredentialReservationConflict

    target, users, claim, _, _ = approved(claim_db)
    service, token, code, row = verified_activation(claim_db, person_id=target.person_id)
    identity = IdentityService(claim_db, cipher=service.cipher, lookup=service.lookup)
    person = claim_db.get(Person, target.person_id)
    original = person.full_name
    with pytest.raises(CredentialReservationConflict):
        identity.update_person(
            target.person_id,
            PersonUpdate(full_name="普通更正不能绕过"),
            actor_user_id=users["accounts"]["admin"].id,
            request_id=str(uuid4()),
        )
    claim_db.refresh(person)
    claim_db.refresh(row)
    assert person.full_name == original and row.state == "verified"
    assert row.expires_at > service._now()
    identity.update_person(
        target.person_id,
        PersonUpdate(nickname="允许昵称", profile_notes="允许备注"),
        actor_user_id=users["accounts"]["admin"].id,
        request_id=str(uuid4()),
    )
    claim_db.refresh(claim)
    assert claim.status == "approved" and claim.version == 2
    assert service.finish(token, code, SecretStr("SecurePass1")).completed


@pytest.mark.parametrize("status", ["active", "disabled"])
def test_reviewed_claim_never_opens_second_account_in_any_status(claim_db, status):
    target, _, _, _, _ = approved(claim_db)
    service, token, code, _ = verified_activation(claim_db, person_id=target.person_id)
    service.finish(token, code, SecretStr("SecurePass1"))
    account = claim_db.scalar(select(UserAccount).where(UserAccount.person_id == target.person_id))
    account.status = status
    claim_db.commit()
    service, sender = activation(claim_db, clock=lambda: datetime.now(UTC) + timedelta(seconds=183))
    service.start("claim@example.com", "127.0.90.8")
    assert not sender.messages
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(UserAccount)
            .where(UserAccount.person_id == target.person_id)
        )
        == 1
    )


def test_claim_initial_registration_and_expired_activation_codes_are_not_fresh_proof(
    claim_db, monkeypatch
):
    from tests.services.test_profile_claims import approved_target, claims, verify_new

    target, users = approved_target(claim_db)
    # Only the generator input is deterministic. Persisted challenges, hashes,
    # delivery capture, target/purpose checks and OTP verification remain real.
    code_inputs = iter(range(10_000, 40_000, 10_000))
    monkeypatch.setattr(
        "app.registration.verification_service.secrets.randbelow", lambda _upper: next(code_inputs)
    )
    now = [datetime.now(UTC)]
    public, sender = claims(claim_db, clock=lambda: now[0])
    verify_new(public, sender)
    initial_code = SecretStr(sender.messages[-1].code)
    claim = claim_db.scalar(select(ProfileClaim))
    reviews(claim_db).approve(actor(users, "admin"), claim.id, decision())
    now[0] += timedelta(seconds=61)
    service, token, good_code, row = verified_activation(
        claim_db, person_id=target.person_id, now=now[0]
    )
    with pytest.raises(VerificationInvalidOrExpired):
        service.finish(token, initial_code, SecretStr("SecurePass1"))
    registration = service.verification.issue(
        identifier_kind="email",
        identifier="other-proof@example.com",
        purpose="registration",
        request_source="127.0.90.7",
    )
    registration_code = SecretStr(service.verification.sender.code_for(registration.challenge_id))
    with pytest.raises(VerificationInvalidOrExpired):
        service.verify(token, registration_code)
    expired, _ = activation(claim_db, clock=lambda: row.expires_at + timedelta(seconds=1))
    with pytest.raises(VerificationInvalidOrExpired):
        expired.finish(token, good_code, SecretStr("SecurePass1"))
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )


def test_binding_uses_same_email_trusted_approval_lineage(claim_db):
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import intake

    target, users, claim, _, _ = approved(claim_db)
    service, token, code, _ = verified_activation(claim_db, person_id=target.person_id)
    data = intake(users, email="claim@example.com")
    payload = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields
    }
    approvals(claim_db).correct_profile(
        actor(users, "admin"),
        target.person_id,
        AssistedProfileCorrection(
            **dict(payload, full_name="可信更正后姓名"),
            expected_version=2,
            idempotency_key=uuid4(),
            reason="同邮箱更正",
        ),
    )
    with pytest.raises(VerificationInvalidOrExpired):
        service.finish(token, code, SecretStr("SecurePass1"))
    service, token, code, _ = verified_activation(
        claim_db, person_id=target.person_id, now=datetime.now(UTC) + timedelta(seconds=183)
    )
    service.finish(token, code, SecretStr("SecurePass1"))
    claim_db.refresh(claim)
    assert claim.status == "completed"


def test_trusted_correction_invalidates_competing_pending_evidence(claim_db):
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import intake

    target, users, claim, _ = pending(claim_db)
    data = intake(users, email=None)
    payload = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields
    }
    approvals(claim_db).correct_profile(
        actor(users, "admin"),
        target.person_id,
        AssistedProfileCorrection(
            **dict(payload, full_name="可信更正新姓名"),
            expected_version=1,
            idempotency_key=uuid4(),
            reason="使原申请证据失效",
        ),
    )
    claim_db.refresh(claim)
    assert claim.status == "invalidated" and claim.version == 2


def test_identifier_addition_cannot_take_approved_formal_claim_email(claim_db):
    from app.auth.service import AccountIdentifierUnavailable
    from tests.services.test_account_access_readiness import services

    _, users, _, _, _ = approved(claim_db)
    _, _, auth = services(claim_db)
    auth.clock = lambda: datetime.now(UTC) + timedelta(seconds=61)
    service, _ = activation(claim_db, clock=auth.clock)
    who = actor(users, "admin")
    challenge = auth.issue_identifier_addition(
        who,
        identifier_kind="email",
        identifier="claim@example.com",
        current_password="AdminApi2026Aa",
        request_source="127.0.90.9",
        verification=service.verification,
    )
    code = service.verification.sender.code_for(challenge.challenge_id)
    with pytest.raises(AccountIdentifierUnavailable):
        auth.verify_identifier_addition(
            who,
            challenge_id=challenge.challenge_id,
            code=code,
            identifier_kind="email",
            identifier="claim@example.com",
            current_password="AdminApi2026Aa",
            request_id=str(uuid4()),
            verification=service.verification,
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "revoked_binding",
        "revoked_claim",
        "changed_formal_email",
        "untrusted_current",
    ],
)
def test_activation_rejects_changed_or_untrusted_binding_evidence(claim_db, tamper):
    from app.personnel_intake.claims.models import ProfileClaimBinding

    target, _, claim, _, _ = approved(claim_db)
    service, token, code, _ = verified_activation(claim_db, person_id=target.person_id)
    binding = claim_db.scalar(
        select(ProfileClaimBinding).where(ProfileClaimBinding.claim_id == claim.id)
    )
    if tamper == "revoked_binding":
        binding.revoked_at = datetime.now(UTC)
    elif tamper == "revoked_claim":
        claim.status = "revoked"
    elif tamper == "changed_formal_email":
        person = claim_db.get(Person, target.person_id)
        person.email_ciphertext = service.cipher.encrypt("changed@example.com", field_name="email")
        person.email_digest = service.lookup.digest_domain("person-email", "changed@example.com")
    elif tamper == "untrusted_current":
        claim_db.get(
            AssistedPersonProfile, target.person_id
        ).current_approval_id = target.approval_id
    claim_db.commit()
    with pytest.raises(Exception) as caught:
        service.finish(token, code, SecretStr("SecurePass1"))
    assert (
        isinstance(caught.value, VerificationInvalidOrExpired)
        or getattr(caught.value, "status", None) == 409
    )
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )


def shared_email_history_world(db):
    """A's rejected E links discovery to B's later valid pending E, not authority."""
    from app.personnel_intake.claims.schemas import ClaimReasonInput
    from tests.api.conftest import make_cn_id
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import intake
    from tests.services.test_profile_claims import claim_input, claims, verify_new

    target, users, _, _, _ = approved(db)
    activation_service, token, code, _ = verified_activation(db, person_id=target.person_id)
    activation_service.finish(token, code, SecretStr("SecurePass1"))
    now = [datetime.now(UTC) + timedelta(seconds=183)]
    public, sender = claims(db, clock=lambda: now[0])
    verify_new(public, sender, claim_input(email="history-shared@example.com"))
    history = db.scalar(
        select(ProfileClaim).where(
            ProfileClaim.email_digest
            == public.lookup.digest_domain(
                "registration-verification-email", "history-shared@example.com"
            )
        )
    )
    reviews(db, clock=lambda: now[0]).reject(
        actor(users, "admin"),
        history.id,
        ClaimReasonInput(expected_version=1, idempotency_key=uuid4(), reason="本人重新选择邮箱"),
    )
    second = approvals(db).direct_create(
        actor(users, "admin"),
        intake(
            users,
            full_name="另一个建档人员",
            identity_number=make_cn_id("20000102", 323),
            phone="13900000901",
            email=None,
        ),
        uuid4(),
    )
    db.commit()
    now[0] += timedelta(seconds=61)
    verify_new(
        public,
        sender,
        claim_input(
            full_name="另一个建档人员",
            identity_number=make_cn_id("20000102", 323),
            email="history-shared@example.com",
        ),
    )
    other = db.scalar(select(ProfileClaim).where(ProfileClaim.target_person_id == second.person_id))
    verify_new(public, sender, claim_input(email="own-pending@example.com"))
    own = db.scalar(
        select(ProfileClaim).where(
            ProfileClaim.email_digest
            == public.lookup.digest_domain(
                "registration-verification-email", "own-pending@example.com"
            )
        )
    )
    return target, users, history, other, own, now


@pytest.mark.parametrize("entry", ["identity", "trusted_correction", "identifier"])
def test_contact_history_does_not_invalidate_other_person_pending_claim(
    claim_db, entry, monkeypatch
):
    from app.auth.service import AuthContext
    from app.identity.schemas import PersonUpdate
    from app.identity.service import IdentityService
    from app.personnel_intake.models import PersonnelCredentialReservation
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from tests.services.test_account_access_readiness import services
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import intake

    target, users, history, other, own, now = shared_email_history_world(claim_db)

    class MutationClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0] if tz is not None else now[0].replace(tzinfo=None)

    # Public proof clocks advance over real resend windows; mutation writers
    # must share that clock so a reservation is never ended before its creation.
    monkeypatch.setattr("app.identity.service.datetime", MutationClock)
    monkeypatch.setattr("app.personnel_intake.approval_service.datetime", MutationClock)
    service, _ = activation(claim_db, clock=lambda: now[0])
    if entry == "identity":
        IdentityService(claim_db, cipher=service.cipher, lookup=service.lookup).update_person(
            target.person_id,
            PersonUpdate(full_name="正常资料更正姓名"),
            actor_user_id=users["accounts"]["admin"].id,
            request_id=str(uuid4()),
        )
    elif entry == "trusted_correction":
        data = intake(users, email="claim@example.com")
        payload = {
            key: getattr(data, key)
            for key in AssistedProfileCorrection.model_fields
            if key in type(data).model_fields
        }
        approvals(claim_db).correct_profile(
            actor(users, "admin"),
            target.person_id,
            AssistedProfileCorrection(
                **dict(payload, full_name="可信资料更正姓名"),
                expected_version=2,
                idempotency_key=uuid4(),
                reason="本人资料更正",
            ),
        )
    else:
        _, _, auth = services(claim_db)
        auth.clock = lambda: now[0]
        account = claim_db.scalar(
            select(UserAccount).where(UserAccount.person_id == target.person_id)
        )
        who = AuthContext(
            account=account, person=claim_db.get(Person, target.person_id), session=None
        )
        challenge = auth.issue_identifier_addition(
            who,
            identifier_kind="phone",
            identifier="13900000902",
            current_password="SecurePass1",
            request_source="127.0.92.1",
            verification=service.verification,
        )
        auth.verify_identifier_addition(
            who,
            challenge_id=challenge.challenge_id,
            code=service.verification.sender.code_for(challenge.challenge_id),
            identifier_kind="phone",
            identifier="13900000902",
            current_password="SecurePass1",
            request_id=str(uuid4()),
            verification=service.verification,
        )
    for row in (history, other, own):
        claim_db.refresh(row)
    assert (history.status, history.version) == ("rejected", 2)
    assert (own.status, own.version) == ("invalidated", 2)
    assert (other.status, other.version) == ("pending", 1)
    assert (
        claim_db.scalar(
            select(PersonnelCredentialReservation.id).where(
                PersonnelCredentialReservation.profile_claim_id == other.id,
                PersonnelCredentialReservation.ended_at.is_(None),
            )
        )
        is not None
    )
    assert (
        claim_db.scalar(
            select(PersonnelCredentialReservation.id).where(
                PersonnelCredentialReservation.profile_claim_id == own.id,
                PersonnelCredentialReservation.ended_at.is_(None),
            )
        )
        is None
    )
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.resource_id == other.id, AuditEvent.action == "profile_claim.invalidated"
            )
        )
        == 0
    )


@pytest.mark.parametrize("status", ["active", "disabled"])
def test_activated_reviewed_source_unverified_shared_contact_keeps_original_identifier_policy(
    claim_db, status
):
    from app.auth.service import AccountIdentifierUnavailable
    from app.identity.schemas import PersonUpdate
    from app.identity.service import IdentityService
    from tests.services.test_account_access_readiness import services

    target, users, _, _, _ = approved(claim_db)
    service, token, code, _ = verified_activation(claim_db, person_id=target.person_id)
    service.finish(token, code, SecretStr("SecurePass1"))
    IdentityService(claim_db, cipher=service.cipher, lookup=service.lookup).update_person(
        target.person_id,
        PersonUpdate(email="shared-unverified@example.com"),
        actor_user_id=users["accounts"]["admin"].id,
        request_id=str(uuid4()),
    )
    account = claim_db.scalar(select(UserAccount).where(UserAccount.person_id == target.person_id))
    assert account.account_access_origin == "assisted_activation"
    # Exercise both persisted account states; this is not an assertion that
    # restoring active bypasses the separate account-readiness/login policy.
    account.status = status
    claim_db.commit()
    _, _, auth = services(claim_db)
    now = [datetime.now(UTC) + timedelta(seconds=183)]
    auth.clock = lambda: now[0]
    verification_service, _ = activation(claim_db, clock=auth.clock)
    who = actor(users, "admin")
    challenge = auth.issue_identifier_addition(
        who,
        identifier_kind="email",
        identifier="shared-unverified@example.com",
        current_password="AdminApi2026Aa",
        request_source="127.0.92.2",
        verification=verification_service.verification,
    )
    auth.verify_identifier_addition(
        who,
        challenge_id=challenge.challenge_id,
        code=verification_service.verification.sender.code_for(challenge.challenge_id),
        identifier_kind="email",
        identifier="shared-unverified@example.com",
        current_password="AdminApi2026Aa",
        request_id=str(uuid4()),
        verification=verification_service.verification,
    )
    now[0] += timedelta(seconds=61)
    other = actor(users, "coach")
    challenge = auth.issue_identifier_addition(
        other,
        identifier_kind="email",
        identifier="shared-unverified@example.com",
        current_password="Coach2026Aa",
        request_source="127.0.92.3",
        verification=verification_service.verification,
    )
    with pytest.raises(AccountIdentifierUnavailable):
        auth.verify_identifier_addition(
            other,
            challenge_id=challenge.challenge_id,
            code=verification_service.verification.sender.code_for(challenge.challenge_id),
            identifier_kind="email",
            identifier="shared-unverified@example.com",
            current_password="Coach2026Aa",
            request_id=str(uuid4()),
            verification=verification_service.verification,
        )
