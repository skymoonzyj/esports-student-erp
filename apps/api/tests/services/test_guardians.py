from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, delete, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.identity import schemas as identity_schemas
from app.identity import service as identity_service_module
from app.identity.models import GuardianProfile, GuardianRelationship, Person, StudentProfile
from app.identity.schemas import GuardianCreate, StudentCreate
from app.identity.service import (
    GuardianCycle,
    GuardianRelationshipAlreadyExists,
    GuardianSelfRelationship,
    IdentityService,
    IdentityServiceError,
    PersonNotFound,
)
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from tests.account_identifiers import add_test_primary_login_identifier


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


def test_mixed_origin_guardian_disable_keeps_assisted_student_and_player_active(db):
    from tests.services.test_account_access_readiness import assisted_account, services, sessions
    from tests.services.test_assisted_eligibility import approved_minor
    from tests.services.test_personnel_approval import platform_users

    users = platform_users.__wrapped__(db)
    first, _, first_edges = approved_minor(db, users)
    player, _, _ = approved_minor(db, users, kind="player", sequence=323)
    old, _, old_edges = approved_minor(db, users, sequence=325)
    old_edges[0].requires_verification = False
    assisted = [assisted_account(db, first), assisted_account(db, player)]
    legacy = UserAccount(person_id=old.person_id, password_hash="synthetic", status="active")
    guardian = UserAccount(person_id=first_edges[0].guardian_person_id, password_hash="synthetic", status="active")
    db.add_all([legacy, guardian])
    db.flush()
    db.add(UserRole(user_id=guardian.id, role_id=ROLES["guardian"].id))
    _, accounts, auth = services(db)
    all_sessions = [sessions(db, account, auth)[1] for account in [*assisted, legacy, guardian]]
    db.commit()
    statements = []
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.lower().split()))
    event.listen(db.bind, "before_cursor_execute", record)
    try:
        accounts.disable_account(guardian.id, actor_user_id=users["accounts"]["admin"].id, request_id="mixed-origin")
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert [account.status for account in [*assisted, legacy, guardian]] == ["active", "active", "disabled", "disabled"]
    assert all(row.revoked_at is None for rows in all_sessions[:2] for row in rows)
    assert all(row.revoked_at is not None for rows in all_sessions[2:] for row in rows)
    assert sum(statement.startswith("update auth_sessions") for statement in statements) == 1
    assert sum("from user_accounts" in statement and "for update" in statement for statement in statements) == 1


@pytest.fixture
def identity_service(db: Session) -> IdentityService:
    return IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(reversed(range(32)))),
    )


@pytest.fixture
def minor_student(identity_service: IdentityService):
    return identity_service.create_student(
        StudentCreate(
            full_name="未成年学员",
            identity_number=make_cn_id("20100727", 102),
            sex="female",
            date_of_birth=date(2010, 7, 27),
            student_number="MINOR-001",
        )
    )


@pytest.fixture
def valid_guardian(identity_service: IdentityService, db: Session):
    guardian = identity_service.create_guardian(
        GuardianCreate(
            full_name="有效监护人",
            identity_number=make_cn_id("19800102", 101),
            sex="male",
            date_of_birth=date(1980, 1, 2),
            phone="13900139001",
            email="guardian@example.com",
        )
    )
    accounts = AccountService(
        db,
        identity_service=identity_service,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 26),
    )
    created = accounts.create_account(AccountCreate(person_id=guardian.id))
    account = db.get(UserAccount, created.account.id)
    assert account is not None
    db.add(UserRole(user_id=account.id, role_id=ROLES["guardian"].id))
    db.commit()
    return guardian


