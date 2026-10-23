from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person, StaffProfile
from app.rbac.models import UserRole
from app.rbac.policy import PolicyDecision, RbacPolicy
from app.rbac.seed import ROLES
from app.rbac.service import RbacService
from app.teaching.coach_service import CoachService
from app.teaching.cohort_service import CohortService
from app.teaching.errors import (
    CoachAssignmentAlreadyEnded,
    CoachAssignmentConflict,
    CoachAssignmentNotFound,
    CoachNotEligible,
    CohortArchived,
    PrimaryCoachConflict,
)
from app.teaching.models import CoachAssignment, Cohort, CohortStatusEvent, TeachingClass
from app.teaching.schemas import (
    CoachAssignmentCreate,
    CoachAssignmentEndRequest,
    CohortCreate,
    TeachingClassCreate,
    TeachingClassUpdate,
)

PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)


def _create_account(
    db: Session,
    *,
    name: str,
    staff_type: str | None,
    account: bool = True,
    active: bool = True,
    coach_role: bool = True,
) -> tuple[Person, UserAccount | None]:
    marker = uuid4().hex
    person = Person(
        full_name=name,
        identity_ciphertext="coach-assignment-ciphertext",
        identity_digest=f"coach-assignment-identity-{marker}",
        phone_ciphertext=None,
        phone_digest=None,
        email=f"coach-{marker}@example.test",
        sex="female",
        date_of_birth=date(1988, 1, 2),
    )
    db.add(person)
    db.flush()
    if staff_type is not None:
        db.add(StaffProfile(person_id=person.id, staff_type=staff_type))
    if not account:
        db.flush()
        return person, None
    stored = UserAccount(
        person_id=person.id,
        login_phone_digest=f"coach-assignment-phone-{marker}",
        email_normalized=f"coach-{marker}@example.test",
        password_hash="coach-assignment-password-hash",
        status="active" if active else "disabled",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(stored)
    db.flush()
    if coach_role:
        db.add(UserRole(user_id=stored.id, role_id=ROLES["coach"].id))
    db.flush()
    return person, stored


@pytest.fixture
def assignment_setup(db: Session):
    actor, actor_account = _create_account(
        db,
        name="教练分配测试管理员",
        staff_type="administration",
        coach_role=False,
    )
    del actor
    assert actor_account is not None
    coach, coach_account = _create_account(
        db,
        name="合格分配教练",
        staff_type="coach",
    )
    assert coach_account is not None
    cohort_service = CohortService(db)
    cohort = cohort_service.create_cohort(
        CohortCreate(
            name="教练分配测试营期",
            starts_at=NOW + timedelta(days=10),
            ends_at=NOW + timedelta(days=30),
            default_check_in_at=NOW + timedelta(days=10),
            default_check_out_at=NOW + timedelta(days=30),
        ),
        actor_user_id=actor_account.id,
    )
    first_class = cohort_service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name="教练分配一班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
        ),
        actor_user_id=actor_account.id,
    )
    second_class = cohort_service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name="教练分配二班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
        ),
        actor_user_id=actor_account.id,
    )
    return {
        "actor_id": actor_account.id,
        "coach": coach,
        "coach_account": coach_account,
        "cohort": cohort,
        "first_class": first_class,
        "second_class": second_class,
        "cohort_service": cohort_service,
        "service": CoachService(db),
    }


