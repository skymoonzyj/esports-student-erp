from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import app.teaching.router as teaching_router
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import AuthSession, UserAccount
from app.teaching.coach_service import CoachService
from app.teaching.cohort_service import CohortService
from app.teaching.models import CoachAssignment
from app.teaching.schemas import CoachAssignmentCreate, CohortCreate, TeachingClassCreate

_RAW_SECRET_SUFFIX = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
_LOWERCASE_RAW_SECRET_SUFFIX = "abcdefghij0123456789"


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post("/api/v1/auth/login", json={"identifier": phone, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def assert_minor_account_was_disabled(
    db: Session,
    account_id: UUID,
) -> None:
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert account.status == "disabled"
    assert (
        db.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.user_id == account_id,
                AuthSession.revoked_at.is_(None),
            )
        )
        == 0
    )


def test_coach_assignment_audit_failure_rolls_back_create_and_end(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    actor_id = platform_users["accounts"]["admin"].id
    coach_id = platform_users["people"]["coach"].id
    now = datetime(2026, 7, 28, 8, tzinfo=UTC)
    cohort_service = CohortService(db)
    cohort = cohort_service.create_cohort(
        CohortCreate(
            name="教练审计回滚营期",
            starts_at=now + timedelta(days=10),
            ends_at=now + timedelta(days=30),
            default_check_in_at=now + timedelta(days=10),
            default_check_out_at=now + timedelta(days=30),
        ),
        actor_user_id=actor_id,
    )
    teaching_class = cohort_service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=UUID("30000000-0000-4000-8000-000000000001"),
            name="教练审计回滚班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
        ),
        actor_user_id=actor_id,
    )

    def reject_audit(*args, **kwargs) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated coach assignment audit failure")

    monkeypatch.setattr(teaching_router.AuditService, "append", reject_audit)
    rejected_create = db_client.post(
        f"/api/v1/classes/{teaching_class.id}/coach-assignments",
        headers={"X-CSRF-Token": csrf},
        json={
            "coach_person_id": str(coach_id),
            "assignment_type": "assistant",
            "valid_from": (now - timedelta(days=1)).isoformat(),
        },
    )
    assert rejected_create.status_code == 500
    assert rejected_create.json()["code"] == "teaching_audit_failed"
    assert (
        db.scalar(
            select(func.count(CoachAssignment.id)).where(
                CoachAssignment.class_id == teaching_class.id
            )
        )
        == 0
    )

    existing = CoachService(db).create_assignment(
        teaching_class.id,
        CoachAssignmentCreate(
            coach_person_id=coach_id,
            assignment_type="assistant",
            valid_from=now - timedelta(days=1),
        ),
        actor_user_id=actor_id,
    )
    rejected_end = db_client.post(
        f"/api/v1/classes/{teaching_class.id}/coach-assignments/{existing.id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "审计失败不得结束"},
    )

    assert rejected_end.status_code == 500
    assert rejected_end.json()["code"] == "teaching_audit_failed"
    db.expire_all()
    stored = db.get(CoachAssignment, existing.id)
    assert stored is not None
    assert stored.ended_at is None
    assert stored.end_reason is None


def test_guardian_cascade_audit_validation_failure_rolls_back_everything(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    with pytest.raises(AuditMetadataRejected, match="request id"):
        platform_users["identity_service"].end_guardian_relationship(
            platform_users["people"]["minor_student"].id,
            minor_login.relationship.id,
            ended_by_user_id=platform_users["accounts"]["admin"].id,
            reason="监护安排结束",
            ended_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
            request_id="13800138000",
        )

    db.expire_all()
    relationship = db.get(type(minor_login.relationship), minor_login.relationship.id)
    account = db.get(UserAccount, minor_login.account_id)
    assert relationship is not None and relationship.ended_at is None
    assert account is not None and account.status == "active"
    assert (
        db.scalar(
            select(AuthSession.revoked_at).where(AuthSession.user_id == minor_login.account_id)
        )
        is None
    )
    assert (
        db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.resource_id == minor_login.account_id,
                AuditEvent.action == "account.auto_disable.guardian_missing",
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"field": "csrf-secret-x"},
        {"error_code": "Bearer session-token-x"},
        {"result_count": {"value": "cipher-secret-x"}},
    ],
)
def test_audit_service_rejects_nonconforming_allowlisted_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="person.identity.reveal",
            resource_type="person",
            resource_id=platform_users["people"]["own_student"].id,
            purpose="入住资料核对",
            outcome="failed",
            request_id="unsafe-metadata",
            metadata=metadata,
        )


