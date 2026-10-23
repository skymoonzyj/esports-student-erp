from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.admissions.errors import (
    AdmissionsServiceError,
    ArchivedCohortLocked,
    CapacityOverrideReasonRequired,
    ClassCapacityReached,
    ClassCohortMismatch,
    ClassInactive,
)
from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    EnrollmentEvent,
    Payment,
    PaymentRefund,
)
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person
from app.teaching.models import Cohort, TeachingClass

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
SOURCE_CLASS_ID = UUID("f0000000-0000-4000-8000-000000000001")
TARGET_CLASS_ID = UUID("10000000-0000-4000-8000-000000000001")


def _person(db: Session, *, name: str) -> Person:
    marker = uuid4().hex
    person = Person(
        full_name=name,
        identity_ciphertext=f"enrollment-identity-{marker}",
        identity_digest=f"enrollment-identity-digest-{marker}",
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="female",
        date_of_birth=date(2000, 1, 1),
    )
    db.add(person)
    db.flush()
    return person


def _account(db: Session, *, person: Person) -> UserAccount:
    marker = uuid4().hex
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"enrollment-phone-{marker}",
        email_normalized=person.email or f"enrollment-{marker}@example.test",
        password_hash="enrollment-test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    return account


def _cohort(
    db: Session,
    *,
    actor_user_id: UUID,
    name: str,
    status: str = "open",
) -> Cohort:
    cohort = Cohort(
        name=f"{name}-{uuid4().hex[:8]}",
        starts_at=NOW,
        ends_at=NOW + timedelta(days=10),
        default_check_in_at=NOW,
        default_check_out_at=NOW + timedelta(days=10),
        status=status,
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=actor_user_id,
    )
    db.add(cohort)
    db.flush()
    return cohort


def _class(
    db: Session,
    *,
    cohort_id: UUID,
    actor_user_id: UUID,
    name: str,
    class_id: UUID | None = None,
    capacity: int = 2,
    default_tuition_cents: int = 100_000,
) -> TeachingClass:
    teaching_class = TeachingClass(
        id=class_id or uuid4(),
        cohort_id=cohort_id,
        project_id=PROJECT_ID,
        name=f"{name}-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=capacity,
        default_tuition_cents=default_tuition_cents,
        is_active=True,
        created_by_user_id=actor_user_id,
    )
    db.add(teaching_class)
    db.flush()
    return teaching_class


