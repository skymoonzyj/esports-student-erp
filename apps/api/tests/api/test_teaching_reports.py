from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier
from app.evaluations import report_router
from app.evaluations.report_api_schemas import TeachingReportVersionReviewResponse
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportReview,
    StudentReportVersion,
    StudentReportWindowGrant,
)
from app.evaluations.report_schemas import (
    TeachingReportDraftSave,
    TeachingReportDraftStartInput,
    TeachingReportReopenInput,
    TeachingReportReviewInput,
    TeachingReportSubmitInput,
)
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.identity.models import GuardianRelationship, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import GameProject
from tests.services.test_teaching_report_access import (
    NOW,
    ReportAccessScenario,
    create_report_access_scenario,
)


@dataclass(frozen=True)
class ReportApiScenario:
    report: ReportAccessScenario
    credentials: dict[str, tuple[str, str]]

    def login(self, client: TestClient, key: str) -> str:
        client.cookies.clear()
        phone, password = self.credentials[key]
        response = client.post(
            "/api/v1/auth/login",
            json={"identifier": phone, "password": password},
        )
        assert response.status_code == 200
        return response.json()["csrf_token"]


def _create_pending_report(
    client: TestClient,
    scenario: ReportApiScenario,
    *,
    starts_on: date,
    body: str,
) -> tuple[UUID, dict[str, object]]:
    csrf = scenario.login(client, "primary")
    report_id = uuid4()
    draft = client.put(
        f"/api/v1/evaluations/reports/{report_id}/draft",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"api-draft-{uuid4()}",
        },
        json={
            "enrollment_id": str(scenario.report.enrollment.id),
            "report_kind": "daily",
            "period_start_date": str(starts_on),
            "body": body,
            "dimension_values": {},
            "expected_draft_revision": 0,
        },
    )
    assert draft.status_code == 200
    submitted = client.post(
        f"/api/v1/evaluations/reports/{report_id}/submit",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"api-submit-{uuid4()}",
        },
        json={
            "expected_lock_version": draft.json()["lock_version"],
            "expected_draft_revision": draft.json()["draft_revision"],
        },
    )
    assert submitted.status_code == 201
    return report_id, submitted.json()


def _report_write_counts(db: Session) -> tuple[int, ...]:
    return (
        db.scalar(select(func.count(StudentReport.id))) or 0,
        db.scalar(select(func.count(StudentReportDraft.report_id))) or 0,
        db.scalar(select(func.count(StudentReportVersion.id))) or 0,
        db.scalar(select(func.count(StudentReportReview.id))) or 0,
        db.scalar(select(func.count(StudentReportWindowGrant.id))) or 0,
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))) or 0,
        db.scalar(select(func.count(AuditEvent.id))) or 0,
    )


@pytest.fixture
def report_api_scenario(
    db: Session,
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> ReportApiScenario:
    monkeypatch.setattr("app.evaluations.router._now", lambda: NOW)
    monkeypatch.setattr("app.evaluations.report_router._now", lambda: NOW)
    scenario = create_report_access_scenario(db, add_primary_identifiers=False)
    lookup_key = base64.urlsafe_b64decode(
        db_client.app.state.settings.field_lookup_key.get_secret_value()
    )
    cipher_key = base64.urlsafe_b64decode(
        db_client.app.state.settings.field_encryption_master_key.get_secret_value()
    )
    lookup = LookupDigest(lookup_key)
    cipher = EnvelopeCipher(cipher_key)
    passwords = PasswordService()
    credentials: dict[str, tuple[str, str]] = {}
    for index, (key, account) in enumerate(scenario.accounts.items(), start=20):
        phone = f"1390000{index:04d}"
        password = f"Rpt{key[:6]}26Aa"
        person = scenario.people[key]
        person.phone_ciphertext = f"report-api-phone:{phone}"
        person.phone_digest = lookup.digest(phone)
        email = f"report-api-{key}-{uuid4().hex[:8]}@example.com"
        person.email = None
        person.email_ciphertext = cipher.encrypt(email, field_name="email")
        person.email_digest = lookup.digest_domain("person-email", email)
        account.email_normalized = None
        account.login_phone_digest = None
        account.password_hash = passwords.hash(password)
        account.force_password_change = False
        normalized_phone = normalize_login_identifier(phone)
        db.add(
            AccountLoginIdentifier(
                account_id=account.id,
                kind=normalized_phone.kind,
                identifier_digest=login_identifier_digest(lookup, normalized_phone),
                verified_at=NOW,
                verification_source="test_verified",
                is_primary=True,
            )
        )
        credentials[key] = (phone, password)
    db.commit()
    return ReportApiScenario(report=scenario, credentials=credentials)


def test_openapi_exposes_the_frozen_teaching_report_routes(
    db_client: TestClient,
) -> None:
    paths = db_client.app.openapi()["paths"]
    expected = {
        "/api/v1/evaluations/reports/context": {"get"},
        "/api/v1/evaluations/reports/targets": {"get"},
        "/api/v1/evaluations/reports/slots": {"get"},
        "/api/v1/evaluations/reports": {"get"},
        "/api/v1/evaluations/reports/{report_id}": {"get"},
        "/api/v1/evaluations/reports/{report_id}/versions": {"get"},
        "/api/v1/evaluations/reports/{report_id}/draft": {"put"},
        "/api/v1/evaluations/reports/{report_id}/submit": {"post"},
        "/api/v1/evaluations/reports/{report_id}/correction-drafts": {"post"},
        "/api/v1/evaluations/reports/{report_id}/revisions": {"post"},
        "/api/v1/evaluations/reports/review-queue": {"get"},
        "/api/v1/evaluations/reports/{report_id}/reviews": {"post"},
        "/api/v1/evaluations/reports/{report_id}/reopen": {"post"},
        "/api/v1/evaluations/students/{student_id}/published-reports": {"get"},
    }

    for path, methods in expected.items():
        assert path in paths
        assert set(paths[path]) == methods

    expected_pairs = {
        (method.upper(), path)
        for path, methods in expected.items()
        for method in methods
    }
    def iter_runtime_routes(routes: object, prefix: str = ""):
        for route in routes:  # type: ignore[union-attr]
            if isinstance(route, APIRoute):
                yield route, prefix
                continue
            nested_router = getattr(route, "original_router", None)
            if nested_router is None:
                continue
            nested_prefix = getattr(route.include_context, "prefix", "")
            yield from iter_runtime_routes(
                nested_router.routes,
                f"{prefix}{nested_prefix}",
            )

    runtime_pairs: list[tuple[str, str]] = []
    for route, prefix in iter_runtime_routes(db_client.app.routes):
        for method in route.methods:
            pair = (method.upper(), f"{prefix}{route.path}")
            if pair in expected_pairs:
                runtime_pairs.append(pair)
    assert set(runtime_pairs) == expected_pairs
    assert len(runtime_pairs) == len(expected_pairs)

    report_openapi_pairs = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/v1/evaluations/reports")
        or path == "/api/v1/evaluations/students/{student_id}/published-reports"
        for method in operations
    }
    assert report_openapi_pairs == expected_pairs

    operation_ids = [
        operation["operationId"]
        for operations in paths.values()
        for operation in operations.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))

    ordered_paths = [
        f"{prefix}{route.path}"
        for route, prefix in iter_runtime_routes(db_client.app.routes)
    ]
    dynamic_index = ordered_paths.index("/api/v1/evaluations/reports/{report_id}")
    for static_path in (
        "/api/v1/evaluations/reports/context",
        "/api/v1/evaluations/reports/targets",
        "/api/v1/evaluations/reports/slots",
        "/api/v1/evaluations/reports/review-queue",
    ):
        assert ordered_paths.index(static_path) < dynamic_index


