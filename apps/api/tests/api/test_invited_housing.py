import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select

from app.audit.models import AuditEvent
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from tests.invited_housing_helpers import NOW, binding, code, manager, organization, stay

BASE = "/api/v1/referrals/me"


def login(client, users, key="self_student"):
    phone, password, _ = users["credentials"][key]
    assert (
        client.post(
            "/api/v1/auth/login", json={"identifier": phone, "password": password}
        ).status_code
        == 200
    )


def test_no_session_errors_are_private(db_client):
    for endpoint in ("sources", "housing"):
        response = db_client.get(f"{BASE}/{endpoint}")
        assert response.status_code == 401
        assert response.headers["Cache-Control"] == "no-store, private"


def test_only_relation_rows_paginated_and_payload_has_strict_whitelist(
    db_client, db, platform_users
):
    actor = platform_users["accounts"]["self_student"]
    first, _, _ = binding(db, actor, bound_at=NOW)
    second, _, _ = binding(db, actor, bound_at=NOW - timedelta(days=1))
    outsider, _, _ = binding(db, platform_users["accounts"]["coach"])
    stay(db, actor, first, status="checked_in", start=NOW - timedelta(days=1))
    db.commit()
    login(db_client, platform_users)
    response = db_client.get(f"{BASE}/housing?page_size=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["items"][0]["person_id"] == str(first.id)
    assert set(payload) == {"as_of", "items", "total", "page", "page_size"}
    assert set(payload["items"][0]) == {"person_id", "full_name", "nickname", "source", "housing"}
    assert set(payload["items"][0]["housing"]["stay"]) == {
        "building_name",
        "floor_name",
        "room_name",
        "duration_mode",
        "planned_check_in_at",
        "planned_check_out_at",
        "actual_check_in_at",
        "actual_check_out_at",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "roommates",
        "phone",
        "identity",
        "email",
        "notes",
        "payment",
        "revenue",
        "私密住宿备注",
        outsider.full_name,
    ):
        assert forbidden not in serialized
    assert response.headers["Cache-Control"] == "no-store, private"
    second_page = db_client.get(f"{BASE}/housing?page_size=1&page=2").json()
    assert second_page["items"][0]["person_id"] == str(second.id)
    assert db_client.get(f"{BASE}/housing?status=unarranged").json()["total"] == 1
    audit = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "referrals.housing.read")
    ).all()
    assert audit and all(
        set(row.event_metadata) <= {"source_kind", "organization_id", "result_count"}
        for row in audit
    )


@pytest.mark.parametrize(
    "query",
    [
        "source_kind=organization",
        "organization_id=garbage",
        "source_kind=person&organization_id=00000000-0000-0000-0000-000000000001",
        "page_size=101",
        "status=cancelled",
    ],
)
def test_invalid_filters_are_private(db_client, db, platform_users, query):
    code(db, platform_users["accounts"]["self_student"])
    db.commit()
    login(db_client, platform_users)
    response = db_client.get(f"{BASE}/housing?{query}")
    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store, private"


def test_live_org_revocation_cannot_reuse_sources_or_capability(db_client, db, platform_users):
    actor = platform_users["accounts"]["self_student"]
    creator = platform_users["accounts"]["admin"]
    first, second = organization(db, creator), organization(db, creator)
    manager(db, first, actor)
    mapping = manager(db, second, actor)
    binding(db, creator, second)
    db.commit()
    login(db_client, platform_users)
    assert db_client.get("/api/v1/portal/context").json()["invitations_available"] is True
    assert len(db_client.get(f"{BASE}/sources").json()["sources"]) == 2
    mapping.revoked_by_user_id = creator.id
    mapping.revoked_at = NOW
    db.commit()
    for org_id in (second.id, uuid4()):
        response = db_client.get(
            f"{BASE}/housing?source_kind=organization&organization_id={org_id}"
        )
        assert response.status_code == 404
        assert response.headers["Cache-Control"] == "no-store, private"
    assert db_client.get(f"{BASE}/housing").json()["total"] == 0
    assert db_client.get("/api/v1/revenue-share/me").status_code in (403, 404)


def test_person_deny_before_count_and_pagination_and_no_n_plus_one(db_client, db, platform_users):
    actor = platform_users["accounts"]["self_student"]
    denied, _, _ = binding(db, actor, bound_at=NOW + timedelta(days=1))
    for _ in range(100):
        binding(db, actor)
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["referrals.read_invited_housing"].id,
            scope_kind="person",
            scope_id=denied.id,
            effect="deny",
        )
    )
    db.commit()
    login(db_client, platform_users)
    counts = []

    def count(*_):
        counts.append(1)

    event.listen(db.bind, "before_cursor_execute", count)
    try:
        sizes = []
        for size in (1, 20, 100):
            counts.clear()
            response = db_client.get(f"{BASE}/housing?page_size={size}")
            assert response.status_code == 200
            assert response.json()["total"] == 100
            assert len(response.json()["items"]) == size
            assert all(item["person_id"] != str(denied.id) for item in response.json()["items"])
            sizes.append(len(counts))
        assert sizes[0] == sizes[1] == sizes[2]
    finally:
        event.remove(db.bind, "before_cursor_execute", count)


def test_inconsistent_authorized_person_not_hidden_by_page_or_status(db_client, db, platform_users):
    actor = platform_users["accounts"]["self_student"]
    broken, _, _ = binding(db, actor, bound_at=NOW - timedelta(days=1))
    binding(db, actor)
    stay(
        db,
        actor,
        broken,
        status="checked_in",
        start=NOW - timedelta(days=20),
        end=NOW - timedelta(days=10),
    )
    stay(db, actor, broken, status="checkout_pending", start=NOW - timedelta(days=9))
    db.commit()
    login(db_client, platform_users)
    for query in ("page_size=1", "status=unarranged", "page=100"):
        response = db_client.get(f"{BASE}/housing?{query}")
        assert response.status_code == 409
        assert response.json()["code"] == "invited_housing_inconsistent"
        assert broken.full_name not in response.text


@pytest.mark.parametrize("change", ["role_removed", "deny", "account_disabled", "org_disabled"])
def test_next_request_rechecks_roles_account_org_and_deny(db_client, db, platform_users, change):
    actor = platform_users["accounts"]["self_student"]
    org = organization(db, platform_users["accounts"]["admin"])
    manager(db, org, actor)
    db.commit()
    login(db_client, platform_users)
    assert (
        db_client.get(
            f"{BASE}/housing?source_kind=organization&organization_id={org.id}"
        ).status_code
        == 200
    )
    actor_id, org_id = actor.id, org.id
    if change == "role_removed":
        db.execute(delete(UserRole).where(UserRole.user_id == actor_id))
    elif change == "deny":
        db.add(
            UserPermissionOverride(
                user_id=actor_id,
                permission_id=PERMISSIONS["referrals.read_invited_housing"].id,
                scope_kind="self",
                effect="deny",
            )
        )
    elif change == "account_disabled":
        actor.status = "disabled"
    else:
        org.is_active = False
    db.commit()
    response = db_client.get(f"{BASE}/housing?source_kind=organization&organization_id={org_id}")
    assert (
        response.status_code
        == {"role_removed": 403, "deny": 403, "account_disabled": 401, "org_disabled": 404}[change]
    )
    assert response.headers["Cache-Control"] == "no-store, private"
