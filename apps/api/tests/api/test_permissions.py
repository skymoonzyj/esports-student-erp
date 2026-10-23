from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.rbac.router as rbac_router
from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService, LastActiveAdminProtected
from app.housing.seed_inventory import FLOOR_SEEDS, INITIAL_BUILDINGS
from app.identity.models import GuardianRelationship
from app.identity.schemas import StudentCreate
from app.identity.service import IdentityService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.cohort_service import CohortService
from app.teaching.schemas import CohortCreate, TeachingClassCreate, TeachingClassUpdate

ADMIN_PASSWORD = "AdminA1x26"
OTHER_PASSWORD = "OtherA1x26"


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


@pytest.fixture
def permission_accounts(db: Session):
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
    )
    accounts = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 26),
    )

    def create_account(name: str, sequence: int, phone: str, password: str):
        person = identity.create_student(
            StudentCreate(
                full_name=name,
                identity_number=make_cn_id("19900102", sequence),
                sex="female" if sequence % 2 == 0 else "male",
                date_of_birth=date(1990, 1, 2),
                student_number=f"PERM-{sequence}",
            )
        )
        created = accounts.create_account(
            AccountCreate(
                person_id=person.id,
                login_phone=phone,
                email=f"perm-{sequence}@example.com",
            )
        )
        stored = db.get(UserAccount, created.account.id)
        assert stored is not None
        stored.password_hash = PasswordService().hash(password)
        stored.force_password_change = False
        db.commit()
        return stored

    admin = create_account("权限管理员", 102, "19990000001", ADMIN_PASSWORD)
    other = create_account("普通账号", 104, "19990000004", OTHER_PASSWORD)
    db.add(UserRole(user_id=admin.id, role_id=ROLES["admin"].id))
    db.commit()
    return {"admin": admin, "other": other, "accounts": accounts, "identity": identity}


def sign_in(client: TestClient, phone: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def sign_in_platform_user(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    return sign_in(client, phone, password)


def test_admissions_amount_visibility_is_an_independent_permission(
    db: Session,
    platform_users,
) -> None:
    customer = platform_users["accounts"]["customer_service"]
    admin = platform_users["accounts"]["admin"]

    customer_visibility = RbacService(db).admissions_visibility(user_id=customer.id)
    assert customer_visibility.can_read is True
    assert customer_visibility.can_manage is False
    assert customer_visibility.can_read_amounts is False

    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    admin_visibility = RbacService(db).admissions_visibility(user_id=admin.id)
    assert admin_visibility.can_read is True
    assert admin_visibility.can_manage is True
    assert admin_visibility.can_read_amounts is False


def create_permission_scope_class(
    db: Session,
    *,
    actor_user_id: UUID,
    name: str,
    is_active: bool = True,
):
    now = datetime(2027, 1, 2, 8, tzinfo=UTC)
    service = CohortService(db)
    cohort = service.create_cohort(
        CohortCreate(
            name=f"{name}营期",
            starts_at=now + timedelta(days=10),
            ends_at=now + timedelta(days=30),
            default_check_in_at=now + timedelta(days=10),
            default_check_out_at=now + timedelta(days=30),
        ),
        actor_user_id=actor_user_id,
    )
    teaching_class = service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=UUID("30000000-0000-4000-8000-000000000001"),
            name=name,
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
        ),
        actor_user_id=actor_user_id,
    )
    if not is_active:
        teaching_class = service.update_class(
            teaching_class.id,
            TeachingClassUpdate(is_active=False),
            actor_user_id=actor_user_id,
        )
    return cohort, teaching_class


def assert_minor_account_was_disabled(
    db: Session,
    account_id: UUID,
) -> None:
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert account.status == "disabled"
    assert (
        db.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.user_id == account_id,
                AuthSession.revoked_at.is_(None),
            )
        )
        == 0
    )


def test_removing_guardian_role_disables_minor(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    csrf = sign_in_platform_user(db_client, platform_users, "admin")
    guardian_account = platform_users["accounts"]["guardian"]

    response = db_client.delete(
        f"/api/v1/accounts/{guardian_account.id}/roles/{ROLES['guardian'].id}",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "remove-guardian-role"},
    )

    assert response.status_code == 204
    assert_minor_account_was_disabled(db, minor_login.account_id)
    actions = list(
        db.scalars(
            select(AuditEvent.action).where(
                AuditEvent.resource_id.in_([guardian_account.id, minor_login.account_id])
            )
        )
    )
    assert "account.role.remove" in actions
    assert actions.count("account.auto_disable.guardian_missing") == 1


def test_family_permission_override_does_not_change_guardian_readiness(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    csrf = sign_in_platform_user(db_client, platform_users, "admin")
    guardian_account = platform_users["accounts"]["guardian"]
    created = db_client.post(
        f"/api/v1/accounts/{guardian_account.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "deny",
            "scope_kind": "family",
            "scope_id": None,
        },
    )
    assert created.status_code == 201

    removed = db_client.delete(
        f"/api/v1/accounts/{guardian_account.id}/permission-overrides/{created.json()['id']}",
        headers={"X-CSRF-Token": csrf},
    )

    assert removed.status_code == 204
    account = db.get(UserAccount, minor_login.account_id)
    assert account is not None and account.status == "active"
    assert (
        db.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.user_id == minor_login.account_id,
                AuthSession.revoked_at.is_(None),
            )
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.resource_id == minor_login.account_id,
                AuditEvent.action == "account.auto_disable.guardian_missing",
            )
        )
        == 0
    )