def test_openapi_teaching_report_routes_have_typed_response_contracts(
    db_client: TestClient,
) -> None:
    paths = db_client.app.openapi()["paths"]
    expected = {
        (
            "/api/v1/evaluations/reports/targets",
            "get",
            "TeachingReportTargetPageResponse",
            "200",
        ),
        ("/api/v1/evaluations/reports", "get", "TeachingReportPageResponse", "200"),
        (
            "/api/v1/evaluations/reports/{report_id}/versions",
            "get",
            "TeachingReportVersionPageResponse",
            "200",
        ),
        (
            "/api/v1/evaluations/reports/review-queue",
            "get",
            "TeachingReportReviewQueuePageResponse",
            "200",
        ),
        (
            "/api/v1/evaluations/students/{student_id}/published-reports",
            "get",
            "TeachingReportPublishedPageResponse",
            "200",
        ),
        (
            "/api/v1/evaluations/reports/{report_id}/submit",
            "post",
            "TeachingReportSubmitResponse",
            "201",
        ),
        (
            "/api/v1/evaluations/reports/{report_id}/correction-drafts",
            "post",
            "TeachingReportDraftStartResponse",
            "201",
        ),
        (
            "/api/v1/evaluations/reports/{report_id}/revisions",
            "post",
            "TeachingReportDraftStartResponse",
            "201",
        ),
        (
            "/api/v1/evaluations/reports/{report_id}/reviews",
            "post",
            "TeachingReportReviewResponse",
            "200",
        ),
        (
            "/api/v1/evaluations/reports/{report_id}/reopen",
            "post",
            "TeachingReportReopenResponse",
            "201",
        ),
    }
    for path, method, schema_name, status in expected:
        schema = (
            paths[path][method]["responses"][status]["content"]["application/json"]
            ["schema"]
        )
        assert schema == {"$ref": f"#/components/schemas/{schema_name}"}


def test_openapi_workspace_reads_publish_bounds_and_problem_contracts(
    db_client: TestClient,
) -> None:
    paths = db_client.app.openapi()["paths"]
    reads = {
        ("/api/v1/evaluations/reports/targets", "get"): ("403", "422"),
        (
            "/api/v1/evaluations/reports/{report_id}/versions",
            "get",
        ): ("403", "404", "422"),
    }
    for (path, method), statuses in reads.items():
        operation = paths[path][method]
        for status in statuses:
            schema = operation["responses"][status]["content"]["application/json"][
                "schema"
            ]
            assert schema == {
                "$ref": "#/components/schemas/TeachingReportProblemResponse"
            }

    target_parameters = {
        parameter["name"]: parameter["schema"]
        for parameter in paths["/api/v1/evaluations/reports/targets"]["get"][
            "parameters"
        ]
    }
    assert target_parameters["query"]["anyOf"][0]["maxLength"] == 80
    assert target_parameters["page"]["minimum"] == 1
    assert target_parameters["page_size"]["minimum"] == 1
    assert target_parameters["page_size"]["maximum"] == 100

    version_parameters = {
        parameter["name"]: parameter["schema"]
        for parameter in paths[
            "/api/v1/evaluations/reports/{report_id}/versions"
        ]["get"]["parameters"]
    }
    assert version_parameters["page"]["minimum"] == 1
    assert version_parameters["page_size"]["minimum"] == 1
    assert version_parameters["page_size"]["maximum"] == 100


@pytest.mark.parametrize(
    ("decision", "reason"),
    (
        ("approved", "approved reviews cannot expose a reason"),
        ("rejected", None),
        ("rejected", "   "),
    ),
)
def test_version_review_response_rejects_invalid_decision_reason_pairs(
    decision: str,
    reason: str | None,
) -> None:
    with pytest.raises(ValidationError):
        TeachingReportVersionReviewResponse(
            decision=decision,  # type: ignore[arg-type]
            reason=reason,
            reviewed_at=datetime(2026, 8, 22, 4, tzinfo=UTC),
        )


