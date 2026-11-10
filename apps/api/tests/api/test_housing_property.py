from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.errors import ProblemError
from app.housing.models import HousingRoomIssue
from app.housing.property_schemas import PropertyChangeCreate, PropertyReview
from app.housing.property_service import HousingPropertyService
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from tests.api import test_housing_room_status_export as snapshot_fixtures
from tests.invited_housing_helpers import NOW, account, room

snapshot_client = snapshot_fixtures.snapshot_client
base_snapshot_context = snapshot_fixtures.snapshot_context


@pytest.fixture
def snapshot_context(base_snapshot_context):
    from sqlalchemy.orm import Session

    with Session(base_snapshot_context.engine) as db:
        db.add(UserRole(user_id=base_snapshot_context.actor_id, role_id=ROLES["administration"].id))
        db.commit()
    return base_snapshot_context


@pytest.mark.parametrize(
    "roles,deny_export,show",
    [
        (("property",), False, True),
        (("property", "administration"), False, True),
        (("administration",), False, False),
        (("super_admin",), False, False),
        (("property",), True, False),
    ],
)
def test_http_projects_dedicated_export_entry_without_changing_export_authority(
    snapshot_client, roles, deny_export, show
):
    from uuid import uuid4

    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        db.execute(delete(UserRole).where(UserRole.user_id == context.actor_id))
        for role in roles:
            db.add(UserRole(user_id=context.actor_id, role_id=ROLES[role].id))
        if deny_export:
            db.add(
                UserPermissionOverride(
                    user_id=context.actor_id,
                    permission_id=PERMISSIONS["housing.exports.basic"].id,
                    effect="deny",
                    scope_kind="global",
                )
            )
        db.commit()
    response = client.get("/api/v1/housing/property/capabilities")
    assert response.status_code == 200, response.text
    assert response.json()["can_export"] is not deny_export
    assert response.json().get("show_basic_export") is show
    assert response.headers["cache-control"] == "no-store, private"
    missing = client.get("/api/v1/housing/property/capabilities", params={"room_id": str(uuid4())})
    assert missing.status_code == 200, missing.text
    assert missing.json() == {
        "can_submit": False,
        "can_review": False,
        "can_export": False,
        "show_basic_export": False,
        "can_create_request": False,
    }


def test_administration_without_property_keeps_owned_submission_history_and_withdrawal(
    db, platform_users
):
    from app.housing.access import HousingAccessService
    from app.housing.property_models import HousingPropertyChange

    actor, target = account(db, "administration"), room(db)
    service = HousingPropertyService(db)
    access = HousingAccessService(db, actor.id, NOW)
    assert access.roles == {"administration"}
    assert service.capabilities(access, room_id=target.id)["can_submit"] is True
    proposal = HousingPropertyChange(
        room_id=target.id,
        base_version=target.version,
        changes={"notes": "历史行政巡查建议"},
        before={"notes": None},
        reason="历史现场巡查",
        status="pending",
        submitted_by_user_id=actor.id,
        submitted_at=NOW,
    )
    db.add(proposal)
    db.flush()
    pending = service.list(
        access,
        actor.id,
        status="pending",
        room_id=target.id,
        page=1,
        page_size=20,
        view="submitted",
    )
    assert pending.total == 1
    assert [item.id for item in pending.items] == [proposal.id]
    assert pending.items[0].can_withdraw is True
    assert pending.items[0].can_review is False
    withdrawn = service.withdraw(
        proposal.id, actor_id=actor.id, occurred_at=NOW + timedelta(seconds=1)
    )
    assert withdrawn.status == "withdrawn"
    history = service.list(
        access,
        actor.id,
        status="withdrawn",
        room_id=target.id,
        page=1,
        page_size=20,
        view="submitted",
    )
    assert history.total == 1
    assert [item.id for item in history.items] == [proposal.id]
    assert history.items[0].status == "withdrawn"
    assert history.items[0].can_withdraw is False
    assert target.notes is None


