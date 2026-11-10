from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, select

from app.housing.errors import HousingServiceError
from app.housing.models import HousingRoom, HousingStay
from app.portal.departure_access import DepartureAccess
from app.portal.departure_query import DepartureQuery
from tests.departure_helpers import NOW, actor, deny, enrollment, housing, person


def read(db, context, *, window="three_days", page=1, page_size=20, now=NOW):
    return DepartureQuery(db, DepartureAccess(db, context, now)).read_page(
        now=now,
        window=window,
        page=page,
        page_size=page_size,
    )


def test_merges_by_person_not_name(db):
    context = actor(db)
    first, second = person(db, "同名"), person(db, "同名")
    housing(db, context, first)
    enrollment(db, context, first)
    enrollment(db, context, second)
    result = read(db, context)
    assert result.total == result.today_count == result.three_day_count == 2
    assert {row.person_id for row in result.items} == {first.id, second.id}
    assert [len(row.events) for row in result.items].count(2) == 1


def test_independent_end_overrides_cohort(db):
    context = actor(db)
    subject = person(db)
    enrollment(db, context, subject, custom_end=NOW + timedelta(days=7))
    assert read(db, context).total == 0


def test_completed_custom_end_is_not_lost(db):
    context = actor(db)
    subject = person(db)
    source = enrollment(
        db,
        context,
        subject,
        end=NOW - timedelta(days=3),
        custom_end=datetime(2026, 10, 25, 16, tzinfo=UTC),
        status="completed",
        cohort_status="completed",
    )
    result = read(db, context)
    assert result.total == 1
    fact = result.items[0].events[0]
    assert (fact.source_id, fact.date, fact.enrollment_status) == (
        source.id,
        date(2026, 10, 25),
        "completed",
    )


def test_archived_cohort_is_read_only_not_cancelled(db):
    context = actor(db)
    subject = person(db)
    enrollment(
        db,
        context,
        subject,
        end=NOW - timedelta(days=3),
        custom_end=datetime(2026, 10, 25, 16, tzinfo=UTC),
        status="completed",
        cohort_status="archived",
    )
    assert read(db, context).total == 1


def test_long_term_and_nonresident_enrollment(db):
    context = actor(db)
    resident, nonresident = person(db), person(db)
    housing(db, context, resident, end=None)
    enrollment(db, context, resident)
    enrollment(db, context, nonresident)
    result = read(db, context)
    assert result.total == 2
    assert all(fact.kind == "enrollment_end" for row in result.items for fact in row.events)


def test_unactivated_confirmed_person_is_included(db):
    context = actor(db)
    subject = person(db)
    subject.document_type = "passport"
    subject.passport_number_digest = "departure-pending-passport"
    subject.intake_mode = "pending"
    subject.sex = subject.date_of_birth = None
    housing(db, context, subject)
    assert read(db, context).items[0].person_id == subject.id


@pytest.mark.parametrize("status", ["planned", "checked_out", "cancelled"])
def test_inactive_stays_are_excluded(db, status):
    context = actor(db)
    housing(db, context, person(db), status=status)
    assert read(db, context).total == 0


def test_voided_stays_and_cancelled_enrollments_are_excluded(db):
    context = actor(db)
    source = housing(db, context, person(db), status="checked_out")
    source.voided_at = NOW
    source.voided_by_user_id = context.account.id
    source.void_reason_ciphertext = "test-only"
    enrollment(db, context, person(db), status="cancelled")
    db.flush()
    assert read(db, context).total == 0


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (datetime(2026, 10, 24, 16, tzinfo=UTC), date(2026, 10, 24)),
        (datetime(2026, 10, 24, 16, 0, 0, 1000, tzinfo=UTC), date(2026, 10, 24)),
        (datetime(2026, 10, 24, 16, 0, 1, tzinfo=UTC), date(2026, 10, 25)),
        (datetime(2026, 10, 23, 16, tzinfo=UTC), None),
        (datetime(2026, 10, 26, 16, tzinfo=UTC), date(2026, 10, 26)),
        (datetime(2026, 10, 27, 16, tzinfo=UTC), None),
    ],
)
def test_sql_exclusive_end_date_matches_display(db, timestamp, expected):
    context = actor(db)
    enrollment(db, context, person(db), end=timestamp)
    result = read(db, context)
    assert [fact.date for row in result.items for fact in row.events] == (
        [] if expected is None else [expected]
    )


def test_merge_before_pagination_and_only_selected_window_events(db):
    context = actor(db)
    for number in range(45):
        subject = person(db, f"学员{number:02}")
        housing(db, context, subject, end=NOW + timedelta(days=number % 3))
        if number % 2 == 0:
            enrollment(db, context, subject)
    pages = [read(db, context, page=number) for number in (1, 2, 3)]
    assert [len(result.items) for result in pages] == [20, 20, 5]
    assert all(result.total == result.three_day_count == 45 for result in pages)
    assert len({row.person_id for result in pages for row in result.items}) == 45
    today = read(db, context, window="today", page_size=50)
    assert today.total == today.today_count
    assert all(fact.date == date(2026, 10, 24) for row in today.items for fact in row.events)
    assert today.three_day_count == 45


