from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select

from app.esports_assessments.catalog import get_suite
from app.esports_assessments.errors import AssessmentNotFound
from app.esports_assessments.models import AssessmentAttempt, AssessmentResult
from tests.api import test_esports_access as access_fixtures
from tests.api.test_esports_access import NOW, grant
from tests.services.test_esports_queries import session_row

esports_people = access_fixtures.esports_people


def round_row(
    db,
    actor,
    scores=(60, 60, 60, 60),
    *,
    at=NOW,
    suite_id="pc-fps",
    version=2,
    flags=False,
    mouse_mode="raw",
    report_id=None,
):
    session, report = session_row(db, actor, at=at, suite_id=suite_id, flags=flags)
    suite = get_suite(suite_id, catalog_version=f"{suite_id}-v{version}")
    session.catalog_snapshot = suite.model_dump(mode="json")
    for index, test_id in enumerate(suite.test_ids):
        result = db.scalar(
            select(AssessmentResult).where(
                AssessmentResult.session_id == session.id, AssessmentResult.test_id == test_id
            )
        )
        result.score = scores[index]
        attempt = db.get(AssessmentAttempt, result.attempt_id)
        attempt.config_snapshot = suite.tasks[test_id].model_dump(mode="json")
        if suite_id == "pc-fps":
            attempt.settings_snapshot = {**attempt.settings_snapshot, "mouse_mode": mouse_mode}
    if report_id:
        report.id = report_id
    db.flush()
    return session, report


def ranking(db, actor, report):
    from app.esports_assessments.ranking import AssessmentRankings

    return AssessmentRankings(db, actor, NOW).report(report.id)


def test_esports_best_whole_round_exact_mean_competition_ties_and_snapshot_immutable(
    db, esports_people
):
    _, actors, _ = esports_people
    _, old = round_row(db, actors["student"], (100, 0, 100, 0), at=NOW - timedelta(days=2))
    _, best = round_row(db, actors["student"], (70, 80, 60, 61), at=NOW - timedelta(days=1))
    round_row(db, actors["other"], (70, 80, 60, 61))
    _, lower = round_row(db, actors["guardian"], (70, 80, 60, 60))
    round_row(db, actors["student"], (90, 0, 90, 0))
    frozen = deepcopy(old.report_snapshot)
    db.commit()
    result = ranking(db, actors["student"], old)
    assert result.best_report_id == best.id
    assert result.overall_score == 67.75
    assert (result.overall_rank, result.participant_count) == (1, 3)
    assert [(i.score, i.rank) for i in result.items] == [(70, 1), (80, 1), (60, 1), (61, 1)]
    assert ranking(db, actors["super_admin"], lower).overall_rank == 3
    assert old.report_snapshot == frozen
    round_row(db, actors["super_admin"], (71, 81, 61, 62))
    db.commit()
    refreshed = ranking(db, actors["student"], old)
    assert (refreshed.overall_rank, refreshed.participant_count) == (2, 4)
    assert [i.rank for i in refreshed.items] == [2, 2, 2, 2]
    payload = refreshed.model_dump(mode="json")
    assert str(actors["other"].person.id) not in str(payload)
    assert actors["other"].person.full_name not in str(payload)


def test_esports_tied_best_round_uses_earliest_completion_then_stable_report_uuid(
    db, esports_people
):
    _, actors, _ = esports_people
    _, later = round_row(db, actors["student"], (70, 70, 70, 70), at=NOW)
    _, earlier = round_row(
        db, actors["student"], (60, 80, 70, 70), at=NOW - timedelta(days=1), report_id=UUID(int=200)
    )
    _, stable = round_row(
        db, actors["student"], (80, 60, 70, 70), at=NOW - timedelta(days=1), report_id=UUID(int=100)
    )
    db.commit()
    result = ranking(db, actors["student"], later)
    assert result.best_report_id == stable.id != earlier.id
    assert [i.score for i in result.items] == [80, 60, 70, 70]


