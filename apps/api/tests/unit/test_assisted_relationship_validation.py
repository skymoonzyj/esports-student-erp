import pytest
from pydantic import ValidationError

from app.personnel_intake.schemas import AssistedGuardianInput


def guardian(relationship):
    return AssistedGuardianInput(
        full_name="测试监护人",
        identity_number="110105199001021238",
        phone="13800000000",
        relationship=relationship,
        sex="male",
        date_of_birth="1990-01-02",
    )


@pytest.mark.parametrize("relationship", ["母", " 母 ", "  ", "关系" * 20 + "长"])
def test_relationship_rejected_before_intake_when_trimmed_length_outside_canonical_rule(
    relationship,
):
    with pytest.raises(ValidationError) as error:
        guardian(relationship)
    assert error.value.errors()[0]["loc"] == ("relationship",)


@pytest.mark.parametrize("relationship,want", [(" 母亲 ", "母亲"), ("关" * 40, "关" * 40)])
def test_relationship_accepts_trimmed_canonical_boundaries(relationship, want):
    assert guardian(relationship).relationship == want
