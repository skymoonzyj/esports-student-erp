from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, literal, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.evaluations.access import (
    EvaluationAccessService,
    EvaluationDecision,
    EvaluationResource,
    EvaluationSqlResource,
)
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
)
from app.identity.models import GuardianRelationship, Person, StaffProfile, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.schemas import PermissionOverrideCreate
from app.rbac.seed import ROLES
from app.rbac.service import PermissionDelegationDenied, RbacService
from app.teaching.models import CoachAssignment, Cohort, TeachingClass
from app.teaching.seed import INITIAL_PROJECTS

START = datetime(2026, 8, 1, 0, tzinfo=UTC)
END = START + timedelta(days=21)


@dataclass(frozen=True)
class EvaluationScenario:
    accounts: dict[str, UserAccount]
    people: dict[str, Person]
    cohort: Cohort
    assigned_class: TeachingClass
    unrelated_class: TeachingClass
    permissions: dict[str, Permission]
    staff_assignment: CohortStaffAssignment


def _person(*, name: str, birth: date) -> Person:
    token = uuid4().hex
    return Person(
        full_name=name,
        identity_ciphertext=f"cipher:{token}",
        identity_digest=(token * 2)[:64],
        sex="female",
        date_of_birth=birth,
    )


def _account(person: Person, *, key: str) -> UserAccount:
    token = uuid4().hex
    return UserAccount(
        person_id=person.id,
        login_phone_digest=(token * 2)[:64],
        email_normalized=f"{key}-{token[:8]}@example.com",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
    )


@pytest.fixture
def evaluation_scenario(db: Session) -> EvaluationScenario:
    people = {
        "admin": _person(name="评价管理员", birth=date(1980, 1, 1)),
        "student": _person(name="评价学员", birth=date(2000, 1, 1)),
        "other_student": _person(name="其他评价学员", birth=date(2001, 1, 1)),
        "guardian": _person(name="评价监护人", birth=date(1982, 1, 1)),
        "primary_coach": _person(name="评价主教练", birth=date(1985, 1, 1)),
        "assistant_coach": _person(name="评价助教", birth=date(1986, 1, 1)),
        "unrelated_coach": _person(name="无关教练", birth=date(1987, 1, 1)),
        "customer_service": _person(name="评价客服", birth=date(1988, 1, 1)),
        "generic_staff": _person(name="评价生活老师", birth=date(1989, 1, 1)),
    }
    db.add_all(people.values())
    db.flush()

    db.add_all(
        [
            StudentProfile(
                person_id=people["student"].id,
                student_number=f"EVA-{uuid4().hex[:12]}",
                status="active",
            ),
            StudentProfile(
                person_id=people["other_student"].id,
                student_number=f"EVA-{uuid4().hex[:12]}",
                status="active",
            ),
            *[
                StaffProfile(person_id=people[key].id, staff_type="coach")
                for key in ("primary_coach", "assistant_coach", "unrelated_coach")
            ],
            StaffProfile(person_id=people["admin"].id, staff_type="administration"),
            StaffProfile(
                person_id=people["customer_service"].id,
                staff_type="customer_service",
            ),
            StaffProfile(person_id=people["generic_staff"].id, staff_type="life_teacher"),
        ]
    )
    db.flush()

    accounts = {
        key: _account(person, key=key) for key, person in people.items() if key != "other_student"
    }
    db.add_all(accounts.values())
    db.flush()

    role_keys = {
        "admin": "admin",
        "student": "student",
        "guardian": "guardian",
        "primary_coach": "coach",
        "assistant_coach": "coach",
        "unrelated_coach": "coach",
        "customer_service": "customer_service",
        "generic_staff": "staff",
    }
    db.add_all(
        UserRole(user_id=accounts[key].id, role_id=ROLES[role_key].id)
        for key, role_key in role_keys.items()
    )

    db.add(
        GuardianRelationship(
            guardian_person_id=people["guardian"].id,
            student_person_id=people["student"].id,
            relationship="mother",
            is_primary=True,
            valid_from=START.date(),
        )
    )

    cohort = Cohort(
        name=f"评价授权届次-{uuid4().hex[:8]}",
        starts_at=START,
        ends_at=END,
        default_check_in_at=START,
        default_check_out_at=END,
        status="active",
        created_by_user_id=accounts["admin"].id,
    )
    db.add(cohort)
    db.flush()
    assigned_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=INITIAL_PROJECTS[0].id,
        name=f"评价授权班-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        created_by_user_id=accounts["admin"].id,
    )
    unrelated_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=INITIAL_PROJECTS[1].id,
        name=f"无关授权班-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        created_by_user_id=accounts["admin"].id,
    )
    db.add_all([assigned_class, unrelated_class])
    db.flush()

    db.add_all(
        [
            CoachAssignment(
                class_id=assigned_class.id,
                coach_person_id=people["primary_coach"].id,
                assignment_type="primary",
                valid_from=START,
                valid_until=END,
                created_by_user_id=accounts["admin"].id,
            ),
            CoachAssignment(
                class_id=assigned_class.id,
                coach_person_id=people["assistant_coach"].id,
                assignment_type="assistant",
                valid_from=START,
                valid_until=END,
                created_by_user_id=accounts["admin"].id,
            ),
        ]
    )

    staff_assignment = CohortStaffAssignment(
        cohort_id=cohort.id,
        role_id=UUID("41000000-0000-4000-8000-000000000001"),
        staff_person_id=people["generic_staff"].id,
        project_id=INITIAL_PROJECTS[0].id,
        role_name_snapshot="生活老师",
        role_category_snapshot="life_service",
        valid_from=START,
        valid_until=END,
        created_by_user_id=accounts["admin"].id,
    )
    db.add(staff_assignment)
    db.flush()

    permissions = {
        row.key: row
        for row in db.scalars(
            select(Permission).where(
                Permission.key.in_(
                    {
                        "evaluation_media.upload",
                        "evaluation_media.original.read",
                        "student_game_accounts.manage",
                    }
                )
            )
        )
    }
    db.add(
        CohortStaffAssignmentPermission(
            assignment_id=staff_assignment.id,
            permission_id=permissions["evaluation_media.upload"].id,
            created_by_user_id=accounts["admin"].id,
        )
    )
    db.flush()
    return EvaluationScenario(
        accounts=accounts,
        people=people,
        cohort=cohort,
        assigned_class=assigned_class,
        unrelated_class=unrelated_class,
        permissions=permissions,
        staff_assignment=staff_assignment,
    )


