from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.registration.delivery import CapturingVerificationSender
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import RegistrationVerificationChallenge
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationInvalidOrExpired,
    VerificationTarget,
    VerificationTargetMismatch,
)
from app.security.encryption import LookupDigest
from tests.db.test_personnel_approval_concurrency import approval_world as _approval_world
from tests.db.test_personnel_approval_concurrency import committed_world as _committed_world
from tests.db.test_team_concurrency import reload_actor
from tests.services.test_assisted_activation import activation
from tests.services.test_personnel_approval import approvals

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)
LOOKUP = LookupDigest(bytes(range(32)))
EMAIL = "activation-preview@example.com"


@pytest.fixture
def committed_world(postgres_engine):
    yield from _committed_world.__wrapped__(postgres_engine)


@pytest.fixture
def approval_world(postgres_engine, committed_world):
    yield from _approval_world.__wrapped__(postgres_engine, committed_world)


def _service(db, sender):
    return RegistrationVerificationService(
        db,
        lookup=LOOKUP,
        verification_pepper=bytes(reversed(range(32))),
        sender=sender,
        app_env="test",
        clock=lambda: NOW,
    )


def _target(**changes):
    values = {
        "application_id": None,
        "account_id": None,
        "identifier_kind": "email",
        "identifier_digest": registration_identifier_digest(
            LOOKUP, normalize_registration_identifier("email", EMAIL)
        ),
        "purpose": "assisted_activation",
    }
    return VerificationTarget(**(values | changes))


@pytest.fixture
def committed_challenge(postgres_engine):
    sender = CapturingVerificationSender(messages=[])
    with Session(postgres_engine) as setup:
        issued = _service(setup, sender).issue(
            identifier_kind="email",
            identifier=EMAIL,
            purpose="assisted_activation",
            request_source="127.0.0.71",
        )
    try:
        yield issued, sender
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(RegistrationVerificationChallenge).where(
                    RegistrationVerificationChallenge.id == issued.challenge_id
                )
            )


def test_preview_does_not_consume_or_extend_original_challenge(
    postgres_engine, committed_challenge
):
    issued, sender = committed_challenge
    with Session(postgres_engine) as caller:
        checked = _service(caller, sender).verify_unconsumed(
            issued.challenge_id, sender.code_for(issued.challenge_id), expected_target=_target()
        )
        assert checked.challenge_id == issued.challenge_id
        assert checked.expires_at == issued.expires_at
    with Session(postgres_engine) as independent:
        row = independent.get(RegistrationVerificationChallenge, issued.challenge_id)
        assert row.consumed_at is None
        assert row.attempts == 0


def test_preview_five_errors_survive_caller_rollback_and_close_proof(
    postgres_engine, committed_challenge
):
    from app.auth.models import AuthRateLimit

    issued, sender = committed_challenge
    actual = sender.code_for(issued.challenge_id)
    wrong = "000000" if actual != "000000" else "111111"
    for attempt in range(1, 6):
        with Session(postgres_engine) as caller:
            with pytest.raises(RuntimeError, match="outer callback failed"), caller.begin():
                caller.add(
                    AuthRateLimit(
                        key_digest="e" * 64, attempts=1, window_started_at=NOW, updated_at=NOW
                    )
                )
                caller.flush()
                with pytest.raises(VerificationInvalidOrExpired):
                    _service(caller, sender).verify_unconsumed(
                        issued.challenge_id, wrong, expected_target=_target()
                    )
                raise RuntimeError("outer callback failed")
            assert not caller.in_transaction()
        with Session(postgres_engine) as independent:
            assert independent.get(AuthRateLimit, "e" * 64) is None
            assert (
                independent.get(RegistrationVerificationChallenge, issued.challenge_id).attempts
                == attempt
            )
    with Session(postgres_engine) as caller, pytest.raises(VerificationInvalidOrExpired):
        _service(caller, sender).verify_unconsumed(
            issued.challenge_id, actual, expected_target=_target()
        )


