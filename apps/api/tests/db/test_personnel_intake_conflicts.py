from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountIdentifierUnavailable, AccountService, AuthContext
from app.identity.models import Person
from app.identity.schemas import PersonBaseInput, PersonUpdate
from app.identity.service import IdentityService
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.locks import CredentialReservationPolicy, lock_personnel_credentials
from app.personnel_intake.models import PersonnelCredentialReservation, PersonnelIntakeRequest
from app.registration.delivery import CapturingVerificationSender
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from tests.api.test_registration_profiles import (
    _open_registration_session,
    complete_profile_payload,
)
from tests.api.test_registration_start import (
    send_registration_code,
    start_registration,
    verify_registration,
)
from tests.db.test_account_identifier_concurrency import (
    IDENTIFIER_PASSWORD,
    NOW,
    _auth,
    _verification,
)
from tests.services.test_people import make_cn_id


def claim_reservation(db, identity, *, expired=False, status="pending"):
    from app.personnel_intake.claims.models import ProfileClaim, ProfileClaimSession
    from app.registration.models import RegistrationVerificationChallenge

    now = datetime.now(UTC)
    verified = now - timedelta(days=8) if expired else now
    email = "claim-reserved@example.com"
    digest = identity.lookup.digest_domain("registration-verification-email", email)
    challenge = RegistrationVerificationChallenge(
        purpose="profile_claim",
        identifier_kind="email",
        identifier_digest=digest,
        code_digest="a" * 64,
        request_source_digest="b" * 64,
        expires_at=verified + timedelta(minutes=10),
        last_sent_at=verified,
        consumed_at=verified,
    )
    db.add(challenge)
    db.flush()
    session = ProfileClaimSession(
        token_digest=uuid4().hex * 2,
        challenge_id=challenge.id,
        payload_ciphertext="synthetic",
        payload_digest="c" * 64,
        identity_digest="d" * 64,
        email_digest=digest,
        state="verified",
        created_at=verified,
        expires_at=verified + timedelta(minutes=10),
        verified_at=verified,
    )
    db.add(session)
    db.flush()
    claim = ProfileClaim(
        session_id=session.id,
        challenge_id=challenge.id,
        payload_ciphertext="synthetic",
        payload_digest="c" * 64,
        identity_digest="d" * 64,
        email_digest=digest,
        verified_at=verified,
        challenge_consumed_at=verified,
        expires_at=verified + timedelta(days=7),
        status=status,
    )
    db.add(claim)
    db.flush()
    reservation = PersonnelCredentialReservation(
        kind="email",
        digest=digest,
        profile_claim_id=claim.id,
    )
    db.add(reservation)
    db.flush()
    return email, claim, reservation


def test_expired_claim_email_reservation_is_released_under_lock(db, identity):
    email, claim, reservation = claim_reservation(db, identity, expired=True)
    lock_personnel_credentials(db, emails={email}, identity_digests=set(), lookup=identity.lookup)
    CredentialReservationPolicy(db, lookup=identity.lookup).require_email_available(email=email)
    db.refresh(claim)
    db.refresh(reservation)
    assert claim.status == "expired" and claim.version == 2
    assert reservation.ended_at is not None
    assert claim.payload_ciphertext == "synthetic" and claim.verified_at is not None


def test_person_exemption_cannot_steal_claim_email(db, identity):
    person = identity.create_base_person(person_input(703))
    email, claim, _ = claim_reservation(db, identity)
    policy = CredentialReservationPolicy(db, lookup=identity.lookup)
    with pytest.raises(PersonnelIntakeError):
        policy.require_email_available(email=email, person_id=person.id)
    policy.require_email_available(email=email, profile_claim_id=claim.id)
    with pytest.raises(PersonnelIntakeError):
        policy.require_email_available(email=email, profile_claim_id=uuid4())


