from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.schemas import PermissionOverrideCreate
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService, RbacServiceError


@pytest.mark.parametrize("operation", ["add", "remove"])
@pytest.mark.parametrize("change", ["revoke_actor", "protect_target"])
def test_ordinary_overrides_recheck_authority_and_target_after_identity_lock_wait(
    postgres_engine, operation, change
):
    people = [uuid4(), uuid4()]
    actor, target, existing = uuid4(), uuid4(), uuid4()
    with Session(postgres_engine) as db, db.begin():
        for person_id in people:
            db.add(
                Person(
                    id=person_id,
                    full_name="虚拟委托授权锁测试",
                    sex="female",
                    date_of_birth=date(1980, 1, 1),
                    identity_digest=uuid4().hex,
                    identity_ciphertext="synthetic-not-an-identity",
                )
            )
        db.flush()
        for account, person in zip([actor, target], people):
            db.add(
                UserAccount(
                    id=account,
                    person_id=person,
                    password_hash="unused",
                    status="active",
                    force_password_change=False,
                )
            )
        db.flush()
        db.add_all(
            [
                UserPermissionOverride(
                    user_id=actor,
                    permission_id=PERMISSIONS[key].id,
                    effect="allow",
                    scope_kind="global",
                )
                for key in ("permissions.manage", "procurement.read")
            ]
        )
        db.add(
            UserPermissionOverride(
                id=existing,
                user_id=target,
                permission_id=PERMISSIONS["travel_reports.read"].id,
                effect="allow",
                scope_kind="global",
            )
        )
    started = Event()
    backend_pid = []

    def mutate():
        with postgres_engine.connect() as connection, Session(connection) as db:
            db.execute(text("SET lock_timeout = '8s'"))
            backend_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                service = RbacService(db)
                if operation == "add":
                    service.add_override(
                        actor_user_id=actor,
                        account_id=target,
                        input=PermissionOverrideCreate(
                            permission_id=PERMISSIONS["procurement.read"].id,
                            effect="allow",
                            scope_kind="global",
                        ),
                    )
                else:
                    service.remove_override(
                        actor_user_id=actor,
                        account_id=target,
                        override_id=existing,
                        before_delete=lambda _: None,
                    )
                return "ok"
            except RbacServiceError as exc:
                return exc.code

    try:
        with Session(postgres_engine) as blocker:
            blocker.scalar(select(Person).where(Person.id == people[0]).with_for_update())
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(mutate)
                try:
                    assert started.wait(5)
                    waiting = False
                    deadline = monotonic() + 5
                    with postgres_engine.connect() as observer:
                        while monotonic() < deadline:
                            waiting = observer.scalar(
                                text(
                                    "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                                ),
                                {"pid": backend_pid[0]},
                            )
                            observer.commit()
                            if waiting:
                                break
                            sleep(0.01)
                    assert waiting, "override mutation did not reach the actual identity lock wait"
                    if change == "revoke_actor":
                        blocker.execute(
                            delete(UserPermissionOverride).where(
                                UserPermissionOverride.user_id == actor,
                                UserPermissionOverride.permission_id
                                == PERMISSIONS["permissions.manage"].id,
                            )
                        )
                    else:
                        blocker.add(UserRole(user_id=target, role_id=ROLES["finance"].id))
                    blocker.commit()
                finally:
                    blocker.rollback()
                assert future.result(timeout=10) == "permission_delegation_denied"
        with Session(postgres_engine) as verify:
            assert verify.get(UserPermissionOverride, existing) is not None
            assert (
                verify.scalar(
                    select(UserPermissionOverride.id).where(
                        UserPermissionOverride.user_id == target,
                        UserPermissionOverride.permission_id == PERMISSIONS["procurement.read"].id,
                    )
                )
                is None
            )
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            name = cleanup.scalar(text("SELECT current_database()"))
            assert name == "erp_e2e" or name.endswith("_test")
            cleanup.execute(delete(UserRole).where(UserRole.user_id.in_([actor, target])))
            cleanup.execute(
                delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id.in_([actor, target])
                )
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id.in_([actor, target])))
            cleanup.execute(delete(Person).where(Person.id.in_(people)))
