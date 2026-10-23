from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.evaluations.survey_query_service import CampSurveyQueryService


@dataclass(frozen=True)
class SurveyQuerySeed:
    cohort_id: UUID
    project_one_id: UUID
    project_two_id: UUID
    staff_id: UUID
    staff_name: str


def _insert_person(
    db: Session,
    person_id: UUID,
    name: str,
    *,
    sex: str = "female",
    birth_date: date = date(1990, 1, 2),
) -> None:
    db.execute(
        text(
            "INSERT INTO people ("
            "id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth"
            ") VALUES (:id, :name, 'camp-survey-query-ciphertext', :digest, :sex, :birth_date)"
        ),
        {
            "id": person_id,
            "name": name,
            "digest": f"camp-survey-query-{person_id}",
            "sex": sex,
            "birth_date": birth_date,
        },
    )


def _insert_account(db: Session, account_id: UUID, person_id: UUID) -> None:
    db.execute(
        text(
            "INSERT INTO user_accounts ("
            "id, person_id, login_phone_digest, email_normalized, password_hash, "
            "status, force_password_change, failed_login_count"
            ") VALUES (:id, :person_id, :phone_digest, :email, 'camp-survey-query-password', "
            "'active', false, 0)"
        ),
        {
            "id": account_id,
            "person_id": person_id,
            "phone_digest": f"camp-survey-query-phone-{account_id}",
            "email": f"camp-survey-query-{account_id}@example.test",
        },
    )


def _insert_project(db: Session, project_id: UUID, actor_id: UUID, name: str) -> None:
    db.execute(
        text(
            "INSERT INTO game_projects "
            "(id, name, sort_order, is_active, created_by_user_id) "
            "VALUES (:id, :name, :sort_order, true, :actor_id)"
        ),
        {"id": project_id, "name": name, "sort_order": 100, "actor_id": actor_id},
    )


def _insert_cohort(db: Session, cohort_id: UUID, actor_id: UUID) -> None:
    starts_at = datetime(2026, 8, 1, 8, tzinfo=UTC)
    db.execute(
        text(
            "INSERT INTO cohorts ("
            "id, name, starts_at, ends_at, default_check_in_at, default_check_out_at, "
            "status, auto_start_enabled, auto_complete_enabled, created_by_user_id"
            ") VALUES (:id, :name, :starts_at, :ends_at, :check_in, :check_out, "
            "'completed', false, false, :actor_id)"
        ),
        {
            "id": cohort_id,
            "name": f"问卷查询测试营期 {cohort_id.hex[:8]}",
            "starts_at": starts_at,
            "ends_at": starts_at + timedelta(days=7),
            "check_in": starts_at,
            "check_out": starts_at + timedelta(hours=10),
            "actor_id": actor_id,
        },
    )


def _insert_class(
    db: Session,
    class_id: UUID,
    cohort_id: UUID,
    project_id: UUID,
    actor_id: UUID,
    name: str,
) -> None:
    db.execute(
        text(
            "INSERT INTO teaching_classes ("
            "id, cohort_id, project_id, name, class_type, professional_team_id, capacity, "
            "default_tuition_cents, is_active, created_by_user_id"
            ") VALUES (:id, :cohort_id, :project_id, :name, 'standard', NULL, 20, 0, true, :actor_id)"
        ),
        {
            "id": class_id,
            "cohort_id": cohort_id,
            "project_id": project_id,
            "name": name,
            "actor_id": actor_id,
        },
    )


def _insert_enrollment(
    db: Session,
    *,
    enrollment_id: UUID,
    admission_case_id: UUID,
    student_id: UUID,
    cohort_id: UUID,
    class_id: UUID,
    actor_id: UUID,
) -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    # Admission cases and enrollments have a deferred circular foreign key.  Start
    # the case in its valid pending state, create the completed enrollment, then
    # transition the case to its enrolled state.
    db.execute(
        text(
            "INSERT INTO admission_cases (id, student_person_id, status, created_by_user_id) "
            "VALUES (:id, NULL, 'pending_match', :actor_id)"
        ),
        {"id": admission_case_id, "actor_id": actor_id},
    )
    db.execute(
        text(
            "INSERT INTO enrollments ("
            "id, admission_case_id, student_person_id, cohort_id, class_id, "
            "agreed_tuition_cents, status, confirmed_at, completed_at, created_by_user_id"
            ") VALUES (:id, :case_id, :student_id, :cohort_id, :class_id, 0, "
            "'completed', :confirmed_at, :completed_at, :actor_id)"
        ),
        {
            "id": enrollment_id,
            "case_id": admission_case_id,
            "student_id": student_id,
            "cohort_id": cohort_id,
            "class_id": class_id,
            "confirmed_at": now,
            "completed_at": now + timedelta(hours=1),
            "actor_id": actor_id,
        },
    )
    db.execute(
        text(
            "UPDATE admission_cases SET student_person_id = :student_id, status = 'enrolled', "
            "current_enrollment_id = :enrollment_id, updated_by_user_id = :actor_id "
            "WHERE id = :case_id"
        ),
        {
            "student_id": student_id,
            "enrollment_id": enrollment_id,
            "actor_id": actor_id,
            "case_id": admission_case_id,
        },
    )