def _student_resource(scenario: EvaluationScenario) -> EvaluationResource:
    return EvaluationResource(
        student_person_id=scenario.people["student"].id,
        cohort_id=scenario.cohort.id,
        project_id=scenario.assigned_class.project_id,
        class_id=scenario.assigned_class.id,
        enrollment_id=uuid4(),
    )


def _override(
    db: Session,
    scenario: EvaluationScenario,
    *,
    account: str,
    permission: str,
    effect: str,
    scope_kind: str,
    scope_id: UUID | None = None,
    valid_from: datetime | None = START,
    valid_until: datetime | None = END,
) -> UserPermissionOverride:
    row = UserPermissionOverride(
        user_id=scenario.accounts[account].id,
        permission_id=scenario.permissions[permission].id,
        effect=effect,
        scope_kind=scope_kind,
        scope_id=scope_id,
        valid_from=valid_from,
        valid_until=valid_until,
        created_by_user_id=scenario.accounts["admin"].id,
        grant_reason="evaluation authorization test",
    )
    db.add(row)
    db.flush()
    return row


def test_default_evaluation_authorization_is_least_privilege(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    service = EvaluationAccessService(db)
    resource = _student_resource(evaluation_scenario)

    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["admin"].id,
            permission="student_game_accounts.manage",
            resource=resource,
            occurred_at=START + timedelta(days=1),
        ).allowed
        is True
    )
    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["student"].id,
            permission="student_game_accounts.manage",
            resource=resource,
            occurred_at=START + timedelta(days=1),
        ).allowed
        is True
    )
    for key in ("guardian", "customer_service"):
        assert (
            service.decide(
                account_id=evaluation_scenario.accounts[key].id,
                permission="student_game_accounts.manage",
                resource=resource,
                occurred_at=START + timedelta(days=1),
            ).allowed
            is False
        )


