from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Connection, event, func, insert, select, update
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.evaluations.bridge_service import EvaluationBridgeService
from app.evaluations.config import EvaluationBridgeConfig
from app.evaluations.errors import (
    EvaluationAccessDenied,
    EvaluationBridgeRequestConflict,
    EvaluationBridgeRequestExpired,
    EvaluationDelegatedSessionInvalid,
    EvaluationTicketInvalid,
)
from app.evaluations.models import EvaluationBridgeRequest, EvaluationBridgeTicket
from app.evaluations.schemas import EvaluationLoginRequestCreate
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher

NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
STATE = (
    "eyJleHBpcmVzX2F0IjoxNzg1Mzg5MTAwLCJtb2RlIjoic3RhbmRhbG9uZSIs"
    "Im5vbmNlIjoiYWJjIiwicmV0dXJuX3BhdGgiOiIvIiwidiI6MX0."
    "signature_abcdefghijklmnopqrstuvwxyz0123456789"
)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


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


def _create_auth_context(db: Session, *, role_key: str = "admin") -> AuthContext:
    marker = uuid4().hex
    person = Person(
        full_name=f"评价桥接账号-{marker[:8]}",
        identity_ciphertext=f"evaluation-identity-{marker}",
        identity_digest=f"evaluation-identity-digest-{marker}",
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="female",
        date_of_birth=date(1990, 1, 2),
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"evaluation-phone-{marker}",
        email_normalized=f"evaluation-{marker}@example.test",
        password_hash="evaluation-test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(account)
    db.flush()
    session = AuthSession(
        user_id=account.id,
        token_digest=marker.ljust(64, "0"),
        csrf_digest=marker[::-1].ljust(64, "0"),
        expires_at=NOW + timedelta(hours=1),
        created_at=NOW,
        updated_at=NOW,
    )
    db.add_all(
        [
            session,
            UserRole(user_id=account.id, role_id=ROLES[role_key].id),
        ]
    )
    db.flush()
    return AuthContext(session=session, account=account, person=person)


@pytest.fixture
def evaluation_clock() -> MutableClock:
    return MutableClock(NOW)


@pytest.fixture
def evaluation_auth_context(db: Session) -> AuthContext:
    return _create_auth_context(db)


@pytest.fixture
def evaluation_bridge_service(
    db: Session,
    evaluation_clock: MutableClock,
) -> EvaluationBridgeService:
    return EvaluationBridgeService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        token_pepper=bytes(range(32)),
        config=EvaluationBridgeConfig(
            erp_web_base_url="http://127.0.0.1:5175",
            standalone_base_url="http://localhost:8010",
            embedded_path="/evaluation-tool",
            request_ttl_seconds=300,
            ticket_ttl_seconds=60,
        ),
        clock=evaluation_clock,
    )


def test_bridge_issues_idempotent_ticket_and_consumes_it_once(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    request_row = db.get(EvaluationBridgeRequest, created.request_id)
    assert request_row is not None
    assert STATE not in request_row.state_ciphertext
    assert created.expires_at == NOW + timedelta(minutes=5)
    assert request_row.created_at == NOW
    assert request_row.updated_at == NOW

    first = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-issue-1",
    )
    repeated = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-issue-2",
    )
    assert repeated == first
    assert first.expires_at == NOW + timedelta(seconds=60)
    assert first.state == STATE
    assert first.callback_url == "http://localhost:8010/auth/erp/callback"

    delegated = evaluation_bridge_service.exchange_ticket(
        first.ticket,
        request_id_for_audit="bridge-exchange-1",
    )
    assert delegated.account_id == evaluation_auth_context.account.id
    assert delegated.session_id == evaluation_auth_context.session.id
    ticket_row = db.scalar(
        select(EvaluationBridgeTicket).where(
            EvaluationBridgeTicket.login_request_id == created.request_id
        )
    )
    assert ticket_row is not None
    assert ticket_row.created_at == NOW
    assert ticket_row.updated_at == NOW
    assert ticket_row.consumed_at == NOW
    assert ticket_row.token_ciphertext is None

    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.resource_id.in_([created.request_id, ticket_row.id]))
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    issued_events = [
        event
        for event in events
        if event.action == "evaluation.bridge.ticket_issued"
    ]
    exchanged_events = [
        event
        for event in events
        if event.action == "evaluation.bridge.ticket_exchanged"
    ]
    assert len(issued_events) == 2
    assert len(exchanged_events) == 1
    assert issued_events[0].event_metadata == {
        "mode": "standalone",
        "client_id": "game-score-tool",
    }
    assert issued_events[1].event_metadata == issued_events[0].event_metadata
    assert exchanged_events[0].event_metadata == {
        "login_request_id": str(created.request_id)
    }
    serialized_events = " ".join(str(event.event_metadata) for event in events)
    assert STATE not in serialized_events
    assert first.ticket not in serialized_events
    assert evaluation_auth_context.account.email_normalized not in serialized_events

    with pytest.raises(EvaluationTicketInvalid):
        evaluation_bridge_service.exchange_ticket(
            first.ticket,
            request_id_for_audit="bridge-exchange-replay",
        )


