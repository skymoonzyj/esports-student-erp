from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.models import Base
from app.identity.models import Person
from app.personnel_intake.claims.models import ProfileClaim, ProfileClaimSession
from app.personnel_intake.claims.schemas import ClaimStartInput
from app.personnel_intake.models import PersonnelCredentialReservation
from app.registration.delivery import CapturingVerificationSender
from app.registration.models import RegistrationVerificationChallenge
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationInvalidOrExpired,
    VerificationTargetMismatch,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.api.conftest import make_cn_id
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_approval import platform_users as _platform_users
from tests.services.test_personnel_intake import actor, intake


@pytest.fixture
def claim_db(postgres_engine):
    # Every operation must really commit: failed OTP accounting uses a second
    # independent connection. This fixture owns only the leased disposable DB.
    with Session(postgres_engine) as db:
        baseline = {
            table: [dict(row) for row in db.execute(table.select()).mappings()]
            for table in Base.metadata.tables.values()
        }
        remaining = {table for table, rows in baseline.items() if rows}
        restore_order = []
        while remaining:
            ready = [
                table
                for table in remaining
                if not any(
                    fk.column.table in remaining
                    and fk.column.table is not table
                    and any(row[fk.parent.key] is not None for row in baseline[table])
                    for fk in table.foreign_keys
                )
            ]
            assert ready, "committed test baseline has cyclic business facts; do not truncate it"
            restore_order.extend(sorted(ready, key=lambda table: table.name))
            remaining.difference_update(ready)
        tables = ", ".join(
            postgres_engine.dialect.identifier_preparer.quote(t.name)
            for t in Base.metadata.tables.values()
        )
        db.rollback()
        try:
            yield db
        finally:
            db.rollback()
            db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
            for table in restore_order:
                db.execute(table.insert(), baseline[table])
            db.commit()


def claim_input(**changes):
    return ClaimStartInput(
        full_name=changes.pop("full_name", "隔离建档人员"),
        identity_number=changes.pop("identity_number", make_cn_id("20000102", 321)),
        email=changes.pop("email", "claim@example.com"),
        **changes,
    )


def claims(db, *, clock=None, sender=None, **limits):
    from app.personnel_intake.claims.public_service import ProfileClaimService

    lookup = LookupDigest(bytes(range(32)))
    sender = sender or CapturingVerificationSender(messages=[])
    verification = RegistrationVerificationService(
        db,
        lookup=lookup,
        verification_pepper=bytes(reversed(range(32))),
        sender=sender,
        app_env="test",
        clock=clock,
    )
    return ProfileClaimService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=lookup,
        verification=verification,
        token_pepper=bytes(range(1, 33)),
        clock=clock,
        **limits,
    ), sender


def approved_target(db):
    users = _platform_users.__wrapped__(db)
    result = approvals(db).direct_create(actor(users, "admin"), intake(users, email=None), uuid4())
    db.commit()
    return result, users


def incomplete_pending_target(db, **changes):
    from app.personnel_intake.schemas import PendingIntakeInput

    users = _platform_users.__wrapped__(db)
    data = PendingIntakeInput.model_validate(
        {
            "kind": "staff",
            "full_name": "隔离护照本人",
            "identity_number": "PENDING-CLAIM-851",
            "document_type": "passport",
            "document_issuing_country": "US",
            "team_allocation_status": "not_required",
            **changes,
        }
    )
    result = approvals(db).direct_create_pending(actor(users, "admin"), data, uuid4())
    db.commit()
    return result, users


def pending_claim_input(**changes):
    return claim_input(
        **{
            "full_name": "隔离护照本人",
            "identity_number": " pending-claim-851 ",
            "document_type": "passport",
            "issuing_country": "US",
            "completion": {
                "phone": "+1 202 555 0100",
                "sex": "female",
                "date_of_birth": "2000-01-02",
            },
            **changes,
        }
    )


