from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, EnrollmentProgram
from app.auth.models import UserAccount
from app.teaching.models import (
    Cohort,
    GameProject,
    ProjectCoachAssignment,
    TeachingClass,
)

PROJECT_IDS = (
    UUID("30000000-0000-4000-8000-000000000001"),
    UUID("30000000-0000-4000-8000-000000000002"),
)
EXPECTED_PROGRAM_PRICES = [
    ("周末半天班", 80_000),
    ("周末全天班", 150_000),
    ("七日体验营", 699_900),
    ("月度体验营", 1_288_800),
    ("季度体验营", 3_288_800),
    ("家庭教育及亲子关系修复课程", 300_000),
    ("普通暑期班", 1_188_800),
    ("职业班冲刺班", 1_388_800),
    ("暑期进阶班", 1_488_800),
]


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def submit_application(
    client: TestClient,
    *,
    csrf_token: str,
    student_id: UUID,
    project_id: str,
    program_id: str,
    coach_person_id: UUID | None = None,
):
    return client.post(
        "/api/v1/enrollment-applications",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "student_person_id": str(student_id),
            "project_id": project_id,
            "program_id": program_id,
            **(
                {"coach_person_id": str(coach_person_id)}
                if coach_person_id is not None
                else {}
            ),
            "payment_preference": "offline",
        },
    )


def test_student_catalog_contains_all_projects_prices_and_course_details(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "self_student")

    response = db_client.get("/api/v1/enrollment-catalog")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    body = response.json()
    assert body["students"] == [
        {
            "student_person_id": str(platform_users["people"]["self_student"].id),
            "student_name": "自助账号学员",
            "student_number": "API-S-004",
            "age_status": "adult",
            "access_source": "self",
        }
    ]
    assert [project["name"] for project in body["projects"]] == [
        "英雄联盟",
        "和平精英",
        "三角洲行动",
        "无畏契约",
        "王者荣耀",
    ]
    assert [
        (program["name"], program["price_cents"]) for program in body["programs"]
    ] == EXPECTED_PROGRAM_PRICES
    assert all(program["summary"] for program in body["programs"])
    assert all(program["target_audience"] for program in body["programs"])
    assert all(program["highlights"] for program in body["programs"])
    seven_day = next(program for program in body["programs"] if program["name"] == "七日体验营")
    assert "报名时需由工作人员确认餐食安排" in seven_day["inclusion_note"]
    assert len(seven_day["schedule"]) == 8


def test_student_submits_offline_application_with_snapshots_and_no_duplicate(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf_token = sign_in(db_client, platform_users, "self_student")
    catalog = db_client.get("/api/v1/enrollment-catalog").json()
    project = catalog["projects"][0]
    program = catalog["programs"][6]

    created = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["self_student"].id,
        project_id=project["id"],
        program_id=program["id"],
    )
    duplicate = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["self_student"].id,
        project_id=project["id"],
        program_id=program["id"],
    )

    assert created.status_code == 201, created.text
    assert created.json() == {
        "id": created.json()["id"],
        "order_number": created.json()["order_number"],
        "student_person_id": str(platform_users["people"]["self_student"].id),
        "student_name": "自助账号学员",
        "status": "ready",
        "project_id": project["id"],
        "project_name": "英雄联盟",
        "program_id": program["id"],
        "program_name": "普通暑期班",
        "coach_person_id": None,
        "coach_name": None,
        "price_cents": 1_188_800,
        "payment_preference": "offline",
        "payment_status": "unpaid",
        "created_at": created.json()["created_at"],
        "updated_at": created.json()["updated_at"],
    }
    admission_case = db.get(AdmissionCase, UUID(created.json()["id"]))
    assert admission_case is not None
    assert (
        admission_case.application_source,
        admission_case.project_name_snapshot,
        admission_case.program_name_snapshot,
        admission_case.agreed_tuition_cents,
        admission_case.payment_preference,
    ) == ("self_service", "英雄联盟", "普通暑期班", 1_188_800, "offline")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate_enrollment_application"
    assert "请勿重复提交" in duplicate.json()["detail"]