def test_nullable_owner_exemptions_preserve_other_owner_conflicts(db, identity):
    email, claim, reservation = claim_reservation(db, identity)
    policy = CredentialReservationPolicy(db, lookup=identity.lookup)
    with pytest.raises(PersonnelIntakeError):
        policy.require_email_available(email=email, request_id=uuid4())
    reservation.ended_at = datetime.now(UTC)
    db.flush()
    request, _ = reserve(db, identity, kind="email", value=email)
    with pytest.raises(PersonnelIntakeError):
        policy.require_email_available(email=email, profile_claim_id=claim.id)
    policy.require_email_available(email=email, request_id=request.id)


def test_anonymous_registration_start_cannot_steal_claim_owner(db, identity, db_client):
    from app.registration.models import RegistrationApplication

    email, claim, reservation = claim_reservation(db, identity)
    application = RegistrationApplication(
        primary_purpose="student",
        state="contact_verified",
        verified_identifier_kind="email",
        verified_identifier_digest=claim.email_digest,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db.add(application)
    db.flush()
    claim_id, reservation_id, application_id = claim.id, reservation.id, application.id
    response = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "email",
            "identifier": email,
        },
    )
    assert response.status_code == 409
    db.expire_all()
    assert db.get(type(claim), claim_id).status == "pending"
    assert db.get(PersonnelCredentialReservation, reservation_id).ended_at is None
    assert db.get(RegistrationApplication, application_id).state == "contact_verified"


@pytest.mark.parametrize("status", ["pending", "approved"])
def test_claim_expiry_boundary_preserves_approved_evidence_and_is_idempotent(db, identity, status):
    from app.audit.models import AuditEvent
    from app.personnel_intake.claims.reservations import expire_claim_reservations_in_transaction

    email, claim, reservation = claim_reservation(db, identity, status=status)
    lock_personnel_credentials(db, emails={email}, identity_digests=set(), lookup=identity.lookup)
    for _ in range(2):
        expire_claim_reservations_in_transaction(
            db, email_digest=claim.email_digest, now=claim.expires_at
        )
    db.refresh(reservation)
    assert claim.status == ("expired" if status == "pending" else "approved")
    assert claim.version == (2 if status == "pending" else 1)
    assert (reservation.ended_at is not None) is (status == "pending")
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.resource_id == claim.id)))
    assert len(events) == (1 if status == "pending" else 0)


@pytest.mark.parametrize(
    "state",
    [
        "draft",
        "contact_verified",
        "identity_completed",
        "pending_guardian",
        "ready_to_finalize",
        "conflict",
        "rejected",
        "expired",
    ],
)
@pytest.mark.parametrize("elapsed", [False, True])
def test_active_registration_email_conflicts_with_claim(db, identity, state, elapsed):
    from app.registration.models import RegistrationApplication

    email = "registration-reserved@example.com"
    application = RegistrationApplication(
        primary_purpose="student",
        state=state,
        verified_identifier_kind="email",
        verified_identifier_digest=identity.lookup.digest_domain(
            "registration-verification-email", email
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=-1 if elapsed else 10),
    )
    db.add(application)
    db.flush()
    policy = CredentialReservationPolicy(db, lookup=identity.lookup)
    if state not in {"rejected", "expired"} and not elapsed:
        with pytest.raises(PersonnelIntakeError):
            policy.require_email_available(email=email)
    else:
        policy.require_email_available(email=email)
    policy.require_email_available(email=email, registration_application_id=application.id)


@pytest.fixture
def identity(db: Session) -> IdentityService:
    return IdentityService(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )


def person_input(sequence: int, *, email: str | None = None) -> PersonBaseInput:
    return PersonBaseInput(
        full_name="隔离占位测试",
        identity_number=make_cn_id("19900102", sequence),
        sex="male" if sequence % 2 else "female",
        date_of_birth=date(1990, 1, 2),
        phone="13800138000",
        email=email,
    )


