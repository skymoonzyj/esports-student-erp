from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select, text, update
from sqlalchemy.orm import Session

from app.admissions.models import EnrollmentEvent
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.evaluations.errors import EvaluationIdempotencyConflict
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.staff_models import CohortStaffAssignment, EvaluationStaffRole
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffRating,
    CampSurveyStaffSnapshot,
    CampSurveySubmission,
    CampSurveyVersion,
)
from app.evaluations.survey_snapshot_service import CampSurveySnapshotService
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.identity.models import Person, StaffProfile
from app.teaching.cohort_service import CohortService
from app.teaching.models import CoachAssignment, TeachingClass
from tests.account_identifiers import add_test_primary_login_identifier
from tests.services.test_teaching_scheduler import (
    NOW,
    PROJECT_ID,
    _add_active_enrollment,
    _add_actor,
    _add_cohort,
)


@dataclass(frozen=True)
class _PersistentScenario:
    cycle_id: UUID
    enrollment_id: UUID
    admission_case_id: UUID
    class_id: UUID
    cohort_id: UUID
    actor_id: UUID
    assistant_assignment_id: UUID
    assistant_person_id: UUID
    person_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class _PersistentFirstOpenScenario:
    enrollment_id: UUID
    admission_case_id: UUID
    class_id: UUID
    cohort_id: UUID
    actor_id: UUID
    person_ids: tuple[UUID, ...]


def _add_coach(
    db: Session,
    *,
    label: str,
    account_status: str = "active",
    with_staff_profile: bool = True,
    deleted: bool = False,
) -> Person:
    unique = uuid4().hex
    person = Person(
        full_name=label,
        identity_ciphertext=f"snapshot-{label}-ciphertext",
        identity_digest=f"snapshot-{unique}",
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="female",
        date_of_birth=NOW.date().replace(year=1990),
        deleted_at=NOW if deleted else None,
    )
    db.add(person)
    db.flush()
    if with_staff_profile:
        db.add(StaffProfile(person_id=person.id, staff_type="coach"))
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"snapshot-phone-{unique}",
        email_normalized=f"snapshot-{unique}@example.test",
        password_hash="snapshot-test-password-hash",
        status=account_status,
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    add_test_primary_login_identifier(db, account)
    return person


def _service(db: Session) -> CampSurveySnapshotService:
    return CampSurveySnapshotService(
        db,
        idempotency=EvaluationIdempotencyService(db, digest_key=b"s" * 32),
    )


def _seed_persistent_scenario(engine: Engine) -> _PersistentScenario:
    with Session(engine, expire_on_commit=False) as db:
        actor_person, actor = _add_actor(db, marker="snapshot-concurrent")
        cohort = _add_cohort(
            db,
            actor_user_id=actor.id,
            name="snapshot concurrent",
            status="active",
            starts_at=NOW - timedelta(days=2),
            ends_at=NOW + timedelta(days=1),
            auto_start_enabled=False,
            auto_complete_enabled=False,
        )
        enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
        primary = _add_coach(db, label=f"并发主教练-{actor.id}")
        db.add(
            CoachAssignment(
                class_id=enrollment.class_id,
                coach_person_id=primary.id,
                assignment_type="primary",
                valid_from=enrollment.confirmed_at,
                valid_until=NOW,
                created_by_user_id=actor.id,
            )
        )
        db.commit()
        CohortService(db).transition(
            cohort.id,
            target="completed",
            trigger="manual",
            actor_user_id=actor.id,
            occurred_at=NOW,
            request_id="snapshot-concurrent-completion",
        )
        cycle = db.scalar(
            select(CampSurveyCycle).where(
                CampSurveyCycle.enrollment_id == enrollment.id
            )
        )
        assert cycle is not None
        assistant = _add_coach(db, label=f"并发助教-{actor.id}")
        assistant_assignment = CoachAssignment(
            class_id=enrollment.class_id,
            coach_person_id=assistant.id,
            assignment_type="assistant",
            valid_from=enrollment.confirmed_at + timedelta(hours=1),
            valid_until=NOW,
            created_by_user_id=actor.id,
        )
        db.add(assistant_assignment)
        db.commit()
        return _PersistentScenario(
            cycle_id=cycle.id,
            enrollment_id=enrollment.id,
            admission_case_id=enrollment.admission_case_id,
            class_id=enrollment.class_id,
            cohort_id=cohort.id,
            actor_id=actor.id,
            assistant_assignment_id=assistant_assignment.id,
            assistant_person_id=assistant.id,
            person_ids=(actor_person.id, primary.id, assistant.id),
        )


def _seed_persistent_first_open_scenario(
    engine: Engine,
) -> _PersistentFirstOpenScenario:
    with Session(engine, expire_on_commit=False) as db:
        actor_person, actor = _add_actor(db, marker="snapshot-first-open")
        cohort = _add_cohort(
            db,
            actor_user_id=actor.id,
            name="snapshot first open",
            status="active",
            starts_at=NOW - timedelta(days=2),
            ends_at=NOW,
            auto_start_enabled=False,
            auto_complete_enabled=False,
        )
        enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
        primary = _add_coach(db, label=f"首次并发主教练-{actor.id}")
        db.add(
            CoachAssignment(
                class_id=enrollment.class_id,
                coach_person_id=primary.id,
                assignment_type="primary",
                valid_from=enrollment.confirmed_at,
                valid_until=NOW,
                created_by_user_id=actor.id,
            )
        )
        enrollment.status = "completed"
        enrollment.completed_at = NOW
        enrollment.updated_by_user_id = actor.id
        db.commit()
        return _PersistentFirstOpenScenario(
            enrollment_id=enrollment.id,
            admission_case_id=enrollment.admission_case_id,
            class_id=enrollment.class_id,
            cohort_id=cohort.id,
            actor_id=actor.id,
            person_ids=(actor_person.id, primary.id),
        )