def test_project_coach_is_filtered_required_and_snapshotted_for_enrollment(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    project = db.get(GameProject, PROJECT_IDS[0])
    other_project = db.get(GameProject, PROJECT_IDS[1])
    coach = platform_users["people"]["coach"]
    admin = platform_users["accounts"]["admin"]
    assert project is not None and other_project is not None
    db.add(
        ProjectCoachAssignment(
            project_id=project.id,
            coach_person_id=coach.id,
            is_active=True,
            created_by_user_id=admin.id,
        )
    )
    db.commit()
    csrf_token = sign_in(db_client, platform_users, "self_student")
    catalog = db_client.get("/api/v1/enrollment-catalog").json()
    configured = next(item for item in catalog["projects"] if item["id"] == str(project.id))
    unconfigured = next(
        item for item in catalog["projects"] if item["id"] == str(other_project.id)
    )
    program_id = catalog["programs"][0]["id"]

    assert configured["coaches"] == [
        {"person_id": str(coach.id), "full_name": coach.full_name}
    ]
    assert unconfigured["coaches"] == []

    missing = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["self_student"].id,
        project_id=str(project.id),
        program_id=program_id,
    )
    unavailable = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["self_student"].id,
        project_id=str(project.id),
        program_id=program_id,
        coach_person_id=uuid4(),
    )
    cross_project = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["self_student"].id,
        project_id=str(other_project.id),
        program_id=program_id,
        coach_person_id=coach.id,
    )
    created = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["self_student"].id,
        project_id=str(project.id),
        program_id=program_id,
        coach_person_id=coach.id,
    )

    assert missing.status_code == 422
    assert missing.json()["detail"] == "请选择该项目的教练"
    assert unavailable.status_code == 409
    assert unavailable.json()["code"] == "enrollment_coach_not_available"
    assert cross_project.status_code == 409
    assert created.status_code == 201, created.text
    assert created.json()["coach_person_id"] == str(coach.id)
    assert created.json()["coach_name"] == coach.full_name
    admission_case = db.get(AdmissionCase, UUID(created.json()["id"]))
    assert admission_case is not None
    assert admission_case.selected_coach_person_id == coach.id
    assert admission_case.coach_name_snapshot == coach.full_name

    sign_in(db_client, platform_users, "admin")
    response = db_client.get(
        "/api/v1/admission-cases",
        params={"application_source": "self_service"},
    )
    order = next(item for item in response.json()["items"] if item["id"] == created.json()["id"])
    assert order["selected_coach_person_id"] == str(coach.id)
    assert order["selected_coach_name"] == coach.full_name


def test_back_office_lists_submitted_offline_application_as_an_order(
    db_client: TestClient,
    platform_users,
) -> None:
    student_csrf = sign_in(db_client, platform_users, "self_student")
    catalog = db_client.get("/api/v1/enrollment-catalog").json()
    created = submit_application(
        db_client,
        csrf_token=student_csrf,
        student_id=platform_users["people"]["self_student"].id,
        project_id=catalog["projects"][0]["id"],
        program_id=catalog["programs"][2]["id"],
    )
    assert created.status_code == 201
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        "/api/v1/admission-cases",
        params={"status": "ready", "application_source": "self_service"},
    )

    assert response.status_code == 200
    order = next(
        item for item in response.json()["items"] if item["id"] == created.json()["id"]
    )
    assert order["student_name"] == "自助账号学员"
    assert order["application_source"] == "self_service"
    assert order["payment_preference"] == "offline"
    assert order["payment_status"] == "unpaid"
    assert order["agreed_tuition_cents"] == catalog["programs"][2]["price_cents"]
    assert order["due_cents"] == catalog["programs"][2]["price_cents"]


