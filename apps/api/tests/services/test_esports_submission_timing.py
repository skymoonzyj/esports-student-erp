from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.esports_assessments.errors import (
    AssessmentConflict,
    AssessmentExpired,
    AssessmentForbidden,
)
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
)
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.schemas import PracticeCreate
from tests.esports_helpers import (
    attempt_request,
    environment,
    result_request,
    session_request,
    settings,
    student_actor,
)

pytestmark = pytest.mark.usefixtures("esports_enabled")
NOW = datetime(2026, 9, 23, 2, tzinfo=UTC)


def _practice_create() -> PracticeCreate:
    return PracticeCreate(
        suite_id="pc-fps",
        test_id="gridshot",
        difficulty=3,
        settings=settings("pc-fps"),
        environment=environment("pc-fps"),
        operation_key=uuid4(),
    )


def test_formal_early_first_save_keeps_original_operation(db: Session) -> None:
    actor = student_actor(db)
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    run = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "gridshot"),
        NOW,
    )
    request = result_request(
        "pc-fps",
        "gridshot",
        seed=run.seed,
        catalog_version=run.config.catalog_version,
    )
    audit_count = db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.actor_user_id == actor.account.id)
    )

    with pytest.raises(AssessmentConflict) as error:
        service.submit(
            actor,
            run.id,
            request,
            NOW + timedelta(milliseconds=62_999),
        )

    assert error.value.code == "esports_result_too_early"
    db.expire_all()
    stored_attempt = db.get(AssessmentAttempt, run.id)
    assert stored_attempt is not None and stored_attempt.state == "running"
    assert db.scalar(select(func.count()).select_from(AssessmentResult)) == 0
    assert db.scalar(select(func.count()).select_from(AssessmentReport)) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.actor_user_id == actor.account.id)
        )
        == audit_count
    )

    saved = service.submit(actor, run.id, request, NOW + timedelta(seconds=63))

    assert service.submit(actor, run.id, request, NOW + timedelta(minutes=31)) == saved


def test_practice_early_first_save_keeps_original_operation(db: Session) -> None:
    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    run = service.start(actor, _practice_create(), NOW)
    request = result_request(
        "pc-fps",
        "gridshot",
        seed=run.seed,
        catalog_version=run.config.catalog_version,
    )

    with pytest.raises(AssessmentConflict) as error:
        service.submit(
            actor,
            run.id,
            request,
            NOW + timedelta(milliseconds=62_999),
        )

    assert error.value.code == "esports_result_too_early"
    db.expire_all()
    stored_run = db.get(AssessmentPracticeRun, run.id)
    assert stored_run is not None and stored_run.state == "running"
    assert db.scalar(select(func.count()).select_from(AssessmentResult)) == 0
    assert db.scalar(select(func.count()).select_from(AssessmentReport)) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.actor_user_id == actor.account.id)
        )
        == 0
    )

    saved = service.submit(actor, run.id, request, NOW + timedelta(seconds=63))

    assert service.submit(actor, run.id, request, NOW + timedelta(minutes=31)) == saved


def test_formal_and_practice_allow_first_save_at_90_seconds(db: Session) -> None:
    formal_actor = student_actor(db)
    formal_service = FormalAssessmentService(db)
    session = formal_service.create(formal_actor, session_request(), NOW)
    formal_run = formal_service.start_attempt(
        formal_actor,
        session.id,
        attempt_request("pc-fps", "gridshot"),
        NOW,
    )

    formal_saved = formal_service.submit(
        formal_actor,
        formal_run.id,
        result_request(
            "pc-fps",
            "gridshot",
            seed=formal_run.seed,
            catalog_version=formal_run.config.catalog_version,
        ),
        NOW + timedelta(seconds=90),
    )

    practice_actor = student_actor(db)
    practice_service = PracticeAssessmentService(db)
    practice_run = practice_service.start(practice_actor, _practice_create(), NOW)
    practice_saved = practice_service.submit(
        practice_actor,
        practice_run.id,
        result_request(
            "pc-fps",
            "gridshot",
            seed=practice_run.seed,
            catalog_version=practice_run.config.catalog_version,
        ),
        NOW + timedelta(seconds=90),
    )

    assert formal_saved.result_id
    assert practice_saved.result_id == practice_run.id