@pytest.mark.parametrize(
    ("staff_type", "account", "active", "coach_role", "reason"),
    [
        ("administration", True, True, True, "not_coach_staff"),
        ("coach", False, True, True, "account_missing_or_disabled"),
        ("coach", True, False, True, "account_missing_or_disabled"),
        ("coach", True, True, False, "coach_role_missing"),
    ],
)
def test_assignment_rejects_ineligible_coach_with_stable_reason(
    db: Session,
    assignment_setup,
    staff_type: str,
    account: bool,
    active: bool,
    coach_role: bool,
    reason: str,
) -> None:
    person, _ = _create_account(
        db,
        name=f"不合格教练 {reason}",
        staff_type=staff_type,
        account=account,
        active=active,
        coach_role=coach_role,
    )

    with pytest.raises(CoachNotEligible) as error:
        assignment_setup["service"].create_assignment(
            assignment_setup["first_class"].id,
            CoachAssignmentCreate(
                coach_person_id=person.id,
                assignment_type="assistant",
                valid_from=NOW,
            ),
            actor_user_id=assignment_setup["actor_id"],
        )

    assert error.value.code == "coach_not_eligible"
    assert error.value.reason == reason
    assert db.scalar(
        select(func.count(CoachAssignment.id)).where(
            CoachAssignment.coach_person_id == person.id
        )
    ) == 0


def test_assignment_rejects_soft_deleted_coach_with_stable_reason(
    db: Session,
    assignment_setup,
) -> None:
    person, _ = _create_account(
        db,
        name="已删除教练",
        staff_type="coach",
    )
    person.deleted_at = NOW
    db.flush()

    with pytest.raises(CoachNotEligible) as error:
        assignment_setup["service"].create_assignment(
            assignment_setup["first_class"].id,
            CoachAssignmentCreate(
                coach_person_id=person.id,
                assignment_type="assistant",
                valid_from=NOW,
            ),
            actor_user_id=assignment_setup["actor_id"],
        )

    assert error.value.code == "coach_not_eligible"
    assert error.value.reason == "person_missing_or_deleted"
    assert db.scalar(
        select(func.count(CoachAssignment.id)).where(
            CoachAssignment.coach_person_id == person.id
        )
    ) == 0


class DecisionCountingPolicy(RbacPolicy):
    def __init__(self) -> None:
        self.calls = 0

    def decide(
        self,
        permission: str,
        resource_tokens: set[str],
        role_rules,
        overrides,
    ) -> PolicyDecision:
        self.calls += 1
        return super().decide(permission, resource_tokens, role_rules, overrides)


def test_teaching_visibility_evaluates_only_relevant_classes(
    db: Session,
    assignment_setup,
) -> None:
    service = assignment_setup["service"]
    first_class = assignment_setup["first_class"]
    coach = assignment_setup["coach"]
    coach_account = assignment_setup["coach_account"]
    service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            coach_person_id=coach.id,
            assignment_type="assistant",
            valid_from=NOW,
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    policy = DecisionCountingPolicy()

    visibility = RbacService(db, policy=policy).teaching_visibility(
        user_id=coach_account.id,
        permission="teaching.read",
        actor_person_id=coach.id,
        at=NOW,
    )

    assert visibility.global_access is False
    assert visibility.class_ids == frozenset({first_class.id})
    assert policy.calls == 2


def test_primary_assignments_cannot_overlap(
    db: Session,
    assignment_setup,
) -> None:
    service = assignment_setup["service"]
    first_class = assignment_setup["first_class"]
    actor_id = assignment_setup["actor_id"]
    first_coach = assignment_setup["coach"]
    second_coach, _ = _create_account(
        db,
        name="第二主教练",
        staff_type="coach",
    )
    service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            coach_person_id=first_coach.id,
            assignment_type="primary",
            valid_from=NOW,
        ),
        actor_user_id=actor_id,
    )

    with pytest.raises(PrimaryCoachConflict) as error:
        service.create_assignment(
            first_class.id,
            CoachAssignmentCreate(
                coach_person_id=second_coach.id,
                assignment_type="primary",
                valid_from=NOW + timedelta(hours=1),
            ),
            actor_user_id=actor_id,
        )

    assert error.value.code == "primary_coach_conflict"
    assert db.scalar(
        select(func.count(CoachAssignment.id)).where(
            CoachAssignment.class_id == first_class.id
        )
    ) == 1