@pytest.mark.parametrize(
    ("purpose", "metadata"),
    [
        ("审核申请 13800000000", {}),
        ("审核申请 reviewer@example.com", {}),
        ("审核申请 用户@example.com", {}),
        ("审核申请 user@例子.公司", {}),
        ("注册冲突复核", {"person_id": "110105199001021107"}),
        ("注册冲突复核", {"field": "identity_number", "error_code": "13800000000"}),
    ],
)
def test_registration_review_audit_rejects_raw_profile_values(
    db: Session,
    platform_users,
    purpose: str,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="registration.conflict.resolve",
            resource_type="registration_application",
            resource_id=uuid4(),
            purpose=purpose,
            outcome="allowed",
            request_id="registration-review-audit-test",
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "purpose",
    [
        "verification_code=123456",
        "password=short",
        "identifier=legacy-7",
        "phone=010-12345678",
        "email=user@localhost",
        "Cookie: erp_registration_session=abc123",
        "X-Registration-CSRF-Token: abc123",
        "verification-code-123456",
        "verification code 123456",
        "x-registration-csrf-token-abc123",
    ],
)
def test_registration_audit_rejects_labeled_low_entropy_secrets(
    purpose: str,
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_purpose(purpose)
    assert AuditService.public_purpose(purpose) == "敏感内容已隐藏"


@pytest.mark.parametrize(
    "request_id",
    [
        "verification_code=123456",
        "verification-code-123456",
        "verification code 123456",
        "x-registration-csrf-token-abc123",
    ],
)
def test_registration_audit_rejects_labeled_secret_request_id(
    db: Session,
    request_id: str,
) -> None:
    with pytest.raises(AuditMetadataRejected, match="request id"):
        AuditService(db).append(
            actor_user_id=None,
            action="registration.denied",
            resource_type="registration_application",
            resource_id=uuid4(),
            purpose=None,
            outcome="denied",
            request_id=request_id,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"verification_code": "123456"},
        {"password": "short"},
        {"identity_number": "masked"},
        {"identifier": "legacy-7"},
        {"phone": "masked"},
        {"email": "masked"},
        {"registration_cookie": "abc123"},
        {"registration_csrf": "abc123"},
    ],
)
def test_registration_audit_rejects_sensitive_metadata_key_names(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected, match="sensitive data"):
        AuditService.validate_metadata(metadata)


def test_registration_audit_allows_only_typed_safe_metadata() -> None:
    metadata = {
        "application_id": "71000000-0000-4000-8000-000000000001",
        "result_count": 2,
        "purpose": "student",
        "state": "expired",
        "identifier_kind": "email",
        "conflict_code": "identity_profile_mismatch",
    }

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    ("catalog_version", "scoring_version"),
    [
        ("talent-training-v1", "talent-score-v1"),
        ("talent-training-v2", "talent-score-v2"),
    ],
)
def test_talent_audit_allows_only_registered_public_versions(
    catalog_version: str,
    scoring_version: str,
) -> None:
    metadata = {
        "catalog_version": catalog_version,
        "scoring_version": scoring_version,
    }

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {"catalog_version": "talent-training-v3"},
        {"scoring_version": "talent-score-v3"},
    ],
)
def test_talent_audit_rejects_unregistered_version_values(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("catalog_version", []),
        ("catalog_version", {}),
        ("scoring_version", []),
        ("scoring_version", {}),
    ],
)
def test_talent_audit_filters_and_rejects_non_string_versions(
    key: str,
    value: object,
) -> None:
    metadata = {key: value}

    assert AuditService.public_metadata(metadata) == {}
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)


def test_enrollment_application_audit_metadata_is_public_and_valid() -> None:
    metadata = {
        "admission_case_id": "71000000-0000-4000-8000-000000000001",
        "student_person_id": "71000000-0000-4000-8000-000000000002",
        "project_id": "71000000-0000-4000-8000-000000000003",
        "program_id": "71000000-0000-4000-8000-000000000004",
        "coach_person_id": "71000000-0000-4000-8000-000000000005",
        "payment_preference": "offline",
    }

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    "purpose",
    [
        "password_recovery",
        "account_identifier_verification",
        "self_service_registration",
        "registration_conflict_review",
        "staff_identity_application_review",
        "password-reset-2026",
        "email-sender-2026",
        "registration-cookie-cleanup-2026",
        "high-cardinality-phone-1000",
    ],
)
def test_audit_hardening_preserves_existing_stable_purposes(purpose: str) -> None:
    AuditService.validate_purpose(purpose)
    assert AuditService.public_purpose(purpose) == purpose


def test_audit_hardening_preserves_existing_typed_phone_and_identity_labels() -> None:
    metadata = {
        "field": "phone",
        "permission_key": "people.identity.reveal",
        "auxiliary_phone_id": "71000000-0000-4000-8000-000000000002",
        "upload_session_id": "71000000-0000-4000-8000-000000000003",
        "identity_mode": "hidden",
    }

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