def test_unknown_profile_issuer_is_not_claimed_by_bare_passport_number(claim_db):
    target, _ = incomplete_pending_target(claim_db, document_issuing_country=None)
    service, sender = claims(claim_db)
    verify_new(service, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    assert claim.target_person_id is None and claim.private_match_reason == "profile_not_matched"
    assert claim_db.get(Person, target.person_id).document_issuing_country is None


def test_v2_historical_proof_rejects_original_session_version_mismatch(claim_db, monkeypatch):
    from types import SimpleNamespace

    from app.personnel_intake.claims.proof import validate_claim_proof

    incomplete_pending_target(claim_db)
    service, sender = claims(claim_db)
    verify_new(service, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    original = claim_db.get(ProfileClaimSession, claim.session_id)
    assert claim.submission_version == original.submission_version == 2
    assert (
        validate_claim_proof(claim_db, claim, cipher=service.cipher, lookup=service.lookup).version
        == 2
    )
    # Storage already forbids this update. Independently test the reader's
    # defense in depth against a mismatched historical session marker.
    mismatched = SimpleNamespace(
        **{c.key: getattr(original, c.key) for c in original.__table__.columns}
    )
    mismatched.submission_version = 1
    real_get = claim_db.get

    def read(model, identifier, **kwargs):
        return (
            mismatched
            if model is ProfileClaimSession and identifier == original.id
            else real_get(model, identifier, **kwargs)
        )

    monkeypatch.setattr(claim_db, "get", read)
    with pytest.raises(VerificationInvalidOrExpired):
        validate_claim_proof(claim_db, claim, cipher=service.cipher, lookup=service.lookup)


def test_pending_corrupt_reserved_email_still_has_uniform_public_response(claim_db):
    target, _ = incomplete_pending_target(claim_db, email="claim@example.com")
    person = claim_db.get(Person, target.person_id)
    person.email_ciphertext = "corrupt-private-envelope"
    claim_db.commit()
    service, sender = claims(claim_db)
    _, response = verify_new(service, sender, pending_claim_input())
    assert response.model_dump() == {"status": "waiting_review", "reason": None}
    assert claim_db.scalar(select(ProfileClaim)).private_match_reason == "approval_evidence_invalid"


def test_incomplete_pending_profile_matches_only_exact_known_issuer(claim_db):
    target, _ = incomplete_pending_target(claim_db)
    service, sender = claims(claim_db)
    _, response = verify_new(service, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    assert response.model_dump() == {"status": "waiting_review", "reason": None}
    assert claim.target_person_id == target.person_id and claim.private_match_reason is None
    assert claim.submission_version == 2
    person = claim_db.get(Person, target.person_id)
    assert person.phone_ciphertext is None and person.sex is None and person.date_of_birth is None


def verify_new(service, sender, value=None, source="127.0.30.1"):
    issued = service.start(value or claim_input(), source)
    token = issued.session_token.get_secret_value()
    result = service.verify(token, SecretStr(sender.messages[-1].code))
    return token, result


def test_verified_claim_is_immutable_and_duplicate_returns_existing(claim_db):
    result, _ = approved_target(claim_db)
    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0])
    token, public = verify_new(service, sender)
    assert public.model_dump() == {"status": "waiting_review", "reason": None}
    original = claim_db.scalar(select(ProfileClaim))
    old = (
        original.session_id,
        original.challenge_id,
        original.payload_ciphertext,
        original.verified_at,
        original.challenge_consumed_at,
        original.expires_at,
    )
    assert original.target_person_id == result.person_id and original.private_match_reason is None
    now[0] += timedelta(minutes=2)
    second, _ = verify_new(service, sender)
    assert service.status(second).status == service.status(token).status == "waiting_review"
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 1
    claim_db.refresh(original)
    assert old == (
        original.session_id,
        original.challenge_id,
        original.payload_ciphertext,
        original.verified_at,
        original.challenge_consumed_at,
        original.expires_at,
    )
    verify_new(service, sender, claim_input(email="second@example.com"))
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 2
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(PersonnelCredentialReservation)
            .where(
                PersonnelCredentialReservation.profile_claim_id.is_not(None),
                PersonnelCredentialReservation.kind == "identity",
            )
        )
        == 0
    )


