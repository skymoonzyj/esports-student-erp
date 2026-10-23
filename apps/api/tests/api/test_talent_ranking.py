# Imported pytest fixtures are intentionally named by test arguments.
# ruff: noqa: F811
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select

from app.talent_tests.models import TalentTestResult
from tests.api.test_talent_routes import PREFIX, talent_http  # noqa: F401
from tests.services.test_talent_access import grant, talent_people  # noqa: F401
from tests.services.test_talent_queries import (
    _insert_session,
    _normalized_eight,
    _normalized_seven,
)

NOW = datetime(2026, 10, 5, 8, tzinfo=UTC)


def seed_round(
    db,
    actor,
    scores,
    *,
    age=0,
    version=2,
    inputs=None,
    warned=False,
    report_id=None,
    viewport=1280,
    locale="zh-CN",
    status="completed",
):
    results = (
        _normalized_eight(f"talent-training-v{version}") if version >= 2 else _normalized_seven()
    )
    for index, result in enumerate(results):
        result.score = scores[index] if isinstance(scores, list) else scores
        result.device.input_kind = inputs[index] if inputs else "mouse"
        result.device.viewport_width = viewport
        result.locale = locale
        result.quality_flags = ["background_interrupted"] if warned and index == 0 else []
    session, report = _insert_session(
        db,
        person=actor.person,
        actor=actor,
        created_at=NOW - timedelta(days=age),
        status=status,
        results=results,
        report_id=report_id,
        catalog_version=f"talent-training-v{version}",
    )
    db.commit()
    return session, report


def ranking(client, report):
    response = client.get(f"{PREFIX}/reports/{report.id}/ranking")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    return response.json()


def test_ranking_uses_one_best_whole_round_and_realtime_competition_ties(talent_http, db):
    client, _, actors, _ = talent_http
    _, old = seed_round(db, actors["student"], [90, 50, 60, 60, 60, 60, 60, 60], age=3)
    _, opened = seed_round(db, actors["student"], [10, 100, 50, 50, 50, 50, 50, 50], age=1)
    _, competitor = seed_round(
        db, actors["other"], [100, 50, 50, 60, 60, 60, 60, 60], viewport=1024
    )
    snapshot = deepcopy(opened.report_snapshot)
    result = ranking(client, opened)
    assert result["report_id"] == str(opened.id)
    assert result["best_report_id"] == str(old.id)
    assert result["status"] == "ranked" and result["reason"] is None
    assert result["overall_score"] == 62.5 and result["overall_rank"] == 1
    assert result["participant_count"] == 2
    assert [item["score"] for item in result["items"]] == [90, 50, 60, 60, 60, 60, 60, 60]
    assert [item["rank"] for item in result["items"]] == [2, 1, 1, 1, 1, 1, 1, 1]
    seed_round(db, actors["super_admin"], 80)
    result = ranking(client, opened)
    assert result["participant_count"] == 3 and result["overall_rank"] == 2
    assert [item["rank"] for item in result["items"]] == [2, 2, 2, 2, 2, 2, 2, 2]
    assert str(competitor.id) not in str(result)
    assert str(actors["other"].person.id) not in str(result)
    assert set(result) == {
        "report_id",
        "calculated_at",
        "status",
        "reason",
        "best_report_id",
        "best_completed_at",
        "overall_score",
        "overall_rank",
        "participant_count",
        "items",
    }
    assert datetime.fromisoformat(result["calculated_at"]).utcoffset() is not None
    db.expire(opened)
    assert opened.report_snapshot == snapshot


def test_ranking_tied_round_chooses_earliest_time_then_report_uuid(talent_http, db):
    client, _, actors, _ = talent_http
    _, later = seed_round(db, actors["student"], 60)
    _, earliest_high_id = seed_round(
        db, actors["student"], 60, age=2, report_id=UUID("f0000000-0000-4000-8000-000000000001")
    )
    _, winner = seed_round(
        db, actors["student"], 60, age=2, report_id=UUID("10000000-0000-4000-8000-000000000001")
    )
    result = ranking(client, later)
    assert result["best_report_id"] == str(winner.id)
    assert result["best_completed_at"] == earliest_high_id.report_snapshot["completed_at"]
    assert result["participant_count"] == 1


@pytest.mark.parametrize("version,count,mean", [(1, 7, 60.14), (2, 8, 60.13), (3, 8, 60.13)])
def test_ranking_versions_are_separate_and_mean_uses_half_up(talent_http, db, version, count, mean):
    client, _, actors, _ = talent_http
    _, report = seed_round(db, actors["student"], [61] + [60] * (count - 1), version=version)
    for other_version in (1, 2, 3):
        if other_version != version:
            seed_round(db, actors["other"], 100, version=other_version)
    result = ranking(client, report)
    assert result["overall_score"] == mean
    assert result["participant_count"] == 1
    assert len(result["items"]) == count