def test_guardian_account_disable_locks_people_before_account_rows(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
    db: Session,
) -> None:
    db.autoflush = False
    identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    accounts = AccountService(
        db,
        identity_service=identity_service,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 27),
    )
    accounts.create_account(
        AccountCreate(
            person_id=minor_student.id,
            login_phone="13900139002",
            email="minor-lock-order@example.com",
        )
    )
    second_minor = identity_service.create_student(
        StudentCreate(
            full_name="第二名未成年学员",
            identity_number=make_cn_id("20110727", 103),
            sex="male",
            date_of_birth=date(2011, 7, 27),
            student_number="MINOR-LOCK-002",
        )
    )
    identity_service.add_guardian(
        second_minor.id,
        valid_guardian.id,
        relationship="father",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    accounts.create_account(
        AccountCreate(
            person_id=second_minor.id,
            login_phone="13900139003",
            email="minor-lock-order-2@example.com",
        )
    )
    guardian_account = db.scalar(
        select(UserAccount).where(UserAccount.person_id == valid_guardian.id)
    )
    assert guardian_account is not None
    bind = db.get_bind()
    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.lower().split()))

    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        accounts.disable_account(
            guardian_account.id,
            actor_user_id=None,
            request_id="guardian-disable-lock-order",
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)

    people_locks = [
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select")
        and " from people " in statement
        and " for update" in statement
    ]
    account_locks = [
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select")
        and " from user_accounts " in statement
        and " for update" in statement
    ]
    session_writes = [
        index
        for index, statement in enumerate(statements)
        if statement.startswith("update auth_sessions")
    ]
    dependent_queries = [
        statement
        for statement in statements
        if statement.startswith("select guardian_relationships.student_person_id")
        and "guardian_relationships.guardian_person_id =" in statement
    ]
    assert people_locks
    assert all("order by people.id" in statements[index] for index in people_locks)
    assert len(dependent_queries) == 1
    assert len(account_locks) == 1
    assert "order by user_accounts.id" in statements[account_locks[0]]
    assert len(session_writes) == 1
    assert max(people_locks) < account_locks[0] < session_writes[0]


def test_add_guardian_joins_sorted_person_lock_protocol(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
    db: Session,
) -> None:
    bind = db.get_bind()
    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.lower().split()))

    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        identity_service.add_guardian(
            minor_student.id,
            valid_guardian.id,
            relationship="mother",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)

    people_locks = [
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select")
        and " from people " in statement
        and " for update" in statement
    ]
    relationship_insert = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("insert into guardian_relationships")
    )
    assert len(people_locks) == 1
    assert "order by people.id" in statements[people_locks[0]]
    assert people_locks[0] < relationship_insert