@pytest.mark.parametrize("suite_id", ["pc-fps", "mobile-fps", "pc-moba", "mobile-moba"])
@pytest.mark.parametrize("version", [1, 2, 3])
def test_esports_suites_and_frozen_versions_are_independent(db, esports_people, suite_id, version):
    _, actors, _ = esports_people
    _, report = round_row(db, actors["student"], suite_id=suite_id, version=version)
    for alternative in (1, 2, 3):
        if alternative != version:
            round_row(db, actors["other"], (100,) * 4, suite_id=suite_id, version=alternative)
    for alternative in ("pc-fps", "mobile-fps", "pc-moba", "mobile-moba"):
        if alternative != suite_id:
            round_row(db, actors["super_admin"], (100,) * 4, suite_id=alternative, version=version)
    db.commit()
    result = ranking(db, actors["student"], report)
    assert result.status == "ranked"
    assert (result.overall_rank, result.participant_count) == (1, 1)
    assert [item.test_id for item in result.items] == list(get_suite(suite_id).test_ids)


def test_esports_geometry_and_sensitivity_do_not_split_input_group(db, esports_people):
    _, actors, _ = esports_people
    _, report = round_row(db, actors["student"])
    other, _ = round_row(db, actors["other"], (90,) * 4)
    for attempt in db.scalars(
        select(AssessmentAttempt).where(AssessmentAttempt.session_id == other.id)
    ):
        attempt.settings_snapshot = {**attempt.settings_snapshot, "sensitivity": 1.5}
        attempt.environment_snapshot = {
            **attempt.environment_snapshot,
            "width_css_px": 1600,
            "device_pixel_ratio": 2,
        }
    db.commit()
    result = ranking(db, actors["student"], report)
    assert (result.overall_rank, result.participant_count) == (2, 2)


def test_esports_raw_relative_input_groups_are_per_item_and_separate(db, esports_people):
    _, actors, _ = esports_people
    _, raw = round_row(db, actors["student"])
    _, relative = round_row(db, actors["student"], (100,) * 4, mouse_mode="relative")
    round_row(db, actors["other"], (90,) * 4, mouse_mode="relative")
    mixed, mixed_report = round_row(db, actors["guardian"], (80,) * 4)
    attempt = db.scalar(select(AssessmentAttempt).where(AssessmentAttempt.session_id == mixed.id))
    attempt.settings_snapshot = {**attempt.settings_snapshot, "mouse_mode": "relative"}
    db.commit()
    assert ranking(db, actors["student"], raw).participant_count == 1
    result = ranking(db, actors["student"], relative)
    assert (result.participant_count, result.overall_rank) == (2, 1)
    assert result.best_report_id == relative.id
    assert ranking(db, actors["super_admin"], mixed_report).participant_count == 1


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("warning", "quality_warning"),
        ("missing", "incomplete_results"),
        ("unknown", "incompatible_input"),
        ("config", "incompatible_input"),
    ],
)
def test_esports_bad_target_is_ineligible_even_with_another_good_round(
    db, esports_people, defect, reason
):
    _, actors, _ = esports_people
    session, report = round_row(db, actors["student"], flags=defect == "warning")
    round_row(db, actors["student"], (90,) * 4)
    result = db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == session.id))
    attempt = db.get(AssessmentAttempt, result.attempt_id)
    if defect == "missing":
        db.delete(result)
    if defect == "unknown":
        attempt.environment_snapshot = {**attempt.environment_snapshot, "input_kind": "unknown"}
    if defect == "config":
        attempt.config_snapshot = {**attempt.config_snapshot, "engine_version": "unregistered"}
    db.commit()
    value = ranking(db, actors["student"], report)
    assert value.status == "not_eligible" and value.reason == reason
    assert value.participant_count == 0 and value.items == ()
    assert value.best_report_id is value.overall_score is value.overall_rank is None


