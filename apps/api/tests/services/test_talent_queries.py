from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.identity.models import GuardianRelationship, Person
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.talent_tests.catalog import LEGACY_TEST_IDS, TEST_IDS, get_rules
from app.talent_tests.errors import TalentNotFound
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from app.talent_tests.queries import TalentQueries, find_comparable_baselines
from app.talent_tests.reporting import build_report
from app.talent_tests.schemas import NormalizedResult, PersonDisplay, RawResultInput
from app.talent_tests.scoring import normalize_result
from tests.unit.talent_test_helpers import valid_payload

RAW_ADAPTER: TypeAdapter[RawResultInput] = TypeAdapter(RawResultInput)
NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)
QueryPeople = tuple[dict[str, Person], dict[str, AuthContext]]
CapturedStatement = tuple[str, Any]


def _capture_statements(connection: Connection) -> tuple[list[CapturedStatement], Any]:
    statements: list[CapturedStatement] = []

    def capture(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append((statement, parameters))

    event.listen(connection, "before_cursor_execute", capture)
    return statements, capture


def _normalized_seven() -> list[NormalizedResult]:
    rules = get_rules("talent-training-v1")
    return [
        normalize_result(RAW_ADAPTER.validate_python(valid_payload(test_id)), rules)
        for test_id in LEGACY_TEST_IDS
    ]


def _normalized_eight() -> list[NormalizedResult]:
    rules = get_rules("talent-training-v2")
    return [
        normalize_result(RAW_ADAPTER.validate_python(valid_payload(test_id)), rules)
        for test_id in TEST_IDS
    ]


@pytest.fixture
def query_people(db: Session) -> QueryPeople:
    people: dict[str, Person] = {}
    actors: dict[str, AuthContext] = {}
    for role in ("student", "guardian", "other"):
        marker = uuid4().hex
        person = Person(
            full_name=f"{role}-name",
            nickname=f"{role}-nick",
            identity_ciphertext=f"identity-secret-{role}",
            identity_digest=marker,
            email_ciphertext=f"email-secret-{role}",
            email_digest=uuid4().hex,
            sex="male",
            date_of_birth=date(2000, 1, 1),
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=uuid4().hex,
            email_normalized=f"{marker}@example.test",
            password_hash="test",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        role_key = "student" if role == "other" else role
        db.add(UserRole(user_id=account.id, role_id=ROLES[role_key].id))
        people[role] = person
        actors[role] = AuthContext(
            account=account,
            person=person,
            session=AuthSession(),
        )
    db.add(
        GuardianRelationship(
            guardian_person_id=people["guardian"].id,
            student_person_id=people["student"].id,
            relationship="parent",
            valid_from=date(2020, 1, 1),
        )
    )
    db.commit()
    return people, actors


def _insert_session(
    db: Session,
    *,
    person: Person,
    actor: AuthContext,
    created_at: datetime,
    status: str,
    results: list[NormalizedResult] | None = None,
    session_id: UUID | None = None,
    report_id: UUID | None = None,
    snapshot_name: str | None = None,
    catalog_version: str = "talent-training-v1",
    scoring_version: str | None = None,
) -> tuple[TalentTestSession, TalentTestReport | None]:
    rules = get_rules(catalog_version)
    terminal_at = created_at + timedelta(minutes=10)
    session = TalentTestSession(
        id=session_id or uuid4(),
        person_id=person.id,
        created_by_user_id=actor.account.id,
        creation_key=uuid4(),
        creation_request_digest=uuid4().hex * 2,
        catalog_version=rules.catalog_version,
        scoring_version=scoring_version or rules.scoring_version,
        report_version=rules.report_version,
        rules_snapshot=rules.model_dump(mode="json"),
        status=status,
        created_at=created_at,
        completed_at=terminal_at if status == "completed" else None,
        abandoned_at=terminal_at if status == "abandoned" else None,
        abandon_request_id=uuid4() if status == "abandoned" else None,
        abandon_request_digest=uuid4().hex * 2 if status == "abandoned" else None,
    )
    db.add(session)
    db.flush()
    for index, result in enumerate(results or []):
        db.add(
            TalentTestResult(
                session_id=session.id,
                test_id=result.test_id,
                engine_version=result.engine_version,
                locale=result.locale,
                raw_result=result.raw.model_dump(mode="json"),
                device_snapshot=result.device.model_dump(mode="json"),
                normalized_result=result.model_dump(mode="json"),
                score=result.score,
                quality_flags=list(result.quality_flags),
                duration_ms=result.duration_ms,
                background_interrupted=result.background_interrupted,
                submission_id=uuid4(),
                submission_digest=f"{index + 1:064x}",
                created_at=created_at + timedelta(seconds=index),
            )
        )
    report = None
    if status == "completed":
        frozen_results = results or _normalized_seven()
        snapshot = build_report(
            session_id=session.id,
            person=PersonDisplay(
                id=person.id,
                name=snapshot_name or person.full_name,
                nickname=person.nickname,
            ),
            completed_at=terminal_at,
            rules=rules,
            results=frozen_results,
            baselines={},
        )
        report = TalentTestReport(
            id=report_id or uuid4(),
            session_id=session.id,
            report_snapshot=snapshot.model_dump(mode="json"),
            created_at=terminal_at,
        )
        db.add(report)
    db.flush()
    return session, report


def test_find_comparable_baselines_skips_newer_incompatible_history_per_test(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, actors = query_people
    current = _normalized_seven()

    old_results = _normalized_seven()
    reaction_payload = valid_payload("reactiontime")
    reaction_payload["raw"]["samples_ms"] = [225] * 5
    reaction_payload["duration_ms"] = 1125
    old_reaction_index = next(
        index for index, item in enumerate(old_results) if item.test_id == "reactiontime"
    )
    old_results[old_reaction_index] = normalize_result(
        RAW_ADAPTER.validate_python(reaction_payload),
        get_rules("talent-training-v1"),
    )
    _, old_report = _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=3),
        status="completed",
        results=old_results,
        report_id=UUID("10000000-0000-4000-8000-000000000001"),
    )

    recent_results = _normalized_seven()
    next(
        item for item in recent_results if item.test_id == "reactiontime"
    ).device.viewport_width = 1024
    _, recent_report = _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=2),
        status="completed",
        results=recent_results,
        report_id=UUID("20000000-0000-4000-8000-000000000002"),
    )

    incompatible_results = _normalized_seven()
    incompatible_reaction = next(
        item for item in incompatible_results if item.test_id == "reactiontime"
    )
    incompatible_reaction.background_interrupted = True
    incompatible_reaction.quality_flags = ["background_interrupted"]
    aim = next(item for item in incompatible_results if item.test_id == "aim")
    assert aim.device.geometry is not None
    aim.device.geometry.width = 1024
    chimp_index = next(
        index for index, item in enumerate(incompatible_results) if item.test_id == "chimp"
    )
    incompatible_results[chimp_index] = incompatible_results[chimp_index].model_copy(
        update={"locale": "en-US"}
    )
    _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=1, hours=12),
        status="completed",
        results=incompatible_results,
    )

    warning_results = _normalized_seven()
    for item in warning_results:
        item.background_interrupted = True
        item.quality_flags = ["background_interrupted"]
    _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=1),
        status="completed",
        results=warning_results,
    )
    db.commit()

    assert old_report is not None
    assert recent_report is not None

    baselines = find_comparable_baselines(
        db,
        person_id=people["student"].id,
        results=current,
        before=NOW,
    )

    assert baselines["reactiontime"][0] == old_report.id
    assert baselines["reactiontime"][1].raw_value == 225
    assert baselines["aim"][0] == recent_report.id
    assert baselines["chimp"][0] == recent_report.id