def _cleanup_persistent_scenario(
    engine: Engine, scenario: _PersistentScenario
) -> None:
    with engine.connect() as connection:
        connection.execute(
            text(
                "ALTER TABLE camp_survey_staff_snapshots "
                "DISABLE TRIGGER camp_survey_staff_snapshots_immutable"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE camp_survey_snapshot_versions "
                "DISABLE TRIGGER camp_survey_snapshot_versions_immutable"
            )
        )
        connection.execute(
            text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
        )
        connection.commit()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM evaluation_idempotency_keys "
                    "WHERE actor_key = :actor_key"
                ),
                {"actor_key": str(scenario.actor_id)},
            )
            connection.execute(
                text("DELETE FROM audit_events WHERE actor_user_id = :actor_id"),
                {"actor_id": scenario.actor_id},
            )
            connection.execute(
                text(
                    "DELETE FROM camp_survey_staff_snapshots "
                    "WHERE snapshot_version_id IN "
                    "(SELECT id FROM camp_survey_snapshot_versions WHERE cycle_id = :cycle_id)"
                ),
                {"cycle_id": scenario.cycle_id},
            )
            connection.execute(
                text(
                    "DELETE FROM camp_survey_snapshot_versions "
                    "WHERE cycle_id = :cycle_id"
                ),
                {"cycle_id": scenario.cycle_id},
            )
            connection.execute(
                text("DELETE FROM camp_survey_cycles WHERE id = :cycle_id"),
                {"cycle_id": scenario.cycle_id},
            )
            connection.execute(
                text(
                    "UPDATE admission_cases SET current_enrollment_id = NULL, "
                    "status = 'ready' WHERE id = :case_id"
                ),
                {"case_id": scenario.admission_case_id},
            )
            connection.execute(
                text("DELETE FROM enrollment_events WHERE enrollment_id = :enrollment_id"),
                {"enrollment_id": scenario.enrollment_id},
            )
            connection.execute(
                text("DELETE FROM coach_assignments WHERE class_id = :class_id"),
                {"class_id": scenario.class_id},
            )
            connection.execute(
                text("DELETE FROM enrollments WHERE id = :enrollment_id"),
                {"enrollment_id": scenario.enrollment_id},
            )
            connection.execute(
                text("DELETE FROM admission_cases WHERE id = :case_id"),
                {"case_id": scenario.admission_case_id},
            )
            connection.execute(
                text("DELETE FROM teaching_classes WHERE id = :class_id"),
                {"class_id": scenario.class_id},
            )
            connection.execute(
                text("DELETE FROM cohort_status_events WHERE cohort_id = :cohort_id"),
                {"cohort_id": scenario.cohort_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :cohort_id"),
                {"cohort_id": scenario.cohort_id},
            )
            connection.execute(
                text(
                    "DELETE FROM staff_profiles "
                    "WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                ),
                {"person_ids": list(scenario.person_ids)},
            )
            connection.execute(
                text(
                    "DELETE FROM user_accounts "
                    "WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                ),
                {"person_ids": list(scenario.person_ids)},
            )
            connection.execute(
                text("DELETE FROM people WHERE id = ANY(CAST(:person_ids AS uuid[]))"),
                {"person_ids": list(scenario.person_ids)},
            )
    finally:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "ALTER TABLE camp_survey_staff_snapshots "
                    "ENABLE TRIGGER camp_survey_staff_snapshots_immutable"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE camp_survey_snapshot_versions "
                    "ENABLE TRIGGER camp_survey_snapshot_versions_immutable"
                )
            )
            connection.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            connection.commit()


def _cleanup_persistent_first_open_scenario(
    engine: Engine,
    scenario: _PersistentFirstOpenScenario,
) -> None:
    with engine.connect() as connection:
        connection.execute(
            text(
                "ALTER TABLE camp_survey_staff_snapshots "
                "DISABLE TRIGGER camp_survey_staff_snapshots_immutable"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE camp_survey_snapshot_versions "
                "DISABLE TRIGGER camp_survey_snapshot_versions_immutable"
            )
        )
        connection.commit()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM camp_survey_staff_snapshots "
                    "WHERE snapshot_version_id IN ("
                    "SELECT snapshot.id FROM camp_survey_snapshot_versions snapshot "
                    "JOIN camp_survey_cycles cycle ON cycle.id = snapshot.cycle_id "
                    "WHERE cycle.enrollment_id = :enrollment_id)"
                ),
                {"enrollment_id": scenario.enrollment_id},
            )
            connection.execute(
                text(
                    "DELETE FROM camp_survey_snapshot_versions "
                    "WHERE cycle_id IN (SELECT id FROM camp_survey_cycles "
                    "WHERE enrollment_id = :enrollment_id)"
                ),
                {"enrollment_id": scenario.enrollment_id},
            )
            connection.execute(
                text(
                    "DELETE FROM camp_survey_cycles "
                    "WHERE enrollment_id = :enrollment_id"
                ),
                {"enrollment_id": scenario.enrollment_id},
            )
            connection.execute(
                text(
                    "UPDATE admission_cases SET current_enrollment_id = NULL, "
                    "status = 'ready' WHERE id = :case_id"
                ),
                {"case_id": scenario.admission_case_id},
            )
            connection.execute(
                text(
                    "DELETE FROM enrollment_events "
                    "WHERE enrollment_id = :enrollment_id"
                ),
                {"enrollment_id": scenario.enrollment_id},
            )
            connection.execute(
                text("DELETE FROM coach_assignments WHERE class_id = :class_id"),
                {"class_id": scenario.class_id},
            )
            connection.execute(
                text("DELETE FROM enrollments WHERE id = :enrollment_id"),
                {"enrollment_id": scenario.enrollment_id},
            )
            connection.execute(
                text("DELETE FROM admission_cases WHERE id = :case_id"),
                {"case_id": scenario.admission_case_id},
            )
            connection.execute(
                text("DELETE FROM teaching_classes WHERE id = :class_id"),
                {"class_id": scenario.class_id},
            )
            connection.execute(
                text("DELETE FROM cohort_status_events WHERE cohort_id = :cohort_id"),
                {"cohort_id": scenario.cohort_id},
            )
            connection.execute(
                text("DELETE FROM cohorts WHERE id = :cohort_id"),
                {"cohort_id": scenario.cohort_id},
            )
            connection.execute(
                text(
                    "DELETE FROM staff_profiles "
                    "WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                ),
                {"person_ids": list(scenario.person_ids)},
            )
            connection.execute(
                text(
                    "DELETE FROM user_accounts "
                    "WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                ),
                {"person_ids": list(scenario.person_ids)},
            )
            connection.execute(
                text(
                    "DELETE FROM people "
                    "WHERE id = ANY(CAST(:person_ids AS uuid[]))"
                ),
                {"person_ids": list(scenario.person_ids)},
            )
    finally:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "ALTER TABLE camp_survey_staff_snapshots "
                    "ENABLE TRIGGER camp_survey_staff_snapshots_immutable"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE camp_survey_snapshot_versions "
                    "ENABLE TRIGGER camp_survey_snapshot_versions_immutable"
                )
            )
            connection.commit()


