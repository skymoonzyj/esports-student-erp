from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import delete, event, select

from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.models import AuthSession, UserAccount
from app.housing.models import HousingRoom, HousingRoomPhoto
from app.housing.room_photo_service import HousingRoomPhotoService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from tests.api.test_housing_occupancy import _sign_in
from tests.db.test_housing_issue_service import _room


def _png(color="red"):
    output = BytesIO()
    Image.new("RGB", (30, 15), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def photo_room(db):
    room = _room(db)
    db.commit()
    return room


def _upload(client, room_id, csrf, version, *, slot=1, content=None):
    return client.put(
        f"/api/v1/housing/rooms/{room_id}/photos/{slot}",
        headers={"x-csrf-token": csrf},
        data={"version": str(version)},
        files={"file": ("private-room.png", _png() if content is None else content, "image/png")},
    )


def _photos(db, room_id):
    db.expire_all()
    return list(db.scalars(select(HousingRoomPhoto).where(HousingRoomPhoto.room_id == room_id)))


def test_three_slots_have_no_fourth_slot(db_client, db, platform_users, photo_room):
    csrf = _sign_in(db_client, platform_users, "admin")
    empty = db_client.get(f"/api/v1/housing/rooms/{photo_room.id}/detail")
    assert empty.status_code == 200
    assert empty.json()["photos"] == []
    version = empty.json()["room"]["version"]
    for slot in (1, 2, 3):
        response = _upload(db_client, photo_room.id, csrf, version, slot=slot)
        assert response.status_code == 200, response.text
        assert response.json()["room"]["version"] == version + 1
        assert len(response.json()["photos"]) == slot
        card = db_client.get(f"/api/v1/housing/rooms/{photo_room.id}")
        assert card.status_code == 200
        assert card.json()["photo_count"] == slot
        version += 1
    assert sorted(photo.slot for photo in _photos(db, photo_room.id)) == [1, 2, 3]
    assert _upload(db_client, photo_room.id, csrf, version, slot=4).status_code == 422


def test_invalid_replacement_preserves_original_photo(db_client, db, platform_users, photo_room):
    csrf = _sign_in(db_client, platform_users, "admin")
    response = _upload(db_client, photo_room.id, csrf, photo_room.version)
    assert response.status_code == 200, response.text
    original = _photos(db, photo_room.id)[0]
    original_sha256 = original.sha256
    original_room_version = response.json()["room"]["version"]
    invalid_response = _upload(
        db_client, photo_room.id, csrf, original_room_version, content=b"not a PNG"
    )
    assert invalid_response.status_code == 422
    assert _photos(db, photo_room.id)[0].sha256 == original_sha256
    assert db.get(HousingRoom, photo_room.id).version == original_room_version


def test_audit_failure_rolls_back_photo_and_room_version(
    db_client, db, platform_users, photo_room, monkeypatch
):
    import app.housing.occupancy_router as occupancy

    csrf = _sign_in(db_client, platform_users, "admin")
    original_audit = occupancy._audit

    def reject_success(*args, **kwargs):
        if kwargs["action"] == "housing.room_photo.put" and kwargs["outcome"] == "allowed":
            raise AuditMetadataRejected("forced audit failure")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(occupancy, "_audit", reject_success)
    response = _upload(db_client, photo_room.id, csrf, 1)
    assert response.status_code == 500, response.text
    assert _photos(db, photo_room.id) == []
    assert db.get(HousingRoom, photo_room.id).version == 1


def test_delete_recreate_rejects_old_room_version(db_client, db, platform_users, photo_room):
    csrf = _sign_in(db_client, platform_users, "admin")
    uploaded = _upload(db_client, photo_room.id, csrf, 1)
    assert uploaded.status_code == 200, uploaded.text
    old_id = uploaded.json()["photos"][0]["id"]
    deleted = db_client.request(
        "DELETE",
        f"/api/v1/housing/rooms/{photo_room.id}/photos/{old_id}",
        json={"version": 2},
        headers={"x-csrf-token": csrf},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["photos"] == []
    rebuilt = _upload(db_client, photo_room.id, csrf, 3)
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["photos"][0]["id"] != old_id
    assert _upload(db_client, photo_room.id, csrf, 1).status_code == 409
    stale_delete = db_client.request(
        "DELETE",
        f"/api/v1/housing/rooms/{photo_room.id}/photos/{old_id}",
        json={"version": 2},
        headers={"x-csrf-token": csrf},
    )
    assert stale_delete.status_code == 409
    assert len(_photos(db, photo_room.id)) == 1
    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "housing.room_photo.delete",
                AuditEvent.resource_id == UUID(old_id),
                AuditEvent.outcome == "allowed",
            )
        )
    )
    assert len(events) == 1
    assert events[0].event_metadata["photo_id"] == old_id
    assert not {"content", "thumbnail_content"}.intersection(events[0].event_metadata)