def test_guardian_invalidation_waits_for_concurrent_relationship_membership(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    lookup = LookupDigest(bytes(reversed(range(32))))
    cipher = EnvelopeCipher(bytes(range(32)))
    passwords = PasswordService()
    with Session(postgres_engine) as setup:
        identity = IdentityService(setup, cipher=cipher, lookup=lookup)
        guardian = identity.create_guardian(
            GuardianCreate(
                full_name="并发监护人",
                identity_number=make_cn_id("19800102", 883),
                sex="male",
                date_of_birth=date(1980, 1, 2),
                phone="13900139880",
                email="concurrent-guardian@example.com",
            )
        )
        student = identity.create_student(
            StudentCreate(
                full_name="并发关系学员",
                identity_number=make_cn_id("20120102", 884),
                sex="female",
                date_of_birth=date(2012, 1, 2),
                student_number="CONCURRENT-GUARDIAN-881",
            )
        )
        accounts = AccountService(
            setup,
            identity_service=identity,
            passwords=passwords,
            reference_date_provider=lambda: date(2026, 7, 27),
        )
        guardian_account_id = accounts.create_account(
            AccountCreate(person_id=guardian.id)
        ).account.id
        with setup.begin():
            setup.add(
                UserRole(
                    user_id=guardian_account_id,
                    role_id=ROLES["guardian"].id,
                )
            )
            student_account = UserAccount(
                person_id=student.id,
                login_phone_digest=lookup.digest("13900139881"),
                email_normalized="concurrent-student@example.com",
                password_hash=passwords.hash("Concurrent26Aa"),
                status="active",
                force_password_change=False,
                failed_login_count=0,
            )
            setup.add(student_account)
            add_test_primary_login_identifier(setup, student_account)
            student_account_id = student_account.id
            guardian_id = guardian.id
            student_id = student.id

    def cleanup() -> None:
        with Session(postgres_engine) as cleanup_db, cleanup_db.begin():
            cleanup_db.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            try:
                cleanup_db.execute(
                    delete(AuditEvent).where(
                        AuditEvent.resource_id.in_(
                            [guardian_account_id, student_account_id]
                        )
                    )
                )
            finally:
                cleanup_db.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            cleanup_db.execute(
                delete(GuardianRelationship).where(
                    GuardianRelationship.guardian_person_id == guardian_id,
                    GuardianRelationship.student_person_id == student_id,
                )
            )
            cleanup_db.execute(
                delete(UserRole).where(UserRole.user_id == guardian_account_id)
            )
            cleanup_db.execute(
                delete(UserAccount).where(
                    UserAccount.id.in_([guardian_account_id, student_account_id])
                )
            )
            cleanup_db.execute(
                delete(StudentProfile).where(StudentProfile.person_id == student_id)
            )
            cleanup_db.execute(
                delete(GuardianProfile).where(GuardianProfile.person_id == guardian_id)
            )
            cleanup_db.execute(delete(Person).where(Person.id.in_([guardian_id, student_id])))

    request.addfinalizer(cleanup)

    relationship_inserted = threading.Event()
    allow_relationship_commit = threading.Event()
    relationship_done = threading.Event()
    invalidation_started = threading.Event()
    invalidation_done = threading.Event()
    invalidation_backend_pid: list[int] = []
    relationship_thread_id: list[int] = []
    errors: list[Exception] = []

    def pause_before_relationship_commit(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            relationship_thread_id
            and threading.get_ident() == relationship_thread_id[0]
            and statement.lower().startswith("insert into guardian_relationships")
        ):
            relationship_inserted.set()
            if not allow_relationship_commit.wait(timeout=10):
                raise TimeoutError("relationship commit was not released")

    def add_relationship() -> None:
        relationship_thread_id.append(threading.get_ident())
        try:
            with Session(postgres_engine, autoflush=False) as worker:
                IdentityService(worker, cipher=cipher, lookup=lookup).add_guardian(
                    student_id,
                    guardian_id,
                    relationship="mother",
                    is_primary=True,
                    valid_from=date(2026, 1, 1),
                    valid_until=None,
                )
        except Exception as exc:  # noqa: BLE001 - propagate thread failures below
            errors.append(exc)
        finally:
            relationship_done.set()

    def invalidate_guardian() -> None:
        try:
            with Session(postgres_engine, autoflush=False) as worker:
                backend_pid = worker.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(backend_pid, int)
                invalidation_backend_pid.append(backend_pid)
                invalidation_started.set()
                identity = IdentityService(worker, cipher=cipher, lookup=lookup)
                AccountService(
                    worker,
                    identity_service=identity,
                    passwords=passwords,
                    reference_date_provider=lambda: date(2026, 7, 27),
                ).disable_account(
                    guardian_account_id,
                    actor_user_id=None,
                    request_id="concurrent-guardian-invalidation",
                )
        except Exception as exc:  # noqa: BLE001 - propagate thread failures below
            errors.append(exc)
        finally:
            invalidation_done.set()

    event.listen(postgres_engine, "after_cursor_execute", pause_before_relationship_commit)
    relationship_worker = threading.Thread(target=add_relationship)
    invalidation_worker = threading.Thread(target=invalidate_guardian)
    try:
        relationship_worker.start()
        assert relationship_inserted.wait(timeout=5)
        invalidation_worker.start()
        assert invalidation_started.wait(timeout=5)
        deadline = time.monotonic() + 5
        waiting_on_lock = False
        while time.monotonic() < deadline and not invalidation_done.is_set():
            with postgres_engine.connect() as observer:
                wait_event_type = observer.scalar(
                    text(
                        "SELECT wait_event_type FROM pg_stat_activity "
                        "WHERE pid = :backend_pid"
                    ),
                    {"backend_pid": invalidation_backend_pid[0]},
                )
            if wait_event_type == "Lock":
                waiting_on_lock = True
                break
            time.sleep(0.01)
        assert waiting_on_lock
        assert not invalidation_done.is_set()
        allow_relationship_commit.set()
    finally:
        allow_relationship_commit.set()
        try:
            if relationship_worker.ident is not None:
                relationship_worker.join(timeout=10)
            if invalidation_worker.ident is not None:
                invalidation_worker.join(timeout=10)
        finally:
            event.remove(
                postgres_engine,
                "after_cursor_execute",
                pause_before_relationship_commit,
            )

    assert not relationship_worker.is_alive()
    assert not invalidation_worker.is_alive()
    assert errors == []
    with Session(postgres_engine) as verification:
        account = verification.get(UserAccount, student_account_id)
        assert account is not None
        assert account.status == "disabled"


def test_minor_without_guardian_is_not_ready(
    identity_service: IdentityService,
    minor_student,
) -> None:
    result = identity_service.readiness(
        minor_student.id,
        reference_date=date(2026, 7, 26),
    )

    assert result.ready is False
    assert result.missing == ["guardian"]


def test_valid_guardian_makes_minor_ready_and_is_listed(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
) -> None:
    identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )

    result = identity_service.readiness(
        minor_student.id,
        reference_date=date(2026, 7, 26),
    )
    guardians = identity_service.list_guardians(
        minor_student.id,
        reference_date=date(2026, 7, 26),
    )

    assert result.ready is True
    assert result.missing == []
    assert [(item.id, item.relationship, item.is_primary) for item in guardians] == [
        (valid_guardian.id, "mother", True)
    ]