def test_housing_audit_metadata_is_public_and_valid() -> None:
    metadata = {
        "error_code": "housing_capacity_conflict",
        "scope_kind": "floor",
        "building_id": "52000000-0000-4000-8000-000000000010",
        "floor_id": "52000000-0000-4000-8000-000000000020",
        "room_id": "52000000-0000-4000-8000-000000000030",
        "stay_id": "52000000-0000-4000-8000-000000000040",
        "issue_id": "52000000-0000-4000-8000-000000000050",
        "transfer_id": "52000000-0000-4000-8000-000000000060",
        "resident_person_id": "51000000-0000-4000-8000-000000000070",
        "source_room_id": "52000000-0000-4000-8000-000000000080",
        "target_room_id": "52000000-0000-4000-8000-000000000090",
    }

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    "error_code",
    [
        "housing_account_required",
        "housing_profile_incomplete",
        "verified_email_required",
        "minor_guardian_required",
        "housing_room_not_verified",
        "housing_room_unavailable",
        "housing_capacity_conflict",
        "housing_gender_conflict",
        "housing_person_time_conflict",
        "housing_stale_version",
        "housing_checkout_confirmation_required",
        "housing_room_has_active_stays",
        "housing_invalid_stay_transition",
        "housing_open_issue_exists",
        "housing_issue_not_open",
    ],
)
def test_housing_lifecycle_error_codes_are_public(error_code: str) -> None:
    metadata = {"error_code": error_code}

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    "error_code",
    [
        "housing_report_empty",
        "housing_export_incomplete",
        "housing_report_scope_denied",
        "housing_report_state_conflict",
        "housing_report_correction_invalid",
        "housing_report_snapshot_unavailable",
        "housing_report_render_failed",
        "housing_audit_failed",
    ],
)
def test_housing_reporting_error_codes_are_public(error_code: str) -> None:
    metadata = {"error_code": error_code}

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


def test_housing_reporting_metadata_is_typed_and_public() -> None:
    metadata = {
        "batch_id": "52000000-0000-4000-8000-0000000000a1",
        "building_id": "52000000-0000-4000-8000-0000000000b1",
        "floor_id": "52000000-0000-4000-8000-0000000000c1",
        "scope_ids": [
            "52000000-0000-4000-8000-0000000000c1",
            "52000000-0000-4000-8000-0000000000c2",
        ],
        "resident_count": 2,
        "stay_row_count": 3,
        "guardian_row_count": 1,
        "format": "xlsx",
    }

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {"scope_ids": ["52000000-0000-4000-8000-0000000000A1"]},
        {
            "scope_ids": [
                "52000000-0000-4000-8000-0000000000a1",
                "52000000-0000-4000-8000-0000000000a1",
            ]
        },
        {"scope_ids": ["not-a-uuid"]},
        {"scope_ids": [f"52000000-0000-4000-8000-{index:012d}" for index in range(501)]},
        {"resident_count": True},
        {"stay_row_count": -1},
        {"guardian_row_count": "1"},
        {"format": "pdf"},
        {"format": []},
        {"purpose": "派出所报送"},
        {"note": "人工报送备注"},
        {"reason": "作废原因"},
    ],
)
def test_housing_reporting_metadata_rejects_untyped_or_free_text_values(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)


def test_housing_metadata_uuid_values_are_canonicalized() -> None:
    uppercase = {
        "stay_id": "52000000-0000-4000-8000-0000000000AA",
        "issue_id": "52000000-0000-4000-8000-0000000000BB",
        "transfer_id": "52000000-0000-4000-8000-0000000000CC",
        "resident_person_id": "51000000-0000-4000-8000-0000000000DD",
        "source_room_id": "52000000-0000-4000-8000-0000000000EE",
        "target_room_id": "52000000-0000-4000-8000-0000000000FF",
    }
    expected = {key: value.lower() for key, value in uppercase.items()}

    assert AuditService.public_metadata(uppercase) == expected
    AuditService.validate_metadata(expected)
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(uppercase)


def test_food_preference_audit_metadata_is_typed_and_public() -> None:
    metadata = {
        "person_id": "51000000-0000-4000-8000-000000000070",
        "old_version": 0,
        "new_version": 1,
        "cuisine_count": 3,
        "dish_count": 2,
        "population": "students",
        "include_inactive": False,
        "person_count": 20,
        "filled_count": 15,
        "file_sha256": "a" * 64,
        "source": "profile_admin",
        "format": "csv_zip",
    }

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)


def test_procurement_reimbursement_audit_metadata_is_typed_and_public() -> None:
    metadata = {"is_reimbursed": True}

    assert AuditService.public_metadata(metadata) == metadata
    AuditService.validate_metadata(metadata)
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata({"is_reimbursed": 1})


@pytest.mark.parametrize(
    "metadata",
    [
        {"old_version": -1},
        {"new_version": 0},
        {"cuisine_count": 4},
        {"dish_count": True},
        {"population": "everyone"},
        {"include_inactive": 1},
        {"person_count": -1},
        {"filled_count": "1"},
        {"file_sha256": "A" * 64},
        {"file_sha256": "a" * 63},
        {"source": "profile_coach"},
        {"format": "zip"},
        {"cuisine_name": "川菜"},
        {"dish_name": "宫保鸡丁"},
        {"name": "宫保鸡丁"},
    ],
)
def test_food_preference_audit_metadata_rejects_names_and_malformed_values(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {"resident_name": "张三"},
        {"phone": "13800138000"},
        {"identity_number": "11010519491231002X"},
        {"visit_reason": "临时探访"},
        {"issue_description": "宿舍设施损坏"},
        {"transfer_reason": "与室友不合"},
        {"notes": "个人健康情况"},
        {"stay_id": "13800138000"},
        {"issue_id": "11010519491231002X"},
        {"transfer_id": "not-a-uuid"},
        {"source_room_id": "13800138000"},
        {"target_room_id": "not-a-uuid"},
    ],
)
def test_housing_audit_rejects_pii_free_text_and_non_uuid_metadata(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)