@pytest.mark.parametrize("variant,size", [("original", (30, 15)), ("thumbnail", (30, 15))])
def test_read_returns_private_decodable_image(db_client, platform_users, photo_room, variant, size):
    csrf = _sign_in(db_client, platform_users, "admin")
    uploaded = _upload(db_client, photo_room.id, csrf, 1)
    assert uploaded.status_code == 200, uploaded.text
    metadata = uploaded.json()["photos"][0]
    assert {"content", "thumbnail_content", "sha256"}.isdisjoint(metadata)
    response = db_client.get(
        f"/api/v1/housing/rooms/{photo_room.id}/photos/{metadata['id']}",
        params={"variant": variant},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    with Image.open(BytesIO(response.content)) as decoded:
        decoded.load()
        assert decoded.size == size


def test_missing_csrf_cannot_upload_or_delete(db_client, db, platform_users, photo_room):
    _sign_in(db_client, platform_users, "admin")
    assert _upload(db_client, photo_room.id, "", 1).status_code == 403
    assert (
        db_client.request(
            "DELETE",
            f"/api/v1/housing/rooms/{photo_room.id}/photos/{uuid4()}",
            json={"version": 1},
        ).status_code
        == 403
    )
    assert _photos(db, photo_room.id) == []


def test_photo_route_paths_validate_slot_and_uuid(db_client, platform_users, photo_room):
    csrf = _sign_in(db_client, platform_users, "admin")
    for slot in (0, 4, "not-a-slot"):
        assert _upload(db_client, photo_room.id, csrf, 1, slot=slot).status_code == 422
    assert (
        db_client.get(f"/api/v1/housing/rooms/{photo_room.id}/photos/not-uuid").status_code == 422
    )
    assert (
        db_client.get(
            f"/api/v1/housing/rooms/{photo_room.id}/photos/{uuid4()}?variant=unknown"
        ).status_code
        == 422
    )


@pytest.mark.parametrize("key", ["coach", "self_student", "guardian"])
def test_summary_visibility_does_not_grant_photo_reads(
    db_client, db, platform_users, photo_room, key
):
    csrf = _sign_in(db_client, platform_users, "admin")
    uploaded = _upload(db_client, photo_room.id, csrf, 1)
    assert uploaded.status_code == 200, uploaded.text
    photo_id = uploaded.json()["photos"][0]["id"]
    _sign_in(db_client, platform_users, key)
    response = db_client.get(f"/api/v1/housing/rooms/{photo_room.id}/photos/{photo_id}")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, private"
    assert _photos(db, photo_room.id)[0].id == UUID(photo_id)


def test_scoped_read_and_manage_deny_cannot_be_bypassed(db_client, db, platform_users, photo_room):
    csrf = _sign_in(db_client, platform_users, "admin")
    uploaded = _upload(db_client, photo_room.id, csrf, 1)
    assert uploaded.status_code == 200, uploaded.text
    photo_id = uploaded.json()["photos"][0]["id"]
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="deny",
            scope_kind="building",
            scope_id=photo_room.building_id,
        )
    )
    db.commit()
    response = db_client.get(f"/api/v1/housing/rooms/{photo_room.id}/photos/{photo_id}")
    assert response.status_code == 404
    assert _upload(db_client, photo_room.id, csrf, 2).status_code == 404


