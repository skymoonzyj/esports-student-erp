from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.evaluations.survey_query_service import CampSurveyQueryService


class _Statement:
    """Small statement double that preserves the query service call shape."""

    def __init__(self, source: str) -> None:
        self.source = source

    def order_by(self, *_columns: object) -> _Statement:
        return self


class _Result:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return iter(self._rows)


class _Database:
    def __init__(
        self,
        *,
        main_rows: list[tuple[object, ...]],
        supplement_rows: list[tuple[object, ...]],
    ) -> None:
        self.main_rows = main_rows
        self.supplement_rows = supplement_rows

    def execute(self, statement: object) -> _Result:
        if isinstance(statement, _Statement):
            if statement.source == "main":
                return _Result(self.main_rows)
            if statement.source == "supplement":
                return _Result(self.supplement_rows)
        # The service performs a separate class -> project lookup after the
        # two feedback queries.  No project context is needed for this unit
        # regression, so return an empty result for that statement.
        return _Result([])


def _main_row(
    *,
    cycle_id: UUID,
    submission_id: UUID,
    staff_person_id: UUID,
    snapshot_id: UUID,
    score: int,
) -> tuple[object, ...]:
    cycle = SimpleNamespace(id=cycle_id, cohort_id=uuid4(), class_id=uuid4())
    submission = SimpleNamespace(id=submission_id)
    version = SimpleNamespace(id=uuid4(), version=1)
    rating = SimpleNamespace(score=score, reason=None, suggestion=None)
    snapshot = SimpleNamespace(
        id=snapshot_id,
        staff_person_id=staff_person_id,
        role_category="teaching",
        role_name_snapshot="主教练",
        staff_name_snapshot="测试工作人员",
        role_key_snapshot="primary_coach",
    )
    return cycle, submission, version, rating, snapshot


def _supplement_row(
    *,
    cycle_id: UUID,
    submission_id: UUID,
    staff_person_id: UUID,
    snapshot_id: UUID,
    score: int,
) -> tuple[object, ...]:
    cycle, submission, _version, _rating, snapshot = _main_row(
        cycle_id=cycle_id,
        submission_id=submission_id,
        staff_person_id=staff_person_id,
        snapshot_id=snapshot_id,
        score=score,
    )
    supplement = SimpleNamespace(id=uuid4())
    rating = SimpleNamespace(score=score, reason=None, suggestion=None)
    latest_version = SimpleNamespace(id=uuid4(), version=1)
    return cycle, submission, supplement, rating, snapshot, latest_version


def test_main_rating_wins_and_supplement_adds_only_new_staff_persons() -> None:
    """A stable person must contribute once, with the main rating preferred."""

    cycle_id = uuid4()
    submission_id = uuid4()
    existing_person_id = uuid4()
    new_person_id = uuid4()
    service = CampSurveyQueryService(
        _Database(
            main_rows=[
                _main_row(
                    cycle_id=cycle_id,
                    submission_id=submission_id,
                    staff_person_id=existing_person_id,
                    snapshot_id=uuid4(),
                    score=9,
                )
            ],
            supplement_rows=[
                _supplement_row(
                    cycle_id=cycle_id,
                    submission_id=submission_id,
                    staff_person_id=existing_person_id,
                    snapshot_id=uuid4(),
                    score=2,
                ),
                _supplement_row(
                    cycle_id=cycle_id,
                    submission_id=submission_id,
                    staff_person_id=new_person_id,
                    snapshot_id=uuid4(),
                    score=7,
                ),
            ],
        )
    )
    service._main_statement = lambda **_: _Statement("main")  # type: ignore[method-assign]
    service._supplement_statement = lambda **_: _Statement(  # type: ignore[method-assign]
        "supplement"
    )

    rows = service._feedback_rows()

    assert len(rows) == 2
    existing_rows = [
        row for row in rows if row.snapshot.staff_person_id == existing_person_id
    ]
    new_rows = [row for row in rows if row.snapshot.staff_person_id == new_person_id]
    assert len(existing_rows) == 1
    assert existing_rows[0].rating.score == 9
    assert existing_rows[0].is_supplement is False
    assert len(new_rows) == 1
    assert new_rows[0].rating.score == 7
    assert new_rows[0].is_supplement is True


def test_deduplication_is_scoped_to_a_submission_and_cycle() -> None:
    """Separate respondents/cycles still contribute independent feedback."""

    staff_person_id = uuid4()
    first_cycle_id = uuid4()
    second_cycle_id = uuid4()
    first_submission_id = uuid4()
    second_submission_id = uuid4()
    service = CampSurveyQueryService(
        _Database(
            main_rows=[
                _main_row(
                    cycle_id=first_cycle_id,
                    submission_id=first_submission_id,
                    staff_person_id=staff_person_id,
                    snapshot_id=uuid4(),
                    score=8,
                ),
                _main_row(
                    cycle_id=second_cycle_id,
                    submission_id=second_submission_id,
                    staff_person_id=staff_person_id,
                    snapshot_id=uuid4(),
                    score=6,
                ),
            ],
            supplement_rows=[
                _supplement_row(
                    cycle_id=first_cycle_id,
                    submission_id=first_submission_id,
                    staff_person_id=staff_person_id,
                    snapshot_id=uuid4(),
                    score=1,
                ),
                _supplement_row(
                    cycle_id=second_cycle_id,
                    submission_id=second_submission_id,
                    staff_person_id=staff_person_id,
                    snapshot_id=uuid4(),
                    score=3,
                ),
            ],
        )
    )
    service._main_statement = lambda **_: _Statement("main")  # type: ignore[method-assign]
    service._supplement_statement = lambda **_: _Statement(  # type: ignore[method-assign]
        "supplement"
    )

    rows = service._feedback_rows()

    assert len(rows) == 2
    assert {row.rating.score for row in rows} == {6, 8}
    assert all(row.is_supplement is False for row in rows)