def test_embedded_request_uses_fixed_erp_callback(
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="embedded",
            state=STATE,
        )
    )

    issued = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-embedded",
    )

    assert issued.callback_url == (
        "http://127.0.0.1:5175/evaluation-tool/auth/erp/callback"
    )


def test_second_account_cannot_reauthorize_login_request(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    first = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-first-account",
    )
    other_context = _create_auth_context(db)

    with pytest.raises(EvaluationBridgeRequestConflict):
        evaluation_bridge_service.issue_ticket(
            request_id=created.request_id,
            context=other_context,
            request_id_for_audit="bridge-second-account",
        )

    stored = db.scalar(
        select(EvaluationBridgeTicket).where(
            EvaluationBridgeTicket.login_request_id == created.request_id
        )
    )
    assert stored is not None
    assert stored.account_id == evaluation_auth_context.account.id
    original_digest = stored.token_digest
    db.expire_all()
    stored_after_conflict = db.get(EvaluationBridgeTicket, stored.id)
    assert stored_after_conflict is not None
    assert stored_after_conflict.token_digest == original_digest
    assert stored_after_conflict.token_ciphertext is not None
    assert first.ticket not in stored_after_conflict.token_ciphertext


def test_session_context_rejects_unknown_session_without_account_oracle(
    evaluation_bridge_service: EvaluationBridgeService,
) -> None:
    with pytest.raises(
        EvaluationDelegatedSessionInvalid,
        match="delegated session is invalid",
    ):
        evaluation_bridge_service.session_context(uuid4())


def test_create_login_request_prunes_only_500_old_rows(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
) -> None:
    old_created_at = NOW - timedelta(hours=48)
    old_expires_at = NOW - timedelta(hours=25)
    recent_created_at = NOW - timedelta(hours=2)
    recent_expires_at = NOW - timedelta(hours=1)
    db.execute(
        insert(EvaluationBridgeRequest),
        [
            {
                "id": uuid4(),
                "client_id": "stale-request",
                "mode": "standalone",
                "state_ciphertext": "stale-ciphertext",
                "expires_at": old_expires_at,
                "authorized_at": None,
                "created_at": old_created_at,
                "updated_at": old_created_at,
            }
            for _ in range(501)
        ]
        + [
            {
                "id": uuid4(),
                "client_id": "recent-expired-request",
                "mode": "standalone",
                "state_ciphertext": "recent-ciphertext",
                "expires_at": recent_expires_at,
                "authorized_at": None,
                "created_at": recent_created_at,
                "updated_at": recent_created_at,
            }
        ],
    )
    db.flush()

    evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )

    assert db.scalar(
        select(func.count(EvaluationBridgeRequest.id)).where(
            EvaluationBridgeRequest.client_id == "stale-request"
        )
    ) == 1
    assert db.scalar(
        select(func.count(EvaluationBridgeRequest.id)).where(
            EvaluationBridgeRequest.client_id == "recent-expired-request"
        )
    ) == 1