def _role(
    role_key: str,
    role_name: str,
    role_category: str,
) -> dict[str, str]:
    return {
        "role_key": role_key,
        "role_name": role_name,
        "role_category": role_category,
    }


def _insert_cycle(
    db: Session,
    *,
    cycle_id: UUID,
    enrollment_id: UUID,
    student_id: UUID,
    cohort_id: UUID,
    class_id: UUID,
    actor_id: UUID,
    staff_id: UUID,
    staff_name: str,
    roles: list[dict[str, str]],
    score: int,
    extra_staff_id: UUID | None = None,
    extra_staff_name: str = "不纳入汇总工作人员",
    add_supplement: bool = False,
    supplement_score: int = 6,
) -> None:
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    snapshot_v1_id = uuid4()
    staff_snapshot_v1_id = uuid4()
    submission_id = uuid4()
    survey_version_id = uuid4()
    db.execute(
        text(
            "INSERT INTO camp_survey_cycles ("
            "id, enrollment_id, student_person_id, cohort_id, class_id, opens_at, due_at, "
            "current_snapshot_version"
            ") VALUES (:id, :enrollment_id, :student_id, :cohort_id, :class_id, "
            ":opens_at, :due_at, 1)"
        ),
        {
            "id": cycle_id,
            "enrollment_id": enrollment_id,
            "student_id": student_id,
            "cohort_id": cohort_id,
            "class_id": class_id,
            "opens_at": now,
            "due_at": now + timedelta(days=2),
        },
    )
    db.execute(
        text(
            "INSERT INTO camp_survey_snapshot_versions "
            "(id, cycle_id, version, change_reason, created_by_user_id) "
            "VALUES (:id, :cycle_id, 1, 'initial roster', :actor_id)"
        ),
        {"id": snapshot_v1_id, "cycle_id": cycle_id, "actor_id": actor_id},
    )
    db.execute(
        text(
            "INSERT INTO camp_survey_staff_snapshots ("
            "id, snapshot_version_id, staff_person_id, staff_name_snapshot, role_key_snapshot, "
            "role_name_snapshot, role_category, roles_snapshot, service_intervals, include_in_summary"
            ") VALUES (:id, :snapshot_id, :staff_id, :staff_name, :role_key, :role_name, "
            ":role_category, CAST(:roles AS jsonb), CAST(:intervals AS jsonb), true)"
        ),
        {
            "id": staff_snapshot_v1_id,
            "snapshot_id": snapshot_v1_id,
            "staff_id": staff_id,
            "staff_name": staff_name,
            "role_key": roles[0]["role_key"],
            "role_name": roles[0]["role_name"],
            "role_category": roles[0]["role_category"],
            "roles": json.dumps(roles, ensure_ascii=False),
            "intervals": "[]",
        },
    )
    if extra_staff_id is not None:
        extra_snapshot_id = uuid4()
        db.execute(
            text(
                "INSERT INTO camp_survey_staff_snapshots ("
                "id, snapshot_version_id, staff_person_id, staff_name_snapshot, role_key_snapshot, "
                "role_name_snapshot, role_category, roles_snapshot, service_intervals, include_in_summary"
                ") VALUES (:id, :snapshot_id, :staff_id, :staff_name, 'life_service', '生活服务', "
                "'life_service', CAST(:roles AS jsonb), CAST('[]' AS jsonb), false)"
            ),
            {
                "id": extra_snapshot_id,
                "snapshot_id": snapshot_v1_id,
                "staff_id": extra_staff_id,
                "staff_name": extra_staff_name,
                "roles": json.dumps([_role("life_service", "生活服务", "life_service")]),
            },
        )
    db.execute(
        text(
            "INSERT INTO camp_survey_submissions ("
            "id, cycle_id, respondent_person_id, student_person_id, respondent_kind, "
            "revision_count, latest_version, first_submitted_at, last_submitted_at"
            ") VALUES (:id, :cycle_id, :respondent_id, :student_id, 'self', 0, 1, :now, :now)"
        ),
        {
            "id": submission_id,
            "cycle_id": cycle_id,
            "respondent_id": student_id,
            "student_id": student_id,
            "now": now,
        },
    )
    db.execute(
        text(
            "INSERT INTO camp_survey_versions ("
            "id, submission_id, snapshot_version_id, cycle_id, version, course_training_score, "
            "course_training_reason, life_service_score, life_service_reason, "
            "service_management_score, service_management_reason, overall_suggestion, "
            "favorite_cuisines, favorite_dishes, food_preference_version"
            ") VALUES (:id, :submission_id, :snapshot_id, :cycle_id, 1, 8, '课程清晰', 7, '服务周到', "
            "9, '管理有序', :overall_suggestion, CAST(:cuisines AS jsonb), "
            "CAST(:dishes AS jsonb), 1)"
        ),
        {
            "id": survey_version_id,
            "submission_id": submission_id,
            "snapshot_id": snapshot_v1_id,
            "cycle_id": cycle_id,
            "overall_suggestion": f"总体建议-{cycle_id.hex[:6]}",
            "cuisines": json.dumps(
                [{"cuisine_option_id": str(uuid4()), "name": "川菜", "custom_name": None}],
                ensure_ascii=False,
            ),
            "dishes": json.dumps(["宫保鸡丁"], ensure_ascii=False),
        },
    )
    if extra_staff_id is not None:
        # Give the excluded snapshot a real rating after its version exists so
        # the query's ``include_in_summary`` predicate, rather than missing
        # data, is what keeps it out of the projection.
        db.execute(
            text(
                "INSERT INTO camp_survey_staff_ratings ("
                "id, version_id, staff_snapshot_id, snapshot_version_id, score, reason, suggestion"
                ") VALUES (:id, :version_id, :staff_id, :snapshot_id, 1, '应被排除', '应被排除')"
            ),
            {
                "id": uuid4(),
                "version_id": survey_version_id,
                "staff_id": extra_snapshot_id,
                "snapshot_id": snapshot_v1_id,
            },
        )
    db.execute(
        text(
            "INSERT INTO camp_survey_staff_ratings ("
            "id, version_id, staff_snapshot_id, snapshot_version_id, score, reason, suggestion"
            ") VALUES (:id, :version_id, :staff_id, :snapshot_id, :score, '反馈原因', '改进建议')"
        ),
        {
            "id": uuid4(),
            "version_id": survey_version_id,
            "staff_id": staff_snapshot_v1_id,
            "snapshot_id": snapshot_v1_id,
            "score": score,
        },
    )
    if add_supplement:
        snapshot_v2_id = uuid4()
        staff_snapshot_v2_id = uuid4()
        supplement_id = uuid4()
        db.execute(
            text(
                "INSERT INTO camp_survey_snapshot_versions "
                "(id, cycle_id, version, change_reason, created_by_user_id) "
                "VALUES (:id, :cycle_id, 2, '补充工作人员', :actor_id)"
            ),
            {"id": snapshot_v2_id, "cycle_id": cycle_id, "actor_id": actor_id},
        )
        db.execute(
            text(
                "INSERT INTO camp_survey_staff_snapshots ("
                "id, snapshot_version_id, staff_person_id, staff_name_snapshot, role_key_snapshot, "
                "role_name_snapshot, role_category, roles_snapshot, service_intervals, include_in_summary"
                ") VALUES (:id, :snapshot_id, :staff_id, :staff_name, :role_key, :role_name, "
                ":role_category, CAST(:roles AS jsonb), CAST('[]' AS jsonb), true)"
            ),
            {
                "id": staff_snapshot_v2_id,
                "snapshot_id": snapshot_v2_id,
                "staff_id": staff_id,
                "staff_name": staff_name,
                "role_key": roles[0]["role_key"],
                "role_name": roles[0]["role_name"],
                "role_category": roles[0]["role_category"],
                "roles": json.dumps(roles, ensure_ascii=False),
            },
        )
        db.execute(
            text(
                "UPDATE camp_survey_cycles SET current_snapshot_version = 2 WHERE id = :cycle_id"
            ),
            {"cycle_id": cycle_id},
        )
        db.execute(
            text(
                "INSERT INTO camp_survey_supplements ("
                "id, submission_id, snapshot_version_id, cycle_id, change_reason, created_by_user_id, submitted_at"
                ") VALUES (:id, :submission_id, :snapshot_id, :cycle_id, '补充评价', :actor_id, :submitted_at)"
            ),
            {
                "id": supplement_id,
                "submission_id": submission_id,
                "snapshot_id": snapshot_v2_id,
                "cycle_id": cycle_id,
                "actor_id": actor_id,
                "submitted_at": now,
            },
        )
        db.execute(
            text(
                "INSERT INTO camp_survey_supplement_ratings ("
                "id, supplement_id, staff_snapshot_id, snapshot_version_id, score, reason, suggestion"
                ") VALUES (:id, :supplement_id, :staff_id, :snapshot_id, :score, '补充原因', '补充建议')"
            ),
            {
                "id": uuid4(),
                "supplement_id": supplement_id,
                "staff_id": staff_snapshot_v2_id,
                "snapshot_id": snapshot_v2_id,
                "score": supplement_score,
            },
        )