def _active_enrollment(
    db: Session,
    *,
    actor_user_id: UUID,
    student_person_id: UUID,
    cohort: Cohort,
    teaching_class: TeachingClass,
    agreed_tuition_cents: int = 88_000,
) -> tuple[AdmissionCase, Enrollment]:
    admission_case = AdmissionCase(
        student_person_id=student_person_id,
        status="ready",
        agreed_tuition_cents=agreed_tuition_cents,
        tuition_override_reason="初始学费审批",
        created_by_user_id=actor_user_id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=student_person_id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=agreed_tuition_cents,
        status="active",
        confirmed_at=NOW,
        created_by_user_id=actor_user_id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    db.add(
        EnrollmentEvent(
            enrollment_id=enrollment.id,
            event_type="confirmed",
            old_class_id=None,
            new_class_id=teaching_class.id,
            old_status=None,
            new_status="active",
            enrollment_count_snapshot=1,
            actor_user_id=actor_user_id,
            request_id=f"setup-{uuid4().hex}",
            occurred_at=NOW,
        )
    )
    db.flush()
    return admission_case, enrollment


@dataclass(frozen=True)
class EnrollmentScenario:
    db: Session
    admin: UserAccount
    student: Person
    cohort: Cohort
    source: TeachingClass
    target: TeachingClass
    other_cohort_target: TeachingClass
    admission_case: AdmissionCase
    enrollment: Enrollment

    def fill_target(self) -> Enrollment:
        self.target.capacity = 1
        filler = _person(self.db, name="占位学员")
        _, enrollment = _active_enrollment(
            self.db,
            actor_user_id=self.admin.id,
            student_person_id=filler.id,
            cohort=self.cohort,
            teaching_class=self.target,
            agreed_tuition_cents=200_000,
        )
        self.db.commit()
        return enrollment


@pytest.fixture
def enrollment_scenario(db: Session) -> EnrollmentScenario:
    admin_person = _person(db, name="报名生命周期管理员")
    admin = _account(db, person=admin_person)
    student = _person(db, name="报名生命周期学员")
    cohort = _cohort(db, actor_user_id=admin.id, name="生命周期营期")
    source = _class(
        db,
        cohort_id=cohort.id,
        actor_user_id=admin.id,
        name="原班级",
        class_id=SOURCE_CLASS_ID,
        default_tuition_cents=100_000,
    )
    target = _class(
        db,
        cohort_id=cohort.id,
        actor_user_id=admin.id,
        name="目标班级",
        class_id=TARGET_CLASS_ID,
        default_tuition_cents=200_000,
    )
    admission_case, enrollment = _active_enrollment(
        db,
        actor_user_id=admin.id,
        student_person_id=student.id,
        cohort=cohort,
        teaching_class=source,
    )
    payment = Payment(
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=None,
        payer_person_id=student.id,
        amount_cents=100_000,
        currency="CNY",
        paid_at=NOW,
        manual_note="生命周期测试付款",
        created_by_user_id=admin.id,
    )
    db.add(payment)
    db.flush()
    db.add(
        AdmissionCasePayment(
            admission_case_id=admission_case.id,
            payment_id=payment.id,
            attached_by_user_id=admin.id,
        )
    )
    other_cohort = _cohort(db, actor_user_id=admin.id, name="另一营期")
    other_target = _class(
        db,
        cohort_id=other_cohort.id,
        actor_user_id=admin.id,
        name="跨营期班级",
    )
    db.commit()
    return EnrollmentScenario(
        db=db,
        admin=admin,
        student=student,
        cohort=cohort,
        source=source,
        target=target,
        other_cohort_target=other_target,
        admission_case=admission_case,
        enrollment=enrollment,
    )


def _service(db: Session) -> Any:
    from app.admissions.enrollment_service import EnrollmentService

    return EnrollmentService(db)


def _transfer_input(
    target_class_id: UUID,
    *,
    reason: str = "调整训练方向",
    capacity_override_reason: str | None = None,
) -> Any:
    from app.admissions.schemas import TransferEnrollment

    return TransferEnrollment(
        target_class_id=target_class_id,
        reason=reason,
        capacity_override_reason=capacity_override_reason,
    )


def _cancel_input(*, reason: str = "学员改期") -> Any:
    from app.admissions.schemas import CancelEnrollment

    return CancelEnrollment(reason=reason)


def _normalized(statement: str) -> str:
    return " ".join(statement.lower().split())


def test_transfer_preserves_tuition_records_class_snapshots_and_locks_in_order(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario
    bind = scenario.db.get_bind()
    assert isinstance(bind, Connection)
    statements: list[str] = []

    def record_statement(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = _normalized(statement)
        if normalized.startswith("select") and " for update" in normalized:
            statements.append(normalized)

    before = scenario.enrollment.agreed_tuition_cents
    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        moved = _service(scenario.db).transfer(
            scenario.enrollment.id,
            _transfer_input(scenario.target.id),
            actor_user_id=scenario.admin.id,
            request_id="transfer-1",
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)

    assert moved.class_id == scenario.target.id
    assert moved.agreed_tuition_cents == before
    lifecycle_event = scenario.db.scalar(
        select(EnrollmentEvent).where(
            EnrollmentEvent.enrollment_id == scenario.enrollment.id,
            EnrollmentEvent.event_type == "transferred",
        )
    )
    assert lifecycle_event is not None
    assert lifecycle_event.old_class_id == scenario.source.id
    assert lifecycle_event.new_class_id == scenario.target.id
    assert (lifecycle_event.old_status, lifecycle_event.new_status) == (
        "active",
        "active",
    )
    assert lifecycle_event.reason == "调整训练方向"

    cohort_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from cohorts " in statement
    )
    class_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from teaching_classes " in statement
    )
    enrollment_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from enrollments " in statement
    )
    assert cohort_lock < class_lock < enrollment_lock
    assert "order by teaching_classes.id" in statements[class_lock]
    assert "order by enrollments.id" in statements[enrollment_lock]


