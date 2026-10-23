from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Connection, Engine, delete, event, func, or_, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.core.transactions import write_transaction
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.identity.readiness import GuardianIssueCode, GuardianReadinessPolicy, is_minor
from app.identity.schemas import GuardianCreate, PersonSummary, StudentCreate
from app.identity.service import IdentityService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

REFERENCE_DATE = date(2026, 7, 27)


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


@dataclass
class ReadinessFixture:
    db: Session
    identity: IdentityService
    accounts: AccountService
    minor_student: PersonSummary
    adult_student: PersonSummary
    sequence: int = 200

    def link_guardian_for(
        self,
        student: PersonSummary,
        *,
        birth_date: date,
        account_status: str | None,
        role_key: str | None,
        deny_people_read: bool = False,
    ) -> GuardianRelationship:
        self.sequence += 1
        guardian = self.identity.create_guardian(
            GuardianCreate(
                full_name=f"判定监护人 {self.sequence}",
                identity_number=make_cn_id(
                    birth_date.strftime("%Y%m%d"),
                    self.sequence,
                ),
                sex="female" if self.sequence % 2 == 0 else "male",
                date_of_birth=birth_date,
                phone=f"139{self.sequence:08d}",
                email=f"guardian-{self.sequence}@example.com",
            )
        )
        valid_from = date(2026, 1, 1)
        if is_minor(birth_date, valid_from):
            # Test-only legacy data: readiness must diagnose invalid rows that predate
            # the public service's guardian age invariant.
            self.db.add(
                GuardianRelationship(
                    guardian_person_id=guardian.id,
                    student_person_id=student.id,
                    relationship="mother",
                    is_primary=True,
                    valid_from=valid_from,
                    valid_until=None,
                )
            )
            self.db.commit()
        else:
            self.identity.add_guardian(
                student.id,
                guardian.id,
                relationship="mother",
                is_primary=True,
                valid_from=valid_from,
                valid_until=None,
            )
        if account_status is not None:
            created = self.accounts.create_account(AccountCreate(person_id=guardian.id))
            account = self.db.get(UserAccount, created.account.id)
            assert account is not None
            account.status = account_status
            if role_key is not None:
                self.db.add(UserRole(user_id=account.id, role_id=ROLES[role_key].id))
            if deny_people_read:
                self.db.add(
                    UserPermissionOverride(
                        user_id=account.id,
                        permission_id=PERMISSIONS["people.read"].id,
                        effect="deny",
                        scope_kind="family",
                        scope_id=None,
                    )
                )
            self.db.commit()
        relationship = self.db.scalar(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id == guardian.id,
                GuardianRelationship.student_person_id == student.id,
                GuardianRelationship.ended_at.is_(None),
            )
        )
        assert relationship is not None
        return relationship

    def link_guardian(
        self,
        *,
        birth_date: date,
        account_status: str | None,
        role_key: str | None,
        deny_people_read: bool = False,
    ) -> GuardianRelationship:
        return self.link_guardian_for(
            self.minor_student,
            birth_date=birth_date,
            account_status=account_status,
            role_key=role_key,
            deny_people_read=deny_people_read,
        )


@pytest.fixture
def readiness_fixture(db: Session) -> ReadinessFixture:
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
    )
    accounts = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: REFERENCE_DATE,
    )
    minor = identity.create_student(
        StudentCreate(
            full_name="待判定未成年学员",
            identity_number=make_cn_id("20120102", 190),
            sex="female",
            date_of_birth=date(2012, 1, 2),
            student_number="READY-MINOR-001",
        )
    )
    adult = identity.create_student(
        StudentCreate(
            full_name="待判定成年学员",
            identity_number=make_cn_id("19900102", 191),
            sex="male",
            date_of_birth=date(1990, 1, 2),
            student_number="READY-ADULT-001",
        )
    )
    return ReadinessFixture(db, identity, accounts, minor, adult)


@contextmanager
def captured_selects(db: Session) -> Iterator[list[str]]:
    bind = db.get_bind()
    assert isinstance(bind, Connection)
    statements: list[str] = []

    def record_statement(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)