def test_login_exposes_effective_global_navigation_permissions(
    db_client: TestClient,
    permission_accounts,
) -> None:
    del permission_accounts

    response = db_client.post(
        "/api/v1/auth/login",
        json={"phone": "19990000001", "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 200
    assert set(response.json()["user"]["permissions"]) == set(PERMISSIONS)


def test_admin_lists_and_assigns_roles(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    roles = db_client.get("/api/v1/roles")
    permissions = db_client.get("/api/v1/permissions")
    assigned = db_client.post(
        f"/api/v1/accounts/{permission_accounts['other'].id}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_id": str(ROLES["coach"].id)},
    )

    assert roles.status_code == 200
    assert {item["key"] for item in roles.json()} == set(ROLES)
    assert {item["key"] for item in permissions.json()} == set(PERMISSIONS)
    assert assigned.status_code == 204
    assert db.get(UserRole, (permission_accounts["other"].id, ROLES["coach"].id)) is not None
    removed = db_client.delete(
        f"/api/v1/accounts/{permission_accounts['other'].id}/roles/{ROLES['coach'].id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert removed.status_code == 204
    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_id == permission_accounts["other"].id,
                AuditEvent.action.in_({"account.role.assign", "account.role.remove"}),
            )
        )
    )
    assert {event.action for event in events} == {
        "account.role.assign",
        "account.role.remove",
    }
    assert all(
        event.resource_type == "account"
        and event.outcome == "allowed"
        and event.event_metadata == {"role_key": "coach"}
        for event in events
    )


def test_admin_adds_and_removes_permission_override(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    created = db_client.post(
        f"/api/v1/accounts/{permission_accounts['other'].id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "allow",
            "scope_kind": "person",
            "scope_id": str(permission_accounts["admin"].person_id),
        },
    )

    assert created.status_code == 201
    override_id = created.json()["id"]
    removed = db_client.delete(
        f"/api/v1/accounts/{permission_accounts['other'].id}/permission-overrides/{override_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert removed.status_code == 204
    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_id == permission_accounts["other"].id,
                AuditEvent.action.in_(
                    {"account.permission_override.add", "account.permission_override.remove"}
                ),
            )
        )
    )
    assert {event.action for event in events} == {
        "account.permission_override.add",
        "account.permission_override.remove",
    }
    assert all(
        event.resource_type == "account"
        and event.outcome == "allowed"
        and event.event_metadata
        == {
            "permission_key": "people.read",
            "effect": "allow",
            "scope_kind": "person",
            "scope_id": str(permission_accounts["admin"].person_id),
        }
        for event in events
    )


def test_sensitive_evaluation_override_requires_and_returns_a_finite_grant(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    admin = permission_accounts["admin"]
    target = permission_accounts["other"]
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    payload = {
        "permission_id": str(PERMISSIONS["student_game_accounts.manage"].id),
        "effect": "allow",
        "scope_kind": "project",
        "scope_id": "30000000-0000-4000-8000-000000000001",
        "valid_from": "2026-08-01T00:00:00+08:00",
        "valid_until": "2026-08-22T00:00:00+08:00",
        "grant_reason": "temporary project support",
    }

    preview = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides/preview",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    created = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "timed-evaluation-grant"},
        json=payload,
    )
    access = db_client.get(f"/api/v1/accounts/{target.id}/access")

    assert preview.status_code == 200
    assert preview.json() == {"allowed": True, "reason": "account_allow"}
    assert created.status_code == 201
    assert created.json()["valid_from"] == payload["valid_from"]
    assert created.json()["valid_until"] == payload["valid_until"]
    assert created.json()["grant_reason"] == payload["grant_reason"]
    assert created.json()["scope_label"] == "英雄联盟"
    assert access.status_code == 200
    assert access.json()["permission_overrides"] == [created.json()]
    stored = db.get(UserPermissionOverride, UUID(created.json()["id"]))
    assert stored is not None
    assert stored.created_by_user_id == admin.id
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "account.permission_override.add",
            AuditEvent.request_id == "timed-evaluation-grant",
        )
    )
    assert event is not None
    assert event.event_metadata == {
        "permission_key": "student_game_accounts.manage",
        "effect": "allow",
        "scope_kind": "project",
        "scope_id": payload["scope_id"],
    }
    assert payload["grant_reason"] not in str(event.event_metadata)