def test_uncommitted_challenge_is_not_email_proof(postgres_engine):
    sender = CapturingVerificationSender(messages=[])
    with (
        Session(postgres_engine) as caller,
        pytest.raises(RuntimeError, match="rollback setup"),
        caller.begin(),
    ):
        issued = _service(caller, sender).issue_in_transaction(
            identifier_kind="email",
            identifier=EMAIL,
            purpose="assisted_activation",
            request_source="127.0.0.71",
        )
        with pytest.raises(VerificationInvalidOrExpired):
            _service(caller, sender).verify_unconsumed(
                issued.challenge_id, sender.code_for(issued.challenge_id), expected_target=_target()
            )
        raise RuntimeError("rollback setup")


@pytest.mark.parametrize("changes", [{"purpose": "registration"}, {"identifier_digest": "f" * 64}])
def test_preview_wrong_purpose_or_target_never_proves_or_counts(
    postgres_engine, committed_challenge, changes
):
    issued, sender = committed_challenge
    with Session(postgres_engine) as caller, pytest.raises(VerificationTargetMismatch):
        _service(caller, sender).verify_unconsumed(
            issued.challenge_id,
            sender.code_for(issued.challenge_id),
            expected_target=_target(**changes),
        )
    with Session(postgres_engine) as independent:
        assert independent.get(RegistrationVerificationChallenge, issued.challenge_id).attempts == 0


def test_preview_does_not_wait_for_caller_held_challenge_lock(postgres_engine, committed_challenge):
    from app.registration.verification_service import VerificationBusy

    issued, sender = committed_challenge
    with Session(postgres_engine) as caller, caller.begin():
        caller.scalar(
            select(RegistrationVerificationChallenge)
            .where(RegistrationVerificationChallenge.id == issued.challenge_id)
            .with_for_update()
        )
        with pytest.raises(VerificationBusy):
            _service(caller, sender).verify_unconsumed(
                issued.challenge_id, sender.code_for(issued.challenge_id), expected_target=_target()
            )
        assert caller.scalar(select(1)) == 1


@pytest.mark.parametrize("invalid_state", ["expired", "consumed", "attempts_exhausted"])
def test_preview_rejects_closed_committed_challenges(
    postgres_engine, committed_challenge, invalid_state
):
    issued, sender = committed_challenge
    with Session(postgres_engine) as setup, setup.begin():
        row = setup.get(RegistrationVerificationChallenge, issued.challenge_id)
        if invalid_state == "expired":
            row.expires_at = NOW
        elif invalid_state == "consumed":
            row.consumed_at = NOW
        else:
            row.attempts = 5
    with Session(postgres_engine) as caller, pytest.raises(VerificationInvalidOrExpired):
        _service(caller, sender).verify_unconsumed(
            issued.challenge_id, sender.code_for(issued.challenge_id), expected_target=_target()
        )


