from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.evaluations.errors import CampSurveyResourceNotFound
from app.evaluations.survey_access import CampSurveyAccessService

ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000001")
STUDENT_ID = UUID("20000000-0000-4000-8000-000000000001")
CYCLE_ID = UUID("30000000-0000-4000-8000-000000000001")
GUARDIAN_ID = UUID("40000000-0000-4000-8000-000000000001")


class ScalarDB:
    def __init__(self, *values: object) -> None:
        self.values = list(values)
        self.calls = 0

    def scalar(self, _statement: object) -> object:
        self.calls += 1
        return self.values.pop(0)


def _account(*, person_id: UUID = STUDENT_ID, status: str = "active") -> SimpleNamespace:
    return SimpleNamespace(id=ACCOUNT_ID, person_id=person_id, status=status)


def _cycle() -> SimpleNamespace:
    return SimpleNamespace(id=CYCLE_ID, student_person_id=STUDENT_ID)


def test_access_service_has_private_resolution_contract() -> None:
    service = CampSurveyAccessService(SimpleNamespace())  # type: ignore[arg-type]
    assert hasattr(service, "resolve_respondent")
    assert hasattr(service, "can_respond")
    assert date(2026, 1, 1) < datetime(2026, 1, 2, tzinfo=UTC).date()
    assert UUID("40000000-0000-4000-8000-000000000001")


def test_student_must_have_active_profile_to_respond() -> None:
    db = ScalarDB(_account(), _cycle(), None)
    service = CampSurveyAccessService(db)  # type: ignore[arg-type]

    with pytest.raises(CampSurveyResourceNotFound):
        service.resolve_respondent(
            cycle_id=CYCLE_ID,
            account_id=ACCOUNT_ID,
            occurred_at=datetime(2026, 8, 13, 8, tzinfo=UTC),
        )


def test_guardian_relationship_uses_shanghai_business_date_and_inclusive_end() -> None:
    relationship = SimpleNamespace(
        id=UUID("50000000-0000-4000-8000-000000000001"),
        guardian_person_id=GUARDIAN_ID,
        student_person_id=STUDENT_ID,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 8, 14),
        valid_until=date(2026, 8, 14),
        ended_at=None,
    )
    # 2026-08-13 16:30 UTC is 2026-08-14 00:30 in Shanghai.
    db = ScalarDB(_account(person_id=GUARDIAN_ID), _cycle(), UUID("60000000-0000-4000-8000-000000000001"), relationship)
    service = CampSurveyAccessService(db)  # type: ignore[arg-type]

    resolved = service.resolve_respondent(
        cycle_id=CYCLE_ID,
        account_id=ACCOUNT_ID,
        occurred_at=datetime(2026, 8, 13, 16, 30, tzinfo=UTC),
    )

    assert resolved.respondent_kind == "guardian"
    assert resolved.guardian_relationship_id == relationship.id


def test_guardian_relationship_ended_at_revokes_at_exact_instant() -> None:
    occurred_at = datetime(2026, 8, 13, 8, tzinfo=UTC)
    relationship = SimpleNamespace(
        id=UUID("50000000-0000-4000-8000-000000000002"),
        guardian_person_id=GUARDIAN_ID,
        student_person_id=STUDENT_ID,
        relationship="father",
        is_primary=False,
        valid_from=date(2026, 8, 1),
        valid_until=None,
        ended_at=occurred_at,
    )
    db = ScalarDB(
        _account(person_id=GUARDIAN_ID),
        _cycle(),
        UUID("60000000-0000-4000-8000-000000000002"),
        relationship,
    )
    service = CampSurveyAccessService(db)  # type: ignore[arg-type]

    with pytest.raises(CampSurveyResourceNotFound):
        service.resolve_respondent(
            cycle_id=CYCLE_ID,
            account_id=ACCOUNT_ID,
            occurred_at=occurred_at,
        )


def test_guardian_without_current_role_is_private_not_found() -> None:
    db = ScalarDB(
        _account(person_id=GUARDIAN_ID),
        _cycle(),
        None,
    )
    service = CampSurveyAccessService(db)  # type: ignore[arg-type]

    with pytest.raises(CampSurveyResourceNotFound):
        service.resolve_respondent(
            cycle_id=CYCLE_ID,
            account_id=ACCOUNT_ID,
            occurred_at=datetime(2026, 8, 13, 8, tzinfo=UTC),
        )


def test_relationship_for_another_student_does_not_authorize_this_cycle() -> None:
    other_student_id = UUID("20000000-0000-4000-8000-000000000002")
    relationship = SimpleNamespace(
        id=UUID("50000000-0000-4000-8000-000000000003"),
        guardian_person_id=GUARDIAN_ID,
        student_person_id=other_student_id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 8, 1),
        valid_until=None,
        ended_at=None,
    )
    db = ScalarDB(
        _account(person_id=GUARDIAN_ID),
        _cycle(),
        UUID("60000000-0000-4000-8000-000000000003"),
        None,
    )
    service = CampSurveyAccessService(db)  # type: ignore[arg-type]

    with pytest.raises(CampSurveyResourceNotFound):
        service.resolve_respondent(
            cycle_id=CYCLE_ID,
            account_id=ACCOUNT_ID,
            occurred_at=datetime(2026, 8, 13, 8, tzinfo=UTC),
        )

    assert relationship.student_person_id != _cycle().student_person_id


def test_disabled_account_is_private_not_found_without_writes() -> None:
    db = ScalarDB(None, _cycle())
    service = CampSurveyAccessService(db)  # type: ignore[arg-type]

    with pytest.raises(CampSurveyResourceNotFound):
        service.resolve_respondent(
            cycle_id=CYCLE_ID,
            account_id=ACCOUNT_ID,
            occurred_at=datetime(2026, 8, 13, 8, tzinfo=UTC),
        )

    assert db.calls == 2
