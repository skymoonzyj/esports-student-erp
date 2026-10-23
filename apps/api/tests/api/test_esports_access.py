from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.identity.models import GuardianRelationship, Person, StaffProfile
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass

NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)


@pytest.fixture
def esports_people(db: Session):
    people = {}
    actors = {}
    for role in (
        "student",
        "guardian",
        "coach",
        "super_admin",
        "admin",
        "administration",
        "finance",
        "assistant",
        "other",
    ):
        marker = uuid4().hex
        person = Person(
            full_name=role,
            identity_ciphertext="cipher",
            identity_digest=marker,
            sex="male",
            date_of_birth=date(1990, 1, 1),
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=marker,
            email_normalized=f"{marker}@example.test",
            password_hash="test",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        if role in ROLES:
            db.add(UserRole(user_id=account.id, role_id=ROLES[role].id))
        if role == "coach":
            db.add(StaffProfile(person_id=person.id, staff_type="coach"))
        people[role] = person
        actors[role] = AuthContext(account=account, person=person, session=AuthSession())
    guardian = GuardianRelationship(
        guardian_person_id=people["guardian"].id,
        student_person_id=people["student"].id,
        relationship="parent",
        valid_from=date(2020, 1, 1),
    )
    db.add(guardian)
    db.commit()
    return people, actors, guardian


def grant(
    db,
    actor,
    permission="esports_assessments.read",
    *,
    effect="allow",
    scope="global",
    scope_id=None,
):
    rule = UserPermissionOverride(
        user_id=actor.account.id,
        permission_id=PERMISSIONS[permission].id,
        effect=effect,
        scope_kind=scope,
        scope_id=scope_id,
    )
    db.add(rule)
    db.commit()
    return rule


@pytest.mark.parametrize(
    "role,visible",
    [
        ("student", True),
        ("guardian", True),
        ("super_admin", True),
        ("coach", False),
        ("admin", False),
        ("administration", False),
        ("finance", False),
        ("assistant", False),
    ],
)
def test_esports_default_person_visibility(db, esports_people, role, visible):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentNotFound
    from app.esports_assessments.models import AssessmentReport, AssessmentResult, AssessmentSession

    people, actors, _ = esports_people
    access = AssessmentAccess(db, actors[role], NOW)
    ids = set(db.scalars(access.visible_person_ids()))
    assert (people["student"].id in ids) is visible
    if visible:
        access.require_person(people["student"].id)
    else:
        tables = (AuditEvent, AssessmentSession, AssessmentResult, AssessmentReport)
        before = [db.scalar(select(func.count()).select_from(table)) for table in tables]
        for person_id in (people["student"].id, uuid4()):
            with pytest.raises(AssessmentNotFound):
                access.require_person(person_id)
        assert [db.scalar(select(func.count()).select_from(table)) for table in tables] == before


def test_esports_family_stays_valid_for_adults_but_ends_immediately(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentNotFound

    people, actors, relationship = esports_people
    access = AssessmentAccess(db, actors["guardian"], NOW)
    access.require_person(people["student"].id)
    relationship.ended_at = NOW
    db.commit()
    with pytest.raises(AssessmentNotFound):
        access.require_person(people["student"].id)


def test_esports_dual_identity_deny_wins_and_sql_excludes_person(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentNotFound

    people, actors, _ = esports_people
    actor = actors["guardian"]
    db.add(UserRole(user_id=actor.account.id, role_id=ROLES["student"].id))
    grant(db, actor, scope="person", scope_id=people["other"].id)
    grant(db, actor, effect="deny", scope="person", scope_id=people["student"].id)
    access = AssessmentAccess(db, actor, NOW)
    assert set(db.scalars(access.visible_person_ids())) == {actor.person.id, people["other"].id}
    with pytest.raises(AssessmentNotFound):
        access.require_person(people["student"].id)


def test_esports_take_needs_self_take_and_effective_read(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentForbidden

    _, actors, _ = esports_people
    AssessmentAccess(db, actors["student"], NOW).require_take()
    grant(db, actors["student"], effect="deny", scope="self")
    with pytest.raises(AssessmentForbidden):
        AssessmentAccess(db, actors["student"], NOW).require_take()
    grant(db, actors["other"], "esports_assessments.take", scope="global")
    grant(db, actors["other"])
    with pytest.raises(AssessmentForbidden):
        AssessmentAccess(db, actors["other"], NOW).require_take()


def test_esports_disabled_account_is_private_and_cannot_take(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentForbidden, AssessmentNotFound

    people, actors, _ = esports_people
    actor = actors["student"]
    actor.account.status = "disabled"
    db.commit()
    access = AssessmentAccess(db, actor, NOW)
    assert list(db.scalars(access.visible_person_ids())) == []
    with pytest.raises(AssessmentNotFound):
        access.require_person(people["student"].id)
    with pytest.raises(AssessmentForbidden):
        access.require_take()


@pytest.mark.parametrize(
    "role,partial", [("other", False), ("super_admin", True), ("student", True)]
)
def test_esports_partial_requires_owner_or_actual_global_superadmin(
    db, esports_people, role, partial
):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentNotFound
    from app.esports_assessments.models import AssessmentSession

    people, actors, _ = esports_people
    if role == "other":
        grant(db, actors[role])
    access = AssessmentAccess(db, actors[role], NOW)
    session = AssessmentSession(person_id=people["student"].id, status="in_progress")
    if partial:
        access.require_session(session)
    else:
        with pytest.raises(AssessmentNotFound):
            access.require_session(session)
    session.status = "completed"
    access.require_session(session)


@pytest.fixture
def esports_class(db, esports_people):
    people, actors, _ = esports_people
    admin_id = actors["super_admin"].account.id
    cohort = Cohort(
        name="esports",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW + timedelta(days=2),
        default_check_in_at=NOW - timedelta(days=2),
        default_check_out_at=NOW + timedelta(days=2),
        status="active",
        created_by_user_id=admin_id,
    )
    db.add(cohort)
    db.flush()
    project = db.scalar(select(GameProject).limit(1))
    classes = []
    for name in ("assigned", "same-project"):
        classroom = TeachingClass(
            cohort_id=cohort.id,
            project_id=project.id,
            name=name,
            class_type="standard",
            capacity=10,
            created_by_user_id=admin_id,
        )
        db.add(classroom)
        db.flush()
        classes.append(classroom)
    assignment = CoachAssignment(
        class_id=classes[0].id,
        coach_person_id=people["coach"].id,
        assignment_type="primary",
        valid_from=NOW - timedelta(days=1),
        created_by_user_id=admin_id,
    )
    db.add(assignment)
    enrollments = []
    for key, classroom in zip(("student", "other"), classes, strict=True):
        case = AdmissionCase(student_person_id=people[key].id, status="ready")
        db.add(case)
        db.flush()
        enrollment = Enrollment(
            admission_case_id=case.id,
            student_person_id=people[key].id,
            cohort_id=cohort.id,
            class_id=classroom.id,
            agreed_tuition_cents=0,
            status="active",
            confirmed_at=NOW,
            created_by_user_id=admin_id,
        )
        db.add(enrollment)
        enrollments.append(enrollment)
    db.commit()
    return assignment, classes, enrollments


@pytest.mark.parametrize(
    "invalid",
    [
        "ended",
        "expired",
        "future",
        "inactive_class",
        "completed_enrollment",
        "ineligible",
        "custom_ended",
        "cohort_ended",
    ],
)
def test_esports_coach_current_class_only_and_lifecycle(db, esports_people, esports_class, invalid):
    from app.esports_assessments.access import AssessmentAccess

    people, actors, _ = esports_people
    assignment, classes, enrollments = esports_class
    access = AssessmentAccess(db, actors["coach"], NOW)
    assert set(db.scalars(access.visible_person_ids())) == {people["student"].id}
    if invalid == "ended":
        admin_id = actors["super_admin"].account.id
        assignment.ended_at = NOW
        assignment.ended_by_user_id = admin_id
        assignment.end_reason = "ended"
    elif invalid == "expired":
        assignment.valid_until = NOW
    elif invalid == "future":
        assignment.valid_from = NOW + timedelta(hours=1)
    elif invalid == "inactive_class":
        classes[0].is_active = False
    elif invalid == "completed_enrollment":
        enrollments[0].status = "completed"
        enrollments[0].completed_at = NOW
    elif invalid == "ineligible":
        db.get(StaffProfile, people["coach"].id).staff_type = "other"
    elif invalid == "custom_ended":
        enrollments[0].schedule_mode = "custom"
        enrollments[0].custom_starts_at = NOW - timedelta(days=1)
        enrollments[0].custom_ends_at = NOW
    else:
        db.get(Cohort, classes[0].cohort_id).ends_at = NOW
    db.commit()
    assert list(db.scalars(access.visible_person_ids())) == []


def test_esports_assigned_class_scope_id_deny_only_matching_class(
    db, esports_people, esports_class
):
    from app.esports_assessments.access import AssessmentAccess

    people, actors, _ = esports_people
    _, classes, _ = esports_class
    grant(db, actors["coach"], effect="deny", scope="assigned_class", scope_id=classes[1].id)
    access = AssessmentAccess(db, actors["coach"], NOW)
    assert set(db.scalars(access.visible_person_ids())) == {people["student"].id}
    grant(db, actors["coach"], effect="deny", scope="assigned_class", scope_id=classes[0].id)
    assert list(db.scalars(access.visible_person_ids())) == []


def test_esports_person_deny_blocks_even_superadmin_partial(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentNotFound
    from app.esports_assessments.models import AssessmentSession

    people, actors, _ = esports_people
    grant(db, actors["super_admin"], effect="deny", scope="person", scope_id=people["student"].id)
    access = AssessmentAccess(db, actors["super_admin"], NOW)
    with pytest.raises(AssessmentNotFound):
        access.require_session(
            AssessmentSession(person_id=people["student"].id, status="in_progress")
        )


def test_esports_override_windows_are_live(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess

    people, actors, _ = esports_people
    rule = grant(db, actors["other"], scope="person", scope_id=people["student"].id)
    rule.valid_from = NOW - timedelta(days=1)
    rule.valid_until = NOW
    db.commit()
    access = AssessmentAccess(db, actors["other"], NOW)
    assert list(db.scalars(access.visible_person_ids())) == []
    rule.valid_until = NOW + timedelta(seconds=1)
    db.commit()
    assert list(db.scalars(access.visible_person_ids())) == [people["student"].id]


def test_esports_assistant_assignment_is_current_coach_relation(db, esports_people, esports_class):
    from app.esports_assessments.access import AssessmentAccess

    people, actors, _ = esports_people
    assignment, _, _ = esports_class
    assignment.assignment_type = "assistant"
    db.commit()
    assert list(db.scalars(AssessmentAccess(db, actors["coach"], NOW).visible_person_ids())) == [
        people["student"].id
    ]


@pytest.mark.parametrize("status", ["open", "upcoming"])
@pytest.mark.parametrize("schedule", ["cohort", "custom"])
def test_current_coach_sees_future_enrollment_in_open_or_upcoming_cohort(
    db, esports_people, esports_class, status, schedule
):
    from app.esports_assessments.access import AssessmentAccess

    people, actors, _ = esports_people
    _, classes, enrollments = esports_class
    cohort = db.get(Cohort, classes[0].cohort_id)
    cohort.status = status
    cohort.starts_at = NOW + timedelta(days=7)
    cohort.ends_at = NOW + timedelta(days=14)
    cohort.default_check_in_at = cohort.starts_at
    cohort.default_check_out_at = cohort.ends_at
    if schedule == "custom":
        enrollments[0].schedule_mode = "custom"
        enrollments[0].custom_starts_at = NOW + timedelta(days=8)
        enrollments[0].custom_ends_at = NOW + timedelta(days=13)
    db.commit()
    access = AssessmentAccess(db, actors["coach"], NOW)
    assert set(db.scalars(access.visible_person_ids())) == {people["student"].id}
    access.require_person(people["student"].id)


def test_esports_session_sql_filters_before_count_and_page(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.catalog import get_suite
    from app.esports_assessments.models import AssessmentSession

    people, actors, _ = esports_people
    rules = get_suite("pc-fps")
    rows = []
    for person_key, status in (
        ("student", "completed"),
        ("student", "in_progress"),
        ("other", "completed"),
    ):
        row = AssessmentSession(
            person_id=people[person_key].id,
            created_by_user_id=actors[person_key].account.id,
            creation_key=uuid4(),
            creation_request_digest="a" * 64,
            suite_id="pc-fps",
            catalog_snapshot=rules.model_dump(mode="json"),
            scoring_snapshot={},
            report_rules_snapshot={},
            status=status,
            completed_at=NOW if status == "completed" else None,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    access = AssessmentAccess(db, actors["guardian"], NOW)
    visible = select(AssessmentSession.id).where(access.visible_session_predicate())
    assert db.scalar(select(func.count()).select_from(visible.subquery())) == 1
    assert list(db.scalars(visible.limit(1))) == [rows[0].id]
    grant(db, actors["guardian"])
    visible = select(AssessmentSession.id).where(access.visible_session_predicate())
    assert set(db.scalars(visible)) == {rows[0].id, rows[2].id}


def test_old_permissions_do_not_grant_new_module(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentForbidden

    _, actors, _ = esports_people
    actor = actors["other"]
    grant(db, actor, "talent_tests.take", scope="self")
    grant(db, actor, "talent_tests.read", scope="self")
    access = AssessmentAccess(db, actor, NOW)
    assert list(db.scalars(access.visible_person_ids())) == []
    with pytest.raises(AssessmentForbidden):
        access.require_take()


def test_private_run_denies_owner_when_read_revoked(db, esports_people):
    from app.esports_assessments.access import AssessmentAccess
    from app.esports_assessments.errors import AssessmentNotFound

    _, actors, _ = esports_people
    actor = actors["student"]
    access = AssessmentAccess(db, actor, NOW)
    access.require_private_run(actor.person.id)
    grant(db, actor, effect="deny", scope="self")
    with pytest.raises(AssessmentNotFound):
        access.require_private_run(actor.person.id)
