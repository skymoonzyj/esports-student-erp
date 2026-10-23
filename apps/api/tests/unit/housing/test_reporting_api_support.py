from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest

from app.core.errors import ProblemError
from app.housing.api_support import _public_error_code, _raise_housing_error
from app.housing.errors import (
    HousingAuditFailure,
    HousingExportIncomplete,
    HousingReportCorrectionInvalid,
    HousingReportEmpty,
    HousingReportingError,
    HousingReportRenderFailed,
    HousingReportScopeDenied,
    HousingReportSnapshotUnavailable,
    HousingReportStateConflict,
)
from app.housing.reporting_types import ProjectionIssue


def _generation_error(
    error_type: type[HousingReportEmpty | HousingExportIncomplete],
) -> HousingReportingError:
    return error_type(
        uuid4(),
        {"issue_count": 0, "field_counts": {}},
        (),
    )


@pytest.mark.parametrize(
    ("factory", "expected_status", "expected_code"),
    [
        (
            lambda: _generation_error(HousingReportEmpty),
            422,
            "housing_report_empty",
        ),
        (
            lambda: _generation_error(HousingExportIncomplete),
            422,
            "housing_export_incomplete",
        ),
        (
            lambda: HousingReportScopeDenied("secret scope"),
            403,
            "housing_report_scope_denied",
        ),
        (
            lambda: HousingReportStateConflict("secret state"),
            409,
            "housing_report_state_conflict",
        ),
        (
            lambda: HousingReportCorrectionInvalid("secret correction"),
            409,
            "housing_report_correction_invalid",
        ),
        (
            lambda: HousingReportSnapshotUnavailable("secret snapshot"),
            503,
            "housing_report_snapshot_unavailable",
        ),
        (
            lambda: HousingReportRenderFailed("secret render"),
            503,
            "housing_report_render_failed",
        ),
        (
            lambda: HousingAuditFailure("secret audit"),
            500,
            "housing_audit_failed",
        ),
    ],
)
def test_reporting_errors_have_stable_public_problem_mapping(
    factory: Callable[[], HousingReportingError],
    expected_status: int,
    expected_code: str,
) -> None:
    error = factory()

    assert _public_error_code(error) == expected_code
    with pytest.raises(ProblemError) as caught:
        _raise_housing_error(error)

    assert caught.value.status == expected_status
    assert caught.value.code == expected_code
    assert "secret" not in caught.value.detail


def test_incomplete_error_exposes_only_safe_flat_issue_fields() -> None:
    batch_id = uuid4()
    person_id = uuid4()
    stay_id = uuid4()
    error = HousingExportIncomplete(
        batch_id,
        {"issue_count": 1, "field_counts": {"phone": 1}},
        (
            ProjectionIssue(
                person_id=person_id,
                person_name="待补资料学员",
                masked_phone="138****0001",
                stay_id=stay_id,
                missing_fields=("phone", "identity_number"),
            ),
        ),
    )

    with pytest.raises(ProblemError) as caught:
        _raise_housing_error(error)

    assert caught.value.errors == [
        {
            "field": f"report_issue.{person_id}",
            "message": (
                f"待补资料学员（138****0001，住宿 {stay_id}）"
                "缺少：phone、identity_number"
            ),
            "batch_id": str(batch_id),
            "person_id": str(person_id),
            "person_name": "待补资料学员",
            "masked_phone": "138****0001",
            "stay_id": str(stay_id),
            "missing_fields": "phone,identity_number",
        }
    ]
    serialized = str(caught.value.errors)
    assert "13800138001" not in serialized
    assert "110105" not in serialized


def test_incomplete_error_sanitizes_malformed_issue_fields() -> None:
    error = HousingExportIncomplete(
        uuid4(),
        {"issue_count": 1, "field_counts": {"phone": 1}},
        (
            ProjectionIssue(
                person_id=uuid4(),
                person_name="13800138000",
                masked_phone="13800138000",
                stay_id=uuid4(),
                missing_fields=("phone", "identity:11010519491231002X"),
            ),
        ),
    )

    with pytest.raises(ProblemError) as caught:
        _raise_housing_error(error)

    assert caught.value.errors is not None
    issue = caught.value.errors[0]
    assert issue["field"].startswith("report_issue.")
    assert issue["message"].startswith("敏感内容已隐藏（")
    assert issue["person_name"] == "敏感内容已隐藏"
    assert issue["masked_phone"] == ""
    assert issue["missing_fields"] == "phone"
    serialized = str(issue)
    assert "13800138000" not in serialized
    assert "11010519491231002X" not in serialized
