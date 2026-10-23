from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.esports_assessments.errors import AssessmentConflict, AssessmentExpired
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.models import (
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
)
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
NOW = datetime(2026, 9, 22, 2, tzinfo=UTC)


def practice_request(
    suite_id: str = "pc-fps",
    test_id: str = "precision",
    *,
    difficulty: int = 3,
    key=None,
) -> PracticeCreate:
    return PracticeCreate(
        suite_id=suite_id,
        test_id=test_id,
        difficulty=difficulty,
        settings=settings(suite_id),
        environment=environment(suite_id),
        operation_key=key or uuid4(),
    )


def test_practice_submit_persists_only_practice_metrics_and_replays(db) -> None:
    from app.esports_assessments.practice_service import PracticeAssessmentService

    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    create = practice_request(difficulty=4)
    run = service.start(actor, create, NOW)
    submit = result_request(
        "pc-fps",
        "precision",
        seed=run.seed,
        catalog_version=run.config.catalog_version,
    )

    saved = service.submit(actor, run.id, submit, NOW + timedelta(seconds=63))
    replay = service.submit(actor, run.id, submit, NOW + timedelta(seconds=64))

    assert saved == replay
    assert saved.mode == "practice"
    assert saved.scored.score is saved.scored.grade is saved.session is None
    stored = db.get(AssessmentPracticeRun, run.id)
    assert stored is not None
    assert stored.state == "submitted"
    assert stored.config_snapshot["difficulty"] == 4
    assert stored.raw_result is not None and stored.metrics == saved.scored.metrics
    assert len(stored.comparison_key or "") == 64
    assert db.scalar(select(func.count()).select_from(AssessmentResult)) == 0
    assert db.scalar(select(func.count()).select_from(AssessmentReport)) == 0


def test_practice_creation_replay_precedes_withdrawal_and_cross_mode_keys_are_isolated(
    db, monkeypatch
) -> None:
    from app.esports_assessments import catalog
    from app.esports_assessments.practice_service import PracticeAssessmentService

    actor = student_actor(db)
    key = uuid4()
    formal = FormalAssessmentService(db).create(actor, session_request(key=key), NOW)
    request = practice_request(key=key)
    service = PracticeAssessmentService(db)
    first = service.start(actor, request, NOW)
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset())

    replay = service.start(actor, request, NOW + timedelta(seconds=1))

    assert formal.id != replay.id == first.id
    assert replay.server_now == NOW + timedelta(seconds=1)
    assert replay.run_until == first.run_until


def test_practice_and_formal_share_one_running_account_occupancy(db) -> None:
    from app.esports_assessments.practice_service import PracticeAssessmentService

    actor = student_actor(db)
    formal_service = FormalAssessmentService(db)
    session = formal_service.create(actor, session_request(), NOW)
    formal_service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "precision"),
        NOW,
    )

    with pytest.raises(AssessmentConflict) as error:
        PracticeAssessmentService(db).start(
            actor,
            practice_request("pc-moba", "last-hit"),
            NOW + timedelta(seconds=1),
        )

    assert error.value.code == "esports_active_run"
    assert db.scalar(select(func.count()).select_from(AssessmentPracticeRun)) == 0


def test_practice_invalid_cross_suite_payload_does_not_claim_submission_key(db) -> None:
    from app.esports_assessments.practice_service import PracticeAssessmentService

    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    run = service.start(actor, practice_request(), NOW)
    key = uuid4()
    invalid = result_request("pc-moba", "last-hit", seed=run.seed, key=key)

    with pytest.raises(ValueError):
        service.submit(actor, run.id, invalid, NOW + timedelta(seconds=91))

    valid = result_request(
        "pc-fps",
        "precision",
        seed=run.seed,
        key=key,
        catalog_version=run.config.catalog_version,
    )
    saved = service.submit(actor, run.id, valid, NOW + timedelta(seconds=92))
    assert saved.result_id == run.id


def test_practice_expired_submit_is_durable_and_interrupt_projects_expired(db) -> None:
    from app.esports_assessments.practice_service import PracticeAssessmentService

    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    run = service.start(actor, practice_request(), NOW)
    after_deadline = run.submit_until
    submit = result_request(
        "pc-fps",
        "precision",
        seed=run.seed,
        catalog_version=run.config.catalog_version,
    )

    with pytest.raises(AssessmentExpired):
        service.submit(actor, run.id, submit, after_deadline)

    stored = db.get(AssessmentPracticeRun, run.id)
    assert stored is not None and stored.state == "expired"
    projected = service.interrupt(actor, run.id, uuid4(), after_deadline + timedelta(seconds=1))
    assert projected.state == "expired"


def test_practice_expired_interrupt_returns_expired_projection_for_http_translation(db) -> None:
    from app.esports_assessments.practice_service import PracticeAssessmentService

    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    run = service.start(actor, practice_request(), NOW)

    interrupted = service.interrupt(actor, run.id, uuid4(), run.submit_until)

    assert interrupted.state == "expired"
    assert db.get(AssessmentPracticeRun, run.id).state == "expired"
