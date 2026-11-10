from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person, StaffProfile
from app.rbac.models import UserRole
from app.rbac.schemas import JobRoleChangeRequest
from app.rbac.seed import ROLES
from app.rbac.service import RbacService, RbacServiceError


@pytest.fixture
def committed_accounts(postgres_engine):
    person_ids = [uuid4() for _ in range(3)]
    account_ids = [uuid4() for _ in range(3)]
    with Session(postgres_engine) as db, db.begin():
        for person_id in person_ids:
            db.add(Person(id=person_id, full_name="虚拟换岗并发测试", sex="female",
                          date_of_birth=date(1980, 1, 1), identity_digest=uuid4().hex,
                          identity_ciphertext="synthetic-not-an-identity"))
        db.flush()
        for account_id, person_id in zip(account_ids, person_ids):
            db.add(UserAccount(id=account_id, person_id=person_id, password_hash="unused",
                               status="active", force_password_change=False))
        db.flush()
        db.add_all([UserRole(user_id=account_ids[0], role_id=ROLES["super_admin"].id),
                    UserRole(user_id=account_ids[1], role_id=ROLES["customer_service"].id),
                    UserRole(user_id=account_ids[2], role_id=ROLES["super_admin"].id)])
    try:
        yield account_ids
    finally:
        with Session(postgres_engine) as db, db.begin():
            name = db.scalar(text("SELECT current_database()"))
            assert name == "erp_e2e" or name.endswith("_test")
            db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(account_ids)))
            db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            db.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
            db.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(person_ids)))
            db.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
            db.execute(delete(Person).where(Person.id.in_(person_ids)))


def test_simultaneous_job_changes_accept_only_one_role_snapshot(postgres_engine, committed_accounts):
    root, target, _ = committed_accounts
    barrier = Barrier(2)

    def change(new_key):
        with Session(postgres_engine) as db:
            db.execute(text("SET lock_timeout = '8s'"))
            barrier.wait(timeout=5)
            try:
                RbacService(db).change_job_role(actor_user_id=root, account_id=target,
                    input=JobRoleChangeRequest(from_role_id=ROLES["customer_service"].id,
                        to_role_id=ROLES[new_key].id, keep_concurrent_roles=False,
                        expected_role_ids=[ROLES["customer_service"].id]), request_id=str(uuid4()))
                return "ok"
            except RbacServiceError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, ["finance", "coach"]))
    assert sorted(results) == ["job_role_conflict", "ok"]
    with Session(postgres_engine) as db:
        roles = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == target)))
        assert roles in ({ROLES["finance"].id}, {ROLES["coach"].id})
        assert len(list(db.scalars(select(AuditEvent.id).where(
            AuditEvent.action == "account.job_role.change", AuditEvent.resource_id == target)))) == 1


def test_two_roots_cannot_concurrently_remove_each_other(postgres_engine, committed_accounts):
    first, _, second = committed_accounts
    barrier = Barrier(2)

    def change(pair):
        actor, target = pair
        with Session(postgres_engine) as db:
            db.execute(text("SET lock_timeout = '8s'"))
            barrier.wait(timeout=5)
            try:
                RbacService(db).change_job_role(actor_user_id=actor, account_id=target,
                    input=JobRoleChangeRequest(from_role_id=ROLES["super_admin"].id,
                        to_role_id=ROLES["finance"].id, keep_concurrent_roles=False,
                        expected_role_ids=[ROLES["super_admin"].id]), request_id=str(uuid4()))
                return "ok"
            except RbacServiceError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [(first, second), (second, first)]))
    assert sorted(results) == ["ok", "permission_delegation_denied"]
    with Session(postgres_engine) as db:
        assert len(list(db.scalars(select(UserRole.user_id).where(
            UserRole.user_id.in_([first, second]), UserRole.role_id == ROLES["super_admin"].id)))) == 1


def test_job_change_rechecks_actor_after_actual_postgres_lock_wait(postgres_engine, committed_accounts):
    root, target, _ = committed_accounts
    started = Event()
    backend_pid = []

    def change():
        with postgres_engine.connect() as connection, Session(connection) as db:
            db.execute(text("SET lock_timeout = '8s'"))
            backend_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                RbacService(db).change_job_role(actor_user_id=root, account_id=target,
                    input=JobRoleChangeRequest(from_role_id=ROLES["customer_service"].id,
                        to_role_id=ROLES["finance"].id, keep_concurrent_roles=False,
                        expected_role_ids=[ROLES["customer_service"].id]), request_id=str(uuid4()))
                return "ok"
            except RbacServiceError as exc:
                return exc.code

    with Session(postgres_engine) as blocker:
        root_person_id = blocker.scalar(select(UserAccount.person_id).where(UserAccount.id == root))
        blocker.scalar(select(Person).where(Person.id == root_person_id).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(change)
            try:
                assert started.wait(5)
                deadline = monotonic() + 5
                waiting = False
                with postgres_engine.connect() as observer:
                    while monotonic() < deadline:
                        waiting = observer.scalar(text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                        ), {"pid": backend_pid[0]})
                        observer.commit()
                        if waiting:
                            break
                        sleep(0.01)
                assert waiting, "job change did not reach PostgreSQL identity lock wait"
                blocker.execute(delete(UserRole).where(
                    UserRole.user_id == root, UserRole.role_id == ROLES["super_admin"].id))
                blocker.commit()
            finally:
                blocker.rollback()
            assert future.result(timeout=10) == "permission_delegation_denied"
    with Session(postgres_engine) as db:
        assert set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == target))) == {
            ROLES["customer_service"].id}
        assert db.scalar(select(AuditEvent.id).where(
            AuditEvent.action == "account.job_role.change", AuditEvent.resource_id == target)) is None
