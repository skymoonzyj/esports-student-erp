import pytest
from pydantic import ValidationError

from app.identity.schemas import PersonUpdate, SelfProfileUpdate, StaffCreate


def test_staff_profile_patch_retains_omitted_and_explicit_clear_fields() -> None:
    assert PersonUpdate().model_dump(exclude_unset=True) == {}
    assert PersonUpdate(nickname=None, profile_notes=None).model_dump(exclude_unset=True) == {
        "nickname": None, "profile_notes": None,
    }
    assert PersonUpdate(nickname="  新昵称  ", profile_notes="  内部备注  ").model_dump(exclude_unset=True) == {
        "nickname": "新昵称", "profile_notes": "内部备注",
    }


@pytest.mark.parametrize("field,length", [("nickname", 41), ("profile_notes", 2001)])
def test_staff_profile_patch_rejects_oversized_text(field: str, length: int) -> None:
    with pytest.raises(ValidationError):
        PersonUpdate(**{field: "字" * length})


def test_self_profile_rejects_internal_notes() -> None:
    with pytest.raises(ValidationError):
        SelfProfileUpdate(nickname="自己昵称", profile_notes="内部不可见")


@pytest.mark.parametrize("staff_type", ["team_manager", "assistant_coach"])
def test_team_staff_classifications_are_valid_for_create_and_patch(staff_type: str) -> None:
    created = StaffCreate.model_validate({
        "full_name": "合成工作人员", "identity_number": "11010519491231002X",
        "sex": "female", "date_of_birth": "1949-12-31",
        "phone": "13800138000", "email": "staff@example.com", "staff_type": staff_type,
    })
    assert created.staff_type == staff_type
    assert PersonUpdate.model_validate({"staff_type": staff_type}).staff_type == staff_type


@pytest.mark.parametrize("staff_type", ["coach", "life_teacher", "customer_service", "administration", "other"])
def test_legacy_primary_staff_types_remain_readable_and_editable(staff_type: str) -> None:
    assert PersonUpdate.model_validate({"staff_type": staff_type}).staff_type == staff_type