def _seed_query_data(db: Session) -> SurveyQuerySeed:
    actor_person_id = uuid4()
    actor_id = uuid4()
    staff_id = uuid4()
    excluded_staff_id = uuid4()
    student_ids = [uuid4(), uuid4(), uuid4()]
    cohort_id = uuid4()
    project_one_id = uuid4()
    project_two_id = uuid4()
    class_one_id = uuid4()
    class_two_id = uuid4()
    class_three_id = uuid4()

    _insert_person(db, actor_person_id, "问卷查询测试操作人")
    _insert_account(db, actor_id, actor_person_id)
    _insert_person(db, staff_id, "兼任工作人员")
    _insert_person(db, excluded_staff_id, "不纳入汇总工作人员")
    for index, student_id in enumerate(student_ids, start=1):
        _insert_person(db, student_id, f"问卷查询测试学员{index}", sex="male", birth_date=date(2012, 1, index))

    _insert_project(db, project_one_id, actor_id, f"问卷项目一-{project_one_id.hex[:8]}")
    _insert_project(db, project_two_id, actor_id, f"问卷项目二-{project_two_id.hex[:8]}")
    _insert_cohort(db, cohort_id, actor_id)
    _insert_class(db, class_one_id, cohort_id, project_one_id, actor_id, "问卷班级一")
    _insert_class(db, class_two_id, cohort_id, project_one_id, actor_id, "问卷班级二")
    _insert_class(db, class_three_id, cohort_id, project_two_id, actor_id, "问卷班级三")

    for student_id, class_id, cycle_id in (
        (student_ids[0], class_one_id, uuid4()),
        (student_ids[1], class_two_id, uuid4()),
        (student_ids[2], class_three_id, uuid4()),
    ):
        enrollment_id = uuid4()
        _insert_enrollment(
            db,
            enrollment_id=enrollment_id,
            admission_case_id=uuid4(),
            student_id=student_id,
            cohort_id=cohort_id,
            class_id=class_id,
            actor_id=actor_id,
        )
        if class_id == class_one_id:
            _insert_cycle(
                db,
                cycle_id=cycle_id,
                enrollment_id=enrollment_id,
                student_id=student_id,
                cohort_id=cohort_id,
                class_id=class_id,
                actor_id=actor_id,
                staff_id=staff_id,
                staff_name="兼任工作人员",
                roles=[
                    _role("operations", "运营工作人员", "operations"),
                    _role("teaching", "教学工作人员", "teaching"),
                ],
                score=8,
                extra_staff_id=excluded_staff_id,
                add_supplement=True,
                supplement_score=6,
            )
        elif class_id == class_two_id:
            _insert_cycle(
                db,
                cycle_id=cycle_id,
                enrollment_id=enrollment_id,
                student_id=student_id,
                cohort_id=cohort_id,
                class_id=class_id,
                actor_id=actor_id,
                staff_id=staff_id,
                staff_name="兼任工作人员",
                roles=[
                    _role("operations", "运营工作人员", "operations"),
                    _role("life_service", "生活服务工作人员", "life_service"),
                ],
                score=9,
            )
        else:
            _insert_cycle(
                db,
                cycle_id=cycle_id,
                enrollment_id=enrollment_id,
                student_id=student_id,
                cohort_id=cohort_id,
                class_id=class_id,
                actor_id=actor_id,
                staff_id=staff_id,
                staff_name="兼任工作人员",
                roles=[_role("operations", "运营工作人员", "operations")],
                score=3,
            )

    return SurveyQuerySeed(
        cohort_id=cohort_id,
        project_one_id=project_one_id,
        project_two_id=project_two_id,
        staff_id=staff_id,
        staff_name="兼任工作人员",
    )


