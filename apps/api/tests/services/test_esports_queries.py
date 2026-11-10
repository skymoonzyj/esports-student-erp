from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from app.audit.models import AuditEvent
from app.esports_assessments.catalog import get_suite
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.esports_assessments.reporting import build_report, comparison_key
from app.esports_assessments.schemas import PersonDisplaySnapshot
from tests.api import test_esports_access as access_fixtures
from tests.api.test_esports_access import NOW, grant
from tests.unit.test_esports_reporting import environment, report_results, settings

esports_people = access_fixtures.esports_people


def legacy_suite(suite_id: str):
    return get_suite(suite_id, catalog_version=f"{suite_id}-v2")


def session_row(
    db,
    actor,
    *,
    at=NOW,
    status="completed",
    suite_id="pc-fps",
    key=None,
    flags=False,
    catalog_version=None,
):
    suite = get_suite(suite_id, catalog_version=catalog_version or f"{suite_id}-v2")
    row = AssessmentSession(
        person_id=actor.person.id,
        created_by_user_id=actor.account.id,
        suite_id=suite_id,
        creation_key=uuid4(),
        creation_request_digest="a" * 64,
        catalog_snapshot=suite.model_dump(mode="json"),
        scoring_snapshot={},
        report_rules_snapshot={},
        status=status,
        created_at=at - timedelta(minutes=5),
        completed_at=at if status == "completed" else None,
        abandoned_at=at if status == "abandoned" else None,
    )
    db.add(row)
    db.flush()
    if status != "completed":
        return row, None
    results = report_results(suite_id, catalog_version=catalog_version)
    for index, value in enumerate(results):
        if flags:
            value = value.model_copy(
                update={
                    "scored": value.scored.model_copy(update={"quality_flags": ("long_frames",)})
                }
            )
        attempt = AssessmentAttempt(
            session_id=row.id,
            created_by_user_id=actor.account.id,
            test_id=value.test_id,
            sequence_no=1,
            seed=1,
            config_snapshot=value.config.model_dump(mode="json"),
            settings_snapshot=value.settings.model_dump(mode="json"),
            environment_snapshot=value.environment.model_dump(mode="json"),
            state="submitted",
            creation_key=uuid4(),
            creation_request_digest="a" * 64,
            created_at=at - timedelta(minutes=2),
            run_until=at - timedelta(seconds=30),
            submit_until=at + timedelta(minutes=28),
            submitted_at=at,
        )
        db.add(attempt)
        db.flush()
        result = AssessmentResult(
            attempt_id=attempt.id,
            session_id=row.id,
            test_id=value.test_id,
            created_by_user_id=actor.account.id,
            submission_key=uuid4(),
            submission_request_digest="b" * 64,
            raw_result={},
            metrics=value.scored.metrics,
            score=value.scored.score,
            grade=value.scored.grade,
            quality_flags=["long_frames"] if flags else [],
            comparison_key=key or comparison_key(value.config, value.settings, value.environment),
            created_at=at,
        )
        db.add(result)
        db.flush()
        results[index] = value.model_copy(update={"result_id": result.id})
    report_id = uuid4()
    snapshot = build_report(
        row.id,
        PersonDisplaySnapshot(person_id=actor.person.id, display_name=actor.person.full_name),
        suite,
        results,
        at,
        report_id=report_id,
    )
    report = AssessmentReport(
        id=report_id,
        session_id=row.id,
        report_snapshot=snapshot.model_dump(mode="json"),
        created_at=at,
    )
    db.add(report)
    db.flush()
    return row, report