def test_unused_snapshot_rebuild_is_content_idempotent_and_audited_once(
    db: Session,
) -> None:
    _, actor = _add_actor(db, marker="snapshot-rebuild")
    cohort = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="snapshot rebuild",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW + timedelta(days=1),
        auto_start_enabled=False,
        auto_complete_enabled=False,
    )
    enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
    primary = _add_coach(db, label="快照主教练")
    db.add(
        CoachAssignment(
            class_id=enrollment.class_id,
            coach_person_id=primary.id,
            assignment_type="primary",
            valid_from=enrollment.confirmed_at,
            valid_until=NOW,
            created_by_user_id=actor.id,
        )
    )
    db.commit()

    CohortService(db).transition(
        cohort.id,
        target="completed",
        trigger="manual",
        actor_user_id=actor.id,
        occurred_at=NOW,
        request_id="snapshot-initial-completion",
    )
    cycle = db.scalar(
        select(CampSurveyCycle).where(CampSurveyCycle.enrollment_id == enrollment.id)
    )
    assert cycle is not None
    assistant = _add_coach(db, label="补录助教")
    db.add(
        CoachAssignment(
            class_id=enrollment.class_id,
            coach_person_id=assistant.id,
            assignment_type="assistant",
            valid_from=enrollment.confirmed_at + timedelta(hours=1),
            valid_until=NOW,
            created_by_user_id=actor.id,
        )
    )
    db.commit()

    service = _service(db)
    lock_order: list[str] = []

    def record_lock(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        normalized = " ".join(statement.upper().split())
        if "FOR UPDATE" not in normalized:
            return
        for table, label in (
            ("COHORTS", "cohort"),
            ("ENROLLMENTS", "enrollment"),
            ("COACH_ASSIGNMENTS", "coach"),
            ("COHORT_STAFF_ASSIGNMENTS", "staff"),
            ("CAMP_SURVEY_CYCLES", "cycle"),
        ):
            if f"FROM {table}" in normalized:
                lock_order.append(label)
                return

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record_lock)
    try:
        first = service.create_snapshot_version(
            cycle.id,
            actor_user_id=actor.id,
            change_reason="补录实际服务助教，并排除误纳入主教练",
            excluded_staff_person_ids={primary.id},
            idempotency_key="snapshot-correction-1",
            request_id="snapshot-correction-request-1",
            occurred_at=NOW + timedelta(minutes=1),
        )
    finally:
        event.remove(connection, "before_cursor_execute", record_lock)
    assert lock_order == ["cohort", "enrollment", "coach", "staff", "cycle"]
    replay = service.create_snapshot_version(
        cycle.id,
        actor_user_id=actor.id,
        change_reason="补录实际服务助教，并排除误纳入主教练",
        excluded_staff_person_ids={primary.id},
        idempotency_key="snapshot-correction-1",
        request_id="snapshot-correction-request-replay",
        occurred_at=NOW + timedelta(minutes=2),
    )
    equivalent = service.create_snapshot_version(
        cycle.id,
        actor_user_id=actor.id,
        change_reason="补录实际服务助教，并排除误纳入主教练",
        excluded_staff_person_ids={primary.id},
        idempotency_key="snapshot-correction-equivalent",
        request_id="snapshot-correction-request-equivalent",
        occurred_at=NOW + timedelta(minutes=3),
    )
    with pytest.raises(EvaluationIdempotencyConflict):
        service.create_snapshot_version(
            cycle.id,
            actor_user_id=actor.id,
            change_reason="同一幂等键换成不同修正内容",
            excluded_staff_person_ids={assistant.id},
            idempotency_key="snapshot-correction-1",
            request_id="snapshot-correction-request-conflict",
            occurred_at=NOW + timedelta(minutes=4),
        )
    assert db.scalar(
        select(func.count(CampSurveyCycle.id)).where(CampSurveyCycle.id == cycle.id)
    ) == 1
    before_unknown_exclusion = (
        db.scalar(
            select(CampSurveyCycle.current_snapshot_version).where(
                CampSurveyCycle.id == cycle.id
            )
        ),
        db.scalar(
            select(func.count(CampSurveySnapshotVersion.id)).where(
                CampSurveySnapshotVersion.cycle_id == cycle.id
            )
        ),
        db.scalar(
            select(func.count(CampSurveyStaffSnapshot.id)).join(
                CampSurveySnapshotVersion,
                CampSurveySnapshotVersion.id
                == CampSurveyStaffSnapshot.snapshot_version_id,
            ).where(CampSurveySnapshotVersion.cycle_id == cycle.id)
        ),
        db.scalar(
            select(func.count(EvaluationIdempotencyKey.id)).where(
                EvaluationIdempotencyKey.actor_key == str(actor.id),
                EvaluationIdempotencyKey.scope
                == "camp_survey.snapshot.version",
            )
        ),
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "camp_survey.snapshot.version.create",
            )
        ),
    )
    with pytest.raises(ValueError, match="excluded staff is not in the survey roster"):
        service.create_snapshot_version(
            cycle.id,
            actor_user_id=actor.id,
            change_reason="排除一个从未服务过的未知人员",
            excluded_staff_person_ids={uuid4()},
            idempotency_key="snapshot-correction-unknown-exclusion",
            request_id="snapshot-correction-unknown-exclusion-request",
            occurred_at=NOW + timedelta(minutes=5),
        )
    after_unknown_exclusion = (
        db.scalar(
            select(CampSurveyCycle.current_snapshot_version).where(
                CampSurveyCycle.id == cycle.id
            )
        ),
        db.scalar(
            select(func.count(CampSurveySnapshotVersion.id)).where(
                CampSurveySnapshotVersion.cycle_id == cycle.id
            )
        ),
        db.scalar(
            select(func.count(CampSurveyStaffSnapshot.id)).join(
                CampSurveySnapshotVersion,
                CampSurveySnapshotVersion.id
                == CampSurveyStaffSnapshot.snapshot_version_id,
            ).where(CampSurveySnapshotVersion.cycle_id == cycle.id)
        ),
        db.scalar(
            select(func.count(EvaluationIdempotencyKey.id)).where(
                EvaluationIdempotencyKey.actor_key == str(actor.id),
                EvaluationIdempotencyKey.scope
                == "camp_survey.snapshot.version",
            )
        ),
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "camp_survey.snapshot.version.create",
            )
        ),
    )
    assert after_unknown_exclusion == before_unknown_exclusion

    db.expire_all()
    stored_cycle = db.get(CampSurveyCycle, cycle.id)
    assert stored_cycle is not None
    assert stored_cycle.current_snapshot_version == 2
    assert first.snapshot.version == 2
    assert replay.snapshot.id == equivalent.snapshot.id == first.snapshot.id
    assert tuple(row.staff_person_id for row in first.staff) == tuple(
        row.staff_person_id for row in replay.staff
    ) == tuple(row.staff_person_id for row in equivalent.staff)
    assert first.idempotent is False
    assert replay.idempotent is equivalent.idempotent is True
    assert db.scalar(
        select(func.count(CampSurveySnapshotVersion.id)).where(
            CampSurveySnapshotVersion.cycle_id == cycle.id
        )
    ) == 2
    rows = list(
        db.scalars(
            select(CampSurveyStaffSnapshot).where(
                CampSurveyStaffSnapshot.snapshot_version_id == first.snapshot.id
            )
        )
    )
    assert {row.staff_person_id for row in rows} == {primary.id, assistant.id}
    assert {
        row.staff_person_id: row.include_in_summary for row in rows
    } == {primary.id: False, assistant.id: True}
    audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "camp_survey.snapshot.version.create",
                AuditEvent.resource_id == first.snapshot.id,
            )
        )
    )
    assert len(audits) == 1
    assert audits[0].actor_user_id == actor.id
    assert audits[0].purpose is None
    assert audits[0].request_id == "snapshot-correction-request-1"
    assert audits[0].event_metadata == {
        "cohort_id": str(cohort.id),
        "enrollment_id": str(enrollment.id),
        "snapshot_version": 2,
        "item_count": 2,
    }
    assert db.scalar(
        select(func.count(EvaluationIdempotencyKey.id)).where(
            EvaluationIdempotencyKey.actor_key == str(actor.id),
            EvaluationIdempotencyKey.scope == "camp_survey.snapshot.version",
        )
    ) == 2


