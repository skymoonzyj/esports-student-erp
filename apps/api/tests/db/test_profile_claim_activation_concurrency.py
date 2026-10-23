from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.identity.models import Person
from app.personnel_intake.activation_models import AssistedActivationSession
from app.personnel_intake.claims.models import ProfileClaim, ProfileClaimBinding
from app.personnel_intake.claims.schemas import ClaimReasonInput
from app.registration.models import RegistrationVerificationChallenge
from tests.services.test_assisted_activation import activation
from tests.services.test_profile_claim_binding import (
    approved,
    registration_application,
    verified_activation,
)
from tests.services.test_profile_claim_review import decision, pending, reviews
from tests.services.test_profile_claims import claim_db as _claim_db


@pytest.fixture
def claim_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


def _race(engine, actions):
    barrier = Barrier(len(actions))
    backend_pids = []

    def run(action):
        # Pin each physical connection across service-owned commits and the
        # barrier, rather than allowing a sequential pool checkout to disguise
        # itself as independent-connection concurrency.
        with engine.connect() as connection, Session(bind=connection) as db:
            db.execute(text("SET lock_timeout = '5s'"))
            backend_pids.append(db.scalar(select(func.pg_backend_pid())))
            db.commit()
            barrier.wait(timeout=10)
            try:
                action(db)
                return "success"
            except Exception as error:
                if getattr(error, "status", None) in {400, 409, 422} or type(error).__name__ in {
                    "VerificationInvalidOrExpired",
                    "VerificationBusy",
                    "RegistrationFinalizationUnavailable",
                    "RegistrationConflictPendingReview",
                    "CredentialReservationConflict",
                    "AccountIdentifierUnavailable",
                }:
                    return "conflict"
                raise

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        outcomes = list(pool.map(run, actions))
    assert len(set(backend_pids)) == len(actions)
    return outcomes


def _who(db, account_id):
    account = db.get(UserAccount, account_id)
    return AuthContext(account=account, person=db.get(Person, account.person_id), session=None)