@pytest.mark.parametrize(
    "request_id",
    ["13800138000", "11010519491231002X", "trace-11010519491231002Xsuffix"],
)
def test_audit_service_rejects_sensitive_request_id(
    db: Session,
    platform_users,
    request_id: str,
) -> None:
    with pytest.raises(AuditMetadataRejected, match="request id"):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="person.identity.reveal",
            resource_type="person",
            resource_id=platform_users["people"]["own_student"].id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata={"field": "identity_number"},
        )


def _assert_audit_rejects_secret_shaped_request_id(
    db: Session,
    prefix: str,
    *,
    lowercase: bool = False,
) -> None:
    suffix = _LOWERCASE_RAW_SECRET_SUFFIX if lowercase else _RAW_SECRET_SUFFIX
    with pytest.raises(AuditMetadataRejected, match="request id"):
        AuditService(db).append(
            actor_user_id=None,
            action="audit.request_id.test",
            resource_type="audit_test",
            resource_id=None,
            purpose=None,
            outcome="allowed",
            request_id=prefix + suffix,
            metadata={},
        )
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.action == "audit.request_id.test")
        )
        == 0
    )


def test_audit_service_rejects_ticket_shaped_request_id(
    db: Session,
) -> None:
    _assert_audit_rejects_secret_shaped_request_id(db, "Ticket_")


def test_audit_service_rejects_state_shaped_request_id(
    db: Session,
) -> None:
    _assert_audit_rejects_secret_shaped_request_id(db, "State_")


def test_audit_service_rejects_lowercase_secret_shaped_request_id(
    db: Session,
) -> None:
    _assert_audit_rejects_secret_shaped_request_id(db, "ticket_", lowercase=True)


@pytest.mark.parametrize(
    "request_id",
    [
        "state-0123456789abcdef0123456789abcdef",
        "ticket_abcdefghij_0123456789",
        "ticket-aaaaaaaa-bbbbbbbb-cccccccc",
        "service-credential-12345678-abcdefgh",
        "state.a-a-a-a-a-a-a-a-a-a-a-a-a-a",
        "a" * 43,
        "oauth-state_abcdefghij-0123456789",
        "oauth-state-aaaaaaaa-bbbbbbbb-cccccccc",
        "integration-local-service-token-2026-rotate-now",
    ],
)
def test_audit_service_rejects_structured_secret_shaped_request_id(
    db: Session,
    request_id: str,
) -> None:
    with pytest.raises(AuditMetadataRejected, match="request id"):
        AuditService(db).append(
            actor_user_id=None,
            action="audit.request_id.test",
            resource_type="audit_test",
            resource_id=None,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata={},
        )
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.action == "audit.request_id.test")
        )
        == 0
    )


@pytest.mark.parametrize(
    "request_id",
    [
        "00000000-0000-0000-0000-000000000000",
        "019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b",
        "build-10000000000",
        "build-1101051949123100217",
        "scheduler-019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b",
        "scheduler-019A2B3C-4D5E-7F60-8A9B-0C1D2E3F4A5B",
        "ready-019a2b3c4d5e7f608a9b0c1d2e3f4a5b",
        "ready-019A2B3C4D5E7F608A9B0C1D2E3F4A5B",
        "ready-689146567309405985cfbb87f005ce07",
        "e2e-full-class-confirmed",
        "password-reset-2026",
        "email-sender-2026",
        "registration-cookie-cleanup-2026",
        "high-cardinality-phone-1000",
    ],
)
def test_audit_service_allows_safe_request_id(
    db: Session,
    request_id: str,
) -> None:
    event = AuditService(db).append(
        actor_user_id=None,
        action="audit.request_id.test",
        resource_type="audit_test",
        resource_id=None,
        purpose=None,
        outcome="allowed",
        request_id=request_id,
        metadata={},
    )

    assert event.request_id == request_id


def test_audit_service_allows_only_typed_guardian_metadata(
    db: Session,
    platform_users,
) -> None:
    student_id = str(platform_users["people"]["own_student"].id)
    guardian_id = str(platform_users["people"]["guardian"].id)
    relationship_id = "0198f229-5d69-7000-8000-000000000001"
    metadata = {
        "student_person_id": student_id,
        "guardian_person_id": guardian_id,
        "relationship_id": relationship_id,
        "affected_account_count": 0,
    }

    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="guardian.relationship.test",
        resource_type="guardian_relationship",
        resource_id=UUID(relationship_id),
        purpose=None,
        outcome="allowed",
        request_id="guardian-safe-metadata",
        metadata=metadata,
    )

    assert event.event_metadata == metadata
    assert AuditService.public_metadata(event.event_metadata) == metadata


def test_audit_service_allows_exact_minor_guardian_error_code(
    db: Session,
    platform_users,
) -> None:
    metadata = {"error_code": "minor_guardian_required"}

    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="account.enable.guardian_missing",
        resource_type="account",
        resource_id=platform_users["accounts"]["minor_student"].id,
        purpose=None,
        outcome="denied",
        request_id="minor-guardian-error-code",
        metadata=metadata,
    )

    assert event.event_metadata == metadata
    assert AuditService.public_metadata(event.event_metadata) == metadata


