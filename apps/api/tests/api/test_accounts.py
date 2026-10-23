from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.auth.admin_router as account_admin_router
from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.auth.service import LastActiveAdminProtected
from app.identity.models import GuardianRelationship
from app.identity.schemas import StaffCreate
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post("/api/v1/auth/login", json={"phone": phone, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def assert_minor_account_was_disabled(
    db: Session,
    account_id: UUID,
) -> None:
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert account.status == "disabled"
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == account_id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 0


def test_disabling_last_guardian_account_disables_minor(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    csrf = sign_in(db_client, platform_users, "admin")
    guardian_account = platform_users["accounts"]["guardian"]

    response = db_client.post(
        f"/api/v1/accounts/{guardian_account.id}/disable",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "disable-last-guardian"},
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
    assert "account.disable" in actions
    assert actions.count("account.auto_disable.guardian_missing") == 1


def test_admin_creates_lists_and_resets_account(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    person = platform_users["people"]["adult_without_account"]
    created = db_client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "login_phone": "13900139010",
            "email": "new-account@example.com",
        },
    )

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store, private"
    account_id = created.json()["account"]["id"]
    assert created.json()["temporary_password"]
    listed = db_client.get("/api/v1/accounts", params={"page": 1, "status": "active"})
    assert listed.status_code == 200
    assert next(item for item in listed.json()["items"] if item["id"] == account_id)[
        "phone"
    ] == "139****9010"
    assert "temporary_password" not in listed.text

    reset = db_client.post(
        f"/api/v1/accounts/{account_id}/reset-password",
        headers={"X-CSRF-Token": csrf},
    )
    assert reset.status_code == 200
    assert reset.headers["cache-control"] == "no-store, private"
    assert reset.json()["temporary_password"]
    assert db.get(UserAccount, account_id).force_password_change is True  # type: ignore[union-attr]
    for action in ("account.create", "account.password.reset"):
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == action,
                AuditEvent.resource_id == account_id,
            )
        )
        assert event is not None
        assert event.actor_user_id == platform_users["accounts"]["admin"].id
        assert event.resource_type == "account"
        assert event.outcome == "allowed"
        assert event.event_metadata == {}
        assert created.json()["temporary_password"] not in str(event.event_metadata)
        assert reset.json()["temporary_password"] not in str(event.event_metadata)


def test_admin_opens_account_from_existing_person_contacts(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    person = platform_users["identity_service"].create_staff(
        StaffCreate(
            full_name="待开户生活老师",
            identity_number=make_cn_id("19920102", 115),
            sex="male",
            date_of_birth=date(1992, 1, 2),
            phone="13900139115",
            email="life-teacher@example.com",
            staff_type="life_teacher",
        )
    )

    response = db_client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={"person_id": str(person.id)},
    )

    assert response.status_code == 201
    assert response.json()["account"]["person_id"] == str(person.id)
    assert response.json()["account"]["phone"] == "139****9115"
    assert response.json()["temporary_password"]