def test_author_targets_api_filters_facets_and_private_projection(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_api_scenario.login(db_client, "primary")
    response = db_client.get(
        "/api/v1/evaluations/reports/targets",
        params={
            "cohort_id": str(scenario.cohort.id),
            "project_id": str(scenario.teaching_class.project_id),
            "class_id": str(scenario.teaching_class.id),
            "student_id": str(scenario.people["student"].id),
            "query": scenario.people["student"].full_name,
            "page": 1,
            "page_size": 1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert set(payload) == {"items", "facets", "total", "page", "page_size"}
    assert set(payload["items"][0]) == {
        "enrollment_id",
        "student_person_id",
        "student_name",
        "student_number",
        "cohort_id",
        "cohort_name",
        "cohort_starts_at",
        "cohort_ends_at",
        "project_id",
        "project_name",
        "class_id",
        "class_name",
    }
    assert set(payload["facets"]) == {"cohorts", "projects", "classes"}
    assert payload["items"][0]["enrollment_id"] == str(scenario.enrollment.id)
    assert payload["facets"]["cohorts"][0]["id"] == str(scenario.cohort.id)
    serialized = response.text
    for forbidden in (
        "phone",
        "email",
        "identity",
        "payment",
        "guardian",
        "assignment_id",
        "permission_id",
        "audit",
        "game_id",
    ):
        assert forbidden not in serialized

    empty = db_client.get(
        "/api/v1/evaluations/reports/targets",
        params={"class_id": str(uuid4())},
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["total"] == 0


def test_author_targets_query_length_is_measured_after_trimming(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    report_api_scenario.login(db_client, "primary")

    accepted = db_client.get(
        "/api/v1/evaluations/reports/targets",
        params={"query": f" {'x' * 80} "},
    )
    rejected = db_client.get(
        "/api/v1/evaluations/reports/targets",
        params={"query": f" {'x' * 81} "},
    )

    assert accepted.status_code == 200
    assert accepted.json()["items"] == []
    assert rejected.status_code == 422


def test_report_versions_api_masks_missing_and_denied_targets_identically(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_id, _ = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="版本接口正文",
    )
    report_api_scenario.login(db_client, "primary")
    author = db_client.get(
        f"/api/v1/evaluations/reports/{report_id}/versions",
        params={"page": 1, "page_size": 20},
    )
    assert author.status_code == 200
    assert author.json()["items"][0]["body"] == "版本接口正文"

    scenario.allow_account("assistant")
    report_api_scenario.login(db_client, "assistant")
    denied = db_client.get(f"/api/v1/evaluations/reports/{report_id}/versions")
    missing = db_client.get(f"/api/v1/evaluations/reports/{uuid4()}/versions")
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()


def test_workspace_read_endpoints_validate_bounds_without_writes(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    report_id, _ = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="分页边界版本正文",
    )
    report_api_scenario.login(db_client, "primary")
    before = _report_write_counts(db)

    responses = [
        db_client.get(path, params=params)
        for path in (
            "/api/v1/evaluations/reports/targets",
            f"/api/v1/evaluations/reports/{report_id}/versions",
        )
        for params in (
            {"page": 0},
            {"page_size": 0},
            {"page_size": 101},
        )
    ]
    responses.append(
        db_client.get(
            "/api/v1/evaluations/reports/targets",
            params={"query": "x" * 81},
        )
    )

    assert all(response.status_code == 422 for response in responses)
    assert _report_write_counts(db) == before


def test_workspace_read_endpoints_without_capability_are_403_and_zero_write(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    report_api_scenario.login(db_client, "outsider")
    before = _report_write_counts(db)

    targets = db_client.get("/api/v1/evaluations/reports/targets")
    versions = db_client.get(
        f"/api/v1/evaluations/reports/{uuid4()}/versions"
    )

    assert targets.status_code == versions.status_code == 403
    assert targets.json()["code"] == "evaluation_operation_denied"
    assert versions.json()["code"] == "evaluation_operation_denied"
    assert _report_write_counts(db) == before


def test_report_versions_api_allows_reviewer_then_rechecks_revocation(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_id, _ = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="审核人可见的版本正文",
    )
    report_api_scenario.login(db_client, "admin")
    before_read = _report_write_counts(db)

    visible = db_client.get(
        f"/api/v1/evaluations/reports/{report_id}/versions"
    )

    assert visible.status_code == 200
    payload = visible.json()
    assert set(payload) == {"items", "total", "page", "page_size"}
    assert set(payload["items"][0]) == {
        "report_id",
        "version_id",
        "version",
        "source",
        "body",
        "dimension_values",
        "submitted_at",
        "is_current",
        "is_published",
        "review",
    }
    assert payload["items"][0]["review"] is None
    for forbidden in (
        "author_user_id",
        "author_person_id",
        "reviewed_by_user_id",
        "previous_version_id",
        "window_grant",
        "audit",
        "idempotency",
    ):
        assert forbidden not in visible.text
    assert _report_write_counts(db) == before_read

    permission_id = db.scalar(
        select(Permission.id).where(Permission.key == "student_reports.review")
    )
    assert permission_id is not None
    db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["admin"].id,
            permission_id=permission_id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=1),
            created_by_user_id=scenario.accounts["admin"].id,
            grant_reason="版本接口即时撤权测试",
        )
    )
    db.flush()
    before_revoked = _report_write_counts(db)

    revoked = db_client.get(
        f"/api/v1/evaluations/reports/{report_id}/versions"
    )
    missing = db_client.get(
        f"/api/v1/evaluations/reports/{uuid4()}/versions"
    )

    assert revoked.status_code == missing.status_code == 404
    assert revoked.json() == missing.json()
    assert revoked.json()["code"] == "evaluation_resource_not_found"
    assert _report_write_counts(db) == before_revoked


@pytest.mark.parametrize(
    "failure",
    (
        "deleted_student",
        "inactive_student",
        "cancelled_enrollment",
        "archived_cohort",
        "inactive_class",
        "inactive_project",
    ),
)
def test_workspace_read_endpoints_hide_ineligible_targets_identically(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
    failure: str,
) -> None:
    scenario = report_api_scenario.report
    report_id, _ = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="目标失效后不可枚举的版本正文",
    )
    if failure == "deleted_student":
        scenario.people["student"].deleted_at = NOW
    elif failure == "inactive_student":
        profile = db.get(StudentProfile, scenario.people["student"].id)
        assert profile is not None
        profile.status = "inactive"
    elif failure == "cancelled_enrollment":
        scenario.enrollment.status = "cancelled"
        scenario.enrollment.cancelled_at = NOW
    elif failure == "archived_cohort":
        scenario.cohort.status = "archived"
    elif failure == "inactive_class":
        scenario.teaching_class.is_active = False
    else:
        project = db.get(GameProject, scenario.teaching_class.project_id)
        assert project is not None
        project.is_active = False
    db.flush()
    report_api_scenario.login(db_client, "primary")
    report = db.get(StudentReport, report_id)
    assert report is not None
    before_state = (
        report.status,
        report.lock_version,
        report.current_version_id,
        report.published_version_id,
        report.current_window_grant_id,
    )
    before = _report_write_counts(db)

    targets = db_client.get("/api/v1/evaluations/reports/targets")
    denied = db_client.get(
        f"/api/v1/evaluations/reports/{report_id}/versions"
    )
    missing = db_client.get(
        f"/api/v1/evaluations/reports/{uuid4()}/versions"
    )

    assert targets.status_code == 200
    assert targets.json()["items"] == []
    assert targets.json()["facets"] == {
        "cohorts": [],
        "projects": [],
        "classes": [],
    }
    assert targets.json()["total"] == 0
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
    assert "目标失效后不可枚举的版本正文" not in denied.text
    db.refresh(report)
    assert (
        report.status,
        report.lock_version,
        report.current_version_id,
        report.published_version_id,
        report.current_window_grant_id,
    ) == before_state
    assert _report_write_counts(db) == before


def test_openapi_teaching_report_mutations_publish_headers_replays_and_problems(
    db_client: TestClient,
) -> None:
    paths = db_client.app.openapi()["paths"]
    mutations = {
        (
            "/api/v1/evaluations/reports/{report_id}/draft",
            "put",
        ): TeachingReportDraftSave,
        (
            "/api/v1/evaluations/reports/{report_id}/submit",
            "post",
        ): TeachingReportSubmitInput,
        (
            "/api/v1/evaluations/reports/{report_id}/correction-drafts",
            "post",
        ): TeachingReportDraftStartInput,
        (
            "/api/v1/evaluations/reports/{report_id}/revisions",
            "post",
        ): TeachingReportDraftStartInput,
        (
            "/api/v1/evaluations/reports/{report_id}/reviews",
            "post",
        ): TeachingReportReviewInput,
        (
            "/api/v1/evaluations/reports/{report_id}/reopen",
            "post",
        ): TeachingReportReopenInput,
    }
    for (path, method), model in mutations.items():
        operation = paths[path][method]
        parameters = {
            (parameter["in"], parameter["name"]): parameter
            for parameter in operation["parameters"]
        }
        for name in ("Idempotency-Key", "X-CSRF-Token"):
            assert parameters[("header", name)]["required"] is True
        body_schema = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        expected_schema = model.model_json_schema()
        # FastAPI's OpenAPI merger omits a nullable field's explicit default;
        # the request model still supplies the same runtime default.
        if model is TeachingReportReviewInput:
            expected_schema["properties"]["reason"].pop("default", None)
        assert body_schema == expected_schema
        for status in ("403", "404", "409", "422"):
            schema = operation["responses"][status]["content"]["application/json"][
                "schema"
            ]
            assert schema == {
                "$ref": "#/components/schemas/TeachingReportProblemResponse"
            }

    replay_responses = {
        (
            "/api/v1/evaluations/reports/{report_id}/submit",
            "TeachingReportSubmitResponse",
        ),
        (
            "/api/v1/evaluations/reports/{report_id}/correction-drafts",
            "TeachingReportDraftStartResponse",
        ),
        (
            "/api/v1/evaluations/reports/{report_id}/revisions",
            "TeachingReportDraftStartResponse",
        ),
        (
            "/api/v1/evaluations/reports/{report_id}/reopen",
            "TeachingReportReopenResponse",
        ),
    }
    for path, schema_name in replay_responses:
        schema = paths[path]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert schema == {"$ref": f"#/components/schemas/{schema_name}"}


def test_primary_author_context_and_slots_are_live_and_pagination_is_bounded(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    report_api_scenario.login(db_client, "primary")
    enrollment_id = report_api_scenario.report.enrollment.id

    context = db_client.get("/api/v1/evaluations/reports/context")
    evaluation_context = db_client.get("/api/v1/evaluations/context")
    slots = db_client.get(
        "/api/v1/evaluations/reports/slots",
        params={"enrollment_id": str(enrollment_id)},
    )
    invalid_pages = [
        db_client.get(
            "/api/v1/evaluations/reports",
            params={
                "enrollment_id": str(enrollment_id),
                **parameters,
            },
        )
        for parameters in (
            {"page": 0},
            {"page_size": 0},
            {"page_size": 101},
        )
    ]

    assert context.status_code == 200
    assert context.json() == {
        "author_available": True,
        "reviewer_available": False,
        "family_available": False,
    }
    assert evaluation_context.status_code == 200
    assert evaluation_context.json()["reports_available"] is True
    assert slots.status_code == 200
    payload = slots.json()
    assert len(payload["items"]) == 11
    assert payload["items"][0]["enrollment_id"] == str(enrollment_id)
    assert payload["items"][0]["report_id"] is None
    assert payload["items"][0]["allowed_actions"] == ["save_draft"]
    assert all(response.status_code == 422 for response in invalid_pages)


def test_report_context_only_marks_live_writable_slots(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_api_scenario.login(db_client, "primary")
    monkeypatch.setattr(report_router, "_now", lambda: NOW + timedelta(days=30))

    response = db_client.get("/api/v1/evaluations/reports/context")

    assert response.status_code == 200
    assert response.json() == {
        "author_available": False,
        "reviewer_available": False,
        "family_available": False,
    }


def test_client_chosen_report_id_draft_is_strict_idempotent_and_reauthorized(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    csrf = report_api_scenario.login(db_client, "primary")
    scenario = report_api_scenario.report
    report_id = uuid4()
    url = f"/api/v1/evaluations/reports/{report_id}/draft"
    body = {
        "enrollment_id": str(scenario.enrollment.id),
        "report_kind": "daily",
        "period_start_date": NOW.date().isoformat(),
        "body": "API 日报正文",
        "dimension_values": {},
        "expected_draft_revision": 0,
    }
    headers = {
        "X-CSRF-Token": csrf,
        "Idempotency-Key": f"report-api-draft-{uuid4()}",
    }
    before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    invalid = db_client.put(
        url,
        headers=headers,
        json={**body, "unexpected": True},
    )
    unsupported_dimensions = db_client.put(
        url,
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"report-api-draft-{uuid4()}",
        },
        json={**body, "dimension_values": {"unconfirmed_score": 10}},
    )

    assert invalid.status_code == 422
    assert unsupported_dimensions.status_code == 422
    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before

    created = db_client.put(url, headers=headers, json=body)
    replay = db_client.put(url, headers=headers, json=body)

    assert created.status_code == 200
    assert created.json()["report_id"] == str(report_id)
    assert created.json()["status"] == "draft"
    assert created.json()["lock_version"] == 1
    assert created.json()["draft_revision"] == 1
    assert created.json()["idempotent"] is False
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert db.get(StudentReportDraft, report_id) is not None
    assert db.scalar(select(func.count(StudentReport.id))) == before[0] + 1
    assert db.scalar(select(func.count(AuditEvent.id))) == before[1] + 1
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == before[2] + 1

    conflict_before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )
    conflict = db_client.put(
        url,
        headers=headers,
        json={**body, "body": "同一幂等键不得改写正文"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "evaluation_idempotency_conflict"
    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == conflict_before

    scenario.deny_account("primary")
    blocked_before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )
    blocked = db_client.put(
        url,
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"report-api-draft-{uuid4()}",
        },
        json={**body, "expected_draft_revision": 1, "body": "不应写入"},
    )

    assert blocked.status_code == 404
    assert blocked.json()["code"] == "evaluation_resource_not_found"
    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == blocked_before


def test_author_report_list_detail_filters_pages_and_masks_private_targets(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    csrf = report_api_scenario.login(db_client, "primary")
    report_id = uuid4()
    created = db_client.put(
        f"/api/v1/evaluations/reports/{report_id}/draft",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"api-draft-{uuid4()}",
        },
        json={
            "enrollment_id": str(scenario.enrollment.id),
            "report_kind": "daily",
            "period_start_date": NOW.date().isoformat(),
            "body": "用于作者列表和详情的草稿",
            "dimension_values": {},
            "expected_draft_revision": 0,
        },
    )
    assert created.status_code == 200

    page_one = db_client.get(
        "/api/v1/evaluations/reports",
        params={
            "enrollment_id": str(scenario.enrollment.id),
            "kind": "daily",
            "status": "draft",
            "page": 1,
            "page_size": 1,
        },
    )
    page_two = db_client.get(
        "/api/v1/evaluations/reports",
        params={
            "enrollment_id": str(scenario.enrollment.id),
            "kind": "daily",
            "status": "draft",
            "page": 2,
            "page_size": 1,
        },
    )
    detail = db_client.get(f"/api/v1/evaluations/reports/{report_id}")
    invalid_status = db_client.get(
        "/api/v1/evaluations/reports",
        params={
            "enrollment_id": str(scenario.enrollment.id),
            "status": "unknown",
        },
    )
    wrong_enrollment = db_client.get(
        "/api/v1/evaluations/reports",
        params={"enrollment_id": str(uuid4())},
    )

    assert page_one.status_code == 200
    assert page_one.json()["total"] == 1
    assert page_one.json()["page"] == 1
    assert page_one.json()["page_size"] == 1
    assert [item["report_id"] for item in page_one.json()["items"]] == [
        str(report_id)
    ]
    assert page_two.status_code == 200
    assert page_two.json()["total"] == 1
    assert page_two.json()["items"] == []
    assert detail.status_code == 200
    assert detail.json()["report_id"] == str(report_id)
    assert detail.json()["draft_body"] == "用于作者列表和详情的草稿"
    assert invalid_status.status_code == 422
    assert wrong_enrollment.status_code == 404

    scenario.allow_account("assistant")
    report_api_scenario.login(db_client, "assistant")
    denied = db_client.get(f"/api/v1/evaluations/reports/{report_id}")
    missing = db_client.get(f"/api/v1/evaluations/reports/{uuid4()}")
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()


def test_report_context_requires_account_level_evaluation_access(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    scenario.deny_evaluation_center("outsider")
    report_api_scenario.login(db_client, "outsider")

    response = db_client.get("/api/v1/evaluations/reports/context")

    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_access_denied"


def test_assigned_coach_is_not_misclassified_as_family_after_publication(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_id, submitted = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="仅学员与监护人可见的已发布日报",
    )
    admin_csrf = report_api_scenario.login(db_client, "admin")
    approved = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": f"api-review-{uuid4()}",
        },
        json={
            "expected_lock_version": submitted["lock_version"],
            "decision": "approved",
            "reason": None,
        },
    )
    assert approved.status_code == 200

    report_api_scenario.login(db_client, "assistant")
    before = _report_write_counts(db)
    context = db_client.get("/api/v1/evaluations/reports/context")
    published = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports"
    )

    assert context.status_code == 200
    assert context.json() == {
        "author_available": False,
        "reviewer_available": False,
        "family_available": False,
    }
    assert published.status_code == 403
    assert published.json()["code"] == "evaluation_operation_denied"
    assert _report_write_counts(db) == before


def test_guardian_role_does_not_turn_assigned_coach_scope_into_family_scope(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_id, submitted = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="不得通过教练班级范围读取的已发布日报",
    )
    admin_csrf = report_api_scenario.login(db_client, "admin")
    approved = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": f"api-review-{uuid4()}",
        },
        json={
            "expected_lock_version": submitted["lock_version"],
            "decision": "approved",
            "reason": None,
        },
    )
    assert approved.status_code == 200
    db.add(
        UserRole(
            user_id=scenario.accounts["assistant"].id,
            role_id=ROLES["guardian"].id,
        )
    )
    db.flush()

    report_api_scenario.login(db_client, "assistant")
    before = _report_write_counts(db)
    context = db_client.get("/api/v1/evaluations/reports/context")
    published = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports"
    )

    assert context.status_code == 200
    assert context.json()["family_available"] is False
    assert published.status_code == 404
    assert published.json()["code"] == "evaluation_resource_not_found"
    assert _report_write_counts(db) == before


