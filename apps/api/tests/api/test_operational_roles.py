from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.housing.seed_inventory import INITIAL_ROOMS
from app.personnel_intake.models import CoachPersonResponsibility
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import ADMIN_OPERATIONAL_FINANCE_PERMISSIONS, ROLES
from app.revenue_share.models import ReferralCode


def _login(
    client: TestClient,
    platform_users: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def test_administration_reads_accounts_without_account_mutations(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    account = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=account.id, role_id=ROLES["administration"].id))
    db.commit()

    login = _login(db_client, platform_users, "customer_service")
    permissions = set(login["user"]["permissions"])
    assert {
        "accounts.read",
        "people.list",
        "people.read",
        "housing.read",
        "housing.rooms.manage",
        "housing.stays.manage",
        "housing.transfers.manage",
        "housing.checkouts.manage",
        "housing.issues.manage",
        "housing.reports.export_sensitive",
        "food_preferences.read",
        "food_preferences.export",
        "procurement.read",
        "procurement.manage",
        "procurement.settle",
        "procurement.export",
        "procurement.centers.manage",
    } <= permissions
    assert "accounts.manage" not in permissions
    assert db_client.get("/api/v1/accounts").status_code == 200

    target = platform_users["accounts"]["guardian"]
    denied = db_client.post(
        f"/api/v1/accounts/{target.id}/disable",
        headers={"X-CSRF-Token": login["csrf_token"]},
    )
    assert denied.status_code == 403


