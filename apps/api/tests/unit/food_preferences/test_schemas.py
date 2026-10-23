from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.food_preferences.schemas import (
    CuisineCatalogResponse,
    CuisineCreateRequest,
    CuisineOptionResponse,
    CuisinePatchRequest,
    CuisineSelectionInput,
    DishSuggestionResponse,
    FoodPreferenceExportRequest,
    FoodPreferencePersonPageResponse,
    FoodPreferencePersonResponse,
    FoodPreferenceProfileResponse,
    FoodPreferenceRankResponse,
    FoodPreferenceSelfResponse,
    FoodPreferenceStatisticsPageResponse,
    FoodPreferenceStatisticsResponse,
    FoodPreferenceWriteRequest,
)


def test_food_preference_write_request_normalizes_ordered_values_and_is_closed() -> None:
    cuisine_id = uuid4()
    payload = FoodPreferenceWriteRequest.model_validate(
        {
            "expected_version": 0,
            "cuisines": [{"cuisine_option_id": str(cuisine_id), "custom_name": None}],
            "dishes": [" 宫保  鸡丁 "],
        }
    )

    assert payload.expected_version == 0
    assert payload.cuisines[0].cuisine_option_id == cuisine_id
    assert payload.dishes == ("宫保 鸡丁",)
    with pytest.raises(ValidationError):
        FoodPreferenceWriteRequest.model_validate(
            {**payload.model_dump(), "unexpected": True}
        )


@pytest.mark.parametrize("field", ["expected_version", "cuisines", "dishes"])
@pytest.mark.parametrize("value", [True, 1.0, "0"])
def test_food_preference_write_request_rejects_non_strict_integer_or_sequence_values(
    field: str, value: object
) -> None:
    payload: dict[str, object] = {"expected_version": 0, "cuisines": [], "dishes": []}
    payload[field] = value
    with pytest.raises(ValidationError):
        FoodPreferenceWriteRequest.model_validate(payload)


@pytest.mark.parametrize("expected_version", [-1, True, 1.0, "1"])
def test_food_preference_write_request_expected_version_is_strict_non_negative(
    expected_version: object,
) -> None:
    with pytest.raises(ValidationError):
        FoodPreferenceWriteRequest.model_validate(
            {"expected_version": expected_version, "cuisines": [], "dishes": []}
        )


def test_food_preference_write_request_preserves_order_and_allows_zero_to_three() -> None:
    values = ["鱼香肉丝", "宫保鸡丁", "清蒸鱼"]
    payload = FoodPreferenceWriteRequest(
        expected_version=2,
        cuisines=[{"cuisine_option_id": uuid4()}, {"cuisine_option_id": uuid4()}],
        dishes=values,
    )
    assert payload.dishes == tuple(values)
    assert isinstance(payload.cuisines, tuple)

    with pytest.raises(ValidationError):
        FoodPreferenceWriteRequest(
            expected_version=2,
            cuisines=[{"cuisine_option_id": uuid4()}] * 4,
            dishes=[],
        )
    with pytest.raises(ValidationError):
        FoodPreferenceWriteRequest(
            expected_version=2,
            cuisines=[],
            dishes=values + ["红烧肉"],
        )


def test_food_preference_write_request_defers_repeated_option_to_catalog() -> None:
    other_id = uuid4()
    payload = FoodPreferenceWriteRequest(
        expected_version=0,
        cuisines=[
            {"cuisine_option_id": other_id, "custom_name": "川菜"},
            {"cuisine_option_id": other_id, "custom_name": "粤菜"},
        ],
        dishes=[],
    )

    assert [item.custom_name for item in payload.cuisines] == ["川菜", "粤菜"]


def test_food_preference_write_request_rejects_duplicate_normalized_custom_cuisines() -> None:
    with pytest.raises(ValidationError, match="unique"):
        FoodPreferenceWriteRequest(
            expected_version=0,
            cuisines=[
                {
                    "cuisine_option_id": uuid4(),
                    "custom_name": "ＳｉＣｈｕａｎ",
                },
                {
                    "cuisine_option_id": uuid4(),
                    "custom_name": " sichuan ",
                },
            ],
            dishes=[],
        )


def test_food_preference_write_request_rejects_duplicate_normalized_dishes() -> None:
    with pytest.raises(ValidationError, match="unique"):
        FoodPreferenceWriteRequest(
            expected_version=0,
            cuisines=[],
            dishes=["ＳｉＣｈｕａｎ", " sichuan "],
        )


def test_food_preference_write_request_rejects_non_text_dishes() -> None:
    with pytest.raises(ValidationError):
        FoodPreferenceWriteRequest(expected_version=0, cuisines=[], dishes=[123])


def test_cuisine_selection_normalizes_blank_custom_name_to_none() -> None:
    selection = CuisineSelectionInput(cuisine_option_id=uuid4(), custom_name=" \t ")
    assert selection.custom_name is None


def test_cuisine_create_and_patch_requests_normalize_names_and_use_strict_sort_order() -> None:
    created = CuisineCreateRequest(name="  粤\u3000菜 ", sort_order=3)
    assert created.name == "粤 菜"
    assert created.sort_order == 3
    with pytest.raises(ValidationError):
        CuisineCreateRequest(name="川菜", sort_order=True)

    patched = CuisinePatchRequest(name="  ＳｉＣｈｕａｎ  ")
    assert patched.name == "SiChuan"
    with pytest.raises(ValidationError):
        CuisinePatchRequest()