def test_snapshot_excludes_deleted_disabled_and_unprofiled_staff(
    db: Session,
) -> None:
    _, actor = _add_actor(db, marker="snapshot-eligibility")
    cohort = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="snapshot eligibility",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW + timedelta(days=1),
        auto_start_enabled=False,
        auto_complete_enabled=False,
    )
    enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
    active = _add_coach(db, label="有效主教练")
    disabled = _add_coach(
        db,
        label="停用账号助教",
        account_status="disabled",
    )
    unprofiled = _add_coach(
        db,
        label="无员工档案助教",
        with_staff_profile=False,
    )
    deleted = _add_coach(db, label="已删除助教", deleted=True)
    for person, assignment_type in (
        (active, "primary"),
        (disabled, "assistant"),
        (unprofiled, "assistant"),
        (deleted, "assistant"),
    ):
        db.add(
            CoachAssignment(
                class_id=enrollment.class_id,
                coach_person_id=person.id,
                assignment_type=assignment_type,
                valid_from=enrollment.confirmed_at,
                valid_until=NOW,
                created_by_user_id=actor.id,
            )
        )
    db.commit()

    CohortService(db).transition(
        cohort.id,
        target="completed",
        trigger="manual",
        actor_user_id=actor.id,
        occurred_at=NOW,
        request_id="snapshot-eligibility-completion",
    )
    cycle = db.scalar(
        select(CampSurveyCycle).where(CampSurveyCycle.enrollment_id == enrollment.id)
    )
    assert cycle is not None
    snapshot = db.scalar(
        select(CampSurveySnapshotVersion).where(
            CampSurveySnapshotVersion.cycle_id == cycle.id,
            CampSurveySnapshotVersion.version == 1,
        )
    )
    assert snapshot is not None
    staff_ids = set(
        db.scalars(
            select(CampSurveyStaffSnapshot.staff_person_id).where(
                CampSurveyStaffSnapshot.snapshot_version_id == snapshot.id
            )
        )
    )

    assert staff_ids == {active.id}