def test_same_coach_cannot_overlap_roles_in_one_class(assignment_setup) -> None:
    service = assignment_setup["service"]
    first_class = assignment_setup["first_class"]
    coach = assignment_setup["coach"]
    actor_id = assignment_setup["actor_id"]
    service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            coach_person_id=coach.id,
            assignment_type="assistant",
            valid_from=NOW,
        ),
        actor_user_id=actor_id,
    )

    with pytest.raises(CoachAssignmentConflict) as error:
        service.create_assignment(
            first_class.id,
            CoachAssignmentCreate(
                coach_person_id=coach.id,
                assignment_type="primary",
                valid_from=NOW + timedelta(hours=1),
            ),
            actor_user_id=actor_id,
        )

    assert error.value.code == "coach_assignment_conflict"


def test_end_requires_reason_and_preserves_assignment_history(assignment_setup) -> None:
    service = assignment_setup["service"]
    first_class = assignment_setup["first_class"]
    assignment = service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            coach_person_id=assignment_setup["coach"].id,
            assignment_type="assistant",
            valid_from=NOW,
            valid_until=NOW + timedelta(days=30),
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    with pytest.raises(ValidationError):
        CoachAssignmentEndRequest(reason=" ")

    ended = service.end_assignment(
        first_class.id,
        assignment.id,
        CoachAssignmentEndRequest(reason="  教学安排调整  "),
        actor_user_id=assignment_setup["actor_id"],
        occurred_at=NOW + timedelta(days=5),
    )
    history, total = service.list_assignments(
        first_class.id,
        include_ended=True,
        page=1,
        page_size=20,
    )
    active, active_total = service.list_assignments(
        first_class.id,
        include_ended=False,
        page=1,
        page_size=20,
    )

    assert ended.ended_at == NOW + timedelta(days=5)
    assert ended.end_reason == "教学安排调整"
    assert ended.valid_until == NOW + timedelta(days=30)
    assert total == 1 and [item.id for item in history] == [assignment.id]
    assert active_total == 0 and active == []


def test_end_resolves_assignment_by_class_and_rejects_repeat(
    db: Session,
    assignment_setup,
) -> None:
    service = assignment_setup["service"]
    first_class = assignment_setup["first_class"]
    second_class = assignment_setup["second_class"]
    assignment = service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            coach_person_id=assignment_setup["coach"].id,
            assignment_type="assistant",
            valid_from=NOW,
        ),
        actor_user_id=assignment_setup["actor_id"],
    )

    with pytest.raises(CoachAssignmentNotFound):
        service.end_assignment(
            second_class.id,
            assignment.id,
            CoachAssignmentEndRequest(reason="错误班级"),
            actor_user_id=assignment_setup["actor_id"],
            occurred_at=NOW + timedelta(days=1),
        )

    first_end = service.end_assignment(
        first_class.id,
        assignment.id,
        CoachAssignmentEndRequest(reason="首次结束"),
        actor_user_id=assignment_setup["actor_id"],
        occurred_at=NOW + timedelta(days=2),
    )
    first_snapshot = (first_end.ended_at, first_end.end_reason)
    with pytest.raises(CoachAssignmentAlreadyEnded) as error:
        service.end_assignment(
            first_class.id,
            assignment.id,
            CoachAssignmentEndRequest(reason="不得覆盖"),
            actor_user_id=assignment_setup["actor_id"],
            occurred_at=NOW + timedelta(days=3),
        )

    assert error.value.code == "coach_assignment_already_ended"
    db.expire_all()
    stored = db.get(CoachAssignment, assignment.id)
    assert stored is not None
    assert (stored.ended_at, stored.end_reason) == first_snapshot