def test_full_detail_read_scope_cannot_cross_buildings(db_client, db, platform_users, photo_room):
    csrf = _sign_in(db_client, platform_users, "admin")
    other = _room(db)
    db.commit()
    one = _upload(db_client, photo_room.id, csrf, 1)
    two = _upload(db_client, other.id, csrf, 1)
    assert one.status_code == two.status_code == 200
    actor_id = platform_users["accounts"]["self_student"].id
    db.add(
        UserPermissionOverride(
            user_id=actor_id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="allow",
            scope_kind="building",
            scope_id=photo_room.building_id,
        )
    )
    db.commit()
    _sign_in(db_client, platform_users, "self_student")
    allowed = db_client.get(
        f"/api/v1/housing/rooms/{photo_room.id}/photos/{one.json()['photos'][0]['id']}"
    )
    assert allowed.status_code == 200
    denied = db_client.get(
        f"/api/v1/housing/rooms/{other.id}/photos/{two.json()['photos'][0]['id']}"
    )
    assert denied.status_code == 404


def test_permission_override_without_actual_admin_role_cannot_write(
    db_client, db, platform_users, photo_room
):
    actor_id = platform_users["accounts"]["customer_service"].id
    for permission in ("housing.read", "housing.rooms.manage"):
        db.add(
            UserPermissionOverride(
                user_id=actor_id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="global",
            )
        )
    db.commit()
    csrf = _sign_in(db_client, platform_users, "customer_service")
    denied = _upload(db_client, photo_room.id, csrf, 1)
    assert denied.status_code == 403, denied.text
    assert _photos(db, photo_room.id) == []


@pytest.mark.parametrize("revoke", ["role", "session", "account", "manage_deny"])
def test_authority_is_rechecked_after_room_lock(
    db_client, db, platform_users, photo_room, monkeypatch, revoke
):
    csrf = _sign_in(db_client, platform_users, "admin")
    actor_id = platform_users["accounts"]["admin"].id
    original_lock = HousingRoomPhotoService._locked_room

    def lock_then_revoke(self, room_id):
        room = original_lock(self, room_id)
        if revoke == "role":
            self.db.execute(delete(UserRole).where(UserRole.user_id == actor_id))
        elif revoke == "session":
            for session in self.db.scalars(
                select(AuthSession).where(AuthSession.user_id == actor_id)
            ):
                session.revoked_at = datetime.now(UTC)
        elif revoke == "account":
            self.db.get(UserAccount, actor_id).status = "disabled"
        else:
            self.db.add(
                UserPermissionOverride(
                    user_id=actor_id,
                    permission_id=PERMISSIONS["housing.rooms.manage"].id,
                    effect="deny",
                    scope_kind="building",
                    scope_id=room.building_id,
                )
            )
        self.db.flush()
        return room

    monkeypatch.setattr(HousingRoomPhotoService, "_locked_room", lock_then_revoke)
    denied = _upload(db_client, photo_room.id, csrf, 1)
    assert denied.status_code in {403, 404}, denied.text
    assert _photos(db, photo_room.id) == []


def test_list_and_detail_never_load_photo_blobs(db_client, db, platform_users, photo_room):
    csrf = _sign_in(db_client, platform_users, "admin")
    assert _upload(db_client, photo_room.id, csrf, 1).status_code == 200
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if "housing_room_photos" in statement.lower():
            statements.append(statement.lower())

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", capture)
    try:
        for path in (
            f"/api/v1/housing/overview?building_id={photo_room.building_id}",
            f"/api/v1/housing/rooms/{photo_room.id}/detail",
        ):
            db.expire_all()
            statements.clear()
            response = db_client.get(path)
            assert response.status_code == 200, response.text
            assert statements
            assert all("housing_room_photos.content" not in sql for sql in statements)
            assert all("housing_room_photos.thumbnail_content" not in sql for sql in statements)
            if path.endswith("/detail"):
                assert len(response.json()["photos"]) == 1
                assert len([sql for sql in statements if "mime_type" in sql]) == 1
    finally:
        event.remove(connection, "before_cursor_execute", capture)