def test_fresh_email_proof_can_reopen_own_approved_status(claim_db):
    approved_target(claim_db)
    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0])
    old_token, _ = verify_new(service, sender)
    original = claim_db.scalar(select(ProfileClaim))
    proof = (original.session_id, original.challenge_id, original.verified_at, original.expires_at)
    original.status = "approved"
    original.version += 1
    # Matching must not run again now the reviewed target has an email.
    person = claim_db.get(Person, original.target_person_id)
    person.email_ciphertext = service.cipher.encrypt("claim@example.com", field_name="email")
    person.email_digest = service.lookup.digest_domain("person-email", "claim@example.com")
    claim_db.commit()
    now[0] += timedelta(days=1)
    with pytest.raises(VerificationInvalidOrExpired):
        service.status(old_token)
    _, status = verify_new(service, sender)
    assert status.model_dump() == {"status": "approved", "reason": None}
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 1
    claim_db.refresh(original)
    assert proof == (
        original.session_id,
        original.challenge_id,
        original.verified_at,
        original.expires_at,
    )


def test_failed_guesses_survive_outer_rollback_and_close_after_five(claim_db):
    service, sender = claims(claim_db)
    issued = service.start(claim_input(), "127.0.30.2")
    actual = sender.messages[-1].code
    wrong = "000000" if actual != "000000" else "000001"
    for count in range(1, 6):
        with pytest.raises(VerificationInvalidOrExpired):
            service.verify(issued.session_token.get_secret_value(), SecretStr(wrong))
        claim_db.rollback()
        assert (
            claim_db.get(
                RegistrationVerificationChallenge, sender.messages[-1].challenge_id
            ).attempts
            == count
        )
    with pytest.raises(VerificationInvalidOrExpired):
        service.verify(issued.session_token.get_secret_value(), SecretStr(actual))
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 0


def test_pending_quota_counts_unknown_inputs_but_duplicate_reuses_without_extension(claim_db):
    from app.personnel_intake.claims.errors import ProfileClaimError

    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0], email_pending_limit=1)
    verify_new(service, sender)
    now[0] += timedelta(seconds=60)
    verify_new(service, sender)
    now[0] += timedelta(seconds=60)
    issued = service.start(claim_input(full_name="另一个输入"), "127.0.30.3")
    with pytest.raises(ProfileClaimError) as caught:
        service.verify(issued.session_token.get_secret_value(), SecretStr(sender.messages[-1].code))
    assert caught.value.status == 429 and caught.value.retry_after_seconds > 0
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 1


def test_old_session_challenge_replay_and_cross_session_codes_are_rejected(claim_db):
    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0])
    first = service.start(claim_input(), "127.0.30.4")
    code = sender.messages[-1].code
    now[0] += timedelta(seconds=60)
    second = service.start(claim_input(), "127.0.30.4")
    with pytest.raises(VerificationInvalidOrExpired):
        service.verify(first.session_token.get_secret_value(), SecretStr(code))
    service.verify(second.session_token.get_secret_value(), SecretStr(sender.messages[-1].code))
    with pytest.raises(VerificationInvalidOrExpired):
        service.verify(second.session_token.get_secret_value(), SecretStr(sender.messages[-1].code))
    assert service.status(second.session_token.get_secret_value()).status == "waiting_review"