@pytest.fixture
def activation_world(postgres_engine, approval_world, request):
    from datetime import date
    from uuid import uuid4

    from sqlalchemy import or_, text

    from app.audit.models import AuditEvent
    from app.auth.models import AuthRateLimit, UserAccount
    from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
    from app.identity.models import GuardianProfile, GuardianRelationship, Person, StaffProfile
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.personnel_intake.schemas import IntakeInput
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.registration.models import RegistrationConsentAcceptance
    from tests.api.conftest import make_cn_id

    ids, _teams, data = approval_world
    minor_kind = getattr(request, "param", None)
    with Session(postgres_engine) as setup:
        prior_limits = set(setup.scalars(select(AuthRateLimit.key_digest)))
        if minor_kind:
            setup.add(StaffProfile(person_id=ids["admin"][1], staff_type="coach"))
            setup.add(UserRole(user_id=ids["admin"][0], role_id=ROLES["coach"].id))
            setup.flush()
            data = IntakeInput.model_validate(
                data.model_dump()
                | {
                    "kind": minor_kind,
                    "identity_number": make_cn_id("20150102", 987),
                    "date_of_birth": date(2015, 1, 2),
                    "team_id": None if minor_kind == "student" else data.team_id,
                    "coach_person_ids": [] if minor_kind == "player" else [ids["admin"][1]],
                    "guardians": [
                        {
                            "full_name": "已批准监护人",
                            "identity_number": make_cn_id("19800102", 701),
                            "phone": "13900000880",
                            "sex": "male",
                            "date_of_birth": date(1980, 1, 2),
                            "relationship": "父亲",
                            "is_primary": True,
                        }
                    ],
                }
            )
        result = approvals(setup).direct_create(reload_actor(setup, ids["admin2"]), data, uuid4())
    try:
        yield result, data, ids
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            account_ids = select(UserAccount.id).where(UserAccount.person_id == result.person_id)
            guardian_ids = list(
                cleanup.scalars(
                    select(GuardianRelationship.guardian_person_id).where(
                        GuardianRelationship.student_person_id == result.person_id
                    )
                )
            )
            room_ids = list(
                cleanup.scalars(
                    select(HousingStay.room_id).where(HousingStay.person_id == result.person_id)
                )
            )
            floor_ids = list(
                cleanup.scalars(select(HousingRoom.floor_id).where(HousingRoom.id.in_(room_ids)))
            )
            building_ids = list(
                cleanup.scalars(
                    select(HousingFloor.building_id).where(HousingFloor.id.in_(floor_ids))
                )
            )
            cleanup.execute(delete(HousingStay).where(HousingStay.person_id == result.person_id))
            cleanup.execute(delete(HousingRoom).where(HousingRoom.id.in_(room_ids)))
            cleanup.execute(delete(HousingFloor).where(HousingFloor.id.in_(floor_ids)))
            cleanup.execute(delete(HousingBuilding).where(HousingBuilding.id.in_(building_ids)))
            challenge_ids = list(
                cleanup.scalars(
                    select(AssistedActivationSession.challenge_id).where(
                        AssistedActivationSession.person_id == result.person_id
                    )
                )
            )
            cleanup.execute(
                delete(AssistedActivationSession).where(
                    AssistedActivationSession.person_id == result.person_id
                )
            )
            cleanup.execute(
                delete(RegistrationVerificationChallenge).where(
                    RegistrationVerificationChallenge.id.in_(challenge_ids)
                )
            )
            cleanup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            cleanup.execute(
                delete(AuditEvent).where(
                    or_(
                        AuditEvent.resource_id == result.person_id,
                        AuditEvent.resource_id.in_(account_ids),
                        AuditEvent.actor_user_id.in_(account_ids),
                    ),
                )
            )
            cleanup.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            cleanup.execute(
                delete(RegistrationConsentAcceptance).where(
                    RegistrationConsentAcceptance.account_id.in_(account_ids)
                )
            )
            cleanup.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
            cleanup.execute(delete(UserAccount).where(UserAccount.person_id == result.person_id))
            if minor_kind:
                cleanup.execute(
                    delete(GuardianRelationship).where(
                        GuardianRelationship.student_person_id == result.person_id
                    )
                )
                cleanup.execute(
                    delete(GuardianProfile).where(GuardianProfile.person_id.in_(guardian_ids))
                )
                cleanup.execute(delete(Person).where(Person.id.in_(guardian_ids)))
                cleanup.execute(
                    delete(StaffProfile).where(StaffProfile.person_id == ids["admin"][1])
                )
            cleanup.execute(
                delete(AuthRateLimit).where(AuthRateLimit.key_digest.not_in(prior_limits))
            )


@pytest.mark.parametrize("activation_world", ["student", "player", "both"], indirect=True)
def test_activation_preserves_minor_person_and_creates_only_verified_email(
    postgres_engine, activation_world
):
    from app.identity.models import GuardianRelationship
    from tests.services.test_assisted_activation import (
        assert_activation_preserves_person_and_creates_only_verified_email,
    )

    result, data, ids = activation_world
    roles = {"student"} if data.kind == "student" else {"team_member"}
    if data.kind == "both":
        roles.add("student")
    with Session(postgres_engine) as db:
        edges = list(
            db.scalars(
                select(GuardianRelationship)
                .where(GuardianRelationship.student_person_id == result.person_id)
                .order_by(GuardianRelationship.id)
            )
        )
        assert_activation_preserves_person_and_creates_only_verified_email(
            db, result, data, edges, ids["admin2"][0], roles
        )


