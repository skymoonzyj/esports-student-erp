from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, false, func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.housing import reporting_router as reporting_router_module
from app.housing.dependencies import get_housing_reporting_db
from app.housing.errors import (
    HousingAuditFailure,
    HousingExportIncomplete,
    HousingPersistenceFailure,
    HousingReportScopeDenied,
    HousingResourceNotFound,
)
from app.housing.models import HousingBuilding, HousingFloor
from app.housing.reporting_models import HousingReportBatch, HousingReportBatchScope
from app.housing.reporting_router import get_housing_reporting_service
from app.housing.reporting_service import (
    GenerateReportCommand,
    HousingReportingService,
    MarkSubmittedCommand,
    ReportBatchActions,
    ReportBatchDetail,
    ReportBatchPage,
    ReportBatchScopeSummary,
    ReportBatchStatusCounts,
    ReportBuildingOption,
    ReportCohortOption,
    ReportDownload,
    ReportFilterOptions,
    ReportFloorOption,
    VoidReportCommand,
)
from app.housing.reporting_types import ProjectionIssue
from app.housing.seed_inventory import FLOOR_SEEDS
from app.main import create_app
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.security.encryption import EnvelopeCipher

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
START = datetime(2026, 8, 6, tzinfo=UTC)
END = datetime(2026, 8, 7, tzinfo=UTC)
BUILDING_ID = FLOOR_SEEDS[0].building_id
FLOOR_ID = FLOOR_SEEDS[0].id


def _sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _detail(batch_id: UUID | None = None) -> ReportBatchDetail:
    resolved_id = batch_id or uuid4()
    return ReportBatchDetail(
        id=resolved_id,
        batch_number="HR-20260807-000001",
        status="generated",
        version=1,
        range_start=START,
        range_end=END,
        cohort_filter=(),
        person_type_filter=("student",),
        purpose="派出所人工报送",
        resident_count=1,
        stay_row_count=1,
        guardian_row_count=0,
        scopes=(
            ReportBatchScopeSummary(
                building_id=BUILDING_ID,
                building_name="4幢",
                floor_id=FLOOR_ID,
                floor_name="二楼",
            ),
        ),
        created_at=NOW,
        created_by_user_id=UUID("71000000-0000-4000-8000-000000000001"),
        created_by_name="管理员",
        submitted_at=None,
        submitted_by_user_id=None,
        submitted_by_name=None,
        voided_at=None,
        voided_by_user_id=None,
        voided_by_name=None,
        corrects_batch_id=None,
        failure_code=None,
        allowed_actions=ReportBatchActions(
            download=True,
            mark_submitted=True,
            void=True,
            correct=False,
        ),
        submission_note=None,
        void_reason=None,
        failure_summary=None,
        correction_chain=(),
    )


class FakeReportingService:
    def __init__(self) -> None:
        self.detail = _detail()
        self.calls: list[tuple[str, object]] = []
        self.missing_ids: set[UUID] = set()
        self.generate_error: Exception | None = None
        self.get_error: Exception | None = None
        self.mark_error: Exception | None = None
        self.download_error: Exception | None = None
        self.download_filename = "housing-report-HR-20260807-000001.xlsx"

    def generate(self, command: GenerateReportCommand):
        self.calls.append(("generate", command))
        if self.generate_error is not None:
            raise self.generate_error
        return self.detail

    def list(self, query):
        self.calls.append(("list", query))
        return ReportBatchPage(
            items=(self.detail,),
            total=1,
            page=query.page,
            page_size=query.page_size,
            status_counts=ReportBatchStatusCounts(
                total=1,
                generated=1,
                marked_submitted=0,
                voided=0,
                failed=0,
            ),
            access_revision="a" * 64,
            filter_options=ReportFilterOptions(
                buildings=(ReportBuildingOption(id=BUILDING_ID, name="4幢"),),
                floors=(
                    ReportFloorOption(
                        id=FLOOR_ID,
                        building_id=BUILDING_ID,
                        name="二楼",
                    ),
                ),
                cohorts=(
                    ReportCohortOption(
                        id=UUID("40000000-0000-4000-8000-000000000001"),
                        name="英雄联盟一期",
                    ),
                ),
            ),
        )

    def get(self, batch_id: UUID):
        self.calls.append(("get", batch_id))
        if self.get_error is not None:
            raise self.get_error
        if batch_id in self.missing_ids:
            raise HousingResourceNotFound("private")
        return self.detail

    def download(self, batch_id: UUID, format: str):
        self.calls.append(("download", (batch_id, format)))
        if self.download_error is not None:
            raise self.download_error
        return ReportDownload(
            content=b"private-report-bytes",
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if format == "xlsx"
                else "application/zip"
            ),
            filename=(
                self.download_filename
                if format == "xlsx"
                else "housing-report-HR-20260807-000001.zip"
            ),
        )

    def mark_submitted(self, batch_id: UUID, command: MarkSubmittedCommand):
        self.calls.append(("mark_submitted", (batch_id, command)))
        if self.mark_error is not None:
            raise self.mark_error
        return self.detail

    def void(self, batch_id: UUID, command: VoidReportCommand):
        self.calls.append(("void", (batch_id, command)))
        return self.detail