def test_snapshot_rebuilds_transferred_class_and_project_staff_history(
    db: Session,
) -> None:
    _, actor = _add_actor(db, marker="snapshot-transfer-history")
    cohort = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="snapshot transfer history",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW,
        auto_start_enabled=False,
        auto_complete_enabled=False,
    )
    enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
    first_class = db.get(TeachingClass, enrollment.class_id)
    assert first_class is not None
    second_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"snapshot-transferred-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=actor.id,
    )
    db.add(second_class)
    db.flush()

    confirmed_at = enrollment.confirmed_at
    transferred_at = NOW - timedelta(days=1)
    early_departure_at = confirmed_at + timedelta(hours=12)
    joined_at = transferred_at + timedelta(hours=3)
    first_class_coach = _add_coach(db, label="转班前主教练")
    second_class_coach = _add_coach(db, label="转班后主教练")
    life_teacher = _add_coach(db, label="提前离任生活老师")
    custom_staff = _add_coach(db, label="开营后加入自定义角色")
    inactive_staff = _add_coach(db, label="停用角色工作人员")
    no_overlap_staff = _add_coach(db, label="无重叠工作人员")

    marker = uuid4().hex[:8]
    project_assistant_role = EvaluationStaffRole(
        key=f"project_assistant_{marker}",
        name="项目助教",
        category="teaching",
        is_builtin=False,
        is_active=True,
        created_by_user_id=actor.id,
    )
    life_role = EvaluationStaffRole(
        key=f"life_teacher_{marker}",
        name="生活老师",
        category="life_service",
        is_builtin=False,
        is_active=True,
        created_by_user_id=actor.id,
    )
    custom_role = EvaluationStaffRole(
        key=f"custom_operations_{marker}",
        name="自定义运营角色",
        category="operations",
        is_builtin=False,
        is_active=True,
        created_by_user_id=actor.id,
    )
    inactive_role = EvaluationStaffRole(
        key=f"inactive_staff_{marker}",
        name="已停用角色",
        category="operations",
        is_builtin=False,
        is_active=False,
        created_by_user_id=actor.id,
    )
    db.add_all(
        (project_assistant_role, life_role, custom_role, inactive_role)
    )
    db.flush()

    db.add_all(
        (
            CoachAssignment(
                class_id=first_class.id,
                coach_person_id=first_class_coach.id,
                assignment_type="primary",
                valid_from=confirmed_at,
                valid_until=transferred_at,
                created_by_user_id=actor.id,
            ),
            CoachAssignment(
                class_id=second_class.id,
                coach_person_id=second_class_coach.id,
                assignment_type="primary",
                valid_from=confirmed_at,
                valid_until=NOW,
                created_by_user_id=actor.id,
            ),
            CoachAssignment(
                class_id=first_class.id,
                coach_person_id=no_overlap_staff.id,
                assignment_type="assistant",
                valid_from=transferred_at + timedelta(hours=1),
                valid_until=NOW,
                created_by_user_id=actor.id,
            ),
            CohortStaffAssignment(
                cohort_id=cohort.id,
                role_id=project_assistant_role.id,
                staff_person_id=first_class_coach.id,
                project_id=PROJECT_ID,
                class_id=None,
                role_name_snapshot=project_assistant_role.name,
                role_category_snapshot=project_assistant_role.category,
                valid_from=confirmed_at,
                valid_until=NOW,
                ended_at=None,
                ended_by_user_id=None,
                end_reason=None,
                created_by_user_id=actor.id,
            ),
            CohortStaffAssignment(
                cohort_id=cohort.id,
                role_id=life_role.id,
                staff_person_id=life_teacher.id,
                project_id=PROJECT_ID,
                class_id=None,
                role_name_snapshot=life_role.name,
                role_category_snapshot=life_role.category,
                valid_from=confirmed_at,
                valid_until=NOW,
                ended_at=early_departure_at,
                ended_by_user_id=actor.id,
                end_reason="提前离任",
                created_by_user_id=actor.id,
            ),
            CohortStaffAssignment(
                cohort_id=cohort.id,
                role_id=custom_role.id,
                staff_person_id=custom_staff.id,
                project_id=PROJECT_ID,
                class_id=None,
                role_name_snapshot=custom_role.name,
                role_category_snapshot=custom_role.category,
                valid_from=joined_at,
                valid_until=NOW,
                ended_at=None,
                ended_by_user_id=None,
                end_reason=None,
                created_by_user_id=actor.id,
            ),
            CohortStaffAssignment(
                cohort_id=cohort.id,
                role_id=inactive_role.id,
                staff_person_id=inactive_staff.id,
                project_id=PROJECT_ID,
                class_id=None,
                role_name_snapshot=inactive_role.name,
                role_category_snapshot=inactive_role.category,
                valid_from=confirmed_at,
                valid_until=NOW,
                ended_at=None,
                ended_by_user_id=None,
                end_reason=None,
                created_by_user_id=actor.id,
            ),
        )
    )
    db.add_all(
        (
            EnrollmentEvent(
                enrollment_id=enrollment.id,
                event_type="confirmed",
                old_class_id=None,
                new_class_id=first_class.id,
                old_status=None,
                new_status="active",
                reason="确认报名",
                enrollment_count_snapshot=1,
                actor_user_id=actor.id,
                request_id="snapshot-transfer-confirmed",
                occurred_at=confirmed_at,
            ),
            EnrollmentEvent(
                enrollment_id=enrollment.id,
                event_type="transferred",
                old_class_id=first_class.id,
                new_class_id=second_class.id,
                old_status="active",
                new_status="active",
                reason="中途转班",
                enrollment_count_snapshot=1,
                actor_user_id=actor.id,
                request_id="snapshot-transfer-moved",
                occurred_at=transferred_at,
            ),
            EnrollmentEvent(
                enrollment_id=enrollment.id,
                event_type="completed",
                old_class_id=second_class.id,
                new_class_id=None,
                old_status="active",
                new_status="completed",
                reason="确认结营",
                enrollment_count_snapshot=1,
                actor_user_id=actor.id,
                request_id="snapshot-transfer-completed",
                occurred_at=NOW,
            ),
        )
    )
    enrollment.class_id = second_class.id
    enrollment.status = "completed"
    enrollment.completed_at = NOW
    enrollment.updated_by_user_id = actor.id
    db.commit()

    result = CampSurveySnapshotService(db).ensure_cycle(
        enrollment.id,
        actor_user_id=actor.id,
    )

    assert {row.staff_person_id for row in result.staff} == {
        first_class_coach.id,
        second_class_coach.id,
        life_teacher.id,
        custom_staff.id,
    }
    first_coach_row = next(
        row for row in result.staff if row.staff_person_id == first_class_coach.id
    )
    assert {item["role_key"] for item in first_coach_row.roles_snapshot} == {
        "primary_coach",
        project_assistant_role.key,
    }
    assert any(
        item["scope"] == f"class:{first_class.id}"
        and datetime.fromisoformat(item["from"]) == confirmed_at
        and datetime.fromisoformat(item["until"]) == transferred_at
        for item in first_coach_row.service_intervals
    )
    project_intervals = [
        item
        for item in first_coach_row.service_intervals
        if item["role_key"] == project_assistant_role.key
    ]
    assert len(project_intervals) == 1
    assert datetime.fromisoformat(project_intervals[0]["from"]) == confirmed_at
    assert datetime.fromisoformat(project_intervals[0]["until"]) == NOW

    life_row = next(
        row for row in result.staff if row.staff_person_id == life_teacher.id
    )
    assert datetime.fromisoformat(life_row.service_intervals[0]["until"]) == (
        early_departure_at
    )
    custom_row = next(
        row for row in result.staff if row.staff_person_id == custom_staff.id
    )
    assert datetime.fromisoformat(custom_row.service_intervals[0]["from"]) == joined_at
    second_coach_row = next(
        row for row in result.staff if row.staff_person_id == second_class_coach.id
    )
    second_class_intervals = [
        item
        for item in second_coach_row.service_intervals
        if item["scope"] == f"class:{second_class.id}"
    ]
    assert len(second_class_intervals) == 1
    assert datetime.fromisoformat(second_class_intervals[0]["from"]) == transferred_at
    assert datetime.fromisoformat(second_class_intervals[0]["until"]) == NOW