def test_audit_service_allows_only_canonical_teaching_resource_ids(
    db: Session,
    platform_users,
) -> None:
    project_id = "30000000-0000-4000-8000-000000000001"
    team_id = "40000000-0000-4000-8000-000000000001"
    metadata = {
        "project_id": project_id,
        "professional_team_id": team_id,
        "class_id": "60000000-0000-4000-8000-000000000001",
    }

    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="teaching.professional_team.test",
        resource_type="professional_team",
        resource_id=UUID(team_id),
        purpose=None,
        outcome="allowed",
        request_id="teaching-safe-metadata",
        metadata=metadata,
    )

    assert event.event_metadata == metadata
    assert AuditService.public_metadata(event.event_metadata) == metadata


def test_audit_service_allows_typed_evaluation_bridge_metadata(
    db: Session,
    platform_users,
) -> None:
    login_request_id = "71000000-0000-4000-8000-000000000001"
    metadata = {
        "mode": "embedded",
        "client_id": "game-score-tool",
        "login_request_id": login_request_id,
    }

    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="evaluation.bridge.test",
        resource_type="evaluation_bridge_request",
        resource_id=UUID(login_request_id),
        purpose=None,
        outcome="allowed",
        request_id="evaluation-bridge-safe-metadata",
        metadata=metadata,
    )

    assert event.event_metadata == metadata
    assert AuditService.public_metadata(event.event_metadata) == metadata


def test_audit_service_allows_typed_evaluation_foundation_metadata(
    db: Session,
    platform_users,
) -> None:
    identifiers = {
        key: str(uuid4())
        for key in (
            "game_account_id",
            "auxiliary_phone_id",
            "staff_role_id",
            "staff_assignment_id",
            "analysis_profile_id",
            "media_id",
            "media_blob_id",
        )
    }
    metadata = {
        **identifiers,
        "profile_version": 2,
        "analysis_mode": "manual_only",
        "old_limit": 3,
        "new_limit": 5,
        "scope_kind": "project",
        "scope_id": str(uuid4()),
    }

    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="evaluation.foundation.test",
        resource_type="evaluation_foundation",
        resource_id=None,
        purpose=None,
        outcome="allowed",
        request_id="evaluation-foundation-safe-metadata",
        metadata=metadata,
    )

    assert event.event_metadata == metadata
    assert AuditService.public_metadata(event.event_metadata) == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"analysis_mode": "standalone"},
        {"profile_version": 0},
        {"profile_version": True},
        {"old_limit": 2},
        {"new_limit": True},
        {"media_blob_id": uuid4()},
        {"game_id": "player-secret"},
        {"phone_digest": "a" * 64},
        {"media_hash": "b" * 64},
        {"storage_key": "objects/private.webp"},
        {"physical_path": "/srv/private.webp"},
    ],
)
def test_audit_service_rejects_untyped_or_sensitive_evaluation_foundation_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="evaluation.foundation.test",
            resource_type="evaluation_foundation",
            resource_id=None,
            purpose=None,
            outcome="failed",
            request_id="evaluation-foundation-unsafe-metadata",
            metadata=metadata,
        )


def test_audit_service_allows_only_typed_system_evaluation_metadata(
    db: Session,
    platform_users,
) -> None:
    identifiers = {
        key: str(uuid4())
        for key in (
            "upload_session_id",
            "task_id",
            "attempt_id",
            "result_version_id",
            "adapter_certification_id",
            "idempotency_key_id",
        )
    }
    metadata = {
        **identifiers,
        "task_status": "needs_review",
        "attempt_status": "succeeded",
        "review_decision": "approved",
        "evaluation_action": "publish",
        "task_version": 2_147_483_647,
        "attempt": 2,
        "result_version": 2_147_483_647,
        "media_count": 3,
        "adapter_version": "1.0.0",
        "scoring_rule_version": "1",
        "result_schema_version": "1",
        "error_code": "evaluation_stale_version",
    }

    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="evaluation.task.test",
        resource_type="system_evaluation_task",
        resource_id=UUID(identifiers["task_id"]),
        purpose=None,
        outcome="allowed",
        request_id="system-evaluation-safe-metadata",
        metadata=metadata,
    )

    assert event.event_metadata == metadata
    assert AuditService.public_metadata(event.event_metadata) == metadata


def test_audit_service_allows_only_typed_camp_survey_print_metadata(
    db: Session,
    platform_users,
) -> None:
    metadata = {
        "staff_person_id": str(platform_users["people"]["coach"].id),
        "cohort_id": str(uuid4()),
        "identity_mode": "real_name",
        "task_version": 2,
        "item_count": 12,
    }
    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="camp_surveys.print",
        resource_type="camp_survey_print_projection",
        resource_id=None,
        purpose=None,
        outcome="allowed",
        request_id="camp-survey-print-safe",
        metadata=metadata,
    )
    assert AuditService.public_metadata(event.event_metadata) == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"respondent_person_id": str(uuid4())},
        {"score": 10},
        {"reason": "不应写入审计"},
        {"suggestion": "不应写入审计"},
        {"identity_mode": "unexpected"},
        {"item_count": True},
    ],
)
def test_audit_service_rejects_camp_survey_body_or_untyped_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="camp_surveys.print",
            resource_type="camp_survey_print_projection",
            resource_id=None,
            purpose=None,
            outcome="allowed",
            request_id="camp-survey-print-unsafe",
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"task_id": "not-a-uuid"},
        {"task_status": "deleted"},
        {"attempt_status": "unknown"},
        {"review_decision": "override"},
        {"evaluation_action": "publish with a free-text reason"},
        {"task_version": 0},
        {"task_version": 2_147_483_648},
        {"attempt": 4},
        {"result_version": True},
        {"result_version": 2_147_483_648},
        {"media_count": 4},
        {"adapter_version": "x" * 41},
        {"safe_error_summary": "database exception with private details"},
        {"reason": "free text belongs in the business event only"},
    ],
)
def test_audit_service_rejects_untyped_or_free_text_system_evaluation_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="evaluation.task.test",
            resource_type="system_evaluation_task",
            resource_id=None,
            purpose=None,
            outcome="failed",
            request_id="system-evaluation-unsafe-metadata",
            metadata=metadata,
        )


