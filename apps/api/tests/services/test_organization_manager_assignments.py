from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.models import UserAccount
from app.core.errors import ProblemError
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.revenue_share.models import RevenueShareOrganization


def make_account(db: Session, role: str) -> UserAccount:
    marker = uuid4().hex
    person = Person(
        full_name=f"负责人测试-{role}",
        identity_ciphertext=f"test-{marker}",
        identity_digest=marker,
        sex="male",
        date_of_birth=date(1990, 1, 1),
    )
    db.add(person)
    db.flush()
    account = UserAccount(person_id=person.id, password_hash="test", status="active")
    db.add(account)
    db.flush()
    db.add(UserRole(user_id=account.id, role_id=ROLES[role].id))
    db.flush()
    return account


@pytest.fixture
def manager_scenario(db: Session):
    actor = make_account(db, "super_admin")
    target = make_account(db, "registered_user")
    organization = RevenueShareOrganization(
        name=f"负责人机构-{uuid4().hex}",
        created_by_user_id=actor.id,
    )
    db.add(organization)
    db.commit()
    return actor, target, organization


def manager_service(db: Session):
    # Deferred import keeps RED a genuine missing-feature test failure.
    from app.revenue_share.organization_manager_service import OrganizationManagerService

    return OrganizationManagerService(db)


def assignments(db: Session):
    from app.revenue_share.models import RevenueShareOrganizationManagerAssignment

    return list(db.scalars(select(RevenueShareOrganizationManagerAssignment)))


def grant(db: Session, scenario, **kwargs):
    actor, target, organization = scenario
    return manager_service(db).grant_manager(
        actor_user_id=actor.id,
        organization_id=organization.id,
        account_id=target.id,
        request_id="test-manager",
        **kwargs,
    )


def test_grant_twice_has_one_active_assignment_and_one_audit(db: Session, manager_scenario):
    first = grant(db, manager_scenario)
    second = grant(db, manager_scenario)
    assert first == second
    assert len(assignments(db)) == 1
    assert assignments(db)[0].revoked_at is None
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "referral_organization_manager.assign",
            )
        )
        == 1
    )
    assert set(first.model_dump()) == {
        "account_id",
        "organization_id",
        "full_name",
        "nickname",
        "granted_at",
    }


def test_revoke_then_regrant_preserves_history(db: Session, manager_scenario):
    actor, target, organization = manager_scenario
    now = datetime.now(UTC)
    grant(db, manager_scenario, occurred_at=now)
    for _ in range(2):
        manager_service(db).revoke_manager(
            actor_user_id=actor.id,
            organization_id=organization.id,
            account_id=target.id,
            request_id="test-revoke",
            occurred_at=now + timedelta(seconds=1),
        )
    grant(db, manager_scenario, occurred_at=now + timedelta(seconds=2))
    rows = assignments(db)
    assert len(rows) == 2
    assert sum(row.revoked_at is None for row in rows) == 1
    revoked = next(row for row in rows if row.revoked_at is not None)
    assert revoked.revoked_by_user_id == actor.id
    assert revoked.revoked_at == now + timedelta(seconds=1)
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "referral_organization_manager.revoke",
            )
        )
        == 1
    )


@pytest.mark.parametrize("role", ["admin", "finance", "registered_user"])
def test_real_admin_and_finance_cannot_manage_organization_managers(
    db: Session,
    manager_scenario,
    role: str,
):
    _, target, organization = manager_scenario
    actor = make_account(db, role)
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["revenue_share.manage"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.commit()
    service = manager_service(db)
    for operation, kwargs in (
        (service.list_managers, {"organization_id": organization.id}),
        (service.list_candidates, {"q": "", "page": 1, "page_size": 20}),
        (service.grant_manager, {"organization_id": organization.id, "account_id": target.id}),
        (service.revoke_manager, {"organization_id": organization.id, "account_id": target.id}),
    ):
        with pytest.raises(ProblemError) as denied:
            operation(actor_user_id=actor.id, request_id="test-forbidden", **kwargs)
        assert denied.value.status == 403
    assert assignments(db) == []


@pytest.mark.parametrize(
    "change",
    [
        "actor_role",
        "actor_disabled",
        "actor_deleted",
        "target_disabled",
        "target_deleted",
        "permission_deny",
    ],
)
def test_write_rechecks_actor_role_and_target_status(db: Session, manager_scenario, change: str):
    actor, target, _ = manager_scenario
    if change == "actor_role":
        db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    elif change.endswith("disabled"):
        account = actor if change.startswith("actor") else target
        db.execute(
            update(UserAccount).where(UserAccount.id == account.id).values(status="disabled"),
            execution_options={"synchronize_session": False},
        )
    elif change.endswith("deleted"):
        account = actor if change.startswith("actor") else target
        db.execute(
            update(Person)
            .where(Person.id == account.person_id)
            .values(deleted_at=datetime.now(UTC)),
            execution_options={"synchronize_session": False},
        )
    else:
        db.add(
            UserPermissionOverride(
                user_id=actor.id,
                permission_id=PERMISSIONS["revenue_share.manage"].id,
                effect="deny",
                scope_kind="global",
            )
        )
        db.flush()
    with pytest.raises(ProblemError) as denied:
        grant(db, manager_scenario)
    assert denied.value.status == (404 if change.startswith("target") else 403)
    assert assignments(db) == []


def test_inactive_organization_cannot_grant_but_can_revoke(db: Session, manager_scenario):
    actor, target, organization = manager_scenario
    grant(db, manager_scenario)
    organization.is_active = False
    db.commit()
    with pytest.raises(ProblemError) as denied:
        grant(db, manager_scenario)
    assert denied.value.status == 409
    manager_service(db).revoke_manager(
        actor_user_id=actor.id,
        organization_id=organization.id,
        account_id=target.id,
        request_id="test-inactive-revoke",
    )
    assert assignments(db)[0].revoked_at is not None


@pytest.mark.parametrize("change", ["actor_role", "permission_deny", "target_disabled"])
def test_grant_rechecks_changes_after_precheck_before_write(
    db, manager_scenario, monkeypatch, change
):
    from app.identity.readiness import GuardianReadinessPolicy

    actor, target, _ = manager_scenario
    original = GuardianReadinessPolicy.lock_accounts

    def lock_and_change(policy, person_ids):
        rows = original(policy, person_ids)
        if change == "actor_role":
            db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
        elif change == "target_disabled":
            db.execute(
                update(UserAccount).where(UserAccount.id == target.id).values(status="disabled"),
                execution_options={"synchronize_session": False},
            )
        else:
            db.add(
                UserPermissionOverride(
                    user_id=actor.id,
                    permission_id=PERMISSIONS["revenue_share.manage"].id,
                    effect="deny",
                    scope_kind="global",
                )
            )
            db.flush()
        return rows

    monkeypatch.setattr(GuardianReadinessPolicy, "lock_accounts", lock_and_change)
    with pytest.raises(ProblemError) as denied:
        grant(db, manager_scenario)
    assert denied.value.status == (404 if change == "target_disabled" else 403)
    assert assignments(db) == []


def test_assignment_and_audit_are_one_transaction(db: Session, manager_scenario):
    actor, target, organization = manager_scenario
    with pytest.raises(AuditMetadataRejected):
        manager_service(db).grant_manager(
            actor_user_id=actor.id,
            account_id=target.id,
            organization_id=organization.id,
            request_id="phone=13900000000",
        )
    assert assignments(db) == []
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "referral_organization_manager.assign",
            )
        )
        == 0
    )


