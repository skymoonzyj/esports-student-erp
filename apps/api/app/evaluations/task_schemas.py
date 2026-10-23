from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvaluationAuthorizationSource = Literal[
    "self",
    "family",
    "coach",
    "staff",
    "administrator",
]
EvaluationTaskStatus = Literal[
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "archived",
    "published",
    "revision_draft",
]


class _StrictEvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class EvaluationUploadSessionCreate(_StrictEvaluationInput):
    project_id: UUID
    student_person_id: UUID | None = None
    enrollment_id: UUID | None = None
    delegated_upload_reason: str | None = Field(default=None, max_length=300)

    @field_validator("delegated_upload_reason", mode="before")
    @classmethod
    def trim_delegated_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_complete_target_pair(self) -> Self:
        if (self.student_person_id is None) != (self.enrollment_id is None):
            raise ValueError("student_person_id and enrollment_id must be provided together")
        return self


class EvaluationTaskCreate(_StrictEvaluationInput):
    student_person_id: UUID | None = None
    enrollment_id: UUID | None = None
    cohort_id: UUID | None = None
    class_id: UUID | None = None

    @model_validator(mode="after")
    def require_complete_or_empty_target(self) -> Self:
        values = (
            self.student_person_id,
            self.enrollment_id,
            self.cohort_id,
            self.class_id,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError("evaluation task target must be complete")
        return self


class EvaluationUploadSlotInput(_StrictEvaluationInput):
    file: UploadFile


class EvaluationProfileSlot(BaseModel):
    key: str
    label: str
    position: int = Field(ge=1, le=3)
    required: bool


class EvaluationUploadTargetResponse(BaseModel):
    student_person_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    project_id: UUID
    class_id: UUID
    identity_summary: str
    project_name: str
    cohort_name: str
    class_name: str
    authorization_source: EvaluationAuthorizationSource
    requires_delegated_upload_reason: bool
    upload_window_start: datetime
    upload_window_end: datetime
    profile_id: UUID
    profile_version: int = Field(ge=1)
    profile_mode: Literal["manual_only", "automatic"]
    slots: list[EvaluationProfileSlot]


class EvaluationUploadContextResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    upload_available: bool
    targets: list[EvaluationUploadTargetResponse]


class EvaluationWebSessionContextResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    identity_summary: str
    upload_available: bool


class EvaluationUploadSessionResponse(BaseModel):
    id: UUID
    status: Literal["open", "committed", "expired", "cancelled"]
    expires_at: datetime


class EvaluationUploadSlotResponse(BaseModel):
    id: UUID
    session_id: UUID
    slot_key: str
    position: int = Field(ge=1, le=3)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(gt=0)
    width_pixels: int = Field(gt=0)
    height_pixels: int = Field(gt=0)


class EvaluationUploadSlotDeletedResponse(BaseModel):
    session_id: UUID
    slot_key: str
    deleted: Literal[True] = True


class EvaluationTaskCreatedResponse(BaseModel):
    id: UUID
    public_number: str
    status: EvaluationTaskStatus
    lock_version: int = Field(ge=1)


class EvaluationTaskMatchedResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    student_person_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    project_id: UUID
    class_id: UUID
    task_status: EvaluationTaskStatus
    task_version: int = Field(ge=1)


class EvaluationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int = Field(ge=1)
    source: Literal["automatic", "manual", "correction", "revision"]
    fields: dict[str, Any]
    confidences: dict[str, Any]
    warnings: list[dict[str, Any]]
    components: list[dict[str, Any]]
    missing_fields: list[str]
    score: float = Field(ge=0, le=100)
    grade: str
    advice: str
    adapter_id: str | None
    adapter_version: str | None
    scoring_rule_version: str | None
    result_schema_version: str
    review_decision: Literal["pending", "approved", "rejected"]
    reviewed_at: datetime | None
    rejection_reason: str | None
    published_at: datetime | None
    previous_version_id: UUID | None


class EvaluationAllowedActionsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    match: bool
    save_manual_draft: bool
    submit_review: bool
    create_correction: bool
    create_revision: bool
    review: bool
    archive: bool
    reopen: bool
    view_preview: bool


class EvaluationTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    public_number: str
    status: EvaluationTaskStatus
    lock_version: int = Field(ge=1)
    created_at: datetime
    student_person_id: UUID | None
    enrollment_id: UUID | None
    cohort_id: UUID | None
    project_id: UUID
    class_id: UUID | None
    identity_summary: str | None
    cohort_name: str | None
    project_name: str
    class_name: str | None
    media_count: int = Field(ge=0)
    current_result: EvaluationResultResponse | None
    published_result: EvaluationResultResponse | None
    result_versions: list[EvaluationResultResponse]
    allowed_actions: EvaluationAllowedActionsResponse
    allowed_reopen_modes: list[Literal["automatic", "correction"]]


class EvaluationTaskPageResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    items: list[EvaluationTaskResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class EvaluationTaskDetailResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task: EvaluationTaskResponse


class EvaluationDashboardResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    upload_available: bool
    processing: int = Field(ge=0)
    pending_match: int = Field(ge=0)
    manual_input: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    rejected: int = Field(ge=0)
    published: int = Field(ge=0)


class EvaluationTaskFilterOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str = Field(min_length=1, max_length=200)


class EvaluationTaskFilterOptionsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    api_version: Literal["v2"] = "v2"
    cohorts: list[EvaluationTaskFilterOptionResponse]
    projects: list[EvaluationTaskFilterOptionResponse]
    classes: list[EvaluationTaskFilterOptionResponse]
    students: list[EvaluationTaskFilterOptionResponse]


class EvaluationWorkspaceMediaSlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    position: int = Field(ge=1, le=3)
    required: bool


class EvaluationWorkspaceProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mode: Literal["manual_only", "automatic"]
    adapter_id: str | None
    adapter_version: str | None
    scoring_rule_version: str | None
    result_schema_version: str
    required_fields: list[str]
    media_slots: list[EvaluationWorkspaceMediaSlotResponse]


class EvaluationWorkspaceMediaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slot_key: str
    position: int = Field(ge=1, le=3)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(gt=0)
    width_pixels: int = Field(gt=0)
    height_pixels: int = Field(gt=0)


class EvaluationWorkspaceDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    draft_revision: int = Field(ge=1)
    fields: dict[str, Any]
    confidences: dict[str, Any]
    warnings: list[dict[str, Any]]
    components: list[dict[str, Any]]
    missing_fields: list[str]
    score: float | None = Field(default=None, ge=0, le=100)
    grade: str | None
    advice: str | None
    updated_at: datetime


class EvaluationTaskWorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    api_version: Literal["v2"] = "v2"
    task_id: UUID
    task_version: int = Field(ge=1)
    profile: EvaluationWorkspaceProfileResponse
    media: list[EvaluationWorkspaceMediaResponse]
    draft: EvaluationWorkspaceDraftResponse | None


class EvaluationMatchTargetItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_person_id: UUID
    student_name: str
    student_number: str
    enrollment_id: UUID
    cohort_id: UUID
    cohort_name: str
    class_id: UUID
    class_name: str


class EvaluationMatchTargetPageResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    items: list[EvaluationMatchTargetItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class EvaluationDraftSavedResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    task_status: EvaluationTaskStatus
    task_version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    is_complete: bool
    missing_required_fields: list[str]
    updated_at: datetime


class EvaluationResultSubmittedResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    result_id: UUID
    result_version: int = Field(ge=1)
    task_status: EvaluationTaskStatus
    task_version: int = Field(ge=1)


class EvaluationRevisionStartedResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    task_status: EvaluationTaskStatus
    task_version: int = Field(ge=1)
    draft_revision: int | None = Field(default=None, ge=1)


class EvaluationReviewedResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    result_id: UUID
    result_version: int = Field(ge=1)
    task_status: EvaluationTaskStatus
    task_version: int = Field(ge=1)
    review_decision: Literal["approved", "rejected"]
    reviewed_at: datetime
    published_at: datetime | None


class EvaluationTaskStateChangedResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    task_status: EvaluationTaskStatus
    task_version: int = Field(ge=1)


class EvaluationAdapterCertificationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    adapter_id: str
    adapter_version: str
    scoring_rule_version: str
    result_schema_version: str
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    certified_by_user_id: UUID
    certified_at: datetime


class EvaluationAdapterCertificationResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    certification: EvaluationAdapterCertificationItem


class EvaluationAdapterCertificationListResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    items: list[EvaluationAdapterCertificationItem]