def test_reports_authorize_before_count_page_and_private_lookup(db, esports_people):
    from app.esports_assessments.errors import AssessmentNotFound
    from app.esports_assessments.queries import AssessmentQueries

    _, actors, relationship = esports_people
    own, own_report = session_row(db, actors["student"])
    _, other_report = session_row(db, actors["other"], at=NOW + timedelta(minutes=1))
    session_row(db, actors["student"], status="in_progress")
    db.commit()
    queries = AssessmentQueries(db, actors["guardian"], NOW)
    page = queries.reports(limit=1)
    assert page.visible_total == 1
    assert [item.id for item in page.items] == [own_report.id]
    assert page.next_cursor is None
    assert queries.report(own_report.id).session_id == own.id
    statements = []

    def capture(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(sql)

    event.listen(db.connection(), "before_cursor_execute", capture)
    for target in (other_report.id, uuid4()):
        with pytest.raises(AssessmentNotFound, match="资源不存在"):
            queries.report(target)
    for target in (actors["other"].person.id, uuid4()):
        with pytest.raises(AssessmentNotFound, match="资源不存在"):
            queries.reports(filters={"person_id": target})
    assert not any(
        sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements
    )
    event.remove(db.connection(), "before_cursor_execute", capture)
    relationship.ended_at = NOW
    db.commit()
    assert queries.reports().visible_total == 0
    with pytest.raises(AssessmentNotFound):
        queries.report(own_report.id)


def test_report_cursor_stable_order_and_filtered_total(db, esports_people):
    from app.esports_assessments.queries import AssessmentQueries

    _, actors, _ = esports_people
    ids = [session_row(db, actors["student"])[1].id for _ in range(3)]
    session_row(db, actors["other"], at=NOW + timedelta(days=1))
    db.commit()
    queries = AssessmentQueries(db, actors["student"], NOW)
    page = queries.reports(limit=2)
    second = queries.reports(cursor=page.next_cursor, limit=2)
    assert [item.id for item in (*page.items, *second.items)] == sorted(ids, reverse=True)
    assert page.visible_total == second.visible_total == 3
    assert second.next_cursor is None


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "garbage",
        "e30",
        "a" * 2049,
        "eyJ0IjoiMjAyNi0wOS0yMCIsImlkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIn0",
    ],
)
def test_invalid_cursors_are_rejected(db, esports_people, cursor):
    from app.esports_assessments.queries import AssessmentQueries

    with pytest.raises(ValueError):
        AssessmentQueries(db, esports_people[1]["student"], NOW).reports(cursor=cursor)


@pytest.mark.parametrize("limit", [0, 101, -1, True, 1.5, "20"])
def test_invalid_limits_are_rejected(db, esports_people, limit):
    from app.esports_assessments.queries import AssessmentQueries

    with pytest.raises(ValueError):
        AssessmentQueries(db, esports_people[1]["student"], NOW).history("formal", limit=limit)


@pytest.mark.parametrize(
    "filters",
    [
        {"date_from": "20260920"},
        {"date_from": "0001-01-01"},
        {"date_to": "9999-12-31"},
        {"date_to": "2026-09-31"},
        {"date_from": "2026-09-21", "date_to": "2026-09-20"},
        {"suite_id": "unknown"},
        {"person_id": "not-a-uuid"},
        {"untrusted": "value"},
    ],
)
def test_report_filters_strictly_rejected(db, esports_people, filters):
    from app.esports_assessments.queries import AssessmentQueries

    with pytest.raises(ValueError):
        AssessmentQueries(db, esports_people[1]["student"], NOW).reports(filters=filters)


def test_report_dates_are_inclusive_china_days(db, esports_people):
    from app.esports_assessments.queries import AssessmentQueries

    _, actors, _ = esports_people
    start = datetime(2026, 9, 19, 16, tzinfo=UTC)
    records = [
        session_row(db, actors["student"], at=start + offset)[1]
        for offset in (
            timedelta(microseconds=-1),
            timedelta(),
            timedelta(days=1, microseconds=-1),
            timedelta(days=1),
        )
    ]
    db.commit()
    page = AssessmentQueries(db, actors["student"], NOW).reports(
        filters={"date_from": "2026-09-20", "date_to": "2026-09-20"}
    )
    assert {item.id for item in page.items} == {records[1].id, records[2].id}
    assert page.visible_total == 2