def test_print_projection_filters_project_and_merges_roles_and_supplements(
    db: Session,
) -> None:
    seed = _seed_query_data(db)
    service = CampSurveyQueryService(db)

    project_one = service.print_projection(
        identity="hidden",
        cohort_id=seed.cohort_id,
        project_id=seed.project_one_id,
    )

    # Two cycles target the same project/staff.  Their role snapshots and the
    # The v2 supplement belongs to the same stable staff person as the main
    # rating and must not inflate the aggregate.
    assert len(project_one.items) == 1
    item = project_one.items[0]
    assert item.project_id == seed.project_one_id
    assert item.item_count == 2
    assert item.average_score == 8.5
    assert {detail.score for detail in item.details} == {8, 9}
    assert {role["role_key"] for role in item.roles} == {
        "operations",
        "teaching",
        "life_service",
    }

    project_two = service.print_projection(
        identity="hidden",
        cohort_id=seed.cohort_id,
        project_id=seed.project_two_id,
    )
    assert len(project_two.items) == 1
    assert project_two.items[0].project_id == seed.project_two_id
    assert project_two.items[0].item_count == 1

    # ``teaching`` is the second role in the first snapshot, so this proves the
    # JSON role array is searched instead of only the first role column.
    teaching = service.print_projection(
        identity="hidden",
        cohort_id=seed.cohort_id,
        project_id=seed.project_one_id,
        role_key="teaching",
    )
    assert len(teaching.items) == 1
    assert teaching.items[0].item_count == 1

    life_service = service.print_projection(
        identity="hidden",
        cohort_id=seed.cohort_id,
        project_id=seed.project_one_id,
        role_key="life_service",
    )
    assert len(life_service.items) == 1
    assert life_service.items[0].item_count == 1


