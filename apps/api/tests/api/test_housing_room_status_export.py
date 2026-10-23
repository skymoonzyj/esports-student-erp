from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import StringIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.dependencies import require_standard_account
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.housing import room_status_export_router as export_router
from app.housing.dependencies import get_housing_cipher, get_housing_lookup
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom
from app.identity.models import Person
from app.main import create_app
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.security.encryption import LookupDigest
from tests.db.test_housing_reporting_concurrency import concurrent_context

snapshot_context = concurrent_context


@pytest.fixture
def snapshot_client(snapshot_context, test_settings) -> Iterator[tuple]:
    context = snapshot_context
    with Session(context.engine) as db:
        account = db.get(UserAccount, context.actor_id)
        person = db.get(Person, account.person_id)
        session = AuthSession(
            id=uuid4(),
            user_id=account.id,
            token_digest=uuid4().hex,
            csrf_digest=uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add(session)
        db.commit()
        auth = AuthContext(session=session, account=account, person=person)
        # Materialize immutable identities before closing this fixture connection.
        _ = (auth.session.id, auth.account.id, auth.person.id)
    app = create_app(test_settings)

    def fresh_db():
        with Session(context.engine) as db:
            yield db

    app.dependency_overrides[get_db] = fresh_db
    app.dependency_overrides[require_standard_account] = lambda: auth
    app.dependency_overrides[get_housing_cipher] = lambda: context.cipher
    app.dependency_overrides[get_housing_lookup] = lambda: LookupDigest(b"c" * 32)
    with TestClient(app) as client:
        yield client, context, auth


def _export(client, context, **filters):
    return client.post(
        "/api/v1/housing/room-status/exports",
        json={
            "purpose": "虚拟房态验证",
            "format": "csv",
            "building_ids": [str(context.building_id)],
            **filters,
        },
    )


def test_current_snapshot_download_is_private_and_committed_audit(snapshot_client):
    client, context, _ = snapshot_client
    response = _export(client, context)
    assert response.status_code == 200, response.text
    data = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert len(data) == 1
    assert data[0]["当前入住人数"] == "1"
    assert data[0]["计划离开日期"] == ""
    assert response.headers["cache-control"] == "no-store, private"
    assert "housing-room-status-" in response.headers["content-disposition"]
    with Session(context.engine) as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.actor_user_id == context.actor_id,
                AuditEvent.action == "housing.room_status.export",
            )
        )
        assert audit is not None and audit.outcome == "allowed"
        assert audit.purpose == "虚拟房态验证"


def test_no_matches_and_explicit_unknown_scope_are_private(snapshot_client):
    client, context, _ = snapshot_client
    empty = _export(client, context, query="不可能匹配虚拟房间")
    assert empty.status_code == 422
    assert empty.json()["code"] == "housing_export_empty"
    unknown = _export(client, context, floor_ids=[str(uuid4())])
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "housing_resource_not_found"


def test_floor_scoped_parent_building_does_not_leak_denied_sibling(snapshot_client):
    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        db.execute(delete(UserRole).where(UserRole.user_id == context.actor_id))
        for key in ("housing.read", "housing.reports.export_sensitive"):
            db.add(
                UserPermissionOverride(
                    user_id=context.actor_id,
                    permission_id=PERMISSIONS[key].id,
                    effect="allow",
                    scope_kind="floor",
                    scope_id=context.floor_id,
                )
            )
        denied = HousingFloor(
            id=uuid4(),
            building_id=context.building_id,
            number=99,
            name="不可见虚拟楼层",
            display_order=99,
            is_active=True,
        )
        db.add(denied)
        db.flush()
        db.add(
            HousingRoom(
                id=uuid4(),
                building_id=context.building_id,
                floor_id=denied.id,
                room_number="不可见房间",
                normalized_key="不可见房间",
                room_type="single",
                capacity=1,
                verification_status="verified",
                service_status="available",
            )
        )
        db.commit()
        denied_id = denied.id
    try:
        response = _export(client, context)
        assert response.status_code == 200, response.text
        assert "不可见" not in response.content.decode("utf-8-sig")
        denied_response = _export(client, context, floor_ids=[str(denied_id)])
        unknown_response = _export(client, context, floor_ids=[str(uuid4())])
        assert denied_response.status_code == unknown_response.status_code == 404
        assert denied_response.json()["code"] == unknown_response.json()["code"]
    finally:
        with Session(context.engine) as db:
            db.execute(delete(HousingRoom).where(HousingRoom.floor_id == denied_id))
            db.execute(delete(HousingFloor).where(HousingFloor.id == denied_id))
            db.commit()


@pytest.fixture
def sibling_floor(snapshot_client):
    _, context, _ = snapshot_client
    floor_id = uuid4()
    with Session(context.engine) as db:
        db.add(HousingFloor(id=floor_id, building_id=context.building_id, number=98,
                            name="虚拟范围隔离层", display_order=98, is_active=True))
        db.flush()
        db.add(HousingRoom(id=uuid4(), building_id=context.building_id, floor_id=floor_id,
                           room_number="范围隔离房", normalized_key="范围隔离房", room_type="single",
                           capacity=1, verification_status="verified", service_status="available"))
        db.commit()
    try:
        yield floor_id
    finally:
        with Session(context.engine) as db:
            db.execute(delete(HousingRoom).where(HousingRoom.floor_id == floor_id))
            db.execute(delete(HousingFloor).where(HousingFloor.id == floor_id))
            db.commit()