def test_guardian_can_apply_for_bound_student_but_not_another_student(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf_token = sign_in(db_client, platform_users, "guardian")
    catalog = db_client.get("/api/v1/enrollment-catalog").json()
    bound_ids = {student["student_person_id"] for student in catalog["students"]}
    own_student_id = platform_users["people"]["own_student"].id
    assert str(own_student_id) in bound_ids

    allowed = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=own_student_id,
        project_id=catalog["projects"][1]["id"],
        program_id=catalog["programs"][0]["id"],
    )
    forbidden = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["other_student"].id,
        project_id=catalog["projects"][1]["id"],
        program_id=catalog["programs"][0]["id"],
    )

    assert allowed.status_code == 201
    assert allowed.json()["student_name"] == "已绑定学员"
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "enrollment_application_forbidden"


def test_back_office_account_does_not_gain_student_application_permission(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/enrollment-catalog")

    assert response.status_code == 403
    assert response.json()["code"] == "enrollment_application_forbidden"


def test_stopped_program_and_project_cannot_receive_new_applications(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf_token = sign_in(db_client, platform_users, "self_student")
    catalog = db_client.get("/api/v1/enrollment-catalog").json()
    project = db.get(GameProject, UUID(catalog["projects"][0]["id"]))
    program = db.get(EnrollmentProgram, UUID(catalog["programs"][0]["id"]))
    assert project is not None and program is not None
    project.is_active = False
    db.flush()
    stopped_project = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["self_student"].id,
        project_id=str(project.id),
        program_id=str(program.id),
    )
    project.is_active = True
    program.is_active = False
    db.flush()
    stopped_program = submit_application(
        db_client,
        csrf_token=csrf_token,
        student_id=platform_users["people"]["self_student"].id,
        project_id=str(project.id),
        program_id=str(program.id),
    )

    assert stopped_project.status_code == 404
    assert stopped_project.json()["code"] == "enrollment_project_not_found"
    assert stopped_program.status_code == 404
    assert stopped_program.json()["code"] == "enrollment_program_not_found"


def test_back_office_options_and_confirmation_preserve_selected_project(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    admin: UserAccount = platform_users["accounts"]["admin"]
    program = db.scalar(select(EnrollmentProgram).order_by(EnrollmentProgram.sort_order))
    assert program is not None
    selected_project = db.get(GameProject, PROJECT_IDS[0])
    other_project = db.get(GameProject, PROJECT_IDS[1])
    assert selected_project is not None and other_project is not None
    admission_case = AdmissionCase(
        student_person_id=platform_users["people"]["self_student"].id,
        status="ready",
        agreed_tuition_cents=program.price_cents,
        application_source="self_service",
        selected_project_id=selected_project.id,
        selected_program_id=program.id,
        project_name_snapshot=selected_project.name,
        program_name_snapshot=program.name,
        payment_preference="offline",
        created_by_user_id=admin.id,
        updated_by_user_id=admin.id,
    )
    starts_at = datetime(2030, 7, 1, 1, tzinfo=UTC)
    cohort = Cohort(
        name="项目限制测试营期",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=21),
        default_check_in_at=starts_at,
        default_check_out_at=starts_at + timedelta(days=21),
        status="open",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin.id,
    )
    db.add_all([admission_case, cohort])
    db.flush()
    selected_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=selected_project.id,
        name="英雄联盟报名班",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=admin.id,
    )
    other_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=other_project.id,
        name="和平精英报名班",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=admin.id,
    )
    db.add_all([selected_class, other_class])
    db.commit()
    csrf_token = sign_in(db_client, platform_users, "admin")

    options = db_client.get(f"/api/v1/admission-cases/{admission_case.id}/enrollment-options")
    mismatch = db_client.post(
        f"/api/v1/admission-cases/{admission_case.id}/confirm-enrollment",
        headers={"X-CSRF-Token": csrf_token},
        json={"class_id": str(other_class.id)},
    )

    assert options.status_code == 200
    assert [item["class_id"] for item in options.json()["items"]] == [str(selected_class.id)]
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "enrollment_project_mismatch"
    assert mismatch.json()["detail"] == "只能分配到学员申请时选择的游戏项目"