def test_expired_request_is_distinct_from_invalid_ticket(
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
    evaluation_clock: MutableClock,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    evaluation_clock.value = NOW + timedelta(minutes=5)

    with pytest.raises(EvaluationBridgeRequestExpired):
        evaluation_bridge_service.issue_ticket(
            request_id=created.request_id,
            context=evaluation_auth_context,
            request_id_for_audit="bridge-expired-request",
        )


def test_expired_ticket_is_invalid_even_while_request_and_session_are_live(
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
    evaluation_clock: MutableClock,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    issued = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-live-ticket",
    )
    evaluation_clock.value = NOW + timedelta(seconds=60)

    with pytest.raises(EvaluationTicketInvalid):
        evaluation_bridge_service.exchange_ticket(
            issued.ticket,
            request_id_for_audit="bridge-expired-ticket",
        )


@pytest.mark.parametrize("ticket_state", ["consumed", "expired"])
@pytest.mark.parametrize("mutation", ["revoke_session", "deny_access"])
def test_invalid_ticket_precedes_live_session_and_permission_rechecks(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
    evaluation_clock: MutableClock,
    ticket_state: str,
    mutation: str,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    issued = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-before-invalid-ticket",
    )
    if ticket_state == "consumed":
        evaluation_bridge_service.exchange_ticket(
            issued.ticket,
            request_id_for_audit="bridge-consume-before-mutation",
        )
    else:
        assert ticket_state == "expired"
        evaluation_clock.value = NOW + timedelta(seconds=60)

    if mutation == "revoke_session":
        db.execute(
            update(AuthSession)
            .where(AuthSession.id == evaluation_auth_context.session.id)
            .values(revoked_at=NOW)
            .execution_options(synchronize_session=False)
        )
    else:
        assert mutation == "deny_access"
        db.add(
            UserPermissionOverride(
                user_id=evaluation_auth_context.account.id,
                permission_id=PERMISSIONS["evaluation_center.access"].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            )
        )
    db.commit()

    with pytest.raises(EvaluationTicketInvalid):
        evaluation_bridge_service.exchange_ticket(
            issued.ticket,
            request_id_for_audit="bridge-invalid-ticket-after-mutation",
        )


def test_expired_unconsumed_ticket_is_rotated_for_the_same_session(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
    evaluation_clock: MutableClock,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    first = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-ticket-before-rotation",
    )
    original = db.scalar(
        select(EvaluationBridgeTicket).where(
            EvaluationBridgeTicket.login_request_id == created.request_id
        )
    )
    assert original is not None
    original_id = original.id
    original_digest = original.token_digest
    evaluation_clock.value = NOW + timedelta(seconds=61)

    rotated = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-ticket-after-rotation",
    )

    assert rotated.ticket != first.ticket
    db.expire_all()
    stored = db.get(EvaluationBridgeTicket, original_id)
    assert stored is not None
    assert stored.token_digest != original_digest
    assert stored.created_at == NOW
    assert stored.updated_at == evaluation_clock.value
    with pytest.raises(EvaluationTicketInvalid):
        evaluation_bridge_service.exchange_ticket(
            first.ticket,
            request_id_for_audit="bridge-old-ticket-after-rotation",
        )