def test_esports_peer_warnings_incomplete_deleted_and_malformed_inputs_are_excluded(
    db, esports_people
):
    people, actors, _ = esports_people
    _, report = round_row(db, actors["student"])
    round_row(db, actors["other"], (100,) * 4, flags=True)
    round_row(db, actors["guardian"], (100,) * 4)
    people["guardian"].deleted_at = NOW
    broken, _ = round_row(db, actors["super_admin"], (100,) * 4)
    result = db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == broken.id))
    db.delete(result)
    malformed, _ = round_row(db, actors["coach"], (100,) * 4)
    attempt = db.scalar(
        select(AssessmentAttempt).where(AssessmentAttempt.session_id == malformed.id)
    )
    attempt.environment_snapshot = {**attempt.environment_snapshot, "input_kind": "unknown"}
    session_row(db, actors["admin"], status="abandoned")
    session_row(db, actors["assistant"], status="in_progress")
    db.commit()
    assert ranking(db, actors["student"], report).participant_count == 1


@pytest.mark.parametrize(
    "defect,reason",
    [("unsubmitted_attempt", "incomplete_results"), ("defaulted_catalog", "incompatible_input")],
)
def test_esports_invalid_opened_round_cannot_fall_back_to_another_valid_best_round(
    db, esports_people, defect, reason
):
    _, actors, _ = esports_people
    session, report = round_row(db, actors["student"])
    round_row(db, actors["student"], (90,) * 4)
    if defect == "unsubmitted_attempt":
        attempt = db.scalar(
            select(AssessmentAttempt).where(AssessmentAttempt.session_id == session.id)
        )
        attempt.state = "expired"
        attempt.submitted_at = None
    else:
        metadata = deepcopy(session.catalog_snapshot)
        del metadata["tasks"]["precision"]["params"]["horizontal_fov_deg"]
        session.catalog_snapshot = metadata
    db.commit()
    value = ranking(db, actors["student"], report)
    assert value.status == "not_eligible"
    assert value.reason == reason
    assert value.best_report_id is value.overall_score is value.overall_rank is None
    assert value.participant_count == 0 and value.items == ()


def test_esports_rank_authorizes_before_aggregate_and_private_not_found(db, esports_people):
    _, actors, relationship = esports_people
    _, own = round_row(db, actors["student"])
    _, other = round_row(db, actors["other"])
    db.commit()
    statements = []

    def capture(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(sql)

    event.listen(db.connection(), "before_cursor_execute", capture)
    for report in (other, type("Missing", (), {"id": uuid4()})()):
        with pytest.raises(AssessmentNotFound):
            ranking(db, actors["student"], report)
    assert not any("row_number()" in sql.lower() for sql in statements)
    assert not any(
        sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements
    )
    event.remove(db.connection(), "before_cursor_execute", capture)
    grant(db, actors["student"], effect="deny", scope="self")
    with pytest.raises(AssessmentNotFound):
        ranking(db, actors["student"], own)
    relationship.ended_at = NOW
    db.commit()
    with pytest.raises(AssessmentNotFound):
        ranking(db, actors["guardian"], own)


def test_esports_rank_aggregate_query_count_is_independent_of_participant_histories(
    db, esports_people
):
    _, actors, _ = esports_people
    _, report = round_row(db, actors["student"])
    db.commit()
    statements = []

    def capture(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(sql)

    event.listen(db.connection(), "before_cursor_execute", capture)
    first = ranking(db, actors["student"], report)
    first_count = len(statements)
    assert first.participant_count == 1
    assert sum("row_number()" in sql.lower() for sql in statements) == 1
    event.remove(db.connection(), "before_cursor_execute", capture)
    for index in range(12):
        round_row(db, actors["other"], (70 + index,) * 4, at=NOW + timedelta(minutes=index))
    db.commit()
    statements.clear()
    event.listen(db.connection(), "before_cursor_execute", capture)
    second = ranking(db, actors["student"], report)
    assert second.participant_count == 2
    assert len(statements) == first_count
    assert sum("row_number()" in sql.lower() for sql in statements) == 1
    assert len(second.items) == 4
    event.remove(db.connection(), "before_cursor_execute", capture)
