from uuid import UUID

import pytest
from pydantic import ValidationError

from app.housing.reporting_projection import classify_person_type
from app.rbac.seed import ROLE_PERMISSION_GRANTS, ROLES
from app.registration.schemas import PurposeDetails


def test_assistant_seed_grants_only_team_personnel_reading() -> None:
    assert "assistant_coach" in ROLES
    assert ROLES["assistant_coach"].id == UUID("10000000-0000-4000-8000-000000000015")
    assert {(permission, scope) for role, permission, scope in ROLE_PERMISSION_GRANTS
            if role == "assistant_coach"} == {
        ("team_memberships.read", "assigned_team"),
        ("personnel_intake.housing.read", "global"),
    }
    assert ("coach", "team_memberships.read", "assigned_team") in ROLE_PERMISSION_GRANTS


@pytest.mark.parametrize("staff_type", ["team_manager", "assistant_coach"])
def test_new_internal_staff_types_do_not_expand_self_registration(staff_type: str) -> None:
    with pytest.raises(ValidationError):
        PurposeDetails(requested_staff_type=staff_type, staff_application_reason="申请岗位")


@pytest.mark.parametrize("staff_type", ["team_manager", "assistant_coach"])
def test_new_staff_types_remain_recognized_for_housing_reports(staff_type: str) -> None:
    assert classify_person_type(
        is_student=False, staff_type=staff_type, is_visitor=False
    ) == staff_type
