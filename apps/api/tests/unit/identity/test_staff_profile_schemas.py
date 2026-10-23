import pytest
from pydantic import ValidationError

from app.identity.schemas import PersonUpdate, SelfProfileUpdate


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