def test_archived_cohort_rejects_assignment_creation_and_end(assignment_setup) -> None:
    service = assignment_setup["service"]
    cohort_service = assignment_setup["cohort_service"]
    cohort = assignment_setup["cohort"]
    first_class = assignment_setup["first_class"]
    second_class = assignment_setup["second_class"]
    assignment = service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            coach_person_id=assignment_setup["coach"].id,
            assignment_type="primary",
            valid_from=NOW,
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    service.create_assignment(
        second_class.id,
        CoachAssignmentCreate(
            coach_person_id=assignment_setup["coach"].id,
            assignment_type="primary",
            valid_from=NOW,
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    for target in ("open", "active", "completed", "archived"):
        cohort_service.transition(
            cohort.id,
            target=target,
            trigger="manual",
            actor_user_id=assignment_setup["actor_id"],
            occurred_at=NOW,
            request_id=f"assignment-archive-{target}",
        )

    with pytest.raises(CohortArchived):
        service.create_assignment(
            first_class.id,
            CoachAssignmentCreate(
                coach_person_id=assignment_setup["coach"].id,
                assignment_type="assistant",
                valid_from=NOW + timedelta(days=1),
            ),
            actor_user_id=assignment_setup["actor_id"],
        )
    with pytest.raises(CohortArchived):
        service.end_assignment(
            first_class.id,
            assignment.id,
            CoachAssignmentEndRequest(reason="归档后不得结束"),
            actor_user_id=assignment_setup["actor_id"],
            occurred_at=NOW + timedelta(days=1),
        )


def test_later_non_overlapping_assignment_is_allowed(assignment_setup) -> None:
    service = assignment_setup["service"]
    first_class = assignment_setup["first_class"]
    common = {
        "coach_person_id": assignment_setup["coach"].id,
        "assignment_type": "assistant",
    }
    first = service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            **common,
            valid_from=NOW,
            valid_until=NOW + timedelta(days=2),
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    second = service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            **common,
            valid_from=NOW + timedelta(days=2),
        ),
        actor_user_id=assignment_setup["actor_id"],
    )

    assert first.id != second.id