def test_submit_review_publish_and_family_revocation_are_live(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_id = uuid4()
    primary_csrf = report_api_scenario.login(db_client, "primary")
    draft = db_client.put(
        f"/api/v1/evaluations/reports/{report_id}/draft",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": f"api-draft-{uuid4()}",
        },
        json={
            "enrollment_id": str(scenario.enrollment.id),
            "report_kind": "daily",
            "period_start_date": NOW.date().isoformat(),
            "body": "经 API 提交并发布的日报",
            "dimension_values": {},
            "expected_draft_revision": 0,
        },
    )
    assert draft.status_code == 200

    submit_key = f"api-submit-{uuid4()}"
    submit_body = {
        "expected_lock_version": draft.json()["lock_version"],
        "expected_draft_revision": draft.json()["draft_revision"],
    }
    submitted = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/submit",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": submit_key,
        },
        json=submit_body,
    )
    replayed_submit = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/submit",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": submit_key,
        },
        json=submit_body,
    )

    assert submitted.status_code == 201
    assert submitted.json()["status"] == "pending_review"
    assert submitted.json()["version"] == 1
    assert submitted.json()["idempotent"] is False
    assert replayed_submit.status_code == 200
    assert replayed_submit.json()["idempotent"] is True
    assert db.scalar(select(func.count(StudentReportVersion.id))) == 1

    admin_csrf = report_api_scenario.login(db_client, "admin")
    review_context = db_client.get("/api/v1/evaluations/reports/context")
    review_foundation_context = db_client.get("/api/v1/evaluations/context")
    queue = db_client.get("/api/v1/evaluations/reports/review-queue")
    invalid_review_pages = [
        db_client.get(
            "/api/v1/evaluations/reports/review-queue",
            params=parameters,
        )
        for parameters in (
            {"page": 0},
            {"page_size": 0},
            {"page_size": 101},
        )
    ]
    assert review_context.status_code == 200
    assert review_context.json()["reviewer_available"] is True
    assert review_foundation_context.status_code == 200
    assert review_foundation_context.json()["reports_available"] is True
    assert queue.status_code == 200
    assert queue.json()["total"] == 1
    assert queue.json()["items"][0]["report_id"] == str(report_id)
    assert queue.json()["items"][0]["body"] == "经 API 提交并发布的日报"
    assert all(response.status_code == 422 for response in invalid_review_pages)

    review_key = f"api-review-{uuid4()}"
    review_body = {
        "expected_lock_version": submitted.json()["lock_version"],
        "decision": "approved",
        "reason": None,
    }
    reviewed = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": review_key,
        },
        json=review_body,
    )
    replayed_review = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": review_key,
        },
        json=review_body,
    )

    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "published"
    assert reviewed.json()["decision"] == "approved"
    assert reviewed.json()["idempotent"] is False
    assert replayed_review.status_code == 200
    assert replayed_review.json()["idempotent"] is True
    assert replayed_review.json()["review_id"] == reviewed.json()["review_id"]
    assert db.scalar(select(func.count(StudentReportReview.id))) == 1

    report_api_scenario.login(db_client, "guardian")
    family_context = db_client.get("/api/v1/evaluations/reports/context")
    family_foundation_context = db_client.get("/api/v1/evaluations/context")
    family = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports",
        params={"enrollment_id": str(scenario.enrollment.id)},
    )
    invalid_family_pages = [
        db_client.get(
            f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports",
            params=parameters,
        )
        for parameters in (
            {"page": 0},
            {"page_size": 0},
            {"page_size": 101},
        )
    ]
    assert family_context.status_code == 200
    assert family_context.json()["family_available"] is True
    assert family_foundation_context.status_code == 200
    assert family_foundation_context.json()["reports_available"] is True
    assert family.status_code == 200
    assert family.json()["total"] == 1
    assert family.json()["items"][0]["report_id"] == str(report_id)
    assert family.json()["items"][0]["body"] == "经 API 提交并发布的日报"
    assert "draft_body" not in family.json()["items"][0]
    assert all(response.status_code == 422 for response in invalid_family_pages)

    wrong_enrollment_before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )
    wrong_enrollment = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports",
        params={"enrollment_id": str(uuid4())},
    )
    assert wrong_enrollment.status_code == 404
    assert wrong_enrollment.json()["code"] == "evaluation_resource_not_found"
    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == wrong_enrollment_before

    teaching_read_permission_id = db.scalar(
        select(Permission.id).where(Permission.key == "teaching.read")
    )
    assert teaching_read_permission_id is not None
    enrollment_deny = UserPermissionOverride(
        user_id=scenario.accounts["guardian"].id,
        permission_id=teaching_read_permission_id,
        effect="deny",
        scope_kind="enrollment",
        scope_id=scenario.enrollment.id,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=1),
        created_by_user_id=scenario.accounts["admin"].id,
        grant_reason="API 家庭报名范围拒绝测试",
    )
    db.add(enrollment_deny)
    db.flush()
    unfiltered_denied = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports"
    )
    explicit_denied = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports",
        params={"enrollment_id": str(scenario.enrollment.id)},
    )
    assert unfiltered_denied.status_code == 200
    assert unfiltered_denied.json()["total"] == 0
    assert unfiltered_denied.json()["items"] == []
    assert explicit_denied.status_code == 404
    assert explicit_denied.json()["code"] == "evaluation_resource_not_found"

    db.delete(enrollment_deny)
    db.flush()
    restored = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports"
    )
    assert restored.status_code == 200
    assert restored.json()["total"] == 1

    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id
            == scenario.people["guardian"].id,
            GuardianRelationship.student_person_id
            == scenario.people["student"].id,
        )
    )
    assert relationship is not None
    relationship.ended_at = NOW - timedelta(microseconds=1)
    relationship.ended_by_user_id = scenario.accounts["admin"].id
    relationship.end_reason = "API 即时撤销测试"
    db.flush()
    before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    revoked = db_client.get(
        f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports"
    )
    revoked_context = db_client.get("/api/v1/evaluations/reports/context")
    revoked_foundation_context = db_client.get("/api/v1/evaluations/context")

    assert revoked.status_code == 404
    assert revoked.json()["code"] == "evaluation_resource_not_found"
    assert revoked_context.status_code == 200
    assert revoked_context.json() == {
        "author_available": False,
        "reviewer_available": False,
        "family_available": False,
    }
    assert revoked_foundation_context.status_code == 200
    assert revoked_foundation_context.json()["reports_available"] is False
    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