def test_removed_wrong_assignment_is_carried_forward_as_excluded(
    db: Session,
) -> None:
    _, actor = _add_actor(db, marker="snapshot-exclusion")
    cohort = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="snapshot exclusion",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW + timedelta(days=1),
        auto_start_enabled=False,
        auto_complete_enabled=False,
    )
    enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
    wrong_staff = _add_coach(db, label="误录工作人员")
    wrong_assignment = CoachAssignment(
        class_id=enrollment.class_id,
        coach_person_id=wrong_staff.id,
        assignment_type="primary",
        valid_from=enrollment.confirmed_at,
        valid_until=NOW,
        created_by_user_id=actor.id,
    )
    db.add(wrong_assignment)
    db.commit()
    CohortService(db).transition(
        cohort.id,
        target="completed",
        trigger="manual",
        actor_user_id=actor.id,
        occurred_at=NOW,
        request_id="snapshot-exclusion-completion",
    )
    cycle = db.scalar(
        select(CampSurveyCycle).where(CampSurveyCycle.enrollment_id == enrollment.id)
    )
    assert cycle is not None
    db.delete(wrong_assignment)
    db.commit()

    result = _service(db).create_snapshot_version(
        cycle.id,
        actor_user_id=actor.id,
        change_reason="删除误录服务历史并从后续汇总排除",
        excluded_staff_person_ids={wrong_staff.id},
        idempotency_key="snapshot-exclusion-correction",
        request_id="snapshot-exclusion-request",
        occurred_at=NOW + timedelta(minutes=1),
    )

    assert result.snapshot.version == 2
    assert len(result.staff) == 1
    assert result.staff[0].staff_person_id == wrong_staff.id
    assert result.staff[0].include_in_summary is False