@pytest.mark.parametrize(
    "roles,deny_direct",
    [
        (("administration",), False),
        (("super_admin",), False),
        (("administration", "property"), False),
        (("super_admin", "property"), False),
        (("administration", "property"), True),
    ],
)
def test_http_direct_managers_save_without_proposals_and_cannot_fall_back_after_deny(
    db_client, db, platform_users, roles, deny_direct
):
    from sqlalchemy import func

    from app.housing.models import HousingRoom
    from app.housing.property_models import HousingPropertyChange
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    from tests.api.test_housing import sign_in

    actor = platform_users["accounts"]["customer_service"]
    for role in roles:
        db.add(UserRole(user_id=actor.id, role_id=ROLES[role].id))
    target = room(db)
    if deny_direct:
        db.add(
            UserPermissionOverride(
                user_id=actor.id,
                permission_id=PERMISSIONS["housing.rooms.manage"].id,
                effect="deny",
                scope_kind="floor",
                scope_id=target.floor_id,
            )
        )
    target_id, version = target.id, target.version
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")
    capabilities = db_client.get(
        "/api/v1/housing/property/capabilities", params={"room_id": str(target_id)}
    )
    assert capabilities.status_code == 200, capabilities.text
    assert capabilities.json().get("can_create_request") is False
    rejected = db_client.post(
        f"/api/v1/housing/rooms/{target_id}/change-requests",
        json={
            "expected_version": version,
            "changes": {"notes": "不应待审"},
            "reason": "行政直接修改",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 403, rejected.text
    assert "直接编辑" in rejected.json()["detail"]
    db.expire_all()
    assert db.get(HousingRoom, target_id).version == version
    assert db.get(HousingRoom, target_id).notes is None
    direct = db_client.patch(
        f"/api/v1/housing/rooms/{target_id}",
        json={"version": version, "notes": "行政直接保存", "service_status": "maintenance"},
        headers={"X-CSRF-Token": csrf},
    )
    assert direct.status_code == (403 if deny_direct else 200), direct.text
    db.expire_all()
    persisted = db.get(HousingRoom, target_id)
    assert persisted.version == version + (0 if deny_direct else 1)
    assert persisted.notes == (None if deny_direct else "行政直接保存")
    assert persisted.service_status == ("available" if deny_direct else "maintenance")
    assert (
        db.scalar(
            select(func.count())
            .select_from(HousingPropertyChange)
            .where(HousingPropertyChange.room_id == target_id)
        )
        == 0
    )


def test_property_views_filter_before_count_and_pagination(db, platform_users):
    from app.housing.access import HousingAccessService
    from app.housing.property_models import HousingPropertyChange
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    actor, other = account(db, "property"), account(db, "property")
    db.add(UserRole(user_id=actor.id, role_id=ROLES["administration"].id))
    targets = [room(db) for _ in range(7)]
    rows = []
    for index, target in enumerate(targets):
        row = HousingPropertyChange(
            room_id=target.id,
            base_version=target.version,
            changes={"notes": "建议"},
            before={"notes": None},
            reason="巡查",
            status="pending",
            submitted_by_user_id=actor.id if index in {0, 2, 4, 6} else other.id,
            submitted_at=NOW + timedelta(seconds=index),
        )
        db.add(row)
        rows.append(row)
    for permission, indices in (
        ("housing.changes.review", (4, 5)),
        ("housing.changes.submit", (2,)),
    ):
        for index in indices:
            db.add(
                UserPermissionOverride(
                    user_id=actor.id,
                    permission_id=PERMISSIONS[permission].id,
                    effect="deny",
                    scope_kind="floor",
                    scope_id=targets[index].floor_id,
                )
            )
    db.flush()
    service = HousingPropertyService(db)
    access = HousingAccessService(db, actor.id, NOW)
    expected = {"submitted": [6, 4, 0], "review": [6, 3, 2, 1, 0], "all": [6, 4, 3, 2, 1, 0]}
    for view, indices in expected.items():
        actual = []
        for page in range(1, (len(indices) + 1) // 2 + 1):
            result = service.list(
                access, actor.id, status="pending", room_id=None, page=page, page_size=2, view=view
            )
            assert result.total == len(indices)
            actual.extend(item.id for item in result.items)
            for item in result.items:
                if item.id in {rows[index].id for index in (0, 2, 4, 6)}:
                    assert item.can_review is False
        assert actual == [rows[index].id for index in indices]
    default = service.list(access, actor.id, status="pending", room_id=None, page=1, page_size=100)
    assert [item.id for item in default.items] == [rows[index].id for index in expected["all"]]
    with pytest.raises(ProblemError) as caught:
        service.review(
            rows[0].id,
            PropertyReview(decision="approve"),
            actor_id=actor.id,
            occurred_at=NOW + timedelta(minutes=1),
        )
    assert caught.value.status == 403


def test_http_rejects_forged_property_view(snapshot_client):
    client, _, _ = snapshot_client
    response = client.get("/api/v1/housing/property/changes?view=forged")
    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store, private"


def test_http_property_views_keep_submission_ownership_and_default_union(snapshot_client):
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.housing.models import HousingRoom
    from app.housing.property_models import HousingPropertyChange

    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        db.add(UserRole(user_id=context.actor_id, role_id=ROLES["property"].id))
        targets = [
            HousingRoom(
                building_id=context.building_id,
                floor_id=context.floor_id,
                room_number=f"VIEW-{index}",
                normalized_key=f"VIEW-{index}",
                room_type="single",
                capacity=1,
            )
            for index in range(3)
        ]
        db.add_all(targets)
        db.flush()
        rows = [
            HousingPropertyChange(
                room_id=target.id,
                base_version=target.version,
                changes={"notes": "巡查建议"},
                before={"notes": None},
                reason="巡查",
                status="pending",
                submitted_by_user_id=context.actor_id
                if index != 1
                else context.resident_account_id,
                submitted_at=NOW + timedelta(seconds=index),
            )
            for index, target in enumerate(targets)
        ]
        db.add_all(rows)
        db.flush()
        row_ids = [row.id for row in rows]
        room_ids = [target.id for target in targets]
        db.commit()
    try:
        for view, indices in (("submitted", [2, 0]), ("review", [2, 1, 0]), (None, [2, 1, 0])):
            actual = []
            for page in range(1, len(indices) + 1):
                response = client.get(
                    "/api/v1/housing/property/changes",
                    params={"page": page, "page_size": 1, **({"view": view} if view else {})},
                )
                assert response.status_code == 200, response.text
                assert response.json()["total"] == len(indices)
                actual.extend(item["id"] for item in response.json()["items"])
            assert actual == [str(row_ids[index]) for index in indices]
    finally:
        with Session(context.engine) as db:
            db.execute(delete(HousingPropertyChange).where(HousingPropertyChange.id.in_(row_ids)))
            db.execute(delete(HousingRoom).where(HousingRoom.id.in_(room_ids)))
            db.commit()


def test_proposal_pending_does_not_mutate_and_approval_executes_once(db, platform_users):
    proposer, reviewer = account(db, "property"), account(db, "administration")
    target = room(db)
    target.cleaning_status = "pending"
    target.cleaning_pending_at = NOW
    db.flush()
    version = target.version
    service = HousingPropertyService(db)
    row = service.submit(
        target.id,
        PropertyChangeCreate(
            expected_version=version,
            changes={"service_status": "maintenance", "notes": "维修"},
            reason="巡查",
        ),
        actor_id=proposer.id,
        occurred_at=NOW,
    )
    assert target.version == version
    assert target.service_status == "available"
    assert row.status == "pending"
    service.review(
        row.id,
        PropertyReview(decision="approve"),
        actor_id=reviewer.id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert target.service_status == "maintenance"
    assert target.cleaning_status == "pending"
    assert target.version == version + 1
    with pytest.raises(ProblemError):
        service.review(
            row.id,
            PropertyReview(decision="approve"),
            actor_id=reviewer.id,
            occurred_at=NOW + timedelta(seconds=2),
        )
    assert target.version == version + 1


def test_property_cannot_directly_manage_even_with_independent_grant(db, platform_users):
    from app.housing.access import HousingAccessService
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    proposer, target = account(db, "property"), room(db)
    db.add(
        UserPermissionOverride(
            user_id=proposer.id,
            permission_id=PERMISSIONS["housing.rooms.manage"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.flush()
    access = HousingAccessService(db, proposer.id, NOW)
    assert access.can_submit_changes(target)
    assert not access.can_manage_room(target)
    assert not access.can_manage_issues(target)


@pytest.mark.parametrize("failure", ["self", "staff", "admin", "revoked", "disabled", "stale"])
def test_review_revalidates_identity_scope_and_version(db, platform_users, failure):
    proposer, reviewer = account(db, "property"), account(db, "administration")
    target = room(db)
    service = HousingPropertyService(db)
    row = service.submit(
        target.id,
        PropertyChangeCreate(
            expected_version=target.version, changes={"notes": "待审核"}, reason="巡查"
        ),
        actor_id=proposer.id,
        occurred_at=NOW,
    )
    if failure == "self":
        reviewer = proposer
        db.add(UserRole(user_id=proposer.id, role_id=ROLES["administration"].id))
    elif failure in {"staff", "admin"}:
        reviewer = account(db, failure)
    elif failure == "revoked":
        db.delete(db.scalar(select(UserRole).where(UserRole.user_id == proposer.id)))
    elif failure == "disabled":
        proposer.status = "disabled"
    elif failure == "stale":
        target.version += 1
    db.flush()
    with pytest.raises(ProblemError):
        service.review(
            row.id,
            PropertyReview(decision="approve"),
            actor_id=reviewer.id,
            occurred_at=NOW + timedelta(seconds=1),
        )
    assert row.status == "pending"
    assert target.notes is None


def test_approved_problem_uses_issue_lifecycle_and_preserves_evidence(db, platform_users):
    proposer, reviewer, target = account(db, "property"), account(db, "administration"), room(db)
    service = HousingPropertyService(db)
    row = service.submit(
        target.id,
        PropertyChangeCreate(
            expected_version=target.version,
            changes={"service_status": "problem", "notes": "漏水"},
            reason="巡查发现",
        ),
        actor_id=proposer.id,
        occurred_at=NOW,
    )
    service.review(
        row.id,
        PropertyReview(decision="approve"),
        actor_id=reviewer.id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    issue = db.scalar(select(HousingRoomIssue).where(HousingRoomIssue.room_id == target.id))
    assert issue.status == "open" and issue.description == "漏水"
    row2 = service.submit(
        target.id,
        PropertyChangeCreate(
            expected_version=target.version,
            changes={"service_status": "maintenance"},
            reason="转维修处理",
        ),
        actor_id=proposer.id,
        occurred_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(ProblemError) as caught:
        service.review(
            row2.id,
            PropertyReview(decision="approve"),
            actor_id=reviewer.id,
            occurred_at=NOW + timedelta(seconds=3),
        )
    assert caught.value.status == 409
    assert caught.value.code == "housing_issue_workflow_required"
    assert "重新提交" in caught.value.detail
    assert issue.status == "open"
    assert issue.description == "漏水"
    assert issue.resolution_note is None
    assert target.service_status == "problem"
    assert row2.status == "pending"


@pytest.mark.parametrize("operation", ["reject", "withdraw"])
def test_reject_withdraw_do_not_apply_pending_changes(db, platform_users, operation):
    proposer, reviewer, target = account(db, "property"), account(db, "administration"), room(db)
    version = target.version
    service = HousingPropertyService(db)
    row = service.submit(
        target.id,
        PropertyChangeCreate(expected_version=version, changes={"notes": "待审"}, reason="巡查"),
        actor_id=proposer.id,
        occurred_at=NOW,
    )
    if operation == "reject":
        service.review(
            row.id,
            PropertyReview(decision="reject", note="无需修改"),
            actor_id=reviewer.id,
            occurred_at=NOW + timedelta(seconds=1),
        )
        assert row.status == "rejected"
    else:
        service.withdraw(row.id, actor_id=proposer.id, occurred_at=NOW + timedelta(seconds=1))
        assert row.status == "withdrawn"
    assert target.version == version and target.notes is None


def test_http_proposal_audited_and_existing_room_write_forbidden(db_client, db, platform_users):
    from app.audit.models import AuditEvent
    from tests.api.test_housing import sign_in

    proposer = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=proposer.id, role_id=ROLES["property"].id))
    target = room(db)
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")
    response = db_client.post(
        f"/api/v1/housing/rooms/{target.id}/change-requests",
        json={
            "expected_version": target.version,
            "changes": {"notes": "等待审核"},
            "reason": "日常巡查",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["can_withdraw"] is True
    assert (
        db.scalar(
            select(AuditEvent).where(
                AuditEvent.actor_user_id == proposer.id,
                AuditEvent.action == "housing.property_change.submit",
            )
        )
        is not None
    )
    db.refresh(target)
    assert target.notes is None
    response = db_client.patch(
        f"/api/v1/housing/rooms/{target.id}",
        json={"version": target.version, "notes": "绕过审批"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("status", ["pending", "rejected", "withdrawn", "approved"])
def test_http_delete_preserves_property_proposal_history(db_client, db, platform_users, status):
    from fastapi.testclient import TestClient

    from app.audit.models import AuditEvent
    from app.housing.models import HousingRoom, HousingStay
    from app.housing.property_models import HousingPropertyChange
    from tests.api.test_housing import sign_in

    proposer, reviewer, target = account(db, "property"), account(db, "administration"), room(db)
    service = HousingPropertyService(db)
    proposal = service.submit(
        target.id,
        PropertyChangeCreate(
            expected_version=target.version,
            changes={"notes": "保留巡查历史"},
            reason="日常巡查",
        ),
        actor_id=proposer.id,
        occurred_at=NOW,
    )
    if status == "withdrawn":
        service.withdraw(proposal.id, actor_id=proposer.id, occurred_at=NOW + timedelta(seconds=1))
    elif status in {"rejected", "approved"}:
        service.review(
            proposal.id,
            PropertyReview(
                decision="approve" if status == "approved" else "reject",
                note="巡查审核",
            ),
            actor_id=reviewer.id,
            occurred_at=NOW + timedelta(seconds=1),
        )
    room_id, proposal_id = target.id, proposal.id
    version, notes = target.version, target.notes
    assert proposal.status == status
    assert db.scalar(select(HousingStay.id).where(HousingStay.room_id == room_id)) is None
    assert db.scalar(select(HousingRoomIssue.id).where(HousingRoomIssue.room_id == room_id)) is None
    db.commit()
    request_id = f"housing-room-delete-property-{status}"
    # Exercise the normal HTTP error response, including an uncaught server error
    # before the domain guard exists, rather than letting TestClient re-raise it.
    with TestClient(db_client.app, raise_server_exceptions=False) as client:
        response = client.delete(
            f"/api/v1/housing/rooms/{room_id}",
            headers={
                "X-CSRF-Token": sign_in(client, platform_users, "admin"),
                "X-Request-ID": request_id,
            },
        )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "housing_catalog_has_children"
    db.expire_all()
    retained_room = db.get(HousingRoom, room_id)
    retained_proposal = db.get(HousingPropertyChange, proposal_id)
    assert retained_room is not None
    assert retained_room.version == version and retained_room.notes == notes
    assert retained_proposal is not None
    assert retained_proposal.room_id == room_id and retained_proposal.status == status
    assert retained_proposal.changes == {"notes": "保留巡查历史"}
    assert retained_proposal.reason == "日常巡查"
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert audit.action == "housing.room.delete"
    assert audit.actor_user_id == platform_users["accounts"]["admin"].id
    assert audit.resource_id == room_id and audit.outcome == "failed"


def test_http_delete_unused_room_still_succeeds(db_client, db, platform_users):
    from app.audit.models import AuditEvent
    from app.housing.models import HousingRoom
    from tests.api.test_housing import sign_in

    room_id = room(db).id
    db.commit()
    response = db_client.delete(
        f"/api/v1/housing/rooms/{room_id}",
        headers={
            "X-CSRF-Token": sign_in(db_client, platform_users, "admin"),
            "X-Request-ID": "housing-room-delete-unused-property-control",
        },
    )
    assert response.status_code == 204, response.text
    db.expire_all()
    assert db.get(HousingRoom, room_id) is None
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.request_id == "housing-room-delete-unused-property-control"
        )
    )
    assert audit is not None
    assert audit.action == "housing.room.delete" and audit.outcome == "allowed"


def test_http_basic_export_has_only_current_basic_fields_and_audit(snapshot_client):
    import csv
    from io import StringIO

    from sqlalchemy.orm import Session

    from app.audit.models import AuditEvent

    client, context, _ = snapshot_client
    response = client.post(
        "/api/v1/housing/property/exports",
        json={
            "kind": "occupants",
            "format": "csv",
            "purpose": "住宿摘要核对",
            "building_ids": [str(context.building_id)],
        },
    )
    assert response.status_code == 200, response.text
    data = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert len(data) == 1
    assert set(data[0]) == {
        "楼栋",
        "楼层",
        "房号",
        "姓名",
        "昵称",
        "性别",
        "实际入住日期",
        "计划离开日期",
        "房卡开卡日",
        "房卡到期日",
    }
    assert data[0]["计划离开日期"] == "常住"
    assert response.headers["cache-control"] == "no-store, private"
    with Session(context.engine) as db:
        assert (
            db.scalar(
                select(AuditEvent).where(
                    AuditEvent.actor_user_id == context.actor_id,
                    AuditEvent.action == "housing.property.export",
                )
            )
            is not None
        )


def test_basic_export_revoked_before_response_never_returns_file(snapshot_client, monkeypatch):
    from sqlalchemy.orm import Session

    from app.housing import property_export_router as exports
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    client, context, _ = snapshot_client
    original = exports.render_property_export

    def revoke_then_render(*args, **kwargs):
        result = original(*args, **kwargs)
        with Session(context.engine) as db:
            db.add(
                UserPermissionOverride(
                    user_id=context.actor_id,
                    permission_id=PERMISSIONS["housing.exports.basic"].id,
                    effect="deny",
                    scope_kind="global",
                )
            )
            db.commit()
        return result

    monkeypatch.setattr(exports, "render_property_export", revoke_then_render)
    response = client.post(
        "/api/v1/housing/property/exports",
        json={
            "kind": "occupants",
            "format": "csv",
            "purpose": "住宿摘要核对",
            "building_ids": [str(context.building_id)],
        },
    )
    assert response.status_code == 409, response.text
    assert "content-disposition" not in response.headers


def test_property_explicit_deny_and_mixed_roles_remain_scoped(db, platform_users):
    from app.housing.access import HousingAccessService
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    proposer, target = account(db, "property"), room(db)
    db.add(UserRole(user_id=proposer.id, role_id=ROLES["coach"].id))
    db.add(
        UserPermissionOverride(
            user_id=proposer.id,
            permission_id=PERMISSIONS["housing.changes.submit"].id,
            effect="deny",
            scope_kind="floor",
            scope_id=target.floor_id,
        )
    )
    db.flush()
    access = HousingAccessService(db, proposer.id, NOW)
    assert access.can_read_room(target)
    assert not access.can_submit_changes(target)
    assert access.can_manage_keycards(target)
    assert (
        db.scalar(
            select(type(target).id).where(
                type(target).id == target.id, access.submit_changes_criteria()
            )
        )
        is None
    )


def test_only_one_pending_request_per_room(db, platform_users):
    proposer, target = account(db, "property"), room(db)
    service = HousingPropertyService(db)
    payload = PropertyChangeCreate(
        expected_version=target.version, changes={"notes": "巡查"}, reason="日常巡查"
    )
    service.submit(target.id, payload, actor_id=proposer.id, occurred_at=NOW)
    with pytest.raises(ProblemError) as caught:
        service.submit(
            target.id, payload, actor_id=proposer.id, occurred_at=NOW + timedelta(seconds=1)
        )
    assert caught.value.status == 409


def test_reviewer_can_reject_inactive_proposer_without_applying_changes(db, platform_users):
    proposer, reviewer, target = account(db, "property"), account(db, "administration"), room(db)
    service = HousingPropertyService(db)
    proposal = service.submit(
        target.id,
        PropertyChangeCreate(
            expected_version=target.version, changes={"notes": "待审"}, reason="巡查"
        ),
        actor_id=proposer.id,
        occurred_at=NOW,
    )
    proposer.status = "disabled"
    db.flush()
    service.review(
        proposal.id,
        PropertyReview(decision="reject", note="申请人已停用"),
        actor_id=reviewer.id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert proposal.status == "rejected"
    assert target.notes is None


def test_concurrent_review_executes_exactly_once(snapshot_context):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime

    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.auth.models import UserAccount
    from app.housing.models import HousingRoom
    from app.housing.property_models import HousingPropertyChange
    from app.identity.models import Person

    context = snapshot_context
    with Session(context.engine) as db:
        proposer = account(db, "property")
        proposer_id, person_id = proposer.id, proposer.person_id
        target = db.scalar(select(HousingRoom).where(HousingRoom.floor_id == context.floor_id))
        room_id = target.id
        initial_version = target.version
        proposal = HousingPropertyService(db).submit(
            target.id,
            PropertyChangeCreate(
                expected_version=initial_version, changes={"notes": "并发审核"}, reason="日常巡查"
            ),
            actor_id=proposer_id,
            occurred_at=datetime.now(UTC),
        )
        request_id = proposal.id
        db.commit()

    def approve():
        with Session(context.engine) as db:
            try:
                result = HousingPropertyService(db).review(
                    request_id,
                    PropertyReview(decision="approve"),
                    actor_id=context.actor_id,
                    occurred_at=datetime.now(UTC),
                )
                return result.status
            except ProblemError as exc:
                return exc.status

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: approve(), range(2)))
        assert sorted(results, key=str) == [409, "approved"]
        with Session(context.engine) as db:
            assert db.get(HousingRoom, room_id).version == initial_version + 1
    finally:
        with Session(context.engine) as db:
            db.get(HousingRoom, room_id).updated_by_user_id = None
            db.execute(delete(HousingPropertyChange).where(HousingPropertyChange.id == request_id))
            db.execute(delete(UserRole).where(UserRole.user_id == proposer_id))
            db.execute(delete(UserAccount).where(UserAccount.id == proposer_id))
            db.execute(delete(Person).where(Person.id == person_id))
            db.commit()


def test_downgrade_refuses_custom_property_permission_grants(db):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.rbac.models import RolePermission
    from app.rbac.seed import PERMISSIONS

    db.add(
        RolePermission(
            role_id=ROLES["staff"].id,
            permission_id=PERMISSIONS["housing.exports.basic"].id,
            scope_kind="global",
        )
    )
    db.flush()
    migration = import_module("migrations.versions.0083_housing_property_changes")
    with (
        db.begin_nested(),
        pytest.raises(RuntimeError, match="custom.*grant"),
        Operations.context(MigrationContext.configure(db.connection())),
    ):
        migration.downgrade()


def test_problem_export_includes_all_unavailable_rooms_and_open_evidence(snapshot_client):
    import csv
    from datetime import UTC, datetime
    from io import StringIO

    from sqlalchemy import delete, update
    from sqlalchemy.orm import Session

    from app.housing.issue_service import HousingIssueService
    from app.housing.models import HousingRoom
    from app.housing.schemas import RoomIssueCreate

    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        target = db.scalar(select(HousingRoom).where(HousingRoom.floor_id == context.floor_id))
        HousingIssueService(db).report(
            target.id,
            RoomIssueCreate(room_version=target.version, description="窗户漏水"),
            actor_user_id=context.actor_id,
            occurred_at=datetime.now(UTC),
            authorize_room=lambda _: None,
        )
        for index in range(25):
            db.add(
                HousingRoom(
                    building_id=context.building_id,
                    floor_id=context.floor_id,
                    room_number=f"Q{index}",
                    normalized_key=f"Q{index}",
                    room_type="single",
                    capacity=1,
                    service_status=("problem", "maintenance", "disabled")[index % 3],
                    notes="=1+1" if index == 0 else "联系13800138000" if index == 1 else "检查记录",
                )
            )
        db.commit()
    try:
        response = client.post(
            "/api/v1/housing/property/exports",
            json={
                "kind": "problems",
                "format": "csv",
                "purpose": "问题房巡检核对",
                "building_ids": [str(context.building_id)],
            },
        )
        assert response.status_code == 200, response.text
        rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
        assert len(rows) == 26
        assert set(rows[0]) == {
            "楼栋",
            "楼层",
            "房号",
            "房型",
            "服务状态",
            "房间备注",
            "未解决问题说明",
        }
        assert {row["服务状态"] for row in rows} == {"问题房", "维修中", "停用"}
        assert any(row["未解决问题说明"] == "窗户漏水" for row in rows)
        assert next(row["房间备注"] for row in rows if row["房号"] == "Q0") == "'=1+1"
        assert (
            next(row["房间备注"] for row in rows if row["房号"] == "Q1") == "[含敏感资料，已隐藏]"
        )
    finally:
        with Session(context.engine) as db:
            room_ids = select(HousingRoom.id).where(HousingRoom.floor_id == context.floor_id)
            db.execute(delete(HousingRoomIssue).where(HousingRoomIssue.room_id.in_(room_ids)))
            db.execute(
                update(HousingRoom)
                .where(HousingRoom.floor_id == context.floor_id)
                .values(updated_by_user_id=None)
            )
            db.commit()


@pytest.mark.parametrize("change", ["move", "move_allowed", "delete"])
def test_problem_export_rechecks_each_room_after_scope_or_identity_change(
    snapshot_client, monkeypatch, change
):
    from sqlalchemy import delete, update
    from sqlalchemy.orm import Session

    from app.housing import property_export_router as exports
    from app.housing.inventory_service import HousingInventoryService
    from app.housing.models import HousingFloor, HousingRoom
    from app.housing.schemas import RoomUpdate
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        denied_floor = HousingFloor(building_id=context.building_id, number=3, name="受限楼层")
        db.add(denied_floor)
        db.flush()
        denied_floor_id = denied_floor.id
        target = HousingRoom(
            building_id=context.building_id,
            floor_id=context.floor_id,
            room_number="EMPTY-PROBLEM",
            normalized_key="EMPTY-PROBLEM",
            room_type="single",
            capacity=1,
            service_status="maintenance",
            notes="受限维修说明",
        )
        db.add(target)
        if change != "move_allowed":
            db.add(
                UserPermissionOverride(
                    user_id=context.actor_id,
                    permission_id=PERMISSIONS["housing.exports.basic"].id,
                    effect="deny",
                    scope_kind="floor",
                    scope_id=denied_floor_id,
                )
            )
        db.flush()
        target_id = target.id
        db.commit()
    original = exports.render_property_export

    def move_exported_room_then_render(*args, **kwargs):
        result = original(*args, **kwargs)
        with Session(context.engine) as db:
            target = db.get(HousingRoom, target_id)
            if change in {"move", "move_allowed"}:
                HousingInventoryService(db).update_room(
                    target.id,
                    RoomUpdate(version=target.version, floor_id=denied_floor_id),
                    actor_user_id=context.actor_id,
                )
            else:
                HousingInventoryService(db).delete_room(target.id)
            db.commit()
        return result

    monkeypatch.setattr(exports, "render_property_export", move_exported_room_then_render)
    try:
        response = client.post(
            "/api/v1/housing/property/exports",
            json={
                "kind": "problems",
                "format": "csv",
                "purpose": "跨楼层权限核对",
                "building_ids": [str(context.building_id)],
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "housing_access_changed"
        assert "content-disposition" not in response.headers
        assert "受限维修说明" not in response.text
    finally:
        with Session(context.engine) as db:
            db.execute(delete(HousingRoom).where(HousingRoom.id == target_id))
            db.execute(
                delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == context.actor_id,
                    UserPermissionOverride.scope_id == denied_floor_id,
                )
            )
            db.execute(delete(HousingFloor).where(HousingFloor.id == denied_floor_id))
            db.execute(
                update(HousingRoom)
                .where(HousingRoom.floor_id == context.floor_id)
                .values(updated_by_user_id=None)
            )
            db.commit()


@pytest.mark.parametrize(
    "change,expected_status", [("deny", 409), ("disabled", 401), ("session_revoked", 401)]
)
def test_export_refreshes_authority_after_waiting_for_room_lock(
    snapshot_client, change, expected_status
):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime
    from threading import Event
    from time import monotonic, sleep

    from sqlalchemy import delete, event, text
    from sqlalchemy.orm import Session

    from app.auth.models import AuthSession, UserAccount
    from app.housing.models import HousingRoom
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    client, context, auth = snapshot_client
    with Session(context.engine) as db:
        target = HousingRoom(
            building_id=context.building_id,
            floor_id=context.floor_id,
            room_number="WAIT-ROOM",
            normalized_key="WAIT-ROOM",
            room_type="single",
            capacity=1,
            service_status="maintenance",
            notes="锁等待期间不得泄露",
        )
        db.add(target)
        db.flush()
        target_id = target.id
        db.commit()
    lock_query_started = Event()
    backend_pids = []

    def observe_room_lock(
        connection, cursor, statement, parameters, execution_context, executemany
    ):
        if "FOR SHARE" in statement and "housing_rooms" in statement:
            backend_pids.append(connection.connection.driver_connection.info.backend_pid)
            lock_query_started.set()

    event.listen(context.engine, "before_cursor_execute", observe_room_lock)
    try:
        with Session(context.engine) as holder, ThreadPoolExecutor(max_workers=1) as pool:
            holder.scalar(select(HousingRoom).where(HousingRoom.id == target_id).with_for_update())
            future = pool.submit(
                client.post,
                "/api/v1/housing/property/exports",
                json={
                    "kind": "problems",
                    "format": "csv",
                    "purpose": "锁等待权限复验",
                    "building_ids": [str(context.building_id)],
                },
            )
            try:
                assert lock_query_started.wait(5), "export never reached the shared room lock"
                deadline = monotonic() + 5
                with Session(context.engine) as observer:
                    while not observer.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=:pid AND wait_event_type='Lock')"
                        ),
                        {"pid": backend_pids[0]},
                    ):
                        assert monotonic() < deadline, "export was not blocked on the held room row"
                        sleep(0.01)
                with Session(context.engine) as revoker:
                    if change == "deny":
                        revoker.add(
                            UserPermissionOverride(
                                user_id=context.actor_id,
                                permission_id=PERMISSIONS["housing.exports.basic"].id,
                                effect="deny",
                                scope_kind="global",
                            )
                        )
                    elif change == "disabled":
                        revoker.get(UserAccount, context.actor_id).status = "disabled"
                    else:
                        revoker.get(AuthSession, auth.session.id).revoked_at = datetime.now(UTC)
                    revoker.commit()
            finally:
                holder.rollback()
            response = future.result(timeout=10)
        assert response.status_code == expected_status, response.text
        assert "content-disposition" not in response.headers
        assert "锁等待期间不得泄露" not in response.text
    finally:
        event.remove(context.engine, "before_cursor_execute", observe_room_lock)
        with Session(context.engine) as db:
            db.execute(delete(HousingRoom).where(HousingRoom.id == target_id))
            db.commit()