def test_guardian_list_projection_has_constant_query_count_for_student_guardians(
    identity_service: IdentityService,
    db: Session,
    record_property,
) -> None:
    small_roster = identity_service.create_student(
        StudentCreate(
            full_name="单监护人学员",
            identity_number=make_cn_id("20120102", 590),
            sex="female",
            date_of_birth=date(2012, 1, 2),
            student_number="GUARDIAN-LIST-SMALL",
        )
    )
    large_roster = identity_service.create_student(
        StudentCreate(
            full_name="多监护人学员",
            identity_number=make_cn_id("20120102", 592),
            sex="female",
            date_of_birth=date(2012, 1, 2),
            student_number="GUARDIAN-LIST-LARGE",
        )
    )
    guardian_students = []
    for index in range(20):
        sequence = 600 + index
        guardian = identity_service.create_student(
            StudentCreate(
                full_name=f"成年学员监护人 {index:02d}",
                identity_number=make_cn_id("19900102", sequence),
                sex="female" if sequence % 2 == 0 else "male",
                date_of_birth=date(1990, 1, 2),
                student_number=f"DUAL-ROLE-{index:02d}",
            )
        )
        guardian_students.append(guardian)
        identity_service.add_guardian(
            large_roster.id,
            guardian.id,
            relationship="guardian",
            is_primary=index == 0,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
    identity_service.add_guardian(
        small_roster.id,
        guardian_students[0].id,
        relationship="guardian",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    account = UserAccount(
        person_id=guardian_students[0].id,
        password_hash="not-used-by-this-read-test",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    db.add_all([
        UserRole(user_id=account.id, role_id=ROLES["team_member"].id),
        UserRole(user_id=account.id, role_id=ROLES["team_manager"].id),
    ])
    db.flush()
    large_guardians = identity_service.list_guardians(
        large_roster.id,
        reference_date=date(2026, 7, 27),
    )
    small_guardians = identity_service.list_guardians(
        small_roster.id,
        reference_date=date(2026, 7, 27),
    )
    assert all(item.person_types == ["student", "guardian"] for item in large_guardians)
    assert small_guardians[0].person_types == ["student", "guardian"]
    assert {role.key for role in small_guardians[0].roles} == {"team_member", "team_manager"}
    assert all(item.team.model_dump() == {"visibility": "denied", "current": None}
               for item in large_guardians)

    def count_queries(student_id: UUID) -> int:
        count = 0

        def before_cursor_execute(*_: object) -> None:
            nonlocal count
            count += 1

        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", before_cursor_execute)
        try:
            identity_service.list_guardians(
                student_id,
                reference_date=date(2026, 7, 27),
            )
        finally:
            event.remove(bind, "before_cursor_execute", before_cursor_execute)
        return count

    large_queries = count_queries(large_roster.id)
    small_queries = count_queries(small_roster.id)
    record_property("large_guardian_queries", large_queries)
    record_property("small_guardian_queries", small_queries)
    assert large_queries == small_queries


def test_expired_or_ended_guardian_does_not_make_minor_ready(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
) -> None:
    relationship = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="father",
        is_primary=False,
        valid_from=date(2025, 1, 1),
        valid_until=date(2025, 12, 31),
    )

    assert identity_service.readiness(
        minor_student.id,
        reference_date=date(2026, 7, 26),
    ).missing == ["guardian"]
    identity_service.end_guardian_relationship(
        minor_student.id,
        relationship.relationship_id,
        ended_by_user_id=None,
        reason="关系到期归档",
        ended_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
        request_id="service-test",
    )
    assert identity_service.list_guardians(
        minor_student.id,
        reference_date=date(2025, 6, 1),
    ) == []


def test_guardian_cannot_be_the_student(
    identity_service: IdentityService,
    minor_student,
) -> None:
    with pytest.raises(GuardianSelfRelationship):
        identity_service.add_guardian(
            minor_student.id,
            minor_student.id,
            relationship="self",
            is_primary=False,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )


def test_overlapping_relationship_race_is_reported_as_existing_relationship(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Diagnostics:
        constraint_name = "excl_guardian_relationships_overlapping_dates"

    class ExclusionViolation(Exception):
        diag = Diagnostics()

    def raise_exclusion_violation(*args: object, **kwargs: object) -> None:
        raise IntegrityError("INSERT", {}, ExclusionViolation())

    monkeypatch.setattr(identity_service.db, "flush", raise_exclusion_violation)

    with pytest.raises(GuardianRelationshipAlreadyExists) as error:
        identity_service.add_guardian(
            minor_student.id,
            valid_guardian.id,
            relationship="mother",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
    assert error.value.code == "guardian_relationship_overlaps"


def test_guardian_relationship_graph_cannot_cycle(identity_service: IdentityService) -> None:
    one = identity_service.create_student(
        StudentCreate(
            full_name="学员甲",
            identity_number=make_cn_id("19800102", 102),
            sex="female",
            date_of_birth=date(1980, 1, 2),
            student_number="CYCLE-001",
        )
    )
    two = identity_service.create_student(
        StudentCreate(
            full_name="学员乙",
            identity_number=make_cn_id("19810102", 104),
            sex="female",
            date_of_birth=date(1981, 1, 2),
            student_number="CYCLE-002",
        )
    )
    identity_service.add_guardian(
        two.id,
        one.id,
        relationship="other",
        is_primary=False,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )

    with pytest.raises(GuardianCycle):
        identity_service.add_guardian(
            one.id,
            two.id,
            relationship="other",
            is_primary=False,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )


def test_ended_relationship_does_not_block_inverse_guardian_relationship(
    identity_service: IdentityService,
) -> None:
    one = identity_service.create_student(
        StudentCreate(
            full_name="历史学员甲",
            identity_number=make_cn_id("19800102", 102),
            sex="female",
            date_of_birth=date(1980, 1, 2),
            student_number="ENDED-CYCLE-001",
        )
    )
    two = identity_service.create_student(
        StudentCreate(
            full_name="历史学员乙",
            identity_number=make_cn_id("19810102", 104),
            sex="female",
            date_of_birth=date(1981, 1, 2),
            student_number="ENDED-CYCLE-002",
        )
    )
    first = identity_service.add_guardian(
        two.id,
        one.id,
        relationship="other",
        is_primary=False,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    identity_service.end_guardian_relationship(
        two.id,
        first.relationship_id,
        ended_by_user_id=None,
        reason="监护安排结束",
        ended_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
        request_id="service-test",
    )

    inverse = identity_service.add_guardian(
        one.id,
        two.id,
        relationship="other",
        is_primary=False,
        valid_from=date(2026, 7, 28),
        valid_until=None,
    )

    assert inverse.relationship_id != first.relationship_id


def test_guardian_without_contact_details_is_not_valid_for_readiness(
    identity_service: IdentityService,
    minor_student,
) -> None:
    contactless = identity_service.create_student(
        StudentCreate(
            full_name="无联系方式人员",
            identity_number=make_cn_id("19850102", 103),
            sex="male",
            date_of_birth=date(1985, 1, 2),
            student_number="CONTACTLESS-001",
        )
    )
    identity_service.add_guardian(
        minor_student.id,
        contactless.id,
        relationship="other",
        is_primary=False,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )

    assert identity_service.readiness(
        minor_student.id,
        reference_date=date(2026, 7, 26),
    ).missing == ["guardian"]


def test_ending_and_rebinding_preserves_guardian_history(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
    db: Session,
) -> None:
    first = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    ended_at = datetime(2026, 7, 27, 2, tzinfo=UTC)
    ended = identity_service.end_guardian_relationship(
        minor_student.id,
        first.relationship_id,
        ended_by_user_id=None,
        reason="监护安排变更",
        ended_at=ended_at,
        request_id="service-test",
    )
    second = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 7, 28),
        valid_until=None,
    )

    history = identity_service.list_guardians(minor_student.id, reference_date=None)
    active_on_end_date = identity_service.list_guardians(
        minor_student.id,
        reference_date=date(2026, 7, 27),
    )
    active_after_rebind = identity_service.list_guardians(
        minor_student.id,
        reference_date=date(2026, 7, 28),
    )

    assert [item.relationship_id for item in history] == [
        second.relationship_id,
        first.relationship_id,
    ]
    assert history[1].status == "ended"
    assert history[1].ended_at == ended_at
    assert history[1].end_reason == "监护安排变更"
    assert ended.status == "ended"
    assert first.relationship_id != second.relationship_id
    assert active_on_end_date == []
    assert [item.relationship_id for item in active_after_rebind] == [
        second.relationship_id
    ]
    assert db.scalar(select(GuardianRelationship).where(GuardianRelationship.id == first.relationship_id))


def test_real_postgresql_overlap_maps_to_public_error_and_ended_pair_can_rebind(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
) -> None:
    first = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
    )

    with pytest.raises(GuardianRelationshipAlreadyExists) as error:
        identity_service.add_guardian(
            minor_student.id,
            valid_guardian.id,
            relationship="father",
            is_primary=False,
            valid_from=date(2026, 6, 1),
            valid_until=None,
        )

    assert error.value.code == "guardian_relationship_overlaps"
    identity_service.end_guardian_relationship(
        minor_student.id,
        first.relationship_id,
        ended_by_user_id=None,
        reason="监护安排变更",
        ended_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
        request_id="service-test",
    )
    rebound = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2027, 1, 1),
        valid_until=None,
    )
    assert rebound.relationship_id != first.relationship_id


