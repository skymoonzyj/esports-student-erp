from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.teaching.models import Cohort, TeachingClass

NOW = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def create_case(
    db: Session,
    *,
    admin_id: UUID,
    student_id: UUID | None,
    status: str,
) -> AdmissionCase:
    admission_case = AdmissionCase(
        student_person_id=student_id,
        status=status,
        created_by_user_id=admin_id,
    )
    db.add(admission_case)
    db.commit()
    return admission_case


def create_cohort(
    db: Session,
    *,
    admin_id: UUID,
    status: str = "open",
    name: str | None = None,
) -> Cohort:
    cohort = Cohort(
        name=name or f"报名选项营期-{uuid4().hex[:8]}",
        starts_at=NOW + timedelta(days=10),
        ends_at=NOW + timedelta(days=20),
        default_check_in_at=NOW + timedelta(days=10),
        default_check_out_at=NOW + timedelta(days=20),
        status=status,
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin_id,
    )
    db.add(cohort)
    db.flush()
    return cohort


def create_class(
    db: Session,
    *,
    admin_id: UUID,
    cohort_id: UUID,
    name: str,
    capacity: int = 20,
    default_tuition_cents: int = 128_000,
    is_active: bool = True,
) -> TeachingClass:
    teaching_class = TeachingClass(
        cohort_id=cohort_id,
        project_id=PROJECT_ID,
        name=name,
        class_type="standard",
        professional_team_id=None,
        capacity=capacity,
        default_tuition_cents=default_tuition_cents,
        is_active=is_active,
        created_by_user_id=admin_id,
    )
    db.add(teaching_class)
    db.flush()
    return teaching_class