def test_audit_service_allows_only_typed_teaching_report_metadata(
    db: Session,
    platform_users,
) -> None:
    metadata = {
        "report_id": str(uuid4()),
        "enrollment_id": str(uuid4()),
        "report_kind": "weekly",
        "report_period_start": "2026-08-01",
        "report_status": "revision_pending_review",
        "report_version": 3,
        "report_window_kind": "revision",
        "review_decision": "approved",
        "evaluation_action": "submit",
    }

    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="student_report.test",
        resource_type="student_report",
        resource_id=UUID(metadata["report_id"]),
        purpose=None,
        outcome="allowed",
        request_id="teaching-report-safe-metadata",
        metadata=metadata,
    )

    assert event.event_metadata == metadata
    assert AuditService.public_metadata(event.event_metadata) == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"report_id": "not-a-uuid"},
        {"report_kind": "monthly"},
        {"report_period_start": "2026-8-1"},
        {"report_period_start": "2026-02-30"},
        {"report_status": "deleted"},
        {"report_version": 0},
        {"report_version": True},
        {"report_window_kind": "forever"},
        {"reason": "驳回和重开原因只能保存在业务表"},
    ],
)
def test_audit_service_rejects_untyped_or_free_text_teaching_report_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="student_report.test",
            resource_type="student_report",
            resource_id=None,
            purpose=None,
            outcome="failed",
            request_id="teaching-report-unsafe-metadata",
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"mode": "external"},
        {"client_id": "other-score-tool"},
        {"login_request_id": "not-a-uuid"},
        {"login_request_id": UUID("71000000-0000-4000-8000-000000000001")},
    ],
)
def test_audit_service_rejects_untyped_evaluation_bridge_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="evaluation.bridge.test",
            resource_type="evaluation_bridge_request",
            resource_id=None,
            purpose=None,
            outcome="failed",
            request_id="evaluation-bridge-unsafe-metadata",
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"project_id": "not-a-uuid"},
        {"professional_team_id": UUID("40000000-0000-4000-8000-000000000001")},
        {"project_name": "英雄联盟"},
        {"professional_team_name": "职业队"},
    ],
)
def test_audit_service_rejects_untyped_or_named_teaching_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="teaching.catalog.test",
            resource_type="teaching_catalog",
            resource_id=None,
            purpose=None,
            outcome="failed",
            request_id="teaching-unsafe-metadata",
            metadata=metadata,
        )


def test_audit_service_allows_typed_cohort_transition_metadata(
    db: Session,
    platform_users,
) -> None:
    cohort_id = "50000000-0000-4000-8000-000000000001"
    metadata = {
        "cohort_id": cohort_id,
        "old_status": "open",
        "new_status": "active",
        "transition_trigger": "request_reconciliation",
    }

    event = AuditService(db).append(
        actor_user_id=None,
        action="teaching.cohort.transition",
        resource_type="cohort",
        resource_id=UUID(cohort_id),
        purpose=None,
        outcome="allowed",
        request_id="cohort-transition-safe-metadata",
        metadata=metadata,
    )

    assert event.event_metadata == metadata
    assert AuditService.public_metadata(event.event_metadata) == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"cohort_id": "not-a-uuid"},
        {"old_status": "unknown"},
        {"new_status": "deleted"},
        {"transition_trigger": "client"},
        {"cohort_name": "暑期营"},
    ],
)
def test_audit_service_rejects_invalid_cohort_transition_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="teaching.cohort.test",
            resource_type="cohort",
            resource_id=None,
            purpose=None,
            outcome="failed",
            request_id="cohort-transition-unsafe-metadata",
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"student_person_id": "not-a-uuid"},
        {"guardian_person_id": "0198F229-5D69-7000-8000-000000000001"},
        {"relationship_id": UUID("0198f229-5d69-7000-8000-000000000001")},
        {"affected_account_count": -1},
        {"affected_account_count": True},
        {"full_name": "学员家长"},
        {"phone": "19990000005"},
        {"identity_number": "110105198401021039"},
        {"email": "guardian@example.com"},
        {"end_reason": "监护安排变更"},
        {"arbitrary": "safe-looking-code"},
    ],
)
def test_audit_service_rejects_untyped_or_pii_guardian_metadata(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="guardian.relationship.test",
            resource_type="guardian_relationship",
            resource_id=None,
            purpose=None,
            outcome="failed",
            request_id="guardian-unsafe-metadata",
            metadata=metadata,
        )


