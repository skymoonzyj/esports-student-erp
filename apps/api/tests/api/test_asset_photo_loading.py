from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event
from sqlalchemy.orm import Session


def test_asset_lists_export_and_personal_assets_load_photo_metadata_in_one_query(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    phone, password, _ = platform_users["credentials"]["admin"]
    login = db_client.post(
        "/api/v1/auth/login", json={"identifier": phone, "password": password}
    )
    assert login.status_code == 200
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    actor_id = str(platform_users["people"]["admin"].id)
    photo = BytesIO()
    Image.new("RGB", (3, 2), color=(20, 40, 80)).save(photo, format="PNG")
    photo_bytes = photo.getvalue()
    asset_ids = []
    for index in range(3):
        created = db_client.post(
            "/api/v1/assets", headers=headers, json={"name": f"照片元信息测试{index}"}
        )
        assert created.status_code == 201
        asset_id = created.json()["id"]
        asset_ids.append(asset_id)
        uploaded = db_client.put(
            f"/api/v1/assets/{asset_id}/photo",
            headers=headers,
            files={"file": ("identification.png", photo_bytes, "image/png")},
        )
        assert uploaded.status_code == 200
        assigned = db_client.post(
            f"/api/v1/assets/{asset_id}/assign",
            headers=headers,
            json={"person_id": actor_id, "assigned_at": "2026-08-20T09:00:00+08:00"},
        )
        assert assigned.status_code == 200

    statements: list[str] = []

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "from asset_photos" in statement.lower():
            statements.append(statement.lower())

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", capture_sql)
    try:
        for path in ("/api/v1/assets", "/api/v1/assets/me", f"/api/v1/assets/person/{actor_id}"):
            db.expire_all()
            statements.clear()
            response = db_client.get(path)
            assert response.status_code == 200
            body = response.json()
            items = body["items"] if isinstance(body, dict) else body
            own_items = [item for item in items if item["id"] in asset_ids]
            assert len(own_items) == 3
            assert all(item["photo"]["size_bytes"] == len(photo_bytes) for item in own_items)
            assert len(statements) == 1
            assert "asset_photos.content" not in statements[0]

        db.expire_all()
        statements.clear()
        exported = db_client.post(
            "/api/v1/assets/exports",
            headers=headers,
            json={"format": "csv", "purpose": "照片元信息导出验证"},
        )
        assert exported.status_code == 200
        assert exported.content.decode("utf-8-sig").count("已上传") == 3
        assert len(statements) == 1
        assert "asset_photos.content" not in statements[0]

        db.expire_all()
        statements.clear()
        downloaded = db_client.get(f"/api/v1/assets/{asset_ids[0]}/photo")
        assert downloaded.status_code == 200
        assert downloaded.content == photo_bytes
        assert "private" in downloaded.headers["cache-control"]
        assert len(statements) == 1
        assert "asset_photos.content" in statements[0]
    finally:
        event.remove(connection, "before_cursor_execute", capture_sql)
