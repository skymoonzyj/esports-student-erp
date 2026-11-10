from datetime import timedelta
from uuid import uuid4

import pytest

from app.esports_assessments import catalog
from app.esports_assessments.errors import AssessmentConflict
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.queries import AssessmentQueries
from app.esports_assessments.schemas import ResultSubmit, SessionCreate
from tests.api.test_esports_access import NOW
from tests.esports_helpers import attempt_request, quality, student_actor
from tests.services.test_esports_practice import practice_request


@pytest.fixture(autouse=True)
def frozen_v4_catalog(monkeypatch):
    from tests.esports_helpers import pin_moba_v4

    pin_moba_v4(monkeypatch)


def submission():
    return ResultSubmit(
        operation_key=uuid4(),
        raw={"kind": "lane_last_hit", "deaths": [], "attacks": 0, "cancelled_attacks": 0},
        quality=quality("pc-moba").model_copy(update={"duration_ms": 300000}),
    )


@pytest.mark.parametrize("mode", ["melee", "ranged"])
def test_five_minute_formal_save_creates_single_report_and_replays(db, mode):
    actor = student_actor(db)
    service = FormalAssessmentService(db)
    request = SessionCreate(operation_key=uuid4(), suite_id="pc-moba", attack_mode=mode)
    session = service.create(actor, request, NOW)
    assert session.members == ("last-hit",)
    assert service.create(actor, request, NOW) == session
    with pytest.raises(AssessmentConflict):
        service.create(
            actor,
            request.model_copy(update={"attack_mode": "ranged" if mode == "melee" else "melee"}),
            NOW,
        )
    run = service.start_attempt(actor, session.id, attempt_request("pc-moba", "last-hit"), NOW)
    assert run.run_until == NOW + timedelta(seconds=330)
    payload = submission()
    with pytest.raises(AssessmentConflict, match="测试尚未完成"):
        service.submit(actor, run.id, payload, NOW + timedelta(seconds=302))
    saved = service.submit(actor, run.id, payload, NOW + timedelta(seconds=303))
    assert saved.session.status == "completed"
    assert saved.session.report_id is not None
    assert service.submit(actor, run.id, payload, NOW + timedelta(seconds=304)) == saved
    report = AssessmentQueries(db, actor, NOW).report(saved.session.report_id)
    assert len(report.results) == 1
    assert report.suite.tasks["last-hit"].params.attack_mode == mode


@pytest.mark.parametrize("mode", ["melee", "ranged"])
def test_five_minute_practice_preserves_frozen_mode_on_submission(db, mode):
    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    request = practice_request("pc-moba", "last-hit").model_copy(update={"attack_mode": mode})
    run = service.start(actor, request, NOW)
    assert run.run_until == NOW + timedelta(seconds=330)
    assert run.config.params.attack_mode == mode
    saved = service.submit(actor, run.id, submission(), NOW + timedelta(seconds=303))
    assert saved.scored.metrics["unresolved"] == 50


def test_new_moba_requires_mode_but_historical_creation_key_replays_without_it(db, monkeypatch):
    actor = student_actor(db)
    service = FormalAssessmentService(db)
    request = SessionCreate(operation_key=uuid4(), suite_id="pc-moba")
    with pytest.raises(ValueError, match="请选择"):
        service.create(actor, request, NOW)
    old = catalog.get_suite("pc-moba", catalog_version="pc-moba-v3")
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda *_args, **_kwargs: old)
        # Seed the historical persisted request exactly as the old service did.
        from app.esports_assessments.lifecycle import operation_digest
        from app.esports_assessments.models import AssessmentSession

        row = AssessmentSession(
            person_id=actor.person.id,
            created_by_user_id=actor.account.id,
            suite_id="pc-moba",
            creation_key=request.operation_key,
            creation_request_digest=operation_digest(
                "formal",
                "create_session",
                None,
                {"operation_key": request.operation_key, "suite_id": "pc-moba"},
            ),
            catalog_snapshot=old.model_dump(mode="json"),
            scoring_snapshot=old.model_dump(mode="json"),
            report_rules_snapshot=old.model_dump(mode="json"),
            status="in_progress",
            created_at=NOW,
            updated_at=NOW,
        )
        db.add(row)
        db.commit()
    replay = service.create(actor, request, NOW)
    assert replay.suite_config.catalog_version == "pc-moba-v3"


def test_catalog_opens_only_desktop_assessments():
    assert catalog.AVAILABLE_SUITES == frozenset({"pc-fps", "pc-moba"})


def complete_lane(db, actor, mode, hits):
    service = FormalAssessmentService(db)
    session = service.create(
        actor, SessionCreate(operation_key=uuid4(), suite_id="pc-moba", attack_mode=mode), NOW
    )
    run = service.start_attempt(actor, session.id, attempt_request("pc-moba", "last-hit"), NOW)
    payload = submission().model_copy(
        update={
            "raw": catalog.validate_raw(
                run.config,
                {
                    "kind": "lane_last_hit",
                    "deaths": [
                        {
                            "unit_id": i,
                            "at_ms": i // 5 * 30000 + 1000 + i % 5,
                            "by_player": i < hits,
                        }
                        for i in range(50)
                    ],
                    "attacks": 50,
                    "cancelled_attacks": 0,
                },
            )
        }
    )
    return service.submit(actor, run.id, payload, NOW + timedelta(seconds=303)).session.report_id


def test_melee_ranged_and_historical_rank_pools_do_not_mix(db):
    from app.esports_assessments.ranking import AssessmentRankings
    from tests.services.test_esports_rankings import round_row

    subject, high, middle, low = [student_actor(db) for _ in range(4)]
    melee = complete_lane(db, subject, "melee", 20)
    ranged = complete_lane(db, subject, "ranged", 30)
    complete_lane(db, high, "melee", 40)
    complete_lane(db, middle, "melee", 30)
    complete_lane(db, low, "melee", 10)
    complete_lane(db, high, "ranged", 50)
    round_row(db, high, (100,) * 4, suite_id="pc-moba", version=3)
    db.commit()
    melee_rank = AssessmentRankings(db, subject, NOW).report(melee)
    assert (melee_rank.participant_count, melee_rank.overall_rank, melee_rank.overall_score) == (
        4,
        3,
        40,
    )
    assert [(s.rank, s.score, s.gap) for s in melee_rank.top_scores] == [
        (1, 80, 40),
        (2, 60, 20),
        (3, 40, 0),
    ]
    ranged_rank = AssessmentRankings(db, subject, NOW).report(ranged)
    assert (ranged_rank.participant_count, ranged_rank.overall_rank, ranged_rank.overall_score) == (
        2,
        2,
        60,
    )
    assert len(ranged_rank.items) == 1
    assert [(s.rank, s.score, s.gap) for s in ranged_rank.top_scores] == [(1, 100, 40), (2, 60, 0)]