def test_exchange_rechecks_digest_after_locking_ticket(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    issued = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-before-concurrent-rotation",
    )
    ticket_row = db.scalar(
        select(EvaluationBridgeTicket).where(
            EvaluationBridgeTicket.login_request_id == created.request_id
        )
    )
    assert ticket_row is not None
    bind = db.get_bind()
    rotated = False

    def rotate_before_lock(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal rotated
        normalized = " ".join(statement.lower().split())
        if (
            rotated
            or "from evaluation_bridge_tickets" not in normalized
            or " for update" not in normalized
        ):
            return
        rotated = True
        connection.execute(
            update(EvaluationBridgeTicket)
            .where(EvaluationBridgeTicket.id == ticket_row.id)
            .values(
                token_digest="f" * 64,
                expires_at=NOW + timedelta(minutes=2),
                updated_at=NOW,
            )
        )

    event.listen(bind, "before_cursor_execute", rotate_before_lock)
    try:
        with pytest.raises(EvaluationTicketInvalid):
            evaluation_bridge_service.exchange_ticket(
                issued.ticket,
                request_id_for_audit="bridge-after-concurrent-rotation",
            )
    finally:
        event.remove(bind, "before_cursor_execute", rotate_before_lock)

    assert rotated is True


@pytest.mark.parametrize(
    ("operation", "mutation", "expected"),
    [
        ("exchange", "revoke_session", EvaluationDelegatedSessionInvalid),
        ("session", "revoke_session", EvaluationDelegatedSessionInvalid),
        ("exchange", "disable_account", EvaluationDelegatedSessionInvalid),
        ("session", "disable_account", EvaluationDelegatedSessionInvalid),
        ("exchange", "force_password_change", EvaluationDelegatedSessionInvalid),
        ("session", "force_password_change", EvaluationDelegatedSessionInvalid),
        ("issue", "deny_access", EvaluationAccessDenied),
        ("exchange", "deny_access", EvaluationAccessDenied),
        ("session", "deny_access", EvaluationAccessDenied),
    ],
)
def test_bridge_rechecks_session_and_global_permission(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
    operation: str,
    mutation: str,
    expected: type[Exception],
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    issued = None
    if operation != "issue":
        issued = evaluation_bridge_service.issue_ticket(
            request_id=created.request_id,
            context=evaluation_auth_context,
            request_id_for_audit="bridge-before-revocation",
        )
    if mutation == "revoke_session":
        db.execute(
            update(AuthSession)
            .where(AuthSession.id == evaluation_auth_context.session.id)
            .values(revoked_at=NOW)
            .execution_options(synchronize_session=False)
        )
    elif mutation == "disable_account":
        db.execute(
            update(UserAccount)
            .where(UserAccount.id == evaluation_auth_context.account.id)
            .values(status="disabled")
            .execution_options(synchronize_session=False)
        )
    elif mutation == "force_password_change":
        db.execute(
            update(UserAccount)
            .where(UserAccount.id == evaluation_auth_context.account.id)
            .values(force_password_change=True)
            .execution_options(synchronize_session=False)
        )
    else:
        assert mutation == "deny_access"
        db.add(
            UserPermissionOverride(
                user_id=evaluation_auth_context.account.id,
                permission_id=PERMISSIONS["evaluation_center.access"].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            )
        )
    db.commit()

    with pytest.raises(expected):
        if operation == "issue":
            evaluation_bridge_service.issue_ticket(
                request_id=created.request_id,
                context=evaluation_auth_context,
                request_id_for_audit="bridge-after-revocation",
            )
        elif operation == "exchange":
            assert issued is not None
            evaluation_bridge_service.exchange_ticket(
                issued.ticket,
                request_id_for_audit="bridge-after-revocation",
            )
        else:
            assert operation == "session"
            evaluation_bridge_service.session_context(
                evaluation_auth_context.session.id
            )


def test_bridge_security_rechecks_refresh_the_identity_map(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
) -> None:
    cached_account = evaluation_auth_context.account
    assert cached_account.status == "active"
    previous_expire_on_commit = db.expire_on_commit
    db.expire_on_commit = False
    try:
        db.execute(
            update(UserAccount)
            .where(UserAccount.id == cached_account.id)
            .values(status="disabled")
            .execution_options(synchronize_session=False)
        )
        db.commit()
        assert cached_account.status == "active"

        with pytest.raises(EvaluationDelegatedSessionInvalid):
            evaluation_bridge_service.session_context(
                evaluation_auth_context.session.id
            )
    finally:
        db.expire_on_commit = previous_expire_on_commit


def test_issue_ticket_audit_failure_rolls_back_ticket_and_authorization(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )

    def reject_audit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated evaluation audit failure")

    monkeypatch.setattr(AuditService, "append", reject_audit)
    with pytest.raises(AuditMetadataRejected):
        evaluation_bridge_service.issue_ticket(
            request_id=created.request_id,
            context=evaluation_auth_context,
            request_id_for_audit="bridge-issue-audit-failure",
        )

    db.expire_all()
    request_row = db.get(EvaluationBridgeRequest, created.request_id)
    assert request_row is not None
    assert request_row.authorized_at is None
    assert db.scalar(
        select(func.count(EvaluationBridgeTicket.id)).where(
            EvaluationBridgeTicket.login_request_id == created.request_id
        )
    ) == 0


def test_exchange_ticket_audit_failure_rolls_back_consumption(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    issued = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-before-exchange-audit-failure",
    )
    ticket_row = db.scalar(
        select(EvaluationBridgeTicket).where(
            EvaluationBridgeTicket.login_request_id == created.request_id
        )
    )
    assert ticket_row is not None and ticket_row.token_ciphertext is not None
    original_ciphertext = ticket_row.token_ciphertext

    def reject_audit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated evaluation audit failure")

    monkeypatch.setattr(AuditService, "append", reject_audit)
    with pytest.raises(AuditMetadataRejected):
        evaluation_bridge_service.exchange_ticket(
            issued.ticket,
            request_id_for_audit="bridge-exchange-audit-failure",
        )

    db.expire_all()
    stored = db.get(EvaluationBridgeTicket, ticket_row.id)
    assert stored is not None
    assert stored.consumed_at is None
    assert stored.token_ciphertext == original_ciphertext


def test_issue_and_exchange_use_one_consistent_lock_order(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )

    with captured_statements(db) as issue_statements:
        issued = evaluation_bridge_service.issue_ticket(
            request_id=created.request_id,
            context=evaluation_auth_context,
            request_id_for_audit="bridge-lock-order-issue",
        )

    request_lock = next(
        index
        for index, statement in enumerate(issue_statements)
        if "from evaluation_bridge_requests" in statement
        and " for update" in statement
    )
    ticket_lock = next(
        index
        for index, statement in enumerate(issue_statements)
        if "from evaluation_bridge_tickets" in statement
        and " for update" in statement
    )
    session_lock = next(
        index
        for index, statement in enumerate(issue_statements)
        if "from auth_sessions" in statement and " for update" in statement
    )
    assert request_lock < ticket_lock < session_lock

    with captured_statements(db) as exchange_statements:
        evaluation_bridge_service.exchange_ticket(
            issued.ticket,
            request_id_for_audit="bridge-lock-order-exchange",
        )

    ticket_location = next(
        index
        for index, statement in enumerate(exchange_statements)
        if statement.startswith("select evaluation_bridge_tickets.id")
        and " for update" not in statement
    )
    locked_ticket = next(
        index
        for index, statement in enumerate(exchange_statements)
        if "from evaluation_bridge_tickets" in statement
        and " for update" in statement
    )
    locked_session = next(
        index
        for index, statement in enumerate(exchange_statements)
        if "from auth_sessions" in statement and " for update" in statement
    )
    assert ticket_location < locked_ticket < locked_session


def test_expiry_clock_is_read_only_after_required_locks(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    snapshots: list[tuple[str, ...]] = []
    active_statements: list[str] = []

    def recording_clock() -> datetime:
        snapshots.append(tuple(active_statements))
        return NOW

    evaluation_bridge_service.clock = recording_clock
    with captured_statements(db) as issue_statements:
        active_statements = issue_statements
        issued = evaluation_bridge_service.issue_ticket(
            request_id=created.request_id,
            context=evaluation_auth_context,
            request_id_for_audit="bridge-clock-after-issue-locks",
        )

    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert any(
            "from evaluation_bridge_requests" in statement
            and " for update" in statement
            for statement in snapshot
        )
        assert any(
            "from evaluation_bridge_tickets" in statement
            and " for update" in statement
            for statement in snapshot
        )
        assert any(
            "from auth_sessions" in statement and " for update" in statement
            for statement in snapshot
        )

    snapshots.clear()
    with captured_statements(db) as exchange_statements:
        active_statements = exchange_statements
        evaluation_bridge_service.exchange_ticket(
            issued.ticket,
            request_id_for_audit="bridge-clock-after-exchange-locks",
        )

    assert len(snapshots) == 1
    for snapshot in snapshots:
        assert any(
            statement.startswith("select evaluation_bridge_tickets.id")
            and " for update" not in statement
            for statement in snapshot
        )
        assert any(
            "from evaluation_bridge_tickets" in statement
            and " for update" in statement
            for statement in snapshot
        )
        assert any(
            "from auth_sessions" in statement and " for update" in statement
            for statement in snapshot
        )


def test_bridge_rejects_naive_clock_and_short_pepper(db: Session) -> None:
    config = EvaluationBridgeConfig(
        erp_web_base_url="http://127.0.0.1:5175",
        standalone_base_url="http://localhost:8010",
        embedded_path="/evaluation-tool",
        request_ttl_seconds=300,
        ticket_ttl_seconds=60,
    )
    with pytest.raises(ValueError, match="pepper"):
        EvaluationBridgeService(
            db,
            cipher=EnvelopeCipher(bytes(range(32))),
            token_pepper=b"too-short",
            config=config,
        )
    service = EvaluationBridgeService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        token_pepper=bytes(range(32)),
        config=config,
        clock=lambda: datetime(2026, 7, 30, 8, 0),  # noqa: DTZ001
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        service.create_login_request(
            EvaluationLoginRequestCreate(
                client_id="game-score-tool",
                mode="standalone",
                state=STATE,
            )
        )


@pytest.mark.parametrize(
    "values",
    [
        {"client_id": "other-tool", "mode": "standalone", "state": STATE},
        {"client_id": "game-score-tool", "mode": "external", "state": STATE},
        {
            "client_id": "game-score-tool",
            "mode": "standalone",
            "state": "unsafe state with spaces",
        },
    ],
)
def test_login_request_schema_rejects_unfrozen_inputs(
    values: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        EvaluationLoginRequestCreate.model_validate(values)