@pytest.mark.parametrize(
    "failure",
    (
        "cancelled_enrollment",
        "archived_cohort",
        "inactive_class",
        "inactive_project",
    ),
)
def test_family_publication_rechecks_live_target_and_hides_invalid_rows(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
    failure: str,
) -> None:
    report_id, submitted = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="目标失效后不得继续展示的家庭日报",
    )
    admin_csrf = report_api_scenario.login(db_client, "admin")
    approved = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": f"api-review-{uuid4()}",
        },
        json={
            "expected_lock_version": submitted["lock_version"],
            "decision": "approved",
            "reason": None,
        },
    )
    assert approved.status_code == 200

    scenario = report_api_scenario.report
    if failure == "cancelled_enrollment":
        scenario.enrollment.status = "cancelled"
        scenario.enrollment.cancelled_at = NOW
    elif failure == "archived_cohort":
        scenario.cohort.status = "archived"
    elif failure == "inactive_class":
        scenario.teaching_class.is_active = False
    else:
        project = db.get(GameProject, scenario.teaching_class.project_id)
        assert project is not None
        project.is_active = False
    db.flush()

    report_api_scenario.login(db_client, "guardian")
    report_context = db_client.get("/api/v1/evaluations/reports/context")
    foundation_context = db_client.get("/api/v1/evaluations/context")
    published_url = (
        f"/api/v1/evaluations/students/{scenario.people['student'].id}"
        "/published-reports"
    )
    unfiltered = db_client.get(published_url)
    explicit = db_client.get(
        published_url,
        params={"enrollment_id": str(scenario.enrollment.id)},
    )

    assert report_context.status_code == 200
    assert report_context.json()["family_available"] is False
    assert foundation_context.status_code == 200
    assert foundation_context.json()["reports_available"] is False
    assert unfiltered.status_code == 200
    assert unfiltered.json()["total"] == 0
    assert unfiltered.json()["items"] == []
    assert "目标失效后不得继续展示的家庭日报" not in unfiltered.text
    assert explicit.status_code == 404
    assert explicit.json()["code"] == "evaluation_resource_not_found"