@pytest.mark.parametrize("scope", ["email", "identity", "global"])
def test_pending_quotas_are_atomic_across_independent_connections(claim_db, scope):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.personnel_intake.claims.errors import ProfileClaimError

    now = [datetime.now(UTC)]
    limits = {f"{scope}_pending_limit": 1}
    service, sender = claims(claim_db, clock=lambda: now[0], **limits)
    first = service.start(claim_input(), "127.0.30.10")
    first_code = sender.messages[-1].code
    now[0] += timedelta(seconds=60)
    second_input = (
        claim_input(full_name="第二个输入")
        if scope == "email"
        else claim_input(email="other@example.com")
    )
    if scope == "global":
        second_input = claim_input(
            email="other@example.com", identity_number=make_cn_id("20000102", 323)
        )
    second = service.start(second_input, "127.0.30.11")
    barrier = Barrier(2)
    engine = claim_db.get_bind()

    def compete(token, code):
        with Session(engine) as db:
            worker, _ = claims(db, clock=lambda: now[0], **limits)
            barrier.wait(timeout=10)
            try:
                return worker.verify(token.get_secret_value(), SecretStr(code)).status
            except ProfileClaimError as error:
                assert error.status == 429 and error.retry_after_seconds > 0
                return "limited"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: compete(*pair),
                [
                    (first.session_token, first_code),
                    (second.session_token, sender.messages[-1].code),
                ],
            )
        )
    assert sorted(results) == ["limited", "waiting_review"]
    claim_db.expire_all()
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 1
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(RegistrationVerificationChallenge)
            .where(RegistrationVerificationChallenge.consumed_at.is_not(None))
        )
        == 1
    )


def test_exact_send_and_cookie_expiry_boundaries(claim_db):
    from app.registration.start_limiter import RegistrationStartRateLimited

    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0])
    first = service.start(claim_input(), "127.0.30.12")
    now[0] += timedelta(seconds=59)
    with pytest.raises(RegistrationStartRateLimited):
        service.start(claim_input(), "127.0.30.12")
    assert len(sender.messages) == 1
    now[0] += timedelta(seconds=1)
    second = service.start(claim_input(), "127.0.30.12")
    assert len(sender.messages) == 2
    now[0] = second.public.expires_at
    with pytest.raises(VerificationInvalidOrExpired):
        service.verify(second.session_token.get_secret_value(), SecretStr(sender.messages[-1].code))
    with pytest.raises(VerificationInvalidOrExpired):
        service.status(first.session_token.get_secret_value())


def test_pending_expires_at_seven_days_and_releases_real_reservation(claim_db):
    approved_target(claim_db)
    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0])
    verify_new(service, sender)
    row = claim_db.scalar(select(ProfileClaim))
    original_deadline = row.expires_at
    now[0] = original_deadline - timedelta(seconds=1)
    token, _ = verify_new(service, sender)
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 1
    now[0] = original_deadline
    assert service.status(token).status == "expired"
    claim_db.refresh(row)
    assert row.status == "expired" and row.version == 2
    reservation = claim_db.scalar(
        select(PersonnelCredentialReservation).where(
            PersonnelCredentialReservation.profile_claim_id == row.id
        )
    )
    assert reservation.ended_at == original_deadline
    now[0] += timedelta(seconds=60)
    _, status = verify_new(service, sender)
    assert status.status == "waiting_review"
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 2


def test_session_pruning_redacts_only_expired_copies_and_preserves_historical_proof(claim_db):
    from app.personnel_intake.claims.proof import validate_claim_proof
    from app.personnel_intake.claims.public_service import prune_expired_session_inputs

    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0])
    verify_new(service, sender)
    original = claim_db.scalar(select(ProfileClaim))
    old_ciphertext = original.payload_ciphertext
    now[0] += timedelta(minutes=9)
    service.start(claim_input(email="active@example.com"), "127.0.30.13")
    now[0] += timedelta(minutes=1)
    assert prune_expired_session_inputs(claim_db, now=now[0]) == 1
    claim_db.commit()
    sessions = list(
        claim_db.scalars(select(ProfileClaimSession).order_by(ProfileClaimSession.created_at))
    )
    assert sessions[0].payload_ciphertext is None and sessions[1].payload_ciphertext is not None
    assert original.payload_ciphertext == old_ciphertext
    assert (
        validate_claim_proof(claim_db, original, cipher=service.cipher, lookup=service.lookup).email
        == "claim@example.com"
    )