@pytest.mark.parametrize(
    ("payload_update", "expected_code"),
    [
        ({"valid_from": None}, "validation_error"),
        ({"valid_until": None}, "invalid_permission_override"),
        ({"grant_reason": None}, "invalid_permission_override"),
        ({"scope_kind": "global", "scope_id": None}, "invalid_permission_override"),
        (
            {"grant_reason": "contact 13800138000 for approval"},
            "invalid_permission_override",
        ),
    ],
)
def test_sensitive_evaluation_override_rejects_unsafe_or_indefinite_allows(
    db_client: TestClient,
    permission_accounts,
    payload_update: dict[str, object],
    expected_code: str,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    payload = {
        "permission_id": str(PERMISSIONS["student_game_accounts.manage"].id),
        "effect": "allow",
        "scope_kind": "project",
        "scope_id": "30000000-0000-4000-8000-000000000001",
        "valid_from": "2026-08-01T00:00:00+08:00",
        "valid_until": "2026-08-22T00:00:00+08:00",
        "grant_reason": "temporary project support",
        **payload_update,
    }

    response = db_client.post(
        f"/api/v1/accounts/{permission_accounts['other'].id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["code"] == expected_code


def test_any_permission_override_reason_rejects_sensitive_data(
    db_client: TestClient,
    permission_accounts,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    response = db_client.post(
        f"/api/v1/accounts/{permission_accounts['other'].id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "deny",
            "scope_kind": "global",
            "scope_id": None,
            "grant_reason": "contact 13800138000 for approval",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_permission_override"


def test_permissions_manager_cannot_delegate_an_evaluation_permission_they_lack(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    csrf = sign_in(db_client, "19990000004", OTHER_PASSWORD)

    response = db_client.post(
        f"/api/v1/accounts/{manager.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["student_game_accounts.manage"].id),
            "effect": "allow",
            "scope_kind": "project",
            "scope_id": "30000000-0000-4000-8000-000000000001",
            "valid_from": "2026-08-01T00:00:00+08:00",
            "valid_until": "2026-08-22T00:00:00+08:00",
            "grant_reason": "temporary project support",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_delegation_denied"
    assert (
        db.scalar(
            select(UserPermissionOverride.id).where(
                UserPermissionOverride.user_id == manager.id,
                UserPermissionOverride.permission_id
                == PERMISSIONS["student_game_accounts.manage"].id,
            )
        )
        is None
    )


def test_family_allow_requires_global_delegation_and_denial_is_atomic(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    manager = platform_users["accounts"]["guardian"]
    target = platform_users["accounts"]["coach"]
    student = platform_users["people"]["own_student"]
    now = datetime.now(UTC)
    db.add_all(
        [
            GuardianRelationship(
                guardian_person_id=target.person_id,
                student_person_id=student.id,
                relationship="guardian",
                is_primary=False,
                valid_from=date(2026, 1, 1),
            ),
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="allow",
                scope_kind="person",
                scope_id=target.person_id,
            ),
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["student_game_accounts.manage"].id,
                effect="allow",
                scope_kind="family",
                scope_id=None,
                valid_from=now - timedelta(days=1),
                valid_until=now + timedelta(days=8),
                created_by_user_id=platform_users["accounts"]["admin"].id,
                grant_reason="临时家庭游戏账号管理",
            ),
        ]
    )
    db.commit()
    payload = {
        "permission_id": str(PERMISSIONS["student_game_accounts.manage"].id),
        "effect": "allow",
        "scope_kind": "family",
        "scope_id": None,
        "valid_from": (now - timedelta(minutes=1)).isoformat(),
        "valid_until": (now + timedelta(days=7)).isoformat(),
        "grant_reason": "临时家庭游戏账号协助",
    }
    target_overrides = select(UserPermissionOverride.id).where(
        UserPermissionOverride.user_id == target.id,
        UserPermissionOverride.permission_id
        == PERMISSIONS["student_game_accounts.manage"].id,
    )
    target_audits = select(func.count(AuditEvent.id)).where(
        AuditEvent.action == "account.permission_override.add",
        AuditEvent.resource_id == target.id,
    )
    before_audits = db.scalar(target_audits)
    csrf = sign_in_platform_user(db_client, platform_users, "guardian")

    denied = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )

    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_delegation_denied"
    assert db.scalar(target_overrides) is None
    assert db.scalar(target_audits) == before_audits

    admin_csrf = sign_in_platform_user(db_client, platform_users, "admin")
    created = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": admin_csrf},
        json=payload,
    )

    assert created.status_code == 201
    assert created.json()["scope_kind"] == "family"
    assert db.scalar(target_overrides) is not None
    assert db.scalar(target_audits) == before_audits + 1


def test_admin_reads_an_accounts_current_access_snapshot(
    db_client: TestClient,
    permission_accounts,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    account_id = permission_accounts["other"].id
    assert (
        db_client.post(
            f"/api/v1/accounts/{account_id}/roles",
            headers={"X-CSRF-Token": csrf},
            json={"role_id": str(ROLES["coach"].id)},
        ).status_code
        == 204
    )
    created = db_client.post(
        f"/api/v1/accounts/{account_id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "deny",
            "scope_kind": "global",
            "scope_id": None,
        },
    )

    response = db_client.get(f"/api/v1/accounts/{account_id}/access")

    assert created.status_code == 201
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert [role["key"] for role in response.json()["roles"]] == ["coach"]
    assert response.json()["permission_overrides"] == [created.json()]


def test_person_scoped_deny_overrides_global_permission_management(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    admin = permission_accounts["admin"]
    target = permission_accounts["other"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="deny",
            scope_kind="person",
            scope_id=target.person_id,
        )
    )
    db.commit()
    sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    catalog = db_client.get("/api/v1/roles")
    target_access = db_client.get(f"/api/v1/accounts/{target.id}/access")
    directory = db_client.get("/api/v1/permission-account-directory")

    assert catalog.status_code == 200
    assert target_access.status_code == 403
    assert target_access.json()["code"] == "permission_denied"
    assert str(target.id) not in {item["id"] for item in directory.json()}


def test_person_scoped_deny_blocks_global_manager_role_mutations(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["admin"]
    target = permission_accounts["other"]
    db.add_all(
        [
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="deny",
                scope_kind="person",
                scope_id=target.person_id,
            ),
            UserRole(user_id=target.id, role_id=ROLES["coach"].id),
        ]
    )
    db.commit()
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    assigned = db_client.post(
        f"/api/v1/accounts/{target.id}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_id": str(ROLES["staff"].id)},
    )
    removed = db_client.delete(
        f"/api/v1/accounts/{target.id}/roles/{ROLES['coach'].id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert assigned.status_code == 403
    assert assigned.json()["code"] == "permission_denied"
    assert removed.status_code == 403
    assert removed.json()["code"] == "permission_denied"
    assert db.get(UserRole, (target.id, ROLES["staff"].id)) is None
    assert db.get(UserRole, (target.id, ROLES["coach"].id)) is not None


def test_family_scoped_permission_management_uses_real_relationship_tokens(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["admin"]
    target = permission_accounts["other"]
    db.delete(db.get(UserRole, (manager.id, ROLES["admin"].id)))
    relationship = GuardianRelationship(
        guardian_person_id=manager.person_id,
        student_person_id=target.person_id,
        relationship="guardian",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    db.add(relationship)
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="family",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    response = db_client.get(f"/api/v1/accounts/{target.id}/access")
    relationship.ended_at = datetime(2026, 7, 27, 2, tzinfo=UTC)
    db.commit()
    after_end = db_client.get(f"/api/v1/accounts/{target.id}/access")

    assert response.status_code == 200
    assert after_end.status_code == 403
    assert after_end.json()["code"] == "permission_denied"


def test_family_scoped_deny_overrides_global_permission_management(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["admin"]
    target = permission_accounts["other"]
    db.add(
        GuardianRelationship(
            guardian_person_id=manager.person_id,
            student_person_id=target.person_id,
            relationship="guardian",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="deny",
            scope_kind="family",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    response = db_client.get(f"/api/v1/accounts/{target.id}/access")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_available_permissions_uses_the_real_self_resource(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    db.add_all(
        [
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="allow",
                scope_kind="self",
                scope_id=None,
            ),
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="deny",
                scope_kind="person",
                scope_id=manager.person_id,
            ),
        ]
    )
    db.commit()

    login = db_client.post(
        "/api/v1/auth/login",
        json={"phone": "19990000004", "password": OTHER_PASSWORD},
    )
    roles = db_client.get("/api/v1/roles")

    assert login.status_code == 200
    assert "permissions.manage" not in login.json()["user"]["scoped_permissions"]
    assert roles.status_code == 403


def test_permission_preview_evaluates_family_denies_for_a_person_target(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    target_account = permission_accounts["other"]
    scoped_person = permission_accounts["identity"].create_student(
        StudentCreate(
            full_name="家庭范围预览学员",
            identity_number=make_cn_id("20120102", 118),
            sex="female",
            date_of_birth=date(2012, 1, 2),
            student_number="PERM-118",
        )
    )
    db.add(
        GuardianRelationship(
            guardian_person_id=target_account.person_id,
            student_person_id=scoped_person.id,
            relationship="guardian",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=target_account.id,
            permission_id=PERMISSIONS["people.read"].id,
            effect="deny",
            scope_kind="family",
            scope_id=None,
        )
    )
    db.commit()
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    response = db_client.post(
        f"/api/v1/accounts/{target_account.id}/permission-overrides/preview",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "allow",
            "scope_kind": "person",
            "scope_id": str(scoped_person.id),
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {"allowed": False, "reason": "explicit_deny"}


def test_permission_change_cannot_remove_the_last_effective_recovery_admin(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    actor = permission_accounts["admin"]
    target = permission_accounts["other"]
    db.add(UserRole(user_id=target.id, role_id=ROLES["admin"].id))
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    response = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["permissions.manage"].id),
            "effect": "deny",
            "scope_kind": "global",
            "scope_id": None,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin_protected"
    assert (
        db.scalar(
            select(UserPermissionOverride.id).where(
                UserPermissionOverride.user_id == target.id,
                UserPermissionOverride.permission_id == PERMISSIONS["permissions.manage"].id,
                UserPermissionOverride.effect == "deny",
                UserPermissionOverride.scope_kind == "global",
            )
        )
        is None
    )


def test_person_scoped_allow_authorizes_only_the_selected_account(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    target = permission_accounts["admin"]
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="person",
            scope_id=target.person_id,
        )
    )
    db.commit()
    login = db_client.post(
        "/api/v1/auth/login",
        json={"phone": "19990000004", "password": OTHER_PASSWORD},
    )

    target_access = db_client.get(f"/api/v1/accounts/{target.id}/access")
    own_access = db_client.get(f"/api/v1/accounts/{manager.id}/access")
    directory = db_client.get("/api/v1/permission-account-directory")
    catalog = db_client.get("/api/v1/roles")

    assert login.status_code == 200
    assert "permissions.manage" in login.json()["user"]["scoped_permissions"]
    assert target_access.status_code == 200
    assert own_access.status_code == 403
    assert [item["id"] for item in directory.json()] == [str(target.id)]
    assert catalog.status_code == 200


@pytest.mark.parametrize("scope_kind", ["person", "self", "family"])
def test_scoped_permission_manager_cannot_assign_or_remove_global_roles(
    db_client: TestClient,
    permission_accounts,
    db: Session,
    scope_kind: str,
) -> None:
    manager = permission_accounts["other"]
    target = manager if scope_kind == "self" else permission_accounts["admin"]
    if scope_kind == "family":
        db.add(
            GuardianRelationship(
                guardian_person_id=manager.person_id,
                student_person_id=target.person_id,
                relationship="guardian",
                is_primary=True,
                valid_from=date(2026, 1, 1),
                valid_until=None,
            )
        )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="allow",
                scope_kind=scope_kind,
                scope_id=target.person_id if scope_kind == "person" else None,
            ),
            UserRole(user_id=target.id, role_id=ROLES["coach"].id),
        ]
    )
    db.commit()
    csrf = sign_in(db_client, "19990000004", OTHER_PASSWORD)

    assigned = db_client.post(
        f"/api/v1/accounts/{target.id}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_id": str(ROLES["staff"].id)},
    )
    removed = db_client.delete(
        f"/api/v1/accounts/{target.id}/roles/{ROLES['coach'].id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert assigned.status_code == 403
    assert assigned.json()["code"] == "permission_denied"
    assert removed.status_code == 403
    assert removed.json()["code"] == "permission_denied"
    assert db.get(UserRole, (target.id, ROLES["staff"].id)) is None
    assert db.get(UserRole, (target.id, ROLES["coach"].id)) is not None


def test_scoped_manager_needs_matching_delegation_to_remove_deny_but_can_remove_allow(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    target = permission_accounts["admin"]
    valid_from = datetime.now(UTC) - timedelta(days=2)
    valid_until = datetime.now(UTC) + timedelta(days=2)
    deny = UserPermissionOverride(
        user_id=target.id,
        permission_id=PERMISSIONS["people.read"].id,
        effect="deny",
        scope_kind="person",
        scope_id=target.person_id,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    allow = UserPermissionOverride(
        user_id=target.id,
        permission_id=PERMISSIONS["teaching.read"].id,
        effect="allow",
        scope_kind="person",
        scope_id=target.person_id,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="allow",
                scope_kind="person",
                scope_id=target.person_id,
            ),
            deny,
            allow,
        ]
    )
    db.commit()
    csrf = sign_in(db_client, "19990000004", OTHER_PASSWORD)

    denied_removal = db_client.delete(
        f"/api/v1/accounts/{target.id}/permission-overrides/{deny.id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert denied_removal.status_code == 403
    assert denied_removal.json()["code"] == "permission_delegation_denied"
    assert db.get(UserPermissionOverride, deny.id) is not None
    assert db.get(UserPermissionOverride, allow.id) is not None

    narrowed = db_client.delete(
        f"/api/v1/accounts/{target.id}/permission-overrides/{allow.id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert narrowed.status_code == 204
    assert db.get(UserPermissionOverride, allow.id) is None

    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["people.read"].id,
            effect="allow",
            scope_kind="person",
            scope_id=target.person_id,
            valid_from=valid_from,
            valid_until=valid_until,
        )
    )
    db.commit()

    delegated_removal = db_client.delete(
        f"/api/v1/accounts/{target.id}/permission-overrides/{deny.id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert delegated_removal.status_code == 204
    assert db.get(UserPermissionOverride, deny.id) is None


@pytest.mark.parametrize(
    ("authority_scope", "authority_start_offset", "authority_end_offset"),
    [
        ("wrong_person", timedelta(0), timedelta(0)),
        ("target_person", timedelta(hours=1), timedelta(0)),
        ("target_person", timedelta(0), -timedelta(hours=1)),
    ],
    ids=["wrong-scope", "starts-late", "ends-early"],
)
def test_deny_removal_rejects_wrong_scope_or_incomplete_delegation_window(
    db_client: TestClient,
    permission_accounts,
    db: Session,
    authority_scope: str,
    authority_start_offset: timedelta,
    authority_end_offset: timedelta,
) -> None:
    manager = permission_accounts["other"]
    target = permission_accounts["admin"]
    valid_from = datetime.now(UTC) - timedelta(days=2)
    valid_until = datetime.now(UTC) + timedelta(days=2)
    deny = UserPermissionOverride(
        user_id=target.id,
        permission_id=PERMISSIONS["people.read"].id,
        effect="deny",
        scope_kind="person",
        scope_id=target.person_id,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="allow",
                scope_kind="person",
                scope_id=target.person_id,
            ),
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["people.read"].id,
                effect="allow",
                scope_kind="person",
                scope_id=(
                    manager.person_id
                    if authority_scope == "wrong_person"
                    else target.person_id
                ),
                valid_from=valid_from + authority_start_offset,
                valid_until=valid_until + authority_end_offset,
            ),
            deny,
        ]
    )
    db.commit()
    csrf = sign_in(db_client, "19990000004", OTHER_PASSWORD)

    response = db_client.delete(
        f"/api/v1/accounts/{target.id}/permission-overrides/{deny.id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_delegation_denied"
    assert db.get(UserPermissionOverride, deny.id) is not None
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "account.permission_override.remove",
                AuditEvent.resource_id == target.id,
            )
        )
        == 0
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/permission-class-directory",
        "/api/v1/permission-cohort-directory",
        "/api/v1/permission-project-directory",
        "/api/v1/permission-enrollment-directory",
    ],
)
def test_scoped_permission_manager_cannot_read_organization_scope_directories(
    db_client: TestClient,
    permission_accounts,
    db: Session,
    path: str,
) -> None:
    manager = permission_accounts["other"]
    target = permission_accounts["admin"]
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="person",
            scope_id=target.person_id,
        )
    )
    db.commit()
    sign_in(db_client, "19990000004", OTHER_PASSWORD)

    response = db_client.get(path)

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_account_access_labels_a_person_scope_unambiguously(
    db_client: TestClient,
    permission_accounts,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    target = permission_accounts["other"]
    person = permission_accounts["admin"]
    created = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "allow",
            "scope_kind": "person",
            "scope_id": str(person.person_id),
        },
    )

    access = db_client.get(f"/api/v1/accounts/{target.id}/access")

    assert created.status_code == 201
    assert created.json()["scope_label"] == "权限管理员"
    assert access.status_code == 200
    assert access.json()["permission_overrides"][0]["scope_label"] == "权限管理员"