def test_preview_does_not_consume_challenge_and_finish_is_atomic(postgres_engine, activation_world):
    from pydantic import SecretStr

    from app.auth.models import UserAccount
    from app.personnel_intake.activation_models import AssistedActivationSession

    result, data, _ = activation_world
    with Session(postgres_engine) as db:
        service, sender = activation(db)
        issued = service.start(data.email.get_secret_value(), "127.0.6.71")
        token = issued.session_token.get_secret_value()
        row = db.scalar(
            select(AssistedActivationSession).where(
                AssistedActivationSession.person_id == result.person_id
            )
        )
        challenge_id, session_id = row.challenge_id, row.id
        code = SecretStr(sender.code_for(challenge_id))
        preview = service.verify(token, code)
        assert preview.kind == "player" and preview.masked_name != data.full_name
    with Session(postgres_engine) as independent:
        assert independent.get(RegistrationVerificationChallenge, challenge_id).consumed_at is None
        assert independent.get(AssistedActivationSession, session_id).state == "verified"
        assert (
            independent.scalar(
                select(UserAccount.id).where(UserAccount.person_id == result.person_id)
            )
            is None
        )
    with Session(postgres_engine) as db:
        assert activation(db)[0].finish(token, code, SecretStr("SecurePass1")).completed
    with Session(postgres_engine) as independent:
        assert (
            independent.get(RegistrationVerificationChallenge, challenge_id).consumed_at is not None
        )
        assert independent.get(AssistedActivationSession, session_id).state == "completed"
        assert independent.scalar(
            select(UserAccount.id).where(UserAccount.person_id == result.person_id)
        )


def test_committed_verified_proof_survives_callback_and_outer_transaction_failure(
    postgres_engine, activation_world, monkeypatch
):
    from pydantic import SecretStr

    from app.audit.service import AuditService
    from app.auth.models import AccountLoginIdentifier, AuthRateLimit, UserAccount
    from app.personnel_intake.activation_models import AssistedActivationSession

    result, data, _ = activation_world
    with Session(postgres_engine) as setup:
        service, sender = activation(setup)
        token = service.start(
            data.email.get_secret_value(), "127.0.6.75"
        ).session_token.get_secret_value()
        row = setup.scalar(
            select(AssistedActivationSession).where(
                AssistedActivationSession.person_id == result.person_id
            )
        )
        session_id, challenge_id = row.id, row.challenge_id
        code = SecretStr(sender.code_for(challenge_id))
        service.verify(token, code)

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditService, "append", fail)
    with (
        Session(postgres_engine) as caller,
        pytest.raises(RuntimeError, match="audit unavailable"),
        caller.begin(),
    ):
        caller.add(
            AuthRateLimit(key_digest="d" * 64, attempts=1, window_started_at=NOW, updated_at=NOW)
        )
        caller.flush()
        activation(caller)[0].finish(token, code, SecretStr("SecurePass1"))
    with Session(postgres_engine) as independent:
        assert independent.get(AuthRateLimit, "d" * 64) is None
        assert independent.get(AssistedActivationSession, session_id).state == "verified"
        assert independent.get(RegistrationVerificationChallenge, challenge_id).consumed_at is None
        assert (
            independent.scalar(
                select(UserAccount.id).where(UserAccount.person_id == result.person_id)
            )
            is None
        )
        assert not list(
            independent.scalars(
                select(AccountLoginIdentifier)
                .join(UserAccount)
                .where(UserAccount.person_id == result.person_id)
            )
        )


@pytest.fixture
def verified_activation(postgres_engine, activation_world):
    from pydantic import SecretStr

    from app.personnel_intake.activation_models import AssistedActivationSession

    result, data, _ = activation_world
    with Session(postgres_engine) as setup:
        service, sender = activation(setup)
        token = service.start(
            data.email.get_secret_value(), "127.0.6.76"
        ).session_token.get_secret_value()
        row = setup.scalar(
            select(AssistedActivationSession).where(
                AssistedActivationSession.person_id == result.person_id
            )
        )
        session_id, challenge_id = row.id, row.challenge_id
        code = sender.code_for(challenge_id)
        service.verify(token, SecretStr(code))
    return result.person_id, token, code, session_id, challenge_id