def test_smtp_failure_is_uniform_and_counted_without_live_session(claim_db):
    import smtplib

    from app.audit.models import AuditEvent
    from app.personnel_intake.claims.errors import ProfileClaimError

    class FailedSender:
        def send_code(self, **kwargs):
            raise smtplib.SMTPException("synthetic private provider failure")

    service, _ = claims(claim_db, sender=FailedSender())
    with pytest.raises(ProfileClaimError) as caught:
        service.start(claim_input(), "127.0.30.14")
    assert (caught.value.status, caught.value.code) == (503, "verification_delivery_failed")
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaimSession)) == 0
    challenge = claim_db.scalar(select(RegistrationVerificationChallenge))
    assert challenge.consumed_at is not None
    event = claim_db.scalar(
        select(AuditEvent).where(AuditEvent.action == "profile_claim.delivery_failed")
    )
    assert event.outcome == "failed" and event.event_metadata == {}
    assert "claim@example.com" not in str(event.__dict__)


def test_wrong_purpose_and_cross_session_code_never_prove_claim(claim_db):
    service, sender = claims(claim_db)
    first = service.start(claim_input(), "127.0.30.15")
    second = service.start(claim_input(email="separate@example.com"), "127.0.30.15")
    first_code, second_code = sender.messages[0].code, sender.messages[1].code
    if first_code == second_code:
        second_code = "000000" if first_code != "000000" else "000001"
    with pytest.raises(VerificationInvalidOrExpired):
        service.verify(first.session_token.get_secret_value(), SecretStr(second_code))
    other = claim_db.get(RegistrationVerificationChallenge, sender.messages[1].challenge_id)
    other.purpose = "registration"
    claim_db.commit()
    with pytest.raises(VerificationTargetMismatch):
        service.verify(second.session_token.get_secret_value(), SecretStr(sender.messages[1].code))
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 0


def test_profile_claim_shares_all_purpose_send_total(claim_db):
    from app.registration.verification_service import VerificationRateLimited

    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0])
    for purpose in (
        "registration",
        "password_reset",
        "assisted_activation",
        "add_login_identifier",
    ):
        service.verification.issue(
            identifier_kind="email",
            identifier="claim@example.com",
            purpose=purpose,
            request_source="127.0.30.16",
        )
        now[0] += timedelta(seconds=60)
    service.start(claim_input(), "127.0.30.16")
    now[0] += timedelta(seconds=60)
    with pytest.raises(VerificationRateLimited):
        service.start(claim_input(), "127.0.30.16")
    assert len(sender.messages) == 5


def test_consumption_callback_never_reacquires_preceding_row_locks(claim_db):
    from sqlalchemy import event

    approved_target(claim_db)
    service, sender = claims(claim_db)
    issued = service.start(claim_input(), "127.0.30.17")
    after_challenge = []
    consuming = [False]
    engine = claim_db.get_bind()

    def observe(connection, cursor, statement, parameters, context, executemany):
        lower = statement.lower()
        if (
            "from registration_verification_challenges" in lower
            and "for update" in lower
            and "nowait" not in lower
        ):
            consuming[0] = True
        elif consuming[0] and "for update" in lower:
            after_challenge.append(lower)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        service.verify(issued.session_token.get_secret_value(), SecretStr(sender.messages[-1].code))
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert consuming[0] and after_challenge == []