def test_correction_after_submission_appends_without_mutating_old_history(
    db: Session,
) -> None:
    _, actor = _add_actor(db, marker="snapshot-submitted")
    cohort = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="snapshot submitted",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW + timedelta(days=1),
        auto_start_enabled=False,
        auto_complete_enabled=False,
    )
    enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
    primary = _add_coach(db, label="已评分主教练")
    db.add(
        CoachAssignment(
            class_id=enrollment.class_id,
            coach_person_id=primary.id,
            assignment_type="primary",
            valid_from=enrollment.confirmed_at,
            valid_until=NOW,
            created_by_user_id=actor.id,
        )
    )
    db.commit()
    CohortService(db).transition(
        cohort.id,
        target="completed",
        trigger="manual",
        actor_user_id=actor.id,
        occurred_at=NOW,
        request_id="snapshot-submitted-completion",
    )
    cycle = db.scalar(
        select(CampSurveyCycle).where(CampSurveyCycle.enrollment_id == enrollment.id)
    )
    assert cycle is not None
    old_snapshot = db.scalar(
        select(CampSurveySnapshotVersion).where(
            CampSurveySnapshotVersion.cycle_id == cycle.id,
            CampSurveySnapshotVersion.version == 1,
        )
    )
    assert old_snapshot is not None
    old_staff = db.scalar(
        select(CampSurveyStaffSnapshot).where(
            CampSurveyStaffSnapshot.snapshot_version_id == old_snapshot.id
        )
    )
    assert old_staff is not None
    old_staff_signature = (
        old_staff.staff_person_id,
        old_staff.staff_name_snapshot,
        old_staff.role_key_snapshot,
        old_staff.role_name_snapshot,
        old_staff.role_category,
        old_staff.roles_snapshot,
        old_staff.service_intervals,
        old_staff.include_in_summary,
    )
    submitted_at = NOW + timedelta(seconds=30)
    submission = CampSurveySubmission(
        cycle_id=cycle.id,
        respondent_person_id=enrollment.student_person_id,
        student_person_id=enrollment.student_person_id,
        respondent_kind="self",
        guardian_relationship_id=None,
        guardian_relationship_snapshot=None,
        revision_count=0,
        latest_version=1,
        first_submitted_at=submitted_at,
        last_submitted_at=submitted_at,
    )
    db.add(submission)
    db.flush()
    version = CampSurveyVersion(
        submission_id=submission.id,
        snapshot_version_id=old_snapshot.id,
        cycle_id=cycle.id,
        version=1,
        course_training_score=8,
        course_training_reason=None,
        life_service_score=8,
        life_service_reason=None,
        service_management_score=8,
        service_management_reason=None,
        overall_suggestion=None,
        favorite_cuisines=[
            {
                "cuisine_option_id": str(uuid4()),
                "name": "川菜",
                "custom_name": None,
            }
        ],
        favorite_dishes=["鱼香肉丝"],
        food_preference_version=None,
        published_at=submitted_at,
    )
    db.add(version)
    db.flush()
    rating = CampSurveyStaffRating(
        version_id=version.id,
        staff_snapshot_id=old_staff.id,
        snapshot_version_id=old_snapshot.id,
        score=8,
        reason="训练安排清晰",
        suggestion=None,
    )
    db.add(rating)
    assistant = _add_coach(db, label="提交后补录助教")
    db.add(
        CoachAssignment(
            class_id=enrollment.class_id,
            coach_person_id=assistant.id,
            assignment_type="assistant",
            valid_from=enrollment.confirmed_at + timedelta(hours=1),
            valid_until=NOW,
            created_by_user_id=actor.id,
        )
    )
    db.commit()

    result = _service(db).create_snapshot_version(
        cycle.id,
        actor_user_id=actor.id,
        change_reason="提交后补录实际服务助教",
        idempotency_key="snapshot-submitted-correction",
        request_id="snapshot-submitted-request",
        occurred_at=NOW + timedelta(minutes=1),
    )

    db.expire_all()
    assert result.snapshot.version == 2
    assert {row.staff_person_id for row in result.staff} == {
        primary.id,
        assistant.id,
    }
    stored_old_snapshot = db.get(CampSurveySnapshotVersion, old_snapshot.id)
    stored_old_staff = db.get(CampSurveyStaffSnapshot, old_staff.id)
    stored_rating = db.get(CampSurveyStaffRating, rating.id)
    assert stored_old_snapshot is not None
    assert stored_old_snapshot.version == 1
    assert stored_old_staff is not None
    assert (
        stored_old_staff.staff_person_id,
        stored_old_staff.staff_name_snapshot,
        stored_old_staff.role_key_snapshot,
        stored_old_staff.role_name_snapshot,
        stored_old_staff.role_category,
        stored_old_staff.roles_snapshot,
        stored_old_staff.service_intervals,
        stored_old_staff.include_in_summary,
    ) == old_staff_signature
    assert stored_rating is not None
    assert (stored_rating.score, stored_rating.reason, stored_rating.suggestion) == (
        8,
        "训练安排清晰",
        None,
    )


def test_concurrent_first_open_creates_one_cycle_and_one_snapshot(
    postgres_engine: Engine,
) -> None:
    scenario = _seed_persistent_first_open_scenario(postgres_engine)
    barrier = Barrier(2)

    def worker() -> tuple[UUID, UUID, tuple[UUID, ...], bool]:
        with Session(postgres_engine) as db:
            barrier.wait(timeout=10)
            result = CampSurveySnapshotService(db).ensure_cycle(
                scenario.enrollment_id,
                actor_user_id=scenario.actor_id,
            )
            reusable = db.scalar(select(func.count(CampSurveyCycle.id))) is not None
            return (
                result.cycle.id,
                result.snapshot.id,
                tuple(row.id for row in result.staff),
                reusable,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))

        assert len({cycle_id for cycle_id, _, _, _ in results}) == 1
        assert len({snapshot_id for _, snapshot_id, _, _ in results}) == 1
        assert len({staff_ids for _, _, staff_ids, _ in results}) == 1
        assert all(reusable for _, _, _, reusable in results)
        with Session(postgres_engine) as db:
            cycle = db.scalar(
                select(CampSurveyCycle).where(
                    CampSurveyCycle.enrollment_id == scenario.enrollment_id
                )
            )
            assert cycle is not None
            assert cycle.current_snapshot_version == 1
            assert db.scalar(
                select(func.count(CampSurveyCycle.id)).where(
                    CampSurveyCycle.enrollment_id == scenario.enrollment_id
                )
            ) == 1
            assert db.scalar(
                select(func.count(CampSurveySnapshotVersion.id)).where(
                    CampSurveySnapshotVersion.cycle_id == cycle.id
                )
            ) == 1
    finally:
        _cleanup_persistent_first_open_scenario(postgres_engine, scenario)