def test_person_scope_rejects_a_missing_person(
    db_client: TestClient,
    permission_accounts,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    response = db_client.post(
        f"/api/v1/accounts/{permission_accounts['other'].id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "allow",
            "scope_kind": "person",
            "scope_id": "00000000-0000-4000-8000-000000000999",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "rbac_resource_not_found"


def test_assigned_class_scope_rejects_a_missing_class(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    target = permission_accounts["other"]
    class_id = "30000000-0000-4000-8000-000000000099"

    response = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "allow",
            "scope_kind": "assigned_class",
            "scope_id": class_id,
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "rbac_resource_not_found"
    assert (
        db.scalar(
            select(UserPermissionOverride.id).where(
                UserPermissionOverride.user_id == target.id,
                UserPermissionOverride.scope_kind == "assigned_class",
            )
        )
        is None
    )


@pytest.mark.parametrize("scope_kind", ["cohort", "project", "enrollment"])
def test_new_permission_scopes_reject_missing_targets(
    db_client: TestClient,
    permission_accounts,
    db: Session,
    scope_kind: str,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    target = permission_accounts["other"]

    response = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["people.read"].id),
            "effect": "allow",
            "scope_kind": scope_kind,
            "scope_id": "00000000-0000-4000-8000-000000000999",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "rbac_resource_not_found"
    assert (
        db.scalar(
            select(UserPermissionOverride.id).where(
                UserPermissionOverride.user_id == target.id,
                UserPermissionOverride.scope_kind == scope_kind,
            )
        )
        is None
    )


def test_admin_adds_and_previews_a_real_assigned_class_scope(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    admin = permission_accounts["admin"]
    target = permission_accounts["other"]
    cohort, teaching_class = create_permission_scope_class(
        db,
        actor_user_id=admin.id,
        name="权限范围班",
    )
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    payload = {
        "permission_id": str(PERMISSIONS["teaching.read"].id),
        "effect": "allow",
        "scope_kind": "assigned_class",
        "scope_id": str(teaching_class.id),
    }

    directory = db_client.get(
        "/api/v1/permission-class-directory",
        params={"query": "权限范围", "page": 1, "page_size": 1},
    )
    preview = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides/preview",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    created = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )

    label = f"{cohort.name} · {teaching_class.name} · 英雄联盟"
    assert directory.status_code == 200
    assert directory.headers["cache-control"] == "no-store, private"
    assert directory.json() == {
        "items": [
            {
                "id": str(teaching_class.id),
                "class_name": teaching_class.name,
                "cohort_name": cohort.name,
                "project_name": "英雄联盟",
                "label": label,
                "is_active": True,
            }
        ],
        "page": 1,
        "page_size": 1,
        "total": 1,
    }
    assert preview.status_code == 200
    assert preview.json() == {"allowed": True, "reason": "account_allow"}
    assert created.status_code == 201
    assert created.json()["scope_label"] == label

    sign_in(db_client, "19990000004", OTHER_PASSWORD)
    class_detail = db_client.get(f"/api/v1/classes/{teaching_class.id}")
    class_list = db_client.get("/api/v1/classes")
    assert class_detail.status_code == 200
    assert class_list.status_code == 200
    assert class_list.json()["total"] == 1
    assert class_list.json()["items"][0]["id"] == str(teaching_class.id)


def test_admin_reads_cohort_project_and_enrollment_scope_directories(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    admin = permission_accounts["admin"]
    target = permission_accounts["other"]
    cohort, teaching_class = create_permission_scope_class(
        db,
        actor_user_id=admin.id,
        name="对象范围目录班",
    )
    admission_case = AdmissionCase(
        student_person_id=target.person_id,
        status="ready",
        created_by_user_id=admin.id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=target.person_id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=0,
        status="active",
        confirmed_at=datetime(2026, 8, 1, tzinfo=UTC),
        created_by_user_id=admin.id,
    )
    db.add(enrollment)
    db.commit()
    sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    cohort_response = db_client.get(
        "/api/v1/permission-cohort-directory",
        params={"query": "对象范围目录", "page": 1, "page_size": 20},
    )
    project_response = db_client.get(
        "/api/v1/permission-project-directory",
        params={"query": "英雄联盟", "page": 1, "page_size": 20},
    )
    enrollment_response = db_client.get(
        "/api/v1/permission-enrollment-directory",
        params={"query": "普通账号", "page": 1, "page_size": 20},
    )

    assert cohort_response.status_code == 200
    assert cohort_response.json() == {
        "items": [
            {
                "id": str(cohort.id),
                "name": cohort.name,
                "status": cohort.status,
                "label": cohort.name,
            }
        ],
        "page": 1,
        "page_size": 20,
        "total": 1,
    }
    assert project_response.status_code == 200
    assert project_response.json() == {
        "items": [
            {
                "id": "30000000-0000-4000-8000-000000000001",
                "name": "英雄联盟",
                "is_active": True,
                "label": "英雄联盟",
            }
        ],
        "page": 1,
        "page_size": 20,
        "total": 1,
    }
    assert enrollment_response.status_code == 200
    assert enrollment_response.json() == {
        "items": [
            {
                "id": str(enrollment.id),
                "student_name": "普通账号",
                "cohort_name": cohort.name,
                "class_name": teaching_class.name,
                "project_name": "英雄联盟",
                "status": "active",
                "label": (f"普通账号 · {cohort.name} · {teaching_class.name} · 英雄联盟"),
            }
        ],
        "page": 1,
        "page_size": 20,
        "total": 1,
    }
    for response in (cohort_response, project_response, enrollment_response):
        assert response.headers["cache-control"] == "no-store, private"


def test_inactive_class_rejects_new_allow_but_keeps_existing_label_and_deny(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    admin = permission_accounts["admin"]
    target = permission_accounts["other"]
    cohort, teaching_class = create_permission_scope_class(
        db,
        actor_user_id=admin.id,
        name="历史权限班",
    )
    _, already_inactive = create_permission_scope_class(
        db,
        actor_user_id=admin.id,
        name="停用待授权班",
        is_active=False,
    )
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    allow_payload = {
        "permission_id": str(PERMISSIONS["teaching.read"].id),
        "effect": "allow",
        "scope_kind": "assigned_class",
        "scope_id": str(teaching_class.id),
    }
    created = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json=allow_payload,
    )
    assert created.status_code == 201
    CohortService(db).update_class(
        teaching_class.id,
        TeachingClassUpdate(is_active=False),
        actor_user_id=admin.id,
    )
    existing_preview = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides/preview",
        headers={"X-CSRF-Token": csrf},
        json=allow_payload,
    )

    inactive_payload = {
        **allow_payload,
        "scope_id": str(already_inactive.id),
    }
    preview = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides/preview",
        headers={"X-CSRF-Token": csrf},
        json=inactive_payload,
    )
    rejected = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json=inactive_payload,
    )
    denied = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={**inactive_payload, "effect": "deny"},
    )
    access = db_client.get(f"/api/v1/accounts/{target.id}/access")
    directory = db_client.get(
        "/api/v1/permission-class-directory",
        params={"query": "停用待授权班"},
    )

    historical_label = f"{cohort.name} · {teaching_class.name} · 英雄联盟"
    assert existing_preview.status_code == 200
    assert existing_preview.json() == {"allowed": True, "reason": "account_allow"}
    assert preview.status_code == 409
    assert preview.json()["code"] == "permission_scope_inactive"
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "permission_scope_inactive"
    assert denied.status_code == 201
    assert denied.json()["scope_label"].endswith("停用待授权班 · 英雄联盟")
    assert access.status_code == 200
    assert historical_label in {
        item["scope_label"] for item in access.json()["permission_overrides"]
    }
    assert directory.status_code == 200
    assert directory.json()["total"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/permission-class-directory",
        "/api/v1/permission-cohort-directory",
        "/api/v1/permission-project-directory",
        "/api/v1/permission-enrollment-directory",
    ],
)
def test_account_without_permission_cannot_read_scope_directories(
    db_client: TestClient,
    permission_accounts,
    path: str,
) -> None:
    sign_in(db_client, "19990000004", OTHER_PASSWORD)

    response = db_client.get(path)

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


