from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, event, func, or_, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.models import AuthRateLimit, AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import (
    AccountService,
    AuthContext,
    AuthService,
    InvalidCredentials,
    InvalidSession,
)
from app.identity.models import Person, StudentProfile
from app.identity.readiness import GuardianReadinessPolicy
from app.identity.schemas import StudentCreate
from app.identity.service import IdentityService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


@pytest.fixture
def active_account(db: Session):
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
    )
    person = identity.create_student(
        StudentCreate(
            full_name="登录测试学员",
            identity_number=make_cn_id("19950102", 102),
            sex="female",
            date_of_birth=date(1995, 1, 2),
            student_number="AUTH-001",
        )
    )
    accounts = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 26),
    )
    created = accounts.create_account(
        AccountCreate(
            person_id=person.id,
            login_phone="13800138000",
            email="auth@example.com",
        )
    )
    return {
        "id": created.account.id,
        "phone": "13800138000",
        "password": created.temporary_password,
        "accounts": accounts,
    }


def login(client: TestClient, active_account, *, password: str | None = None):
    return client.post(
        "/api/v1/auth/login",
        json={
            "phone": active_account["phone"],
            "password": password or active_account["password"],
        },
    )


def test_api_login_accepts_email_identifier(
    db_client: TestClient,
    active_account,
) -> None:
    response = db_client.post(
        "/api/v1/auth/login",
        json={
            "identifier": "AUTH@EXAMPLE.COM",
            "password": active_account["password"],
        },
    )

    assert response.status_code == 200
    assert response.json()["user"]["account_id"] == str(active_account["id"])


@dataclass(frozen=True)
class CommittedAccountScenario:
    account_id: UUID
    person_id: UUID
    phone: str
    password: str


@contextmanager
def committed_account_scenario(
    engine: Engine,
    *,
    sequence: int,
) -> Iterator[CommittedAccountScenario]:
    phone = f"139{sequence:08d}"
    password = "ConcurA1old26"
    identity_number = make_cn_id("19950102", sequence)
    lookup = LookupDigest(bytes(range(32)))
    cipher = EnvelopeCipher(bytes(range(32)))
    with Session(engine) as setup, setup.begin():
        person = Person(
            full_name=f"并发密码测试学员 {sequence}",
            identity_ciphertext=cipher.encrypt(identity_number, field_name="identity_number"),
            identity_digest=lookup.digest(identity_number),
            phone_ciphertext=cipher.encrypt(phone, field_name="phone"),
            phone_digest=lookup.digest(phone),
            email=f"concurrent-{sequence}@example.com",
            sex="female",
            date_of_birth=date(1995, 1, 2),
            deleted_at=None,
        )
        setup.add(person)
        setup.flush()
        setup.add(
            StudentProfile(
                person_id=person.id,
                student_number=f"CONCURRENT-{sequence}",
                status="active",
            )
        )
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=lookup.digest(phone),
            email_normalized=person.email,
            password_hash=PasswordService().hash(password),
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        setup.add(account)
        setup.flush()
        scenario = CommittedAccountScenario(account.id, person.id, phone, password)
    try:
        yield scenario
    finally:
        with Session(engine) as cleanup, cleanup.begin():
            cleanup.execute(delete(AuthSession).where(AuthSession.user_id == scenario.account_id))
            cleanup.execute(delete(AuthRateLimit))
            cleanup.execute(
                delete(AuditEvent).where(
                    or_(
                        AuditEvent.actor_user_id == scenario.account_id,
                        AuditEvent.resource_id == scenario.account_id,
                    )
                )
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id == scenario.account_id))
            cleanup.execute(
                delete(StudentProfile).where(StudentProfile.person_id == scenario.person_id)
            )
            cleanup.execute(delete(Person).where(Person.id == scenario.person_id))


def wait_for_backend_lock(
    engine: Engine,
    backend_pid: int,
    completed: threading.Event,
    *,
    timeout: float = 5,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not completed.is_set():
        with engine.connect() as observer:
            wait_event_type = observer.scalar(
                text(
                    "SELECT wait_event_type FROM pg_stat_activity "
                    "WHERE pid = :backend_pid"
                ),
                {"backend_pid": backend_pid},
            )
        if wait_event_type == "Lock":
            return True
        time.sleep(0.01)
    return False


@contextmanager
def captured_statements(db: Session) -> Iterator[list[str]]:
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
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)