def reserve(
    db: Session, identity: IdentityService, *, kind: str, value: str, person_id: UUID | None = None
):
    actor = identity.create_base_person(person_input(701, email="reservation-actor@example.com"))
    account = UserAccount(
        person_id=actor.id,
        password_hash="not-a-password",
        status="active",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    request = PersonnelIntakeRequest(
        submitted_by_user_id=account.id, kind="student", person_id=person_id
    )
    db.add(request)
    db.flush()
    digest = (
        identity.lookup.digest(value)
        if kind == "identity"
        else identity.lookup.digest_domain("registration-verification-email", value)
    )
    row = PersonnelCredentialReservation(
        request_id=request.id, person_id=person_id, kind=kind, digest=digest
    )
    db.add(row)
    db.flush()
    return request, row


@pytest.mark.parametrize("kind", ["identity", "email"])
def test_pending_intake_blocks_direct_person_creation(
    db: Session, identity: IdentityService, kind: str
) -> None:
    """Removing reservation validation from _new_person permits a second owner."""
    payload = person_input(703, email="reserved@example.com")
    reserve(
        db,
        identity,
        kind=kind,
        value=payload.identity_number if kind == "identity" else str(payload.email),
    )
    with pytest.raises(PersonnelIntakeError) as error:
        identity.create_base_person(payload)
    assert error.value.status_code == 409
    assert (
        db.scalar(
            select(Person.id).where(
                Person.identity_digest == identity.lookup.digest(payload.identity_number)
            )
        )
        is None
    )


def test_pending_intake_blocks_admin_email_change(db: Session, identity: IdentityService) -> None:
    person = identity.create_base_person(person_input(703, email="before@example.com"))
    reserve(db, identity, kind="email", value="reserved@example.com")
    with pytest.raises(PersonnelIntakeError):
        identity.update_person(
            person.id,
            PersonUpdate(email="reserved@example.com"),
            actor_user_id=None,
            request_id="synthetic",
        )
    assert identity.decrypt_email(db.get(Person, person.id)) == "before@example.com"


def test_pending_intake_blocks_account_missing_contact_fill(
    db: Session, identity: IdentityService
) -> None:
    person = identity.create_base_person(person_input(703))
    reserve(db, identity, kind="email", value="reserved@example.com")
    accounts = AccountService(db, identity_service=identity, passwords=PasswordService())
    with pytest.raises(PersonnelIntakeError):
        accounts.create_account(AccountCreate(person_id=person.id, email="reserved@example.com"))
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person.id)) is None


def test_pending_intake_blocks_registration_start_privately(
    db_client: TestClient, db: Session, identity: IdentityService
) -> None:
    reserve(db, identity, kind="email", value="reserved@example.com")
    response = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "email",
            "identifier": "reserved@example.com",
        },
    )
    assert response.status_code == 409
    assert "reserved@example.com" not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"


def test_same_person_reservation_allows_contact_reuse(
    db: Session, identity: IdentityService
) -> None:
    person = identity.create_base_person(person_input(703, email="same-person@example.com"))
    reserve(db, identity, kind="email", value="same-person@example.com", person_id=person.id)
    identity.update_person(
        person.id, PersonUpdate(full_name="同一档案"), actor_user_id=None, request_id="synthetic"
    )
    assert db.get(Person, person.id).full_name == "同一档案"


def test_shared_phone_does_not_reserve_or_merge_people(
    db: Session, identity: IdentityService
) -> None:
    reserve(db, identity, kind="email", value="reserved@example.com")
    first = identity.create_base_person(person_input(703, email="first@example.com"))
    second = identity.create_base_person(person_input(705, email="second@example.com"))
    assert first.id != second.id
    assert db.get(Person, first.id).phone_digest == db.get(Person, second.id).phone_digest