def test_invalid_password_leaves_committed_verified_challenge_reusable(
    postgres_engine, verified_activation
):
    from pydantic import SecretStr

    from app.auth.models import UserAccount
    from app.security.passwords import PasswordPolicyError

    person_id, token, code, _, challenge_id = verified_activation
    with Session(postgres_engine) as caller, pytest.raises(PasswordPolicyError):
        activation(caller)[0].finish(token, SecretStr(code), SecretStr("short"))
    with Session(postgres_engine) as independent:
        assert independent.get(RegistrationVerificationChallenge, challenge_id).consumed_at is None
        assert (
            independent.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
            is None
        )
    with Session(postgres_engine) as caller:
        assert (
            activation(caller)[0].finish(token, SecretStr(code), SecretStr("SecurePass1")).completed
        )


def test_corrupt_evidence_is_not_exposed_until_correct_finish_code(
    postgres_engine, verified_activation
):
    from pydantic import SecretStr

    from app.auth.models import UserAccount
    from app.personnel_intake.guardian_records import ApprovalEvidenceError
    from app.security.encryption import EnvelopeCipher

    person_id, token, code, _, challenge_id = verified_activation
    wrong = SecretStr("000000" if code != "000000" else "111111")
    with Session(postgres_engine) as caller:
        service = activation(caller)[0]
        service.cipher = EnvelopeCipher(b"q" * 32)
        with pytest.raises(VerificationInvalidOrExpired):
            service.finish(token, wrong, SecretStr("SecurePass1"))
        with pytest.raises(ApprovalEvidenceError):
            service.finish(token, SecretStr(code), SecretStr("SecurePass1"))
    with Session(postgres_engine) as independent:
        assert independent.get(RegistrationVerificationChallenge, challenge_id).attempts == 1
        assert independent.get(RegistrationVerificationChallenge, challenge_id).consumed_at is None
        assert (
            independent.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
            is None
        )


def test_finish_five_wrong_guesses_survive_explicit_caller_rollback(
    postgres_engine, verified_activation
):
    from pydantic import SecretStr

    from app.auth.models import AuthRateLimit, UserAccount
    from app.personnel_intake.activation_models import AssistedActivationSession

    person_id, token, code, session_id, challenge_id = verified_activation
    wrong = SecretStr("000000" if code != "000000" else "111111")
    for attempt in range(1, 6):
        with (
            Session(postgres_engine) as caller,
            pytest.raises(RuntimeError, match="rollback caller"),
            caller.begin(),
        ):
            caller.add(
                AuthRateLimit(
                    key_digest="c" * 64, attempts=1, window_started_at=NOW, updated_at=NOW
                )
            )
            caller.flush()
            with pytest.raises(VerificationInvalidOrExpired):
                activation(caller)[0].finish(token, wrong, SecretStr("SecurePass1"))
            raise RuntimeError("rollback caller")
        with Session(postgres_engine) as independent:
            assert independent.get(AuthRateLimit, "c" * 64) is None
            assert (
                independent.get(RegistrationVerificationChallenge, challenge_id).attempts == attempt
            )
            assert (
                independent.get(RegistrationVerificationChallenge, challenge_id).consumed_at is None
            )
            assert independent.get(AssistedActivationSession, session_id).state == "verified"
            assert (
                independent.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
                is None
            )
    with Session(postgres_engine) as caller, pytest.raises(VerificationInvalidOrExpired):
        activation(caller)[0].finish(token, SecretStr(code), SecretStr("SecurePass1"))
    with Session(postgres_engine) as independent:
        assert independent.get(RegistrationVerificationChallenge, challenge_id).attempts == 5


