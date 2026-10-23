import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.identity.models import Person
from app.rbac.models import Permission, UserPermissionOverride


def login(client: TestClient, users, key: str) -> str:
    phone, password, _ = users["credentials"][key]
    response = client.post("/api/v1/auth/login", json={"identifier": phone, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_admin_edits_other_nickname_and_private_notes_with_actual_actor(db_client, platform_users, db: Session) -> None:
    target = platform_users["people"]["self_student"]
    csrf = login(db_client, platform_users, "admin")
    response = db_client.patch(f"/api/v1/people/{target.id}", headers={"X-CSRF-Token": csrf}, json={"nickname": "新昵称", "profile_notes": "内部跟进备注"})
    assert response.status_code == 200
    assert response.json()["nickname"] == "新昵称"
    assert "profile_notes" not in response.json()
    private = db_client.get(f"/api/v1/people/{target.id}/staff-profile")
    assert private.status_code == 200
    assert private.headers["cache-control"] == "no-store, private"
    assert private.json() == {"person_id": str(target.id), "nickname": "新昵称", "profile_notes": "内部跟进备注"}
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "person.profile.update"))
    assert event is not None
    assert event.actor_user_id == platform_users["accounts"]["admin"].id
    assert event.resource_id == target.id
    assert event.event_metadata == {"person_id": str(target.id), "changed_fields": ["nickname", "profile_notes"], "old_nickname": None, "new_nickname": "新昵称"}
    assert "内部跟进备注" not in str(event.event_metadata)


def test_staff_patch_omission_preserves_values_and_null_clears(db_client, platform_users, db: Session) -> None:
    target_id = platform_users["people"]["self_student"].id
    csrf = login(db_client, platform_users, "admin")
    headers = {"X-CSRF-Token": csrf}
    assert db_client.patch(f"/api/v1/people/{target_id}", headers=headers, json={"nickname": "原昵称", "profile_notes": "原备注"}).status_code == 200
    assert db_client.patch(f"/api/v1/people/{target_id}", headers=headers, json={}).status_code == 200
    assert db_client.get(f"/api/v1/people/{target_id}/staff-profile").json()["profile_notes"] == "原备注"
    assert db_client.patch(f"/api/v1/people/{target_id}", headers=headers, json={"nickname": None, "profile_notes": None}).status_code == 200
    db.expire_all()
    target = db.get(Person, target_id)
    assert target.nickname is None and target.profile_notes is None
    assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == "person.profile.update")))) == 2


@pytest.mark.parametrize("key", ["self_student", "guardian", "coach", "customer_service"])
def test_non_editors_cannot_read_or_write_private_profile(db_client, platform_users, key) -> None:
    target = platform_users["people"]["self_student" if key == "self_student" else "own_student"]
    csrf = login(db_client, platform_users, key)
    assert db_client.get(f"/api/v1/people/{target.id}/staff-profile").status_code == 403
    response = db_client.patch(f"/api/v1/people/{target.id}", headers={"X-CSRF-Token": csrf}, json={"nickname": "不应修改", "profile_notes": "不应修改"})
    assert response.status_code == 403


def test_person_scoped_override_cannot_grant_private_profile_access(db_client, platform_users, db: Session) -> None:
    target = platform_users["people"]["own_student"]
    permission = db.scalar(select(Permission).where(Permission.key == "people.update"))
    db.add(UserPermissionOverride(user_id=platform_users["accounts"]["coach"].id, permission_id=permission.id, effect="allow", scope_kind="person", scope_id=target.id))
    db.commit()
    csrf = login(db_client, platform_users, "coach")
    assert db_client.get(f"/api/v1/people/{target.id}/staff-profile").status_code == 403
    assert db_client.patch(f"/api/v1/people/{target.id}", headers={"X-CSRF-Token": csrf}, json={"nickname": "不应修改"}).status_code == 403


def test_target_deny_overrides_global_private_profile_access(db_client, platform_users, db: Session) -> None:
    target = platform_users["people"]["own_student"]
    permission = db.scalar(select(Permission).where(Permission.key == "people.update"))
    db.add(UserPermissionOverride(user_id=platform_users["accounts"]["admin"].id, permission_id=permission.id, effect="deny", scope_kind="person", scope_id=target.id))
    db.commit()
    csrf = login(db_client, platform_users, "admin")
    assert db_client.get(f"/api/v1/people/{target.id}/staff-profile").status_code == 403
    assert db_client.patch(f"/api/v1/people/{target.id}", headers={"X-CSRF-Token": csrf}, json={"profile_notes": "不应修改"}).status_code == 403


def test_private_notes_never_enter_shared_or_self_responses(db_client, platform_users, db: Session) -> None:
    target = db.get(Person, platform_users["people"]["self_student"].id)
    target.profile_notes = "只供后台的唯一备注"
    db.commit()
    login(db_client, platform_users, "admin")
    for path in ("/people", f"/people/{target.id}", "/evaluations/admin/students"):
        response = db_client.get(f"/api/v1{path}")
        assert response.status_code == 200
        assert "profile_notes" not in response.text and "只供后台的唯一备注" not in response.text
    csrf = login(db_client, platform_users, "self_student")
    for path in ("/people/me/profile", f"/people/{target.id}"):
        response = db_client.get(f"/api/v1{path}")
        assert response.status_code == 200
        assert "profile_notes" not in response.text and "只供后台的唯一备注" not in response.text
    response = db_client.patch("/api/v1/people/me/profile", headers={"X-CSRF-Token": csrf}, json={"nickname": "本人新昵称"})
    assert response.status_code == 200 and response.json()["nickname"] == "本人新昵称"
    assert "profile_notes" not in response.text