def test_dynamic_baseline_query_skips_newer_different_level_and_finds_older_match(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, actors = query_people
    current = _normalized_eight()

    old_results = _normalized_eight()
    old_dynamic = next(item for item in old_results if item.test_id == "dynamic-vision")
    old_dynamic.raw_value = 3
    old_dynamic.score = 36
    _, old_report = _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=3),
        status="completed",
        results=old_results,
        catalog_version="talent-training-v2",
    )

    recent_results = _normalized_eight()
    recent_dynamic = next(item for item in recent_results if item.test_id == "dynamic-vision")
    recent_dynamic.raw.selected_level = 2
    _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=2),
        status="completed",
        results=recent_results,
        catalog_version="talent-training-v2",
    )

    wrong_font_results = _normalized_eight()
    next(
        item for item in wrong_font_results if item.test_id == "dynamic-vision"
    ).raw.font_size_px = 40
    _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=1),
        status="completed",
        results=wrong_font_results,
        catalog_version="talent-training-v2",
    )
    db.commit()

    statements, listener = _capture_statements(db.connection())
    try:
        baselines = find_comparable_baselines(
            db,
            person_id=people["student"].id,
            results=current,
            before=NOW,
        )
    finally:
        event.remove(db.connection(), "before_cursor_execute", listener)

    assert old_report is not None
    assert baselines["dynamic-vision"][0] == old_report.id
    assert baselines["dynamic-vision"][1].raw_value == 3
    dynamic_sql = next(
        statement
        for statement, parameters in statements
        if "FROM talent_test_results" in statement and "dynamic-vision" in str(parameters)
    )
    assert dynamic_sql.count("talent_test_results.raw_result") == 2