def test_wrong_finish_guess_never_commits_dirty_autobegin_caller_state(
    postgres_engine, verified_activation
):
    from pydantic import SecretStr

    from app.auth.models import AuthRateLimit, UserAccount

    person_id, token, code, _, challenge_id = verified_activation
    wrong = SecretStr("000000" if code != "000000" else "111111")
    with Session(postgres_engine) as caller:
        caller.add(
            AuthRateLimit(key_digest="b" * 64, attempts=1, window_started_at=NOW, updated_at=NOW)
        )
        caller_transaction = caller.get_transaction()
        with pytest.raises(VerificationInvalidOrExpired):
            activation(caller)[0].finish(token, wrong, SecretStr("SecurePass1"))
        with Session(postgres_engine) as independent:
            assert independent.get(AuthRateLimit, "b" * 64) is None
            assert independent.get(RegistrationVerificationChallenge, challenge_id).attempts == 1
            assert (
                independent.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
                is None
            )
        assert caller.get_transaction() is caller_transaction
        caller.rollback()
    with Session(postgres_engine) as independent:
        assert independent.get(AuthRateLimit, "b" * 64) is None
        assert independent.get(RegistrationVerificationChallenge, challenge_id).attempts == 1


def test_parallel_finish_replays_create_exactly_one_account(postgres_engine, activation_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from pydantic import SecretStr
    from sqlalchemy import func

    from app.audit.models import AuditEvent
    from app.auth.models import UserAccount
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.registration.verification_service import VerificationBusy

    result, data, _ = activation_world
    with Session(postgres_engine) as db:
        service, sender = activation(db)
        token = service.start(
            data.email.get_secret_value(), "127.0.6.72"
        ).session_token.get_secret_value()
        row = db.scalar(
            select(AssistedActivationSession).where(
                AssistedActivationSession.person_id == result.person_id
            )
        )
        code = SecretStr(sender.code_for(row.challenge_id))
        service.verify(token, code)
    barrier = Barrier(2)

    def finish():
        with Session(postgres_engine) as db:
            barrier.wait(5)
            try:
                return activation(db)[0].finish(token, code, SecretStr("SecurePass1")).completed
            except VerificationBusy as error:
                assert type(error) is VerificationBusy
                return "retry"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(finish) for _ in range(2)]
        outcomes = [future.result(20) for future in futures]
        assert outcomes in ([True, True], [True, "retry"], ["retry", True])
    # Both workers have finished, so the winner's account transaction is durable.
    # A caller that received only the explicit retryable contention can replay
    # the same completed token; no sleeps or broad exception allowance.
    for outcome in outcomes:
        if outcome == "retry":
            with Session(postgres_engine) as db:
                assert activation(db)[0].finish(token, code, SecretStr("SecurePass1")).completed
    with Session(postgres_engine) as db:
        assert (
            activation(db)[0]
            .finish(token, SecretStr("not-a-code"), SecretStr("SecurePass1"))
            .completed
        )
    with Session(postgres_engine) as independent:
        assert (
            independent.scalar(
                select(func.count())
                .select_from(UserAccount)
                .where(UserAccount.person_id == result.person_id)
            )
            == 1
        )
        assert (
            independent.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.resource_id == result.person_id,
                    AuditEvent.action == "profile_activation.completed",
                )
            )
            == 1
        )


def test_finish_held_challenge_is_retryable_then_completes_after_release(
    postgres_engine, verified_activation
):
    from pydantic import SecretStr

    from app.auth.models import UserAccount
    from app.registration.verification_service import VerificationBusy

    person_id, token, code, _, challenge_id = verified_activation
    with Session(postgres_engine) as locker, locker.begin():
        locker.scalar(
            select(RegistrationVerificationChallenge)
            .where(RegistrationVerificationChallenge.id == challenge_id)
            .with_for_update()
        )
        with Session(postgres_engine) as caller, pytest.raises(VerificationBusy):
            activation(caller)[0].finish(token, SecretStr(code), SecretStr("SecurePass1"))
        assert locker.scalar(select(1)) == 1
        with Session(postgres_engine) as independent:
            assert independent.get(RegistrationVerificationChallenge, challenge_id).attempts == 0
            assert (
                independent.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
                is None
            )
    with Session(postgres_engine) as caller:
        assert (
            activation(caller)[0].finish(token, SecretStr(code), SecretStr("SecurePass1")).completed
        )
    with Session(postgres_engine) as independent:
        assert independent.get(RegistrationVerificationChallenge, challenge_id).attempts == 0
        assert (
            independent.get(RegistrationVerificationChallenge, challenge_id).consumed_at is not None
        )


