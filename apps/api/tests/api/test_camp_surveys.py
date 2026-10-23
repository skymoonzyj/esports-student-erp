"""PostgreSQL API authorization journeys for camp completion surveys.

These tests intentionally use the public HTTP routes for every authorization
decision.  The fixture only creates the immutable, server-owned cycle and
snapshot rows needed to put a request inside a real survey window; it never
calls the survey service to bypass the API boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.evaluations.errors import CampSurveyResourceNotFound
from app.evaluations.survey_access import CampSurveyAccessService
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffRating,
    CampSurveyStaffSnapshot,
    CampSurveySubmission,
    CampSurveySupplement,
    CampSurveySupplementRating,
    CampSurveyVersion,
)
from app.evaluations.survey_schemas import CampSurveySubmissionInput
from app.evaluations.survey_service import CampSurveyService
from app.evaluations.survey_window import survey_window_for_end
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.food_preferences.models import FoodCuisineOption
from app.food_preferences.preference_service import (
    FoodPreferenceService,
    ReplaceFoodPreferenceCommand,
)
from app.food_preferences.schemas import CuisineSelectionInput
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.identity.schemas import GuardianCreate, StaffCreate
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.passwords import PasswordService
from app.teaching.models import Cohort, TeachingClass

PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
OPEN_AT = datetime.now(UTC) - timedelta(days=1)
DUE_AT = survey_window_for_end(OPEN_AT).closes_at


def _make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


@dataclass
class CampApiScenario:
    db: Session
    users: dict[str, Any]
    cycle: CampSurveyCycle
    second_cycle: CampSurveyCycle
    unrelated_cycle: CampSurveyCycle
    first_snapshot: CampSurveySnapshotVersion
    staff_rows: tuple[CampSurveyStaffSnapshot, ...]
    extra_staff_person_id: UUID
    cuisine: FoodCuisineOption
    guardian_one_relationship_id: UUID
    guardian_two_account: UserAccount
    guardian_two_password: str

    @property
    def accounts(self) -> dict[str, UserAccount]:
        return self.users["accounts"]

    @property
    def people(self) -> dict[str, Any]:
        return self.users["people"]

    @property
    def staff_snapshot_ids(self) -> tuple[UUID, ...]:
        return tuple(row.id for row in self.staff_rows)

    def staff_ids_for_cycle(self, cycle: CampSurveyCycle) -> tuple[UUID, ...]:
        snapshot = self.db.scalar(
            select(CampSurveySnapshotVersion).where(
                CampSurveySnapshotVersion.cycle_id == cycle.id,
                CampSurveySnapshotVersion.version == cycle.current_snapshot_version,
            )
        )
        assert snapshot is not None
        return tuple(
            row.id
            for row in self.db.scalars(
                select(CampSurveyStaffSnapshot).where(
                    CampSurveyStaffSnapshot.snapshot_version_id == snapshot.id
                )
            )
        )

    def login(self, client: TestClient, key: str) -> str:
        if key == "guardian_two":
            phone = self.guardian_two_phone
            password = self.guardian_two_password
        else:
            phone, password, _ = self.users["credentials"][key]
        response = client.post(
            "/api/v1/auth/login",
            json={"identifier": phone, "password": password},
        )
        assert response.status_code == 200, response.text
        return response.json()["csrf_token"]

    # Set by the fixture after the account is created.  Keeping the credential
    # off the database model makes it impossible for a response projection to
    # accidentally become the source of a login secret.
    guardian_two_phone: str = ""

    def submission_payload(
        self,
        *,
        key: str,
        snapshot_version: int = 1,
        staff_ids: tuple[UUID, ...] | None = None,
        food_version: int = 1,
    ) -> dict[str, object]:
        ids = staff_ids if staff_ids is not None else self.staff_snapshot_ids
        return {
            "idempotency_key": key,
            "expected_snapshot_version": snapshot_version,
            "expected_food_preference_version": food_version,
            "favorite_cuisines": [
                {
                    "cuisine_option_id": str(self.cuisine.id),
                    "name": self.cuisine.name,
                    "custom_name": None,
                }
            ],
            "favorite_dishes": ["宫保鸡丁"],
            "course_training_score": 9,
            "course_training_reason": "训练节奏清晰",
            "life_service_score": 8,
            "life_service_reason": "生活照护及时",
            "service_management_score": 9,
            "service_management_reason": "沟通安排清楚",
            "overall_suggestion": "希望增加复盘时间",
            "staff_ratings": [
                {
                    "staff_snapshot_id": str(staff_id),
                    "score": 8 + (index % 3),
                    "reason": "反馈内容",
                    "suggestion": "保持沟通",
                }
                for index, staff_id in enumerate(ids)
            ],
        }

    def supplement_payload(
        self,
        *,
        key: str,
        staff_id: UUID,
    ) -> dict[str, object]:
        return {
            "idempotency_key": key,
            "expected_snapshot_version": 2,
            "change_reason": "补入漏记工作人员",
            "staff_ratings": [
                {
                    "staff_snapshot_id": str(staff_id),
                    "score": 9,
                    "reason": "补充评价",
                    "suggestion": "继续保持",
                }
            ],
        }


def _add_completed_cycle(
    db: Session,
    *,
    student_person_id: UUID,
    cohort: Cohort,
    teaching_class: TeachingClass,
    admin_id: UUID,
    label: str,
    staff_people: tuple[tuple[UUID, str, str, str, str], ...],
) -> tuple[CampSurveyCycle, CampSurveySnapshotVersion, tuple[CampSurveyStaffSnapshot, ...]]:
    case = AdmissionCase(
        student_person_id=student_person_id,
        status="ready",
        agreed_tuition_cents=100_000,
        created_by_user_id=admin_id,
    )
    db.add(case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=case.id,
        student_person_id=student_person_id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=100_000,
        status="completed",
        confirmed_at=OPEN_AT - timedelta(days=10),
        completed_at=OPEN_AT,
        created_by_user_id=admin_id,
    )
    db.add(enrollment)
    db.flush()
    case.status = "enrolled"
    case.current_enrollment_id = enrollment.id
    db.flush()
    cycle = CampSurveyCycle(
        enrollment_id=enrollment.id,
        student_person_id=student_person_id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        opens_at=OPEN_AT,
        due_at=DUE_AT,
        current_snapshot_version=1,
    )
    db.add(cycle)
    db.flush()
    snapshot = CampSurveySnapshotVersion(
        cycle_id=cycle.id,
        version=1,
        change_reason=f"{label} 初始快照",
        created_by_user_id=admin_id,
    )
    db.add(snapshot)
    db.flush()
    rows = tuple(
        CampSurveyStaffSnapshot(
            snapshot_version_id=snapshot.id,
            staff_person_id=person_id,
            staff_name_snapshot=name,
            role_key_snapshot=role_key,
            role_name_snapshot=role_name,
            role_category=category,
            roles_snapshot=[
                {
                    "role_key": role_key,
                    "role_name": role_name,
                    "role_category": category,
                }
            ],
            service_intervals=[
                {
                    "role_key": role_key,
                    "role_name": role_name,
                    "role_category": category,
                    "from": (OPEN_AT - timedelta(days=10)).isoformat(),
                    "until": OPEN_AT.isoformat(),
                    "source": "api-test",
                    "scope": "class",
                }
            ],
            include_in_summary=True,
        )
        for person_id, name, role_key, role_name, category in staff_people
    )
    db.add_all(rows)
    db.flush()
    return cycle, snapshot, rows


@pytest.fixture
def camp_api_scenario(db: Session, platform_users) -> CampApiScenario:
    users = platform_users
    admin = users["accounts"]["admin"]
    for key in ("self_student", "own_student", "other_student"):
        profile = db.get(StudentProfile, users["people"][key].id)
        assert profile is not None
        profile.status = "active"
    db.flush()

    # Add a second real guardian account.  The first fixture guardian already
    # has a valid relationship to another student, so both accounts remain
    # usable when one relationship is ended below.
    marker = uuid4().hex[:8]
    guardian_two_phone = f"139{uuid4().int % 100_000_000:08d}"
    guardian_two_email = f"guardian-two-{marker}@example.com"
    guardian_two_person = users["identity_service"].create_guardian(
        GuardianCreate(
            full_name="第二位营期监护人",
                identity_number=_make_cn_id("19850102", 152),
            sex="female",
            date_of_birth=date(1985, 1, 2),
            phone=guardian_two_phone,
            email=guardian_two_email,
        )
    )
    created = users["account_service"].create_account(
        AccountCreate(
            person_id=guardian_two_person.id,
            login_phone=guardian_two_phone,
            email=guardian_two_email,
        )
    )
    guardian_two = db.get(UserAccount, created.account.id)
    assert guardian_two is not None
    guardian_two.password_hash = PasswordService().hash(
        "GuardTwo2026Aa"
    )
    guardian_two.force_password_change = False
    db.add(UserRole(user_id=guardian_two.id, role_id=ROLES["guardian"].id))
    db.commit()

    identity = users["identity_service"]
    identity.add_guardian(
        users["people"]["self_student"].id,
        users["people"]["guardian"].id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2020, 1, 1),
        valid_until=None,
    )
    guardian_one_other = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id
            == users["people"]["guardian"].id,
            GuardianRelationship.student_person_id
            == users["people"]["self_student"].id,
        )
    )
    assert guardian_one_other is not None
    identity.add_guardian(
        users["people"]["self_student"].id,
        guardian_two_person.id,
        relationship="father",
        is_primary=False,
        valid_from=date(2020, 1, 1),
        valid_until=None,
    )
    # The original platform guardian is already bound to own_student.  Bind
    # the second guardian there as well to exercise one guardian/many students.
    identity.add_guardian(
        users["people"]["own_student"].id,
        guardian_two_person.id,
        relationship="father",
        is_primary=False,
        valid_from=date(2020, 1, 1),
        valid_until=None,
    )

    cohort = Cohort(
        name=f"API 结营授权营期 {marker}",
        starts_at=OPEN_AT - timedelta(days=10),
        ends_at=OPEN_AT,
        default_check_in_at=OPEN_AT - timedelta(days=10),
        default_check_out_at=OPEN_AT,
        status="completed",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"API 结营授权班 {marker}",
        class_type="standard",
        professional_team_id=None,
        capacity=30,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=admin.id,
    )
    db.add(teaching_class)
    db.flush()

    # An unrelated staff profile is deliberately not placed in any snapshot.
    # It is used to prove that an authenticated staff account cannot enumerate
    # anonymous feedback merely by knowing the endpoint.
    extra_staff = identity.create_staff(
        StaffCreate(
            full_name="补充生活老师",
            identity_number=_make_cn_id("19870102", 152),
            sex="female",
            date_of_birth=date(1987, 1, 2),
            phone=f"137{uuid4().int % 100_000_000:08d}",
            email=f"survey-extra-{marker}@example.com",
            staff_type="life_teacher",
        )
    )
    staff_people = (
        (
            users["people"]["coach"].id,
            "平台教练",
            "primary_coach",
            "主教练",
            "teaching",
        ),
    )
    cycle, snapshot, rows = _add_completed_cycle(
        db,
        student_person_id=users["people"]["self_student"].id,
        cohort=cohort,
        teaching_class=teaching_class,
        admin_id=admin.id,
        label="学员本人",
        staff_people=staff_people,
    )
    second_cycle, _, _ = _add_completed_cycle(
        db,
        student_person_id=users["people"]["own_student"].id,
        cohort=cohort,
        teaching_class=teaching_class,
        admin_id=admin.id,
        label="多学员监护",
        staff_people=staff_people,
    )
    unrelated_cycle, _, _ = _add_completed_cycle(
        db,
        student_person_id=users["people"]["other_student"].id,
        cohort=cohort,
        teaching_class=teaching_class,
        admin_id=admin.id,
        label="无关家庭",
        staff_people=staff_people,
    )

    cuisine = FoodCuisineOption(
        name=f"接口川菜 {marker}",
        normalized_name=f"接口川菜-{marker}".casefold(),
        sort_order=10,
        is_active=True,
        is_other=False,
        created_by_user_id=admin.id,
        updated_by_user_id=admin.id,
    )
    db.add(cuisine)
    db.flush()
    # Seed a stable version-1 profile for each student.  All respondent POSTs
    # then prove that guardian submissions do not silently overwrite it.
    for student_key in ("self_student", "own_student", "other_student"):
        FoodPreferenceService(db).replace(
            users["people"][student_key].id,
            ReplaceFoodPreferenceCommand(
                expected_version=0,
                cuisines=(CuisineSelectionInput(cuisine_option_id=cuisine.id),),
                dishes=("宫保鸡丁",),
                actor_user_id=admin.id,
                source="profile_admin",
                    request_id=f"e2e-food-profile-{student_key}",
            ),
        )
    db.commit()
    return CampApiScenario(
        db=db,
        users=users,
        cycle=cycle,
        second_cycle=second_cycle,
        unrelated_cycle=unrelated_cycle,
        first_snapshot=snapshot,
        staff_rows=rows,
        extra_staff_person_id=extra_staff.id,
        cuisine=cuisine,
        guardian_one_relationship_id=guardian_one_other.id,
        guardian_two_account=guardian_two,
        guardian_two_password="GuardTwo2026Aa",
        guardian_two_phone=guardian_two_phone,
    )


def _private_body(response) -> dict[str, object]:
    assert response.headers.get("cache-control") == "no-store, private"
    assert response.headers.get("pragma") == "no-cache"
    assert response.headers.get("x-content-type-options") == "nosniff"
    body = dict(response.json())
    body.pop("request_id", None)
    return body


def _counts(db: Session) -> dict[str, int]:
    models = {
        "submissions": CampSurveySubmission,
        "versions": CampSurveyVersion,
        "ratings": CampSurveyStaffRating,
        "supplements": CampSurveySupplement,
        "supplement_ratings": CampSurveySupplementRating,
        "idempotency": EvaluationIdempotencyKey,
        "audit": AuditEvent,
    }
    return {
        name: int(db.scalar(select(func.count(model.id))) or 0)
        for name, model in models.items()
    }


def _csrf_headers(csrf: str, request_id: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf, "X-Request-ID": request_id}


def _append_snapshot_v2(
    scenario: CampApiScenario,
    *,
    added_include_in_summary: bool = True,
) -> CampSurveyStaffSnapshot:
    db = scenario.db
    cycle = db.get(CampSurveyCycle, scenario.cycle.id)
    assert cycle is not None
    old_rows = tuple(
        scenario.db.scalars(
            select(CampSurveyStaffSnapshot).where(
                CampSurveyStaffSnapshot.snapshot_version_id
                == scenario.first_snapshot.id
            )
        )
    )
    cycle.current_snapshot_version = 2
    snapshot = CampSurveySnapshotVersion(
        cycle_id=cycle.id,
        version=2,
        change_reason="管理员修正服务历史，补入工作人员",
        created_by_user_id=scenario.accounts["admin"].id,
    )
    db.add(snapshot)
    db.flush()
    db.add_all(
        CampSurveyStaffSnapshot(
            snapshot_version_id=snapshot.id,
            staff_person_id=row.staff_person_id,
            staff_name_snapshot=row.staff_name_snapshot,
            role_key_snapshot=row.role_key_snapshot,
            role_name_snapshot=row.role_name_snapshot,
            role_category=row.role_category,
            roles_snapshot=row.roles_snapshot,
            service_intervals=row.service_intervals,
            include_in_summary=row.include_in_summary,
        )
        for row in old_rows
    )
    added = CampSurveyStaffSnapshot(
        snapshot_version_id=snapshot.id,
        staff_person_id=scenario.extra_staff_person_id,
        staff_name_snapshot="补充生活老师",
        role_key_snapshot="life_teacher",
        role_name_snapshot="生活老师",
        role_category="life_service",
        roles_snapshot=[
            {
                "role_key": "life_teacher",
                "role_name": "生活老师",
                "role_category": "life_service",
            }
        ],
        service_intervals=[
            {
                "role_key": "life_teacher",
                "role_name": "生活老师",
                "role_category": "life_service",
                "from": (OPEN_AT - timedelta(days=5)).isoformat(),
                "until": OPEN_AT.isoformat(),
                "source": "api-test-correction",
                "scope": "cohort",
            }
        ],
        include_in_summary=added_include_in_summary,
    )
    db.add(added)
    db.commit()
    return added


def test_student_and_each_guardian_submit_independently_and_one_guardian_has_many_students(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    student_csrf = scenario.login(db_client, "self_student")
    student_response = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(student_csrf, "camp-api-student-submit"),
        json=scenario.submission_payload(key="student-submit-0001"),
    )
    assert student_response.status_code == 201, student_response.text
    assert student_response.json()["version"] == 1

    guardian_one_csrf = scenario.login(db_client, "guardian")
    guardian_one_response = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(guardian_one_csrf, "camp-api-guardian-one-submit"),
        json=scenario.submission_payload(key="guardian-one-submit-0001"),
    )
    assert guardian_one_response.status_code == 201, guardian_one_response.text

    guardian_two_csrf = scenario.login(db_client, "guardian_two")
    guardian_two_response = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(guardian_two_csrf, "camp-api-guardian-two-submit"),
        json=scenario.submission_payload(key="guardian-two-submit-0001"),
    )
    assert guardian_two_response.status_code == 201, guardian_two_response.text

    # The same first guardian is independently authorized for a second
    # student's cycle.  A cycle/respondent pair, rather than an account-wide
    # key, is the uniqueness boundary.
    guardian_one_csrf = scenario.login(db_client, "guardian")
    second_student_response = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.second_cycle.id}/submissions",
        headers=_csrf_headers(guardian_one_csrf, "camp-api-guardian-many-submit"),
        json=scenario.submission_payload(
            key="guardian-many-submit-0001",
            staff_ids=scenario.staff_ids_for_cycle(scenario.second_cycle),
        ),
    )
    assert second_student_response.status_code == 201, second_student_response.text

    submissions = list(
        scenario.db.scalars(
            select(CampSurveySubmission).where(
                CampSurveySubmission.cycle_id == scenario.cycle.id
            )
        )
    )
    assert {row.respondent_person_id for row in submissions} == {
        scenario.people["self_student"].id,
        scenario.people["guardian"].id,
        scenario.guardian_two_account.person_id,
    }
    assert all(row.latest_version == 1 for row in submissions)
    assert scenario.db.scalar(
        select(func.count(CampSurveySubmission.id)).where(
            CampSurveySubmission.cycle_id == scenario.second_cycle.id,
            CampSurveySubmission.respondent_person_id
            == scenario.people["guardian"].id,
        )
    ) == 1


def test_adult_relationship_remains_visible_until_ended_and_unrelated_family_is_private(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    # self_student is an adult; the active relationship, not age, controls
    # this survey authorization.
    assert scenario.people["self_student"].date_of_birth < date(2008, 8, 13)
    guardian_csrf = scenario.login(db_client, "guardian")
    context = db_client.get("/api/v1/evaluations/surveys/context")
    assert context.status_code == 200, context.text
    context_item = next(
        item for item in context.json()
        if item["cycle_id"] == str(scenario.cycle.id)
    )
    assert context_item["student_name"] == scenario.people["self_student"].full_name

    # End only guardian one's relationship through the real admin API.  The
    # second guardian remains active, so ending this relationship must not
    # disable the student account or alter the cycle itself.
    admin_csrf = scenario.login(db_client, "admin")
    ended = db_client.post(
        f"/api/v1/people/{scenario.people['self_student'].id}/guardians/"
        f"{scenario.guardian_one_relationship_id}/end",
        headers=_csrf_headers(admin_csrf, "camp-api-end-guardian-one"),
        json={"reason": "关系解除测试"},
    )
    assert ended.status_code == 200, ended.text

    guardian_csrf = scenario.login(db_client, "guardian")
    denied = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(guardian_csrf, "camp-api-ended-guardian"),
        json=scenario.submission_payload(key="ended-guardian-submit"),
    )
    unrelated = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.unrelated_cycle.id}/submissions",
        headers=_csrf_headers(guardian_csrf, "camp-api-unrelated-family"),
        json=scenario.submission_payload(key="unrelated-family-submit"),
    )
    missing = db_client.post(
        f"/api/v1/evaluations/surveys/{uuid4()}/submissions",
        headers=_csrf_headers(guardian_csrf, "camp-api-missing-cycle"),
        json=scenario.submission_payload(key="missing-cycle-submit"),
    )

    assert denied.status_code == unrelated.status_code == missing.status_code == 404
    assert _private_body(denied) == _private_body(unrelated) == _private_body(missing)
    assert _private_body(denied)["code"] == "evaluation_resource_not_found"


def test_submission_rechecks_guardian_relationship_inside_write_transaction(
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    guardian_account = scenario.accounts["guardian"]
    occurred_at = OPEN_AT + timedelta(hours=1)
    respondent = CampSurveyAccessService(scenario.db).resolve_respondent(
        cycle_id=scenario.cycle.id,
        account_id=guardian_account.id,
        occurred_at=occurred_at,
    )
    assert respondent.guardian_relationship_id is not None

    relationship = scenario.db.get(
        GuardianRelationship,
        respondent.guardian_relationship_id,
    )
    assert relationship is not None
    relationship.ended_at = occurred_at - timedelta(seconds=1)
    scenario.db.commit()

    payload = CampSurveySubmissionInput.model_validate(
        scenario.submission_payload(key="stale-guardian-submit")
    )
    with pytest.raises(CampSurveyResourceNotFound):
        CampSurveyService(scenario.db).submit(
            scenario.cycle.id,
            payload,
            actor_user_id=guardian_account.id,
            actor_person_id=respondent.person_id,
            student_person_id=respondent.student_person_id,
            respondent_kind=respondent.respondent_kind,
            guardian_relationship_id=respondent.guardian_relationship_id,
            guardian_relationship_snapshot=respondent.guardian_relationship_snapshot,
            occurred_at=occurred_at,
        )
    assert scenario.db.scalar(
        select(func.count(CampSurveySubmission.id)).where(
            CampSurveySubmission.cycle_id == scenario.cycle.id,
            CampSurveySubmission.respondent_person_id == guardian_account.person_id,
        )
    ) == 0


def test_submission_rechecks_soft_deleted_respondent_inside_write_transaction(
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    guardian_account = scenario.accounts["guardian"]
    occurred_at = OPEN_AT + timedelta(hours=1)
    respondent = CampSurveyAccessService(scenario.db).resolve_respondent(
        cycle_id=scenario.cycle.id,
        account_id=guardian_account.id,
        occurred_at=occurred_at,
    )
    assert respondent.person_id == guardian_account.person_id

    person = scenario.db.get(Person, respondent.person_id)
    assert person is not None
    person.deleted_at = occurred_at
    scenario.db.commit()

    before = _counts(scenario.db)
    payload = CampSurveySubmissionInput.model_validate(
        scenario.submission_payload(key="soft-deleted-guardian-submit")
    )
    with pytest.raises(CampSurveyResourceNotFound):
        CampSurveyService(scenario.db).submit(
            scenario.cycle.id,
            payload,
            actor_user_id=guardian_account.id,
            actor_person_id=respondent.person_id,
            student_person_id=respondent.student_person_id,
            respondent_kind=respondent.respondent_kind,
            guardian_relationship_id=respondent.guardian_relationship_id,
            guardian_relationship_snapshot=respondent.guardian_relationship_snapshot,
            occurred_at=occurred_at,
        )
    assert _counts(scenario.db) == before


def test_supplement_is_authorized_once_replay_is_idempotent_and_unrelated_staff_is_denied(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    student_csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(student_csrf, "camp-api-supplement-seed"),
        json=scenario.submission_payload(key="supplement-seed-0001"),
    )
    assert submitted.status_code == 201, submitted.text
    submission_id = UUID(submitted.json()["submission_id"])
    added = _append_snapshot_v2(scenario)

    supplement_payload = scenario.supplement_payload(
        key="supplement-submit-0001",
        staff_id=added.id,
    )
    first = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submission_id}/supplement",
        headers=_csrf_headers(student_csrf, "camp-api-supplement-first"),
        json=supplement_payload,
    )
    replay = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submission_id}/supplement",
        headers=_csrf_headers(student_csrf, "camp-api-supplement-replay"),
        json=supplement_payload,
    )
    assert first.status_code == replay.status_code == 201, f"{first.text} / {replay.text}"
    assert first.json()["idempotent"] is False
    assert replay.json()["idempotent"] is True
    assert replay.json()["supplement_id"] == first.json()["supplement_id"]
    assert scenario.db.scalar(
        select(func.count(CampSurveySupplementRating.id)).where(
            CampSurveySupplementRating.supplement_id
            == UUID(first.json()["supplement_id"])
        )
    ) == 1

    # A staff account with no matching roster row cannot obtain even an empty
    # anonymous projection.  This is deliberately an API capability check,
    # not a client-side filtering assertion.
    unrelated_staff_csrf = scenario.login(db_client, "customer_service")
    feedback = db_client.get(
        "/api/v1/evaluations/surveys/my-feedback",
        headers={"X-Request-ID": "camp-api-unrelated-staff-feedback"},
    )
    assert feedback.status_code == 403, feedback.text
    assert feedback.json()["code"] == "permission_denied"

    before = _counts(scenario.db)
    rejected = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submission_id}/supplement",
        headers=_csrf_headers(unrelated_staff_csrf, "camp-api-rejected-supplement"),
        json=scenario.supplement_payload(
            key="rejected-supplement-0001", staff_id=added.id
        ),
    )
    after = _counts(scenario.db)
    assert rejected.status_code == 404
    assert _private_body(rejected)["code"] == "evaluation_resource_not_found"
    assert after == before


def test_context_rehydrates_latest_submission_and_hides_completed_supplements(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(csrf, "camp-api-context-rehydrate-submit"),
        json=scenario.submission_payload(key="camp-api-context-rehydrate-submit"),
    )
    assert submitted.status_code == 201, submitted.text
    added = _append_snapshot_v2(scenario)

    before = db_client.get(
        "/api/v1/evaluations/surveys/context",
        headers={"X-Request-ID": "camp-api-context-rehydrate-before"},
    )
    assert before.status_code == 200, before.text
    item = next(row for row in before.json() if row["cycle_id"] == str(scenario.cycle.id))
    assert item["latest_submission"]["version"] == 1
    assert item["latest_submission"]["staff_ratings"]
    assert [row["id"] for row in item["supplement_staff"]] == [str(added.id)]

    supplement = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submitted.json()['submission_id']}/supplement",
        headers=_csrf_headers(csrf, "camp-api-context-rehydrate-supplement"),
        json=scenario.supplement_payload(
            key="camp-api-context-rehydrate-supplement",
            staff_id=added.id,
        ),
    )
    assert supplement.status_code == 201, supplement.text
    after = db_client.get(
        "/api/v1/evaluations/surveys/context",
        headers={"X-Request-ID": "camp-api-context-rehydrate-after"},
    )
    assert after.status_code == 200, after.text
    after_item = next(row for row in after.json() if row["cycle_id"] == str(scenario.cycle.id))
    assert after_item["supplement_staff"] == []


def test_context_staff_tie_breaker_uses_staff_person_uuid(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    person_ids = sorted(
        (
            scenario.people["admin"].id,
            scenario.people["customer_service"].id,
        ),
        key=str,
    )
    snapshot = CampSurveySnapshotVersion(
        cycle_id=scenario.cycle.id,
        version=2,
        change_reason="稳定排序回归快照",
        created_by_user_id=scenario.accounts["admin"].id,
    )
    scenario.db.add(snapshot)
    scenario.db.flush()
    lower_person_row = CampSurveyStaffSnapshot(
        id=UUID("ffffffff-ffff-4fff-8fff-fffffffffff2"),
        snapshot_version_id=snapshot.id,
        staff_person_id=person_ids[0],
        staff_name_snapshot="相同姓名",
        role_key_snapshot="same_role",
        role_name_snapshot="相同角色",
        role_category="operations",
        roles_snapshot=[
            {
                "role_key": "same_role",
                "role_name": "相同角色",
                "role_category": "operations",
            }
        ],
        service_intervals=[],
        include_in_summary=True,
    )
    higher_person_row = CampSurveyStaffSnapshot(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        snapshot_version_id=snapshot.id,
        staff_person_id=person_ids[1],
        staff_name_snapshot="相同姓名",
        role_key_snapshot="same_role",
        role_name_snapshot="相同角色",
        role_category="operations",
        roles_snapshot=[
            {
                "role_key": "same_role",
                "role_name": "相同角色",
                "role_category": "operations",
            }
        ],
        service_intervals=[],
        include_in_summary=True,
    )
    scenario.db.add_all((lower_person_row, higher_person_row))
    scenario.cycle.current_snapshot_version = 2
    scenario.db.commit()

    scenario.login(db_client, "self_student")
    response = db_client.get(
        "/api/v1/evaluations/surveys/context",
        headers={"X-Request-ID": "camp-api-context-stable-person-order"},
    )

    assert response.status_code == 200, response.text
    item = next(
        row for row in response.json() if row["cycle_id"] == str(scenario.cycle.id)
    )
    assert [row["id"] for row in item["staff"]] == [
        str(lower_person_row.id),
        str(higher_person_row.id),
    ]


def test_context_and_supplement_reject_staff_excluded_by_current_snapshot(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(csrf, "camp-api-excluded-staff-submit"),
        json=scenario.submission_payload(key="camp-api-excluded-staff-submit"),
    )
    assert submitted.status_code == 201, submitted.text
    excluded = _append_snapshot_v2(
        scenario,
        added_include_in_summary=False,
    )

    context = db_client.get(
        "/api/v1/evaluations/surveys/context",
        headers={"X-Request-ID": "camp-api-excluded-staff-context"},
    )
    assert context.status_code == 200, context.text
    item = next(
        row for row in context.json() if row["cycle_id"] == str(scenario.cycle.id)
    )
    assert str(excluded.id) not in {row["id"] for row in item["staff"]}
    assert item["supplement_staff"] == []
    assert item["can_supplement"] is False

    before_rejected_supplement = _counts(scenario.db)
    rejected = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submitted.json()['submission_id']}/supplement",
        headers=_csrf_headers(csrf, "camp-api-excluded-staff-supplement"),
        json=scenario.supplement_payload(
            key="camp-api-excluded-staff-supplement",
            staff_id=excluded.id,
        ),
    )
    assert rejected.status_code == 422
    assert _counts(scenario.db) == before_rejected_supplement


def test_staff_feedback_exposes_only_role_related_overall_fields(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    student_csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(student_csrf, "camp-api-staff-summary-seed"),
        json=scenario.submission_payload(key="camp-api-staff-summary-seed"),
    )
    assert submitted.status_code == 201, submitted.text

    scenario.login(db_client, "coach")
    context_response = db_client.get(
        "/api/v1/evaluations/context",
        headers={"X-Request-ID": "camp-api-staff-survey-context"},
    )
    assert context_response.status_code == 200, context_response.text
    assert context_response.json()["surveys_available"] is True
    response = db_client.get(
        "/api/v1/evaluations/surveys/my-feedback",
        headers={"X-Request-ID": "camp-api-staff-summary-read"},
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["project_id"] == str(PROJECT_ID)
    assert item["role_key"] == "primary_coach"
    assert item["role_name"] == "主教练"
    assert item["course_training_score"] == 9
    assert item["course_training_reason"] == "训练节奏清晰"
    assert item["team_suggestion"] == "希望增加复盘时间"
    assert item["life_service_score"] is None
    assert item["life_service_reason"] is None
    assert item["service_management_score"] is None
    assert item["service_management_reason"] is None


def test_unfiltered_staff_feedback_filters_each_denied_enrollment_row(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    first_csrf = scenario.login(db_client, "self_student")
    first = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(first_csrf, "camp-api-staff-scope-first"),
        json=scenario.submission_payload(key="staff-scope-first"),
    )
    assert first.status_code == 201, first.text

    second_csrf = scenario.login(db_client, "guardian_two")
    second = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.second_cycle.id}/submissions",
        headers=_csrf_headers(second_csrf, "camp-api-staff-scope-second"),
        json=scenario.submission_payload(
            key="staff-scope-second",
            staff_ids=scenario.staff_ids_for_cycle(scenario.second_cycle),
        ),
    )
    assert second.status_code == 201, second.text

    scenario.db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["coach"].id,
            permission_id=PERMISSIONS["evaluation_center.access"].id,
            effect="deny",
            scope_kind="enrollment",
            scope_id=scenario.cycle.enrollment_id,
        )
    )
    scenario.db.commit()

    scenario.login(db_client, "coach")
    before = _counts(scenario.db)
    response = db_client.get(
        "/api/v1/evaluations/surveys/my-feedback",
        headers={"X-Request-ID": "camp-api-staff-scope-unfiltered"},
    )
    after = _counts(scenario.db)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [item["cycle_id"] for item in response.json()["items"]] == [
        str(scenario.second_cycle.id)
    ]
    assert after == before


def test_rejected_submission_has_no_business_idempotency_or_audit_writes(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    before = _counts(scenario.db)
    customer_service_csrf = scenario.login(db_client, "customer_service")
    response = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(customer_service_csrf, "camp-api-rejected-submit"),
        json=scenario.submission_payload(key="rejected-submit-0001"),
    )
    after = _counts(scenario.db)

    assert response.status_code == 404
    assert _private_body(response)["code"] == "evaluation_resource_not_found"
    assert after == before


def test_revision_succeeds_once_then_second_revision_is_stable_conflict(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(csrf, "camp-api-revision-seed"),
        json=scenario.submission_payload(key="camp-api-revision-seed-payload"),
    )
    assert submitted.status_code == 201, submitted.text
    submission_id = submitted.json()["submission_id"]

    first = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submission_id}/revisions",
        headers=_csrf_headers(csrf, "camp-api-revision-first"),
        json=scenario.submission_payload(
            key="camp-api-revision-first-payload",
            food_version=1,
        ),
    )
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 2

    before = _counts(scenario.db)
    second = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submission_id}/revisions",
        headers=_csrf_headers(csrf, "camp-api-revision-second"),
        json=scenario.submission_payload(
            key="camp-api-revision-second-payload",
            food_version=1,
        ),
    )
    after = _counts(scenario.db)
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "evaluation_survey_revision_conflict"
    assert after == before
    assert scenario.db.scalar(
        select(func.count(CampSurveyVersion.id)).where(
            CampSurveyVersion.submission_id == UUID(submission_id)
        )
    ) == 2


def test_revision_after_due_at_is_rejected_without_writes(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(csrf, "camp-api-revision-cutoff-seed"),
        json=scenario.submission_payload(key="camp-api-revision-cutoff-seed-payload"),
    )
    assert submitted.status_code == 201, submitted.text
    submission_id = submitted.json()["submission_id"]
    cycle = scenario.db.get(CampSurveyCycle, scenario.cycle.id)
    assert cycle is not None
    cycle.due_at = datetime.now(UTC) - timedelta(seconds=1)
    scenario.db.commit()

    before = _counts(scenario.db)
    response = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submission_id}/revisions",
        headers=_csrf_headers(csrf, "camp-api-revision-cutoff"),
        json=scenario.submission_payload(
            key="camp-api-revision-cutoff-payload",
            food_version=1,
        ),
    )
    after = _counts(scenario.db)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "evaluation_survey_window_closed"
    assert after == before


def test_staff_feedback_rechecks_live_access_after_permission_revocation(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    scenario.login(db_client, "coach")
    initial = db_client.get(
        "/api/v1/evaluations/surveys/my-feedback",
        headers={"X-Request-ID": "camp-api-staff-feedback-before-revoke"},
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["items"] == []

    scenario.db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["coach"].id,
            permission_id=PERMISSIONS["evaluation_center.access"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    scenario.db.commit()
    denied = db_client.get(
        "/api/v1/evaluations/surveys/my-feedback",
        headers={"X-Request-ID": "camp-api-staff-feedback-after-revoke"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "permission_denied"


def test_identity_only_admin_can_read_real_name_list_but_cannot_print(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    account = scenario.accounts["customer_service"]
    scenario.db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["camp_surveys.identity.read"].id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
        )
    )
    scenario.db.commit()

    scenario.login(db_client, "customer_service")
    before = _counts(scenario.db)
    query = db_client.get(
        "/api/v1/evaluations/admin/surveys?identity=real_name",
        headers={"X-Request-ID": "camp-api-real-name-query-without-print"},
    )
    after_query = _counts(scenario.db)
    print_response = db_client.get(
        "/api/v1/evaluations/admin/surveys/print-projection?identity=real_name",
        headers={"X-Request-ID": "camp-api-real-name-print-without-print"},
    )
    after = _counts(scenario.db)

    assert query.status_code == 200, query.text
    assert query.json()["items"] == []
    assert print_response.status_code == 403, print_response.text
    assert print_response.json()["code"] == "permission_denied"
    assert after_query == {**before, "audit": before["audit"] + 1}
    assert after == after_query
    audit = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.request_id == "camp-api-real-name-query-without-print",
            AuditEvent.action == "camp_surveys.identity.read",
            AuditEvent.outcome == "allowed",
        )
    )
    assert audit is not None


def test_real_name_print_preserves_enrollment_scoped_identity_after_row_filtering(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    first_csrf = scenario.login(db_client, "self_student")
    first = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(first_csrf, "camp-api-print-enrollment-first"),
        json=scenario.submission_payload(key="print-enrollment-first"),
    )
    assert first.status_code == 201, first.text
    second_csrf = scenario.login(db_client, "guardian_two")
    second = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.second_cycle.id}/submissions",
        headers=_csrf_headers(second_csrf, "camp-api-print-enrollment-second"),
        json=scenario.submission_payload(
            key="print-enrollment-second",
            staff_ids=scenario.staff_ids_for_cycle(scenario.second_cycle),
        ),
    )
    assert second.status_code == 201, second.text

    account = scenario.accounts["customer_service"]
    scenario.db.add_all(
        [
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["camp_surveys.print"].id,
                effect="allow",
                scope_kind="cohort",
                scope_id=scenario.cycle.cohort_id,
            ),
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["camp_surveys.identity.read"].id,
                effect="allow",
                scope_kind="enrollment",
                scope_id=scenario.cycle.enrollment_id,
            ),
        ]
    )
    scenario.db.commit()

    scenario.login(db_client, "customer_service")
    response = db_client.get(
        "/api/v1/evaluations/admin/surveys/print-projection?identity=real_name",
        headers={"X-Request-ID": "camp-api-print-enrollment-scope"},
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["item_count"] == 1
    assert [detail["respondent_person_id"] for detail in items[0]["details"]] == [
        str(scenario.people["self_student"].id)
    ]


def test_real_name_print_does_not_combine_permissions_from_different_enrollments(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    first_csrf = scenario.login(db_client, "self_student")
    first = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(first_csrf, "camp-api-cross-scope-first"),
        json=scenario.submission_payload(key="cross-scope-first"),
    )
    assert first.status_code == 201, first.text

    second_csrf = scenario.login(db_client, "guardian_two")
    second = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.second_cycle.id}/submissions",
        headers=_csrf_headers(second_csrf, "camp-api-cross-scope-second"),
        json=scenario.submission_payload(
            key="cross-scope-second",
            staff_ids=scenario.staff_ids_for_cycle(scenario.second_cycle),
        ),
    )
    assert second.status_code == 201, second.text

    account = scenario.accounts["customer_service"]
    scenario.db.add_all(
        [
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["camp_surveys.print"].id,
                effect="allow",
                scope_kind="enrollment",
                scope_id=scenario.cycle.enrollment_id,
            ),
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["camp_surveys.identity.read"].id,
                effect="allow",
                scope_kind="enrollment",
                scope_id=scenario.second_cycle.enrollment_id,
            ),
        ]
    )
    scenario.db.commit()

    scenario.login(db_client, "customer_service")
    before = _counts(scenario.db)
    response = db_client.get(
        "/api/v1/evaluations/admin/surveys/print-projection?identity=real_name",
        headers={"X-Request-ID": "camp-api-cross-scope-print"},
    )
    after = _counts(scenario.db)

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "permission_denied"
    assert after == before
    success_audit = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.request_id == "camp-api-cross-scope-print",
            AuditEvent.action == "camp_surveys.print",
            AuditEvent.outcome == "allowed",
        )
    )
    assert success_audit is None


def test_admin_print_projection_serializes_nested_details_for_both_identity_modes(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    student_csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(student_csrf, "camp-api-print-projection-seed"),
        json=scenario.submission_payload(key="print-projection-seed-payload"),
    )
    assert submitted.status_code == 201, submitted.text

    scenario.login(db_client, "admin")
    hidden = db_client.get(
        "/api/v1/evaluations/admin/surveys/print-projection?identity=hidden",
        headers={"X-Request-ID": "camp-api-print-projection-hidden"},
    )
    real_name = db_client.get(
        "/api/v1/evaluations/admin/surveys/print-projection?identity=real_name",
        headers={"X-Request-ID": "camp-api-print-projection-real-name"},
    )

    assert hidden.status_code == 200, hidden.text
    assert real_name.status_code == 200, real_name.text
    hidden_item = hidden.json()["items"][0]
    real_name_item = real_name.json()["items"][0]
    assert hidden_item["staff_person_id"] == str(scenario.people["coach"].id)
    assert hidden_item["staff_name"] == "平台教练"
    assert hidden_item["details"]
    assert set(hidden_item["details"][0]) == {
        "score",
        "reason",
        "suggestion",
        "ordinal",
        "respondent_person_id",
        "respondent_name",
        "respondent_kind",
    }
    assert hidden_item["details"][0]["respondent_person_id"] is None
    assert hidden_item["details"][0]["respondent_name"] is None
    assert real_name_item["staff_person_id"] == str(scenario.people["coach"].id)
    assert real_name_item["staff_name"] == "平台教练"
    assert real_name_item["details"][0]["respondent_person_id"] == str(
        scenario.people["self_student"].id
    )


@pytest.mark.parametrize("filter_kind", ["cohort", "project", "staff"])
def test_real_name_print_success_audit_has_only_the_frozen_metadata_keys(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
    filter_kind: str,
) -> None:
    scenario = camp_api_scenario
    student_csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(student_csrf, "camp-api-print-audit-seed"),
        json=scenario.submission_payload(key="print-audit-seed"),
    )
    assert submitted.status_code == 201, submitted.text

    scenario.login(db_client, "admin")
    request_id = f"camp-api-real-name-print-audit-keys-{filter_kind}"
    if filter_kind == "cohort":
        filter_query = f"cohort_id={scenario.cycle.cohort_id}"
        expected_filter_metadata: dict[str, object] = {
            "cohort_id": str(scenario.cycle.cohort_id),
        }
    elif filter_kind == "project":
        filter_query = f"project_id={PROJECT_ID}"
        expected_filter_metadata = {}
    else:
        filter_query = f"staff_id={scenario.people['coach'].id}"
        expected_filter_metadata = {
            "staff_person_id": str(scenario.people["coach"].id),
        }
    response = db_client.get(
        "/api/v1/evaluations/admin/surveys/print-projection"
        f"?identity=real_name&{filter_query}",
        headers={"X-Request-ID": request_id},
    )
    assert response.status_code == 200, response.text

    audit = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.request_id == request_id,
            AuditEvent.action == "camp_surveys.print",
            AuditEvent.outcome == "allowed",
        )
    )
    assert audit is not None
    assert audit.event_metadata == {
        "identity_mode": "real_name",
        "snapshot_version": scenario.cycle.current_snapshot_version,
        "item_count": len(response.json()["items"]),
        **expected_filter_metadata,
    }


def test_admin_identity_mode_hides_only_respondent_identity(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    student_csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(student_csrf, "camp-api-admin-identity-seed"),
        json=scenario.submission_payload(key="admin-identity-seed"),
    )
    assert submitted.status_code == 201, submitted.text

    scenario.login(db_client, "admin")
    hidden = db_client.get(
        "/api/v1/evaluations/admin/surveys?identity=hidden",
        headers={"X-Request-ID": "camp-api-admin-hidden"},
    )
    real_name = db_client.get(
        "/api/v1/evaluations/admin/surveys?identity=real_name",
        headers={"X-Request-ID": "camp-api-admin-real-name"},
    )
    assert hidden.status_code == real_name.status_code == 200
    hidden_item = hidden.json()["items"][0]
    real_item = real_name.json()["items"][0]
    assert hidden_item["staff_name"] == "平台教练"
    assert hidden_item["staff_person_id"] == str(scenario.people["coach"].id)
    assert hidden_item["staff_snapshot_id"] is None
    assert hidden_item["respondent_person_id"] is None
    assert hidden_item["respondent_name"] is None
    assert hidden_item["course_training_score"] is None
    assert real_item["respondent_person_id"] == str(scenario.people["self_student"].id)
    assert real_item["respondent_name"] == scenario.people["self_student"].full_name
    assert real_item["respondent_kind"] == "self"
    assert real_item["course_training_score"] == 9
    assert real_item["life_service_score"] == 8
    assert real_item["service_management_score"] == 9
    assert real_item["overall_suggestion"] == "希望增加复盘时间"
    assert real_item["favorite_dishes"] == ["宫保鸡丁"]
    audit_rows = list(
        scenario.db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "camp_surveys.identity.read",
                AuditEvent.request_id == "camp-api-admin-real-name",
            )
        )
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].event_metadata["identity_mode"] == "real_name"
    assert audit_rows[0].event_metadata["snapshot_version"] == 1


def test_scoped_survey_permission_is_usable_and_unfiltered_reads_honor_deny(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    account = scenario.accounts["customer_service"]
    permission = PERMISSIONS["camp_surveys.print"]
    scenario.db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=permission.id,
            effect="allow",
            scope_kind="cohort",
            scope_id=scenario.cycle.cohort_id,
        )
    )
    scenario.db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["evaluation_center.access"].id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
        )
    )
    scenario.db.commit()

    scenario.login(db_client, "customer_service")
    scoped = db_client.get(
        f"/api/v1/evaluations/admin/surveys?cohort_id={scenario.cycle.cohort_id}",
        headers={"X-Request-ID": "camp-api-scoped-print"},
    )
    assert scoped.status_code == 200, scoped.text
    scoped_context = db_client.get(
        "/api/v1/evaluations/context",
        headers={"X-Request-ID": "camp-api-scoped-survey-context"},
    )
    assert scoped_context.status_code == 200, scoped_context.text
    assert scoped_context.json()["surveys_available"] is True

    # A global allow must not bypass an explicit deny for the same cohort when
    # the caller omits filters.  The response remains a safe empty projection.
    scenario.db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=permission.id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
        )
    )
    scenario.db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=permission.id,
            effect="deny",
            scope_kind="cohort",
            scope_id=scenario.cycle.cohort_id,
        )
    )
    scenario.db.commit()
    unfiltered = db_client.get(
        "/api/v1/evaluations/admin/surveys",
        headers={"X-Request-ID": "camp-api-unfiltered-deny"},
    )
    unfiltered_print = db_client.get(
        "/api/v1/evaluations/admin/surveys/print-projection?identity=hidden",
        headers={"X-Request-ID": "camp-api-unfiltered-print-deny"},
    )
    assert unfiltered.status_code == 200, unfiltered.text
    assert unfiltered.json()["items"] == []
    assert unfiltered_print.status_code == 200, unfiltered_print.text
    assert unfiltered_print.json()["items"] == []


def test_closed_empty_cycle_does_not_advertise_survey_capability(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    cycle = scenario.db.get(CampSurveyCycle, scenario.cycle.id)
    assert cycle is not None
    cycle.opens_at = datetime(2026, 7, 1, tzinfo=UTC)
    cycle.due_at = datetime(2026, 7, 2, tzinfo=UTC)
    scenario.db.commit()

    scenario.login(db_client, "self_student")
    response = db_client.get(
        "/api/v1/evaluations/context",
        headers={"X-Request-ID": "camp-api-closed-survey-context"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["surveys_available"] is False


def test_survey_list_and_detail_routes_share_respondent_authorization(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    scenario.login(db_client, "self_student")
    listed = db_client.get(
        f"/api/v1/evaluations/surveys?student_id={scenario.cycle.student_person_id}",
        headers={"X-Request-ID": "camp-api-survey-list"},
    )
    detail = db_client.get(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}",
        headers={"X-Request-ID": "camp-api-survey-detail"},
    )
    unrelated = db_client.get(
        f"/api/v1/evaluations/surveys/{scenario.unrelated_cycle.id}",
        headers={"X-Request-ID": "camp-api-survey-unrelated-detail"},
    )
    assert listed.status_code == detail.status_code == 200
    assert listed.json()[0]["cycle_id"] == str(scenario.cycle.id)
    assert detail.json()["cycle_id"] == str(scenario.cycle.id)
    assert unrelated.status_code == 404
