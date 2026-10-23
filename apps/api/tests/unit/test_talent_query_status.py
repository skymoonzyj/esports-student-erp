from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, literal, select
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.identity.models import Person
from app.talent_tests.access import TalentAccess
from app.talent_tests.catalog import get_rules
from app.talent_tests.models import TalentTestSession
from app.talent_tests.queries import TalentQueries
from app.talent_tests.schemas import SessionSummary, SessionView

NOW = datetime(2026, 9, 22, 8, tzinfo=UTC)
PERSON_ID = UUID("20000000-0000-4000-8000-000000000001")
SESSION_ID = UUID("20000000-0000-4000-8000-000000000002")
Mapper = Literal["detail", "summary"]


@pytest.fixture(autouse=True)
def forbid_database_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("status mapper unit tests must not connect to a database")

    monkeypatch.setattr(Engine, "connect", forbidden_connect)


def map_status(mapper: Mapper, status: str) -> SessionView | SessionSummary:
    row = TalentTestSession(
        id=SESSION_ID,
        person_id=PERSON_ID,
        status=status,
        created_at=NOW,
        completed_at=NOW if status == "completed" else None,
        abandoned_at=NOW if status == "abandoned" else None,
        rules_snapshot=get_rules("talent-training-v2").model_dump(mode="json"),
        results=[],
        report=None,
    )
    if mapper == "detail":
        return TalentQueries.session_view(row)

    # Only persistence and authorization lookup are doubled; exercise the real
    # list query's row-to-DTO mapping without a database fixture or connection.
    db = Mock(spec=Session)
    db.scalar.return_value = 1
    db.scalars.return_value = [row]
    actor = Mock(spec=AuthContext)
    actor.person = Person(id=PERSON_ID)
    queries = TalentQueries(db, actor, NOW)
    queries.access = Mock(spec=TalentAccess)
    queries.access.visible_person_ids.return_value = select(literal(PERSON_ID))
    page = queries.sessions(page=1, page_size=20)
    assert page.total == 1
    assert len(page.items) == 1
    return page.items[0]


@pytest.mark.parametrize("mapper", ["detail", "summary"])
@pytest.mark.parametrize("status", ["in_progress", "completed", "abandoned"])
def test_status_mappers_preserve_each_valid_state(mapper: Mapper, status: str) -> None:
    view = map_status(mapper, status)

    assert view.id == SESSION_ID
    assert view.status == status
    assert view.model_dump(mode="json")["status"] == status
    assert view.created_at == NOW
    assert view.completed_at == (NOW if status == "completed" else None)
    assert view.abandoned_at == (NOW if status == "abandoned" else None)


@pytest.mark.parametrize("mapper", ["detail", "summary"])
def test_status_mappers_reject_unknown_state(mapper: Mapper) -> None:
    with pytest.raises(ValidationError) as error:
        map_status(mapper, "unknown")

    assert error.value.errors()[0]["type"] == "literal_error"