def person_lock_indices(statements: list[str]) -> list[int]:
    return [
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select")
        and "people" in statement
        and " for update" in statement
    ]


def test_login_locks_person_once_before_account_and_session(
    active_account,
    db: Session,
) -> None:
    service = AuthService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: datetime(2026, 7, 27, 2, tzinfo=UTC),
    )

    with captured_statements(db) as statements:
        service.login(
            phone=active_account["phone"],
            password=active_account["password"],
            ip_address="127.0.0.1",
            request_id="login-lock-order",
        )

    person_locks = person_lock_indices(statements)
    account_lock = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select")
        and " from user_accounts " in statement
        and " for update" in statement
    )
    session_write = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("insert into auth_sessions")
    )
    assert len(person_locks) == 1
    assert person_locks[0] < account_lock < session_write


def test_login_accepts_email_identifier_case_insensitively(
    active_account,
    db: Session,
) -> None:
    service = AuthService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: datetime(2026, 7, 27, 2, tzinfo=UTC),
    )

    result = service.login(
        identifier="  AUTH@EXAMPLE.COM ",
        password=active_account["password"],
        ip_address="127.0.0.1",
        request_id="email-login-identifier",
    )

    assert result.context.account.id == active_account["id"]


def test_authenticate_locks_person_once_before_account_and_session(
    active_account,
    db: Session,
) -> None:
    service = AuthService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: datetime(2026, 7, 27, 2, tzinfo=UTC),
    )
    login_result = service.login(
        phone=active_account["phone"],
        password=active_account["password"],
        ip_address="127.0.0.1",
        request_id="authenticate-lock-setup",
    )

    with captured_statements(db) as statements:
        service.authenticate(
            login_result.session_token,
            request_id="authenticate-lock-order",
        )

    person_locks = person_lock_indices(statements)
    account_lock = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select")
        and " from user_accounts " in statement
        and " for update" in statement
    )
    session_lock = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select")
        and " from auth_sessions " in statement
        and " for update" in statement
    )
    assert len(person_locks) == 1
    assert person_locks[0] < account_lock < session_lock


def test_readiness_policy_relocks_people_in_a_new_transaction(
    active_account,
    db: Session,
) -> None:
    person_id = db.scalar(
        select(UserAccount.person_id).where(UserAccount.id == active_account["id"])
    )
    assert person_id is not None
    db.commit()
    policy = GuardianReadinessPolicy(db)

    with captured_statements(db) as statements:
        with db.begin():
            policy.lock_students([person_id])
        with db.begin():
            policy.lock_students([person_id])

    assert len(person_lock_indices(statements)) == 2


def test_readiness_policy_relocks_people_after_savepoint_rollback(
    active_account,
    db: Session,
) -> None:
    person_id = db.scalar(
        select(UserAccount.person_id).where(UserAccount.id == active_account["id"])
    )
    assert person_id is not None
    db.commit()
    policy = GuardianReadinessPolicy(db)

    with captured_statements(db) as statements, db.begin():
        savepoint = db.begin_nested()
        policy.lock_students([person_id])
        savepoint.rollback()
        policy.lock_students([person_id])

    assert len(person_lock_indices(statements)) == 2


def test_login_sets_http_only_cookie_and_requires_csrf(
    db_client: TestClient,
    active_account,
) -> None:
    signed_in = login(db_client, active_account)

    assert signed_in.status_code == 200
    assert "HttpOnly" in signed_in.headers["set-cookie"]
    assert "SameSite=lax" in signed_in.headers["set-cookie"]
    assert signed_in.headers["cache-control"] == "no-store, private"
    assert signed_in.json()["user"]["force_password_change"] is True
    rejected = db_client.post("/api/v1/auth/logout")
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "csrf_invalid"
    accepted = db_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
    )
    assert accepted.status_code == 204
    assert db_client.get("/api/v1/auth/me").status_code == 401