def test_guardian_relationship_create_and_end_audits_are_canonical_and_pii_free(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    student = platform_users["people"]["other_student"]
    guardian = platform_users["people"]["guardian"]
    csrf = sign_in(db_client, platform_users, "admin")
    create_request_id = "guardian-create-request"
    end_request_id = "guardian-end-request"

    created = db_client.post(
        f"/api/v1/people/{student.id}/guardians",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": create_request_id},
        json={
            "guardian_id": str(guardian.id),
            "relationship": "father",
            "is_primary": False,
            "valid_from": "2026-01-01",
            "valid_until": None,
        },
    )
    assert created.status_code == 201
    relationship_id = created.json()["relationship_id"]

    ended = db_client.post(
        f"/api/v1/people/{student.id}/guardians/{relationship_id}/end",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": end_request_id},
        json={"reason": "监护安排变更"},
    )
    assert ended.status_code == 200
    repeated = db_client.post(
        f"/api/v1/people/{student.id}/guardians/{relationship_id}/end",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "guardian-repeat-end"},
        json={"reason": "不得覆盖的后续原因"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "guardian_relationship_already_ended"

    audit_response = db_client.get(
        "/api/v1/audit-events",
        params={"resource_type": "guardian_relationship", "page_size": 100},
    )
    assert audit_response.status_code == 200
    rows = {
        item["action"]: item
        for item in audit_response.json()["items"]
        if item["resource_id"] == relationship_id
    }
    assert set(rows) == {"guardian.relationship.create", "guardian.relationship.end"}
    assert rows["guardian.relationship.create"]["request_id"] == create_request_id
    assert rows["guardian.relationship.create"]["metadata"] == {
        "student_person_id": str(student.id),
        "guardian_person_id": str(guardian.id),
    }
    assert rows["guardian.relationship.end"]["request_id"] == end_request_id
    assert rows["guardian.relationship.end"]["metadata"] == {"student_person_id": str(student.id)}
    for row in rows.values():
        assert row["resource_id"] == str(UUID(row["resource_id"]))
        assert set(row["metadata"]) <= {
            "student_person_id",
            "guardian_person_id",
            "relationship_id",
            "affected_account_count",
            "error_code",
        }
        for value in row["metadata"].values():
            if isinstance(value, str):
                assert value == str(UUID(value))

    stored = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_type == "guardian_relationship",
                AuditEvent.resource_id == UUID(relationship_id),
            )
        )
    )
    assert len(stored) == 2
    assert all("监护安排变更" not in str(event.event_metadata) for event in stored)
    assert all("不得覆盖的后续原因" not in str(event.event_metadata) for event in stored)


