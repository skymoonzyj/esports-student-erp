"""Persisted v4 work survives the v5 default; score pools remain disjoint."""

from datetime import timedelta

import pytest

from app.esports_assessments import catalog
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.queries import AssessmentQueries
from tests.api.test_esports_access import NOW
from tests.esports_helpers import attempt_request, session_request, student_actor
from tests.services import test_esports_rankings as ranking_fixtures
from tests.services.test_esports_practice import practice_request
from tests.services.test_esports_rankings import ranking, round_row
from tests.services.test_esports_v4 import submission

esports_people = ranking_fixtures.esports_people
pytestmark = pytest.mark.usefixtures("esports_enabled")


def create_v4(db, actor, monkeypatch):
    frozen = catalog.get_suite("pc-fps", catalog_version="pc-fps-v4")
    request = session_request()
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda *_args, **_kwargs: frozen)
        session = FormalAssessmentService(db).create(actor, request, NOW)
    return session, request, frozen


def test_v4_pending_creation_replay_after_v5_default(db, monkeypatch):
    actor = student_actor(db)
    old, request, frozen = create_v4(db, actor, monkeypatch)
    assert catalog.get_suite("pc-fps").catalog_version == "pc-fps-v5"
    replay = FormalAssessmentService(db).create(actor, request, NOW + timedelta(seconds=1))
    assert replay == old
    assert replay.suite_config == frozen


def test_v4_pending_submission_after_v5_default(db, monkeypatch):
    actor = student_actor(db)
    service = FormalAssessmentService(db)
    frozen = catalog.get_suite("pc-fps", catalog_version="pc-fps-v4")
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda *_args, **_kwargs: frozen)
        old = service.create(actor, session_request(), NOW)
        pending = service.start_attempt(actor, old.id, attempt_request("pc-fps", "gridshot"), NOW)
    assert catalog.get_suite("pc-fps").catalog_version == "pc-fps-v5"
    for index, test in enumerate(old.members):
        at = NOW + timedelta(seconds=index * 65)
        run = (
            pending
            if index == 0
            else service.start_attempt(actor, old.id, attempt_request("pc-fps", test), at)
        )
        assert run.config == frozen.tasks[test]
        request = submission(run.config)
        saved = service.submit(actor, run.id, request, at + timedelta(seconds=63))
        assert service.submit(actor, run.id, request, at + timedelta(seconds=64)) == saved
    report = AssessmentQueries(db, actor, NOW).report(saved.session.report_id)
    assert report.suite == frozen
    assert [r.test_id for r in report.results] == list(old.members)
    assert all(r.scored.score == 100 for r in report.results)


def test_v4_practice_ticket_after_v5_default(db, monkeypatch):
    actor = student_actor(db)
    frozen = catalog.get_task("pc-fps", "gridshot", 1, catalog_version="pc-fps-v4")
    practice = PracticeAssessmentService(db)
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_task", lambda *_args, **_kwargs: frozen)
        pending = practice.start(actor, practice_request("pc-fps", "gridshot", difficulty=1), NOW)
    assert catalog.get_suite("pc-fps").catalog_version == "pc-fps-v5"
    assert pending.config == frozen
    request = submission(pending.config)
    saved = practice.submit(actor, pending.id, request, NOW + timedelta(seconds=63))
    assert practice.submit(actor, pending.id, request, NOW + timedelta(seconds=64)) == saved
    assert (
        saved.scored.score is None
    )  # Practice remains metrics-only, including historical tickets.
    assert saved.scored.metrics["hits"] == 300


def test_v4_v5_rankings_are_separate(db, esports_people):
    _, actors, _ = esports_people
    _, old = round_row(db, actors["student"], (40,) * 3, version=4)
    _, current = round_row(db, actors["student"], (60,) * 3, version=5)
    round_row(db, actors["other"], (90,) * 3, version=4)
    round_row(db, actors["guardian"], (80,) * 3, version=5)
    round_row(db, actors["super_admin"], (50,) * 3, version=5)
    db.commit()
    for report, score, count, tops in (
        (old, 40, 2, [(1, 90, 50), (2, 40, 0)]),
        (current, 60, 3, [(1, 80, 20), (2, 60, 0), (3, 50, -10)]),
    ):
        value = ranking(db, actors["student"], report)
        assert value.best_report_id == report.id
        assert (value.overall_score, value.overall_rank, value.participant_count) == (
            score,
            2,
            count,
        )
        assert [(t.rank, t.score, t.gap) for t in value.top_scores] == tops
        assert len(value.items) == 3
        for item in value.items:
            assert (item.score, item.rank) == (score, 2)
            assert [(t.rank, t.score, t.gap) for t in item.top_scores] == tops
