from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffSnapshot,
)
from app.evaluations.survey_snapshot_service import CampSurveySnapshotService
from app.identity.models import Person, StaffProfile
from app.teaching.models import CoachAssignment
from tests.db.test_camp_survey_snapshot_service import (
    _add_coach,
    _cleanup_persistent_scenario,
    _seed_persistent_scenario,
)
from tests.services.test_teaching_scheduler import NOW


def _service(db: Session) -> CampSurveySnapshotService:
    return CampSurveySnapshotService(
        db,
        idempotency=EvaluationIdempotencyService(db, digest_key=b"s" * 32),
    )


def test_snapshot_version_refreshes_locked_cycle_after_another_session_commits_v2(
    postgres_engine: Engine,
) -> None:
    scenario = _seed_persistent_scenario(postgres_engine)
    stale_session = Session(postgres_engine, expire_on_commit=False)
    try:
        preloaded = stale_session.get(CampSurveyCycle, scenario.cycle_id)
        assert preloaded is not None
        assert preloaded.current_snapshot_version == 1
        stale_session.commit()

        with Session(postgres_engine) as writer:
            written = _service(writer).create_snapshot_version(
                scenario.cycle_id,
                actor_user_id=scenario.actor_id,
                change_reason="任务 C 并发写入 v2",
                idempotency_key="task-c-cycle-v2",
                request_id="task-c-cycle-second-request",
                occurred_at=NOW + timedelta(minutes=1),
            )
            assert written.snapshot.version == 2

        refreshed = _service(stale_session).create_snapshot_version(
            scenario.cycle_id,
            actor_user_id=scenario.actor_id,
            change_reason="任务 C 在 v2 后重建等价快照",
            idempotency_key="task-c-cycle-after-v2",
            request_id="task-c-cycle-after-second-request",
            occurred_at=NOW + timedelta(minutes=2),
        )

        assert refreshed.snapshot.version == 2
        assert refreshed.idempotent is True
        assert stale_session.scalar(select(func.count(CampSurveyCycle.id))) == 1
        assert stale_session.scalar(
            select(func.count(CampSurveySnapshotVersion.id)).where(
                CampSurveySnapshotVersion.cycle_id == scenario.cycle_id
            )
        ) == 2
    finally:
        stale_session.close()
        _cleanup_persistent_scenario(postgres_engine, scenario)


def test_later_snapshot_inherits_existing_exclusion_for_staff_still_assigned(
    postgres_engine: Engine,
) -> None:
    scenario = _seed_persistent_scenario(postgres_engine)
    added_person_id: UUID | None = None
    try:
        with Session(postgres_engine) as db:
            cycle = db.get(CampSurveyCycle, scenario.cycle_id)
            assert cycle is not None
            initial_rows = list(
                db.scalars(
                    select(CampSurveyStaffSnapshot).where(
                        CampSurveyStaffSnapshot.snapshot_version_id
                        == select(CampSurveySnapshotVersion.id)
                        .where(
                            CampSurveySnapshotVersion.cycle_id == cycle.id,
                            CampSurveySnapshotVersion.version == 1,
                        )
                        .scalar_subquery()
                    )
                )
            )
            wrong_staff_id = next(
                row.staff_person_id
                for row in initial_rows
                if row.staff_person_id != scenario.assistant_person_id
            )

            version_two = _service(db).create_snapshot_version(
                cycle.id,
                actor_user_id=scenario.actor_id,
                change_reason="任务 C 排除仍在 assignment 的误录人员",
                excluded_staff_person_ids={wrong_staff_id},
                idempotency_key="task-c-exclude-v2",
                request_id="task-c-exclude-second-request",
                occurred_at=NOW + timedelta(minutes=1),
            )
            excluded_v2 = next(
                row for row in version_two.staff if row.staff_person_id == wrong_staff_id
            )
            assert excluded_v2.include_in_summary is False

            added_person = _add_coach(db, label="任务 C v3 新增人员")
            added_person_id = added_person.id
            db.add(
                CoachAssignment(
                    class_id=scenario.class_id,
                    coach_person_id=added_person.id,
                    assignment_type="assistant",
                    valid_from=NOW - timedelta(hours=1),
                    valid_until=NOW,
                    created_by_user_id=scenario.actor_id,
                )
            )
            db.commit()

            version_three = _service(db).create_snapshot_version(
                cycle.id,
                actor_user_id=scenario.actor_id,
                change_reason="任务 C v3 新增无关服务人员",
                idempotency_key="task-c-exclude-v3",
                request_id="task-c-exclude-third-request",
                occurred_at=NOW + timedelta(minutes=2),
            )

            assert version_three.snapshot.version == 3
            v3_by_staff = {row.staff_person_id: row for row in version_three.staff}
            assert v3_by_staff[wrong_staff_id].include_in_summary is False
            assert v3_by_staff[scenario.assistant_person_id].include_in_summary is True
            assert v3_by_staff[added_person.id].include_in_summary is True

            db.expire_all()
            old_v2_row = db.scalar(
                select(CampSurveyStaffSnapshot).where(
                    CampSurveyStaffSnapshot.snapshot_version_id == version_two.snapshot.id,
                    CampSurveyStaffSnapshot.staff_person_id == wrong_staff_id,
                )
            )
            assert old_v2_row is not None
            assert old_v2_row.include_in_summary is False
    finally:
        _cleanup_persistent_scenario(postgres_engine, scenario)
        if added_person_id is not None:
            with Session(postgres_engine) as cleanup:
                cleanup.execute(
                    delete(StaffProfile).where(StaffProfile.person_id == added_person_id)
                )
                cleanup.execute(
                    delete(UserAccount).where(UserAccount.person_id == added_person_id)
                )
                cleanup.execute(delete(Person).where(Person.id == added_person_id))
                cleanup.commit()