def test_start_does_not_query_any_target_or_reservation(claim_db):
    from sqlalchemy import event

    service, sender = claims(claim_db)
    queries = []
    engine = claim_db.get_bind()

    def observe(connection, cursor, statement, parameters, context, executemany):
        lower = statement.lower()
        if lower.lstrip().startswith("select"):
            queries.append(lower)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        service.start(claim_input(), "127.0.30.18")
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(sender.messages) == 1
    assert not any(
        f"from {table}" in query
        for table in (
            "people",
            "user_accounts",
            "assisted_person_profiles",
            "profile_claims",
            "personnel_credential_reservations",
        )
        for query in queries
    )


def test_success_callback_failure_rolls_back_claim_session_and_consumption(claim_db, monkeypatch):
    service, sender = claims(claim_db)
    issued = service.start(claim_input(), "127.0.30.19")

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(service, "_audit", fail)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        service.verify(issued.session_token.get_secret_value(), SecretStr(sender.messages[-1].code))
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 0
    assert claim_db.scalar(select(ProfileClaimSession)).state == "requested"
    assert claim_db.scalar(select(RegistrationVerificationChallenge)).consumed_at is None


@pytest.mark.parametrize("state", ["rejected", "revoked", "invalidated", "expired"])
def test_terminal_claim_requires_new_application_not_resurrected_original(claim_db, state):
    now = [datetime.now(UTC)]
    service, sender = claims(claim_db, clock=lambda: now[0])
    token, _ = verify_new(service, sender)
    original = claim_db.scalar(select(ProfileClaim))
    original.status, original.version, original.public_reason = state, 2, "请联系管理员核对"
    claim_db.commit()
    assert service.status(token).status == {"rejected": "rejected", "expired": "expired"}.get(
        state, "closed"
    )
    now[0] += timedelta(seconds=60)
    _, public = verify_new(service, sender)
    assert public.model_dump() == {"status": "waiting_review", "reason": None}
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "registration"),
        ("identifier_digest", "f" * 64),
        ("consumed_at", datetime(2020, 1, 1, tzinfo=UTC)),
    ],
)
def test_historical_proof_validates_original_purpose_email_and_consumed_time(
    claim_db, field, value
):
    from app.personnel_intake.claims.proof import validate_claim_proof

    service, sender = claims(claim_db)
    verify_new(service, sender)
    original = claim_db.scalar(select(ProfileClaim))
    challenge = claim_db.get(RegistrationVerificationChallenge, original.challenge_id)
    setattr(challenge, field, value)
    claim_db.commit()
    with pytest.raises(VerificationInvalidOrExpired):
        validate_claim_proof(claim_db, original, cipher=service.cipher, lookup=service.lookup)


def test_pruning_skips_locked_rows_and_respects_batch_limit(claim_db):
    from app.personnel_intake.claims.public_service import prune_expired_session_inputs

    now = [datetime.now(UTC)]
    service, _ = claims(claim_db, clock=lambda: now[0])
    service.start(claim_input(), "127.0.30.20")
    service.start(claim_input(email="other@example.com"), "127.0.30.20")
    rows = list(claim_db.scalars(select(ProfileClaimSession).order_by(ProfileClaimSession.id)))
    ids = [row.id for row in rows]
    claim_db.rollback()
    engine = claim_db.get_bind()
    with Session(engine) as holder, holder.begin():
        holder.scalar(
            select(ProfileClaimSession).where(ProfileClaimSession.id == ids[0]).with_for_update()
        )
        assert (
            prune_expired_session_inputs(claim_db, now=now[0] + timedelta(minutes=10), limit=1) == 1
        )
        claim_db.commit()
        assert (
            claim_db.get(ProfileClaimSession, ids[0], populate_existing=True).payload_ciphertext
            is not None
        )
        assert (
            claim_db.get(ProfileClaimSession, ids[1], populate_existing=True).payload_ciphertext
            is None
        )
    assert prune_expired_session_inputs(claim_db, now=now[0] + timedelta(minutes=10), limit=1) == 1
    claim_db.commit()