def test_review_queue_without_review_capability_is_account_level_403(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    report_api_scenario.login(db_client, "outsider")

    response = db_client.get("/api/v1/evaluations/reports/review-queue")

    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_operation_denied"


def test_review_mutations_without_review_capability_are_account_level_403(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    csrf = report_api_scenario.login(db_client, "outsider")
    report_id = uuid4()
    before = (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(StudentReportWindowGrant.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    responses = (
        db_client.post(
            f"/api/v1/evaluations/reports/{report_id}/reviews",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"api-review-{uuid4()}",
            },
            json={"unexpected": True},
        ),
        db_client.post(
            f"/api/v1/evaluations/reports/{report_id}/reopen",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"api-reopen-{uuid4()}",
            },
            json={"unexpected": True},
        ),
    )

    for response in responses:
        assert response.status_code == 403
        assert response.json()["code"] == "evaluation_operation_denied"
    assert (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(StudentReportWindowGrant.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


def test_author_and_family_routes_without_operation_capability_are_account_level_403(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    csrf = report_api_scenario.login(db_client, "outsider")
    report_id = uuid4()
    before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    responses = (
        db_client.get(
            "/api/v1/evaluations/reports/slots",
            params={"enrollment_id": str(scenario.enrollment.id)},
        ),
        db_client.get(
            "/api/v1/evaluations/reports",
            params={"enrollment_id": str(scenario.enrollment.id)},
        ),
        db_client.get(f"/api/v1/evaluations/reports/{report_id}"),
        db_client.post(
            f"/api/v1/evaluations/reports/{report_id}/submit",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"api-submit-{uuid4()}",
            },
            json={"unexpected": True},
        ),
        db_client.get(
            f"/api/v1/evaluations/students/{scenario.people['student'].id}/published-reports"
        ),
    )

    for response in responses:
        assert response.status_code == 403
        assert response.json()["code"] == "evaluation_operation_denied"
    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


def test_csrf_idempotency_and_strict_body_fail_before_any_report_write(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    csrf = report_api_scenario.login(db_client, "primary")
    report_id = uuid4()
    url = f"/api/v1/evaluations/reports/{report_id}/draft"
    body = {
        "enrollment_id": str(scenario.enrollment.id),
        "report_kind": "daily",
        "period_start_date": NOW.date().isoformat(),
        "body": "不会被写入的正文",
        "dimension_values": {},
        "expected_draft_revision": 0,
    }
    before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    missing_csrf = db_client.put(
        url,
        headers={"Idempotency-Key": f"api-draft-{uuid4()}"},
        json=body,
    )
    missing_idempotency = db_client.put(
        url,
        headers={"X-CSRF-Token": csrf},
        json=body,
    )
    duplicate_idempotency = db_client.put(
        url,
        headers=[
            ("X-CSRF-Token", csrf),
            ("Idempotency-Key", "first-key"),
            ("Idempotency-Key", "second-key"),
        ],
        json=body,
    )

    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "csrf_invalid"
    assert missing_idempotency.status_code == 422
    assert missing_idempotency.json()["code"] == "evaluation_idempotency_key_invalid"
    assert duplicate_idempotency.status_code == 422
    assert duplicate_idempotency.json()["code"] == "evaluation_idempotency_key_invalid"
    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


def test_missing_and_denied_review_targets_are_the_same_private_404_with_zero_writes(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    primary_csrf = report_api_scenario.login(db_client, "primary")
    report_id = uuid4()
    draft = db_client.put(
        f"/api/v1/evaluations/reports/{report_id}/draft",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": f"api-draft-{uuid4()}",
        },
        json={
            "enrollment_id": str(scenario.enrollment.id),
            "report_kind": "daily",
            "period_start_date": NOW.date().isoformat(),
            "body": "待私有审核目标",
            "dimension_values": {},
            "expected_draft_revision": 0,
        },
    )
    submitted = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/submit",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": f"api-submit-{uuid4()}",
        },
        json={
            "expected_lock_version": draft.json()["lock_version"],
            "expected_draft_revision": draft.json()["draft_revision"],
        },
    )
    assert submitted.status_code == 201

    permission_id = db.scalar(
        select(Permission.id).where(Permission.key == "student_reports.review")
    )
    assert permission_id is not None
    db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["outsider"].id,
            permission_id=permission_id,
            effect="allow",
            scope_kind="assigned_class",
            scope_id=scenario.teaching_class.id,
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=1),
            created_by_user_id=scenario.accounts["admin"].id,
            grant_reason="API 私有审核目标测试授权",
        )
    )
    db.flush()
    outsider_csrf = report_api_scenario.login(db_client, "outsider")
    visible = db_client.get("/api/v1/evaluations/reports/review-queue")
    assert visible.status_code == 200
    assert visible.json()["total"] == 1

    db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["outsider"].id,
            permission_id=permission_id,
            effect="deny",
            scope_kind="enrollment",
            scope_id=scenario.enrollment.id,
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=1),
            created_by_user_id=scenario.accounts["admin"].id,
            grant_reason="API 私有审核目标测试拒绝",
        )
    )
    db.flush()
    revoked_context = db_client.get("/api/v1/evaluations/reports/context")
    revoked_foundation_context = db_client.get("/api/v1/evaluations/context")
    assert revoked_context.status_code == 200
    assert revoked_context.json()["reviewer_available"] is False
    assert revoked_foundation_context.status_code == 200
    assert revoked_foundation_context.json()["reports_available"] is False

    before = (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    def review(target_id: object) -> Response:
        return db_client.post(
            f"/api/v1/evaluations/reports/{target_id}/reviews",
            headers={
                "X-CSRF-Token": outsider_csrf,
                "Idempotency-Key": f"api-review-{uuid4()}",
            },
            json={
                "expected_lock_version": submitted.json()["lock_version"],
                "decision": "approved",
                "reason": None,
            },
        )

    denied = review(report_id)
    missing = review(uuid4())

    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
    assert denied.json()["code"] == "evaluation_resource_not_found"
    assert (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


def test_ineligible_review_target_is_private_and_leaves_zero_writes(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_id, submitted = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="学员停用后不可审核",
    )
    profile = db.get(StudentProfile, scenario.people["student"].id)
    assert profile is not None
    profile.status = "inactive"
    db.flush()
    admin_csrf = report_api_scenario.login(db_client, "admin")
    report = db.get(StudentReport, report_id)
    assert report is not None
    before_report = (
        report.status,
        report.lock_version,
        report.current_version_id,
        report.published_version_id,
        report.current_window_grant_id,
    )
    before_counts = (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    queue = db_client.get("/api/v1/evaluations/reports/review-queue")

    def review(target_id: UUID) -> Response:
        return db_client.post(
            f"/api/v1/evaluations/reports/{target_id}/reviews",
            headers={
                "X-CSRF-Token": admin_csrf,
                "Idempotency-Key": f"api-review-{uuid4()}",
            },
            json={
                "expected_lock_version": submitted["lock_version"],
                "decision": "approved",
                "reason": None,
            },
        )

    denied = review(report_id)
    missing = review(uuid4())

    assert queue.status_code == 200
    assert queue.json()["total"] == 0
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
    db.refresh(report)
    assert (
        report.status,
        report.lock_version,
        report.current_version_id,
        report.published_version_id,
        report.current_window_grant_id,
    ) == before_report
    assert (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before_counts


def test_self_review_conflict_is_typed_and_has_zero_side_effects(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    permission_id = db.scalar(
        select(Permission.id).where(Permission.key == "student_reports.review")
    )
    assert permission_id is not None
    db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["primary"].id,
            permission_id=permission_id,
            effect="allow",
            scope_kind="enrollment",
            scope_id=scenario.enrollment.id,
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=1),
            created_by_user_id=scenario.accounts["admin"].id,
            grant_reason="API 自审冲突测试",
        )
    )
    db.flush()
    csrf = report_api_scenario.login(db_client, "primary")
    report_id = uuid4()
    draft = db_client.put(
        f"/api/v1/evaluations/reports/{report_id}/draft",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"api-draft-{uuid4()}",
        },
        json={
            "enrollment_id": str(scenario.enrollment.id),
            "report_kind": "daily",
            "period_start_date": NOW.date().isoformat(),
            "body": "不能由作者自审",
            "dimension_values": {},
            "expected_draft_revision": 0,
        },
    )
    submitted = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/submit",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"api-submit-{uuid4()}",
        },
        json={
            "expected_lock_version": draft.json()["lock_version"],
            "expected_draft_revision": draft.json()["draft_revision"],
        },
    )
    before = (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    response = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"api-review-{uuid4()}",
        },
        json={
            "expected_lock_version": submitted.json()["lock_version"],
            "decision": "approved",
            "reason": None,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "evaluation_review_conflict"
    assert (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


def test_correction_reopen_and_revision_routes_preserve_the_workflow(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": NOW}
    monkeypatch.setattr(report_router, "_now", lambda: clock["now"])
    first_id, first_submitted = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="等待超期后重新开放整改",
    )
    admin_csrf = report_api_scenario.login(db_client, "admin")
    rejected = db_client.post(
        f"/api/v1/evaluations/reports/{first_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": f"api-review-{uuid4()}",
        },
        json={
            "expected_lock_version": first_submitted["lock_version"],
            "decision": "rejected",
            "reason": "请补充训练目标和完成情况",
        },
    )
    assert rejected.status_code == 200
    first_report = db.get(StudentReport, first_id)
    assert first_report is not None and first_report.correction_due_at is not None
    clock["now"] = first_report.correction_due_at + timedelta(microseconds=1)

    reopen_key = f"api-reopen-{uuid4()}"
    reopen_body = {
        "expected_lock_version": rejected.json()["lock_version"],
        "grant_kind": "correction",
        "reason": "教练网络中断，批准重新开放",
    }
    reopened = db_client.post(
        f"/api/v1/evaluations/reports/{first_id}/reopen",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": reopen_key,
        },
        json=reopen_body,
    )
    replayed_reopen = db_client.post(
        f"/api/v1/evaluations/reports/{first_id}/reopen",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": reopen_key,
        },
        json=reopen_body,
    )
    assert reopened.status_code == 201
    assert reopened.json()["grant_kind"] == "correction"
    assert "reason" not in reopened.json()
    assert replayed_reopen.status_code == 200
    assert replayed_reopen.json()["idempotent"] is True
    assert replayed_reopen.json()["grant_id"] == reopened.json()["grant_id"]

    primary_csrf = report_api_scenario.login(db_client, "primary")
    correction_key = f"api-correction-{uuid4()}"
    correction_body = {"expected_lock_version": reopened.json()["lock_version"]}
    correction = db_client.post(
        f"/api/v1/evaluations/reports/{first_id}/correction-drafts",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": correction_key,
        },
        json=correction_body,
    )
    replayed_correction = db_client.post(
        f"/api/v1/evaluations/reports/{first_id}/correction-drafts",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": correction_key,
        },
        json=correction_body,
    )
    assert correction.status_code == 201
    assert correction.json()["status"] == "correction_draft"
    assert correction.json()["draft_revision"] == 1
    assert replayed_correction.status_code == 200
    assert replayed_correction.json()["idempotent"] is True
    assert (
        replayed_correction.json()["draft_revision"]
        == correction.json()["draft_revision"]
    )

    clock["now"] = NOW
    second_id, second_submitted = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date() - timedelta(days=1),
        body="批准后进入修订",
    )
    admin_csrf = report_api_scenario.login(db_client, "admin")
    approved = db_client.post(
        f"/api/v1/evaluations/reports/{second_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": f"api-review-{uuid4()}",
        },
        json={
            "expected_lock_version": second_submitted["lock_version"],
            "decision": "approved",
            "reason": None,
        },
    )
    assert approved.status_code == 200
    primary_csrf = report_api_scenario.login(db_client, "primary")
    revision_key = f"api-revision-{uuid4()}"
    revision_body = {"expected_lock_version": approved.json()["lock_version"]}
    revision = db_client.post(
        f"/api/v1/evaluations/reports/{second_id}/revisions",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": revision_key,
        },
        json=revision_body,
    )
    replayed_revision = db_client.post(
        f"/api/v1/evaluations/reports/{second_id}/revisions",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": revision_key,
        },
        json=revision_body,
    )

    assert revision.status_code == 201
    assert revision.json()["status"] == "published"
    assert revision.json()["draft_revision"] == 1
    assert replayed_revision.status_code == 200
    assert replayed_revision.json()["idempotent"] is True
    assert replayed_revision.json()["draft_revision"] == revision.json()[
        "draft_revision"
    ]
    second_report = db.get(StudentReport, second_id)
    assert second_report is not None
    assert second_report.published_version_id is not None