def test_erp_session_cookie_is_scoped_to_api_and_logout_clears_legacy_root(
    db_client: TestClient,
    active_account,
) -> None:
    signed_in = login(db_client, active_account)
    assert signed_in.status_code == 200
    login_cookies = signed_in.headers.get_list("set-cookie")
    assert any(
        "erp_session=" in value
        and "Path=/api" in value
        and "Max-Age=0" not in value
        for value in login_cookies
    )
    assert any(
        "erp_session=" in value
        and "Path=/" in value
        and "Path=/api" not in value
        and "Max-Age=0" in value
        for value in login_cookies
    )

    api_cookie = db_client.build_request(
        "GET", "/api/v1/auth/me"
    ).headers.get("cookie", "")
    embedded_cookie = db_client.build_request(
        "GET", "/evaluation-tool/"
    ).headers.get("cookie", "")
    assert "erp_session=" in api_cookie
    assert "erp_session=" not in embedded_cookie

    logged_out = db_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
    )
    assert logged_out.status_code == 204
    logout_cookies = logged_out.headers.get_list("set-cookie")
    assert any("Path=/api" in value and "Max-Age=0" in value for value in logout_cookies)
    assert any(
        "Path=/" in value
        and "Path=/api" not in value
        and "Max-Age=0" in value
        for value in logout_cookies
    )


def test_api_session_wins_when_legacy_root_cookie_coexists_and_root_is_removed(
    db_client: TestClient,
    active_account,
) -> None:
    signed_in = login(db_client, active_account)
    assert signed_in.status_code == 200
    api_cookie = next(
        cookie
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )
    legacy_token = api_cookie.value
    replacement_login = login(db_client, active_account)
    assert replacement_login.status_code == 200
    preferred_cookie = next(
        cookie
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )
    assert preferred_cookie.value != legacy_token
    db_client.cookies.set(
        "erp_session",
        legacy_token,
        domain=api_cookie.domain,
        path="/",
    )

    response = db_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert any(
        "Path=/" in value
        and "Path=/api" not in value
        and "Max-Age=0" in value
        for value in response.headers.get_list("set-cookie")
    )
    assert all(
        cookie.path != "/"
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session"
    )
    assert any(
        cookie.value == preferred_cookie.value
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )
    assert "erp_session=" not in db_client.build_request(
        "GET", "/evaluation-tool/"
    ).headers.get("cookie", "")


def test_path_ambiguous_duplicate_session_does_not_overwrite_api_cookie(
    db_client: TestClient,
    active_account,
) -> None:
    first_login = login(db_client, active_account)
    assert first_login.status_code == 200
    legacy_token = next(
        cookie.value
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )
    second_login = login(db_client, active_account)
    assert second_login.status_code == 200
    preferred_cookie = next(
        cookie
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )
    assert preferred_cookie.value != legacy_token

    response = db_client.get(
        "/api/v1/auth/me",
        headers={
            "Cookie": (
                f"erp_session={legacy_token}; "
                f"erp_session={preferred_cookie.value}"
            )
        },
    )

    assert response.status_code == 200
    response_cookies = response.headers.get_list("set-cookie")
    assert not any(
        "erp_session=" in value
        and "Path=/api" in value
        and "Max-Age=0" not in value
        for value in response_cookies
    )
    assert any(
        "Path=/" in value
        and "Path=/api" not in value
        and "Max-Age=0" in value
        for value in response_cookies
    )
    assert any(
        cookie.value == preferred_cookie.value
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )


@pytest.mark.parametrize("empty_cookie_first", [True, False])
def test_empty_duplicate_session_does_not_overwrite_api_cookie(
    db_client: TestClient,
    active_account,
    *,
    empty_cookie_first: bool,
) -> None:
    first_login = login(db_client, active_account)
    assert first_login.status_code == 200
    legacy_token = next(
        cookie.value
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )
    second_login = login(db_client, active_account)
    assert second_login.status_code == 200
    preferred_cookie = next(
        cookie
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )
    assert preferred_cookie.value != legacy_token
    cookie_parts = ["erp_session=", f"erp_session={legacy_token}"]
    if not empty_cookie_first:
        cookie_parts.reverse()

    response = db_client.get(
        "/api/v1/auth/me",
        headers={"Cookie": "; ".join(cookie_parts)},
    )

    assert response.status_code == 200
    response_cookies = response.headers.get_list("set-cookie")
    assert not any(
        "erp_session=" in value
        and "Path=/api" in value
        and "Max-Age=0" not in value
        for value in response_cookies
    )
    assert any(
        "Path=/" in value
        and "Path=/api" not in value
        and "Max-Age=0" in value
        for value in response_cookies
    )
    assert any(
        cookie.value == preferred_cookie.value
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )


def test_legacy_root_session_is_migrated_to_api_cookie(
    db_client: TestClient,
    active_account,
) -> None:
    signed_in = login(db_client, active_account)
    assert signed_in.status_code == 200
    api_cookie = next(
        cookie
        for cookie in db_client.cookies.jar
        if cookie.name == "erp_session" and cookie.path == "/api"
    )
    db_client.cookies.delete(
        "erp_session",
        domain=api_cookie.domain,
        path="/api",
    )
    db_client.cookies.set(
        "erp_session",
        api_cookie.value,
        domain=api_cookie.domain,
        path="/",
    )

    migrated = db_client.get("/api/v1/auth/me")

    assert migrated.status_code == 200
    migrated_cookies = migrated.headers.get_list("set-cookie")
    assert any(
        f"erp_session={api_cookie.value}" in value
        and "Path=/api" in value
        and "Max-Age=0" not in value
        and "HttpOnly" in value
        and "SameSite=lax" in value
        and "Secure" not in value
        for value in migrated_cookies
    )
    assert any(
        "Path=/" in value
        and "Path=/api" not in value
        and "Max-Age=0" in value
        for value in migrated_cookies
    )
    assert db_client.get("/api/v1/auth/me").status_code == 200
    assert "erp_session=" not in db_client.build_request(
        "GET", "/evaluation-tool/"
    ).headers.get("cookie", "")


def test_csrf_rotation_invalidates_previous_token(
    db_client: TestClient,
    active_account,
) -> None:
    signed_in = login(db_client, active_account)
    old_token = signed_in.json()["csrf_token"]
    rotated = db_client.get("/api/v1/auth/csrf")

    assert rotated.status_code == 200
    assert rotated.json()["csrf_token"] != old_token
    assert (
        db_client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": old_token},
        ).status_code
        == 403
    )
    assert (
        db_client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": rotated.json()["csrf_token"]},
        ).status_code
        == 204
    )


def test_disabling_account_revokes_all_sessions(
    db_client: TestClient,
    active_account,
) -> None:
    assert login(db_client, active_account).status_code == 200

    active_account["accounts"].disable_account(
        active_account["id"],
        actor_user_id=None,
        request_id="service-test",
    )

    assert db_client.get("/api/v1/auth/me").status_code == 401


def test_expired_guardian_disables_login_without_revealing_reason(
    db_client: TestClient,
    minor_login,
    db: Session,
) -> None:
    minor_login.relationship.valid_until = date(2026, 7, 26)
    db.commit()
    request_id = "expired-minor-login"

    response = db_client.post(
        "/api/v1/auth/login",
        headers={"X-Request-ID": request_id},
        json={"phone": minor_login.phone, "password": minor_login.password},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert "guardian" not in response.text.lower()
    account = db.get(UserAccount, minor_login.account_id)
    assert account is not None
    assert account.status == "disabled"
    assert db.scalar(
        select(func.count(AuthSession.id)).where(AuthSession.user_id == minor_login.account_id)
    ) == 0
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.resource_id == minor_login.account_id)
            .order_by(AuditEvent.action)
        )
    )
    assert [(event.action, event.outcome) for event in events] == [
        ("account.auto_disable.guardian_missing", "allowed"),
        ("auth.login.guardian_missing", "denied"),
    ]
    assert all(event.actor_user_id == minor_login.account_id for event in events)
    assert all(event.request_id == request_id for event in events)
    assert all(
        event.event_metadata == {"error_code": "minor_guardian_required"} for event in events
    )
    assert all(minor_login.phone not in str(event.event_metadata) for event in events)
    assert all("mother" not in str(event.event_metadata) for event in events)


def test_authentication_uses_shanghai_date_at_clock_boundary(
    minor_login,
    db: Session,
) -> None:
    current_time = [datetime(2026, 7, 26, 15, 59, 59, tzinfo=UTC)]
    minor_login.relationship.valid_until = date(2026, 7, 26)
    db.commit()
    service = AuthService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: current_time[0],
    )
    login_result = service.login(
        phone=minor_login.phone,
        password=minor_login.password,
        ip_address="127.0.0.1",
        request_id="before-business-date-boundary",
    )

    current_time[0] = datetime(2026, 7, 26, 16, 0, tzinfo=UTC)
    with pytest.raises(InvalidSession, match="invalid session"):
        service.authenticate(
            login_result.session_token,
            request_id="at-business-date-boundary",
        )

    db.expire_all()
    account = db.get(UserAccount, minor_login.account_id)
    assert account is not None and account.status == "disabled"


