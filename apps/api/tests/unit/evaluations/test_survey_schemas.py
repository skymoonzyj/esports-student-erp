from uuid import UUID

import pytest
from pydantic import ValidationError

from app.evaluations.survey_schemas import (
    CampSurveyCuisineSnapshotInput,
    CampSurveyRevisionInput,
    CampSurveySubmissionInput,
    CampSurveySupplementInput,
)

SNAPSHOT_A = UUID("40000000-0000-4000-8000-000000000001")
SNAPSHOT_B = UUID("40000000-0000-4000-8000-000000000002")
CUISINE_A = UUID("50000000-0000-4000-8000-000000000001")
CUISINE_B = UUID("50000000-0000-4000-8000-000000000002")


def valid_payload() -> dict[str, object]:
    return {
        "idempotency_key": "survey-submit-2026-0001",
        "expected_snapshot_version": 1,
        "expected_food_preference_version": 0,
        "favorite_cuisines": [
            {
                "cuisine_option_id": str(CUISINE_A),
                "name": "川菜",
                "custom_name": None,
            }
        ],
        "favorite_dishes": ["  宫保鸡丁  "],
        "course_training_score": 10,
        "course_training_reason": "  训练安排紧凑  ",
        "life_service_score": 8,
        "life_service_reason": "住宿反馈及时",
        "service_management_score": 9,
        "service_management_reason": "管理清晰",
        "overall_suggestion": "希望增加复盘时间",
        "staff_ratings": [
            {
                "staff_snapshot_id": SNAPSHOT_A,
                "score": 10,
                "reason": "认真负责",
            },
            {
                "staff_snapshot_id": SNAPSHOT_B,
                "score": 8,
                "reason": "沟通顺畅",
            },
        ],
    }


def test_submission_trims_optional_text_and_keeps_strict_scores() -> None:
    result = CampSurveySubmissionInput.model_validate(valid_payload())
    assert result.course_training_reason == "训练安排紧凑"
    assert result.overall_suggestion == "希望增加复盘时间"
    assert result.staff_ratings[0].score == 10
    assert result.expected_food_preference_version == 0
    assert result.favorite_cuisines[0].cuisine_option_id == CUISINE_A
    assert result.favorite_cuisines[0].name == "川菜"
    assert result.favorite_dishes == ("宫保鸡丁",)


def test_staff_snapshot_uuid_accepts_json_string_but_stays_a_uuid() -> None:
    payload = valid_payload()
    payload["staff_ratings"] = [
        {
            "staff_snapshot_id": str(SNAPSHOT_A),
            "score": 9,
        },
        {
            "staff_snapshot_id": str(SNAPSHOT_B),
            "score": 8,
        },
    ]
    result = CampSurveySubmissionInput.model_validate(payload)
    assert result.staff_ratings[0].staff_snapshot_id == SNAPSHOT_A


def test_cuisine_snapshots_are_closed_and_keep_uuid_name_and_custom_name() -> None:
    result = CampSurveyCuisineSnapshotInput.model_validate(
        {
            "cuisine_option_id": str(CUISINE_A),
            "name": "其他",
            "custom_name": "  川渝菜  ",
        }
    )
    assert result.cuisine_option_id == CUISINE_A
    assert result.name == "其他"
    assert result.custom_name == "川渝菜"

    with pytest.raises(ValidationError):
        CampSurveyCuisineSnapshotInput.model_validate(
            {
                "cuisine_option_id": CUISINE_A,
                "name": "川菜",
                "custom_name": None,
                "unexpected": True,
            }
        )


@pytest.mark.parametrize("field", ["favorite_cuisines", "favorite_dishes"])
@pytest.mark.parametrize("value", [[], [1, 2, 3, 4]])
def test_favorite_food_fields_require_one_to_three_items(
    field: str, value: object
) -> None:
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        CampSurveySubmissionInput.model_validate(payload)


@pytest.mark.parametrize("value", ["", "  ", ["  "]])
def test_favorite_dishes_are_trimmed_and_reject_blank_values(value: object) -> None:
    payload = valid_payload()
    payload["favorite_dishes"] = [value] if isinstance(value, str) else value
    with pytest.raises(ValidationError):
        CampSurveySubmissionInput.model_validate(payload)


@pytest.mark.parametrize("value", [-1, 1.5, True, "0"])
def test_expected_food_preference_version_is_a_strict_non_negative_integer(
    value: object,
) -> None:
    payload = valid_payload()
    payload["expected_food_preference_version"] = value
    with pytest.raises(ValidationError):
        CampSurveySubmissionInput.model_validate(payload)


def test_revision_requires_food_preference_version_and_supplement_has_no_food_fields() -> None:
    payload = valid_payload()
    revision = CampSurveyRevisionInput.model_validate(payload)
    assert revision.expected_food_preference_version == 0

    supplement = {
        "idempotency_key": "survey-supplement-2026-0001",
        "expected_snapshot_version": 1,
        "staff_ratings": [{"staff_snapshot_id": SNAPSHOT_A, "score": 8}],
        "favorite_cuisines": valid_payload()["favorite_cuisines"],
        "favorite_dishes": valid_payload()["favorite_dishes"],
        "expected_food_preference_version": 0,
    }
    with pytest.raises(ValidationError):
        CampSurveySupplementInput.model_validate(supplement)


@pytest.mark.parametrize("field", ["course_training_score", "life_service_score", "service_management_score"])
@pytest.mark.parametrize("value", [0, 11, 1.5, True, "10"])
def test_overall_scores_must_be_strict_integers_from_one_to_ten(field: str, value: object) -> None:
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        CampSurveySubmissionInput.model_validate(payload)


@pytest.mark.parametrize("value", [0, 11, 1.5, True, "10"])
def test_staff_scores_must_be_strict_integers_from_one_to_ten(value: object) -> None:
    payload = valid_payload()
    payload["staff_ratings"] = [{"staff_snapshot_id": SNAPSHOT_A, "score": value}]
    with pytest.raises(ValidationError):
        CampSurveySubmissionInput.model_validate(payload)


def test_staff_snapshot_ids_must_be_unique() -> None:
    payload = valid_payload()
    payload["staff_ratings"] = [
        {"staff_snapshot_id": SNAPSHOT_A, "score": 8},
        {"staff_snapshot_id": SNAPSHOT_A, "score": 9},
    ]
    with pytest.raises(ValidationError, match="unique"):
        CampSurveySubmissionInput.model_validate(payload)


def test_empty_optional_text_becomes_none_and_unknown_fields_are_rejected() -> None:
    payload = valid_payload()
    payload["course_training_reason"] = "  "
    payload["overall_suggestion"] = ""
    result = CampSurveySubmissionInput.model_validate(payload)
    assert result.course_training_reason is None
    assert result.overall_suggestion is None

    payload["private_note"] = "must not be accepted"
    with pytest.raises(ValidationError):
        CampSurveySubmissionInput.model_validate(payload)


def test_text_length_limits_are_enforced() -> None:
    payload = valid_payload()
    payload["overall_suggestion"] = "x" * 2001
    with pytest.raises(ValidationError):
        CampSurveySubmissionInput.model_validate(payload)


def test_all_staff_ratings_must_belong_to_server_snapshot_set() -> None:
    payload = valid_payload()
    payload["available_staff_snapshot_ids"] = [str(SNAPSHOT_A)]
    with pytest.raises(ValidationError):
        CampSurveySubmissionInput.model_validate(payload)