def test_guardian_must_be_adult_on_relationship_start(
    identity_service: IdentityService,
    minor_student,
) -> None:
    underage_guardian = identity_service.create_guardian(
        GuardianCreate(
            full_name="未成年监护人",
            identity_number=make_cn_id("20100102", 105),
            sex="male",
            date_of_birth=date(2010, 1, 2),
            phone="13900139005",
            email="underage-guardian@example.com",
        )
    )

    with pytest.raises(IdentityServiceError) as error:
        identity_service.add_guardian(
            minor_student.id,
            underage_guardian.id,
            relationship="sibling",
            is_primary=False,
            valid_from=date(2026, 7, 27),
            valid_until=None,
        )

    assert type(error.value).__name__ == "GuardianUnderage"
    assert error.value.code == "guardian_underage"


def test_relationship_status_has_required_precedence() -> None:
    reference_date = date(2026, 7, 27)
    status = identity_service_module.relationship_status

    assert status(
        GuardianRelationship(
            guardian_person_id=uuid4(),
            student_person_id=uuid4(),
            relationship="mother",
            is_primary=True,
            valid_from=date(2027, 1, 1),
            valid_until=None,
            ended_at=datetime(2026, 7, 1, tzinfo=UTC),
        ),
        reference_date,
    ) == "ended"
    assert status(
        GuardianRelationship(
            guardian_person_id=uuid4(),
            student_person_id=uuid4(),
            relationship="mother",
            is_primary=True,
            valid_from=date(2027, 1, 1),
            valid_until=None,
        ),
        reference_date,
    ) == "scheduled"
    assert status(
        GuardianRelationship(
            guardian_person_id=uuid4(),
            student_person_id=uuid4(),
            relationship="mother",
            is_primary=True,
            valid_from=date(2025, 1, 1),
            valid_until=date(2025, 12, 31),
        ),
        reference_date,
    ) == "expired"
    assert status(
        GuardianRelationship(
            guardian_person_id=uuid4(),
            student_person_id=uuid4(),
            relationship="mother",
            is_primary=True,
            valid_from=reference_date,
            valid_until=reference_date,
        ),
        reference_date,
    ) == "active"