@pytest.mark.parametrize(
    ("scope_kind", "scope_id", "expected_label"),
    [
        (
            "building",
            INITIAL_BUILDINGS[0].id,
            INITIAL_BUILDINGS[0].name,
        ),
        (
            "floor",
            FLOOR_SEEDS[0].id,
            f"{INITIAL_BUILDINGS[0].name} · {FLOOR_SEEDS[0].name}",
        ),
    ],
)
def test_admin_adds_and_previews_real_housing_scopes(
    db_client: TestClient,
    permission_accounts,
    scope_kind: str,
    scope_id: UUID,
    expected_label: str,
) -> None:
    target = permission_accounts["other"]
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    payload = {
        "permission_id": str(PERMISSIONS["housing.read"].id),
        "effect": "allow",
        "scope_kind": scope_kind,
        "scope_id": str(scope_id),
    }

    preview = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides/preview",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    created = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    missing = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            **payload,
            "scope_id": "00000000-0000-4000-8000-000000000999",
        },
    )

    assert preview.status_code == 200
    assert preview.json() == {"allowed": True, "reason": "account_allow"}
    assert created.status_code == 201
    assert created.json()["scope_kind"] == scope_kind
    assert created.json()["scope_id"] == str(scope_id)
    assert created.json()["scope_label"] == expected_label
    assert missing.status_code == 404
    assert missing.json()["code"] == "rbac_resource_not_found"