def test_accounts_manager_searches_ready_candidates_without_people_list(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    manager = platform_users["accounts"]["guardian"]
    candidate = platform_users["identity_service"].create_staff(
        StaffCreate(
            full_name="最小权限待开户教练",
            identity_number=make_cn_id("19920102", 117),
            sex="male",
            date_of_birth=date(1992, 1, 2),
            phone="13900139117",
            email="candidate-coach@example.com",
            staff_type="coach",
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "guardian")

    candidates = db_client.get(
        "/api/v1/account-candidates",
        params={"name": "最小权限待开户"},
    )
    people = db_client.get("/api/v1/people", params={"name": "最小权限待开户"})

    assert candidates.status_code == 200
    assert candidates.headers["cache-control"] == "no-store, private"
    assert candidates.json() == [
        {
            "id": str(candidate.id),
            "full_name": "最小权限待开户教练",
            "person_types": ["staff"],
            "phone": "139****9117",
        }
    ]
    assert "13900139117" not in candidates.text
    assert people.status_code == 403


def test_account_candidates_apply_object_scoped_allow_and_deny(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    manager = platform_users["accounts"]["guardian"]
    candidate = platform_users["identity_service"].create_staff(
        StaffCreate(
            full_name="对象范围待开户教练",
            identity_number=make_cn_id("19920102", 119),
            sex="male",
            date_of_birth=date(1992, 1, 2),
            phone="13900139119",
            email="scoped-candidate@example.com",
            staff_type="coach",
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect="allow",
            scope_kind="person",
            scope_id=candidate.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "guardian")

    allowed = db_client.get(
        "/api/v1/account-candidates",
        params={"name": "对象范围待开户"},
    )

    assert allowed.status_code == 200
    assert [item["id"] for item in allowed.json()] == [str(candidate.id)]

    db_client.cookies.clear()
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect="deny",
            scope_kind="person",
            scope_id=candidate.id,
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "guardian")

    denied = db_client.get(
        "/api/v1/account-candidates",
        params={"name": "对象范围待开户"},
    )

    assert denied.status_code == 200
    assert denied.json() == []


@pytest.mark.parametrize(("global_allow", "family_effect", "expected_status"), [
    (False, "allow", 200),
    (True, "deny", 403),
])
def test_account_lifecycle_honors_family_scopes(
    db_client: TestClient,
    platform_users,
    db: Session,
    global_allow: bool,
    family_effect: str,
    expected_status: int,
) -> None:
    manager = platform_users["accounts"]["customer_service"]
    target_person = platform_users["people"]["self_student"]
    target_account = platform_users["accounts"]["self_student"]
    db.add(
        GuardianRelationship(
            guardian_person_id=manager.person_id,
            student_person_id=target_person.id,
            relationship="guardian",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
    )
    if global_allow:
        db.add(
            UserPermissionOverride(
                user_id=manager.id,
                permission_id=PERMISSIONS["accounts.manage"].id,
                effect="allow",
                scope_kind="global",
                scope_id=None,
            )
        )
    db.add(
        UserPermissionOverride(
            user_id=manager.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect=family_effect,
            scope_kind="family",
            scope_id=None,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")

    response = db_client.post(
        f"/api/v1/accounts/{target_account.id}/reset-password",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == expected_status


def test_disabling_an_admin_requires_another_effective_recovery_admin(
    platform_users,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    other_admin = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=other_admin.id, role_id=ROLES["admin"].id))
    db.add_all(
        [
            UserPermissionOverride(
                user_id=other_admin.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            )
            for permission in ("accounts.manage", "permissions.manage")
        ]
    )
    db.commit()

    with pytest.raises(LastActiveAdminProtected):
        platform_users["account_service"].disable_account(
            admin.id,
            actor_user_id=None,
            request_id="service-test",
        )


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"login_phone": "not-a-phone"}, "login_phone"),
        ({"login_phone": "13900139999"}, "login_phone"),
        ({"email": "different@example.com"}, "email"),
    ],
)
def test_account_contact_errors_identify_the_request_field(
    db_client: TestClient,
    platform_users,
    payload: dict[str, str],
    field: str,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    person = platform_users["identity_service"].create_staff(
        StaffCreate(
            full_name=f"联系方式错误 {field}",
            identity_number=make_cn_id("19920102", 123 if field == "email" else 121),
            sex="male",
            date_of_birth=date(1992, 1, 2),
            phone="13900139121",
            email="contact-source@example.com",
            staff_type="coach",
        )
    )

    response = db_client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={"person_id": str(person.id), **payload},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == field


def test_account_readiness_uses_injected_business_date(
    db_client: TestClient,
    platform_users,
    monkeypatch,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    student = platform_users["people"]["other_student"]
    monkeypatch.setattr(
        account_admin_router,
        "business_today",
        lambda: date(2031, 7, 26),
        raising=False,
    )

    response = db_client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(student.id),
            "login_phone": "13900139011",
            "email": "future-adult@example.com",
        },
    )

    assert response.status_code == 201


def test_create_rejects_pending_minor_and_commits_pii_safe_audit(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    student = platform_users["people"]["other_student"]
    request_id = "pending-minor-create"

    response = db_client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_id},
        json={
            "person_id": str(student.id),
            "login_phone": "13900139012",
            "email": "pending-minor@example.com",
        },
    )

    assert response.status_code == 422
    assert response.json()["errors"] == [{"field": "guardian", "message": "required"}]
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == student.id)) is None
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "account.create.guardian_missing",
            AuditEvent.resource_id == student.id,
        )
    )
    assert event is not None
    assert event.actor_user_id == platform_users["accounts"]["admin"].id
    assert event.resource_type == "person"
    assert event.outcome == "denied"
    assert event.request_id == request_id
    assert event.event_metadata == {"error_code": "minor_guardian_required"}
    assert "其他学员" not in str(event.event_metadata)
    assert "13900139012" not in str(event.event_metadata)
    assert "pending-minor@example.com" not in str(event.event_metadata)