def test_failed_denial_audit_rolls_back_auto_disable_and_revocation(
    db_client: TestClient,
    minor_login,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == minor_login.account_id,
            AuthSession.revoked_at.is_(None),
        )
    )
    assert session is not None
    minor_login.relationship.valid_until = date(2026, 7, 26)
    db.commit()
    original_append = AuditService.append
    append_count = 0

    def fail_after_second_flush(self: AuditService, **kwargs: object):
        nonlocal append_count
        append_count += 1
        event_row = original_append(self, **kwargs)  # type: ignore[arg-type]
        if append_count == 2:
            raise RuntimeError("denial audit unavailable")
        return event_row

    monkeypatch.setattr(AuditService, "append", fail_after_second_flush)
    service = AuthService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: datetime(2026, 7, 27, 2, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="denial audit unavailable"):
        service.login(
            phone=minor_login.phone,
            password=minor_login.password,
            ip_address="127.0.0.1",
            request_id="failed-denial-audit",
        )

    db.expire_all()
    account = db.get(UserAccount, minor_login.account_id)
    stored_session = db.get(AuthSession, session.id)
    assert account is not None and account.status == "active"
    assert stored_session is not None and stored_session.revoked_at is None
    assert list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id == "failed-denial-audit",
            )
        )
    ) == []


def test_expired_guardian_revokes_all_sessions_before_returning_data(
    db_client: TestClient,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    assert minor_login.login(db_client).status_code == 200
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == minor_login.account_id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 2
    minor_login.relationship.valid_until = date(2026, 7, 26)
    db.commit()
    request_id = "expired-guardian-session"

    response = db_client.get("/api/v1/auth/me", headers={"X-Request-ID": request_id})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"
    assert "user" not in response.json()
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == minor_login.account_id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 0
    account = db.get(UserAccount, minor_login.account_id)
    assert account is not None
    assert account.status == "disabled"
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.resource_id == minor_login.account_id,
                AuditEvent.action.in_(
                    {
                        "account.auto_disable.guardian_missing",
                        "auth.session.guardian_missing",
                    }
                ),
            )
            .order_by(AuditEvent.action)
        )
    )
    assert [(event.action, event.outcome) for event in events] == [
        ("account.auto_disable.guardian_missing", "allowed"),
        ("auth.session.guardian_missing", "denied"),
    ]
    assert all(event.actor_user_id == minor_login.account_id for event in events)
    assert all(event.request_id == request_id for event in events)
    assert all(
        event.event_metadata == {"error_code": "minor_guardian_required"} for event in events
    )


def test_expired_session_is_rejected(
    db_client: TestClient,
    active_account,
    db: Session,
) -> None:
    assert login(db_client, active_account).status_code == 200
    session = db.scalar(select(AuthSession).where(AuthSession.user_id == active_account["id"]))
    assert session is not None
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    response = db_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"


def test_login_error_does_not_reveal_account_state(
    db_client: TestClient,
    active_account,
) -> None:
    bad_password = login(db_client, active_account, password="wrong-password-2026")
    missing_phone = db_client.post(
        "/api/v1/auth/login",
        json={"phone": "13900139000", "password": "wrong-password-2026"},
    )
    active_account["accounts"].disable_account(
        active_account["id"],
        actor_user_id=None,
        request_id="service-test",
    )
    disabled = login(db_client, active_account)

    assert bad_password.status_code == missing_phone.status_code == disabled.status_code == 401
    assert bad_password.json()["code"] == "invalid_credentials"
    assert missing_phone.json()["detail"] == bad_password.json()["detail"]
    assert disabled.json()["detail"] == bad_password.json()["detail"]