def test_print_projection_excludes_non_summary_staff_and_controls_identity(
    db: Session,
) -> None:
    seed = _seed_query_data(db)
    service = CampSurveyQueryService(db)

    hidden = service.print_projection(
        identity="hidden",
        cohort_id=seed.cohort_id,
        project_id=seed.project_one_id,
    )
    assert len(hidden.items) == 1
    assert hidden.items[0].staff_person_id == seed.staff_id
    assert hidden.items[0].staff_name == seed.staff_name
    assert all(detail.respondent_person_id is None for detail in hidden.items[0].details)

    real_name = service.print_projection(
        identity="real_name",
        can_read_identity=True,
        cohort_id=seed.cohort_id,
        project_id=seed.project_one_id,
    )
    assert len(real_name.items) == 1
    assert real_name.items[0].staff_person_id == seed.staff_id
    assert real_name.items[0].staff_name == seed.staff_name
    assert all(detail.respondent_person_id is not None for detail in real_name.items[0].details)


def test_current_snapshot_exclusion_hides_old_ratings_without_deleting_history(
    db: Session,
) -> None:
    """A correction excludes a person from later summaries, including old rows."""
    seed = _seed_query_data(db)
    cycle = db.scalar(
        text(
            "SELECT cycle.id FROM camp_survey_cycles AS cycle "
            "JOIN teaching_classes AS class ON class.id = cycle.class_id "
            "WHERE cycle.cohort_id = :cohort_id "
            "AND class.project_id = :project_id "
            "AND NOT EXISTS ("
            "SELECT 1 FROM camp_survey_snapshot_versions AS v2 "
            "WHERE v2.cycle_id = cycle.id AND v2.version = 2) "
            "ORDER BY cycle.id LIMIT 1"
        ),
            {"cohort_id": seed.cohort_id, "project_id": seed.project_one_id},
    )
    assert cycle is not None

    # Preserve the original snapshot and ratings, then publish a correction
    # snapshot that explicitly excludes the same staff person.
    snapshot_v2_id = uuid4()
    excluded_row_id = uuid4()
    db.execute(
        text(
            "INSERT INTO camp_survey_snapshot_versions "
            "(id, cycle_id, version, change_reason, created_by_user_id) "
            "SELECT :snapshot_id, :cycle_id, 2, '纠正错误工作人员', "
            "created_by_user_id FROM camp_survey_snapshot_versions "
            "WHERE cycle_id = :cycle_id AND version = 1"
        ),
        {"snapshot_id": snapshot_v2_id, "cycle_id": cycle},
    )
    db.execute(
        text(
            "INSERT INTO camp_survey_staff_snapshots ("
            "id, snapshot_version_id, staff_person_id, staff_name_snapshot, "
            "role_key_snapshot, role_name_snapshot, role_category, roles_snapshot, "
            "service_intervals, include_in_summary"
            ") SELECT :row_id, :snapshot_id, staff_person_id, staff_name_snapshot, "
            "role_key_snapshot, role_name_snapshot, role_category, roles_snapshot, "
            "service_intervals, false FROM camp_survey_staff_snapshots "
            "WHERE snapshot_version_id = ("
            "SELECT id FROM camp_survey_snapshot_versions "
            "WHERE cycle_id = :cycle_id AND version = 1) "
            "AND staff_person_id = :staff_id"
        ),
        {
            "row_id": excluded_row_id,
            "snapshot_id": snapshot_v2_id,
            "cycle_id": cycle,
            "staff_id": seed.staff_id,
        },
    )
    db.execute(
        text(
            "UPDATE camp_survey_cycles SET current_snapshot_version = 2 "
            "WHERE id = :cycle_id"
        ),
        {"cycle_id": cycle},
    )
    db.flush()

    service = CampSurveyQueryService(db)
    rows = service.list_anonymous(
        cohort_id=seed.cohort_id,
        project_id=seed.project_one_id,
    )

    # The other project-one cycle remains visible.  The corrected cycle's old
    # rating is still physically present, but no longer contributes to the
    # public aggregate.
    assert rows.total == 1
    assert all(item.cycle_id != cycle for item in rows.items)
    assert {item.score for item in rows.items} == {8}
    assert db.scalar(
        text("SELECT count(*) FROM camp_survey_staff_ratings WHERE staff_snapshot_id IN ("
             "SELECT id FROM camp_survey_staff_snapshots WHERE snapshot_version_id = ("
             "SELECT id FROM camp_survey_snapshot_versions WHERE cycle_id = :cycle_id AND version = 1))"),
        {"cycle_id": cycle},
    )