def test_family_allow_stops_on_relationship_end(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    relationship_end = START + timedelta(days=10)
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id == evaluation_scenario.people["guardian"].id
        )
    )
    assert relationship is not None
    relationship.ended_at = relationship_end
    relationship.ended_by_user_id = evaluation_scenario.accounts["admin"].id
    relationship.end_reason = "authorization boundary test"
    _override(
        db,
        evaluation_scenario,
        account="guardian",
        permission="student_game_accounts.manage",
        effect="allow",
        scope_kind="family",
    )
    db.flush()
    service = EvaluationAccessService(db)
    resource = _student_resource(evaluation_scenario)

    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["guardian"].id,
            permission="student_game_accounts.manage",
            resource=resource,
            occurred_at=relationship_end - timedelta(microseconds=1),
        ).allowed
        is True
    )
    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["guardian"].id,
            permission="student_game_accounts.manage",
            resource=resource,
            occurred_at=relationship_end,
        ).allowed
        is False
    )


def test_staff_assignment_allow_is_limited_to_assignment_scope_and_time(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    service = EvaluationAccessService(db)
    assigned = _student_resource(evaluation_scenario)
    unrelated = EvaluationResource(
        cohort_id=evaluation_scenario.cohort.id,
        project_id=evaluation_scenario.unrelated_class.project_id,
        class_id=evaluation_scenario.unrelated_class.id,
    )

    assert service.decide(
        account_id=evaluation_scenario.accounts["generic_staff"].id,
        permission="evaluation_media.upload",
        resource=assigned,
        occurred_at=START + timedelta(days=1),
    ) == EvaluationDecision(True, "staff_assignment_allow")
    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["generic_staff"].id,
            permission="evaluation_media.upload",
            resource=unrelated,
            occurred_at=START + timedelta(days=1),
        ).allowed
        is False
    )
    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["generic_staff"].id,
            permission="evaluation_media.upload",
            resource=assigned,
            occurred_at=END,
        ).allowed
        is False
    )
    for coach_key in ("primary_coach", "assistant_coach"):
        assert service.decide(
            account_id=evaluation_scenario.accounts[coach_key].id,
            permission="evaluation_media.original.read",
            resource=assigned,
            occurred_at=START + timedelta(days=1),
        ) == EvaluationDecision(True, "role_allow")
    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["unrelated_coach"].id,
            permission="evaluation_media.original.read",
            resource=assigned,
            occurred_at=START + timedelta(days=1),
        ).allowed
        is False
    )
    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["primary_coach"].id,
            permission="evaluation_media.original.read",
            resource=unrelated,
            occurred_at=START + timedelta(days=1),
        ).allowed
        is False
    )
    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["primary_coach"].id,
            permission="evaluation_media.original.read",
            resource=assigned,
            occurred_at=END,
        ).allowed
        is False
    )


def test_account_deny_beats_admin_role_staff_role_and_specific_allow(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    admin_assignment = CohortStaffAssignment(
        cohort_id=evaluation_scenario.cohort.id,
        role_id=UUID("41000000-0000-4000-8000-000000000001"),
        staff_person_id=evaluation_scenario.people["admin"].id,
        project_id=evaluation_scenario.assigned_class.project_id,
        role_name_snapshot="生活老师",
        role_category_snapshot="life_service",
        valid_from=START,
        valid_until=END,
        created_by_user_id=evaluation_scenario.accounts["admin"].id,
    )
    db.add(admin_assignment)
    db.flush()
    db.add(
        CohortStaffAssignmentPermission(
            assignment_id=admin_assignment.id,
            permission_id=evaluation_scenario.permissions["evaluation_media.upload"].id,
            created_by_user_id=evaluation_scenario.accounts["admin"].id,
        )
    )
    _override(
        db,
        evaluation_scenario,
        account="admin",
        permission="evaluation_media.upload",
        effect="allow",
        scope_kind="project",
        scope_id=evaluation_scenario.assigned_class.project_id,
    )
    _override(
        db,
        evaluation_scenario,
        account="admin",
        permission="evaluation_media.upload",
        effect="deny",
        scope_kind="cohort",
        scope_id=evaluation_scenario.cohort.id,
    )
    db.flush()

    assert EvaluationAccessService(db).decide(
        account_id=evaluation_scenario.accounts["admin"].id,
        permission="evaluation_media.upload",
        resource=_student_resource(evaluation_scenario),
        occurred_at=START + timedelta(days=1),
    ) == EvaluationDecision(False, "explicit_deny")


def test_expired_override_fails_on_the_next_decision(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    expires_at = START + timedelta(days=2)
    _override(
        db,
        evaluation_scenario,
        account="customer_service",
        permission="student_game_accounts.manage",
        effect="allow",
        scope_kind="person",
        scope_id=evaluation_scenario.people["student"].id,
        valid_until=expires_at,
    )
    service = EvaluationAccessService(db)
    resource = _student_resource(evaluation_scenario)

    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["customer_service"].id,
            permission="student_game_accounts.manage",
            resource=resource,
            occurred_at=expires_at - timedelta(microseconds=1),
        ).allowed
        is True
    )
    assert (
        service.decide(
            account_id=evaluation_scenario.accounts["customer_service"].id,
            permission="student_game_accounts.manage",
            resource=resource,
            occurred_at=expires_at,
        ).allowed
        is False
    )


