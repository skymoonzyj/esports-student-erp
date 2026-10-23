from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.evaluations.errors import CampSurveyValidationError
from app.evaluations.survey_schemas import CampSurveySubmissionInput
from app.evaluations.survey_service import (
    _audit_metadata,
    _canonical_food_payload,
    _canonical_submission_payload,
    _mutation_request_id,
    _validate_staff_snapshot_ids,
)
from app.food_preferences.errors import FoodPreferenceValidationFailed
from app.food_preferences.preference_service import (
    FoodPreferenceCuisineProjection,
    FoodPreferenceProjection,
    FoodPreferenceService,
    ReplaceFoodPreferenceCommand,
)


def _input() -> CampSurveySubmissionInput:
    return CampSurveySubmissionInput.model_validate(
        {
            "idempotency_key": "survey-contract-0001",
            "expected_snapshot_version": 1,
            "expected_food_preference_version": 0,
            "favorite_cuisines": [
                {
                    "cuisine_option_id": UUID("50000000-0000-4000-8000-000000000001"),
                    "name": "川菜",
                }
            ],
            "favorite_dishes": ["宫保鸡丁"],
            "course_training_score": 8,
            "life_service_score": 8,
            "service_management_score": 8,
            "staff_ratings": [
                {
                    "staff_snapshot_id": UUID("40000000-0000-4000-8000-000000000001"),
                    "score": 9,
                    "suggestion": "安排更多复盘",
                }
            ],
        }
    )


def test_canonical_payload_contains_content_but_no_server_identity() -> None:
    payload = _canonical_submission_payload(_input())
    assert payload["course_training_score"] == 8
    assert "respondent_person_id" not in payload
    assert "student_person_id" not in payload
    assert payload["staff_ratings"] == [
        {
            "staff_snapshot_id": "40000000-0000-4000-8000-000000000001",
            "score": 9,
            "reason": None,
            "suggestion": "安排更多复盘",
        }
    ]


def test_submission_idempotency_payload_is_scoped_to_cycle() -> None:
    first = _canonical_submission_payload(
        _input(),
        cycle_id=UUID("60000000-0000-4000-8000-000000000001"),
    )
    second = _canonical_submission_payload(
        _input(),
        cycle_id=UUID("60000000-0000-4000-8000-000000000002"),
    )
    assert first["cycle_id"] != second["cycle_id"]


def test_staff_snapshot_ids_must_exactly_cover_snapshot() -> None:
    expected = {
        UUID("40000000-0000-4000-8000-000000000001"),
        UUID("40000000-0000-4000-8000-000000000002"),
    }
    with pytest.raises(ValueError, match="exactly"):
        _validate_staff_snapshot_ids(
            _input().staff_ratings,
            expected,
        )


def test_survey_audit_metadata_contains_only_safe_aggregate_fields() -> None:
    metadata = _audit_metadata(version=2, item_count=3)

    assert metadata == {"new_version": 2, "item_count": 3}
    assert not {"score", "reason", "suggestion", "respondent_person_id"} & metadata.keys()


def test_food_projection_is_the_only_source_for_history_snapshot() -> None:
    projection = FoodPreferenceProjection(
        person_id=UUID("30000000-0000-4000-8000-000000000001"),
        version=1,
        cuisines=(
            FoodPreferenceCuisineProjection(
                cuisine_option_id=UUID("50000000-0000-4000-8000-000000000001"),
                name="官方川菜",
                custom_name=None,
            ),
        ),
        dishes=("宫保鸡丁",),
        updated_at=None,
    )

    assert _canonical_food_payload(projection) == (
        [
            {
                "cuisine_option_id": "50000000-0000-4000-8000-000000000001",
                "name": "官方川菜",
                "custom_name": None,
            }
        ],
        ["宫保鸡丁"],
    )


def test_sync_food_preference_uses_replace_projection_without_legacy_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = FoodPreferenceProjection(
        person_id=UUID("30000000-0000-4000-8000-000000000001"),
        version=1,
        cuisines=(),
        dishes=("宫保鸡丁",),
        updated_at=None,
    )
    calls: list[str] = []

    def fail_get(self: FoodPreferenceService, person_id: UUID) -> object:
        raise AssertionError("submit/revise must not read a stale food snapshot first")

    def replace_locked(
        self: FoodPreferenceService,
        person_id: UUID,
        command: ReplaceFoodPreferenceCommand,
    ) -> FoodPreferenceProjection:
        calls.append("replace_locked")
        assert command.expected_version == 0
        assert (
            command.cuisines[0].cuisine_option_id
            == _input().favorite_cuisines[0].cuisine_option_id
        )
        return projection

    monkeypatch.setattr(FoodPreferenceService, "get", fail_get)
    monkeypatch.setattr(FoodPreferenceService, "replace_locked", replace_locked)

    from app.evaluations.survey_service import CampSurveyService

    result = CampSurveyService(None)._sync_food_preference(  # type: ignore[arg-type]
        student_person_id=projection.person_id,
        input=_input(),
        actor_user_id=UUID("20000000-0000-4000-8000-000000000001"),
        occurred_at=datetime.now(UTC),
        request_id="survey-test",
    )

    assert result is projection
    assert calls == ["replace_locked"]


def test_sync_food_preference_maps_food_validation_to_survey_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def replace_locked(
        self: FoodPreferenceService,
        person_id: UUID,
        command: ReplaceFoodPreferenceCommand,
    ) -> FoodPreferenceProjection:
        raise FoodPreferenceValidationFailed("inactive cuisine option cannot be added")

    monkeypatch.setattr(FoodPreferenceService, "replace_locked", replace_locked)

    from app.evaluations.survey_service import CampSurveyService

    with pytest.raises(CampSurveyValidationError):
        CampSurveyService(None)._sync_food_preference(  # type: ignore[arg-type]
            student_person_id=UUID("30000000-0000-4000-8000-000000000001"),
            input=_input(),
            actor_user_id=UUID("20000000-0000-4000-8000-000000000001"),
            occurred_at=datetime.now(UTC),
            request_id="survey-test",
        )


def test_mutation_request_id_prefers_http_request_id_and_has_safe_background_fallback() -> None:
    resource_id = UUID("40000000-0000-4000-8000-000000000099")
    assert _mutation_request_id("http-req-123", "submit", resource_id) == "http-req-123"
    fallback = _mutation_request_id(None, "submit", resource_id)
    assert fallback == f"camp-survey-submit-{resource_id}"