def test_concurrent_equivalent_rebuilds_create_one_snapshot(
    postgres_engine: Engine,
) -> None:
    scenario = _seed_persistent_scenario(postgres_engine)
    barrier = Barrier(2)

    def worker(index: int) -> tuple[UUID, bool, bool]:
        with Session(postgres_engine) as db:
            barrier.wait(timeout=10)
            result = _service(db).create_snapshot_version(
                scenario.cycle_id,
                actor_user_id=scenario.actor_id,
                change_reason="并发补录实际服务助教",
                idempotency_key=f"snapshot-concurrent-correction-{index}",
                request_id=f"snapshot-concurrent-request-{index}",
                occurred_at=NOW + timedelta(minutes=1),
            )
            reusable = db.scalar(select(func.count(CampSurveyCycle.id))) is not None
            return result.snapshot.id, result.idempotent, reusable

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(worker, (1, 2)))

        assert len({snapshot_id for snapshot_id, _, _ in results}) == 1
        assert sorted(idempotent for _, idempotent, _ in results) == [False, True]
        assert all(reusable for _, _, reusable in results)
        with Session(postgres_engine) as db:
            cycle = db.get(CampSurveyCycle, scenario.cycle_id)
            assert cycle is not None
            assert cycle.current_snapshot_version == 2
            assert db.scalar(
                select(func.count(CampSurveySnapshotVersion.id)).where(
                    CampSurveySnapshotVersion.cycle_id == scenario.cycle_id
                )
            ) == 2
            assert db.scalar(
                select(func.count(EvaluationIdempotencyKey.id)).where(
                    EvaluationIdempotencyKey.actor_key == str(scenario.actor_id),
                    EvaluationIdempotencyKey.scope
                    == "camp_survey.snapshot.version",
                )
            ) == 2
            assert db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "camp_survey.snapshot.version.create",
                    AuditEvent.resource_id == results[0][0],
                )
            ) == 1
    finally:
        _cleanup_persistent_scenario(postgres_engine, scenario)


def test_assignment_correction_waits_for_the_frozen_snapshot_sources(
    postgres_engine: Engine,
) -> None:
    scenario = _seed_persistent_scenario(postgres_engine)
    source_locked = Event()
    update_attempted = Event()

    def before_statement(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        normalized = " ".join(statement.upper().split())
        if normalized.startswith("UPDATE COACH_ASSIGNMENTS"):
            update_attempted.set()

    def after_statement(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        normalized = " ".join(statement.upper().split())
        if "FROM COACH_ASSIGNMENTS" not in normalized or "FOR UPDATE" not in normalized:
            return
        source_locked.set()
        assert update_attempted.wait(10), "assignment correction did not contend"

    try:
        event.listen(postgres_engine, "before_cursor_execute", before_statement)
        event.listen(postgres_engine, "after_cursor_execute", after_statement)
        try:
            def rebuild() -> tuple[int, frozenset[str], bool]:
                with Session(postgres_engine) as db:
                    result = _service(db).create_snapshot_version(
                        scenario.cycle_id,
                        actor_user_id=scenario.actor_id,
                        change_reason="冻结服务来源后重建快照",
                        idempotency_key="snapshot-source-freeze",
                        request_id="snapshot-source-freeze-request",
                        occurred_at=NOW + timedelta(minutes=1),
                    )
                    reusable = (
                        db.scalar(select(func.count(CampSurveyCycle.id))) is not None
                    )
                    assistant_until = frozenset(
                        item["until"]
                        for row in result.staff
                        if row.staff_person_id == scenario.assistant_person_id
                        for item in row.service_intervals
                    )
                    return result.snapshot.version, assistant_until, reusable

            def correct_assignment() -> bool:
                assert source_locked.wait(10), "snapshot did not lock assignment sources"
                with Session(postgres_engine) as db, db.begin():
                    db.execute(
                        update(CoachAssignment)
                        .where(
                            CoachAssignment.id
                            == scenario.assistant_assignment_id
                        )
                        .values(valid_until=NOW - timedelta(hours=12))
                    )
                with Session(postgres_engine) as db:
                    return (
                        db.scalar(select(func.count(CoachAssignment.id))) is not None
                    )

            with ThreadPoolExecutor(max_workers=2) as executor:
                rebuild_future = executor.submit(rebuild)
                correction_future = executor.submit(correct_assignment)
                first_version, first_assistant_until, rebuild_reusable = (
                    rebuild_future.result(timeout=15)
                )
                correction_reusable = correction_future.result(timeout=15)
        finally:
            event.remove(
                postgres_engine,
                "before_cursor_execute",
                before_statement,
            )
            event.remove(
                postgres_engine,
                "after_cursor_execute",
                after_statement,
            )

        assert rebuild_reusable and correction_reusable
        assert first_version == 2
        assert {
            datetime.fromisoformat(value).astimezone(UTC)
            for value in first_assistant_until
        } == {NOW}

        with Session(postgres_engine) as db:
            corrected = _service(db).create_snapshot_version(
                scenario.cycle_id,
                actor_user_id=scenario.actor_id,
                change_reason="读取已提交的服务区间修正",
                idempotency_key="snapshot-source-after-correction",
                request_id="snapshot-source-after-correction-request",
                occurred_at=NOW + timedelta(minutes=2),
            )
            assert corrected.snapshot.version == 3
            corrected_assistant = next(
                row
                for row in corrected.staff
                if row.staff_person_id == scenario.assistant_person_id
            )
            assert {
                datetime.fromisoformat(item["until"]).astimezone(UTC)
                for item in corrected_assistant.service_intervals
            } == {NOW - timedelta(hours=12)}
    finally:
        _cleanup_persistent_scenario(postgres_engine, scenario)