def test_coach_has_owner_summary_but_no_global_housing_access(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    coach = platform_users["accounts"]["coach"]
    target = platform_users["people"]["self_student"]
    db.add(
        CoachPersonResponsibility(
            coach_person_id=coach.person_id,
            person_id=target.id,
            created_by_user_id=platform_users["accounts"]["admin"].id,
        )
    )
    db.commit()

    login = _login(db_client, platform_users, "coach")
    permissions = set(login["user"]["permissions"])
    assert "personnel_intake.housing.read" in permissions
    assert "housing.read" not in permissions
    assert not permissions & {
        "housing.rooms.manage",
        "housing.stays.manage",
        "housing.transfers.manage",
        "housing.checkouts.manage",
        "housing.issues.manage",
        "housing.reports.export_sensitive",
    }

    summary = db_client.get("/api/v1/personnel-intake/managed-people")
    assert summary.status_code == 200
    assert summary.json()["total"] == 1
    assert {item["person_id"] for item in summary.json()["items"]} == {str(target.id)}
    assert set(summary.json()["items"][0]) == {
        "person_id",
        "full_name",
        "nickname",
        "kinds",
        "authorized_affiliations",
        "housing",
    }

    room = INITIAL_ROOMS[0]
    for path, status in (
        ("/housing/catalog", 403),
        ("/housing/overview", 403),
        ("/housing/report-batches", 403),
        (f"/housing/rooms/{room.id}", 404),
        (f"/housing/rooms/{room.id}/detail", 404),
    ):
        denied = db_client.get("/api/v1" + path)
        assert denied.status_code == status
        assert denied.headers["cache-control"] == "no-store, private"

    exported = db_client.post(
        "/api/v1/housing/exports",
        headers={"X-CSRF-Token": login["csrf_token"]},
        json={"format": "csv", "purpose": "隔离测试权限验收"},
    )
    assert exported.status_code == 403


def test_coach_sees_only_their_active_invitation_qr_codes(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    coach = platform_users["people"]["coach"]
    admin = platform_users["accounts"]["admin"]
    db.add_all(
        [
            ReferralCode(
                code="COACH-OWN-01",
                label="教练本人邀请",
                source_kind="person",
                referrer_person_id=coach.id,
                referrer_organization_id=None,
                is_active=True,
                created_by_user_id=admin.id,
            ),
            ReferralCode(
                code="COACH-OLD-01",
                label="已停用邀请",
                source_kind="person",
                referrer_person_id=coach.id,
                referrer_organization_id=None,
                is_active=False,
                created_by_user_id=admin.id,
            ),
            ReferralCode(
                code="OTHER-PERSON-01",
                label="其他人员邀请",
                source_kind="person",
                referrer_person_id=platform_users["people"]["customer_service"].id,
                referrer_organization_id=None,
                is_active=True,
                created_by_user_id=admin.id,
            ),
        ]
    )
    db.commit()
    _login(db_client, platform_users, "coach")

    response = db_client.get("/api/v1/revenue-share/me")

    assert response.status_code == 200
    assert response.json()["referral_codes"] == [
        {
            "id": str(
                db.scalar(
                    select(ReferralCode.id).where(ReferralCode.code == "COACH-OWN-01")
                )
            ),
            "code": "COACH-OWN-01",
            "label": "教练本人邀请",
            "source_kind": "person",
            "source_name": coach.full_name,
            "is_active": True,
            "registration_url": "http://127.0.0.1:5173/register?invite=COACH-OWN-01",
        }
    ]


def test_coach_can_generate_one_idempotent_self_invitation_code(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    coach = platform_users["people"]["coach"]
    login = _login(db_client, platform_users, "coach")
    assert "revenue_share.create_self_referral" in login["user"]["scoped_permissions"]

    first = db_client.put(
        "/api/v1/revenue-share/me/referral-code",
        headers={"X-CSRF-Token": login["csrf_token"]},
    )
    second = db_client.put(
        "/api/v1/revenue-share/me/referral-code",
        headers={"X-CSRF-Token": login["csrf_token"]},
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["source_name"] == coach.full_name
    assert first.json()["label"] == f"{coach.full_name}专属邀请"
    assert first.json()["registration_url"].startswith(
        "http://127.0.0.1:5173/register?invite=INV-"
    )
    assert (
        db.scalar(
            select(func.count(ReferralCode.id)).where(
                ReferralCode.referrer_person_id == coach.id,
                ReferralCode.is_active.is_(True),
            )
        )
        == 1
    )


def test_ordinary_admin_can_generate_self_invitation_but_guardian_cannot(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    ordinary_admin = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=ordinary_admin.id, role_id=ROLES["admin"].id))
    db.commit()
    admin_login = _login(db_client, platform_users, "customer_service")

    created = db_client.put(
        "/api/v1/revenue-share/me/referral-code",
        headers={"X-CSRF-Token": admin_login["csrf_token"]},
    )

    assert created.status_code == 200
    assert created.json()["source_name"] == platform_users["people"]["customer_service"].full_name

    guardian_login = _login(db_client, platform_users, "guardian")
    denied = db_client.put(
        "/api/v1/revenue-share/me/referral-code",
        headers={"X-CSRF-Token": guardian_login["csrf_token"]},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"


def test_student_has_no_asset_or_revenue_share_portal_access(
    db_client: TestClient,
    platform_users,
) -> None:
    login = _login(db_client, platform_users, "self_student")
    effective_permissions = {
        *login["user"]["permissions"],
        *login["user"]["scoped_permissions"],
    }

    assert "enrollment_applications.create" in effective_permissions
    assert effective_permissions.isdisjoint(
        {"assets.read_self", "revenue_share.read_self"}
    )
    assert db_client.get("/api/v1/assets/me").status_code == 403
    assert db_client.get("/api/v1/revenue-share/me").status_code == 403


def test_finance_can_settle_and_export_but_cannot_change_rules(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    account = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=account.id, role_id=ROLES["finance"].id))
    db.commit()

    login = _login(db_client, platform_users, "customer_service")
    permissions = set(login["user"]["permissions"])
    assert {
        "payments.read",
        "revenue_share.finance",
        "revenue_share.export",
        "procurement.read",
        "procurement.settle",
        "procurement.export",
    } <= permissions
    assert "revenue_share.manage" not in permissions
    assert "procurement.manage" not in permissions
    assert "procurement.centers.manage" not in permissions

    assert db_client.get("/api/v1/revenue-share/finance").status_code == 200
    assert db_client.get("/api/v1/revenue-share/configuration").status_code == 403
    exported = db_client.post(
        "/api/v1/revenue-share/finance/exports",
        headers={"X-CSRF-Token": login["csrf_token"]},
        json={"purpose": "财务权限验收"},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")


def test_ordinary_admin_cannot_view_procurement_or_finance_consoles(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    ordinary_admin = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=ordinary_admin.id, role_id=ROLES["admin"].id))
    db.commit()

    login = _login(db_client, platform_users, "customer_service")
    permissions = set(login["user"]["permissions"])
    assert permissions.isdisjoint(ADMIN_OPERATIONAL_FINANCE_PERMISSIONS)
    assert "revenue_share.read_self" in permissions
    assert db_client.get("/api/v1/procurement").status_code == 403
    assert db_client.get("/api/v1/revenue-share/finance").status_code == 403
    assert db_client.get("/api/v1/revenue-share/configuration").status_code == 403


def test_mail_settings_are_super_admin_only_by_default(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    ordinary_admin = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=ordinary_admin.id, role_id=ROLES["admin"].id))
    audit_permission = db.scalar(select(Permission).where(Permission.key == "audit.read"))
    assert audit_permission is not None
    # Even a stale/overly broad permission override must not bypass the
    # super-administrator-only boundary enforced by the audit endpoint.
    db.add(
        UserPermissionOverride(
            user_id=ordinary_admin.id,
            permission_id=audit_permission.id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.commit()

    ordinary_login = _login(db_client, platform_users, "customer_service")
    ordinary_permissions = set(ordinary_login["user"]["permissions"])
    assert "accounts.manage" in ordinary_permissions
    assert "mail.settings.manage" not in ordinary_permissions
    assert "permissions.manage" not in ordinary_permissions
    assert "audit.read" in ordinary_permissions
    assert ordinary_login["user"]["is_super_admin"] is False
    assert db_client.get("/api/v1/mail/settings").status_code == 403
    assert db_client.get("/api/v1/audit-events").status_code == 403

    db_client.cookies.clear()
    super_login = _login(db_client, platform_users, "admin")
    super_permissions = set(super_login["user"]["permissions"])
    assert {
        "mail.settings.manage",
        "permissions.manage",
        "audit.read",
    } <= super_permissions
    assert super_login["user"]["is_super_admin"] is True
    assert db_client.get("/api/v1/mail/settings").status_code == 200