def test_formal_and_practice_reject_first_save_at_30_minute_boundary(db: Session) -> None:
    formal_actor = student_actor(db)
    formal_service = FormalAssessmentService(db)
    session = formal_service.create(formal_actor, session_request(), NOW)
    formal_run = formal_service.start_attempt(
        formal_actor,
        session.id,
        attempt_request("pc-fps", "gridshot"),
        NOW,
    )

    with pytest.raises(AssessmentExpired):
        formal_service.submit(
            formal_actor,
            formal_run.id,
            result_request(
                "pc-fps",
                "gridshot",
                seed=formal_run.seed,
                catalog_version=formal_run.config.catalog_version,
            ),
            formal_run.submit_until,
        )

    practice_actor = student_actor(db)
    practice_service = PracticeAssessmentService(db)
    practice_run = practice_service.start(practice_actor, _practice_create(), NOW)

    with pytest.raises(AssessmentExpired):
        practice_service.submit(
            practice_actor,
            practice_run.id,
            result_request(
                "pc-fps",
                "gridshot",
                seed=practice_run.seed,
                catalog_version=practice_run.config.catalog_version,
            ),
            practice_run.submit_until,
        )

    db.expire_all()
    stored_attempt = db.get(AssessmentAttempt, formal_run.id)
    stored_run = db.get(AssessmentPracticeRun, practice_run.id)
    assert stored_attempt is not None and stored_attempt.state == "expired"
    assert stored_run is not None and stored_run.state == "expired"


def test_formal_success_replay_order_stays_payload_bound_terminal_and_authorized(
    db: Session,
) -> None:
    actor = student_actor(db)
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    run = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "gridshot"),
        NOW,
    )
    request = result_request(
        "pc-fps",
        "gridshot",
        seed=run.seed,
        catalog_version=run.config.catalog_version,
    )
    saved = service.submit(actor, run.id, request, NOW + timedelta(seconds=63))
    changed_payload = result_request(
        "pc-fps",
        "gridshot",
        seed=run.seed,
        key=request.operation_key,
        quality_updates={"long_frame_gap_total_ms": 1},
        catalog_version=run.config.catalog_version,
    )

    with pytest.raises(AssessmentConflict) as mismatch:
        service.submit(actor, run.id, changed_payload, NOW + timedelta(minutes=31))
    assert mismatch.value.code == "esports_replay_mismatch"

    with pytest.raises(AssessmentConflict) as changed_key:
        service.submit(
            actor,
            run.id,
            request.model_copy(update={"operation_key": uuid4()}),
            NOW + timedelta(seconds=64),
        )
    assert changed_key.value.code == "esports_attempt_terminal"

    actor.account.status = "disabled"
    db.commit()
    with pytest.raises(AssessmentForbidden):
        service.submit(actor, run.id, request, NOW + timedelta(minutes=31))

    assert saved.result_id


def test_practice_success_replay_order_stays_payload_bound_terminal_and_authorized(
    db: Session,
) -> None:
    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    run = service.start(actor, _practice_create(), NOW)
    request = result_request(
        "pc-fps",
        "gridshot",
        seed=run.seed,
        catalog_version=run.config.catalog_version,
    )
    saved = service.submit(actor, run.id, request, NOW + timedelta(seconds=63))
    changed_payload = result_request(
        "pc-fps",
        "gridshot",
        seed=run.seed,
        key=request.operation_key,
        quality_updates={"long_frame_gap_total_ms": 1},
        catalog_version=run.config.catalog_version,
    )

    with pytest.raises(AssessmentConflict) as mismatch:
        service.submit(actor, run.id, changed_payload, NOW + timedelta(minutes=31))
    assert mismatch.value.code == "esports_replay_mismatch"

    with pytest.raises(AssessmentConflict) as changed_key:
        service.submit(
            actor,
            run.id,
            request.model_copy(update={"operation_key": uuid4()}),
            NOW + timedelta(seconds=64),
        )
    assert changed_key.value.code == "esports_attempt_terminal"

    actor.account.status = "disabled"
    db.commit()
    with pytest.raises(AssessmentForbidden):
        service.submit(actor, run.id, request, NOW + timedelta(minutes=31))

    assert saved.result_id == run.id