def test_rate_limit_uses_reverse_proxy_client_ip(
    db_client: TestClient,
    active_account,
    db: Session,
) -> None:
    payload = {"phone": "13900139000", "password": "wrong-password-2026"}
    assert (
        db_client.post(
            "/api/v1/auth/login",
            headers={"X-Real-IP": "203.0.113.10"},
            json=payload,
        ).status_code
        == 401
    )
    assert (
        db_client.post(
            "/api/v1/auth/login",
            headers={"X-Real-IP": "203.0.113.11"},
            json=payload,
        ).status_code
        == 401
    )

    assert db.scalar(select(func.count(AuthRateLimit.key_digest))) == 2


def test_login_rejects_oversized_password_without_echoing_it(
    db_client: TestClient,
    active_account,
) -> None:
    oversized = "A1" + "x" * 1024
    response = login(db_client, active_account, password=oversized)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert oversized not in response.text


def test_five_failures_lock_account_and_store_only_rate_limit_digest(
    db_client: TestClient,
    active_account,
    db: Session,
) -> None:
    for _ in range(5):
        assert (
            login(
                db_client,
                active_account,
                password="wrong-password-2026",
            ).status_code
            == 401
        )

    assert login(db_client, active_account).status_code == 401
    account = db.get(UserAccount, active_account["id"])
    assert account is not None
    assert account.locked_until is not None
    rate_limit = db.scalar(select(AuthRateLimit))
    assert rate_limit is not None
    assert active_account["phone"] not in rate_limit.key_digest
    assert "testclient" not in rate_limit.key_digest


def test_blocked_login_skips_expensive_hash_and_recovers_after_timeout(
    active_account,
    db: Session,
) -> None:
    class CountingPasswords(PasswordService):
        def __init__(self) -> None:
            super().__init__()
            self.verify_calls = 0

        def verify(self, password: str, encoded: str) -> bool:
            self.verify_calls += 1
            return super().verify(password, encoded)

    current_time = [datetime(2026, 7, 26, 10, 0, tzinfo=UTC)]
    passwords = CountingPasswords()
    service = AuthService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        passwords=passwords,
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: current_time[0],
    )
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            service.login(
                phone=active_account["phone"],
                password="wrong-password-2026",
                ip_address="127.0.0.1",
                request_id="auth-service-test",
            )
    assert passwords.verify_calls == 5

    with pytest.raises(InvalidCredentials):
        service.login(
            phone=active_account["phone"],
            password=active_account["password"],
            ip_address="127.0.0.1",
            request_id="auth-service-test",
        )
    assert passwords.verify_calls == 5

    current_time[0] += timedelta(minutes=16)
    assert (
        service.login(
            phone=active_account["phone"],
            password=active_account["password"],
            ip_address="127.0.0.1",
            request_id="auth-service-test",
        ).context.account.id
        == active_account["id"]
    )
    assert passwords.verify_calls == 6


def test_change_password_clears_forced_state_and_invalidates_old_password(
    db_client: TestClient,
    active_account,
) -> None:
    signed_in = login(db_client, active_account)
    changed = db_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
        json={
            "current_password": active_account["password"],
            "new_password": "Aa1-new-2026",
        },
    )

    assert changed.status_code == 200
    assert changed.json()["user"]["force_password_change"] is False
    db_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": changed.json()["csrf_token"]},
    )
    assert login(db_client, active_account).status_code == 401
    assert (
        login(
            db_client,
            active_account,
            password="Aa1-new-2026",
        ).status_code
        == 200
    )


