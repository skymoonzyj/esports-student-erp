from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.housing.reporting_schemas import (
    GenerateHousingReportRequest,
    HousingReportBatchDetailResponse,
    HousingReportBatchPageResponse,
    HousingReportBatchScopeResponse,
    MarkHousingReportSubmittedRequest,
    VoidHousingReportRequest,
)
from app.housing.reporting_service import (
    ReportBatchActions,
    ReportBatchCorrectionLink,
    ReportBatchDetail,
    ReportBatchPage,
    ReportBatchScopeSummary,
    ReportBatchStatusCounts,
    ReportBuildingOption,
    ReportFilterOptions,
    ReportFloorOption,
)

START = datetime(2026, 8, 6, tzinfo=UTC)
END = datetime(2026, 8, 7, tzinfo=UTC)
BUILDING_ID = UUID("52000000-0000-4000-8000-000000000010")
FLOOR_ID = UUID("52000000-0000-4000-8000-000000000001")


def test_generate_request_normalizes_strict_scope_and_filters() -> None:
    request = GenerateHousingReportRequest.model_validate(
        {
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
            "scope_mode": "buildings",
            "building_ids": [str(BUILDING_ID)],
            "cohort_ids": ["40000000-0000-4000-8000-000000000001"],
            "person_types": ["student", "coach"],
            "purpose": "  派出所人工报送  ",
        }
    )

    assert request.building_ids == [BUILDING_ID]
    assert request.floor_ids == []
    assert request.purpose == "派出所人工报送"


@pytest.mark.parametrize(
    "payload",
    [
        {"scope_mode": "authorized", "building_ids": [str(BUILDING_ID)]},
        {"scope_mode": "buildings", "building_ids": []},
        {
            "scope_mode": "buildings",
            "building_ids": [str(BUILDING_ID)],
            "floor_ids": [str(FLOOR_ID)],
        },
        {"scope_mode": "floors", "floor_ids": []},
        {"scope_mode": "floors", "floor_ids": [str(FLOOR_ID), str(FLOOR_ID)]},
        {"scope_mode": "authorized", "person_types": ["student", "student"]},
        {"scope_mode": "authorized", "range_start": END, "range_end": START},
        {
            "scope_mode": "authorized",
            "range_start": START.replace(tzinfo=None),
        },
        {"scope_mode": "authorized", "purpose": " "},
        {"scope_mode": "authorized", "unexpected": "value"},
    ],
)
def test_generate_request_rejects_ambiguous_or_unbounded_input(
    payload: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "range_start": START,
        "range_end": END,
        "scope_mode": "authorized",
        "purpose": "派出所人工报送",
    }
    values.update(payload)

    with pytest.raises(ValidationError):
        GenerateHousingReportRequest.model_validate(values)