def test_ended_history_rejects_retroactive_overlap(assignment_setup) -> None:
    service = assignment_setup["service"]
    first_class = assignment_setup["first_class"]
    coach_id = assignment_setup["coach"].id
    assignment = service.create_assignment(
        first_class.id,
        CoachAssignmentCreate(
            coach_person_id=coach_id,
            assignment_type="assistant",
            valid_from=NOW,
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    service.end_assignment(
        first_class.id,
        assignment.id,
        CoachAssignmentEndRequest(reason="五天后结束"),
        actor_user_id=assignment_setup["actor_id"],
        occurred_at=NOW + timedelta(days=5),
    )

    with pytest.raises(CoachAssignmentConflict):
        service.create_assignment(
            first_class.id,
            CoachAssignmentCreate(
                coach_person_id=coach_id,
                assignment_type="assistant",
                valid_from=NOW + timedelta(days=1),
                valid_until=NOW + timedelta(days=2),
            ),
            actor_user_id=assignment_setup["actor_id"],
        )


def test_assignment_inputs_require_aware_validity() -> None:
    with pytest.raises(ValidationError):
        CoachAssignmentCreate(
            coach_person_id=uuid4(),
            assignment_type="assistant",
            valid_from=NOW.replace(tzinfo=None),
        )
    with pytest.raises(ValidationError):
        CoachAssignmentCreate(
            coach_person_id=uuid4(),
            assignment_type="assistant",
            valid_from=NOW,
            valid_until=NOW,
        )


def test_current_assignment_scope_requires_active_account_and_coach_role(
    db: Session,
    assignment_setup,
) -> None:
    service = assignment_setup["service"]
    teaching_class = assignment_setup["first_class"]
    coach = assignment_setup["coach"]
    coach_account = assignment_setup["coach_account"]
    service.create_assignment(
        teaching_class.id,
        CoachAssignmentCreate(
            coach_person_id=coach.id,
            assignment_type="assistant",
            valid_from=NOW,
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    rbac = RbacService(db)

    assert "assigned_class" in rbac.class_resource_tokens(
        actor_person_id=coach.id,
        class_id=teaching_class.id,
        at=NOW + timedelta(hours=1),
    )

    coach_account.status = "disabled"
    db.flush()
    assert "assigned_class" not in rbac.class_resource_tokens(
        actor_person_id=coach.id,
        class_id=teaching_class.id,
        at=NOW + timedelta(hours=1),
    )

    coach_account.status = "active"
    role_assignment = db.get(UserRole, (coach_account.id, ROLES["coach"].id))
    assert role_assignment is not None
    db.delete(role_assignment)
    db.flush()
    assert "assigned_class" not in rbac.class_resource_tokens(
        actor_person_id=coach.id,
        class_id=teaching_class.id,
        at=NOW + timedelta(hours=1),
    )


def test_soft_deleted_assigned_coach_immediately_loses_class_scope(
    db: Session,
    assignment_setup,
) -> None:
    service = assignment_setup["service"]
    teaching_class = assignment_setup["first_class"]
    coach = assignment_setup["coach"]
    service.create_assignment(
        teaching_class.id,
        CoachAssignmentCreate(
            coach_person_id=coach.id,
            assignment_type="assistant",
            valid_from=NOW,
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    rbac = RbacService(db)
    at = NOW + timedelta(hours=1)
    assert "assigned_class" in rbac.class_resource_tokens(
        actor_person_id=coach.id,
        class_id=teaching_class.id,
        at=at,
    )

    coach.deleted_at = NOW
    db.flush()

    assert "assigned_class" not in rbac.class_resource_tokens(
        actor_person_id=coach.id,
        class_id=teaching_class.id,
        at=at,
    )


def test_assignment_scope_uses_half_open_validity_boundaries(
    db: Session,
    assignment_setup,
) -> None:
    service = assignment_setup["service"]
    teaching_class = assignment_setup["first_class"]
    coach = assignment_setup["coach"]
    assignment = service.create_assignment(
        teaching_class.id,
        CoachAssignmentCreate(
            coach_person_id=coach.id,
            assignment_type="assistant",
            valid_from=NOW,
            valid_until=NOW + timedelta(hours=2),
        ),
        actor_user_id=assignment_setup["actor_id"],
    )
    rbac = RbacService(db)

    def has_scope(at: datetime) -> bool:
        return "assigned_class" in rbac.class_resource_tokens(
            actor_person_id=coach.id,
            class_id=teaching_class.id,
            at=at,
        )

    assert not has_scope(NOW - timedelta(microseconds=1))
    assert has_scope(NOW)
    assert has_scope(NOW + timedelta(hours=1, microseconds=-1))
    service.end_assignment(
        teaching_class.id,
        assignment.id,
        CoachAssignmentEndRequest(reason="边界时刻结束"),
        actor_user_id=assignment_setup["actor_id"],
        occurred_at=NOW + timedelta(hours=1),
    )
    assert not has_scope(NOW + timedelta(hours=1))
    assert not has_scope(NOW + timedelta(hours=2))


def test_assignment_history_orders_by_start_then_uuid_descending(
    db: Session,
    assignment_setup,
) -> None:
    service = assignment_setup["service"]
    teaching_class = assignment_setup["first_class"]
    second_coach, _ = _create_account(
        db,
        name="历史排序二号教练",
        staff_type="coach",
    )
    third_coach, _ = _create_account(
        db,
        name="历史排序三号教练",
        staff_type="coach",
    )
    same_start = [
        service.create_assignment(
            teaching_class.id,
            CoachAssignmentCreate(
                coach_person_id=coach_id,
                assignment_type="assistant",
                valid_from=NOW,
            ),
            actor_user_id=assignment_setup["actor_id"],
        )
        for coach_id in (assignment_setup["coach"].id, second_coach.id)
    ]
    latest = service.create_assignment(
        teaching_class.id,
        CoachAssignmentCreate(
            coach_person_id=third_coach.id,
            assignment_type="assistant",
            valid_from=NOW + timedelta(hours=1),
        ),
        actor_user_id=assignment_setup["actor_id"],
    )

    history, total = service.list_assignments(
        teaching_class.id,
        include_ended=True,
        page=1,
        page_size=20,
    )

    assert total == 3
    assert [item.id for item in history] == [
        latest.id,
        *sorted((item.id for item in same_start), reverse=True),
    ]


def test_eligible_coach_directory_uses_shared_eligibility_and_pagination(
    db: Session,
    assignment_setup,
) -> None:
    eligible, _ = _create_account(
        db,
        name="候选合格教练",
        staff_type="coach",
    )
    _create_account(
        db,
        name="候选停用教练",
        staff_type="coach",
        active=False,
    )
    _create_account(
        db,
        name="候选无角色教练",
        staff_type="coach",
        coach_role=False,
    )
    _create_account(
        db,
        name="候选非教练员工",
        staff_type="customer_service",
    )

    people, total = assignment_setup["service"].eligible_coaches(
        assignment_setup["first_class"].id,
        query="候选",
        page=1,
        page_size=1,
    )

    assert total == 1
    assert [person.id for person in people] == [eligible.id]

def test_stale_concurrent_end_cannot_rewrite_first_history(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    with Session(postgres_engine) as setup:
        actor, actor_account = _create_account(
            setup,
            name="并发结束操作员",
            staff_type="administration",
            coach_role=False,
        )
        del actor
        coach, coach_account = _create_account(
            setup,
            name="并发结束教练",
            staff_type="coach",
        )
        assert actor_account is not None
        assert coach_account is not None
        cohort_service = CohortService(setup)
        cohort = cohort_service.create_cohort(
            CohortCreate(
                name=f"并发结束营期 {uuid4().hex[:8]}",
                starts_at=NOW + timedelta(days=10),
                ends_at=NOW + timedelta(days=30),
                default_check_in_at=NOW + timedelta(days=10),
                default_check_out_at=NOW + timedelta(days=30),
            ),
            actor_user_id=actor_account.id,
        )
        teaching_class = cohort_service.create_class(
            TeachingClassCreate(
                cohort_id=cohort.id,
                project_id=PROJECT_ID,
                name="并发结束班级",
                class_type="standard",
                capacity=20,
                default_tuition_cents=0,
            ),
            actor_user_id=actor_account.id,
        )
        assignment = CoachService(setup).create_assignment(
            teaching_class.id,
            CoachAssignmentCreate(
                coach_person_id=coach.id,
                assignment_type="assistant",
                valid_from=NOW,
            ),
            actor_user_id=actor_account.id,
        )
        ids = {
            "actor_person": actor_account.person_id,
            "actor_account": actor_account.id,
            "coach_person": coach.id,
            "coach_account": coach_account.id,
            "class": teaching_class.id,
            "cohort": cohort.id,
            "assignment": assignment.id,
        }

    def cleanup() -> None:
        with Session(postgres_engine) as cleanup_db, cleanup_db.begin():
            cleanup_db.execute(
                delete(CoachAssignment).where(CoachAssignment.id == ids["assignment"])
            )
            cleanup_db.execute(
                delete(TeachingClass).where(TeachingClass.id == ids["class"])
            )
            cleanup_db.execute(delete(Cohort).where(Cohort.id == ids["cohort"]))
            cleanup_db.execute(
                delete(UserRole).where(
                    UserRole.user_id.in_([ids["actor_account"], ids["coach_account"]])
                )
            )
            cleanup_db.execute(
                delete(UserAccount).where(
                    UserAccount.person_id.in_([ids["actor_person"], ids["coach_person"]])
                )
            )
            cleanup_db.execute(
                delete(StaffProfile).where(
                    StaffProfile.person_id.in_([ids["actor_person"], ids["coach_person"]])
                )
            )
            cleanup_db.execute(
                delete(Person).where(
                    Person.id.in_([ids["actor_person"], ids["coach_person"]])
                )
            )

    request.addfinalizer(cleanup)

    with Session(postgres_engine, expire_on_commit=False) as stale_session:
        stale = stale_session.get(CoachAssignment, ids["assignment"])
        assert stale is not None and stale.ended_at is None
        with Session(postgres_engine) as first_session:
            first = CoachService(first_session).end_assignment(
                ids["class"],
                ids["assignment"],
                CoachAssignmentEndRequest(reason="首次结束保留"),
                actor_user_id=ids["actor_account"],
                occurred_at=NOW + timedelta(days=2),
            )
            first_snapshot = (first.ended_at, first.end_reason)

        with pytest.raises(CoachAssignmentAlreadyEnded):
            CoachService(stale_session).end_assignment(
                ids["class"],
                ids["assignment"],
                CoachAssignmentEndRequest(reason="并发不得覆盖"),
                actor_user_id=ids["actor_account"],
                occurred_at=NOW + timedelta(days=3),
            )

    with Session(postgres_engine) as verification:
        stored = verification.get(CoachAssignment, ids["assignment"])
        assert stored is not None
        assert (stored.ended_at, stored.end_reason) == first_snapshot


@pytest.mark.parametrize("write_kind", ["coach_assignment", "class_update"])
def test_teaching_writes_and_open_transition_use_compatible_lock_order(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
    write_kind: str,
) -> None:
    with Session(postgres_engine) as setup:
        actor, actor_account = _create_account(
            setup,
            name="锁顺序操作员",
            staff_type="administration",
            coach_role=False,
        )
        del actor
        primary, primary_account = _create_account(
            setup,
            name="锁顺序主教练",
            staff_type="coach",
        )
        assistant, assistant_account = _create_account(
            setup,
            name="锁顺序助教",
            staff_type="coach",
        )
        assert actor_account is not None
        assert primary_account is not None
        assert assistant_account is not None
        cohort_service = CohortService(setup)
        cohort = cohort_service.create_cohort(
            CohortCreate(
                name=f"锁顺序营期 {uuid4().hex[:8]}",
                starts_at=NOW + timedelta(days=10),
                ends_at=NOW + timedelta(days=30),
                default_check_in_at=NOW + timedelta(days=10),
                default_check_out_at=NOW + timedelta(days=30),
            ),
            actor_user_id=actor_account.id,
        )
        teaching_class = cohort_service.create_class(
            TeachingClassCreate(
                cohort_id=cohort.id,
                project_id=PROJECT_ID,
                name="锁顺序班级",
                class_type="standard",
                capacity=20,
                default_tuition_cents=0,
            ),
            actor_user_id=actor_account.id,
        )
        CoachService(setup).create_assignment(
            teaching_class.id,
            CoachAssignmentCreate(
                coach_person_id=primary.id,
                assignment_type="primary",
                valid_from=NOW,
            ),
            actor_user_id=actor_account.id,
        )
        ids = {
            "actor_person": actor_account.person_id,
            "actor_account": actor_account.id,
            "primary_person": primary.id,
            "primary_account": primary_account.id,
            "assistant_person": assistant.id,
            "assistant_account": assistant_account.id,
            "class": teaching_class.id,
            "cohort": cohort.id,
        }

    def cleanup() -> None:
        with Session(postgres_engine) as cleanup_db, cleanup_db.begin():
            cleanup_db.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            try:
                cleanup_db.execute(
                    delete(AuditEvent).where(AuditEvent.resource_id == ids["cohort"])
                )
            finally:
                cleanup_db.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            cleanup_db.execute(
                delete(CoachAssignment).where(CoachAssignment.class_id == ids["class"])
            )
            cleanup_db.execute(
                delete(CohortStatusEvent).where(CohortStatusEvent.cohort_id == ids["cohort"])
            )
            cleanup_db.execute(
                delete(TeachingClass).where(TeachingClass.id == ids["class"])
            )
            cleanup_db.execute(delete(Cohort).where(Cohort.id == ids["cohort"]))
            account_ids = [
                ids["actor_account"],
                ids["primary_account"],
                ids["assistant_account"],
            ]
            person_ids = [
                ids["actor_person"],
                ids["primary_person"],
                ids["assistant_person"],
            ]
            cleanup_db.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
            cleanup_db.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
            cleanup_db.execute(
                delete(StaffProfile).where(StaffProfile.person_id.in_(person_ids))
            )
            cleanup_db.execute(delete(Person).where(Person.id.in_(person_ids)))

    request.addfinalizer(cleanup)
    assignment_thread_id: list[int] = []
    assignment_locked_class = threading.Event()
    allow_assignment_to_continue = threading.Event()
    transition_started = threading.Event()
    transition_backend_pid: list[int] = []
    errors: list[Exception] = []

    def pause_after_assignment_class_lock(
        conn,
        cursor,
        statement: str,
        parameters,
        context,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        if (
            assignment_thread_id
            and threading.get_ident() == assignment_thread_id[0]
            and "FROM teaching_classes" in statement
            and "FOR UPDATE" in statement
        ):
            assignment_locked_class.set()
            assert allow_assignment_to_continue.wait(timeout=10)

    event.listen(postgres_engine, "after_cursor_execute", pause_after_assignment_class_lock)

    def perform_teaching_write() -> None:
        assignment_thread_id.append(threading.get_ident())
        try:
            with Session(postgres_engine) as worker:
                if write_kind == "coach_assignment":
                    CoachService(worker).create_assignment(
                        ids["class"],
                        CoachAssignmentCreate(
                            coach_person_id=ids["assistant_person"],
                            assignment_type="assistant",
                            valid_from=NOW,
                        ),
                        actor_user_id=ids["actor_account"],
                    )
                else:
                    CohortService(worker).update_class(
                        ids["class"],
                        TeachingClassUpdate(name="锁顺序班级已更新"),
                        actor_user_id=ids["actor_account"],
                    )
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    def open_cohort() -> None:
        try:
            with Session(postgres_engine) as worker:
                backend_pid = worker.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(backend_pid, int)
                transition_backend_pid.append(backend_pid)
                transition_started.set()
                CohortService(worker).transition(
                    ids["cohort"],
                    target="open",
                    trigger="manual",
                    actor_user_id=ids["actor_account"],
                    occurred_at=NOW + timedelta(hours=1),
                    request_id="coach-lock-order-open",
                )
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    assignment_worker = threading.Thread(target=perform_teaching_write)
    transition_worker = threading.Thread(target=open_cohort)
    try:
        assignment_worker.start()
        assert assignment_locked_class.wait(timeout=5)
        transition_worker.start()
        assert transition_started.wait(timeout=5)
        deadline = time.monotonic() + 5
        transition_waiting = False
        while time.monotonic() < deadline:
            with postgres_engine.connect() as observer:
                wait_event_type = observer.scalar(
                    text(
                        "SELECT wait_event_type FROM pg_stat_activity "
                        "WHERE pid = :backend_pid"
                    ),
                    {"backend_pid": transition_backend_pid[0]},
                )
            if wait_event_type == "Lock":
                transition_waiting = True
                break
            time.sleep(0.01)
        assert transition_waiting
        allow_assignment_to_continue.set()
        assignment_worker.join(timeout=10)
        transition_worker.join(timeout=10)
    finally:
        allow_assignment_to_continue.set()
        assignment_worker.join(timeout=10)
        transition_worker.join(timeout=10)
        event.remove(
            postgres_engine,
            "after_cursor_execute",
            pause_after_assignment_class_lock,
        )

    assert not assignment_worker.is_alive()
    assert not transition_worker.is_alive()
    assert errors == []
    with Session(postgres_engine) as verification:
        stored = verification.get(Cohort, ids["cohort"])
        assert stored is not None and stored.status == "open"
        assignment_count = verification.scalar(
            select(func.count(CoachAssignment.id)).where(
                CoachAssignment.class_id == ids["class"]
            )
        )
        assert assignment_count == (2 if write_kind == "coach_assignment" else 1)
        stored_class = verification.get(TeachingClass, ids["class"])
        assert stored_class is not None
        expected_name = (
            "锁顺序班级"
            if write_kind == "coach_assignment"
            else "锁顺序班级已更新"
        )
        assert stored_class.name == expected_name