@pytest.mark.parametrize("denied_permission", ["housing.read", "housing.reports.export_sensitive"])
def test_global_allow_with_explicit_floor_deny_never_exports_denied_rooms(
    snapshot_client, sibling_floor, denied_permission
):
    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        db.execute(delete(UserRole).where(UserRole.user_id == context.actor_id))
        for permission in ("housing.read", "housing.reports.export_sensitive"):
            db.add(UserPermissionOverride(user_id=context.actor_id,
                permission_id=PERMISSIONS[permission].id, effect="allow", scope_kind="global"))
        db.add(UserPermissionOverride(user_id=context.actor_id,
            permission_id=PERMISSIONS[denied_permission].id, effect="deny",
            scope_kind="floor", scope_id=sibling_floor))
        db.commit()
    response = _export(client, context)
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert "范围隔离房" not in response.content.decode("utf-8-sig")
    denied = _export(client, context, floor_ids=[str(sibling_floor)])
    unknown = _export(client, context, floor_ids=[str(uuid4())])
    assert denied.status_code == unknown.status_code == 404
    assert denied.json()["code"] == unknown.json()["code"] == "housing_resource_not_found"
    assert denied.json()["detail"] == unknown.json()["detail"]


def test_disjoint_read_export_floors_do_not_qualify_parent_building(snapshot_client, sibling_floor):
    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        db.execute(delete(UserRole).where(UserRole.user_id == context.actor_id))
        for permission, floor_id in (("housing.read", context.floor_id),
                                     ("housing.reports.export_sensitive", sibling_floor)):
            db.add(UserPermissionOverride(user_id=context.actor_id,
                permission_id=PERMISSIONS[permission].id, effect="allow",
                scope_kind="floor", scope_id=floor_id))
        db.commit()
    denied = _export(client, context)
    unknown = _export(client, context, building_ids=[str(uuid4())])
    assert denied.status_code == unknown.status_code == 404
    assert denied.json()["code"] == unknown.json()["code"] == "housing_resource_not_found"
    assert denied.json()["detail"] == unknown.json()["detail"]
    assert "content-disposition" not in denied.headers


def test_unauthorized_and_unknown_buildings_are_indistinguishable(snapshot_client):
    client, context, _ = snapshot_client
    building_id = uuid4()
    with Session(context.engine) as db:
        db.add(HousingBuilding(id=building_id, code="X" + building_id.hex[:8],
                               name="无权限虚拟楼" + building_id.hex[:8], is_active=True))
        db.execute(delete(UserRole).where(UserRole.user_id == context.actor_id))
        for permission in ("housing.read", "housing.reports.export_sensitive"):
            db.add(UserPermissionOverride(user_id=context.actor_id,
                permission_id=PERMISSIONS[permission].id, effect="allow",
                scope_kind="floor", scope_id=context.floor_id))
        db.commit()
    try:
        assert _export(client, context).status_code == 200
        denied = _export(client, context, building_ids=[str(building_id)])
        unknown = _export(client, context, building_ids=[str(uuid4())])
        assert denied.status_code == unknown.status_code == 404
        for key in ("type", "title", "status", "code", "detail"):
            assert denied.json().get(key) == unknown.json().get(key)
        assert "content-disposition" not in denied.headers
    finally:
        with Session(context.engine) as db:
            db.execute(delete(HousingBuilding).where(HousingBuilding.id == building_id))
            db.commit()


@pytest.mark.parametrize("change", ["permission", "account", "session"])
def test_final_reauthorization_rejects_changes_during_render(snapshot_client, monkeypatch, change):
    client, context, auth = snapshot_client
    original = export_router.render_room_status_export

    def render_and_revoke(*args, **kwargs):
        rendered = original(*args, **kwargs)
        with Session(context.engine) as db:
            if change == "permission":
                db.add(
                    UserPermissionOverride(
                        user_id=context.actor_id,
                        permission_id=PERMISSIONS["housing.reports.export_sensitive"].id,
                        effect="deny",
                        scope_kind="global",
                    )
                )
            elif change == "account":
                db.get(UserAccount, context.actor_id).status = "disabled"
            else:
                db.get(AuthSession, auth.session.id).revoked_at = datetime.now(UTC)
            db.commit()
        return rendered

    monkeypatch.setattr(export_router, "render_room_status_export", render_and_revoke)
    response = _export(client, context)
    assert response.status_code == (409 if change == "permission" else 401)
    assert "content-disposition" not in response.headers
    assert "证件号" not in response.text


def test_audit_rejection_never_returns_export_bytes(snapshot_client, monkeypatch):
    client, context, _ = snapshot_client

    def reject(*args, **kwargs):
        raise AuditMetadataRejected("deliberate isolated audit failure")

    monkeypatch.setattr(export_router, "_audit", reject)
    response = _export(client, context)
    assert response.status_code == 500
    assert response.json()["code"] == "housing_audit_failed"
    assert "content-disposition" not in response.headers


def test_audit_commit_failure_never_returns_export_bytes(snapshot_client, monkeypatch):
    client, context, _ = snapshot_client

    def fail_commit(self):
        raise SQLAlchemyError("isolated failed audit transaction")

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = _export(client, context)
    assert response.status_code == 503
    assert response.json()["code"] == "housing_export_snapshot_unavailable"
    assert "content-disposition" not in response.headers