@pytest.mark.parametrize("operation", ["correction", "revoke", "approve"])
def test_pending_staff_lifecycle_and_finish_preserve_one_account_otp_and_roles(claim_db, operation):
    import hashlib
    import json

    from app.audit.models import AuditEvent
    from app.auth.models import AccountLoginIdentifier
    from app.housing.models import HousingStay
    from app.personnel_intake.claims.errors import ProfileClaimError
    from app.personnel_intake.claims.models import ProfileClaimDecision
    from app.personnel_intake.claims.schemas import ClaimDecisionInput
    from app.personnel_intake.models import (
        AssistedApproval,
        AssistedPersonProfile,
        PersonnelIntakeRequest,
        PersonnelIntakeRevision,
    )
    from app.personnel_intake.schemas import PendingProfileCorrection
    from app.rbac.models import Role, UserRole
    from tests.services.test_assisted_activation import completed_pending_profile
    from tests.services.test_housing_reconciliation import _room, _stay
    from tests.services.test_personnel_approval import approvals

    target, users, claim = completed_pending_profile(claim_db)
    person = claim_db.get(Person, target.person_id)
    person.nickname, person.profile_notes = "竞争保留昵称", "竞争保留备注"
    _stay(
        claim_db, person_id=person.id, room=_room(claim_db), actor_id=users["accounts"]["admin"].id
    )
    claim_db.commit()
    stay_query = select(*HousingStay.__table__.columns).where(HousingStay.person_id == person.id)
    old_stays = list(claim_db.execute(stay_query))
    _, token, code, session = verified_activation(claim_db, person_id=target.person_id)
    profile_version = claim_db.get(AssistedPersonProfile, target.person_id).version
    approval_decision = claim_db.scalar(
        select(ProfileClaimDecision).where(ProfileClaimDecision.claim_id == claim.id)
    )
    replay = ClaimDecisionInput(
        expected_version=1,
        idempotency_key=approval_decision.idempotency_key,
        identity_confirmed=True,
    )
    person_id, claim_id, actor_id, challenge_id = (
        person.id,
        claim.id,
        users["accounts"]["admin"].id,
        session.challenge_id,
    )
    correction = PendingProfileCorrection(
        expected_version=profile_version,
        idempotency_key=uuid4(),
        reason="竞争更正真实联系资料",
        full_name=person.full_name,
        identity_number="PENDING-CLAIM-851",
        document_type="passport",
        document_issuing_country="US",
        phone="+1 202 555 0199",
        sex="female",
        date_of_birth=person.date_of_birth,
    )
    engine = claim_db.get_bind()
    claim_db.rollback()
    approval_retries = []

    def finish(db):
        activation(db, clock=lambda: datetime.now(UTC) + timedelta(seconds=61))[0].finish(
            token, code, SecretStr("SecurePass1")
        )

    def mutate(db):
        who = _who(db, actor_id)
        if operation == "correction":
            approvals(db).correct_pending_profile(who, person_id, correction)
        elif operation == "revoke":
            reviews(db).revoke(
                who,
                claim_id,
                ClaimReasonInput(
                    expected_version=2, idempotency_key=uuid4(), reason="竞争撤销证明"
                ),
            )
        else:
            try:
                reviews(db).approve(who, claim_id, replay)
            except ProfileClaimError as error:
                # Activation may change the discovered account graph while the
                # reviewer waits. Preserve the fail-closed fence, not arbitrary
                # conflicts, then prove the exact same operation is replayable.
                assert error.status == 409 and error.code == "profile_claim_retry"
                approval_retries.append(error.code)
                raise
            except Exception as error:
                # _race accepts broader conflicts for other lifecycle arms;
                # none of those may masquerade as this exact safe retry.
                raise AssertionError("unexpected_approval_replay_error") from error

    outcomes = _race(engine, [finish, mutate])
    if operation == "approve":
        assert outcomes[0] == "success"
        assert outcomes[1] in {"success", "conflict"}
        assert approval_retries == ([] if outcomes[1] == "success" else ["profile_claim_retry"])

        def replay_snapshot():
            snapshots = {}
            for model in (
                Person,
                PersonnelIntakeRequest,
                PersonnelIntakeRevision,
                AssistedApproval,
                AssistedPersonProfile,
                ProfileClaim,
                ProfileClaimBinding,
                ProfileClaimDecision,
                AuditEvent,
                UserAccount,
                AccountLoginIdentifier,
                UserRole,
                AssistedActivationSession,
                RegistrationVerificationChallenge,
                HousingStay,
            ):
                table = model.__table__
                rows = [
                    dict(row)
                    for row in claim_db.execute(
                        select(table).order_by(*table.primary_key.columns)
                    ).mappings()
                ]
                # Assertion failures expose hashes, not synthetic credentials
                # or decrypted identity/contact material.
                snapshots[table.name] = hashlib.sha256(
                    json.dumps(rows, sort_keys=True, default=str).encode()
                ).hexdigest()
            return snapshots

        before_replay = replay_snapshot()
        with Session(engine) as retry_db:
            replayed = reviews(retry_db).approve(_who(retry_db, actor_id), claim_id, replay)
        assert replayed.id == claim_id and replayed.person_id == person_id
        assert replayed.status == "completed" and replayed.version == 3
        assert replay_snapshot() == before_replay, "same_key_replay_mutated_business_evidence"
    else:
        assert sorted(outcomes) == ["conflict", "success"]
    accounts = list(claim_db.scalars(select(UserAccount).where(UserAccount.person_id == person_id)))
    assert len(accounts) <= 1
    if operation == "approve":
        assert len(accounts) == 1
    claim = claim_db.get(ProfileClaim, claim_id, populate_existing=True)
    consumed = claim_db.get(
        RegistrationVerificationChallenge, challenge_id, populate_existing=True
    ).consumed_at
    otp_consumptions = claim_db.scalar(
        select(func.count())
        .select_from(AssistedActivationSession)
        .where(
            AssistedActivationSession.person_id == person_id,
            AssistedActivationSession.state == "completed",
        )
    )
    assert otp_consumptions <= 1
    if accounts:
        assert accounts[0].person_id == person_id
        assert claim.status == "completed" and consumed == claim.completed_at
        assert set(
            claim_db.scalars(
                select(Role.key).join(UserRole).where(UserRole.user_id == accounts[0].id)
            )
        ) == {"registered_user"}
        assert otp_consumptions == 1
    else:
        assert claim.status == ("revoked" if operation == "revoke" else "approved")
        assert otp_consumptions == 0
    assert list(claim_db.execute(stay_query)) == old_stays
    current = claim_db.get(Person, person_id, populate_existing=True)
    assert (current.nickname, current.profile_notes) == ("竞争保留昵称", "竞争保留备注")


