import pytest
from pydantic import ValidationError

from app.personnel_intake import schemas


def test_managed_people_filters_reject_unbounded_or_unknown_inputs():
    model = getattr(schemas, "ManagedPeopleFilters", None)
    assert model is not None, "managed-person filters are missing"
    assert model().page_size == 20
    assert model(query="  小李  ").query == "小李"
    for invalid in (
        {"page_size": 0},
        {"page_size": 101},
        {"query": "x" * 81},
        {"cursor": "x" * 1025},
        {"person_id": "private"},
        {"housing_status": "unknown"},
    ):
        with pytest.raises(ValidationError):
            model(**invalid)