def test_email_correction_and_finish_have_one_legal_winner(postgres_engine, activation_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import uuid4

    from pydantic import SecretStr

    from app.auth.models import AccountLoginIdentifier, UserAccount
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.personnel_intake.errors import PersonnelIntakeError
    from app.personnel_intake.schemas import AssistedProfileCorrection, IntakeInput

    result, data, ids = activation_world
    with Session(postgres_engine) as db:
        service, sender = activation(db)
        token = service.start(
            data.email.get_secret_value(), "127.0.6.73"
        ).session_token.get_secret_value()
        row = db.scalar(
            select(AssistedActivationSession).where(
                AssistedActivationSession.person_id == result.person_id
            )
        )
        code = SecretStr(sender.code_for(row.challenge_id))
        service.verify(token, code)
    correction = AssistedProfileCorrection(
        **{
            key: getattr(data, key)
            for key in AssistedProfileCorrection.model_fields
            if key in IntakeInput.model_fields
        }
        | {"email": "changed-race@example.com"},
        expected_version=1,
        reason="并发更正邮箱",
        idempotency_key=uuid4(),
    )
    barrier = Barrier(2)

    def run(change):
        with Session(postgres_engine) as db:
            barrier.wait(5)
            try:
                if change:
                    approvals(db).correct_profile(
                        reload_actor(db, ids["admin2"]), result.person_id, correction
                    )
                else:
                    activation(db)[0].finish(token, code, SecretStr("SecurePass1"))
                return "success"
            except (PersonnelIntakeError, VerificationInvalidOrExpired):
                return "rejected"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, change) for change in (False, True)]
        assert sorted(future.result(20) for future in futures) == ["rejected", "success"]
    with Session(postgres_engine) as independent:
        account = independent.scalar(
            select(UserAccount).where(UserAccount.person_id == result.person_id)
        )
        if account:
            assert (
                len(
                    list(
                        independent.scalars(
                            select(AccountLoginIdentifier).where(
                                AccountLoginIdentifier.account_id == account.id
                            )
                        )
                    )
                )
                == 1
            )


def test_actual_http_round_trip_sets_only_activation_cookie_and_no_login(
    postgres_engine, activation_world, test_settings
):
    from fastapi.testclient import TestClient

    from app.auth.models import AuthSession, UserAccount
    from app.core.database import get_db
    from app.main import create_app
    from app.personnel_intake.activation_models import AssistedActivationSession

    result, data, _ = activation_world
    sender = CapturingVerificationSender(messages=[])
    app = create_app(
        test_settings.model_copy(update={"erp_web_base_url": "https://erp.example.com"})
    )
    app.state.registration_verification_sender = sender

    def request_db():
        with Session(postgres_engine) as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    with TestClient(app, base_url="https://erp.example.com") as client:
        start = client.post(
            "/api/v1/auth/profile-activation/start",
            json={"email": data.email.get_secret_value()},
            headers={"origin": "https://erp.example.com"},
        )
        assert start.status_code == 200
        assert set(start.json()) == {"accepted", "resend_available_at", "expires_at"}
        assert "erp_session" not in start.headers.get("set-cookie", "")
        with Session(postgres_engine) as check:
            row = check.scalar(
                select(AssistedActivationSession).where(
                    AssistedActivationSession.person_id == result.person_id
                )
            )
            code = sender.code_for(row.challenge_id)
        preview = client.post("/api/v1/auth/profile-activation/verify", json={"code": code})
        assert preview.status_code == 200 and preview.json()["kind"] == "player"
        complete = client.post(
            "/api/v1/auth/profile-activation/complete",
            json={"code": code, "password": "SecurePass1"},
        )
        assert complete.status_code == 200 and complete.json() == {"completed": True}
        assert "erp_session" not in complete.headers.get("set-cookie", "")
        assert complete.headers["cache-control"] == "no-store, private"
    with Session(postgres_engine) as check:
        account = check.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
        assert account.account_access_origin == "assisted_activation"
        assert not list(check.scalars(select(AuthSession).where(AuthSession.user_id == account.id)))