def test_double_finish_completes_exactly_one_claim_account_and_otp_on_independent_connections(
    claim_db,
):
    from app.audit.models import AuditEvent
    from app.rbac.models import UserRole

    target, _, claim, _, _ = approved(claim_db)
    _, token, code, session = verified_activation(claim_db, person_id=target.person_id)
    engine, claim_id, challenge_id = claim_db.get_bind(), claim.id, session.challenge_id
    claim_db.rollback()
    finish = lambda db: activation(db, clock=lambda: datetime.now(UTC) + timedelta(seconds=61))[
        0
    ].finish(token, code, SecretStr("SecurePass1"))
    result = _race(engine, [finish, finish])
    assert "success" in result
    row = claim_db.get(ProfileClaim, claim_id, populate_existing=True)
    assert row.status == "completed" and row.version == 3
    accounts = list(
        claim_db.scalars(select(UserAccount).where(UserAccount.person_id == target.person_id))
    )
    assert len(accounts) == 1 and row.completed_account_id == accounts[0].id
    assert (
        claim_db.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.user_id == accounts[0].id)
        )
        == 1
    )
    assert (
        claim_db.get(RegistrationVerificationChallenge, challenge_id).consumed_at
        == row.completed_at
    )
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "profile_claim.completed")
        )
        == 1
    )
    finish(claim_db)
    claim_db.refresh(row)
    assert row.version == 3


def test_revoke_and_finish_have_one_legal_winner_on_independent_connections(claim_db):
    target, users, claim, _, _ = approved(claim_db)
    _, token, code, _ = verified_activation(claim_db, person_id=target.person_id)
    engine, claim_id, admin_id = claim_db.get_bind(), claim.id, users["accounts"]["admin"].id
    claim_db.rollback()

    def finish(db):
        activation(db, clock=lambda: datetime.now(UTC) + timedelta(seconds=61))[0].finish(
            token, code, SecretStr("SecurePass1")
        )

    def revoke(db):
        reviews(db).revoke(
            _who(db, admin_id),
            claim_id,
            ClaimReasonInput(expected_version=2, idempotency_key=uuid4(), reason="竞争撤销"),
        )

    assert sorted(_race(engine, [finish, revoke])) == ["conflict", "success"]
    row = claim_db.get(ProfileClaim, claim_id, populate_existing=True)
    account = claim_db.scalar(select(UserAccount).where(UserAccount.person_id == target.person_id))
    binding = claim_db.scalar(
        select(ProfileClaimBinding).where(ProfileClaimBinding.claim_id == claim_id)
    )
    assert row.version == 3
    if account:
        assert row.status == "completed" and binding.revoked_at is None
    else:
        assert row.status == "revoked" and binding.revoked_at is not None
        assert claim_db.get(Person, target.person_id).email_ciphertext is None