def test_enable_rejects_pending_minor_and_commits_pii_safe_audit(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
    monkeypatch,
) -> None:
    account = platform_users["accounts"]["minor_student"]
    account.status = "disabled"
    minor_login.relationship.valid_until = date(2026, 7, 26)
    db.commit()
    monkeypatch.setattr(account_admin_router, "business_today", lambda: date(2026, 7, 27))
    csrf = sign_in(db_client, platform_users, "admin")
    request_id = "pending-minor-enable"

    response = db_client.post(
        f"/api/v1/accounts/{account.id}/enable",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_id},
    )

    assert response.status_code == 422
    assert response.json()["errors"] == [{"field": "guardian", "message": "required"}]
    db.refresh(account)
    assert account.status == "disabled"
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "account.enable.guardian_missing",
            AuditEvent.resource_id == account.id,
        )
    )
    assert event is not None
    assert event.actor_user_id == platform_users["accounts"]["admin"].id
    assert event.resource_type == "account"
    assert event.outcome == "denied"
    assert event.request_id == request_id
    assert event.event_metadata == {"error_code": "minor_guardian_required"}
    assert minor_login.phone not in str(event.event_metadata)
    assert "minor-student@example.com" not in str(event.event_metadata)
    assert "mother" not in str(event.event_metadata)


def test_disable_revokes_sessions_and_enable_reactivates(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    customer_account = platform_users["accounts"]["customer_service"]
    sign_in(db_client, platform_users, "customer_service")
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == customer_account.id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 1

    db_client.cookies.clear()
    admin_csrf = sign_in(db_client, platform_users, "admin")
    disabled = db_client.post(
        f"/api/v1/accounts/{customer_account.id}/disable",
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert disabled.status_code == 204
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == customer_account.id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 0

    enabled = db_client.post(
        f"/api/v1/accounts/{customer_account.id}/enable",
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert enabled.status_code == 204
    assert db.get(UserAccount, customer_account.id).status == "active"  # type: ignore[union-attr]
    assert {
        event.action
        for event in db.scalars(
            select(AuditEvent).where(AuditEvent.resource_id == customer_account.id)
        )
    } >= {"account.disable", "account.enable"}
    for action in ("account.disable", "account.enable"):
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == action,
                AuditEvent.resource_id == customer_account.id,
            )
        )
        assert event is not None
        assert event.resource_type == "account"
        assert event.outcome == "allowed"
        assert event.event_metadata == {}


def test_last_active_admin_disable_maps_stable_error(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    admin = platform_users["accounts"]["admin"]

    response = db_client.post(
        f"/api/v1/accounts/{admin.id}/disable",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin_protected"


def test_account_change_rolls_back_when_audit_append_fails(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    account = platform_users["accounts"]["customer_service"]

    def fail_audit(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(account_admin_router.AuditService, "append", fail_audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        db_client.post(
            f"/api/v1/accounts/{account.id}/disable",
            headers={"X-CSRF-Token": csrf},
        )

    db.expire_all()
    assert db.get(UserAccount, account.id).status == "active"  # type: ignore[union-attr]
