from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from tests.api.test_assets import sign_in


def test_warranty_roundtrip_switch_and_private_access(
    db_client: TestClient, platform_users, db: Session
):
    headers = {"X-CSRF-Token": sign_in(db_client, platform_users, "admin")}
    offline = {
        "warranty_channel": "offline",
        "warranty_service_location": "本地维修中心",
        "warranty_contact_name": "售后张先生",
        "warranty_contact_phone": "400-123-4567",
    }
    created = db_client.post(
        "/api/v1/assets",
        headers=headers,
        json={
            "name": "售后测试设备",
            "warranty_expires_on": "2028-09-10",
            **offline,
        },
    )
    assert created.status_code == 201
    asset = created.json()
    assert all(asset[key] == value for key, value in offline.items())
    path = f"/api/v1/assets/{asset['id']}"
    updated = db_client.patch(path, headers=headers, json={"note": "保留售后资料"})
    assert updated.status_code == 200
    assert updated.json()["warranty_contact_phone"] == offline["warranty_contact_phone"]
    assert db_client.get(path).json()["warranty_service_location"] == "本地维修中心"
    scan = db_client.get(f"/api/v1/assets/scan/{asset['asset_code']}").json()
    assert not any(key.startswith("warranty_") and key != "warranty_expires_on" for key in scan)

    online = {
        "warranty_channel": "online",
        "warranty_product_url": "https://shop.example.com/item/1",
        "warranty_order_number": "000012345678901234567890",
    }
    changed = db_client.patch(path, headers=headers, json=online)
    assert changed.status_code == 200
    assert all(changed.json()[key] == value for key, value in online.items())
    for key in ("warranty_contact_phone", "warranty_contact_name", "warranty_service_location"):
        assert changed.json()[key] is None
    exported = db_client.post(
        "/api/v1/assets/exports",
        headers=headers,
        json={
            "format": "csv",
            "purpose": "核对资产售后信息",
        },
    )
    assert exported.status_code == 200
    assert online["warranty_order_number"] in exported.content.decode("utf-8-sig")

    person = platform_users["people"]["customer_service"]
    account = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=account.id, role_id=ROLES["admin"].id))
    db.commit()
    assert (
        db_client.post(
            path + "/assign",
            headers=headers,
            json={
                "person_id": str(person.id),
                "assigned_at": "2026-09-10T00:00:00+08:00",
            },
        ).status_code
        == 200
    )
    own_headers = {"X-CSRF-Token": sign_in(db_client, platform_users, "customer_service")}
    assert (
        db_client.get("/api/v1/assets/me").json()[0]["warranty_order_number"]
        == online["warranty_order_number"]
    )
    assert (
        db_client.get(f"/api/v1/assets/person/{person.id}").json()[0]["warranty_channel"]
        == "online"
    )
    assert (
        db_client.patch(path, headers=own_headers, json={"warranty_channel": None}).status_code
        == 403
    )

    headers = {"X-CSRF-Token": sign_in(db_client, platform_users, "admin")}
    cleared = db_client.patch(path, headers=headers, json={"warranty_channel": None})
    assert cleared.status_code == 200
    assert cleared.json()["warranty_product_url"] is None
    assert cleared.json()["warranty_order_number"] is None


def test_rejects_unsafe_warranty_links(db_client: TestClient, platform_users):
    headers = {"X-CSRF-Token": sign_in(db_client, platform_users, "admin")}
    for url in ("javascript:alert(1)", "https://user:secret@example.com/item", "ftp://example.com"):
        response = db_client.post(
            "/api/v1/assets",
            headers=headers,
            json={
                "name": "链接校验",
                "warranty_channel": "online",
                "warranty_product_url": url,
            },
        )
        assert response.status_code == 422