def test_noncomparable_current_inputs_do_not_query_or_produce_baselines(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, actors = query_people
    _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=1),
        status="completed",
        results=_normalized_seven(),
    )
    db.commit()
    current = _normalized_seven()
    next(item for item in current if item.test_id == "reactiontime").device.input_kind = "mixed"
    next(item for item in current if item.test_id == "aim").quality_flags = ["timing_anomaly"]

    baselines = find_comparable_baselines(
        db,
        person_id=people["student"].id,
        results=current,
        before=NOW,
    )

    assert "reactiontime" not in baselines
    assert "aim" not in baselines
    assert set(baselines) == set(LEGACY_TEST_IDS) - {"reactiontime", "aim"}


def test_baseline_lookup_emits_at_most_seven_bounded_sql_statements(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, _ = query_people
    connection = db.connection()
    statements, listener = _capture_statements(connection)
    try:
        find_comparable_baselines(
            db,
            person_id=people["student"].id,
            results=_normalized_seven(),
            before=NOW,
        )
    finally:
        event.remove(connection, "before_cursor_execute", listener)

    baseline_sql = [
        statement for statement, _ in statements if "FROM talent_test_results" in statement
    ]
    assert len(baseline_sql) == len(LEGACY_TEST_IDS) == 7
    for statement in baseline_sql:
        normalized = " ".join(statement.split())
        assert "LIMIT" in normalized
        assert "talent_test_sessions.completed_at" in normalized
        assert "talent_test_results.quality_flags" in normalized
        assert "talent_test_results.device_snapshot" in normalized


def test_sessions_are_self_scoped_counted_before_paging_and_stably_sorted(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, actors = query_people
    ids = [
        UUID("10000000-0000-4000-8000-000000000001"),
        UUID("20000000-0000-4000-8000-000000000002"),
        UUID("30000000-0000-4000-8000-000000000003"),
    ]
    for offset, session_id in enumerate(ids):
        _insert_session(
            db,
            person=people["student"],
            actor=actors["student"],
            created_at=NOW - timedelta(days=offset),
            status="in_progress" if offset == 0 else "abandoned",
            session_id=session_id,
        )
    _insert_session(
        db,
        person=people["other"],
        actor=actors["other"],
        created_at=NOW + timedelta(days=1),
        status="in_progress",
    )
    db.commit()

    page = TalentQueries(db, actors["student"], NOW).sessions(page=1, page_size=2)

    assert page.total == 3
    assert [item.id for item in page.items] == ids[:2]
    assert all(item.saved_count == 0 for item in page.items)
    assert all(item.required_count == 7 for item in page.items)
    assert all(item.report_id is None for item in page.items)


def test_session_details_enforce_partial_privacy_and_keep_seven_current_results(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, actors = query_people
    partial, _ = _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW,
        status="in_progress",
        results=_normalized_seven()[:3],
    )
    completed, _ = _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=1),
        status="completed",
        results=_normalized_seven(),
    )
    db.commit()

    owner_view = TalentQueries(db, actors["student"], NOW).session(partial.id)
    assert len(owner_view.results) == 3
    assert owner_view.report_id is None
    with pytest.raises(TalentNotFound):
        TalentQueries(db, actors["guardian"], NOW).session(partial.id)
    guardian_view = TalentQueries(db, actors["guardian"], NOW).session(completed.id)
    assert len(guardian_view.results) == 7
    assert guardian_view.report_id is not None


