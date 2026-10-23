"""Exercise persisted v1/v2 runs through v3 defaults, including immutable reports."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest

from app.esports_assessments import catalog
from app.esports_assessments.config_schemas import SuiteConfig
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.models import AssessmentReport
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.queries import AssessmentQueries
from tests.api.test_esports_access import NOW
from tests.esports_helpers import attempt_request, result_request, session_request, student_actor
from tests.services.test_esports_practice import practice_request
from tests.unit.test_esports_versions import SUITES

pytestmark = pytest.mark.usefixtures("esports_enabled")


@pytest.mark.parametrize("suite_id", SUITES)
@pytest.mark.parametrize("revision", [1, 2])
def test_session_views_keep_validated_frozen_suite_for_unfinished_and_abandoned_rounds(
    db, monkeypatch, suite_id, revision
):
    actor = student_actor(db)
    service = FormalAssessmentService(db)
    frozen = catalog.get_suite(suite_id, catalog_version=f"{suite_id}-v{revision}")
    request = session_request(suite_id)
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda _suite_id: frozen)
        created = service.create(actor, request, NOW)
    assert isinstance(created.suite_config, SuiteConfig)
    assert created.suite_config == frozen
    assert service.create(actor, request, NOW).suite_config == frozen
    queries = AssessmentQueries(db, actor, NOW)
    assert queries.session(created.id).suite_config == frozen
    assert queries.context().own_in_progress_sessions[0].suite_config == frozen
    assert queries.history("formal").items[0].session.suite_config == frozen
    abandoned = service.abandon(actor, created.id, uuid4(), NOW + timedelta(seconds=1))
    assert abandoned.status == "abandoned"
    assert abandoned.suite_config == frozen
    assert queries.session(created.id).suite_config == frozen


@pytest.mark.parametrize("suite_id", SUITES)
@pytest.mark.parametrize("revision", [1, 2])
def test_old_formal_round_can_finish_after_upgrade_without_becoming_a_v3_comparison(
    db, monkeypatch, suite_id, revision
):
    actor = student_actor(db)
    service = FormalAssessmentService(db)
    legacy = catalog.get_suite(suite_id, catalog_version=f"{suite_id}-v{revision}")
    first_request = attempt_request(suite_id, legacy.test_ids[0])
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda _suite_id: legacy)
        old = service.create(actor, session_request(suite_id), NOW)
        first = service.start_attempt(actor, old.id, first_request, NOW)
        assert service.start_attempt(actor, old.id, first_request, NOW).id == first.id
        first_submission = result_request(suite_id, first.test_id, seed=first.seed)
        first_saved = service.submit(
            actor,
            first.id,
            first_submission,
            NOW + timedelta(seconds=63),
        )
        assert (
            service.submit(
                actor,
                first.id,
                first_submission,
                NOW + timedelta(seconds=64),
            )
            == first_saved
        )
    assert catalog.get_suite(suite_id).catalog_version == f"{suite_id}-v3"

    def complete(session, at, version, *, start_index=0, use_persisted_version=False):
        saved = first_saved if start_index else None
        for index, test_id in enumerate(session.members[start_index:], start=start_index):
            started_at = at + timedelta(seconds=64 * index)
            request = attempt_request(suite_id, test_id)
            run = service.start_attempt(actor, session.id, request, started_at)
            replay = service.start_attempt(actor, session.id, request, started_at)
            assert run.id == replay.id
            assert run.config.catalog_version == replay.config.catalog_version == version
            submission = (
                result_request(
                    suite_id,
                    test_id,
                    seed=run.seed,
                    catalog_version=run.config.catalog_version,
                )
                if use_persisted_version
                else result_request(suite_id, test_id, seed=run.seed)
            )
            saved = service.submit(actor, run.id, submission, started_at + timedelta(seconds=63))
            assert (
                service.submit(actor, run.id, submission, started_at + timedelta(seconds=64))
                == saved
            )
        assert saved is not None
        return saved.session.report_id

    old_report_id = complete(
        old,
        NOW,
        f"{suite_id}-v{revision}",
        start_index=1,
    )
    frozen = deepcopy(db.get(AssessmentReport, old_report_id).report_snapshot)
    queries = AssessmentQueries(db, actor, NOW + timedelta(minutes=20))
    assert queries.report(old_report_id).suite.catalog_version == f"{suite_id}-v{revision}"
    new = service.create(actor, session_request(suite_id), NOW + timedelta(minutes=10))
    assert new.suite_config.catalog_version == f"{suite_id}-v3"
    new_report_id = complete(
        new,
        NOW + timedelta(minutes=10),
        f"{suite_id}-v3",
        use_persisted_version=True,
    )
    report = queries.report(new_report_id)
    assert report.suite == new.suite_config
    assert [result.config for result in report.results] == [
        new.suite_config.tasks[test_id] for test_id in new.members
    ]
    assert len(report.comparisons) == 4
    for comparison in report.comparisons:
        assert comparison.previous_report_id == old_report_id
        assert comparison.score_delta is None
        assert comparison.non_comparable_reasons == ("config_changed",)
    assert db.get(AssessmentReport, old_report_id).report_snapshot == frozen


@pytest.mark.parametrize("suite_id", SUITES)
@pytest.mark.parametrize("revision", [1, 2])
def test_old_practice_pending_submits_and_replays_under_its_frozen_version(
    db, monkeypatch, suite_id, revision
):
    actor = student_actor(db)
    service = PracticeAssessmentService(db)
    legacy_suite = catalog.get_suite(suite_id, catalog_version=f"{suite_id}-v{revision}")
    test_id = legacy_suite.test_ids[0]
    legacy = legacy_suite.tasks[test_id]
    request = practice_request(suite_id, test_id)
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_task", lambda *_args: legacy)
        old = service.start(actor, request, NOW)
    assert catalog.get_task(suite_id, test_id).catalog_version == f"{suite_id}-v3"
    replay = service.start(actor, request, NOW + timedelta(seconds=1))
    assert replay.id == old.id and replay.config.catalog_version == f"{suite_id}-v{revision}"
    submission = result_request(suite_id, test_id, seed=old.seed)
    saved = service.submit(actor, old.id, submission, NOW + timedelta(seconds=63))
    assert service.submit(actor, old.id, submission, NOW + timedelta(seconds=64)) == saved
    assert saved.scored.score is None and saved.session is None
    current = service.start(actor, practice_request(suite_id, test_id), NOW + timedelta(seconds=65))
    assert current.config.catalog_version == f"{suite_id}-v3"
    queries = AssessmentQueries(db, actor, NOW + timedelta(minutes=2))
    assert (
        queries.detail("practice", old.id).attempt.config.catalog_version
        == f"{suite_id}-v{revision}"
    )