def test_missing_or_deleted_target_person_raises_person_not_found(
    readiness_fixture: ReadinessFixture,
) -> None:
    policy = GuardianReadinessPolicy(readiness_fixture.db)

    with pytest.raises(ValueError, match="^person not found$"):
        policy.evaluate_one(uuid4(), REFERENCE_DATE)

    person = readiness_fixture.db.get(Person, readiness_fixture.minor_student.id)
    assert person is not None
    person.deleted_at = datetime(2026, 7, 27, tzinfo=UTC)
    readiness_fixture.db.commit()

    with pytest.raises(ValueError, match="^person not found$"):
        policy.evaluate_one(person.id, REFERENCE_DATE)


def test_minor_without_guardian_is_pending(readiness_fixture: ReadinessFixture) -> None:
    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
    )

    assert result.age_status == "minor"
    assert result.guardian_status == "pending"
    assert result.missing == ("guardian",)
    assert result.issues == ()
    assert result.active_relationship_ids == ()


def test_automatic_disable_is_idempotent(readiness_fixture: ReadinessFixture) -> None:
    created = readiness_fixture.accounts.create_account(
        AccountCreate(
            person_id=readiness_fixture.adult_student.id,
            login_phone="13800138201",
            email="transition@example.com",
        )
    )
    person = readiness_fixture.db.get(Person, readiness_fixture.adult_student.id)
    assert person is not None
    person.date_of_birth = date(2012, 1, 2)
    readiness_fixture.db.commit()
    policy = GuardianReadinessPolicy(readiness_fixture.db)

    first = policy.disable_student_account_if_pending(
        person.id,
        reference_date=REFERENCE_DATE,
        revoked_at=datetime(2026, 7, 27, tzinfo=UTC),
        audit_actor_user_id=created.account.id,
        audit_request_id="first-auto-disable",
    )
    second = policy.disable_student_account_if_pending(
        person.id,
        reference_date=REFERENCE_DATE,
        revoked_at=datetime(2026, 7, 27, tzinfo=UTC),
        audit_actor_user_id=created.account.id,
        audit_request_id="second-auto-disable",
    )

    assert first == created.account.id
    assert second is None
    assert readiness_fixture.db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.resource_id == created.account.id,
            AuditEvent.action == "account.auto_disable.guardian_missing",
        )
    ) == 1


def test_automatic_disable_audit_failure_rolls_back_state(
    readiness_fixture: ReadinessFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = readiness_fixture.accounts.create_account(
        AccountCreate(
            person_id=readiness_fixture.adult_student.id,
            login_phone="13800138202",
            email="rollback@example.com",
        )
    )
    person = readiness_fixture.db.get(Person, readiness_fixture.adult_student.id)
    assert person is not None
    person.date_of_birth = date(2012, 1, 2)
    session = AuthSession(
        user_id=created.account.id,
        token_digest="a" * 64,
        csrf_digest="b" * 64,
        expires_at=datetime(2026, 7, 28, tzinfo=UTC),
        revoked_at=None,
    )
    readiness_fixture.db.add(session)
    readiness_fixture.db.commit()

    def reject_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditService, "append", reject_audit)
    with (
        pytest.raises(RuntimeError, match="audit unavailable"),
        write_transaction(readiness_fixture.db),
    ):
        GuardianReadinessPolicy(readiness_fixture.db).disable_student_account_if_pending(
            person.id,
            reference_date=REFERENCE_DATE,
            revoked_at=datetime(2026, 7, 27, tzinfo=UTC),
            audit_actor_user_id=created.account.id,
            audit_request_id="failed-auto-disable",
        )

    readiness_fixture.db.expire_all()
    account = readiness_fixture.db.get(UserAccount, created.account.id)
    stored_session = readiness_fixture.db.get(AuthSession, session.id)
    assert account is not None and account.status == "active"
    assert stored_session is not None and stored_session.revoked_at is None