def test_transfer_rejects_cross_cohort_target(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario

    with pytest.raises(ClassCohortMismatch) as error:
        _service(scenario.db).transfer(
            scenario.enrollment.id,
            _transfer_input(scenario.other_cohort_target.id),
            actor_user_id=scenario.admin.id,
            request_id="transfer-cross-cohort",
        )

    assert error.value.code == "class_cohort_mismatch"
    scenario.db.expire_all()
    stored = scenario.db.get(Enrollment, scenario.enrollment.id)
    assert stored is not None and stored.class_id == scenario.source.id


def test_transfer_rejects_inactive_target(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario
    scenario.target.is_active = False
    scenario.db.commit()

    with pytest.raises(ClassInactive) as error:
        _service(scenario.db).transfer(
            scenario.enrollment.id,
            _transfer_input(scenario.target.id),
            actor_user_id=scenario.admin.id,
            request_id="transfer-inactive",
        )

    assert error.value.code == "class_inactive"


def test_full_transfer_reason_never_grants_override_permission(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario
    scenario.fill_target()

    with pytest.raises(ClassCapacityReached) as error:
        _service(scenario.db).transfer(
            scenario.enrollment.id,
            _transfer_input(
                scenario.target.id,
                capacity_override_reason="仅填写原因不能授权",
            ),
            actor_user_id=scenario.admin.id,
            request_id="transfer-capacity-denied",
            capacity_override_allowed=False,
        )

    assert error.value.code == "class_capacity_reached"


def test_authorized_full_transfer_requires_nonblank_override_reason(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario
    scenario.fill_target()

    with pytest.raises(CapacityOverrideReasonRequired) as error:
        _service(scenario.db).transfer(
            scenario.enrollment.id,
            _transfer_input(scenario.target.id, capacity_override_reason="   "),
            actor_user_id=scenario.admin.id,
            request_id="transfer-capacity-reason-missing",
            capacity_override_allowed=True,
        )

    assert error.value.code == "capacity_override_reason_required"


def test_authorized_full_transfer_records_capacity_override(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario
    scenario.fill_target()

    moved = _service(scenario.db).transfer(
        scenario.enrollment.id,
        _transfer_input(
            scenario.target.id,
            capacity_override_reason="校长批准超额转班",
        ),
        actor_user_id=scenario.admin.id,
        request_id="transfer-capacity-approved",
        capacity_override_allowed=True,
    )

    assert moved.class_id == scenario.target.id
    assert moved.capacity_override_reason == "校长批准超额转班"
    event_types = list(
        scenario.db.scalars(
            select(EnrollmentEvent.event_type)
            .where(EnrollmentEvent.enrollment_id == scenario.enrollment.id)
            .order_by(EnrollmentEvent.occurred_at, EnrollmentEvent.id)
        )
    )
    assert event_types.count("transferred") == 1
    assert event_types.count("capacity_overridden") == 1


def test_archived_cohort_blocks_transfer_and_cancellation(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario
    scenario.cohort.status = "archived"
    scenario.db.commit()

    with pytest.raises(ArchivedCohortLocked) as transfer_error:
        _service(scenario.db).transfer(
            scenario.enrollment.id,
            _transfer_input(scenario.target.id),
            actor_user_id=scenario.admin.id,
            request_id="transfer-archived",
        )
    assert transfer_error.value.code == "archived_cohort_locked"

    with pytest.raises(ArchivedCohortLocked) as cancel_error:
        _service(scenario.db).cancel(
            scenario.enrollment.id,
            _cancel_input(),
            actor_user_id=scenario.admin.id,
            request_id="cancel-archived",
        )
    assert cancel_error.value.code == "archived_cohort_locked"


def test_cancel_reopens_case_releases_capacity_and_never_refunds(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario

    cancelled = _service(scenario.db).cancel(
        scenario.enrollment.id,
        _cancel_input(),
        actor_user_id=scenario.admin.id,
        request_id="cancel-1",
    )

    scenario.db.expire_all()
    stored_case = scenario.db.get(AdmissionCase, scenario.admission_case.id)
    stored = scenario.db.get(Enrollment, cancelled.id)
    assert stored is not None
    assert stored.status == "cancelled"
    assert stored.cancelled_at is not None
    assert stored.completed_at is None
    assert stored_case is not None
    assert stored_case.status == "ready"
    assert stored_case.current_enrollment_id is None
    assert stored_case.agreed_tuition_cents is None
    assert stored_case.tuition_override_reason is None
    assert scenario.db.scalar(
        select(func.count()).select_from(Enrollment).where(
            Enrollment.class_id == scenario.source.id,
            Enrollment.status == "active",
        )
    ) == 0
    assert scenario.db.scalar(select(func.count()).select_from(PaymentRefund)) == 0

    lifecycle_event = scenario.db.scalar(
        select(EnrollmentEvent).where(
            EnrollmentEvent.enrollment_id == stored.id,
            EnrollmentEvent.event_type == "cancelled",
        )
    )
    assert lifecycle_event is not None
    assert lifecycle_event.old_class_id == scenario.source.id
    assert lifecycle_event.new_class_id is None
    assert (lifecycle_event.old_status, lifecycle_event.new_status) == (
        "active",
        "cancelled",
    )
    assert lifecycle_event.reason == "学员改期"
    audit = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "admissions.enrollment.cancel",
            AuditEvent.resource_id == stored.id,
        )
    )
    assert audit is not None
    assert audit.event_metadata == {
        "student_person_id": str(scenario.student.id),
        "admission_case_id": str(stored.admission_case_id),
        "enrollment_id": str(stored.id),
        "cohort_id": str(scenario.cohort.id),
        "class_id": str(scenario.source.id),
    }


def test_cancel_rejects_a_non_active_enrollment(
    enrollment_scenario: EnrollmentScenario,
) -> None:
    scenario = enrollment_scenario
    _service(scenario.db).cancel(
        scenario.enrollment.id,
        _cancel_input(),
        actor_user_id=scenario.admin.id,
        request_id="cancel-first",
    )

    with pytest.raises(AdmissionsServiceError) as error:
        _service(scenario.db).cancel(
            scenario.enrollment.id,
            _cancel_input(reason="重复取消"),
            actor_user_id=scenario.admin.id,
            request_id="cancel-repeat",
        )

    assert error.value.code == "enrollment_not_active"


@pytest.mark.parametrize("schema_name", ["TransferEnrollment", "CancelEnrollment"])
def test_lifecycle_reason_schemas_reject_blank_values(schema_name: str) -> None:
    from app.admissions import schemas

    schema = getattr(schemas, schema_name)
    values: dict[str, object] = {"reason": "   "}
    if schema_name == "TransferEnrollment":
        values["target_class_id"] = uuid4()

    with pytest.raises(ValidationError):
        schema(**values)
