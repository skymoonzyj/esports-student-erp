from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.team_management import schemas


def test_accountless_member_does_not_weaken_manager_assignment_contract():
    values = {
        "account_id": None,
        "person_id": uuid4(),
        "full_name": "独立队员",
        "nickname": None,
        "age": 20,
        "is_minor": False,
    }
    assert schemas.TeamCandidate(**values).account_id is None
    assert hasattr(schemas, "ManagerCandidate"), "manager DTO must encode a required account"
    with pytest.raises(ValidationError):
        schemas.ManagerCandidate(**values)
    with pytest.raises(ValidationError):
        schemas.AssignManagerInput(account_id=None, idempotency_key=uuid4())
