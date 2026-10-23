from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.rbac.models import UserRole
from app.rbac.seed import ROLES


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_super_admin_can_create_assign_return_and_scan_asset_without_student_portal_access(
    db_client: TestClient,
    platform_users,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    student = platform_users["people"]["self_student"]

    created = db_client.post(
        "/api/v1/assets",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "name": "训练笔记本",
            "category": "训练设备",
            "brand": "示例品牌",
            "model": "X-1",
            "serial_number": "SERIAL-PRIVATE-001",
            "location": "一号楼训练室",
            "purchase_date": "2026-08-18",
            "warranty_expires_on": "2027-08-18",
        },
    )
    assert created.status_code == 201
    asset = created.json()
    assert asset["asset_code"].startswith("AST-")
    assert asset["status"] == "available"
    assert asset["purchase_date"] == "2026-08-18"
    assert asset["warranty_expires_on"] == "2027-08-18"
    assert asset["source_purchase_id"] is None

    scanned = db_client.get(f"/api/v1/assets/scan/{asset['asset_code']}")
    assert scanned.status_code == 200
    assert scanned.json()["name"] == "训练笔记本"
    assert scanned.json()["purchase_date"] == "2026-08-18"
    assert scanned.json()["warranty_expires_on"] == "2027-08-18"
    assert "serial_number" not in scanned.json()
    assert "person_name" not in scanned.json()

    assigned = db_client.post(
        f"/api/v1/assets/{asset['id']}/assign",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "person_id": str(student.id),
            "assigned_at": "2026-08-20T09:00:00+08:00",
            "expected_return_at": "2026-08-27T18:00:00+08:00",
            "condition_on_assign": "外观完好",
        },
    )
    assert assigned.status_code == 200
    assert assigned.json()["status"] == "assigned"
    assert assigned.json()["current_assignment"]["person_id"] == str(student.id)

    sign_in(db_client, platform_users, "self_student")
    assert db_client.get("/api/v1/assets/me").status_code == 403
    assert db_client.get(f"/api/v1/assets/{asset['id']}").status_code == 403

    admin_csrf = sign_in(db_client, platform_users, "admin")
    returned = db_client.post(
        f"/api/v1/assets/{asset['id']}/return",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "returned_at": "2026-08-25T18:00:00+08:00",
            "condition_on_return": "已归还",
        },
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "available"
    history = db_client.get(f"/api/v1/assets/{asset['id']}/history")
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["returned_at"] is not None


def test_asset_warranty_expiry_cannot_precede_purchase_date(
    db_client: TestClient,
    platform_users,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/assets",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "name": "保修日期测试资产",
            "purchase_date": "2026-08-20",
            "warranty_expires_on": "2026-08-19",
        },
    )
    assert created.status_code == 422


def test_asset_permissions_cover_self_and_guardian_family(
    db_client: TestClient,
    platform_users,
) -> None:
    admin_csrf = sign_in(db_client, platform_users, "admin")
    student = platform_users["people"]["own_student"]
    created = db_client.post(
        "/api/v1/assets",
        headers={"X-CSRF-Token": admin_csrf},
        json={"name": "家庭范围可见设备"},
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]

    assigned = db_client.post(
        f"/api/v1/assets/{asset_id}/assign",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "person_id": str(student.id),
            "assigned_at": "2026-08-20T09:00:00+08:00",
        },
    )
    assert assigned.status_code == 200

    sign_in(db_client, platform_users, "guardian")
    family_assets = db_client.get(f"/api/v1/assets/person/{student.id}")
    assert family_assets.status_code == 200
    assert family_assets.json()[0]["id"] == asset_id

    other_student = platform_users["people"]["other_student"]
    assert db_client.get(f"/api/v1/assets/person/{other_student.id}").status_code == 403


def test_non_manager_cannot_read_asset_ledger(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "customer_service")
    response = db_client.get("/api/v1/assets")
    assert response.status_code == 403


def test_ordinary_admin_can_read_personal_assets_but_cannot_operate_asset_ledger(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    super_admin_csrf = sign_in(db_client, platform_users, "admin")
    ordinary_admin_account = platform_users["accounts"]["customer_service"]
    ordinary_admin_person = platform_users["people"]["customer_service"]

    created = db_client.post(
        "/api/v1/assets",
        headers={"X-CSRF-Token": super_admin_csrf},
        json={"name": "普通管理员个人资产"},
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]
    assigned = db_client.post(
        f"/api/v1/assets/{asset_id}/assign",
        headers={"X-CSRF-Token": super_admin_csrf},
        json={
            "person_id": str(ordinary_admin_person.id),
            "assigned_at": "2026-08-20T09:00:00+08:00",
        },
    )
    assert assigned.status_code == 200

    db.add(UserRole(user_id=ordinary_admin_account.id, role_id=ROLES["admin"].id))
    db.commit()
    ordinary_admin_csrf = sign_in(db_client, platform_users, "customer_service")

    personal_assets = db_client.get("/api/v1/assets/me")
    assert personal_assets.status_code == 200
    assert [asset["id"] for asset in personal_assets.json()] == [asset_id]

    assert db_client.get("/api/v1/assets").status_code == 403
    assert db_client.get(f"/api/v1/assets/{asset_id}").status_code == 403
    assert db_client.get(f"/api/v1/assets/{asset_id}/history").status_code == 403
    assert (
        db_client.post(
            "/api/v1/assets",
            headers={"X-CSRF-Token": ordinary_admin_csrf},
            json={"name": "不应创建的资产"},
        ).status_code
        == 403
    )
    assert (
        db_client.patch(
            f"/api/v1/assets/{asset_id}",
            headers={"X-CSRF-Token": ordinary_admin_csrf},
            json={"name": "不应修改的资产"},
        ).status_code
        == 403
    )
    assert (
        db_client.post(
            f"/api/v1/assets/{asset_id}/return",
            headers={"X-CSRF-Token": ordinary_admin_csrf},
            json={"returned_at": "2026-08-21T09:00:00+08:00"},
        ).status_code
        == 403
    )
    assert (
        db_client.post(
            "/api/v1/assets/exports",
            headers={"X-CSRF-Token": ordinary_admin_csrf},
            json={"format": "xlsx", "purpose": "权限边界验证"},
        ).status_code
        == 403
    )