def test_supplement_ratings_remain_visible_after_a_later_snapshot_revision(
    db: Session,
) -> None:
    seed = _seed_query_data(db)
    cycle = db.scalar(
        text(
            "SELECT id FROM camp_survey_cycles "
            "WHERE cohort_id = :cohort_id AND current_snapshot_version = 2 "
            "LIMIT 1"
        ),
        {"cohort_id": seed.cohort_id},
    )
    assert cycle is not None
    snapshot_v3_id = uuid4()
    db.execute(
        text(
            "INSERT INTO camp_survey_snapshot_versions "
            "(id, cycle_id, version, change_reason, created_by_user_id) "
            "SELECT :snapshot_id, :cycle_id, 3, '后续名单修订', created_by_user_id "
            "FROM camp_survey_snapshot_versions "
            "WHERE cycle_id = :cycle_id AND version = 2"
        ),
        {"snapshot_id": snapshot_v3_id, "cycle_id": cycle},
    )
    db.execute(
        text(
            "INSERT INTO camp_survey_staff_snapshots ("
            "id, snapshot_version_id, staff_person_id, staff_name_snapshot, "
            "role_key_snapshot, role_name_snapshot, role_category, roles_snapshot, "
            "service_intervals, include_in_summary"
            ") SELECT gen_random_uuid(), :snapshot_id, staff_person_id, staff_name_snapshot, "
            "role_key_snapshot, role_name_snapshot, role_category, roles_snapshot, "
            "service_intervals, include_in_summary FROM camp_survey_staff_snapshots "
            "WHERE snapshot_version_id = ("
            "SELECT id FROM camp_survey_snapshot_versions "
            "WHERE cycle_id = :cycle_id AND version = 2)"
        ),
        {"snapshot_id": snapshot_v3_id, "cycle_id": cycle},
    )
    db.execute(
        text(
            "UPDATE camp_survey_cycles SET current_snapshot_version = 3 "
            "WHERE id = :cycle_id"
        ),
        {"cycle_id": cycle},
    )
    db.flush()

    rows = CampSurveyQueryService(db).list_anonymous(
        cohort_id=seed.cohort_id,
        project_id=seed.project_one_id,
    )
    assert rows.total == 2
    assert 6 not in {item.score for item in rows.items}
