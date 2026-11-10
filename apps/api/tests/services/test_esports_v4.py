"""Persisted triad completion and anonymous leaderboards (controller runs serially)."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.esports_assessments import catalog
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.models import AssessmentAttempt, AssessmentResult, AssessmentSession
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.queries import AssessmentQueries
from app.esports_assessments.schemas import ResultSubmit
from tests.api.test_esports_access import NOW
from tests.esports_helpers import (
    attempt_request,
    quality,
    result_request,
    session_request,
    student_actor,
)
from tests.services import test_esports_rankings as ranking_fixtures
from tests.services.test_esports_practice import practice_request
from tests.services.test_esports_rankings import ranking, round_row

esports_people = ranking_fixtures.esports_people

pytestmark = pytest.mark.usefixtures("esports_enabled")


def submission(config, *, perfect=True):
    raw = (
        {
            "kind": "spheretrack90",
            "coverage_ms": 60000 if perfect else 0,
            "loss_count": 0,
            "reacquisition_ms": [],
        }
        if config.test_id == "spheretrack90"
        else {
            "kind": "multi_target",
            "shots": [{"at_ms": i * 100, "hit": perfect} for i in range(300)],
        }
    )
    return ResultSubmit(operation_key=uuid4(), raw=raw, quality=quality("pc-fps"))


def complete_round(db, actor, monkeypatch, *, at=NOW):
    service = FormalAssessmentService(db)
    frozen = catalog.get_suite("pc-fps", catalog_version="pc-fps-v4")
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda *_args, **_kwargs: frozen)
        session = service.create(actor, session_request(), at)
    saves = []
    for index, test_id in enumerate(session.members):
        start = at + timedelta(seconds=65 * index)
        run = service.start_attempt(actor, session.id, attempt_request("pc-fps", test_id), start)
        request = submission(run.config)
        saved = service.submit(actor, run.id, request, start + timedelta(seconds=63))
        assert service.submit(actor, run.id, request, start + timedelta(seconds=64)) == saved
        saves.append(saved)
    return saves


def test_v4_third_save_creates_report_once_with_a_frozen_triad(db, monkeypatch):
    actor = student_actor(db)
    saves = complete_round(db, actor, monkeypatch)
    assert [s.session.report_id is not None for s in saves] == [False, False, True]
    assert saves[-1].session.status == "completed"
    assert len(saves[-1].session.results) == 3
    report = AssessmentQueries(db, actor, NOW).report(saves[-1].session.report_id)
    assert report.suite.catalog_version == "pc-fps-v4"
    assert tuple(r.test_id for r in report.results) == ("gridshot", "sixshot", "spheretrack90")


@pytest.mark.parametrize("revision", [1, 2, 3])
def test_old_fourth_save_and_pending_practice_stay_usable_after_v4(db, monkeypatch, revision):
    actor = student_actor(db)
    frozen = catalog.get_suite("pc-fps", catalog_version=f"pc-fps-v{revision}")
    service = FormalAssessmentService(db)
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda *_args, **_kwargs: frozen)
        old = service.create(actor, session_request(), NOW)
    for index, test_id in enumerate(old.members):
        at = NOW + timedelta(seconds=65 * index)
        run = service.start_attempt(actor, old.id, attempt_request("pc-fps", test_id), at)
        request = result_request(
            "pc-fps", test_id, seed=run.seed, catalog_version=run.config.catalog_version
        )
        saved = service.submit(actor, run.id, request, at + timedelta(seconds=63))
        assert (saved.session.report_id is not None) == (index == 3)
    assert saved.session.suite_config == frozen
    at = NOW + timedelta(minutes=5)
    practice = PracticeAssessmentService(db)
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_task", lambda *_args, **_kwargs: frozen.tasks["precision"])
        pending = practice.start(actor, practice_request("pc-fps", "precision"), at)
    queries = AssessmentQueries(db, actor, at)
    assert queries.recovery().items[0].test_id == "precision"
    request = result_request(
        "pc-fps", "precision", seed=pending.seed, catalog_version=frozen.catalog_version
    )
    saved_practice = practice.submit(actor, pending.id, request, at + timedelta(seconds=63))
    assert practice.submit(actor, pending.id, request, at + timedelta(seconds=64)) == saved_practice


def test_v4_top_scores_are_real_anonymous_bounded_competition_ranks(db, esports_people):
    _, actors, _ = esports_people
    _, subject_report = round_row(db, actors["student"], (60,) * 3, version=4)
    round_row(db, actors["other"], (90, 70, 80), version=4)
    round_row(db, actors["guardian"], (80,) * 3, version=4)
    round_row(db, actors["super_admin"], (50,) * 3, version=4)
    # A person's losing round must not furnish an independently higher item score.
    round_row(db, actors["other"], (100, 0, 0), version=4, at=NOW + timedelta(minutes=10))
    db.commit()
    report = subject_report
    statements = []

    def capture(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(sql)

    event.listen(db.connection(), "before_cursor_execute", capture)
    value = ranking(db, actors["student"], report)
    event.remove(db.connection(), "before_cursor_execute", capture)
    assert value.participant_count == 4
    assert value.overall_rank == 3
    assert [(t.rank, t.score, t.gap) for t in value.top_scores] == [
        (1, 80.0, 20.0),
        (1, 80.0, 20.0),
        (3, 60.0, 0.0),
    ]
    assert [(t.rank, t.score, t.gap) for t in value.items[0].top_scores] == [
        (1, 90.0, 30.0),
        (2, 80.0, 20.0),
        (3, 60.0, 0.0),
    ]
    for top in (*value.top_scores, *(t for i in value.items for t in i.top_scores)):
        assert set(top.model_dump()) == {"rank", "score", "gap"}
    payload = str(value.model_dump(mode="json"))
    for actor in (actors["other"], actors["guardian"], actors["super_admin"]):
        assert str(actor.person.id) not in payload
        assert actor.person.full_name not in payload
    # Each peer projection is server limited, with no identity columns fetched.
    top_queries = [s for s in statements if "anonymous_score" in s]
    assert len(top_queries) == 4
    assert all("LIMIT" in s for s in top_queries)


def test_v4_incomplete_warned_mixed_and_old_rounds_never_join_top_scores(db, esports_people):
    _, actors, _ = esports_people
    subject, subject_report = round_row(db, actors["student"], (60,) * 3, version=4)
    for actor_key, defect in (
        ("other", "warning"),
        ("guardian", "missing"),
        ("super_admin", "config"),
    ):
        session, _ = round_row(db, actors[actor_key], (100,) * 3, version=4)
        result = db.scalar(
            select(AssessmentResult).where(AssessmentResult.session_id == session.id)
        )
        if defect == "warning":
            result.quality_flags = ["long_frames"]
        elif defect == "missing":
            db.delete(result)
        else:
            attempt = db.get(AssessmentAttempt, result.attempt_id)
            attempt.config_snapshot = catalog.get_task(
                "pc-fps", "precision", catalog_version="pc-fps-v3"
            ).model_dump(mode="json")
        db.commit()
    round_row(db, actors["coach"], (100,) * 4, version=3)
    db.commit()
    value = ranking(db, actors["student"], subject_report)
    assert value.participant_count == 1
    assert [(t.rank, t.score, t.gap) for t in value.top_scores] == [(1, 60.0, 0.0)]
    assert all(len(i.top_scores) == 1 for i in value.items)
    broken = db.get(AssessmentSession, subject.id)
    result = db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == broken.id))
    attempt = db.get(AssessmentAttempt, result.attempt_id)
    attempt.config_snapshot = {**attempt.config_snapshot, "engine_version": "pc-fps-v3"}
    db.commit()
    assert ranking(db, actors["student"], subject_report).status == ("not_eligible")