def test_history_is_own_rounds_and_revocation_is_private(db, esports_people):
    from app.esports_assessments.errors import AssessmentNotFound
    from app.esports_assessments.queries import AssessmentQueries

    _, actors, _ = esports_people
    for state in ("completed", "in_progress", "abandoned"):
        session_row(db, actors["student"], status=state)
    session_row(db, actors["other"])
    db.commit()
    q = AssessmentQueries(db, actors["student"], NOW)
    page = q.history("formal")
    assert page.visible_total == 3
    assert {item.session.status for item in page.items} == {"completed", "in_progress", "abandoned"}
    assert q.history("practice").visible_total == 0
    with pytest.raises(ValueError):
        q.history("all")
    grant(db, actors["student"], effect="deny", scope="self")
    before = db.scalar(select(func.count()).select_from(AuditEvent))
    with pytest.raises(AssessmentNotFound):
        q.history("formal")
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == before


def test_comparison_filters_ineligible_recent_rows_before_limit(db, esports_people):
    from app.esports_assessments.errors import AssessmentNotFound
    from app.esports_assessments.queries import AssessmentQueries

    _, actors, _ = esports_people
    _, expected = session_row(db, actors["student"], at=NOW - timedelta(days=3))
    session_row(db, actors["student"], at=NOW - timedelta(days=2), key="0" * 64)
    session_row(db, actors["student"], at=NOW - timedelta(days=1), flags=True)
    current, _ = session_row(db, actors["student"], at=NOW)
    # A later completion must not leak into the baseline, even if its result is older.
    future, _ = session_row(db, actors["student"], at=NOW + timedelta(days=1))
    for result in db.scalars(
        select(AssessmentResult).where(AssessmentResult.session_id == future.id)
    ):
        result.created_at = NOW - timedelta(days=10)
    db.commit()
    q = AssessmentQueries(db, actors["student"], NOW)
    config = legacy_suite("pc-fps").tasks["precision"]
    args = {
        "person_id": actors["student"].person.id,
        "suite_id": "pc-fps",
        "test_id": "precision",
        "comparison_key": comparison_key(config, settings("pc-fps"), environment("pc-fps")),
        "before": NOW,
        "exclude_session_id": current.id,
    }
    report_id, result = q.comparable_result(**args)
    assert report_id == expected.id
    assert result.test_id == "precision"
    assert result.scored.score == 95
    assert q.comparable_result(**{**args, "comparison_key": "1" * 64}) is None
    with pytest.raises(AssessmentNotFound):
        AssessmentQueries(db, actors["other"], NOW).comparable_result(**args)


def test_previous_result_filters_before_limit_and_uses_latest_snapshot_even_if_flagged(
    db, esports_people
):
    from app.esports_assessments.queries import AssessmentQueries

    _, actors, _ = esports_people
    session_row(db, actors["student"], at=NOW - timedelta(days=3))
    candidates = [
        session_row(db, actors["student"], at=NOW - timedelta(days=1), flags=True, key="0" * 64)[1]
        for _ in range(2)
    ]
    expected = max(candidates, key=lambda row: row.id)
    session_row(db, actors["other"], at=NOW - timedelta(hours=2))
    session_row(db, actors["student"], at=NOW - timedelta(hours=1), suite_id="pc-moba")
    excluded, _ = session_row(db, actors["student"], at=NOW - timedelta(minutes=1))
    session_row(db, actors["student"], at=NOW)
    session_row(db, actors["student"], at=NOW + timedelta(days=1))
    session_row(db, actors["student"], status="in_progress")
    db.commit()
    query = AssessmentQueries(db, actors["guardian"], NOW)
    args = {
        "person_id": actors["student"].person.id,
        "suite_id": "pc-fps",
        "test_id": "precision",
        "before": NOW,
        "exclude_session_id": excluded.id,
    }
    found = query.previous_result(**args)
    assert found is not None
    report_id, result = found
    assert report_id == expected.id
    assert result.model_dump(mode="json") == expected.report_snapshot["results"][0]
    assert result.scored.quality_flags == ("long_frames",)
    assert query.previous_result(**{**args, "test_id": "last-hit"}) is None
    assert query.previous_result(**{**args, "before": NOW - timedelta(days=10)}) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        query.previous_result(**{**args, "before": NOW.replace(tzinfo=None)})