def test_request_models_reject_scalar_type_coercion() -> None:
    with pytest.raises(ValidationError):
        CuisineCreateRequest(name=b"\xe5\xb7\x9d\xe8\x8f\x9c", sort_order=0)
    with pytest.raises(ValidationError):
        CuisinePatchRequest(is_active=1)
    with pytest.raises(ValidationError):
        FoodPreferenceExportRequest(
            population="students",
            include_inactive="false",
            format="xlsx",
            purpose="安排餐食",
        )


@pytest.mark.parametrize("purpose", ["abc", "x" * 201, " \t "])
def test_food_preference_export_request_validates_normalized_purpose(purpose: str) -> None:
    with pytest.raises(ValidationError):
        FoodPreferenceExportRequest(
            population="students",
            include_inactive=False,
            format="xlsx",
            purpose=purpose,
        )
    request = FoodPreferenceExportRequest(
        population="all_people",
        include_inactive=True,
        format="csv_zip",
        purpose="  安排下一期营地餐食  ",
    )
    assert request.purpose == "安排下一期营地餐食"


@pytest.mark.parametrize("population", ["student", "all", ""])
def test_food_preference_export_request_restricts_population(population: str) -> None:
    with pytest.raises(ValidationError):
        FoodPreferenceExportRequest(
            population=population,
            include_inactive=False,
            format="xlsx",
            purpose="安排餐食",
        )


@pytest.mark.parametrize("file_format", ["csv", "zip", "xlsx "])
def test_food_preference_export_request_restricts_format(file_format: str) -> None:
    with pytest.raises(ValidationError):
        FoodPreferenceExportRequest(
            population="students",
            include_inactive=False,
            format=file_format,
            purpose="安排餐食",
        )


def test_response_dtos_are_closed_and_use_aware_datetime() -> None:
    cuisine_id = uuid4()
    person_id = uuid4()
    revision = "a" * 64
    option = CuisineOptionResponse(
        id=cuisine_id,
        name="川菜",
        sort_order=1,
        is_active=True,
        is_other=False,
    )
    catalog = CuisineCatalogResponse(
        items=(option,), allowed_actions=("create",), access_revision=revision
    )
    assert catalog.items[0].id == cuisine_id
    assert isinstance(catalog.items, tuple)
    with pytest.raises(ValidationError):
        CuisineCatalogResponse.model_validate(
            {**catalog.model_dump(), "unexpected": True}
        )

    updated_at = datetime(2026, 8, 11, 10, tzinfo=UTC)
    profile = FoodPreferenceProfileResponse(
        person_id=person_id,
        version=0,
        cuisines=(),
        dishes=(),
        updated_at=None,
        allowed_actions=(),
        access_revision=revision,
    )
    assert profile.version == 0
    assert profile.updated_at is None
    populated = profile.model_copy(update={"updated_at": updated_at})
    assert populated.updated_at == updated_at
    with pytest.raises(ValidationError):
        FoodPreferenceProfileResponse.model_validate(
            {**profile.model_dump(), "unexpected": True}
        )
    with pytest.raises(ValidationError):
        FoodPreferenceProfileResponse(
            person_id=person_id,
            version=0,
            cuisines=(),
            dishes=(),
            updated_at=datetime.fromisoformat("2026-08-11T10:00:00"),
            allowed_actions=(),
            access_revision=revision,
        )


def test_self_response_is_closed_and_requires_aware_completion_time() -> None:
    profile = FoodPreferenceProfileResponse(
        person_id=uuid4(),
        version=0,
        cuisines=(),
        dishes=(),
        updated_at=None,
        allowed_actions=("update",),
        access_revision="c" * 64,
    )
    completed_at = datetime(2026, 8, 11, 10, tzinfo=UTC)
    response = FoodPreferenceSelfResponse(
        profile=profile,
        onboarding_required=False,
        onboarding_completed_at=completed_at,
    )

    assert response.profile.person_id == profile.person_id
    assert response.onboarding_completed_at == completed_at
    with pytest.raises(ValidationError):
        FoodPreferenceSelfResponse.model_validate(
            {**response.model_dump(), "unexpected": True}
        )
    with pytest.raises(ValidationError):
        FoodPreferenceSelfResponse(
            profile=profile,
            onboarding_required=False,
            onboarding_completed_at=datetime.fromisoformat("2026-08-11T10:00:00"),
        )


def test_response_dtos_cover_suggestions_ranks_and_paging() -> None:
    now = datetime(2026, 8, 11, 10, tzinfo=UTC)
    suggestion = DishSuggestionResponse(name="宫保鸡丁")
    rank = FoodPreferenceRankResponse(
        label="川菜", count=2, ratio=0.5
    )
    person = FoodPreferencePersonResponse(
        person_id=uuid4(),
        full_name="学生",
        person_types=("student",),
        status="active",
        cuisines=("川菜",),
        dishes=("宫保鸡丁",),
        updated_at=now,
    )
    page = FoodPreferencePersonPageResponse(
        items=(person,), page=1, page_size=20, total=1
    )
    statistics = FoodPreferenceStatisticsResponse(
        population="students",
        include_inactive=False,
        total_people=4,
        filled_people=2,
        unfilled_people=2,
        filled_ratio=0.5,
        cuisines=(rank,),
        dishes=(rank,),
        people=page,
        access_revision="b" * 64,
    )
    assert suggestion.name == "宫保鸡丁"
    assert statistics.people.page == 1
    assert isinstance(statistics.cuisines, tuple)
    assert isinstance(statistics.people.items, tuple)
    with pytest.raises(ValidationError):
        FoodPreferenceStatisticsPageResponse.model_validate(
            {"items": [person], "page": 1, "page_size": 20, "total": 1, "unexpected": 1}
        )
