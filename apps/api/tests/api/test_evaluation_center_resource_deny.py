"""A center deny must dominate resource operations, projections and side effects."""

# ruff: noqa: F811 - imported pytest fixtures are intentionally injected by name

import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.media_store import PrivateMediaStore
from app.rbac.models import Permission, Role, UserPermissionOverride
from app.rbac.schemas import PermissionOverrideCreate
from app.rbac.service import PermissionDelegationDenied, RbacService
from tests.api.test_camp_surveys import camp_api_scenario  # noqa: F401
from tests.api.test_system_evaluation_tasks import (
    _login,
    _png_bytes,
    _session_request,
    system_evaluation_api_scenario,  # noqa: F401
)
from tests.api.test_teaching_reports import (
    NOW,
    _create_pending_report,
    _report_write_counts,
    report_api_scenario,  # noqa: F401
)


def _deny(db, *, actor_id, account_id, scope_kind, scope_id, valid_from=None):
    permission = db.scalar(select(Permission).where(Permission.key == "evaluation_center.access"))
    assert permission is not None
    RbacService(db).add_override(
        actor_user_id=actor_id,
        account_id=account_id,
        input=PermissionOverrideCreate(
            permission_id=permission.id,
            effect="deny",
            scope_kind=scope_kind,
            scope_id=scope_id,
            valid_from=valid_from,
            grant_reason="Resource deny regression",
        ),
    )


@pytest.mark.parametrize("scope_kind", ["enrollment", "assigned_class"])
def test_center_deny_hides_report_and_blocks_draft(
    db, db_client, report_api_scenario, platform_users, scope_kind,
):
    scenario = report_api_scenario.report
    csrf = report_api_scenario.login(db_client, "primary")
    report_id = uuid4()
    payload = {
        "enrollment_id": str(scenario.enrollment.id),
        "report_kind": "daily",
        "period_start_date": NOW.date().isoformat(),
        "body": "private draft",
        "dimension_values": {},
        "expected_draft_revision": 0,
    }
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())}
    created = db_client.put(
        f"/api/v1/evaluations/reports/{report_id}/draft", headers=headers, json=payload
    )
    assert created.status_code == 200, created.text
    _deny(
        db,
        # The report reviewer stays an ordinary admin. A separate real manager
        # installs the deny; report access does not imply permissions.manage.
        actor_id=platform_users["accounts"]["admin"].id,
        account_id=scenario.accounts["primary"].id,
        scope_kind=scope_kind,
        scope_id=scenario.enrollment.id
        if scope_kind == "enrollment"
        else scenario.teaching_class.id,
        valid_from=NOW,
    )
    before = _report_write_counts(db)
    detail = db_client.get(f"/api/v1/evaluations/reports/{report_id}")
    assert detail.status_code == 404, detail.text
    targets = db_client.get("/api/v1/evaluations/reports/targets")
    assert targets.status_code == 200, targets.text
    assert all(
        item["enrollment_id"] != str(scenario.enrollment.id) for item in targets.json()["items"]
    )
    payload["expected_draft_revision"] = created.json()["draft_revision"]
    payload["body"] = "must not persist"
    denied = db_client.put(
        f"/api/v1/evaluations/reports/{report_id}/draft",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json=payload,
    )
    assert denied.status_code == 404, denied.text
    assert _report_write_counts(db) == before