def test_previous_result_reauthorizes_without_private_writes(db, esports_people):
    from app.esports_assessments.errors import AssessmentNotFound
    from app.esports_assessments.queries import AssessmentQueries

    _, actors, relationship = esports_people
    session_row(db, actors["student"], at=NOW - timedelta(days=1))
    db.commit()
    query = AssessmentQueries(db, actors["guardian"], NOW)
    args = {
        "person_id": actors["student"].person.id,
        "suite_id": "pc-fps",
        "test_id": "precision",
        "before": NOW,
        "exclude_session_id": uuid4(),
    }
    assert query.previous_result(**args) is not None
    relationship.ended_at = NOW
    db.commit()
    before = db.scalar(select(func.count()).select_from(AuditEvent))
    statements = []

    def capture(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(sql)

    event.listen(db.connection(), "before_cursor_execute", capture)
    try:
        for person_id in (actors["student"].person.id, actors["other"].person.id, uuid4()):
            with pytest.raises(AssessmentNotFound, match="资源不存在"):
                query.previous_result(**{**args, "person_id": person_id})
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == before
        assert not any(
            sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements
        )
    finally:
        event.remove(db.connection(), "before_cursor_execute", capture)


def test_practice_history_has_only_completed_own_metrics(db, esports_people):
    from app.esports_assessments.queries import AssessmentQueries

    _, actors, _ = esports_people
    config = legacy_suite("pc-fps").tasks["precision"]
    for actor, state in (
        (actors["student"], "submitted"),
        (actors["student"], "running"),
        (actors["other"], "submitted"),
    ):
        row = AssessmentPracticeRun(
            person_id=actor.person.id,
            created_by_user_id=actor.account.id,
            suite_id="pc-fps",
            test_id="precision",
            difficulty=3,
            seed=1,
            config_snapshot=config.model_dump(mode="json"),
            settings_snapshot=settings("pc-fps").model_dump(mode="json"),
            environment_snapshot=environment("pc-fps").model_dump(mode="json"),
            state=state,
            creation_key=uuid4(),
            creation_request_digest="a" * 64,
            created_at=NOW - timedelta(minutes=2),
            run_until=NOW - timedelta(seconds=30),
            submit_until=NOW + timedelta(minutes=28),
            submitted_at=NOW if state == "submitted" else None,
            submission_key=uuid4() if state == "submitted" else None,
            submission_request_digest="b" * 64 if state == "submitted" else None,
            raw_result={},
            metrics={"hit_rate": 0.8},
            quality_flags=[],
            comparison_key="c" * 64,
        )
        db.add(row)
    db.commit()
    page = AssessmentQueries(db, actors["student"], NOW).history("practice")
    assert page.visible_total == 1
    item = page.items[0]
    assert item.scored.metrics == {"hit_rate": 0.8}
    assert item.scored.score is item.report_id is item.session_id is None


def test_supplemental_dtos_reject_invalid_identity_and_naive_dates():
    from pydantic import ValidationError

    from app.esports_assessments.query_schemas import ActiveRunView, CursorPage, ReportFilters

    with pytest.raises(ValidationError):
        ActiveRunView(
            mode="formal",
            attempt_id=uuid4(),
            session_id=None,
            suite_id="pc-fps",
            test_id="precision",
            run_until=NOW,
            submit_until=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValidationError):
        ActiveRunView(
            mode="practice",
            attempt_id=uuid4(),
            session_id=None,
            suite_id="pc-fps",
            test_id="precision",
            run_until=NOW.replace(tzinfo=None),
            submit_until=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValidationError):
        CursorPage[int](items=[1], next_cursor=None, visible_total=0)
    assert ReportFilters(date_from="2026-09-20").date_from.isoformat() == "2026-09-20"


def test_context_recovery_catalog_and_detail_validate_wire_invariants():
    from pydantic import ValidationError

    from app.esports_assessments.query_schemas import (
        AssessmentContextView,
        AttemptDetailView,
        RecoveryItem,
        SuiteCatalogView,
    )
    from app.esports_assessments.schemas import (
        AttemptView,
        PracticeResult,
        PracticeResultSaveView,
        SessionView,
    )

    suite = get_suite("pc-fps", catalog_version="pc-fps-v3")
    session = SessionView(
        id=uuid4(),
        suite_id="pc-fps",
        suite_config=suite,
        status="in_progress",
        members=suite.test_ids,
        results=(),
        report_id=None,
    )
    run = {
        "mode": "formal",
        "attempt_id": uuid4(),
        "session_id": session.id,
        "suite_id": "pc-fps",
        "test_id": "precision",
        "run_until": NOW + timedelta(seconds=90),
        "submit_until": NOW + timedelta(minutes=30),
    }
    context = {
        "server_now": NOW,
        "can_take": True,
        "can_read_self": True,
        "can_read_reports": True,
        "available_suites": ["pc-fps"],
        "own_in_progress_sessions": [session],
        "active_run": run,
    }
    assert AssessmentContextView.model_validate(context).active_run.attempt_id == run["attempt_id"]
    invalid = [
        {"available_suites": ["pc-fps", "pc-fps"]},
        {"own_in_progress_sessions": [session, session]},
        {"own_in_progress_sessions": []},
        {"can_read_self": False},
        {"active_run": {**run, "test_id": "last-hit"}},
        {"active_run": {**run, "session_id": uuid4()}},
    ]
    for change in invalid:
        with pytest.raises(ValidationError):
            AssessmentContextView.model_validate({**context, **change})
    recovery = {**run, "state": "running", "issued_at": NOW}
    assert RecoveryItem.model_validate(recovery).issued_at == NOW
    for change in (
        {"issued_at": run["run_until"]},
        {"test_id": "last-hit"},
        {"state": "submitted"},
    ):
        with pytest.raises(ValidationError):
            RecoveryItem.model_validate({**recovery, **change})
    assert len(SuiteCatalogView(items=(suite,)).items) == 1
    with pytest.raises(ValidationError):
        SuiteCatalogView(items=(suite, suite))
    attempt = AttemptView(
        id=uuid4(),
        mode="practice",
        session_id=None,
        suite_id="pc-fps",
        test_id="precision",
        state="submitted",
        server_now=NOW,
        issued_at=NOW,
        run_until=run["run_until"],
        submit_until=run["submit_until"],
        seed=1,
        config=suite.tasks["precision"],
        settings=settings("pc-fps"),
        environment=environment("pc-fps"),
    )
    saved = PracticeResultSaveView(
        result_id=attempt.id, scored=PracticeResult(metrics={}, quality_flags=())
    )
    assert AttemptDetailView(attempt=attempt, saved=saved).saved.result_id == attempt.id
    with pytest.raises(ValidationError):
        AttemptDetailView(attempt=attempt, saved=None)
    with pytest.raises(ValidationError):
        AttemptDetailView(attempt=attempt.model_copy(update={"state": "running"}), saved=saved)


@pytest.mark.parametrize(
    "payload",
    [
        {"time": "2026-09-20T08:00:00", "id": "00000000-0000-0000-0000-000000000000"},
        {"time": "2026-09-20T08:00:00+00:00", "id": "not-a-uuid"},
        {"time": 0, "id": "00000000-0000-0000-0000-000000000000"},
        {"time": "2026-09-31T08:00:00+00:00", "id": "00000000-0000-0000-0000-000000000000"},
        {
            "time": "2026-09-20T08:00:00+00:00",
            "id": "00000000-0000-0000-0000-000000000000",
            "extra": True,
        },
    ],
)
def test_cursor_payload_requires_aware_datetime_and_canonical_uuid(payload):
    import base64
    import json

    from app.esports_assessments.queries import decode_cursor

    cursor = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    with pytest.raises(ValueError):
        decode_cursor(cursor)