def test_building_scoped_reader_delegates_only_floors_in_that_building(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    target = permission_accounts["admin"]
    building = INITIAL_BUILDINGS[0]
    own_floor = FLOOR_SEEDS[0]
    other_floor = next(
        floor for floor in FLOOR_SEEDS if floor.building_id != building.id
    )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="allow",
                scope_kind="global",
                scope_id=None,
            ),
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["housing.read"].id,
                effect="allow",
                scope_kind="building",
                scope_id=building.id,
            ),
        ]
    )
    db.commit()
    login = db_client.post(
        "/api/v1/auth/login",
        json={"phone": "19990000004", "password": OTHER_PASSWORD},
    )
    assert login.status_code == 200
    assert "housing.read" not in login.json()["user"]["permissions"]
    assert "housing.read" in login.json()["user"]["scoped_permissions"]
    csrf = login.json()["csrf_token"]
    payload = {
        "permission_id": str(PERMISSIONS["housing.read"].id),
        "effect": "allow",
        "scope_kind": "floor",
    }

    delegated = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={**payload, "scope_id": str(own_floor.id)},
    )
    rejected = db_client.post(
        f"/api/v1/accounts/{target.id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={**payload, "scope_id": str(other_floor.id)},
    )

    assert delegated.status_code == 201
    assert delegated.json()["scope_id"] == str(own_floor.id)
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "permission_delegation_denied"