def test_concurrent_automatic_disable_emits_one_transition_audit(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    person_id = uuid4()
    account_id = uuid4()
    with Session(postgres_engine) as setup, setup.begin():
        setup.add(
            Person(
                id=person_id,
                full_name="并发自动停用学员",
                identity_ciphertext="concurrent-auto-disable-ciphertext",
                identity_digest=uuid4().hex + uuid4().hex,
                phone_ciphertext=None,
                phone_digest=None,
                email="concurrent-transition@example.com",
                sex="female",
                date_of_birth=date(2012, 1, 2),
                deleted_at=None,
            )
        )
        setup.flush()
        setup.add(
            StudentProfile(
                person_id=person_id,
                student_number=f"CONCURRENT-DISABLE-{person_id.hex[:8]}",
                status="active",
            )
        )
        setup.add(
            UserAccount(
                id=account_id,
                person_id=person_id,
                login_phone_digest=uuid4().hex + uuid4().hex,
                email_normalized="concurrent-transition@example.com",
                password_hash="not-used-by-this-test",
                status="active",
                force_password_change=False,
                failed_login_count=0,
            )
        )

    def cleanup() -> None:
        with Session(postgres_engine) as cleanup_db, cleanup_db.begin():
            cleanup_db.execute(delete(AuthSession).where(AuthSession.user_id == account_id))
            cleanup_db.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            try:
                cleanup_db.execute(
                    delete(AuditEvent).where(
                        or_(
                            AuditEvent.actor_user_id == account_id,
                            AuditEvent.resource_id == account_id,
                        )
                    )
                )
            finally:
                cleanup_db.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            cleanup_db.execute(delete(UserAccount).where(UserAccount.id == account_id))
            cleanup_db.execute(
                delete(StudentProfile).where(StudentProfile.person_id == person_id)
            )
            cleanup_db.execute(delete(Person).where(Person.id == person_id))

    request.addfinalizer(cleanup)

    worker_started = threading.Event()
    worker_done = threading.Event()
    worker_backend_pid: list[int] = []
    worker_results: list[object] = []
    worker_errors: list[Exception] = []

    def disable_in_worker() -> None:
        try:
            with Session(postgres_engine, autoflush=False) as worker, worker.begin():
                backend_pid = worker.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(backend_pid, int)
                worker_backend_pid.append(backend_pid)
                worker_started.set()
                worker_results.append(
                    GuardianReadinessPolicy(worker).disable_student_account_if_pending(
                        person_id,
                        reference_date=REFERENCE_DATE,
                        revoked_at=datetime(2026, 7, 27, 1, tzinfo=UTC),
                        audit_actor_user_id=account_id,
                        audit_request_id="concurrent-auto-disable-second",
                    )
                )
        except Exception as exc:  # noqa: BLE001 - propagate thread failures below
            worker_errors.append(exc)
        finally:
            worker_done.set()

    worker = threading.Thread(target=disable_in_worker)
    first_result = None
    try:
        with Session(postgres_engine, autoflush=False) as first, first.begin():
            first_result = GuardianReadinessPolicy(first).disable_student_account_if_pending(
                person_id,
                reference_date=REFERENCE_DATE,
                revoked_at=datetime(2026, 7, 27, tzinfo=UTC),
                audit_actor_user_id=account_id,
                audit_request_id="concurrent-auto-disable-first",
            )
            worker.start()
            assert worker_started.wait(timeout=2)
            deadline = time.monotonic() + 5
            waiting_on_lock = False
            while time.monotonic() < deadline and not worker_done.is_set():
                with postgres_engine.connect() as observer:
                    wait_event_type = observer.scalar(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid = :backend_pid"
                        ),
                        {"backend_pid": worker_backend_pid[0]},
                    )
                if wait_event_type == "Lock":
                    waiting_on_lock = True
                    break
                time.sleep(0.01)
            assert waiting_on_lock
            assert not worker_done.is_set()
    finally:
        if worker.ident is not None:
            worker.join(timeout=10)

    assert not worker.is_alive()
    assert worker_errors == []
    assert first_result == account_id
    assert worker_results == [None]
    with Session(postgres_engine) as verification:
        assert verification.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.resource_id == account_id,
                AuditEvent.action == "account.auto_disable.guardian_missing",
            )
        ) == 1


def test_minor_requires_an_adult_guardian_with_active_account_and_role(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(2010, 1, 2),
        account_status="active",
        role_key="guardian",
    )

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.guardian_status == "pending"
    assert result.issues[0].relationship_id == relationship.id
    assert result.issues[0].reason_codes == ("guardian_underage",)


def test_permission_override_does_not_change_guardian_readiness(
    readiness_fixture: ReadinessFixture,
) -> None:
    readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
        deny_people_read=True,
    )

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
    )

    assert result.guardian_status == "ready"
    assert result.missing == ()