def test_case_student_candidates_search_phone_or_student_number_without_people_access(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin: UserAccount = platform_users["accounts"]["admin"]
    admission_case = create_case(
        db,
        admin_id=admin.id,
        student_id=None,
        status="pending_match",
    )
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS["people.list"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    by_number = db_client.get(
        f"/api/v1/admission-cases/{admission_case.id}/student-candidates",
        params={"query": " API-S-003 ", "page": 1, "page_size": 1},
    )
    by_phone = db_client.get(
        f"/api/v1/admission-cases/{admission_case.id}/student-candidates",
        params={"query": " 13800000004 "},
    )
    staff_phone = db_client.get(
        f"/api/v1/admission-cases/{admission_case.id}/student-candidates",
        params={"query": "19990000001"},
    )

    assert by_number.status_code == 200
    assert by_number.headers["cache-control"] == "no-store, private"
    assert by_number.json() == {
        "items": [
            {
                "id": str(platform_users["people"]["adult_without_account"].id),
                "full_name": "待开通账号人员",
                "student_number": "API-S-003",
                "student_status": "draft",
                "masked_phone": None,
            }
        ],
        "page": 1,
        "page_size": 1,
        "total": 1,
    }
    assert by_phone.status_code == 200
    assert by_phone.json()["items"] == [
        {
            "id": str(platform_users["people"]["self_student"].id),
            "full_name": "自助账号学员",
            "student_number": "API-S-004",
            "student_status": "draft",
            "masked_phone": "138****0004",
        }
    ]
    assert "13800000004" not in by_phone.text
    assert staff_phone.status_code == 200
    assert staff_phone.json()["items"] == []


def test_case_student_candidates_require_case_manage_permission(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admission_case = create_case(
        db,
        admin_id=platform_users["accounts"]["admin"].id,
        student_id=None,
        status="pending_match",
    )
    sign_in(db_client, platform_users, "customer_service")

    existing = db_client.get(
        f"/api/v1/admission-cases/{admission_case.id}/student-candidates",
        params={"query": "API-S-003"},
    )
    missing = db_client.get(
        f"/api/v1/admission-cases/{uuid4()}/student-candidates",
        params={"query": "API-S-003"},
    )

    assert existing.status_code == 403
    assert existing.json()["code"] == "permission_denied"
    assert missing.status_code == 403
    assert missing.json()["code"] == "permission_denied"


def test_enrollment_options_use_open_active_classes_real_capacity_and_start_readiness(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin: UserAccount = platform_users["accounts"]["admin"]
    student_id = platform_users["people"]["adult_without_account"].id
    admission_case = create_case(
        db,
        admin_id=admin.id,
        student_id=student_id,
        status="ready",
    )
    cohort = create_cohort(db, admin_id=admin.id)
    full_class = create_class(
        db,
        admin_id=admin.id,
        cohort_id=cohort.id,
        name="已满普通班",
        capacity=1,
        default_tuition_cents=128_000,
    )
    open_class = create_class(
        db,
        admin_id=admin.id,
        cohort_id=cohort.id,
        name="可报名菁英班",
        capacity=2,
        default_tuition_cents=368_000,
    )
    create_class(
        db,
        admin_id=admin.id,
        cohort_id=cohort.id,
        name="已停用班级",
        is_active=False,
    )
    closed_cohort = create_cohort(db, admin_id=admin.id, status="upcoming")
    create_class(
        db,
        admin_id=admin.id,
        cohort_id=closed_cohort.id,
        name="非开放营期班级",
    )
    occupied_case = create_case(
        db,
        admin_id=admin.id,
        student_id=platform_users["people"]["self_student"].id,
        status="ready",
    )
    occupied = Enrollment(
        admission_case_id=occupied_case.id,
        student_person_id=platform_users["people"]["self_student"].id,
        cohort_id=cohort.id,
        class_id=full_class.id,
        agreed_tuition_cents=full_class.default_tuition_cents,
        status="active",
        confirmed_at=NOW,
        created_by_user_id=admin.id,
    )
    db.add(occupied)
    db.flush()
    occupied_case.status = "enrolled"
    occupied_case.agreed_tuition_cents = full_class.default_tuition_cents
    occupied_case.current_enrollment_id = occupied.id
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/admission-cases/{admission_case.id}/enrollment-options",
        params={"page": 1, "page_size": 100},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    body = response.json()
    assert body["total"] == 2
    assert {item["class_id"] for item in body["items"]} == {
        str(full_class.id),
        str(open_class.id),
    }
    full = next(item for item in body["items"] if item["class_id"] == str(full_class.id))
    assert full == {
        "cohort_id": str(cohort.id),
        "cohort_name": cohort.name,
        "cohort_starts_at": cohort.starts_at.isoformat().replace("+00:00", "Z"),
        "cohort_ends_at": cohort.ends_at.isoformat().replace("+00:00", "Z"),
        "cohort_status": "open",
        "class_id": str(full_class.id),
        "class_name": "已满普通班",
        "class_is_active": True,
        "capacity": 1,
        "active_enrollment_count": 1,
        "is_full": True,
        "default_tuition_cents": 128_000,
        "readiness_at_start": "ready",
        "manage_allowed": True,
        "capacity_override_allowed": True,
    }


def test_enrollment_options_apply_target_denies_and_minor_start_readiness(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin: UserAccount = platform_users["accounts"]["admin"]
    admission_case = create_case(
        db,
        admin_id=admin.id,
        student_id=platform_users["people"]["other_student"].id,
        status="ready",
    )
    cohort = create_cohort(db, admin_id=admin.id)
    allowed_class = create_class(
        db,
        admin_id=admin.id,
        cohort_id=cohort.id,
        name="允许报名班级",
        capacity=1,
    )
    denied_class = create_class(
        db,
        admin_id=admin.id,
        cohort_id=cohort.id,
        name="明确禁止班级",
    )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=admin.id,
                permission_id=PERMISSIONS["enrollments.manage"].id,
                effect="deny",
                scope_kind="assigned_class",
                scope_id=denied_class.id,
            ),
            UserPermissionOverride(
                user_id=admin.id,
                permission_id=PERMISSIONS["enrollments.capacity_override"].id,
                effect="deny",
                scope_kind="assigned_class",
                scope_id=allowed_class.id,
            ),
        ]
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/admission-cases/{admission_case.id}/enrollment-options"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["class_id"] == str(allowed_class.id)
    assert response.json()["items"][0]["readiness_at_start"] == (
        "minor_guardian_required"
    )
    assert response.json()["items"][0]["capacity_override_allowed"] is False


def test_case_projection_and_mutations_keep_archived_history_read_only(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin: UserAccount = platform_users["accounts"]["admin"]
    student_id = platform_users["people"]["adult_without_account"].id
    admission_case = create_case(
        db,
        admin_id=admin.id,
        student_id=student_id,
        status="ready",
    )
    cohort = create_cohort(db, admin_id=admin.id, status="archived")
    teaching_class = create_class(
        db,
        admin_id=admin.id,
        cohort_id=cohort.id,
        name="已归档历史班级",
    )
    db.add(
        Enrollment(
            admission_case_id=admission_case.id,
            student_person_id=student_id,
            cohort_id=cohort.id,
            class_id=teaching_class.id,
            agreed_tuition_cents=teaching_class.default_tuition_cents,
            status="cancelled",
            confirmed_at=NOW - timedelta(days=30),
            cancelled_at=NOW - timedelta(days=20),
            created_by_user_id=admin.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    listed = db_client.get(
        "/api/v1/admission-cases",
        params={"status": "ready", "page_size": 100},
    )
    candidates = db_client.get(
        f"/api/v1/admission-cases/{admission_case.id}/student-candidates",
        params={"query": "API-S-003"},
    )
    options = db_client.get(
        f"/api/v1/admission-cases/{admission_case.id}/enrollment-options"
    )
    closed = db_client.post(
        f"/api/v1/admission-cases/{admission_case.id}/close",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "归档后不应再修改"},
    )

    assert listed.status_code == 200
    item = next(
        value for value in listed.json()["items"] if value["id"] == str(admission_case.id)
    )
    assert item["manage_allowed"] is False
    assert item["read_only_reason"] == "archived"
    for response in (candidates, options, closed):
        assert response.status_code == 409
        assert response.json()["code"] == "archived_cohort_locked"
    db.refresh(admission_case)
    assert admission_case.status == "ready"


def test_customer_service_case_projection_is_read_only_without_guessing_permissions(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admission_case = create_case(
        db,
        admin_id=platform_users["accounts"]["admin"].id,
        student_id=None,
        status="pending_match",
    )
    sign_in(db_client, platform_users, "customer_service")

    response = db_client.get(
        "/api/v1/admission-cases",
        params={"status": "pending_match"},
    )

    assert response.status_code == 200
    item = next(
        value for value in response.json()["items"] if value["id"] == str(admission_case.id)
    )
    assert item["manage_allowed"] is False
    assert item["read_only_reason"] is None