@pytest.mark.parametrize("operation", ["change", "reset"])
def test_password_security_transition_blocks_concurrent_old_password_login(
    postgres_engine: Engine,
    operation: str,
    request: pytest.FixtureRequest,
) -> None:
    revocation_reached = threading.Event()
    allow_mutation_to_finish = threading.Event()
    mutation_done = threading.Event()
    racer_started = threading.Event()
    racer_done = threading.Event()
    mutation_thread_id: list[int] = []
    racer_backend_pid: list[int] = []
    mutation_errors: list[Exception] = []
    racer_errors: list[Exception] = []

    def pause_after_session_revocation(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if (
            mutation_thread_id
            and threading.get_ident() == mutation_thread_id[0]
            and "update auth_sessions" in statement.lower()
        ):
            revocation_reached.set()
            if not allow_mutation_to_finish.wait(timeout=10):
                raise TimeoutError("password mutation was not released")

    def new_auth_service(session: Session) -> AuthService:
        return AuthService(
            session,
            lookup=LookupDigest(bytes(range(32))),
            passwords=PasswordService(),
            session_token_pepper=bytes(range(32)),
            session_ttl_minutes=480,
            clock=lambda: datetime(2026, 7, 27, 2, tzinfo=UTC),
        )

    scenario_context = committed_account_scenario(
        postgres_engine,
        sequence=901 if operation == "change" else 902,
    )
    active_account = scenario_context.__enter__()
    request.addfinalizer(lambda: scenario_context.__exit__(None, None, None))

    def mutate_password() -> None:
        mutation_thread_id.append(threading.get_ident())
        try:
            with Session(postgres_engine, autoflush=False) as mutation_db:
                if operation == "change":
                    auth = new_auth_service(mutation_db)
                    context = auth.login(
                        phone=active_account.phone,
                        password=active_account.password,
                        ip_address="127.0.0.1",
                        request_id="password-change-setup",
                    ).context
                    auth.change_password(
                        context,
                        current_password=active_account.password,
                        new_password="ConcurA1new26",
                    )
                else:
                    identity = IdentityService(
                        mutation_db,
                        cipher=EnvelopeCipher(bytes(range(32))),
                        lookup=LookupDigest(bytes(range(32))),
                    )
                    AccountService(
                        mutation_db,
                        identity_service=identity,
                        passwords=PasswordService(),
                    ).reset_password(active_account.account_id)
        except Exception as exc:  # noqa: BLE001 - propagate thread failures below
            mutation_errors.append(exc)
        finally:
            mutation_done.set()

    def race_login() -> None:
        try:
            with Session(postgres_engine, autoflush=False) as racer_db:
                backend_pid = racer_db.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(backend_pid, int)
                racer_backend_pid.append(backend_pid)
                racer_started.set()
                new_auth_service(racer_db).login(
                    phone=active_account.phone,
                    password=active_account.password,
                    ip_address="127.0.0.1",
                    request_id="concurrent-old-password-login",
                )
        except Exception as exc:  # noqa: BLE001 - propagate thread failures below
            racer_errors.append(exc)
        finally:
            racer_done.set()

    event.listen(postgres_engine, "after_cursor_execute", pause_after_session_revocation)
    mutation = threading.Thread(target=mutate_password)
    racer = threading.Thread(target=race_login)
    try:
        mutation.start()
        assert revocation_reached.wait(timeout=10)
        racer.start()
        assert racer_started.wait(timeout=2)
        assert wait_for_backend_lock(
            postgres_engine,
            racer_backend_pid[0],
            racer_done,
        )
    finally:
        allow_mutation_to_finish.set()
        mutation.join(timeout=10)
        if racer.ident is not None:
            racer.join(timeout=10)
        event.remove(postgres_engine, "after_cursor_execute", pause_after_session_revocation)

    assert not mutation.is_alive()
    assert not racer.is_alive()
    assert mutation_errors == []
    assert len(racer_errors) == 1
    assert isinstance(racer_errors[0], InvalidCredentials)
    with Session(postgres_engine) as verification:
        active_sessions = verification.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.user_id == active_account.account_id,
                AuthSession.revoked_at.is_(None),
            )
        )
    assert active_sessions == (1 if operation == "change" else 0)


def test_change_password_reports_password_policy_error(
    db_client: TestClient,
    active_account,
) -> None:
    signed_in = login(db_client, active_account)
    response = db_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
        json={
            "current_password": active_account["password"],
            "new_password": "letters-only",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "password_policy_failed"
    assert response.json()["detail"] == "新密码需为 8–16 位，并同时包含大写、小写字母和数字"


def test_forced_password_account_cannot_access_standard_routes(
    db_client: TestClient,
    active_account,
) -> None:
    @db_client.app.get("/api/v1/_test/standard")
    def standard_route(
        context: Annotated[AuthContext, Depends(require_standard_account)],
    ) -> dict[str, str]:
        return {"account_id": str(context.account.id)}

    signed_in = login(db_client, active_account)
    rejected = db_client.get("/api/v1/_test/standard")
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "force_password_change_required"

    changed = db_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
        json={
            "current_password": active_account["password"],
            "new_password": "Aa1-other-26",
        },
    )
    assert changed.status_code == 200
    assert db_client.get("/api/v1/_test/standard").status_code == 200