def test_adult_student_does_not_require_but_keeps_active_relationship(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian_for(
        readiness_fixture.adult_student,
        birth_date=date(1980, 1, 2),
        account_status=None,
        role_key=None,
    )

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.adult_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.age_status == "adult"
    assert result.guardian_status == "not_applicable"
    assert result.missing == ()
    assert result.issues == ()
    assert result.active_relationship_ids == (relationship.id,)


def test_minor_non_student_does_not_require_guardian(
    readiness_fixture: ReadinessFixture,
) -> None:
    person = readiness_fixture.identity.create_guardian(
        GuardianCreate(
            full_name="未成年非学员",
            identity_number=make_cn_id("20120102", 192),
            sex="female",
            date_of_birth=date(2012, 1, 2),
            phone="13900000192",
            email="minor-non-student@example.com",
        )
    )

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        person.id,
        REFERENCE_DATE,
    )

    assert result.age_status == "minor"
    assert result.guardian_status == "not_applicable"
    assert result.missing == ()


@pytest.mark.parametrize(
    ("account_status", "role_key", "expected_reason"),
    [
        (None, None, "guardian_account_missing"),
        ("disabled", "guardian", "guardian_account_disabled"),
        ("active", None, "guardian_role_missing"),
    ],
)
def test_guardian_account_matrix(
    readiness_fixture: ReadinessFixture,
    account_status: str | None,
    role_key: str | None,
    expected_reason: GuardianIssueCode,
) -> None:
    readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status=account_status,
        role_key=role_key,
    )

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.guardian_status == "pending"
    assert result.issues[0].reason_codes == (expected_reason,)