def test_period_author_keeps_correction_access_after_assignment_ends(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": NOW}
    monkeypatch.setattr(report_router, "_now", lambda: clock["now"])
    report_id, submitted = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="服务期内提交、服务结束后整改",
    )
    admin_csrf = report_api_scenario.login(db_client, "admin")
    rejected = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": f"api-review-{uuid4()}",
        },
        json={
            "expected_lock_version": submitted["lock_version"],
            "decision": "rejected",
            "reason": "请在整改窗口补充训练结果",
        },
    )
    assert rejected.status_code == 200

    scenario = report_api_scenario.report
    admin_account_id = scenario.accounts["admin"].id
    scenario.primary_assignment.ended_at = NOW + timedelta(minutes=30)
    scenario.primary_assignment.ended_by_user_id = admin_account_id
    scenario.primary_assignment.end_reason = "营期服务结束"
    db.flush()
    clock["now"] = NOW + timedelta(hours=1)
    primary_csrf = report_api_scenario.login(db_client, "primary")
    correction = db_client.post(
        f"/api/v1/evaluations/reports/{report_id}/correction-drafts",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": f"api-correction-{uuid4()}",
        },
        json={"expected_lock_version": rejected.json()["lock_version"]},
    )

    assert correction.status_code == 201
    assert correction.json()["status"] == "correction_draft"