@pytest.fixture
def fake_reporting_service(db_client: TestClient):
    service = FakeReportingService()
    db_client.app.dependency_overrides[get_housing_reporting_service] = lambda: service
    try:
        yield service
    finally:
        db_client.app.dependency_overrides.pop(get_housing_reporting_service, None)


def test_reporting_db_is_distinct_and_uses_request_session_engine(
    postgres_engine,
) -> None:
    with Session(postgres_engine) as request_db:
        dependency = get_housing_reporting_db(request_db)
        reporting_db = next(dependency)
        try:
            assert reporting_db is not request_db
            assert reporting_db.get_bind() is postgres_engine
        finally:
            dependency.close()


def test_reporting_db_uses_engine_behind_request_session_connection(
    postgres_engine,
) -> None:
    with postgres_engine.connect() as connection, Session(connection) as request_db:
        dependency = get_housing_reporting_db(request_db)
        reporting_db = next(dependency)
        try:
            assert reporting_db is not request_db
            assert reporting_db.get_bind() is connection.engine
        finally:
            dependency.close()


def test_reporting_route_releases_injected_session_before_service_dependency(
    test_settings,
    postgres_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    injected_engine = create_engine(
        postgres_engine.url,
        pool_size=1,
        max_overflow=0,
    )
    request_sessions: list[Session] = []
    observations: dict[str, object] = {}
    actor_user_id = uuid4()

    class CountingAccount:
        id_reads = 0

        @property
        def id(self) -> UUID:
            self.id_reads += 1
            return actor_user_id

    account = CountingAccount()
    auth = cast(AuthContext, SimpleNamespace(account=account))

    class AllowReportingAccess:
        def __init__(
            self,
            db: Session,
            resolved_actor_user_id: UUID,
            occurred_at: datetime,
        ) -> None:
            del db, occurred_at
            observations["access_actor_user_id"] = resolved_actor_user_id

        def can(self, permission: str, scope_tokens: set[str]) -> bool:
            del permission, scope_tokens
            return True

        def report_floor_criteria(self):
            return false()

    def override_db():
        with Session(
            injected_engine,
            autoflush=False,
            expire_on_commit=False,
        ) as request_db:
            request_sessions.append(request_db)
            yield request_db

    def recording_reporting_service(
        reporting_db: Session,
        cipher: EnvelopeCipher,
        resolved_actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> FakeReportingService:
        del cipher, occurred_at, request_id
        request_db = request_sessions[-1]
        observations.update(
            {
                "actor_user_id": resolved_actor_user_id,
                "request_transaction_active": request_db.in_transaction(),
                "request_pool_checked_out": injected_engine.pool.checkedout(),
                "reporting_is_request": reporting_db is request_db,
                "reporting_bind": reporting_db.get_bind(),
                "request_bind": request_db.get_bind(),
            }
        )
        return FakeReportingService()

    monkeypatch.setattr(
        reporting_router_module,
        "HousingAccessService",
        AllowReportingAccess,
    )
    monkeypatch.setattr(
        reporting_router_module,
        "HousingReportingService",
        recording_reporting_service,
    )
    app = create_app(test_settings)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_standard_account] = lambda: auth
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/housing/report-batches")
    finally:
        injected_engine.dispose()

    assert response.status_code == 200
    assert observations == {
        "access_actor_user_id": actor_user_id,
        "actor_user_id": actor_user_id,
        "request_transaction_active": False,
        "request_pool_checked_out": 0,
        "reporting_is_request": False,
        "reporting_bind": injected_engine,
        "request_bind": injected_engine,
    }
    assert account.id_reads == 1


def test_reporting_list_requires_sensitive_capability_and_is_private(
    db_client: TestClient,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    unauthenticated = db_client.get("/api/v1/housing/report-batches")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "no-store, private"

    _sign_in(db_client, platform_users, "self_student")
    denied = db_client.get("/api/v1/housing/report-batches")
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    assert fake_reporting_service.calls == []

    _sign_in(db_client, platform_users, "admin")
    allowed = db_client.get("/api/v1/housing/report-batches")
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["items"][0]["batch_number"] == "HR-20260807-000001"
    assert payload["items"][0]["created_by_name"] == "管理员"
    assert payload["items"][0]["scopes"] == [
        {
            "building_id": str(BUILDING_ID),
            "building_name": "4幢",
            "floor_id": str(FLOOR_ID),
            "floor_name": "二楼",
        }
    ]
    assert payload["access_revision"] == "a" * 64
    assert payload["filter_options"]["buildings"] == [
        {"id": str(BUILDING_ID), "name": "4幢"}
    ]
    assert "snapshot_ciphertext" not in allowed.text
    assert allowed.headers["cache-control"] == "no-store, private"


def test_reporting_download_uses_private_attachment_headers(
    db_client: TestClient,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    _sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}/download",
        params={"format": "xlsx"},
    )

    assert response.status_code == 200
    assert response.content == b"private-report-bytes"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == (
        'attachment; filename="housing-report-HR-20260807-000001.xlsx"'
    )


def test_reporting_download_rejects_non_server_filename(
    db_client: TestClient,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    _sign_in(db_client, platform_users, "admin")
    fake_reporting_service.download_filename = "student-name\r\nX-Evil: yes.xlsx"

    response = db_client.get(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}/download",
        params={"format": "xlsx"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "housing_report_render_failed"
    assert "X-Evil" not in str(response.headers)


def test_scoped_reporting_permission_allows_floor_and_explicit_deny_wins(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    coach_id = platform_users["accounts"]["coach"].id
    permission_id = PERMISSIONS["housing.reports.export_sensitive"].id
    db.add(
        UserPermissionOverride(
            user_id=coach_id,
            permission_id=permission_id,
            effect="allow",
            scope_kind="floor",
            scope_id=FLOOR_ID,
        )
    )
    db.commit()
    _sign_in(db_client, platform_users, "coach")

    allowed = db_client.get("/api/v1/housing/report-batches")
    assert allowed.status_code == 200

    db.add(
        UserPermissionOverride(
            user_id=coach_id,
            permission_id=permission_id,
            effect="deny",
            scope_kind="floor",
            scope_id=FLOOR_ID,
        )
    )
    db.commit()
    denied = db_client.get("/api/v1/housing/report-batches")

    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    assert [action for action, _ in fake_reporting_service.calls].count("list") == 1
    denied_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_user_id == coach_id,
            AuditEvent.action == "housing.report_batch.list",
            AuditEvent.outcome == "denied",
        )
    )
    assert denied_audit is not None
    assert denied_audit.event_metadata == {"error_code": "permission_denied"}


def test_scoped_reporting_permission_can_read_inactive_historical_floor(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    coach_id = platform_users["accounts"]["coach"].id
    db.add(
        UserPermissionOverride(
            user_id=coach_id,
            permission_id=PERMISSIONS["housing.reports.export_sensitive"].id,
            effect="allow",
            scope_kind="floor",
            scope_id=FLOOR_ID,
        )
    )
    floor = db.get(HousingFloor, FLOOR_ID)
    building = db.get(HousingBuilding, BUILDING_ID)
    assert floor is not None and building is not None
    floor.is_active = False
    building.is_active = False
    db.commit()
    _sign_in(db_client, platform_users, "coach")

    response = db_client.get("/api/v1/housing/report-batches")

    assert response.status_code == 200
    assert [action for action, _ in fake_reporting_service.calls] == ["list"]


def test_reporting_missing_batch_is_private_404_without_error_text_leak(
    db_client: TestClient,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    _sign_in(db_client, platform_users, "admin")
    missing_id = uuid4()
    fake_reporting_service.missing_ids.add(missing_id)

    response = db_client.get(f"/api/v1/housing/report-batches/{missing_id}")

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    assert "private" not in response.text
    assert response.headers["cache-control"] == "no-store, private"


def test_scoped_hidden_and_missing_batches_have_identical_private_404(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    coach_id = platform_users["accounts"]["coach"].id
    db.add(
        UserPermissionOverride(
            user_id=coach_id,
            permission_id=PERMISSIONS["housing.reports.export_sensitive"].id,
            effect="allow",
            scope_kind="floor",
            scope_id=FLOOR_ID,
        )
    )
    db.commit()
    _sign_in(db_client, platform_users, "coach")
    hidden_id = uuid4()
    missing_id = uuid4()
    fake_reporting_service.missing_ids.update({hidden_id, missing_id})

    hidden = db_client.get(f"/api/v1/housing/report-batches/{hidden_id}")
    missing = db_client.get(f"/api/v1/housing/report-batches/{missing_id}")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["code"] == missing.json()["code"]
    assert hidden.json()["detail"] == missing.json()["detail"]
    assert hidden.headers["cache-control"] == "no-store, private"
    assert missing.headers["cache-control"] == "no-store, private"


def test_real_reporting_service_keeps_hidden_and_missing_batches_private_without_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    coach_id = platform_users["accounts"]["coach"].id
    hidden_floor = FLOOR_SEEDS[1]
    db.add(
        UserPermissionOverride(
            user_id=coach_id,
            permission_id=PERMISSIONS["housing.reports.export_sensitive"].id,
            effect="allow",
            scope_kind="floor",
            scope_id=FLOOR_ID,
        )
    )
    ciphertext = "opaque-report-snapshot"
    hidden_batch = HousingReportBatch(
        id=uuid4(),
        status="generated",
        version=1,
        range_start=START,
        range_end=END,
        cohort_filter=[],
        person_type_filter=[],
        purpose="派出所人工报送",
        projection_schema_version=1,
        snapshot_ciphertext=ciphertext,
        snapshot_key_version=1,
        snapshot_digest=hashlib.sha256(ciphertext.encode()).hexdigest(),
        resident_count=1,
        stay_row_count=1,
        guardian_row_count=0,
        created_at=NOW,
        created_by_user_id=coach_id,
        failure_code=None,
        failure_summary=None,
    )
    db.add(hidden_batch)
    db.flush()
    db.add(
        HousingReportBatchScope(
            batch_id=hidden_batch.id,
            building_id=hidden_floor.building_id,
            floor_id=hidden_floor.id,
        )
    )
    db.commit()
    _sign_in(db_client, platform_users, "coach")

    def real_reporting_service(request: Request) -> HousingReportingService:
        return HousingReportingService(
            db,
            EnvelopeCipher(b"q" * 32),
            coach_id,
            NOW,
            str(request.state.request_id),
        )

    db_client.app.dependency_overrides[
        get_housing_reporting_service
    ] = real_reporting_service
    before = db.scalar(select(func.count(HousingReportBatch.id)))
    try:
        hidden = db_client.get(
            f"/api/v1/housing/report-batches/{hidden_batch.id}",
            headers={"X-Request-ID": "real-hidden-report"},
        )
        missing = db_client.get(
            f"/api/v1/housing/report-batches/{uuid4()}",
            headers={"X-Request-ID": "real-missing-report"},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_housing_reporting_service, None)

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["code"] == missing.json()["code"] == "housing_resource_not_found"
    assert hidden.json()["detail"] == missing.json()["detail"]
    assert db.scalar(select(func.count(HousingReportBatch.id))) == before


def test_reporting_audit_failure_is_not_retried_by_route_boundary(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-report-audit-failed-no-retry"
    fake_reporting_service.get_error = HousingAuditFailure("internal secret")

    response = db_client.get(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 500
    assert response.json()["code"] == "housing_audit_failed"
    assert "internal secret" not in response.text
    assert db.scalar(
        select(AuditEvent.id).where(AuditEvent.request_id == request_id)
    ) is None


def test_reporting_persistence_failure_gets_safe_route_session_audit(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    actor_id = platform_users["accounts"]["admin"].id
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-report-persistence-failed"
    fake_reporting_service.get_error = HousingPersistenceFailure("database secret")

    response = db_client.get(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "housing_persistence_failure"
    assert "database secret" not in response.text
    audit = db.scalar(
        select(AuditEvent).where(AuditEvent.request_id == request_id)
    )
    assert audit is not None
    assert (audit.actor_user_id, audit.outcome, audit.event_metadata) == (
        actor_id,
        "failed",
        {"error_code": "housing_persistence_failure"},
    )


def test_download_persistence_failure_uses_format_specific_audit_action(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-report-download-persistence-failed"
    fake_reporting_service.download_error = HousingPersistenceFailure("database secret")

    response = db_client.get(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}/download",
        headers={"X-Request-ID": request_id},
        params={"format": "xlsx"},
    )

    assert response.status_code == 503
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert audit.action == "housing.report_batch.download.xlsx"


def test_generation_scope_denial_uses_route_session_audit_and_writes_no_batch(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = "housing-report-scope-denied"
    fake_reporting_service.generate_error = HousingReportScopeDenied("private scope")

    response = db_client.post(
        "/api/v1/housing/report-batches",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_id},
        json={
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
            "scope_mode": "authorized",
            "purpose": "派出所人工报送",
        },
    )

    assert response.status_code == 403
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert (audit.action, audit.outcome, audit.event_metadata) == (
        "housing.report_batch.generate",
        "denied",
        {"error_code": "housing_report_scope_denied"},
    )


def test_reporting_state_value_error_is_audited_as_validation_failure(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = "housing-report-future-submission"
    fake_reporting_service.mark_error = ValueError("future secret")

    response = db_client.post(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}/mark-submitted",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_id},
        json={"submitted_at": START.isoformat(), "version": 1},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "future secret" not in response.text
    audit = db.scalar(
        select(AuditEvent).where(AuditEvent.request_id == request_id)
    )
    assert audit is not None
    assert (audit.action, audit.outcome, audit.event_metadata) == (
        "housing.report_batch.mark_submitted",
        "failed",
        {"error_code": "validation_error"},
    )


def test_reporting_validation_rejects_extra_fields_before_service_call(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = "housing-report-validation"

    response = db_client.post(
        "/api/v1/housing/report-batches",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_id},
        json={
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
            "scope_mode": "authorized",
            "purpose": "派出所人工报送",
            "snapshot_ciphertext": "must-not-be-accepted",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.headers["cache-control"] == "no-store, private"
    assert fake_reporting_service.calls == []
    audit = db.scalar(
        select(AuditEvent).where(AuditEvent.request_id == request_id)
    )
    assert audit is not None
    assert (audit.action, audit.outcome, audit.event_metadata) == (
        "housing.report_batch.generate",
        "failed",
        {"error_code": "validation_error"},
    )


def test_correction_validation_and_persistence_failures_use_correction_audit_action(
    db_client: TestClient,
    db: Session,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    correction_id = uuid4()
    base_payload = {
        "range_start": START.isoformat(),
        "range_end": END.isoformat(),
        "scope_mode": "authorized",
        "purpose": "派出所人工报送",
        "corrects_batch_id": str(correction_id),
    }

    invalid = db_client.post(
        "/api/v1/housing/report-batches",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "correction-validation"},
        json={**base_payload, "unexpected": "value"},
    )
    fake_reporting_service.generate_error = HousingPersistenceFailure("database secret")
    persistence = db_client.post(
        "/api/v1/housing/report-batches",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "correction-persistence"},
        json=base_payload,
    )

    assert invalid.status_code == 422
    assert persistence.status_code == 503
    audits = {
        event.request_id: event
        for event in db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id.in_(
                    ("correction-validation", "correction-persistence")
                )
            )
        )
    }
    assert set(audits) == {"correction-validation", "correction-persistence"}
    assert all(
        event.action == "housing.report_batch.correct" for event in audits.values()
    )


def test_reporting_routes_translate_filters_and_state_commands(
    db_client: TestClient,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    correction_id = uuid4()
    actor_id = uuid4()

    generated = db_client.post(
        "/api/v1/housing/report-batches",
        headers={"X-CSRF-Token": csrf},
        json={
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
            "scope_mode": "floors",
            "floor_ids": [str(FLOOR_ID)],
            "cohort_ids": ["40000000-0000-4000-8000-000000000001"],
            "person_types": ["student"],
            "purpose": "派出所人工报送",
            "corrects_batch_id": str(correction_id),
        },
    )
    listed = db_client.get(
        "/api/v1/housing/report-batches",
        params=[
            ("status", "generated"),
            ("status", "voided"),
            ("building_id", str(BUILDING_ID)),
            ("floor_id", str(FLOOR_ID)),
            ("created_by_user_id", str(actor_id)),
            ("page", "2"),
            ("page_size", "10"),
        ],
    )
    submitted = db_client.post(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}/mark-submitted",
        headers={"X-CSRF-Token": csrf},
        json={"submitted_at": START.isoformat(), "note": "已人工报送", "version": 1},
    )
    voided = db_client.post(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}/void",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "名单有误", "version": 1},
    )
    csv_download = db_client.get(
        f"/api/v1/housing/report-batches/{fake_reporting_service.detail.id}/download",
        params={"format": "csv"},
    )

    assert generated.status_code == 200
    assert listed.status_code == 200
    assert submitted.status_code == 200
    assert voided.status_code == 200
    assert csv_download.status_code == 200
    assert csv_download.headers["content-type"] == "application/zip"
    assert csv_download.headers["content-disposition"].endswith('.zip"')

    generate_command = next(
        value for action, value in fake_reporting_service.calls if action == "generate"
    )
    assert isinstance(generate_command, GenerateReportCommand)
    assert generate_command.floor_ids == (FLOOR_ID,)
    assert generate_command.corrects_batch_id == correction_id
    list_query = next(
        value for action, value in fake_reporting_service.calls if action == "list"
    )
    assert list_query.statuses == ("generated", "voided")
    assert list_query.building_ids == (BUILDING_ID,)
    assert list_query.floor_ids == (FLOOR_ID,)
    assert list_query.created_by_user_id == actor_id
    assert (list_query.page, list_query.page_size) == (2, 10)


def test_incomplete_generation_returns_safe_issue_list_and_failed_batch_id(
    db_client: TestClient,
    platform_users,
    fake_reporting_service: FakeReportingService,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    batch_id = uuid4()
    person_id = uuid4()
    stay_id = uuid4()
    fake_reporting_service.generate_error = HousingExportIncomplete(
        batch_id,
        {"issue_count": 1, "field_counts": {"phone": 1}},
        (
            ProjectionIssue(
                person_id=person_id,
                person_name="待补资料学员",
                masked_phone="138****0001",
                stay_id=stay_id,
                missing_fields=("phone",),
            ),
        ),
    )

    response = db_client.post(
        "/api/v1/housing/report-batches",
        headers={"X-CSRF-Token": csrf},
        json={
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
            "scope_mode": "authorized",
            "purpose": "派出所人工报送",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "housing_export_incomplete"
    assert response.json()["errors"] == [
        {
            "field": f"report_issue.{person_id}",
            "message": (
                f"待补资料学员（138****0001，住宿 {stay_id}）缺少：phone"
            ),
            "batch_id": str(batch_id),
            "person_id": str(person_id),
            "person_name": "待补资料学员",
            "masked_phone": "138****0001",
            "stay_id": str(stay_id),
            "missing_fields": "phone",
        }
    ]
    assert "13800138001" not in response.text
    assert response.headers["cache-control"] == "no-store, private"