def test_hidden_source_does_not_change_count_order_or_labels(db):
    context = actor(db)
    first, second = person(db, "乙"), person(db, "甲")
    housing(db, context, first, end=NOW + timedelta(days=1))
    housing(db, context, second)
    deny(db, context, "enrollments.read", "global", None)
    before = read(db, context).model_dump()
    enrollment(db, context, first)
    enrollment(db, context, person(db, "隐藏人员"))
    assert read(db, context).model_dump() == before


def test_duplicate_effective_current_stays_fails_closed_outside_date_window(db):
    context = actor(db)
    subject = person(db)
    housing(db, context, subject)
    housing(db, context, subject, start=NOW + timedelta(days=1), end=NOW + timedelta(days=7))
    with pytest.raises(HousingServiceError):
        read(db, context)


def test_hidden_duplicate_current_stay_cannot_change_success(db):
    context = actor(db)
    subject = person(db)
    visible = housing(db, context, subject)
    hidden = housing(
        db, context, subject, start=NOW + timedelta(days=1), end=NOW + timedelta(days=7)
    )
    deny(db, context, "housing.read", "building", db.get(HousingRoom, hidden.room_id).building_id)
    result = read(db, context)
    assert result.total == 1
    assert result.items[0].events[0].source_id == visible.id


def test_today_request_checks_duplicates_in_public_three_day_count(db):
    context = actor(db)
    subject = person(db)
    housing(db, context, subject, end=NOW + timedelta(days=1))
    housing(db, context, subject, start=NOW + timedelta(days=2), end=NOW + timedelta(days=7))
    with pytest.raises(HousingServiceError):
        read(db, context, window="today")


def test_duplicate_stays_without_any_three_day_fact_do_not_change_response(db):
    context = actor(db)
    subject = person(db)
    housing(db, context, subject, end=NOW + timedelta(days=4))
    housing(db, context, subject, start=NOW + timedelta(days=5), end=NOW + timedelta(days=7))
    assert read(db, context).total == 0


def test_teaching_only_candidate_ignores_unrelated_outside_window_housing_conflict(db):
    context = actor(db)
    subject = person(db)
    housing(db, context, subject, end=NOW + timedelta(days=4))
    housing(db, context, subject, start=NOW + timedelta(days=5), end=NOW + timedelta(days=7))
    enrollment(db, context, subject)
    result = read(db, context)
    assert result.total == 1
    assert [fact.kind for fact in result.items[0].events] == ["enrollment_end"]


def test_query_count_is_bounded_and_business_records_unchanged(db):
    context = actor(db)
    for _ in range(10):
        housing(db, context, person(db))
    before = list(db.execute(select(HousingStay.__table__)).all())
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        result = read(db, context)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert result.total == 10
    assert len(statements) <= 35
    assert list(db.execute(select(HousingStay.__table__)).all()) == before


@pytest.mark.parametrize(
    ("now", "end", "want"),
    [
        (
            datetime(2026, 12, 31, 8, tzinfo=UTC),
            datetime(2026, 12, 31, 16, tzinfo=UTC),
            date(2026, 12, 31),
        ),
        (
            datetime(2024, 2, 29, 8, tzinfo=UTC),
            datetime(2024, 2, 29, 16, tzinfo=UTC),
            date(2024, 2, 29),
        ),
        (
            datetime(2026, 4, 30, 8, tzinfo=UTC),
            datetime(2026, 4, 30, 16, tzinfo=UTC),
            date(2026, 4, 30),
        ),
    ],
)
def test_sql_exclusive_end_rolls_back_calendar_boundaries(db, now, end, want):
    context = actor(db)
    enrollment(db, context, person(db), end=end)
    result = read(db, context, now=now)
    assert result.items[0].events[0].date == want


def test_housing_midnight_is_not_exclusive_end(db):
    context = actor(db)
    source = housing(db, context, person(db), end=datetime(2026, 10, 24, 16, tzinfo=UTC))
    result = read(db, context)
    assert result.today_count == 0
    assert result.items[0].events[0].source_id == source.id
    assert result.items[0].events[0].date == date(2026, 10, 25)


def test_no_scope_is_unavailable_not_permission_error(db):
    context = actor(db, "registered_user")
    result = read(db, context)
    assert not result.available
    assert result.total == result.today_count == result.three_day_count == 0
    assert result.items == []


def test_performance_sql_count_and_explain_for_1_100_1000_candidates(db, capsys, request):
    context = actor(db)
    created = 0
    counts = []
    with capsys.disabled():
        print(f"departure owned selected_tests={len(request.session.items)}")
    for size in (1, 100, 1000):
        while created < size:
            housing(db, context, person(db, f"性能虚拟人员{created:04}"))
            created += 1
        statements = []

        def capture(_conn, _cursor, statement, parameters, _ctx, _many, statements=statements):
            statements.append((statement, parameters))

        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            result = read(db, context)
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)
        assert result.total == size
        counts.append(len(statements))
        sql, parameters = next(row for row in statements if "departure_facts" in row[0])
        plan = (
            db.connection()
            .exec_driver_sql("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, parameters)
            .scalar_one()[0]
        )
        with capsys.disabled():
            print(
                f"departure candidates={size} statements={len(statements)} explain_ms={plan['Execution Time']} plan_rows={plan['Plan']['Actual Rows']}"
            )
    assert max(counts) - min(counts) <= 1
    assert max(counts) <= 35