def test_old_registration_and_activation_cannot_create_duplicate_accounts(
    postgres_engine, activation_world
):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Barrier
    from uuid import uuid4

    from pydantic import SecretStr
    from sqlalchemy import func, text

    from app.audit.models import AuditEvent
    from app.auth.models import UserAccount
    from app.identity.models import Person
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.registration.consents import registration_terms_digest
    from app.registration.finalization_service import (
        RegistrationFinalizationError,
        RegistrationFinalizationService,
    )
    from app.registration.models import RegistrationApplication, RegistrationConsentAcceptance
    from app.security.encryption import EnvelopeCipher
    from app.security.passwords import PasswordService

    result, data, _ = activation_world
    cipher = EnvelopeCipher(bytes(range(32)))
    email = data.email.get_secret_value()
    with Session(postgres_engine) as db:
        service, sender = activation(db)
        token = service.start(email, "127.0.6.74").session_token.get_secret_value()
        proof = db.scalar(
            select(AssistedActivationSession).where(
                AssistedActivationSession.person_id == result.person_id
            )
        )
        code = SecretStr(sender.code_for(proof.challenge_id))
        service.verify(token, code)
    with Session(postgres_engine) as setup, setup.begin():
        person = setup.get(Person, result.person_id)
        person_count = setup.scalar(select(func.count()).select_from(Person))
        application = RegistrationApplication(
            primary_purpose="student",
            state="ready_to_finalize",
            verified_identifier_kind="email",
            verified_identifier_digest=registration_identifier_digest(
                LOOKUP, normalize_registration_identifier("email", email)
            ),
            verified_identifier_ciphertext=cipher.encrypt(
                email, field_name="registration_identifier"
            ),
            password_hash=PasswordService().hash("SecurePass1"),
            full_name=data.full_name,
            identity_ciphertext=cipher.encrypt(
                data.identity_number.get_secret_value(), field_name="registration_identity"
            ),
            identity_digest=person.identity_digest,
            sex=person.sex,
            date_of_birth=person.date_of_birth,
            expires_at=NOW + timedelta(days=30),
        )
        setup.add(application)
        setup.flush()
        application_id = application.id
        setup.add(
            RegistrationConsentAcceptance(
                registration_application_id=application_id,
                consent_type="registration_terms",
                policy_version="2026-08",
                content_digest=registration_terms_digest("2026-08"),
                accepted_at=NOW,
                request_id=str(uuid4()),
                source="self_registration",
            )
        )
    barrier = Barrier(2)

    def finish(legacy):
        with Session(postgres_engine) as db:
            barrier.wait(5)
            try:
                if legacy:
                    RegistrationFinalizationService(
                        db, cipher=cipher, lookup=LOOKUP, clock=lambda: NOW
                    ).finalize(application_id, request_id=str(uuid4()))
                else:
                    activation(db)[0].finish(token, code, SecretStr("SecurePass1"))
                return "success"
            except (RegistrationFinalizationError, VerificationInvalidOrExpired):
                return "rejected"

    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(finish, legacy) for legacy in (False, True)]
            outcomes = [future.result(20) for future in futures]
        assert outcomes.count("success") == 1
        with Session(postgres_engine) as check:
            assert check.scalar(select(func.count()).select_from(Person)) == person_count
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(UserAccount)
                    .where(UserAccount.person_id == result.person_id)
                )
                == 1
            )
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            cleanup.execute(delete(AuditEvent).where(AuditEvent.resource_id == application_id))
            cleanup.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            cleanup.execute(
                delete(RegistrationConsentAcceptance).where(
                    RegistrationConsentAcceptance.registration_application_id == application_id
                )
            )
            cleanup.execute(
                delete(RegistrationApplication).where(RegistrationApplication.id == application_id)
            )