def test_state_requests_trim_text_and_require_positive_strict_version() -> None:
    submitted = MarkHousingReportSubmittedRequest.model_validate(
        {
            "submitted_at": START,
            "note": "   ",
            "version": 2,
        }
    )
    voided = VoidHousingReportRequest.model_validate(
        {"reason": "  名单有误  ", "version": 3}
    )

    assert submitted.note is None
    assert submitted.version == 2
    assert voided.reason == "名单有误"
    assert voided.version == 3

    for payload in (
        {"submitted_at": START.replace(tzinfo=None), "version": 1},
        {"submitted_at": START, "version": True},
        {"submitted_at": START, "version": 0},
    ):
        with pytest.raises(ValidationError):
            MarkHousingReportSubmittedRequest.model_validate(payload)
    for payload in (
        {"reason": " ", "version": 1},
        {"reason": "错", "version": 1},
        {"reason": "名单有误", "version": True},
        {"reason": "名单有误", "version": 0},
    ):
        with pytest.raises(ValidationError):
            VoidHousingReportRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            GenerateHousingReportRequest,
            {
                "range_start": START,
                "range_end": END,
                "scope_mode": "authorized",
                "purpose": "派出所报送 13800138000",
            },
        ),
        (
            MarkHousingReportSubmittedRequest,
            {
                "submitted_at": START,
                "note": "核对身份证 11010519491231002X",
                "version": 1,
            },
        ),
        (
            VoidHousingReportRequest,
            {
                "reason": "联系号码 13800138000",
                "version": 1,
            },
        ),
    ],
)
def test_business_text_rejects_complete_phone_or_identity(
    model: type[
        GenerateHousingReportRequest
        | MarkHousingReportSubmittedRequest
        | VoidHousingReportRequest
    ],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_response_models_expose_safe_batch_fields_without_snapshot_material() -> None:
    batch_id = UUID("72000000-0000-4000-8000-000000000001")
    actor_id = UUID("71000000-0000-4000-8000-000000000001")
    detail = ReportBatchDetail(
        id=batch_id,
        batch_number="HR-20260807-000001",
        status="failed",
        version=1,
        range_start=START,
        range_end=END,
        cohort_filter=(),
        person_type_filter=("student",),
        purpose="派出所人工报送",
        resident_count=0,
        stay_row_count=0,
        guardian_row_count=0,
        scopes=(
            ReportBatchScopeSummary(
                building_id=BUILDING_ID,
                building_name="4幢",
                floor_id=FLOOR_ID,
                floor_name="二楼",
            ),
        ),
        created_at=END,
        created_by_user_id=actor_id,
        created_by_name="管理员",
        submitted_at=None,
        submitted_by_user_id=None,
        submitted_by_name=None,
        voided_at=None,
        voided_by_user_id=None,
        voided_by_name=None,
        corrects_batch_id=None,
        failure_code="housing_export_incomplete",
        allowed_actions=ReportBatchActions(
            download=False,
            mark_submitted=False,
            void=False,
            correct=False,
        ),
        submission_note=None,
        void_reason=None,
        failure_summary={"issue_count": 1, "field_counts": {"phone": 1}},
        correction_chain=(
            ReportBatchCorrectionLink(
                id=batch_id,
                batch_number="HR-20260807-000001",
                status="failed",
                created_at=END,
            ),
        ),
    )
    page = ReportBatchPage(
        items=(detail,),
        total=1,
        page=1,
        page_size=20,
        status_counts=ReportBatchStatusCounts(
            total=1,
            generated=0,
            marked_submitted=0,
            voided=0,
            failed=1,
        ),
        access_revision="a" * 64,
        filter_options=ReportFilterOptions(
            buildings=(ReportBuildingOption(id=BUILDING_ID, name="4幢"),),
            floors=(
                ReportFloorOption(
                    id=FLOOR_ID,
                    building_id=BUILDING_ID,
                    name="二楼",
                ),
            ),
            cohorts=(),
        ),
    )

    detail_payload = HousingReportBatchDetailResponse.model_validate(
        detail
    ).model_dump(mode="json")
    page_payload = HousingReportBatchPageResponse.model_validate(page).model_dump(
        mode="json"
    )

    assert detail_payload["failure_summary"] == {
        "issue_count": 1,
        "field_counts": {"phone": 1},
    }
    assert page_payload["items"][0]["id"] == str(batch_id)
    serialized = str({"detail": detail_payload, "page": page_payload})
    assert "snapshot_ciphertext" not in serialized
    assert "snapshot_digest" not in serialized
    assert "snapshot_key_version" not in serialized

    unsafe_detail = replace(
        detail,
        purpose="报送用途 13800138000",
        submission_note="身份证 11010519491231002X",
        void_reason="联系电话 13800138000",
    )
    unsafe_payload = HousingReportBatchDetailResponse.model_validate(
        unsafe_detail
    ).model_dump(mode="json")
    assert unsafe_payload["purpose"] == "敏感内容已隐藏"
    assert unsafe_payload["submission_note"] == "敏感内容已隐藏"
    assert unsafe_payload["void_reason"] == "敏感内容已隐藏"
    assert "13800138000" not in str(unsafe_payload)
    assert "11010519491231002X" not in str(unsafe_payload)


def test_response_models_match_the_safe_web_reporting_contract() -> None:
    assert {"access_revision", "filter_options"} <= set(
        HousingReportBatchPageResponse.model_fields
    )
    summary_fields = HousingReportBatchDetailResponse.model_fields
    assert {
        "created_by_name",
        "submitted_by_name",
        "voided_by_name",
    } <= set(summary_fields)
    scope_annotation = summary_fields["scopes"].annotation
    assert scope_annotation is not None

    assert {"building_name", "floor_name"} <= set(
        HousingReportBatchScopeResponse.model_fields
    )