@pytest.mark.parametrize(
    ("state", "expected_reason"),
    [
        ("future", "relationship_not_started"),
        ("expired", "relationship_expired"),
        ("ended", "relationship_ended"),
    ],
)
def test_guardian_relationship_date_matrix(
    readiness_fixture: ReadinessFixture,
    state: str,
    expected_reason: GuardianIssueCode,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    if state == "future":
        relationship.valid_from = date(2026, 7, 28)
    elif state == "expired":
        relationship.valid_until = date(2026, 7, 26)
    else:
        relationship.ended_at = datetime(2026, 7, 27, tzinfo=UTC)
    readiness_fixture.db.commit()

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.guardian_status == "pending"
    assert result.issues[0].reason_codes == (expected_reason,)


def test_ended_relationship_reason_takes_precedence_over_future_date(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    relationship.ended_at = datetime(2026, 7, 27, tzinfo=UTC)
    relationship.valid_from = date(2026, 7, 28)
    readiness_fixture.db.commit()

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.issues[0].reason_codes == ("relationship_ended",)


def test_incomplete_guardian_email_is_not_valid(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    guardian = readiness_fixture.db.get(Person, relationship.guardian_person_id)
    assert guardian is not None
    guardian.email = None
    readiness_fixture.db.commit()

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.guardian_status == "pending"
    assert result.issues[0].reason_codes == ("guardian_profile_incomplete",)


@pytest.mark.parametrize(
    "field_name",
    [
        "identity_ciphertext",
        "identity_digest",
        "phone_ciphertext",
        "phone_digest",
    ],
)
def test_empty_guardian_identity_or_phone_field_is_not_valid(
    readiness_fixture: ReadinessFixture,
    field_name: str,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    guardian = readiness_fixture.db.get(Person, relationship.guardian_person_id)
    assert guardian is not None
    setattr(guardian, field_name, "")
    readiness_fixture.db.commit()

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.guardian_status == "pending"
    assert result.issues[0].reason_codes == ("guardian_profile_incomplete",)


def test_soft_deleted_guardian_is_not_valid(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    guardian = readiness_fixture.db.get(Person, relationship.guardian_person_id)
    assert guardian is not None
    guardian.deleted_at = datetime(2026, 7, 27, tzinfo=UTC)
    readiness_fixture.db.commit()

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.guardian_status == "pending"
    assert result.issues[0].reason_codes == ("guardian_profile_incomplete",)


def test_issue_reason_codes_include_all_failures_in_deterministic_order(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(2010, 1, 2),
        account_status="disabled",
        role_key=None,
    )
    relationship.valid_from = date(2026, 7, 28)
    guardian = readiness_fixture.db.get(Person, relationship.guardian_person_id)
    assert guardian is not None
    guardian.email = None
    readiness_fixture.db.commit()

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.issues[0].reason_codes == (
        "relationship_not_started",
        "guardian_underage",
        "guardian_profile_incomplete",
        "guardian_account_disabled",
    )


def test_valid_until_is_inclusive(readiness_fixture: ReadinessFixture) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    relationship.valid_until = date(2026, 8, 1)
    readiness_fixture.db.commit()

    on_boundary = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        date(2026, 8, 1),
    )
    after_boundary = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        date(2026, 8, 2),
    )

    assert on_boundary.guardian_status == "ready"
    assert after_boundary.guardian_status == "pending"


def test_issue_suppression_does_not_change_readiness(
    readiness_fixture: ReadinessFixture,
) -> None:
    readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status=None,
        role_key=None,
    )
    policy = GuardianReadinessPolicy(readiness_fixture.db)

    suppressed = policy.evaluate_one(readiness_fixture.minor_student.id, REFERENCE_DATE)
    included = policy.evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert suppressed.guardian_status == included.guardian_status == "pending"
    assert suppressed.missing == included.missing == ("guardian",)
    assert suppressed.issues == ()
    assert included.issues[0].reason_codes == ("guardian_account_missing",)


def test_empty_batch_returns_without_queries(readiness_fixture: ReadinessFixture) -> None:
    with captured_selects(readiness_fixture.db) as statements:
        result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_many(
            [],
            student_ids=set(),
            reference_date=REFERENCE_DATE,
        )

    assert result == {}
    assert statements == []


def test_nonempty_minor_batch_without_relationships_uses_one_query(
    readiness_fixture: ReadinessFixture,
) -> None:
    person = readiness_fixture.db.get(Person, readiness_fixture.minor_student.id)
    assert person is not None

    with captured_selects(readiness_fixture.db) as statements:
        result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_many(
            [person],
            student_ids={person.id},
            reference_date=REFERENCE_DATE,
        )

    assert len(statements) == 1
    statement = statements[0].lower()
    assert "guardian_relationships" in statement
    assert "guardian_relationships.ended_at is null" in statement
    assert "guardian_relationships.valid_from <=" in statement
    assert "guardian_relationships.valid_until is null" in statement
    assert "people.identity_ciphertext," not in statement
    assert "people.phone_ciphertext," not in statement
    assert result[person.id].guardian_status == "pending"


def test_relationship_without_account_uses_two_queries(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status=None,
        role_key=None,
    )
    person = readiness_fixture.db.get(Person, readiness_fixture.minor_student.id)
    assert person is not None

    with captured_selects(readiness_fixture.db) as statements:
        result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_many(
            [person],
            student_ids={person.id},
            reference_date=REFERENCE_DATE,
            include_issues=True,
        )

    assert len(statements) == 2
    assert sum("guardian_relationships" in statement for statement in statements) == 1
    assert sum("user_accounts" in statement for statement in statements) == 1
    assert all("user_roles" not in statement for statement in statements)
    assert result[person.id].issues[0].relationship_id == relationship.id
    assert result[person.id].issues[0].reason_codes == ("guardian_account_missing",)


def test_any_valid_guardian_makes_minor_ready(
    readiness_fixture: ReadinessFixture,
) -> None:
    valid = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    invalid = readiness_fixture.link_guardian(
        birth_date=date(1981, 1, 2),
        account_status=None,
        role_key=None,
    )

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        REFERENCE_DATE,
        include_issues=True,
    )

    assert result.guardian_status == "ready"
    assert result.missing == ()
    assert result.active_relationship_ids == (valid.id,)
    assert len(result.issues) == 1
    assert result.issues[0].relationship_id == invalid.id
    assert result.issues[0].reason_codes == ("guardian_account_missing",)


def test_evaluate_many_batches_relationship_account_and_role_queries(
    readiness_fixture: ReadinessFixture,
) -> None:
    minor_relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    adult_relationship = readiness_fixture.link_guardian_for(
        readiness_fixture.adult_student,
        birth_date=date(1981, 1, 2),
        account_status=None,
        role_key=None,
    )
    people = list(
        readiness_fixture.db.scalars(
            select(Person).where(
                Person.id.in_(
                    {
                        readiness_fixture.minor_student.id,
                        readiness_fixture.adult_student.id,
                    }
                )
            )
        )
    )

    with captured_selects(readiness_fixture.db) as statements:
        result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_many(
            people,
            student_ids={person.id for person in people},
            reference_date=REFERENCE_DATE,
        )

    assert len(statements) == 3
    assert sum("guardian_relationships" in statement for statement in statements) == 1
    assert sum("user_accounts" in statement for statement in statements) == 1
    assert sum("user_roles" in statement for statement in statements) == 1
    assert result[readiness_fixture.minor_student.id].active_relationship_ids == (
        minor_relationship.id,
    )
    assert result[readiness_fixture.adult_student.id].active_relationship_ids == (
        adult_relationship.id,
    )