def test_inactive_account_has_no_evaluation_business_access(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    evaluation_scenario.accounts["admin"].status = "disabled"
    db.flush()
    service = EvaluationAccessService(db)

    for permission in evaluation_scenario.permissions:
        assert service.decide(
            account_id=evaluation_scenario.accounts["admin"].id,
            permission=permission,
            resource=_student_resource(evaluation_scenario),
            occurred_at=START + timedelta(days=1),
        ) == EvaluationDecision(False, "inactive_account")


def test_listing_filters_with_the_same_predicate_as_detail(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    resources = [
        _student_resource(evaluation_scenario),
        EvaluationResource(student_person_id=evaluation_scenario.people["other_student"].id),
    ]
    service = EvaluationAccessService(db)
    filtered = service.filter_resources(
        account_id=evaluation_scenario.accounts["student"].id,
        permission="student_game_accounts.manage",
        resources=resources,
        occurred_at=START + timedelta(days=1),
    )
    individually_allowed = [
        resource
        for resource in resources
        if service.decide(
            account_id=evaluation_scenario.accounts["student"].id,
            permission="student_game_accounts.manage",
            resource=resource,
            occurred_at=START + timedelta(days=1),
        ).allowed
    ]
    batch_decisions = service.decide_resources(
        account_id=evaluation_scenario.accounts["student"].id,
        permission="student_game_accounts.manage",
        resources=resources,
        occurred_at=START + timedelta(days=1),
    )
    individual_decisions = [
        service.decide(
            account_id=evaluation_scenario.accounts["student"].id,
            permission="student_game_accounts.manage",
            resource=resource,
            occurred_at=START + timedelta(days=1),
        )
        for resource in resources
    ]

    assert filtered == individually_allowed == [resources[0]]
    assert batch_decisions == individual_decisions


@pytest.mark.parametrize("scope_kind", ("building", "floor"))
def test_batch_decisions_preserve_housing_scope_tokens(
    db: Session,
    evaluation_scenario: EvaluationScenario,
    scope_kind: str,
) -> None:
    scope_id = uuid4()
    _override(
        db,
        evaluation_scenario,
        account="customer_service",
        permission="evaluation_media.upload",
        effect="allow",
        scope_kind=scope_kind,
        scope_id=scope_id,
    )
    resource = EvaluationResource(
        building_id=scope_id if scope_kind == "building" else None,
        floor_id=scope_id if scope_kind == "floor" else None,
    )
    service = EvaluationAccessService(db)
    occurred_at = START + timedelta(days=1)

    individual = service.decide(
        account_id=evaluation_scenario.accounts["customer_service"].id,
        permission="evaluation_media.upload",
        resource=resource,
        occurred_at=occurred_at,
    )
    batch = service.decide_resources(
        account_id=evaluation_scenario.accounts["customer_service"].id,
        permission="evaluation_media.upload",
        resources=(resource,),
        occurred_at=occurred_at,
    )

    assert individual == EvaluationDecision(True, "account_allow")
    assert batch == [individual]


def test_mixed_resource_filter_query_count_is_constant(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    bind = db.get_bind()
    service = EvaluationAccessService(db)

    def count_queries(size: int) -> tuple[int, list[EvaluationResource]]:
        statements: list[str] = []

        def record_statement(*args: object) -> None:
            statement = args[2]
            if isinstance(statement, str) and statement.lstrip().lower().startswith("select"):
                statements.append(statement)

        resources = [_student_resource(evaluation_scenario) for _ in range(size)]
        event.listen(bind, "before_cursor_execute", record_statement)
        try:
            allowed = service.filter_resources(
                account_id=evaluation_scenario.accounts["admin"].id,
                permission="student_game_accounts.manage",
                resources=resources,
                occurred_at=START + timedelta(days=1),
            )
        finally:
            event.remove(bind, "before_cursor_execute", record_statement)
        return len(statements), allowed

    small_query_count, small_allowed = count_queries(1)
    large_query_count, large_allowed = count_queries(200)

    assert small_allowed
    assert len(large_allowed) == 200
    assert large_query_count <= small_query_count + 1


def test_sql_authorization_matches_object_decisions_across_scope_sources(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    occurred_at = START + timedelta(days=1)
    assigned = _student_resource(evaluation_scenario)
    unrelated = EvaluationResource(
        student_person_id=evaluation_scenario.people["other_student"].id,
        cohort_id=evaluation_scenario.cohort.id,
        project_id=evaluation_scenario.unrelated_class.project_id,
        class_id=evaluation_scenario.unrelated_class.id,
    )
    _override(
        db,
        evaluation_scenario,
        account="admin",
        permission="evaluation_media.upload",
        effect="deny",
        scope_kind="cohort",
        scope_id=evaluation_scenario.cohort.id,
    )
    db.flush()
    service = EvaluationAccessService(db)
    cases = [
        ("admin", "student_game_accounts.manage", assigned),
        ("student", "student_game_accounts.manage", assigned),
        ("student", "student_game_accounts.manage", unrelated),
        ("guardian", "evaluation_media.original.read", assigned),
        ("primary_coach", "evaluation_media.original.read", assigned),
        ("primary_coach", "evaluation_media.original.read", unrelated),
        ("generic_staff", "evaluation_media.upload", assigned),
        ("generic_staff", "evaluation_media.upload", unrelated),
        ("admin", "evaluation_media.upload", assigned),
    ]

    for account_key, permission, resource in cases:
        expected = service.decide(
            account_id=evaluation_scenario.accounts[account_key].id,
            permission=permission,
            resource=resource,
            occurred_at=occurred_at,
        ).allowed
        authorization = service.sql_context(
            account_id=evaluation_scenario.accounts[account_key].id,
            permission=permission,
            occurred_at=occurred_at,
        )
        actual = db.scalar(
            select(
                authorization.allowed(
                    EvaluationSqlResource(
                        student_person_id=(
                            literal(resource.student_person_id)
                            if resource.student_person_id is not None
                            else None
                        ),
                        staff_person_id=(
                            literal(resource.staff_person_id)
                            if resource.staff_person_id is not None
                            else None
                        ),
                        cohort_id=(
                            literal(resource.cohort_id)
                            if resource.cohort_id is not None
                            else None
                        ),
                        project_id=(
                            literal(resource.project_id)
                            if resource.project_id is not None
                            else None
                        ),
                        class_id=(
                            literal(resource.class_id)
                            if resource.class_id is not None
                            else None
                        ),
                        enrollment_id=(
                            literal(resource.enrollment_id)
                            if resource.enrollment_id is not None
                            else None
                        ),
                    )
                )
            )
        )
        assert actual is expected, (account_key, permission, resource)


def test_actor_cannot_delegate_permission_or_scope_they_do_not_hold(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    before = db.scalar(select(func.count()).select_from(UserPermissionOverride))
    with pytest.raises(PermissionDelegationDenied):
        RbacService(db).add_override(
            actor_user_id=evaluation_scenario.accounts["student"].id,
            account_id=evaluation_scenario.accounts["guardian"].id,
            input=PermissionOverrideCreate(
                permission_id=evaluation_scenario.permissions["student_game_accounts.manage"].id,
                effect="allow",
                scope_kind="project",
                scope_id=evaluation_scenario.assigned_class.project_id,
                valid_from=START,
                valid_until=END,
                grant_reason="temporary project support",
            ),
        )
    after = db.scalar(select(func.count()).select_from(UserPermissionOverride))
    assert after == before


def test_family_delegation_requires_global_authority_for_future_relationships(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    target_account = evaluation_scenario.accounts["customer_service"]
    db.add(
        GuardianRelationship(
            guardian_person_id=target_account.person_id,
            student_person_id=evaluation_scenario.people["student"].id,
            relationship="guardian",
            is_primary=False,
            valid_from=START.date(),
        )
    )
    _override(
        db,
        evaluation_scenario,
        account="guardian",
        permission="student_game_accounts.manage",
        effect="allow",
        scope_kind="family",
    )
    db.commit()
    service = RbacService(db)
    before = db.scalar(select(func.count()).select_from(UserPermissionOverride))
    delegated = PermissionOverrideCreate(
        permission_id=evaluation_scenario.permissions[
            "student_game_accounts.manage"
        ].id,
        effect="allow",
        scope_kind="family",
        scope_id=None,
        valid_from=START,
        valid_until=END,
        grant_reason="temporary family game account support",
    )

    # Business access alone does not authorize permission administration.
    for actor_key in ("guardian", "admin"):
        actor = evaluation_scenario.accounts[actor_key]
        assert not service.decide(
            user_id=actor.id,
            permission="permissions.manage",
            resource_tokens={"global"},
        ).allowed
        with pytest.raises(PermissionDelegationDenied):
            service.add_override(
                actor_user_id=actor.id,
                account_id=target_account.id,
                input=delegated,
            )
        assert db.scalar(select(func.count()).select_from(UserPermissionOverride)) == before

    # Only these delegates gain the current management prerequisite. Their
    # distinct business-permission scopes still govern delegation below.
    manager_permission = db.scalar(
        select(Permission).where(Permission.key == "permissions.manage")
    )
    assert manager_permission is not None
    for actor_key in ("guardian", "admin"):
        db.add(UserPermissionOverride(
            user_id=evaluation_scenario.accounts[actor_key].id,
            permission_id=manager_permission.id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
            valid_from=None,
            valid_until=None,
            created_by_user_id=evaluation_scenario.accounts["admin"].id,
            grant_reason="family delegation test management prerequisite",
        ))
    db.commit()
    for actor_key in ("guardian", "admin"):
        actor = evaluation_scenario.accounts[actor_key]
        assert set(db.scalars(
            select(UserRole.role_id).where(UserRole.user_id == actor.id)
        )) == {ROLES[actor_key].id}
        assert not service.account_has_role(actor.id, "super_admin")
        assert service.decide(
            user_id=actor.id,
            permission="permissions.manage",
            resource_tokens={"global"},
        ).allowed
    before = db.scalar(select(func.count()).select_from(UserPermissionOverride))

    with pytest.raises(PermissionDelegationDenied):
        service.add_override(
            actor_user_id=evaluation_scenario.accounts["guardian"].id,
            account_id=target_account.id,
            input=delegated,
        )

    assert db.scalar(select(func.count()).select_from(UserPermissionOverride)) == before

    admin_deny = _override(
        db,
        evaluation_scenario,
        account="admin",
        permission="student_game_accounts.manage",
        effect="deny",
        scope_kind="person",
        scope_id=evaluation_scenario.people["other_student"].id,
    )
    db.commit()
    with pytest.raises(PermissionDelegationDenied):
        service.add_override(
            actor_user_id=evaluation_scenario.accounts["admin"].id,
            account_id=target_account.id,
            input=delegated,
        )
    assert db.scalar(
        select(UserPermissionOverride.id).where(
            UserPermissionOverride.user_id == target_account.id,
            UserPermissionOverride.permission_id == delegated.permission_id,
            UserPermissionOverride.scope_kind == "family",
        )
    ) is None

    stored_admin_deny = db.get(UserPermissionOverride, admin_deny.id)
    assert stored_admin_deny is not None
    db.delete(stored_admin_deny)
    db.commit()
    override, permission = service.add_override(
        actor_user_id=evaluation_scenario.accounts["admin"].id,
        account_id=target_account.id,
        input=delegated,
    )
    assert override.scope_kind == "family"
    assert permission.key == "student_game_accounts.manage"


def test_actor_grant_must_cover_the_entire_delegated_window(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    _override(
        db,
        evaluation_scenario,
        account="customer_service",
        permission="evaluation_media.upload",
        effect="allow",
        scope_kind="cohort",
        scope_id=evaluation_scenario.cohort.id,
        valid_from=START,
        valid_until=START + timedelta(days=2),
    )
    _override(
        db,
        evaluation_scenario,
        account="customer_service",
        permission="evaluation_media.upload",
        effect="allow",
        scope_kind="project",
        scope_id=evaluation_scenario.assigned_class.project_id,
        valid_from=END - timedelta(days=2),
        valid_until=END,
    )

    with pytest.raises(PermissionDelegationDenied):
        RbacService(db).add_override(
            actor_user_id=evaluation_scenario.accounts["customer_service"].id,
            account_id=evaluation_scenario.accounts["guardian"].id,
            input=PermissionOverrideCreate(
                permission_id=evaluation_scenario.permissions["evaluation_media.upload"].id,
                effect="allow",
                scope_kind="assigned_class",
                scope_id=evaluation_scenario.assigned_class.id,
                valid_from=START,
                valid_until=END,
                grant_reason="full window coverage test",
            ),
        )


@pytest.mark.parametrize(
    ("account", "scope_kind", "scope_id_key", "resource_factory"),
    [
        ("customer_service", "cohort", "cohort", lambda s: _student_resource(s)),
        ("customer_service", "project", "project", lambda s: _student_resource(s)),
        ("customer_service", "assigned_class", "class", lambda s: _student_resource(s)),
        ("customer_service", "enrollment", "enrollment", lambda s: _student_resource(s)),
        ("customer_service", "person", "person", lambda s: _student_resource(s)),
        (
            "student",
            "self",
            None,
            lambda s: EvaluationResource(student_person_id=s.people["student"].id),
        ),
        (
            "guardian",
            "family",
            None,
            lambda s: EvaluationResource(student_person_id=s.people["student"].id),
        ),
    ],
)
def test_override_scope_tokens_match_their_resource(
    db: Session,
    evaluation_scenario: EvaluationScenario,
    account: str,
    scope_kind: str,
    scope_id_key: str | None,
    resource_factory,
) -> None:
    resource = resource_factory(evaluation_scenario)
    scope_ids = {
        "cohort": resource.cohort_id,
        "project": resource.project_id,
        "class": resource.class_id,
        "enrollment": resource.enrollment_id,
        "person": resource.student_person_id,
    }
    _override(
        db,
        evaluation_scenario,
        account=account,
        permission="evaluation_media.upload",
        effect="allow",
        scope_kind=scope_kind,
        scope_id=scope_ids.get(scope_id_key) if scope_id_key is not None else None,
    )

    assert (
        EvaluationAccessService(db)
        .decide(
            account_id=evaluation_scenario.accounts[account].id,
            permission="evaluation_media.upload",
            resource=resource,
            occurred_at=START + timedelta(days=1),
        )
        .allowed
        is True
    )


def test_existing_indefinite_override_keeps_working(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    _override(
        db,
        evaluation_scenario,
        account="customer_service",
        permission="student_game_accounts.manage",
        effect="allow",
        scope_kind="person",
        scope_id=evaluation_scenario.people["student"].id,
        valid_from=None,
        valid_until=None,
    )

    assert (
        EvaluationAccessService(db)
        .decide(
            account_id=evaluation_scenario.accounts["customer_service"].id,
            permission="student_game_accounts.manage",
            resource=_student_resource(evaluation_scenario),
            occurred_at=START + timedelta(days=1),
        )
        .allowed
        is True
    )


def test_occurred_at_must_be_timezone_aware(
    db: Session,
    evaluation_scenario: EvaluationScenario,
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        EvaluationAccessService(db).decide(
            account_id=evaluation_scenario.accounts["admin"].id,
            permission="student_game_accounts.manage",
            resource=_student_resource(evaluation_scenario),
            occurred_at=START.replace(tzinfo=None),
        )
