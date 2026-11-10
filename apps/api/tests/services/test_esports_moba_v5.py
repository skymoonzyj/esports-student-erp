from datetime import timedelta
from uuid import uuid4

import pytest

from app.esports_assessments import catalog
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.queries import AssessmentQueries
from app.esports_assessments.ranking import AssessmentRankings
from app.esports_assessments.schemas import ResultSubmit, SessionCreate
from tests.api.test_esports_access import NOW
from tests.esports_helpers import attempt_request, quality, result_request, student_actor
from tests.services.test_esports_practice import practice_request
from tests.unit.test_esports_moba_v5 import raw_v5


def finish(db, actor, mode, hits):
    service = FormalAssessmentService(db)
    request = SessionCreate(operation_key=uuid4(), suite_id="pc-moba", attack_mode=mode)
    session = service.create(actor, request, NOW)
    assert session.suite_config.catalog_version == "pc-moba-v5"
    assert service.create(actor, request, NOW) == session
    run = service.start_attempt(actor, session.id, attempt_request("pc-moba", "last-hit"), NOW)
    saved = service.submit(
        actor,
        run.id,
        ResultSubmit(
            operation_key=uuid4(),
            quality=quality("pc-moba", duration_ms=300000),
            raw=raw_v5(
                deaths=[
                    {"unit_id": i, "at_ms": i // 5 * 20000 + 1000 + i % 5, "by_player": i < hits}
                    for i in range(75)
                ],
                attacks=75,
                hero_death_times=[10000],
                hero_damage_taken=1180,
            ),
        ),
        NOW + timedelta(seconds=303),
    )
    return saved.session.report_id


@pytest.mark.parametrize("mode", ["melee", "ranged"])
def test_v5_saves_life_statistics_and_immutable_report(db, mode):
    actor = student_actor(db)
    report_id = finish(db, actor, mode, 50)
    report = AssessmentQueries(db, actor, NOW).report(report_id)
    assert report.suite.catalog_version == "pc-moba-v5"
    assert report.results[0].scored.score == 67
    assert report.results[0].scored.metrics["hero_deaths"] == 1
    assert report.results[0].scored.metrics["hero_damage_taken"] == 1180
    assert "塔" in report.recommendations[0]


def test_v5_modes_and_frozen_v4_ranking_remain_separate(db, monkeypatch):
    from tests.services.test_esports_moba_v4 import complete_lane

    subject, peer = student_actor(db), student_actor(db)
    melee = finish(db, subject, "melee", 50)
    finish(db, peer, "melee", 75)
    ranged = finish(db, subject, "ranged", 2)
    original = catalog.get_suite
    with monkeypatch.context() as patch:
        patch.setattr(
            catalog,
            "get_suite",
            lambda suite_id, **kwargs: original(
                suite_id,
                **{**kwargs, "catalog_version": kwargs.get("catalog_version") or "pc-moba-v4"},
            ),
        )
        old = complete_lane(db, subject, "melee", 50)
    ranked = AssessmentRankings(db, subject, NOW)
    assert (ranked.report(melee).participant_count, ranked.report(melee).overall_score) == (2, 67)
    assert (ranked.report(ranged).participant_count, ranked.report(ranged).overall_score) == (1, 3)
    assert (ranked.report(old).participant_count, ranked.report(old).overall_score) == (1, 100)
    assert [entry.score for entry in ranked.report(melee).top_scores] == [100, 67]


def test_v5_practice_requires_selection_and_preserves_mode(db):
    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    request = practice_request("pc-moba", "last-hit")
    with pytest.raises(ValueError, match="请选择"):
        service.start(actor, request, NOW)
    run = service.start(actor, request.model_copy(update={"attack_mode": "ranged"}), NOW)
    assert run.config.catalog_version == "pc-moba-v5"
    assert run.config.params.attack_mode == "ranged"
    saved = service.submit(
        actor,
        run.id,
        ResultSubmit(
            operation_key=uuid4(), raw=raw_v5(), quality=quality("pc-moba", duration_ms=300000)
        ),
        NOW + timedelta(seconds=303),
    )
    assert saved.scored.metrics["unresolved"] == 75


def test_v4_operation_key_replay_precedes_new_v5_mode_validation(db, monkeypatch):
    actor = student_actor(db)
    practice_actor = student_actor(db)
    formal = FormalAssessmentService(db)
    practice = PracticeAssessmentService(db)
    request = SessionCreate(operation_key=uuid4(), suite_id="pc-moba", attack_mode="ranged")
    practice_create = practice_request("pc-moba", "last-hit").model_copy(
        update={"attack_mode": "ranged"}
    )
    frozen = catalog.get_suite("pc-moba", catalog_version="pc-moba-v4", attack_mode="ranged")
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda *_args, **_kwargs: frozen)
        patch.setattr(catalog, "get_task", lambda *_args, **_kwargs: frozen.tasks["last-hit"])
        old = formal.create(actor, request, NOW)
        pending = practice.start(practice_actor, practice_create, NOW)
    assert formal.create(actor, request, NOW) == old
    assert practice.start(practice_actor, practice_create, NOW) == pending
    run = formal.start_attempt(actor, old.id, attempt_request("pc-moba", "last-hit"), NOW)
    assert run.config.catalog_version == "pc-moba-v4"
    payload = result_request("pc-moba", "last-hit", seed=run.seed, catalog_version="pc-moba-v4")
    saved = formal.submit(actor, run.id, payload, NOW + timedelta(seconds=303))
    assert saved.scored.score == 100
    assert "hero_deaths" not in saved.scored.metrics
    assert formal.submit(actor, run.id, payload, NOW + timedelta(seconds=304)) == saved
    practice_saved = practice.submit(
        practice_actor, pending.id, payload, NOW + timedelta(seconds=303)
    )
    assert practice_saved.scored.metrics["unresolved"] == 0