def test_revoke_disabled_deleted_target_preserves_audit_identifiers(db: Session, manager_scenario):
    actor, target, organization = manager_scenario
    grant(db, manager_scenario)
    target.status = "disabled"
    db.execute(
        update(Person).where(Person.id == target.person_id).values(deleted_at=datetime.now(UTC))
    )
    db.commit()
    manager_service(db).revoke_manager(
        actor_user_id=actor.id,
        account_id=target.id,
        organization_id=organization.id,
        request_id="test-revoke-disabled",
    )
    revoked = assignments(db)[0]
    assert revoked.revoked_at is not None
    audit = db.scalar(
        select(AuditEvent).where(AuditEvent.action == "referral_organization_manager.revoke")
    )
    assert audit.event_metadata == {
        "organization_id": str(organization.id),
        "target_account_id": str(target.id),
        "assignment_id": str(revoked.id),
    }


def test_database_rejects_duplicate_active_and_invalid_revocation(db: Session, manager_scenario):
    from app.revenue_share.models import RevenueShareOrganizationManagerAssignment as Assignment

    actor, target, organization = manager_scenario
    grant(db, manager_scenario)
    now = datetime.now(UTC)
    for fields in (
        {},
        {"revoked_at": now},
        {"revoked_by_user_id": actor.id},
        {"revoked_at": now - timedelta(days=1), "revoked_by_user_id": actor.id},
    ):
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Assignment(
                    organization_id=organization.id,
                    account_id=target.id,
                    granted_at=now,
                    granted_by_user_id=actor.id,
                    **fields,
                )
            )
            db.flush()


def test_concurrent_grant_independent_connections_has_one_assignment_and_audit(
    postgres_engine: Engine,
):
    from app.revenue_share.models import RevenueShareOrganizationManagerAssignment as Assignment

    with Session(postgres_engine) as setup:
        actor = make_account(setup, "super_admin")
        target = make_account(setup, "registered_user")
        organization = RevenueShareOrganization(
            name=f"并发负责人-{uuid4().hex}", created_by_user_id=actor.id
        )
        setup.add(organization)
        setup.commit()
        actor_id, target_id, org_id = actor.id, target.id, organization.id
        person_ids = (actor.person_id, target.person_id)
    barrier = Barrier(2)

    def perform():
        with Session(postgres_engine) as connection:
            barrier.wait(timeout=10)
            return manager_service(connection).grant_manager(
                actor_user_id=actor_id,
                organization_id=org_id,
                account_id=target_id,
                request_id="test-concurrent-manager",
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(perform) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        assert results[0] == results[1]
        with Session(postgres_engine) as check:
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(Assignment)
                    .where(
                        Assignment.organization_id == org_id,
                        Assignment.revoked_at.is_(None),
                    )
                )
                == 1
            )
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.actor_user_id == actor_id,
                        AuditEvent.action == "referral_organization_manager.assign",
                    )
                )
                == 1
            )
    finally:
        with Session(postgres_engine) as cleanup:
            # Isolated test database only: committed concurrency fixtures must
            # be removed without weakening the production append-only trigger.
            cleanup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            try:
                cleanup.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == actor_id))
            finally:
                cleanup.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            cleanup.execute(delete(Assignment).where(Assignment.organization_id == org_id))
            cleanup.execute(
                delete(RevenueShareOrganization).where(RevenueShareOrganization.id == org_id)
            )
            cleanup.execute(delete(UserRole).where(UserRole.user_id.in_((actor_id, target_id))))
            cleanup.execute(delete(UserAccount).where(UserAccount.id.in_((actor_id, target_id))))
            cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))
            cleanup.commit()