def test_admin_filters_audit_events_and_metadata_is_allowlisted(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    person = platform_users["people"]["own_student"]
    event = AuditEvent(
        actor_user_id=admin.id,
        action="person.identity.reveal",
        resource_type="person",
        resource_id=person.id,
        purpose="核对手机号 138-0013-8000",
        outcome="allowed",
        request_id="audit-query",
        event_metadata={
            "field": "identity_number",
            "phone": "13800138000",
            "password": "must-not-leak",
            "ciphertext": "must-not-leak",
        },
        created_at=datetime(2026, 7, 26, 12, tzinfo=UTC),
    )
    db.add(event)
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        "/api/v1/audit-events",
        params={
            "actor_user_id": str(admin.id),
            "action": "person.identity.reveal",
            "resource_type": "person",
            "date_from": datetime(2026, 7, 26, tzinfo=UTC).isoformat(),
            "date_to": datetime(2026, 7, 27, tzinfo=UTC).isoformat(),
            "page": 1,
            "page_size": 20,
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    row = next(item for item in response.json()["items"] if item["id"] == str(event.id))
    assert row["actor_name"] == platform_users["people"]["admin"].full_name
    assert row["metadata"] == {"field": "identity_number"}
    assert row["purpose"] == "敏感内容已隐藏"
    assert "138-0013-8000" not in response.text
    assert "must-not-leak" not in response.text


def test_audit_service_rejects_sensitive_purpose(
    db: Session,
    platform_users,
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="person.identity.reveal",
            resource_type="person",
            resource_id=platform_users["people"]["own_student"].id,
            purpose="核对身份证 11010519491231002X",
            outcome="allowed",
            request_id="unsafe-purpose",
            metadata={"field": "identity_number"},
        )


@pytest.mark.parametrize(
    "purpose",
    [
        "核对手机号 138-0013-8000",
        "核对手机号 138/0013/8000",
        "核对身份证 110105 19491231 002X",
        "核对临时凭据 AbcdEFG_hijklMNOP123456",
        "核对 token: raw-token-value-2026-safe",
        "核对密码 密码 Aa1!bb@cc#dd",
    ],
)
def test_audit_service_rejects_formatted_pii_and_raw_secrets(
    db: Session,
    platform_users,
    purpose: str,
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="person.identity.reveal",
            resource_type="person",
            resource_id=platform_users["people"]["own_student"].id,
            purpose=purpose,
            outcome="allowed",
            request_id="unsafe-formatted-purpose",
            metadata={"field": "identity_number"},
        )


def test_audit_service_allows_a_long_descriptive_slug(
    db: Session,
    platform_users,
) -> None:
    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="person.identity.reveal",
        resource_type="person",
        resource_id=platform_users["people"]["own_student"].id,
        purpose="security-incident-investigation",
        outcome="allowed",
        request_id="safe-long-purpose",
        metadata={"field": "identity_number"},
    )

    assert event.purpose == "security-incident-investigation"


def test_audit_api_returns_safe_permission_change_metadata(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    target = platform_users["accounts"]["customer_service"]
    event = AuditEvent(
        actor_user_id=admin.id,
        action="account.permission_override.add",
        resource_type="account",
        resource_id=target.id,
        purpose=None,
        outcome="allowed",
        request_id="permission-change-audit",
        event_metadata={
            "permission_key": "people.read",
            "effect": "deny",
            "scope_kind": "person",
            "scope_id": str(platform_users["people"]["other_student"].id),
        },
    )
    db.add(event)
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/audit-events")

    assert response.status_code == 200
    row = next(item for item in response.json()["items"] if item["id"] == str(event.id))
    assert row["actor_name"] == platform_users["people"]["admin"].full_name
    assert row["metadata"] == event.event_metadata


def test_audit_query_requires_audit_read(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "customer_service")

    response = db_client.get("/api/v1/audit-events")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_audit_date_to_is_an_exclusive_boundary(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin = platform_users["accounts"]["admin"]
    before_boundary = AuditEvent(
        actor_user_id=admin.id,
        action="audit.before-boundary",
        resource_type="account",
        resource_id=admin.id,
        purpose=None,
        outcome="allowed",
        request_id="audit-before-boundary",
        event_metadata={},
        created_at=datetime(2026, 7, 26, 23, 59, 59, 999999, tzinfo=UTC),
    )
    at_boundary = AuditEvent(
        actor_user_id=admin.id,
        action="audit.at-boundary",
        resource_type="account",
        resource_id=admin.id,
        purpose=None,
        outcome="allowed",
        request_id="audit-at-boundary",
        event_metadata={},
        created_at=datetime(2026, 7, 27, 0, 0, 0, tzinfo=UTC),
    )
    db.add_all([before_boundary, at_boundary])
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        "/api/v1/audit-events",
        params={"date_to": "2026-07-27T00:00:00Z"},
    )

    assert response.status_code == 200
    event_ids = {item["id"] for item in response.json()["items"]}
    assert str(before_boundary.id) in event_ids
    assert str(at_boundary.id) not in event_ids


def test_audit_events_are_append_only(
    db: Session,
    platform_users,
) -> None:
    event = AuditEvent(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="person.identity.reveal",
        resource_type="person",
        resource_id=platform_users["people"]["own_student"].id,
        purpose="入住资料核对",
        outcome="allowed",
        request_id="audit-append-only",
        event_metadata={"field": "identity_number"},
    )
    db.add(event)
    db.commit()

    with pytest.raises(DBAPIError):
        db.execute(update(AuditEvent).where(AuditEvent.id == event.id).values(outcome="failed"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(delete(AuditEvent).where(AuditEvent.id == event.id))
    db.rollback()


def test_audit_service_accepts_only_typed_admissions_identifiers(
    db: Session,
    platform_users,
) -> None:
    identifiers = {
        "admission_case_id": "71000000-0000-4000-8000-000000000001",
        "enrollment_id": "71000000-0000-4000-8000-000000000002",
        "payment_id": "71000000-0000-4000-8000-000000000003",
        "refund_id": "71000000-0000-4000-8000-000000000004",
        "withdrawal_id": "71000000-0000-4000-8000-000000000005",
        "service_account_id": "71000000-0000-4000-8000-000000000006",
        "credential_id": "71000000-0000-4000-8000-000000000007",
        "payment_conflict_id": "71000000-0000-4000-8000-000000000008",
        "import_event_id": "71000000-0000-4000-8000-000000000009",
        "refund_type": "withdrawal_prorated",
    }

    event = AuditService(db).append(
        actor_user_id=platform_users["accounts"]["admin"].id,
        action="admissions.audit.contract",
        resource_type="enrollment",
        resource_id=UUID(identifiers["enrollment_id"]),
        purpose=None,
        outcome="allowed",
        request_id="admissions-audit-contract",
        metadata=identifiers,
    )

    assert event.event_metadata == identifiers


@pytest.mark.parametrize(
    "metadata",
    [
        {"amount_cents": 100_00},
        {"payer_phone": "19990000001"},
        {"raw_payload": {"order": "sensitive"}},
        {"signature": "signature-secret-value"},
        {"credential_secret": "credential-secret-value"},
    ],
)
def test_audit_service_rejects_admissions_amounts_and_secrets(
    db: Session,
    platform_users,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService(db).append(
            actor_user_id=platform_users["accounts"]["admin"].id,
            action="admissions.audit.reject_sensitive",
            resource_type="payment",
            resource_id=None,
            purpose=None,
            outcome="failed",
            request_id="admissions-audit-sensitive",
            metadata=metadata,
        )