@pytest.mark.parametrize(
    "endpoint",
    ["/api/v1/evaluations/admin/surveys", "/api/v1/evaluations/admin/surveys/print-projection"],
)
@pytest.mark.parametrize("scope_kind", ["global", "enrollment", "assigned_class"])
def test_center_deny_removes_admin_survey_identity(
    db, db_client, camp_api_scenario, endpoint, scope_kind
):
    scenario = camp_api_scenario
    csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers={"X-CSRF-Token": csrf},
        json=scenario.submission_payload(key=str(uuid4())),
    )
    assert submitted.status_code == 201, submitted.text
    account = scenario.accounts["customer_service"]
    admin = db.scalar(select(Role).where(Role.key == "admin"))
    assert admin is not None
    RbacService(db).assign_role(account_id=account.id, role_id=admin.id)
    scenario.login(db_client, "customer_service")
    baseline = db_client.get(endpoint, params={"identity": "real_name"})
    assert baseline.status_code == 200 and baseline.json()["items"], baseline.text
    scope_id = {
        "global": None,
        "enrollment": scenario.cycle.enrollment_id,
        "assigned_class": scenario.cycle.class_id,
    }[scope_kind]
    _deny(
        db,
        actor_id=scenario.accounts["admin"].id,
        account_id=account.id,
        scope_kind=scope_kind,
        scope_id=scope_id,
    )
    request_id = f"deny-survey-{uuid4()}"
    response = db_client.get(
        endpoint, params={"identity": "real_name"}, headers={"X-Request-ID": request_id}
    )
    if scope_kind == "global":
        assert response.status_code == 403, response.text
    else:
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
    assert (
        db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.request_id == request_id)) == 0
    )


def test_center_deny_hides_report_review_queue_and_versions(
    db, db_client, report_api_scenario, platform_users,
):
    scenario = report_api_scenario.report
    report_id, _ = _create_pending_report(
        db_client, report_api_scenario, starts_on=NOW.date(), body="review private"
    )
    csrf = report_api_scenario.login(db_client, "admin")
    baseline = db_client.get("/api/v1/evaluations/reports/review-queue")
    assert baseline.status_code == 200 and baseline.json()["total"] == 1
    _deny(
        db,
        actor_id=platform_users["accounts"]["admin"].id,
        account_id=scenario.accounts["admin"].id,
        scope_kind="enrollment",
        scope_id=scenario.enrollment.id,
        valid_from=NOW,
    )
    before = _report_write_counts(db)
    queue = db_client.get("/api/v1/evaluations/reports/review-queue")
    assert queue.status_code == 200 and queue.json()["total"] == 0, queue.text
    versions = db_client.get(f"/api/v1/evaluations/reports/{report_id}/versions")
    assert versions.status_code == 404, versions.text
    denied = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert denied.status_code == 404, denied.text
    assert _report_write_counts(db) == before


def test_center_deny_blocks_upload_target(
    db, db_client, platform_users, system_evaluation_api_scenario
):
    scenario = system_evaluation_api_scenario
    csrf = _login(db_client, platform_users, "self_student")
    _deny(
        db,
        actor_id=platform_users["accounts"]["admin"].id,
        account_id=scenario.account_id,
        scope_kind="enrollment",
        scope_id=scenario.enrollment_id,
    )
    response = db_client.post(
        "/api/v1/evaluations/system/upload-sessions",
        headers={"X-CSRF-Token": csrf},
        json=_session_request(scenario),
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "evaluation_operation_denied"


@pytest.mark.parametrize("actor", ["student", "guardian"])
@pytest.mark.parametrize("scope_kind", ["enrollment", "assigned_class"])
def test_family_report_capability_excludes_center_denied_publication(
    db, db_client, report_api_scenario, platform_users, actor, scope_kind,
):
    scenario = report_api_scenario.report
    report_id, submitted = _create_pending_report(
        db_client, report_api_scenario, starts_on=NOW.date(), body="family capability regression",
    )
    csrf = report_api_scenario.login(db_client, "admin")
    approved = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        json={"expected_lock_version": submitted["lock_version"], "decision": "approved", "reason": None},
    )
    assert approved.status_code == 200, approved.text
    report_api_scenario.login(db_client, actor)
    baseline = db_client.get("/api/v1/evaluations/reports/context")
    assert baseline.status_code == 200 and baseline.json()["family_available"] is True
    _deny(
        db, actor_id=platform_users["accounts"]["admin"].id, account_id=scenario.accounts[actor].id,
        scope_kind=scope_kind,
        scope_id=scenario.enrollment.id if scope_kind == "enrollment" else scenario.teaching_class.id,
        valid_from=NOW,
    )
    before = _report_write_counts(db)
    context = db_client.get("/api/v1/evaluations/reports/context")
    assert context.status_code == 200, context.text
    assert context.json()["family_available"] is False
    published = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports",
    )
    assert published.status_code == 200 and published.json()["total"] == 0, published.text
    assert _report_write_counts(db) == before