def test_reservation_created_after_challenge_blocks_verification(
    db_client, captured_sender, db, identity
):
    application_id = start_registration(db_client, identifier="reserved@example.com")
    challenge_id = send_registration_code(db_client, application_id)
    reserve(db, identity, kind="email", value="reserved@example.com")
    response = verify_registration(
        db_client, captured_sender, application_id=application_id, challenge_id=challenge_id
    )
    assert response.status_code == 409


@pytest.mark.parametrize("entry", ["registration", "competing_application", "contact_change"])
def test_registration_reservations_do_not_bypass_otp_attempts(
    db_client,
    captured_sender,
    db,
    identity,
    entry,
):
    from app.registration.models import RegistrationApplication, RegistrationVerificationChallenge
    from tests.api.test_registration_guardians import _open_minor_registration

    email = "otp-reservation@example.com"
    reservation_kind = "email"
    reservation_value = email
    if entry == "contact_change":
        application_id, csrf = _open_minor_registration(
            db_client,
            captured_sender,
            phone="13800008713",
            sequence=713,
        )
        headers = {"X-Registration-CSRF-Token": csrf}
        issue_path = "/api/v1/registration/me/contact-challenges"
        verify_path = "/api/v1/registration/me/contact"
        identifier_body = {"identifier_kind": "email", "identifier": email}
    else:
        if entry == "competing_application":
            existing_id = start_registration(db_client, identifier=email)
            existing = db.get(RegistrationApplication, existing_id)
            existing.state = "contact_verified"
            existing.identity_digest = identity.lookup.digest(person_input(703).identity_number)
            db.flush()
            reservation_kind = "identity"
            reservation_value = person_input(703).identity_number
        application_id = start_registration(db_client, identifier=email)
        headers = {}
        issue_path = f"/api/v1/registration/applications/{application_id}/challenge"
        verify_path = f"/api/v1/registration/applications/{application_id}/verify"
        identifier_body = {}

    issued = db_client.post(issue_path, headers=headers, json=identifier_body)
    assert issued.status_code == 201
    challenge_id = UUID(issued.json()["challenge_id"])
    correct_code = captured_sender.code_for(challenge_id)
    wrong_code = "000000" if correct_code != "000000" else "999999"
    original_digest = db.get(RegistrationApplication, application_id).verified_identifier_digest
    reserve(db, identity, kind=reservation_kind, value=reservation_value)

    for attempts in range(1, 6):
        response = db_client.post(
            verify_path,
            headers=headers,
            json={
                **identifier_body,
                "challenge_id": str(challenge_id),
                "code": wrong_code,
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "verification_invalid_or_expired"
        assert response.headers["Cache-Control"] == "no-store, private"
        db.expire_all()
        stored = db.get(RegistrationVerificationChallenge, challenge_id)
        assert stored.attempts == attempts
        assert stored.consumed_at is None
    closed = db_client.post(
        verify_path,
        headers=headers,
        json={
            **identifier_body,
            "challenge_id": str(challenge_id),
            "code": correct_code,
        },
    )
    assert closed.status_code == 422
    db.expire_all()
    assert db.get(RegistrationVerificationChallenge, challenge_id).attempts == 5

    # Advance only the synthetic send record so a fresh challenge can be issued
    # without sleeping or changing production rate-limit behavior.
    db.get(RegistrationVerificationChallenge, challenge_id).last_sent_at -= timedelta(minutes=2)
    db.commit()
    issued = db_client.post(issue_path, headers=headers, json=identifier_body)
    assert issued.status_code == 201
    fresh_id = UUID(issued.json()["challenge_id"])
    conflict = db_client.post(
        verify_path,
        headers=headers,
        json={
            **identifier_body,
            "challenge_id": str(fresh_id),
            "code": captured_sender.code_for(fresh_id),
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "personnel_credential_conflict"
    assert email not in conflict.text and reservation_value not in conflict.text
    db.expire_all()
    fresh = db.get(RegistrationVerificationChallenge, fresh_id)
    assert fresh.attempts == 0
    assert fresh.consumed_at is None
    assert (
        db.get(RegistrationApplication, application_id).verified_identifier_digest
        == original_digest
    )


def test_identity_reservation_blocks_first_registration_identity_write(
    db_client, captured_sender, db, identity
):
    application_id, csrf = _open_registration_session(
        db_client, captured_sender, purpose="student", phone="13800000739"
    )
    payload = complete_profile_payload("student", sequence=739)
    reserve(db, identity, kind="identity", value=payload["identity_number"])
    response = db_client.patch(
        "/api/v1/registration/me", headers={"X-Registration-CSRF-Token": csrf}, json=payload
    )
    assert response.status_code == 409
    from app.registration.models import RegistrationApplication

    db.expire_all()
    assert db.get(RegistrationApplication, application_id).identity_digest is None


def test_reservation_blocks_registration_finalization(db_client, captured_sender, db, identity):
    _application_id, csrf = _open_registration_session(
        db_client, captured_sender, purpose="student", phone="13800000739"
    )
    payload = complete_profile_payload("student", sequence=739)
    response = db_client.patch(
        "/api/v1/registration/me", headers={"X-Registration-CSRF-Token": csrf}, json=payload
    )
    assert response.status_code == 200
    reserve(db, identity, kind="identity", value=payload["identity_number"])
    response = db_client.post(
        "/api/v1/registration/me/finalize", headers={"X-Registration-CSRF-Token": csrf}
    )
    assert response.status_code == 409


def identifier_scenario(db, identity):
    summary = identity.create_base_person(person_input(703, email="before@example.com"))
    person = db.get(Person, summary.id)
    account = UserAccount(
        person_id=person.id,
        password_hash=PasswordService().hash(IDENTIFIER_PASSWORD),
        status="active",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    context = AuthContext(
        session=AuthSession(
            user_id=account.id,
            token_digest="synthetic-token",
            csrf_digest="synthetic-csrf",
            expires_at=NOW + timedelta(hours=1),
        ),
        account=account,
        person=person,
    )
    sender = CapturingVerificationSender(messages=[])
    verification = _verification(db, sender)
    issued = verification.issue(
        identifier_kind="email",
        identifier="reserved@example.com",
        purpose="add_login_identifier",
        request_source="127.0.0.81",
        account_id=account.id,
    )
    return context, verification, issued.challenge_id, sender.code_for(issued.challenge_id)


def test_reservation_blocks_verified_account_email_addition(db, identity):
    context, verification, challenge_id, code = identifier_scenario(db, identity)
    reserve(db, identity, kind="email", value="reserved@example.com")
    with pytest.raises(AccountIdentifierUnavailable):
        _auth(db).verify_identifier_addition(
            context,
            challenge_id=challenge_id,
            code=code,
            identifier_kind="email",
            identifier="reserved@example.com",
            current_password=IDENTIFIER_PASSWORD,
            request_id="synthetic",
            verification=verification,
        )
    from app.registration.models import RegistrationVerificationChallenge

    assert db.get(RegistrationVerificationChallenge, challenge_id).consumed_at is None


def test_legacy_shared_contact_email_can_become_one_unique_login_identifier(db, identity):
    from app.auth.models import AccountLoginIdentifier

    context, verification, challenge_id, code = identifier_scenario(db, identity)
    identity.update_person(
        context.person.id,
        PersonUpdate(email="reserved@example.com"),
        actor_user_id=None,
        request_id="synthetic",
    )
    other = identity.create_base_person(person_input(705, email="reserved@example.com"))
    assert (
        db.scalar(select(AccountLoginIdentifier.id).where(AccountLoginIdentifier.kind == "email"))
        is None
    )

    result = _auth(db).verify_identifier_addition(
        context,
        challenge_id=challenge_id,
        code=code,
        identifier_kind="email",
        identifier="reserved@example.com",
        current_password=IDENTIFIER_PASSWORD,
        request_id="synthetic",
        verification=verification,
    )

    assert [identifier.kind for identifier in result] == ["email"]
    assert (
        db.scalar(
            select(AccountLoginIdentifier.account_id).where(AccountLoginIdentifier.kind == "email")
        )
        == context.account.id
    )
    assert other.id != context.person.id
    assert identity.decrypt_email(db.get(Person, other.id)) == "reserved@example.com"


def test_identifier_addition_locks_credentials_and_people_before_challenge(db, identity):
    context, verification, challenge_id, code = identifier_scenario(db, identity)
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        _auth(db).verify_identifier_addition(
            context,
            challenge_id=challenge_id,
            code=code,
            identifier_kind="email",
            identifier="reserved@example.com",
            current_password=IDENTIFIER_PASSWORD,
            request_id="synthetic",
            verification=verification,
        )
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    challenge = next(
        i
        for i, sql in enumerate(statements)
        if "FROM registration_verification_challenges" in sql and "FOR UPDATE" in sql
    )
    people = next(
        i for i, sql in enumerate(statements) if "FROM people" in sql and "FOR UPDATE" in sql
    )
    advisory = [i for i, sql in enumerate(statements) if "pg_advisory_xact_lock" in sql]
    assert advisory and max(advisory) < challenge
    assert people < challenge


@pytest.mark.parametrize("purpose", ["add_login_identifier", "password_reset"])
def test_invalid_code_still_counts_when_target_becomes_unavailable(db, identity, purpose):
    from app.registration.models import RegistrationVerificationChallenge
    from app.registration.verification_service import VerificationInvalidOrExpired

    context, verification, challenge_id, code = identifier_scenario(db, identity)
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    challenge.purpose = purpose
    context.account.status = "disabled"
    db.flush()
    wrong_code = "000000" if code != "000000" else "999999"
    with pytest.raises(VerificationInvalidOrExpired):
        if purpose == "password_reset":
            _auth(db).complete_password_reset(
                challenge_id=challenge_id,
                code=wrong_code,
                new_password="NewStrong2026Aa",
                request_id="synthetic",
                verification=verification,
            )
        else:
            _auth(db).verify_identifier_addition(
                context,
                challenge_id=challenge_id,
                code=wrong_code,
                identifier_kind="email",
                identifier="reserved@example.com",
                current_password=IDENTIFIER_PASSWORD,
                request_id="synthetic",
                verification=verification,
            )
    db.expire_all()
    assert db.get(RegistrationVerificationChallenge, challenge_id).attempts == 1
    assert db.get(RegistrationVerificationChallenge, challenge_id).consumed_at is None


def test_password_reset_prerequisite_locks_precede_challenge(db, identity):
    context, verification, challenge_id, code = identifier_scenario(db, identity)
    auth = _auth(db)
    auth.verify_identifier_addition(
        context,
        challenge_id=challenge_id,
        code=code,
        identifier_kind="email",
        identifier="reserved@example.com",
        current_password=IDENTIFIER_PASSWORD,
        request_id="synthetic",
        verification=verification,
    )
    sender = CapturingVerificationSender(messages=[])
    verification = _verification(db, sender)
    verification.clock = lambda: NOW + timedelta(seconds=61)
    issued = verification.issue(
        identifier_kind="email",
        identifier="reserved@example.com",
        purpose="password_reset",
        request_source="127.0.0.82",
        account_id=context.account.id,
    )
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        auth.complete_password_reset(
            challenge_id=issued.challenge_id,
            code=sender.code_for(issued.challenge_id),
            new_password="NewStrong2026Aa",
            request_id="synthetic",
            verification=verification,
        )
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    challenge = next(
        i
        for i, sql in enumerate(statements)
        if "FROM registration_verification_challenges" in sql and "FOR UPDATE" in sql
    )
    prerequisites = [
        i
        for i, sql in enumerate(statements)
        if "pg_advisory_xact_lock" in sql or ("FROM people" in sql and "FOR UPDATE" in sql)
    ]
    assert prerequisites and max(prerequisites) < challenge


@pytest.mark.parametrize("entry", ["person", "account"])
@pytest.mark.parametrize("changed_field", ["email", "identity"])
def test_old_contact_snapshot_drift_is_retryable_without_partial_write(
    db, identity, monkeypatch, entry, changed_field
):
    from sqlalchemy import update

    from app.personnel_intake.locks import lock_personnel_credentials
    from app.registration.verification_service import lock_registration_credential_keys

    summary = identity.create_base_person(person_input(703, email="before@example.com"))

    injected = []

    def inject_drift(session):
        injected.append(changed_field)
        values = (
            {
                "email_ciphertext": identity.cipher.encrypt(
                    "changed@example.com", field_name="email"
                ),
                "email_digest": identity.lookup.digest_domain(
                    "person-email", "changed@example.com"
                ),
            }
            if changed_field == "email"
            else {
                "identity_ciphertext": identity.cipher.encrypt(
                    person_input(705).identity_number, field_name="identity_number"
                ),
                "identity_digest": identity.lookup.digest(person_input(705).identity_number),
            }
        )
        session.execute(
            update(Person).where(Person.id == summary.id).values(**values),
            execution_options={"synchronize_session": False},
        )

    def account_race(session, **kwargs):
        lock_personnel_credentials(session, **kwargs)
        inject_drift(session)

    def person_race(session, keys):
        lock_registration_credential_keys(session, keys)
        inject_drift(session)

    if entry == "person":
        # update_person imports this shared lock primitive at the operation
        # boundary so its complete lifecycle key set is acquired in one pass.
        monkeypatch.setattr(
            "app.registration.verification_service.lock_registration_credential_keys", person_race
        )
    else:
        monkeypatch.setattr("app.auth.service.lock_personnel_credentials", account_race)
    with pytest.raises(PersonnelIntakeError) as error:
        if entry == "person":
            identity.update_person(
                summary.id,
                PersonUpdate(email="destination@example.com"),
                actor_user_id=None,
                request_id="synthetic",
            )
        else:
            AccountService(
                db, identity_service=identity, passwords=PasswordService()
            ).create_account(AccountCreate(person_id=summary.id))
    assert error.value.status_code == 409
    assert injected == [changed_field]
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == summary.id)) is None


def test_policy_allows_same_request_and_person_but_not_other_claims(db, identity):
    person = identity.create_base_person(person_input(703, email="same@example.com"))
    request, _row = reserve(
        db, identity, kind="email", value="same@example.com", person_id=person.id
    )
    policy = CredentialReservationPolicy(db, lookup=identity.lookup)
    policy.require_available(
        identity=person_input(703).identity_number,
        email="same@example.com",
        person_id=person.id,
        request_id=request.id,
    )
    with pytest.raises(PersonnelIntakeError):
        policy.require_available(
            identity=person_input(705).identity_number,
            email="same@example.com",
            person_id=None,
            request_id=request.id,
        )


def test_policy_uses_login_email_domain_even_after_person_contact_changed(db, identity):
    from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
    from app.auth.models import AccountLoginIdentifier

    person = identity.create_base_person(person_input(703, email="contact@example.com"))
    account = UserAccount(
        person_id=person.id, password_hash="synthetic", status="active", force_password_change=False
    )
    db.add(account)
    db.flush()
    db.add(
        AccountLoginIdentifier(
            account_id=account.id,
            kind="email",
            identifier_digest=login_identifier_digest(
                identity.lookup, NormalizedLoginIdentifier("email", "login@example.com")
            ),
            verified_at=NOW,
            verification_source="test_verified",
        )
    )
    db.flush()
    policy = CredentialReservationPolicy(db, lookup=identity.lookup)
    with pytest.raises(PersonnelIntakeError):
        policy.require_email_available(email=" LOGIN@example.com ")
    policy.require_email_available(email="login@example.com", person_id=person.id)


@pytest.mark.parametrize("winner", ["intake", "existing"])
def test_intake_and_existing_person_write_serialize_across_connections(postgres_engine, winner):
    """Dropping either entry's shared credential locks permits duplicate ownership."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4

    from sqlalchemy import delete, func, text

    lookup = LookupDigest(bytes(range(32)))
    email = f"race-{uuid4().hex}@example.com"
    payload = person_input(753, email=email)
    with Session(postgres_engine) as setup:
        identity = IdentityService(setup, cipher=EnvelopeCipher(bytes(range(32))), lookup=lookup)
        request, reservation = reserve(
            setup, identity, kind="email", value=f"unused-{uuid4().hex}@example.com"
        )
        request_id = request.id
        actor_id = request.submitted_by_user_id
        actor_person_id = setup.get(UserAccount, actor_id).person_id
        setup.delete(reservation)
        setup.commit()
    acquired = threading.Event()
    release = threading.Event()
    loser_attempting = threading.Event()

    def claim(kind, first):
        with Session(postgres_engine) as session, session.begin():
            session.execute(text("SET LOCAL lock_timeout = '8s'"))

            def observe(_conn, _cursor, statement, _params, _context, _many):
                if "pg_advisory_xact_lock" in statement and not first:
                    loser_attempting.set()

            bind = session.connection()
            event.listen(bind, "before_cursor_execute", observe)
            try:
                if kind == "intake":
                    lock_personnel_credentials(
                        session,
                        emails={email},
                        identity_digests={lookup.digest(payload.identity_number)},
                        lookup=lookup,
                    )
                    CredentialReservationPolicy(session, lookup=lookup).require_available(
                        identity=payload.identity_number,
                        email=email,
                        person_id=None,
                        request_id=request_id,
                    )
                    session.add(
                        PersonnelCredentialReservation(
                            request_id=request_id,
                            kind="email",
                            digest=lookup.digest_domain("registration-verification-email", email),
                        )
                    )
                    session.flush()
                else:
                    IdentityService(
                        session, cipher=EnvelopeCipher(bytes(range(32))), lookup=lookup
                    ).create_base_person(payload)
                if first:
                    acquired.set()
                    assert release.wait(8)
                return "created"
            except PersonnelIntakeError:
                return "conflict"
            finally:
                event.remove(bind, "before_cursor_execute", observe)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(claim, winner, True)
            assert acquired.wait(8)
            second = pool.submit(claim, "existing" if winner == "intake" else "intake", False)
            try:
                assert loser_attempting.wait(8)
                assert not second.done()
            finally:
                release.set()
            assert first.result(timeout=10) == "created"
            assert second.result(timeout=10) == "conflict"
        with Session(postgres_engine) as verify:
            people = verify.scalar(
                select(func.count())
                .select_from(Person)
                .where(Person.identity_digest == lookup.digest(payload.identity_number))
            )
            reservations = verify.scalar(
                select(func.count())
                .select_from(PersonnelCredentialReservation)
                .where(
                    PersonnelCredentialReservation.request_id == request_id,
                    PersonnelCredentialReservation.ended_at.is_(None),
                )
            )
            assert people + reservations == 1
    finally:
        release.set()
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(PersonnelCredentialReservation).where(
                    PersonnelCredentialReservation.request_id == request_id
                )
            )
            cleanup.execute(
                delete(PersonnelIntakeRequest).where(PersonnelIntakeRequest.id == request_id)
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id == actor_id))
            cleanup.execute(
                delete(Person).where(
                    (Person.id == actor_person_id)
                    | (Person.identity_digest == lookup.digest(payload.identity_number))
                )
            )