def test_ranking_input_kind_order_locale_and_frozen_rules_isolate_groups(talent_http, db):
    client, _, actors, _ = talent_http
    inputs = ["mouse", "touch", "pen", "mixed", "mouse", "mouse", "mouse", "mouse"]
    _, report = seed_round(db, actors["student"], 60, inputs=inputs)
    seed_round(db, actors["other"], 70, inputs=list(reversed(inputs)))
    seed_round(db, actors["guardian"], 90, inputs=inputs, locale="en-US")
    session, _ = seed_round(db, actors["super_admin"], 100, inputs=inputs)
    rules = deepcopy(session.rules_snapshot)
    rules["tests"]["reactiontime"]["anchors"][0] = 999
    session.rules_snapshot = rules
    db.commit()
    result = ranking(client, report)
    assert result["participant_count"] == 1 and result["overall_rank"] == 1


def test_ranking_excludes_warned_incomplete_deleted_and_nonterminal_rounds(talent_http, db):
    client, _, actors, _ = talent_http
    _, report = seed_round(db, actors["student"], 60)
    seed_round(db, actors["other"], 100, warned=True)
    incomplete, _ = seed_round(db, actors["guardian"], 100)
    db.execute(
        delete(TalentTestResult).where(
            TalentTestResult.session_id == incomplete.id,
            TalentTestResult.test_id == "dynamic-vision",
        )
    )
    seed_round(db, actors["coach"], 100)
    actors["coach"].person.deleted_at = NOW
    seed_round(db, actors["admin"], 100, status="abandoned")
    seed_round(db, actors["finance"], 100, status="in_progress")
    db.commit()
    result = ranking(client, report)
    assert result["participant_count"] == 1 and result["overall_rank"] == 1


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("warning", "quality_warning"),
        ("missing", "incomplete_results"),
        ("unknown", "incompatible_input"),
        ("malformed", "incompatible_input"),
    ],
)
def test_ineligible_opened_report_does_not_fall_back_to_a_good_round(talent_http, db, kind, reason):
    client, _, actors, _ = talent_http
    seed_round(db, actors["student"], 100, age=1)
    session, report = seed_round(db, actors["student"], 50, warned=kind == "warning")
    row = db.scalar(select(TalentTestResult).where(TalentTestResult.session_id == session.id))
    if kind == "missing":
        db.delete(row)
    elif kind in {"unknown", "malformed"}:
        device = deepcopy(row.device_snapshot)
        device["input_kind"] = "unknown" if kind == "unknown" else {"private": "payload"}
        row.device_snapshot = device
    db.commit()
    result = ranking(client, report)
    assert result["status"] == "not_eligible" and result["reason"] == reason
    assert result["participant_count"] == 0 and result["items"] == []
    for key in ("best_report_id", "best_completed_at", "overall_score", "overall_rank"):
        assert result[key] is None


def test_ranking_authentication_deny_first_and_private_missing_equivalence(talent_http, db):
    client, sign_in, actors, _ = talent_http
    _, report = seed_round(db, actors["student"], 60)
    endpoint = f"{PREFIX}/reports/{report.id}/ranking"
    client.cookies.clear()
    assert client.get(endpoint).status_code == 401
    sign_in("admin")
    denied = client.get(endpoint)
    missing = client.get(f"{PREFIX}/reports/{uuid4()}/ranking")
    assert denied.status_code == missing.status_code == 404
    assert denied.content == missing.content and dict(denied.headers) == dict(missing.headers)
    sign_in("student")
    grant(db, actors["student"], effect="deny", scope="self")
    assert client.get(endpoint).status_code == 404
    assert client.get(endpoint).headers["cache-control"] == "no-store, private"


@pytest.mark.parametrize("malformed_flags", [None, False, {}, ""])
def test_malformed_falsey_quality_flags_cannot_fall_back_to_another_good_round(
    talent_http, db, malformed_flags
):
    client, _, actors, _ = talent_http
    seed_round(db, actors["student"], 100, age=1)
    session, report = seed_round(db, actors["student"], 50)
    row = db.scalar(select(TalentTestResult).where(TalentTestResult.session_id == session.id))
    row.quality_flags = malformed_flags
    db.commit()
    result = ranking(client, report)
    assert result["status"] == "not_eligible" and result["reason"] == "quality_warning"
    assert result["participant_count"] == 0 and result["items"] == []
    for key in ("best_report_id", "best_completed_at", "overall_score", "overall_rank"):
        assert result[key] is None


def test_ranking_competition_gap_and_query_count_do_not_grow_with_histories(talent_http, db):
    client, _, actors, _ = talent_http
    _, report = seed_round(db, actors["student"], 50)
    seed_round(db, actors["other"], 60)
    seed_round(db, actors["guardian"], 60)
    statements = []
    connection = db.connection()

    def capture(_conn, _cursor, statement, _params, _context, _executemany):
        if (
            "FROM talent_test_sessions" in statement
            or "FROM talent_test_results" in statement
            or statement.lstrip().startswith("WITH expected AS")
        ):
            statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        result = ranking(client, report)
        count = len(statements)
        assert count == 3
        assert result["overall_rank"] == 3
        assert all(item["rank"] == 3 for item in result["items"])
        for age in range(1, 5):
            seed_round(db, actors["other"], 40, age=age)
        statements.clear()
        assert ranking(client, report)["participant_count"] == 3
        assert len(statements) == count
    finally:
        event.remove(connection, "before_cursor_execute", capture)
