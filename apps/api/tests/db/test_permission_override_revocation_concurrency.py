from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride
from app.rbac.router import remove_permission_override, require_account_permissions_manager
from app.rbac.seed import PERMISSIONS


@pytest.mark.parametrize("revocation, expected_status", [
    ("permission", 403), ("session", 401), ("account", 401),
])
def test_override_deletion_rejects_committed_revocation_during_lock_wait(
    postgres_engine, revocation: str, expected_status: int,
) -> None:
    actor_person_id, target_person_id = uuid4(), uuid4()
    actor_id, target_id, session_id, override_id, grant_id = (uuid4() for _ in range(5))
    with Session(postgres_engine) as seed, seed.begin():
        for person_id in (actor_person_id, target_person_id):
            seed.add(Person(
                id=person_id, full_name="虚拟权限并发测试",
                identity_ciphertext="synthetic-not-an-identity",
                identity_digest=uuid4().hex, sex="female", date_of_birth=date(1980, 1, 1),
            ))
        seed.flush()
        seed.add_all([
            UserAccount(id=actor_id, person_id=actor_person_id, password_hash="unused",
                        status="active", force_password_change=False),
            UserAccount(id=target_id, person_id=target_person_id, password_hash="unused",
                        status="active", force_password_change=False),
        ])
        seed.flush()
        seed.add(AuthSession(
            id=session_id, user_id=actor_id, token_digest=uuid4().hex,
            csrf_digest=uuid4().hex, expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        seed.add_all([
            UserPermissionOverride(
                id=grant_id, user_id=actor_id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="allow", scope_kind="person", scope_id=target_person_id,
            ),
            UserPermissionOverride(
                id=override_id, user_id=target_id,
                permission_id=PERMISSIONS["travel_reports.read"].id,
                effect="allow", scope_kind="global",
            ),
        ])

    started = Event()
    backend_pid: list[int] = []

    def remove() -> int:
        # Keep the connection pinned across the route's pre-write commit so
        # pg_stat_activity identifies the exact waiting backend.
        with postgres_engine.connect() as connection, Session(connection) as db:
            db.execute(text("SET lock_timeout = '8s'"))
            backend_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            context = AuthContext(
                account=db.get(UserAccount, actor_id),
                person=db.get(Person, actor_person_id),
                session=db.get(AuthSession, session_id),
            )
            require_account_permissions_manager(target_id, context, db)
            request = Request({"type": "http", "headers": []})
            request.state.request_id = str(uuid4())
            started.set()
            try:
                return remove_permission_override(
                    target_id, override_id, request, context, db,
                ).status_code
            except ProblemError as exc:
                return exc.status

    try:
        with Session(postgres_engine) as blocker:
            # Revoke before releasing the required person -> account locks,
            # not by updating an actor row the waiting mutation already owns.
            blocker.scalar(select(Person).where(
                Person.id == actor_person_id,
            ).with_for_update())
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(remove)
                try:
                    assert started.wait(5)
                    deadline = monotonic() + 5
                    with postgres_engine.connect() as observer:
                        while monotonic() < deadline:
                            waiting = observer.scalar(text(
                                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                                "WHERE pid = :pid"
                            ), {"pid": backend_pid[0]})
                            observer.commit()
                            if waiting:
                                break
                            sleep(0.01)
                    assert waiting, "request did not reach the actual PostgreSQL lock wait"
                    if revocation == "permission":
                        blocker.execute(delete(UserPermissionOverride).where(
                            UserPermissionOverride.id == grant_id,
                        ))
                    elif revocation == "session":
                        blocker.execute(update(AuthSession).where(
                            AuthSession.id == session_id,
                        ).values(revoked_at=datetime.now(UTC)))
                    else:
                        blocker.execute(update(UserAccount).where(
                            UserAccount.id == actor_id,
                        ).values(status="disabled"))
                    # Account constraints also lock Person at commit; the lock
                    # owner must perform and commit the revocation itself.
                    blocker.commit()
                finally:
                    blocker.rollback()
                assert future.result(timeout=10) == expected_status
        with Session(postgres_engine) as verify:
            assert verify.get(UserPermissionOverride, override_id) is not None
            assert verify.scalar(select(AuditEvent.id).where(
                AuditEvent.actor_user_id == actor_id,
                AuditEvent.action == "account.permission_override.remove",
                AuditEvent.outcome == "allowed",
            )) is None
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            name = cleanup.scalar(text("SELECT current_database()"))
            assert name == "erp_e2e" or name.endswith("_test")
            cleanup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            cleanup.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == actor_id))
            cleanup.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            cleanup.execute(delete(UserPermissionOverride).where(
                UserPermissionOverride.user_id.in_([actor_id, target_id]),
            ))
            cleanup.execute(delete(AuthSession).where(AuthSession.user_id == actor_id))
            cleanup.execute(delete(UserAccount).where(UserAccount.id.in_([actor_id, target_id])))
            cleanup.execute(delete(Person).where(Person.id.in_([actor_person_id, target_person_id])))