def test_ordinary_finalize_cannot_beat_claim_approval_on_independent_connections(claim_db):
    from app.registration.finalization_service import RegistrationFinalizationService

    target, users, claim, _ = pending(claim_db)
    application_id = registration_application(claim_db, target.person_id)
    engine, claim_id, admin_id = claim_db.get_bind(), claim.id, users["accounts"]["admin"].id
    claim_db.rollback()

    def approve(db):
        reviews(db).approve(_who(db, admin_id), claim_id, decision())

    def finalize(db):
        service, _ = activation(db)
        RegistrationFinalizationService(db, cipher=service.cipher, lookup=service.lookup).finalize(
            application_id, request_id=str(uuid4())
        )

    assert _race(engine, [approve, finalize]) == ["success", "conflict"]
    assert claim_db.get(ProfileClaim, claim_id, populate_existing=True).status == "approved"
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )


def test_failure_after_claim_completion_rolls_back_account_roles_otp_and_claim(
    claim_db, monkeypatch
):
    import app.personnel_intake.activation_service as module

    target, _, claim, _, _ = approved(claim_db)
    service, token, code, row = verified_activation(claim_db, person_id=target.person_id)
    actual = module.complete_claim_in_transaction

    def fail(*args, **kwargs):
        actual(*args, **kwargs)
        raise RuntimeError("isolated completion rollback")

    monkeypatch.setattr(module, "complete_claim_in_transaction", fail)
    with pytest.raises(RuntimeError, match="isolated completion rollback"):
        service.finish(token, code, SecretStr("SecurePass1"))
    claim_db.refresh(claim)
    claim_db.refresh(row)
    assert claim.status == "approved" and claim.version == 2 and claim.completed_at is None
    assert row.state == "verified"
    assert (
        claim_db.get(
            RegistrationVerificationChallenge, row.challenge_id, populate_existing=True
        ).consumed_at
        is None
    )
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )
    monkeypatch.setattr(module, "complete_claim_in_transaction", actual)
    assert service.finish(token, code, SecretStr("SecurePass1")).completed


def test_identifier_addition_and_claim_approval_share_email_ownership_on_independent_connections(
    claim_db,
):
    from tests.services.test_account_access_readiness import services
    from tests.services.test_personnel_intake import actor

    target, users, claim, _ = pending(claim_db)
    _, _, auth = services(claim_db)
    auth.clock = lambda: datetime.now(UTC) + timedelta(seconds=61)
    service, _ = activation(claim_db, clock=auth.clock)
    actor_id, claim_id = users["accounts"]["admin"].id, claim.id
    challenge = auth.issue_identifier_addition(
        actor(users, "admin"),
        identifier_kind="email",
        identifier="claim@example.com",
        current_password="AdminApi2026Aa",
        request_source="127.0.91.3",
        verification=service.verification,
    )
    code = service.verification.sender.code_for(challenge.challenge_id)
    engine = claim_db.get_bind()
    claim_db.rollback()

    def approve(db):
        reviews(db).approve(_who(db, actor_id), claim_id, decision())

    def add_identifier(db):
        _, _, other_auth = services(db)
        other_auth.clock = lambda: datetime.now(UTC) + timedelta(seconds=61)
        verify, _ = activation(db, clock=other_auth.clock)
        other_auth.verify_identifier_addition(
            _who(db, actor_id),
            challenge_id=challenge.challenge_id,
            code=code,
            identifier_kind="email",
            identifier="claim@example.com",
            current_password="AdminApi2026Aa",
            request_id=str(uuid4()),
            verification=verify.verification,
        )

    assert _race(engine, [approve, add_identifier]) == ["success", "conflict"]
    assert claim_db.get(ProfileClaim, claim_id, populate_existing=True).status == "approved"
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )
    assert (
        claim_db.get(
            RegistrationVerificationChallenge, challenge.challenge_id, populate_existing=True
        ).consumed_at
        is None
    )