def test_admin_cannot_remove_own_admin_role_or_deny_permission_management(
    db_client: TestClient,
    permission_accounts,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    removed = db_client.delete(
        f"/api/v1/accounts/{permission_accounts['admin'].id}/roles/{ROLES['admin'].id}",
        headers={"X-CSRF-Token": csrf},
    )
    denied = db_client.post(
        f"/api/v1/accounts/{permission_accounts['admin'].id}/permission-overrides",
        headers={"X-CSRF-Token": csrf},
        json={
            "permission_id": str(PERMISSIONS["permissions.manage"].id),
            "effect": "deny",
            "scope_kind": "global",
            "scope_id": None,
        },
    )

    assert removed.status_code == 409
    assert removed.json()["code"] == "last_admin_protected"
    assert denied.status_code == 409
    assert denied.json()["code"] == "self_permission_lockout"


def test_manager_cannot_remove_own_permission_management_allow(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    override = UserPermissionOverride(
        user_id=manager.id,
        permission_id=PERMISSIONS["permissions.manage"].id,
        effect="allow",
        scope_kind="global",
        scope_id=None,
    )
    db.add(override)
    db.commit()
    csrf = sign_in(db_client, "19990000004", OTHER_PASSWORD)

    response = db_client.delete(
        f"/api/v1/accounts/{manager.id}/permission-overrides/{override.id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "self_permission_lockout"
    assert db.get(UserPermissionOverride, override.id) is not None


def test_account_without_permission_cannot_open_security_settings(
    db_client: TestClient,
    permission_accounts,
) -> None:
    admin_csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    assert (
        db_client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": admin_csrf},
        ).status_code
        == 204
    )
    sign_in(db_client, "19990000004", OTHER_PASSWORD)

    response = db_client.get("/api/v1/roles")
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_permissions_manager_reads_safe_account_directory_without_account_management(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, "19990000004", OTHER_PASSWORD)

    directory = db_client.get("/api/v1/permission-account-directory")
    lifecycle_directory = db_client.get("/api/v1/accounts")

    assert directory.status_code == 200
    assert directory.headers["cache-control"] == "no-store, private"
    assert {item["id"] for item in directory.json()} == {
        str(permission_accounts["admin"].id),
        str(permission_accounts["other"].id),
    }
    assert set(directory.json()[0]) == {"id", "full_name", "status"}
    assert "perm-" not in directory.text
    assert "19990000001" not in directory.text
    assert lifecycle_directory.status_code == 403


def test_permissions_manager_searches_a_minimal_person_scope_directory(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, "19990000004", OTHER_PASSWORD)

    response = db_client.get(
        "/api/v1/permission-person-directory",
        params={"name": "权限管理"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == [
        {
            "id": str(permission_accounts["admin"].person_id),
            "full_name": "权限管理员",
        }
    ]
    assert set(response.json()[0]) == {"id", "full_name"}


def test_last_active_admin_account_cannot_be_disabled(permission_accounts) -> None:
    with pytest.raises(LastActiveAdminProtected) as error:
        permission_accounts["accounts"].disable_account(
            permission_accounts["admin"].id,
            actor_user_id=None,
            request_id="service-test",
        )

    assert error.value.code == "last_admin_protected"


def test_role_change_rolls_back_when_audit_append_fails(
    db_client: TestClient,
    permission_accounts,
    db: Session,
    monkeypatch,
) -> None:
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    account_id = permission_accounts["other"].id
    role_id = ROLES["coach"].id

    def fail_audit(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(rbac_router.AuditService, "append", fail_audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        db_client.post(
            f"/api/v1/accounts/{account_id}/roles",
            headers={"X-CSRF-Token": csrf},
            json={"role_id": str(role_id)},
        )

    db.expire_all()
    assert db.get(UserRole, (account_id, role_id)) is None


def test_permission_override_removal_rolls_back_when_audit_append_fails(
    db_client: TestClient,
    permission_accounts,
    db: Session,
    monkeypatch,
) -> None:
    target = permission_accounts["other"]
    override = UserPermissionOverride(
        user_id=target.id,
        permission_id=PERMISSIONS["people.read"].id,
        effect="deny",
        scope_kind="global",
        scope_id=None,
    )
    db.add(override)
    db.commit()
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    def fail_audit(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(rbac_router.AuditService, "append", fail_audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        db_client.delete(
            f"/api/v1/accounts/{target.id}/permission-overrides/{override.id}",
            headers={"X-CSRF-Token": csrf},
        )

    db.expire_all()
    assert db.get(UserPermissionOverride, override.id) is not None


def test_permission_override_removal_appends_audit_before_delete(
    db_client: TestClient,
    permission_accounts,
    db: Session,
    monkeypatch,
) -> None:
    target = permission_accounts["other"]
    override = UserPermissionOverride(
        user_id=target.id,
        permission_id=PERMISSIONS["people.read"].id,
        effect="deny",
        scope_kind="global",
        scope_id=None,
    )
    db.add(override)
    db.commit()
    csrf = sign_in(db_client, "19990000001", ADMIN_PASSWORD)
    original_append = rbac_router.AuditService.append

    def assert_row_exists_before_audit(service, *args, **kwargs):
        assert db.get(UserPermissionOverride, override.id) is not None
        return original_append(service, *args, **kwargs)

    monkeypatch.setattr(
        rbac_router.AuditService,
        "append",
        assert_row_exists_before_audit,
    )

    response = db_client.delete(
        f"/api/v1/accounts/{target.id}/permission-overrides/{override.id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 204
    assert db.get(UserPermissionOverride, override.id) is None


def test_admin_reads_building_directory_and_floor_directory(
    db_client: TestClient,
    permission_accounts,
) -> None:
    sign_in(db_client, "19990000001", ADMIN_PASSWORD)

    buildings = db_client.get(
        "/api/v1/permission-building-directory",
        params={"query": "4", "page": 1, "page_size": 20},
    )
    floors = db_client.get(
        "/api/v1/permission-floor-directory",
        params={"query": "二楼", "page": 1, "page_size": 20},
    )

    assert buildings.status_code == 200
    assert buildings.headers["cache-control"] == "no-store, private"
    assert buildings.json() == {
        "items": [
            {
                "id": str(INITIAL_BUILDINGS[0].id),
                "code": "4",
                "name": "4幢",
                "is_active": True,
                "label": "4幢",
            }
        ],
        "page": 1,
        "page_size": 20,
        "total": 1,
    }
    assert floors.status_code == 200
    assert floors.headers["cache-control"] == "no-store, private"
    assert floors.json()["page"] == 1
    assert floors.json()["page_size"] == 20
    assert floors.json()["total"] == 4
    assert floors.json()["items"][0] == {
        "id": str(FLOOR_SEEDS[0].id),
        "building_id": str(INITIAL_BUILDINGS[0].id),
        "building_name": "4幢",
        "number": 2,
        "name": "二楼",
        "building_is_active": True,
        "is_active": True,
        "label": "4幢 · 二楼",
    }


def test_person_scoped_manager_cannot_read_building_directory_or_floor_directory(
    db_client: TestClient,
    permission_accounts,
    db: Session,
) -> None:
    manager = permission_accounts["other"]
    target = permission_accounts["admin"]
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="person",
            scope_id=target.person_id,
        )
    )
    db.commit()
    sign_in(db_client, "19990000004", OTHER_PASSWORD)

    responses = [
        db_client.get("/api/v1/permission-building-directory"),
        db_client.get("/api/v1/permission-floor-directory"),
    ]

    assert [response.status_code for response in responses] == [403, 403]
    assert all(
        response.json()["code"] == "permission_denied" for response in responses
    )