def test_future_primary_assignment_is_unavailable_until_service_begins(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": NOW}
    monkeypatch.setattr(report_router, "_now", lambda: clock["now"])
    starts_at = NOW + timedelta(hours=1)
    report_api_scenario.report.primary_assignment.valid_from = starts_at
    db.flush()
    report_api_scenario.login(db_client, "primary")
    enrollment_id = report_api_scenario.report.enrollment.id

    before_context = db_client.get("/api/v1/evaluations/reports/context")
    before_slots = db_client.get(
        "/api/v1/evaluations/reports/slots",
        params={"enrollment_id": str(enrollment_id)},
    )

    assert before_context.status_code == 200
    assert before_context.json()["author_available"] is False
    assert before_slots.status_code == 403
    assert before_slots.json()["code"] == "evaluation_operation_denied"

    clock["now"] = starts_at
    after_context = db_client.get("/api/v1/evaluations/reports/context")
    after_slots = db_client.get(
        "/api/v1/evaluations/reports/slots",
        params={"enrollment_id": str(enrollment_id)},
    )

    assert after_context.status_code == 200
    assert after_context.json()["author_available"] is True
    assert after_slots.status_code == 200


def test_private_target_precedes_malformed_mutation_body_and_leaves_zero_writes(
    db: Session,
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )
    primary_csrf = report_api_scenario.login(db_client, "primary")
    missing_id = uuid4()
    missing_submit = db_client.post(
        f"/api/v1/evaluations/reports/{missing_id}/submit",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": f"api-submit-{uuid4()}",
        },
        json={"unexpected": True},
    )
    admin_csrf = report_api_scenario.login(db_client, "admin")
    missing_review = db_client.post(
        f"/api/v1/evaluations/reports/{missing_id}/reviews",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": f"api-review-{uuid4()}",
        },
        json={"unexpected": True},
    )
    primary_csrf = report_api_scenario.login(db_client, "primary")
    missing_correction = db_client.post(
        f"/api/v1/evaluations/reports/{missing_id}/correction-drafts",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": f"api-correction-{uuid4()}",
        },
        json={"unexpected": True},
    )
    missing_revision = db_client.post(
        f"/api/v1/evaluations/reports/{missing_id}/revisions",
        headers={
            "X-CSRF-Token": primary_csrf,
            "Idempotency-Key": f"api-revision-{uuid4()}",
        },
        json={"unexpected": True},
    )
    admin_csrf = report_api_scenario.login(db_client, "admin")
    missing_reopen = db_client.post(
        f"/api/v1/evaluations/reports/{missing_id}/reopen",
        headers={
            "X-CSRF-Token": admin_csrf,
            "Idempotency-Key": f"api-reopen-{uuid4()}",
        },
        json={"unexpected": True},
    )

    responses = (
        missing_submit,
        missing_review,
        missing_correction,
        missing_revision,
        missing_reopen,
    )
    assert all(response.status_code == 404 for response in responses)
    assert all(response.json() == missing_submit.json() for response in responses)
    assert missing_submit.json()["code"] == "evaluation_resource_not_found"
    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before