def test_guardian_end_request_strips_reason_and_enforces_length() -> None:
    request_type = identity_schemas.GuardianEndRequest

    assert request_type(reason="  监护安排变更  ").reason == "监护安排变更"
    with pytest.raises(ValidationError):
        request_type(reason=" 一 ")
    with pytest.raises(ValidationError):
        request_type(reason="变" * 201)


def test_end_relationship_rejects_missing_or_mismatched_student(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
) -> None:
    relationship = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    other_student = identity_service.create_student(
        StudentCreate(
            full_name="其他未成年学员",
            identity_number=make_cn_id("20110102", 104),
            sex="female",
            date_of_birth=date(2011, 1, 2),
            student_number="MINOR-OTHER-001",
        )
    )

    with pytest.raises(PersonNotFound):
        identity_service.end_guardian_relationship(
            minor_student.id,
            uuid4(),
            ended_by_user_id=None,
            reason="监护安排变更",
            ended_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
            request_id="service-test",
        )
    with pytest.raises(PersonNotFound):
        identity_service.end_guardian_relationship(
            other_student.id,
            relationship.relationship_id,
            ended_by_user_id=None,
            reason="监护安排变更",
            ended_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
            request_id="service-test",
        )


def test_end_relationship_cannot_overwrite_existing_history(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
    db: Session,
) -> None:
    relationship = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    first_ended_at = datetime(2026, 7, 27, 2, tzinfo=UTC)
    identity_service.end_guardian_relationship(
        minor_student.id,
        relationship.relationship_id,
        ended_by_user_id=None,
        reason="首次监护安排结束",
        ended_at=first_ended_at,
        request_id="service-test",
    )

    with pytest.raises(IdentityServiceError) as error:
        identity_service.end_guardian_relationship(
            minor_student.id,
            relationship.relationship_id,
            ended_by_user_id=None,
            reason="不得覆盖的后续原因",
            ended_at=datetime(2026, 7, 28, 2, tzinfo=UTC),
            request_id="service-test",
        )

    assert type(error.value).__name__ == "GuardianRelationshipAlreadyEnded"
    assert error.value.code == "guardian_relationship_already_ended"
    db.expire_all()
    stored = db.get(GuardianRelationship, relationship.relationship_id)
    assert stored is not None
    assert stored.ended_at == first_ended_at
    assert stored.end_reason == "首次监护安排结束"


def test_history_excludes_soft_deleted_guardian_profiles(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
    db: Session,
) -> None:
    identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    guardian = db.get(Person, valid_guardian.id)
    assert guardian is not None
    guardian.deleted_at = datetime(2026, 7, 27, 2, tzinfo=UTC)
    db.commit()

    assert identity_service.list_guardians(minor_student.id, reference_date=None) == []