def test_subjects_and_reports_filter_in_sql_and_report_uses_frozen_identity(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, actors = query_people
    _, visible_report = _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=1),
        status="completed",
        results=_normalized_seven(),
        snapshot_name="冻结姓名",
    )
    _insert_session(
        db,
        person=people["other"],
        actor=actors["other"],
        created_at=NOW,
        status="completed",
        results=_normalized_seven(),
    )
    people["student"].full_name = "当前姓名"
    db.commit()

    assert visible_report is not None

    queries = TalentQueries(db, actors["guardian"], NOW)
    subjects = queries.subjects(name="当前", page=1, page_size=20)
    reports = queries.reports(person_id=None, page=1, page_size=20)
    report = queries.report(visible_report.id)

    assert subjects.total == 1
    assert subjects.items == [
        PersonDisplay(
            id=people["student"].id,
            name="当前姓名",
            nickname="student-nick",
        )
    ]
    assert reports.total == 1
    assert reports.items[0].person.name == "冻结姓名"
    assert report.person.name == "冻结姓名"
    serialized = report.model_dump_json()
    assert "identity-secret" not in serialized
    assert "email-secret" not in serialized
    with pytest.raises(TalentNotFound):
        queries.reports(person_id=people["other"].id, page=1, page_size=20)


def test_authorization_is_inside_report_count_and_page_sql_and_query_plan(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, actors = query_people
    _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW - timedelta(days=1),
        status="completed",
        results=_normalized_seven(),
    )
    _insert_session(
        db,
        person=people["other"],
        actor=actors["other"],
        created_at=NOW,
        status="completed",
        results=_normalized_seven(),
    )
    db.commit()

    connection = db.connection()
    statements, listener = _capture_statements(connection)
    try:
        page = TalentQueries(db, actors["guardian"], NOW).reports(
            person_id=None,
            page=1,
            page_size=1,
        )
    finally:
        event.remove(connection, "before_cursor_execute", listener)

    assert page.total == 1
    count_sql, count_parameters = next(
        (statement, parameters)
        for statement, parameters in statements
        if "count(*)" in statement and "talent_test_reports" in statement
    )
    page_sql, page_parameters = next(
        (statement, parameters)
        for statement, parameters in statements
        if statement.startswith("SELECT talent_test_reports.id")
    )
    for statement in (count_sql, page_sql):
        normalized = " ".join(statement.split())
        assert "talent_test_sessions.person_id IN (SELECT people.id" in normalized
        assert "user_accounts.status" in normalized
    assert "LIMIT" in page_sql

    count_plan = connection.exec_driver_sql(
        f"EXPLAIN (FORMAT JSON) {count_sql}",
        count_parameters,
    ).scalar_one()
    page_plan = connection.exec_driver_sql(
        f"EXPLAIN (FORMAT JSON) {page_sql}",
        page_parameters,
    ).scalar_one()
    count_plan_text = json.dumps(count_plan, default=str)
    page_plan_text = json.dumps(page_plan, default=str)
    assert '"Node Type": "Aggregate"' in count_plan_text
    assert '"Node Type": "Limit"' in page_plan_text
    for plan_text in (count_plan_text, page_plan_text):
        assert '"Relation Name": "people"' in plan_text
        assert '"Relation Name": "user_accounts"' in plan_text
        assert '"Relation Name": "talent_test_sessions"' in plan_text


def test_context_capabilities_come_from_live_access_and_find_active_self_session(
    db: Session,
    query_people: QueryPeople,
) -> None:
    people, actors = query_people
    active, _ = _insert_session(
        db,
        person=people["student"],
        actor=actors["student"],
        created_at=NOW,
        status="in_progress",
    )
    db.commit()

    context = TalentQueries(db, actors["student"], NOW).context()

    assert context.catalog.catalog_version == "talent-training-v2"
    assert context.can_take is True
    assert context.can_read_self is True
    assert context.can_read_reports is True
    assert context.active_session_id == active.id

    actors["student"].account.status = "disabled"
    db.commit()
    disabled = TalentQueries(db, actors["student"], NOW).context()
    assert disabled.can_take is False
    assert disabled.can_read_self is False
    assert disabled.can_read_reports is False
    assert disabled.active_session_id is None