def test_report_reviewer_cannot_install_denies_without_permission_management(db, report_api_scenario):
    scenario = report_api_scenario.report
    before = db.scalar(select(func.count()).select_from(UserPermissionOverride))
    with pytest.raises(PermissionDelegationDenied):
        _deny(
            db,
            actor_id=scenario.accounts["admin"].id,
            account_id=scenario.accounts["primary"].id,
            scope_kind="enrollment",
            scope_id=scenario.enrollment.id,
            valid_from=NOW,
        )
    assert db.scalar(select(func.count()).select_from(UserPermissionOverride)) == before


@pytest.mark.parametrize("variant", ["original", "preview"])
def test_center_project_deny_blocks_original_and_preview_media(
    db, db_client, platform_users, system_evaluation_api_scenario, variant,
):
    scenario = system_evaluation_api_scenario
    # Match the foundation media API fixture: bytes are seeded through the
    # real private store, then valid ORM rows are committed. The HTTP fixture
    # owns an external DB transaction, which task finalization correctly rejects.
    settings = db_client.app.state.settings
    root, quarantine = settings.evaluation_media_paths()
    store = PrivateMediaStore(
        root=root, quarantine_root=quarantine, max_bytes=20 * 1024 * 1024,
        max_pixels=50_000_000, preview_max_edge=1600,
    )
    try:
        validated = store.stage(io.BytesIO(_png_bytes()), declared_filename="fixture.png")
        blob_id = uuid4()
        files = store.persist(validated, blob_id=blob_id)
        store.finalize(files)
        store.release(files)
    finally:
        store.close()
    blob = EvaluationMediaBlob(
        id=blob_id, storage_key=files.storage_key, preview_storage_key=files.preview_storage_key,
        sha256=validated.sha256, mime_type=validated.mime_type, canonical_extension=validated.extension,
        size_bytes=validated.byte_size, width_pixels=validated.width, height_pixels=validated.height,
        storage_status="available", validation_status="validated", retention_status="retained",
    )
    db.add(blob)
    db.flush()
    asset = EvaluationMediaAsset(
        blob_id=blob.id, uploaded_by_user_id=scenario.account_id,
        project_id=scenario.project_id, student_person_id=scenario.student_person_id,
        enrollment_id=scenario.enrollment_id, class_id=scenario.class_id,
        matched_at=datetime.now(UTC), matched_by_user_id=scenario.account_id,
        match_reason="Media resource authorization regression fixture",
    )
    db.add(asset)
    db.commit()
    db_client.cookies.clear()
    _login(db_client, platform_users, "admin")
    path = f"/api/v1/evaluations/media-assets/{asset.id}/{variant}"
    baseline = db_client.get(path)
    assert baseline.status_code == 200, baseline.text
    account_id = platform_users["accounts"]["admin"].id
    _deny(db, actor_id=account_id, account_id=account_id, scope_kind="project", scope_id=scenario.project_id)
    request_id = f"center-media-{uuid4()}"
    denied = db_client.get(path, headers={"X-Request-ID": request_id})
    assert denied.status_code == 404, denied.text
    audits = list(db.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id)))
    if variant == "original":
        assert len(audits) == 1
        assert audits[0].outcome == "denied"
        assert audits[0].event_metadata == {"media_id": str(asset.id)}
    else:
        assert audits == []
