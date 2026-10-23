from __future__ import annotations

import builtins
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Literal, NoReturn, cast
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, Connection, and_, exists, func, not_, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.privacy import contains_phone_or_identity
from app.housing.access import HousingAccessService
from app.housing.errors import (
    HousingAuditFailure,
    HousingExportIncomplete,
    HousingPersistenceFailure,
    HousingReportCorrectionInvalid,
    HousingReportEmpty,
    HousingReportingError,
    HousingReportRenderFailed,
    HousingReportScopeDenied,
    HousingReportSnapshotUnavailable,
    HousingReportStateConflict,
    HousingResourceNotFound,
)
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingStay,
)
from app.housing.reporting_models import HousingReportBatch, HousingReportBatchScope
from app.housing.reporting_projection import HousingReportProjectionBuilder, ProjectionRequest
from app.housing.reporting_renderer import render_csv_zip, render_xlsx
from app.housing.reporting_snapshot import SealedSnapshot, open_projection, seal_projection
from app.housing.reporting_types import (
    HousingReportProjectionV1,
    ProjectionIssue,
    ResidentPersonType,
)
from app.identity.models import Person
from app.security.encryption import EnvelopeCipher
from app.teaching.models import Cohort

ReportBatchStatus = Literal["generated", "marked_submitted", "voided", "failed"]
ReportAction = Literal["download", "mark_submitted", "void", "correct"]
ReportFormat = Literal["xlsx", "csv"]
ReportScopeMode = Literal["authorized", "buildings", "floors"]

_REPORT_STATUSES = frozenset({"generated", "marked_submitted", "voided", "failed"})
_PERSON_TYPES = frozenset(
    {
        "student",
        "coach",
        "life_teacher",
        "customer_service",
        "administration",
        "other",
        "visitor",
    }
)
_SAFE_BATCH_NUMBER = re.compile(r"HR-[0-9]{8}-[0-9]{6}\Z")
_SAFE_REPORT_FILENAME = re.compile(
    r"housing-report-(?:HR-[0-9]{8}-[0-9]{6}|[0-9a-f]{32})\.(?:xlsx|zip)\Z"
)


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _uuid_tuple(values: tuple[UUID, ...], *, field_name: str, limit: int) -> tuple[UUID, ...]:
    normalized = tuple(values)
    if len(normalized) > limit:
        raise ValueError(f"{field_name} has too many values")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} contains duplicates")
    return normalized


@dataclass(frozen=True, slots=True)
class GenerateReportCommand:
    range_start: datetime
    range_end: datetime
    scope_mode: ReportScopeMode
    purpose: str
    building_ids: tuple[UUID, ...] = ()
    floor_ids: tuple[UUID, ...] = ()
    cohort_ids: tuple[UUID, ...] = ()
    person_types: tuple[ResidentPersonType, ...] = ()
    corrects_batch_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_aware(self.range_start, field_name="range_start")
        _require_aware(self.range_end, field_name="range_end")
        if self.range_start >= self.range_end:
            raise ValueError("report range must be half-open and non-empty")
        if self.scope_mode not in {"authorized", "buildings", "floors"}:
            raise ValueError("unsupported report scope mode")
        building_ids = _uuid_tuple(
            self.building_ids,
            field_name="building_ids",
            limit=100,
        )
        floor_ids = _uuid_tuple(self.floor_ids, field_name="floor_ids", limit=500)
        cohort_ids = _uuid_tuple(self.cohort_ids, field_name="cohort_ids", limit=100)
        person_types = tuple(self.person_types)
        if len(set(person_types)) != len(person_types):
            raise ValueError("person_types contains duplicates")
        if any(person_type not in _PERSON_TYPES for person_type in person_types):
            raise ValueError("unsupported report person type")
        if self.scope_mode == "authorized" and (building_ids or floor_ids):
            raise ValueError("authorized scope cannot include explicit IDs")
        if self.scope_mode == "buildings" and (not building_ids or floor_ids):
            raise ValueError("building scope requires only building IDs")
        if self.scope_mode == "floors" and (not floor_ids or building_ids):
            raise ValueError("floor scope requires only floor IDs")
        purpose = self.purpose.strip()
        if len(purpose) < 2 or len(purpose) > 200:
            raise ValueError("report purpose must be 2 to 200 characters")
        if contains_phone_or_identity(purpose):
            raise ValueError("report purpose cannot contain a complete phone or identity")
        object.__setattr__(self, "building_ids", building_ids)
        object.__setattr__(self, "floor_ids", floor_ids)
        object.__setattr__(self, "cohort_ids", cohort_ids)
        object.__setattr__(self, "person_types", person_types)
        object.__setattr__(self, "purpose", purpose)


@dataclass(frozen=True, slots=True)
class MarkSubmittedCommand:
    submitted_at: datetime
    note: str | None
    expected_version: int

    def __post_init__(self) -> None:
        _require_aware(self.submitted_at, field_name="submitted_at")
        if self.expected_version < 1:
            raise ValueError("expected_version must be positive")
        note = (self.note or "").strip() or None
        if note is not None and len(note) > 500:
            raise ValueError("submission note is too long")
        if note is not None and contains_phone_or_identity(note):
            raise ValueError("submission note cannot contain a complete phone or identity")
        object.__setattr__(self, "note", note)


@dataclass(frozen=True, slots=True)
class VoidReportCommand:
    reason: str
    expected_version: int

    def __post_init__(self) -> None:
        reason = self.reason.strip()
        if len(reason) < 2 or len(reason) > 500:
            raise ValueError("void reason must be 2 to 500 characters")
        if contains_phone_or_identity(reason):
            raise ValueError("void reason cannot contain a complete phone or identity")
        if self.expected_version < 1:
            raise ValueError("expected_version must be positive")
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True)
class ReportBatchQuery:
    statuses: tuple[ReportBatchStatus, ...] = ()
    range_start: datetime | None = None
    range_end: datetime | None = None
    building_ids: tuple[UUID, ...] = ()
    floor_ids: tuple[UUID, ...] = ()
    cohort_ids: tuple[UUID, ...] = ()
    created_by_user_id: UUID | None = None
    page: int = 1
    page_size: int = 20

    def __post_init__(self) -> None:
        statuses = tuple(self.statuses)
        if len(set(statuses)) != len(statuses) or any(
            status not in _REPORT_STATUSES for status in statuses
        ):
            raise ValueError("invalid report status filter")
        if self.range_start is not None:
            _require_aware(self.range_start, field_name="range_start")
        if self.range_end is not None:
            _require_aware(self.range_end, field_name="range_end")
        if (
            self.range_start is not None
            and self.range_end is not None
            and self.range_start >= self.range_end
        ):
            raise ValueError("report range must be half-open and non-empty")
        if self.page < 1 or not 1 <= self.page_size <= 100:
            raise ValueError("invalid report pagination")
        object.__setattr__(self, "statuses", statuses)
        object.__setattr__(
            self,
            "building_ids",
            _uuid_tuple(self.building_ids, field_name="building_ids", limit=100),
        )
        object.__setattr__(
            self,
            "floor_ids",
            _uuid_tuple(self.floor_ids, field_name="floor_ids", limit=500),
        )
        object.__setattr__(
            self,
            "cohort_ids",
            _uuid_tuple(self.cohort_ids, field_name="cohort_ids", limit=100),
        )


@dataclass(frozen=True, slots=True)
class ReportBatchActions:
    download: bool
    mark_submitted: bool
    void: bool
    correct: bool


@dataclass(frozen=True, slots=True)
class ReportBatchScopeSummary:
    building_id: UUID
    building_name: str
    floor_id: UUID
    floor_name: str


@dataclass(frozen=True, slots=True)
class ReportBuildingOption:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class ReportFloorOption:
    id: UUID
    building_id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class ReportCohortOption:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class ReportFilterOptions:
    buildings: tuple[ReportBuildingOption, ...]
    floors: tuple[ReportFloorOption, ...]
    cohorts: tuple[ReportCohortOption, ...]


@dataclass(frozen=True, slots=True)
class ReportBatchStatusCounts:
    total: int
    generated: int
    marked_submitted: int
    voided: int
    failed: int


@dataclass(frozen=True, slots=True)
class ReportBatchSummary:
    id: UUID
    batch_number: str
    status: ReportBatchStatus
    version: int
    range_start: datetime
    range_end: datetime
    cohort_filter: tuple[UUID, ...]
    person_type_filter: tuple[ResidentPersonType, ...]
    purpose: str
    resident_count: int
    stay_row_count: int
    guardian_row_count: int
    scopes: tuple[ReportBatchScopeSummary, ...]
    created_at: datetime
    created_by_user_id: UUID
    created_by_name: str
    submitted_at: datetime | None
    submitted_by_user_id: UUID | None
    submitted_by_name: str | None
    voided_at: datetime | None
    voided_by_user_id: UUID | None
    voided_by_name: str | None
    corrects_batch_id: UUID | None
    failure_code: str | None
    allowed_actions: ReportBatchActions


@dataclass(frozen=True, slots=True)
class ReportBatchCorrectionLink:
    id: UUID
    batch_number: str
    status: ReportBatchStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReportBatchDetail(ReportBatchSummary):
    submission_note: str | None
    void_reason: str | None
    failure_summary: dict[str, object] | None
    correction_chain: tuple[ReportBatchCorrectionLink, ...]


@dataclass(frozen=True, slots=True)
class ReportBatchPage:
    items: tuple[ReportBatchSummary, ...]
    total: int
    page: int
    page_size: int
    status_counts: ReportBatchStatusCounts
    access_revision: str
    filter_options: ReportFilterOptions


@dataclass(frozen=True, slots=True)
class ReportDownload:
    content: bytes
    media_type: str
    filename: str

    def __post_init__(self) -> None:
        if _SAFE_REPORT_FILENAME.fullmatch(self.filename) is None:
            raise HousingReportRenderFailed("unsafe housing report filename")


def transition_allowed(status: str, action: str) -> bool:
    return (status, action) in {
        ("generated", "download"),
        ("generated", "mark_submitted"),
        ("generated", "void"),
        ("marked_submitted", "download"),
        ("marked_submitted", "void"),
        ("voided", "correct"),
    }


def allowed_actions(
    status: str,
    *,
    has_correction: bool,
) -> ReportBatchActions:
    return ReportBatchActions(
        download=transition_allowed(status, "download"),
        mark_submitted=transition_allowed(status, "mark_submitted"),
        void=transition_allowed(status, "void"),
        correct=transition_allowed(status, "correct") and not has_correction,
    )


class HousingReportingService:
    def __init__(
        self,
        db: Session,
        cipher: EnvelopeCipher,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> None:
        _require_aware(occurred_at, field_name="occurred_at")
        if not request_id.strip():
            raise ValueError("request_id is required")
        self._db = db
        self._cipher = cipher
        self._actor_user_id = actor_user_id
        self._occurred_at = occurred_at
        self._request_id = request_id

    def generate(self, command: GenerateReportCommand) -> ReportBatchSummary:
        try:
            self._repeatable_read_connection()
            audit_action = (
                "housing.report_batch.correct"
                if command.corrects_batch_id is not None
                else "housing.report_batch.generate"
            )
            access = HousingAccessService(
                self._db,
                self._actor_user_id,
                self._occurred_at,
            )
            corrects_batch_id: UUID | None = None
            if command.corrects_batch_id is not None:
                predecessor = self._locked_visible_batch(
                    command.corrects_batch_id,
                    access,
                )
                if predecessor is None:
                    self._raise_private_not_found(
                        action=audit_action,
                        batch_id=command.corrects_batch_id,
                    )
                if predecessor.status != "voided" or self._has_direct_correction(
                    predecessor.id
                ):
                    self._raise_correction_invalid(predecessor.id)
                corrects_batch_id = predecessor.id
            try:
                scopes = self._expand_generation_scopes(command, access)
            except HousingReportScopeDenied:
                self._db.rollback()
                raise
            result = HousingReportProjectionBuilder(self._db, self._cipher).build(
                ProjectionRequest(
                    generated_at=self._occurred_at,
                    range_start=command.range_start,
                    range_end=command.range_end,
                    floor_ids=tuple(scope.floor_id for scope in scopes),
                    cohort_ids=command.cohort_ids,
                    person_types=command.person_types,
                )
            )
            if result.projection is None:
                self._persist_failed_generation(
                    command=command,
                    scopes=scopes,
                    audit_action=audit_action,
                    failure_code="housing_export_incomplete",
                    failure_summary=self._incomplete_failure_summary(result.issues),
                    issues=result.issues,
                )
            if not result.projection.residents:
                self._persist_failed_generation(
                    command=command,
                    scopes=scopes,
                    audit_action=audit_action,
                    failure_code="housing_report_empty",
                    failure_summary={"issue_count": 0, "field_counts": {}},
                    issues=(),
                )
            projection = result.projection
            try:
                sealed = seal_projection(projection, cipher=self._cipher)
            except Exception as exc:  # noqa: BLE001 - encryption adapter boundary
                self._raise_generation_snapshot_failure(
                    action=audit_action,
                    resource_id=corrects_batch_id,
                    cause=exc,
                )
            batch = HousingReportBatch(
                id=uuid4(),
                status="generated",
                version=1,
                range_start=command.range_start,
                range_end=command.range_end,
                cohort_filter=[str(value) for value in command.cohort_ids],
                person_type_filter=list(command.person_types),
                purpose=command.purpose,
                projection_schema_version=projection.schema_version,
                snapshot_ciphertext=sealed.ciphertext,
                snapshot_key_version=sealed.key_version,
                snapshot_digest=sealed.digest,
                resident_count=projection.resident_count,
                stay_row_count=projection.stay_row_count,
                guardian_row_count=projection.guardian_row_count,
                created_at=self._occurred_at,
                created_by_user_id=self._actor_user_id,
                corrects_batch_id=corrects_batch_id,
                failure_code=None,
                failure_summary=None,
            )
            self._db.add(batch)
            self._db.add_all(
                HousingReportBatchScope(
                    batch_id=batch.id,
                    building_id=scope.building_id,
                    floor_id=scope.floor_id,
                )
                for scope in scopes
            )
            self._db.flush()
            self._append_audit(
                action=audit_action,
                resource_id=batch.id,
                outcome="allowed",
                metadata={"result_count": projection.stay_row_count},
            )
            summary = self._summary(
                batch,
                has_correction=False,
                visible_corrects_batch_id=corrects_batch_id,
                scopes=scopes,
            )
            self._commit()
            return summary
        except (HousingReportingError, HousingPersistenceFailure):
            raise
        except IntegrityError as exc:
            self._db.rollback()
            constraint_name = getattr(
                getattr(exc.orig, "diag", None),
                "constraint_name",
                None,
            )
            if (
                command.corrects_batch_id is not None
                and constraint_name
                == "uq_housing_report_batches_corrects_batch_id"
            ):
                self._append_audit(
                    action="housing.report_batch.correct",
                    resource_id=command.corrects_batch_id,
                    outcome="failed",
                    metadata={"error_code": "housing_report_state_conflict"},
                )
                self._commit()
                raise HousingReportStateConflict(
                    "housing report state conflict"
                ) from exc
            raise HousingPersistenceFailure(
                "unable to persist housing report"
            ) from exc
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise HousingPersistenceFailure("unable to persist housing report") from exc

    def list(self, query: ReportBatchQuery) -> ReportBatchPage:
        try:
            access = HousingAccessService(
                self._db,
                self._actor_user_id,
                self._occurred_at,
            )
            conditions = self._list_conditions(query, access, include_status=True)
            count_conditions = self._list_conditions(
                query,
                access,
                include_status=False,
            )
            total = self._db.scalar(
                select(func.count(HousingReportBatch.id)).where(*conditions)
            ) or 0
            batches = list(
                self._db.scalars(
                    select(HousingReportBatch)
                    .where(*conditions)
                    .order_by(
                        HousingReportBatch.created_at.desc(),
                        HousingReportBatch.id.desc(),
                    )
                    .offset((query.page - 1) * query.page_size)
                    .limit(query.page_size)
                )
            )
            grouped_counts = dict(
                self._db.execute(
                    select(HousingReportBatch.status, func.count(HousingReportBatch.id))
                    .where(*count_conditions)
                    .group_by(HousingReportBatch.status)
                ).tuples().all()
            )
            items = self._summaries(batches, access)
            status_counts = ReportBatchStatusCounts(
                total=sum(grouped_counts.values()),
                generated=grouped_counts.get("generated", 0),
                marked_submitted=grouped_counts.get("marked_submitted", 0),
                voided=grouped_counts.get("voided", 0),
                failed=grouped_counts.get("failed", 0),
            )
            page = ReportBatchPage(
                items=items,
                total=total,
                page=query.page,
                page_size=query.page_size,
                status_counts=status_counts,
                access_revision=access.access_revision,
                filter_options=self._filter_options(access),
            )
            self._append_audit(
                action="housing.report_batch.list",
                resource_id=None,
                outcome="allowed",
                metadata={"result_count": total},
            )
            self._commit()
            return page
        except (HousingReportingError, HousingPersistenceFailure):
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise HousingPersistenceFailure("unable to list housing reports") from exc

    def get(self, batch_id: UUID) -> ReportBatchDetail:
        try:
            access = HousingAccessService(
                self._db,
                self._actor_user_id,
                self._occurred_at,
            )
            batch = self._db.scalar(
                select(HousingReportBatch).where(
                    HousingReportBatch.id == batch_id,
                    self._visible_batch_criterion(access),
                )
            )
            if batch is None:
                self._append_audit(
                    action="housing.report_batch.read",
                    resource_id=batch_id,
                    outcome="denied",
                    metadata={"error_code": "housing_resource_not_found"},
                )
                self._commit()
                raise HousingResourceNotFound("housing report batch not found")
            scopes = tuple(
                ReportBatchScopeSummary(
                    building_id=building_id,
                    building_name=building_name,
                    floor_id=floor_id,
                    floor_name=floor_name,
                )
                for building_id, building_name, floor_id, floor_name in self._db.execute(
                    select(
                        HousingReportBatchScope.building_id,
                        HousingBuilding.name,
                        HousingReportBatchScope.floor_id,
                        HousingFloor.name,
                    )
                    .join(
                        HousingBuilding,
                        HousingBuilding.id == HousingReportBatchScope.building_id,
                    )
                    .join(
                        HousingFloor,
                        HousingFloor.id == HousingReportBatchScope.floor_id,
                    )
                    .where(HousingReportBatchScope.batch_id == batch.id)
                    .order_by(
                        HousingReportBatchScope.building_id,
                        HousingReportBatchScope.floor_id,
                    )
                ).tuples()
            )
            summary = self._summary(
                batch,
                has_correction=self._has_visible_direct_correction(batch.id, access),
                visible_corrects_batch_id=self._visible_predecessor_id(batch, access),
                scopes=scopes,
            )
            correction_chain = self._correction_chain(batch, access)
            detail_values = {
                field.name: getattr(summary, field.name)
                for field in fields(ReportBatchSummary)
            }
            detail = ReportBatchDetail(
                **detail_values,
                submission_note=batch.submission_note,
                void_reason=batch.void_reason,
                failure_summary=(
                    dict(batch.failure_summary)
                    if batch.failure_summary is not None
                    else None
                ),
                correction_chain=correction_chain,
            )
            self._append_audit(
                action="housing.report_batch.read",
                resource_id=batch.id,
                outcome="allowed",
                metadata={"result_count": 1},
            )
            self._commit()
            return detail
        except (HousingReportingError, HousingPersistenceFailure, HousingResourceNotFound):
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise HousingPersistenceFailure("unable to read housing report") from exc

    def download(self, batch_id: UUID, format: ReportFormat) -> ReportDownload:
        if format not in {"xlsx", "csv"}:
            raise ValueError("unsupported housing report format")
        action = f"housing.report_batch.download.{format}"
        try:
            access = HousingAccessService(
                self._db,
                self._actor_user_id,
                self._occurred_at,
            )
            batch = self._locked_visible_batch(batch_id, access)
            if batch is None:
                self._raise_private_not_found(action=action, batch_id=batch_id)
            if not transition_allowed(batch.status, "download"):
                self._raise_state_conflict(action=action, batch_id=batch.id)
            if (
                batch.projection_schema_version != 1
                or batch.snapshot_ciphertext is None
                or batch.snapshot_key_version is None
                or batch.snapshot_digest is None
            ):
                self._raise_download_failure(
                    action=action,
                    batch_id=batch.id,
                    failure_code="housing_report_snapshot_unavailable",
                    cause=ValueError("snapshot fields are unavailable"),
                )
            try:
                projection = open_projection(
                    SealedSnapshot(
                        ciphertext=batch.snapshot_ciphertext,
                        key_version=batch.snapshot_key_version,
                        digest=batch.snapshot_digest,
                    ),
                    cipher=self._cipher,
                )
            except Exception as exc:  # noqa: BLE001 - snapshot adapter boundary
                self._raise_download_failure(
                    action=action,
                    batch_id=batch.id,
                    failure_code="housing_report_snapshot_unavailable",
                    cause=exc,
                )
            if not self._projection_matches_batch(batch, projection):
                self._raise_download_failure(
                    action=action,
                    batch_id=batch.id,
                    failure_code="housing_report_snapshot_unavailable",
                    cause=ValueError("snapshot metadata does not match batch"),
                )
            try:
                if format == "xlsx":
                    content = render_xlsx(projection)
                    media_type = (
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    )
                    extension = "xlsx"
                else:
                    content = render_csv_zip(projection)
                    media_type = "application/zip"
                    extension = "zip"
            except Exception as exc:  # noqa: BLE001 - renderer adapter boundary
                self._raise_download_failure(
                    action=action,
                    batch_id=batch.id,
                    failure_code="housing_report_render_failed",
                    cause=exc,
                )
            self._append_audit(
                action=action,
                resource_id=batch.id,
                outcome="allowed",
                metadata={"result_count": batch.stay_row_count},
            )
            safe_identifier = (
                batch.batch_number
                if _SAFE_BATCH_NUMBER.fullmatch(batch.batch_number)
                else batch.id.hex
            )
            result = ReportDownload(
                content=content,
                media_type=media_type,
                filename=f"housing-report-{safe_identifier}.{extension}",
            )
            self._commit()
            return result
        except (HousingReportingError, HousingPersistenceFailure):
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise HousingPersistenceFailure("unable to download housing report") from exc

    def mark_submitted(
        self,
        batch_id: UUID,
        command: MarkSubmittedCommand,
    ) -> ReportBatchSummary:
        if command.submitted_at > self._occurred_at:
            raise ValueError("submitted_at cannot be in the future")
        try:
            access = HousingAccessService(
                self._db,
                self._actor_user_id,
                self._occurred_at,
            )
            batch = self._locked_visible_batch(batch_id, access)
            if batch is None:
                self._raise_private_not_found(
                    action="housing.report_batch.mark_submitted",
                    batch_id=batch_id,
                )
            if batch.version != command.expected_version or not transition_allowed(
                batch.status,
                "mark_submitted",
            ):
                self._raise_state_conflict(
                    action="housing.report_batch.mark_submitted",
                    batch_id=batch.id,
                )
            batch.status = "marked_submitted"
            batch.submitted_at = command.submitted_at
            batch.submitted_by_user_id = self._actor_user_id
            batch.submission_note = command.note
            batch.version += 1
            self._db.flush()
            self._append_audit(
                action="housing.report_batch.mark_submitted",
                resource_id=batch.id,
                outcome="allowed",
                metadata={"result_count": 1},
            )
            summary = self._summary(
                batch,
                has_correction=False,
                visible_corrects_batch_id=self._visible_predecessor_id(batch, access),
            )
            self._commit()
            return summary
        except (HousingReportingError, HousingPersistenceFailure):
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise HousingPersistenceFailure("unable to submit housing report") from exc

    def void(
        self,
        batch_id: UUID,
        command: VoidReportCommand,
    ) -> ReportBatchSummary:
        try:
            access = HousingAccessService(
                self._db,
                self._actor_user_id,
                self._occurred_at,
            )
            batch = self._locked_visible_batch(batch_id, access)
            if batch is None:
                self._raise_private_not_found(
                    action="housing.report_batch.void",
                    batch_id=batch_id,
                )
            if batch.version != command.expected_version or not transition_allowed(
                batch.status,
                "void",
            ):
                self._raise_state_conflict(
                    action="housing.report_batch.void",
                    batch_id=batch.id,
                )
            batch.status = "voided"
            batch.voided_at = self._occurred_at
            batch.voided_by_user_id = self._actor_user_id
            batch.void_reason = command.reason
            batch.version += 1
            self._db.flush()
            self._append_audit(
                action="housing.report_batch.void",
                resource_id=batch.id,
                outcome="allowed",
                metadata={"result_count": 1},
            )
            summary = self._summary(
                batch,
                has_correction=False,
                visible_corrects_batch_id=self._visible_predecessor_id(batch, access),
            )
            self._commit()
            return summary
        except (HousingReportingError, HousingPersistenceFailure):
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise HousingPersistenceFailure("unable to void housing report") from exc

    def _repeatable_read_connection(self) -> Connection:
        if self._db.in_transaction():
            raise HousingPersistenceFailure(
                "housing report generation requires a fresh dedicated session"
            )
        bind = self._db.get_bind()
        if isinstance(bind, Connection) and bind.in_transaction():
            connection = self._db.connection()
        else:
            connection = self._db.connection(
                execution_options={"isolation_level": "REPEATABLE READ"}
            )
        if connection.get_isolation_level().upper() != "REPEATABLE READ":
            self._db.rollback()
            raise HousingPersistenceFailure(
                "housing report generation requires repeatable read"
            )
        return connection

    def _locked_visible_batch(
        self,
        batch_id: UUID,
        access: HousingAccessService,
    ) -> HousingReportBatch | None:
        return self._db.scalar(
            select(HousingReportBatch)
            .where(
                HousingReportBatch.id == batch_id,
                self._visible_batch_criterion(access),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    def _raise_state_conflict(self, *, action: str, batch_id: UUID) -> NoReturn:
        self._append_audit(
            action=action,
            resource_id=batch_id,
            outcome="failed",
            metadata={"error_code": "housing_report_state_conflict"},
        )
        self._commit()
        raise HousingReportStateConflict("housing report state conflict")

    def _raise_private_not_found(self, *, action: str, batch_id: UUID) -> NoReturn:
        self._append_audit(
            action=action,
            resource_id=batch_id,
            outcome="denied",
            metadata={"error_code": "housing_resource_not_found"},
        )
        self._commit()
        raise HousingResourceNotFound("housing report batch not found")

    def _raise_correction_invalid(self, batch_id: UUID) -> NoReturn:
        self._append_audit(
            action="housing.report_batch.correct",
            resource_id=batch_id,
            outcome="failed",
            metadata={"error_code": "housing_report_correction_invalid"},
        )
        self._commit()
        raise HousingReportCorrectionInvalid("housing report correction is invalid")

    def _raise_download_failure(
        self,
        *,
        action: str,
        batch_id: UUID,
        failure_code: Literal[
            "housing_report_snapshot_unavailable",
            "housing_report_render_failed",
        ],
        cause: Exception,
    ) -> NoReturn:
        self._db.rollback()
        self._append_audit(
            action=action,
            resource_id=batch_id,
            outcome="failed",
            metadata={"error_code": failure_code},
        )
        self._commit()
        if failure_code == "housing_report_snapshot_unavailable":
            raise HousingReportSnapshotUnavailable(
                "housing report snapshot unavailable"
            ) from cause
        raise HousingReportRenderFailed("housing report render failed") from cause

    def _projection_matches_batch(
        self,
        batch: HousingReportBatch,
        projection: HousingReportProjectionV1,
    ) -> bool:
        scope_floor_ids = set(
            self._db.scalars(
                select(HousingReportBatchScope.floor_id).where(
                    HousingReportBatchScope.batch_id == batch.id
                )
            )
        )
        projection_floor_ids = {row.floor_id for row in projection.residents}
        return (
            projection.schema_version == batch.projection_schema_version
            and projection.generated_at == batch.created_at
            and projection.range_start == batch.range_start
            and projection.range_end == batch.range_end
            and projection.resident_count == batch.resident_count
            and projection.stay_row_count == batch.stay_row_count
            and projection.guardian_row_count == batch.guardian_row_count
            and bool(scope_floor_ids)
            and projection_floor_ids <= scope_floor_ids
        )

    def _raise_generation_snapshot_failure(
        self,
        *,
        action: str,
        resource_id: UUID | None,
        cause: Exception,
    ) -> NoReturn:
        self._db.rollback()
        self._append_audit(
            action=action,
            resource_id=resource_id,
            outcome="failed",
            metadata={"error_code": "housing_report_snapshot_unavailable"},
        )
        self._commit()
        raise HousingReportSnapshotUnavailable(
            "housing report snapshot unavailable"
        ) from cause

    @staticmethod
    def _visible_batch_criterion(
        access: HousingAccessService,
    ) -> ColumnElement[bool]:
        has_scope = exists(
            select(HousingReportBatchScope.batch_id).where(
                HousingReportBatchScope.batch_id == HousingReportBatch.id
            )
        ).correlate(HousingReportBatch)
        has_unauthorized_scope = exists(
            select(HousingReportBatchScope.batch_id)
            .join(
                HousingFloor,
                and_(
                    HousingFloor.id == HousingReportBatchScope.floor_id,
                    HousingFloor.building_id == HousingReportBatchScope.building_id,
                ),
            )
            .where(
                HousingReportBatchScope.batch_id == HousingReportBatch.id,
                not_(access.report_floor_criteria()),
            )
        ).correlate(HousingReportBatch)
        return and_(has_scope, not_(has_unauthorized_scope))

    def _list_conditions(
        self,
        query: ReportBatchQuery,
        access: HousingAccessService,
        *,
        include_status: bool,
    ) -> builtins.list[ColumnElement[bool]]:
        conditions: builtins.list[ColumnElement[bool]] = [
            self._visible_batch_criterion(access)
        ]
        if include_status and query.statuses:
            conditions.append(HousingReportBatch.status.in_(query.statuses))
        if query.range_start is not None:
            conditions.append(HousingReportBatch.range_end > query.range_start)
        if query.range_end is not None:
            conditions.append(HousingReportBatch.range_start < query.range_end)
        if query.created_by_user_id is not None:
            conditions.append(
                HousingReportBatch.created_by_user_id == query.created_by_user_id
            )
        if query.building_ids:
            conditions.append(
                exists(
                    select(HousingReportBatchScope.batch_id).where(
                        HousingReportBatchScope.batch_id == HousingReportBatch.id,
                        HousingReportBatchScope.building_id.in_(query.building_ids),
                    )
                ).correlate(HousingReportBatch)
            )
        if query.floor_ids:
            conditions.append(
                exists(
                    select(HousingReportBatchScope.batch_id).where(
                        HousingReportBatchScope.batch_id == HousingReportBatch.id,
                        HousingReportBatchScope.floor_id.in_(query.floor_ids),
                    )
                ).correlate(HousingReportBatch)
            )
        if query.cohort_ids:
            conditions.append(
                or_(
                    *(
                        HousingReportBatch.cohort_filter.contains([str(cohort_id)])
                        for cohort_id in query.cohort_ids
                    )
                )
            )
        return conditions

    def _has_direct_correction(self, batch_id: UUID) -> bool:
        return (
            self._db.scalar(
                select(HousingReportBatch.id).where(
                    HousingReportBatch.corrects_batch_id == batch_id
                )
            )
            is not None
        )

    def _has_visible_direct_correction(
        self,
        batch_id: UUID,
        access: HousingAccessService,
    ) -> bool:
        return (
            self._db.scalar(
                select(HousingReportBatch.id).where(
                    HousingReportBatch.corrects_batch_id == batch_id,
                    self._visible_batch_criterion(access),
                )
            )
            is not None
        )

    def _visible_predecessor_id(
        self,
        batch: HousingReportBatch,
        access: HousingAccessService,
    ) -> UUID | None:
        if batch.corrects_batch_id is None:
            return None
        return self._db.scalar(
            select(HousingReportBatch.id).where(
                HousingReportBatch.id == batch.corrects_batch_id,
                self._visible_batch_criterion(access),
            )
        )

    def _summaries(
        self,
        batches: Sequence[HousingReportBatch],
        access: HousingAccessService,
    ) -> tuple[ReportBatchSummary, ...]:
        if not batches:
            return ()
        batch_ids = tuple(batch.id for batch in batches)
        scopes_by_batch: dict[UUID, builtins.list[ReportBatchScopeSummary]] = {
            batch_id: [] for batch_id in batch_ids
        }
        for (
            batch_id,
            building_id,
            building_name,
            floor_id,
            floor_name,
        ) in self._db.execute(
            select(
                HousingReportBatchScope.batch_id,
                HousingReportBatchScope.building_id,
                HousingBuilding.name,
                HousingReportBatchScope.floor_id,
                HousingFloor.name,
            )
            .join(
                HousingBuilding,
                HousingBuilding.id == HousingReportBatchScope.building_id,
            )
            .join(
                HousingFloor,
                HousingFloor.id == HousingReportBatchScope.floor_id,
            )
            .where(HousingReportBatchScope.batch_id.in_(batch_ids))
            .order_by(
                HousingReportBatchScope.batch_id,
                HousingReportBatchScope.building_id,
                HousingReportBatchScope.floor_id,
            )
        ).tuples():
            scopes_by_batch[batch_id].append(
                ReportBatchScopeSummary(
                    building_id=building_id,
                    building_name=building_name,
                    floor_id=floor_id,
                    floor_name=floor_name,
                )
            )
        visible_corrected_batch_ids = {
            batch_id
            for batch_id in self._db.scalars(
                select(HousingReportBatch.corrects_batch_id).where(
                    HousingReportBatch.corrects_batch_id.in_(batch_ids),
                    self._visible_batch_criterion(access),
                )
            )
            if batch_id is not None
        }
        predecessor_ids = tuple(
            {
                batch.corrects_batch_id
                for batch in batches
                if batch.corrects_batch_id is not None
            }
        )
        visible_predecessor_ids = (
            set(
                self._db.scalars(
                    select(HousingReportBatch.id).where(
                        HousingReportBatch.id.in_(predecessor_ids),
                        self._visible_batch_criterion(access),
                    )
                )
            )
            if predecessor_ids
            else set()
        )
        account_ids = {
            account_id
            for batch in batches
            for account_id in (
                batch.created_by_user_id,
                batch.submitted_by_user_id,
                batch.voided_by_user_id,
            )
            if account_id is not None
        }
        account_names = self._account_names(account_ids)
        return tuple(
            self._summary(
                batch,
                has_correction=batch.id in visible_corrected_batch_ids,
                visible_corrects_batch_id=(
                    batch.corrects_batch_id
                    if batch.corrects_batch_id in visible_predecessor_ids
                    else None
                ),
                scopes=tuple(scopes_by_batch[batch.id]),
                account_names=account_names,
            )
            for batch in batches
        )

    def _correction_chain(
        self,
        batch: HousingReportBatch,
        access: HousingAccessService,
    ) -> tuple[ReportBatchCorrectionLink, ...]:
        ancestors: builtins.list[HousingReportBatch] = []
        seen = {batch.id}
        cursor = batch
        while cursor.corrects_batch_id is not None:
            predecessor = self._db.scalar(
                select(HousingReportBatch).where(
                    HousingReportBatch.id == cursor.corrects_batch_id,
                    self._visible_batch_criterion(access),
                )
            )
            if predecessor is None or predecessor.id in seen:
                break
            ancestors.append(predecessor)
            seen.add(predecessor.id)
            cursor = predecessor
        descendants: builtins.list[HousingReportBatch] = []
        cursor_id = batch.id
        while True:
            successor = self._db.scalar(
                select(HousingReportBatch).where(
                    HousingReportBatch.corrects_batch_id == cursor_id,
                    self._visible_batch_criterion(access),
                )
            )
            if successor is None or successor.id in seen:
                break
            descendants.append(successor)
            seen.add(successor.id)
            cursor_id = successor.id
        return tuple(
            self._correction_link(item)
            for item in (*reversed(ancestors), batch, *descendants)
        )

    @staticmethod
    def _correction_link(batch: HousingReportBatch) -> ReportBatchCorrectionLink:
        return ReportBatchCorrectionLink(
            id=batch.id,
            batch_number=batch.batch_number,
            status=cast(ReportBatchStatus, batch.status),
            created_at=batch.created_at,
        )

    def _expand_generation_scopes(
        self,
        command: GenerateReportCommand,
        access: HousingAccessService,
    ) -> tuple[ReportBatchScopeSummary, ...]:
        floor_query = (
            select(HousingFloor, HousingBuilding.name)
            .join(HousingBuilding, HousingBuilding.id == HousingFloor.building_id)
            .where(
                HousingFloor.is_active.is_(True),
                HousingBuilding.is_active.is_(True),
            )
            .order_by(
                HousingBuilding.display_order,
                HousingBuilding.id,
                HousingFloor.display_order,
                HousingFloor.number,
                HousingFloor.id,
            )
        )
        if command.scope_mode == "authorized":
            floor_query = floor_query.where(access.report_floor_criteria())
        elif command.scope_mode == "buildings":
            active_building_ids = set(
                self._db.scalars(
                    select(HousingBuilding.id).where(
                        HousingBuilding.id.in_(command.building_ids),
                        HousingBuilding.is_active.is_(True),
                    )
                )
            )
            if active_building_ids != set(command.building_ids):
                raise HousingReportScopeDenied("housing report scope is unavailable")
            floor_query = floor_query.where(
                HousingFloor.building_id.in_(command.building_ids)
            )
        else:
            floor_query = floor_query.where(HousingFloor.id.in_(command.floor_ids))
        floor_rows = list(self._db.execute(floor_query).tuples())
        floors = [floor for floor, _building_name in floor_rows]
        explicit_floor_count_mismatch = (
            command.scope_mode == "floors" and len(floors) != len(command.floor_ids)
        )
        requested_building_missing_floor = (
            command.scope_mode == "buildings"
            and {floor.building_id for floor in floors} != set(command.building_ids)
        )
        if (
            not floors
            or explicit_floor_count_mismatch
            or requested_building_missing_floor
            or any(not access.can_export_report_floor(floor) for floor in floors)
        ):
            raise HousingReportScopeDenied("housing report scope is unavailable")
        return tuple(
            ReportBatchScopeSummary(
                building_id=floor.building_id,
                building_name=building_name,
                floor_id=floor.id,
                floor_name=floor.name,
            )
            for floor, building_name in floor_rows
        )

    def _append_audit(
        self,
        *,
        action: str,
        resource_id: UUID | None,
        outcome: Literal["allowed", "denied", "failed"],
        metadata: dict[str, object],
    ) -> None:
        try:
            AuditService(self._db).append(
                actor_user_id=self._actor_user_id,
                action=action,
                resource_type="housing_report_batch",
                resource_id=resource_id,
                purpose=None,
                outcome=outcome,
                request_id=self._request_id,
                metadata=metadata,
            )
        except Exception as exc:
            self._db.rollback()
            raise HousingAuditFailure("housing report audit failed") from exc

    @staticmethod
    def _incomplete_failure_summary(
        issues: Sequence[ProjectionIssue],
    ) -> dict[str, object]:
        field_counts = Counter(
            field_name
            for issue in issues
            for field_name in set(issue.missing_fields)
        )
        return {
            "issue_count": len(issues),
            "field_counts": dict(sorted(field_counts.items())),
        }

    def _persist_failed_generation(
        self,
        *,
        command: GenerateReportCommand,
        scopes: tuple[ReportBatchScopeSummary, ...],
        audit_action: str,
        failure_code: Literal["housing_report_empty", "housing_export_incomplete"],
        failure_summary: dict[str, object],
        issues: Sequence[ProjectionIssue],
    ) -> NoReturn:
        batch = HousingReportBatch(
            id=uuid4(),
            status="failed",
            version=1,
            range_start=command.range_start,
            range_end=command.range_end,
            cohort_filter=[str(value) for value in command.cohort_ids],
            person_type_filter=list(command.person_types),
            purpose=command.purpose,
            projection_schema_version=1,
            snapshot_ciphertext=None,
            snapshot_key_version=None,
            snapshot_digest=None,
            resident_count=0,
            stay_row_count=0,
            guardian_row_count=0,
            created_at=self._occurred_at,
            created_by_user_id=self._actor_user_id,
            # A failed projection is not a correction and must not consume the
            # predecessor's one-successor slot. The failed attempt remains a
            # durable batch and audit event, while a repaired retry may link.
            corrects_batch_id=None,
            failure_code=failure_code,
            failure_summary=dict(failure_summary),
        )
        self._db.add(batch)
        self._db.add_all(
            HousingReportBatchScope(
                batch_id=batch.id,
                building_id=scope.building_id,
                floor_id=scope.floor_id,
            )
            for scope in scopes
        )
        self._db.flush()
        self._append_audit(
            action=audit_action,
            resource_id=batch.id,
            outcome="failed",
            metadata={
                "error_code": failure_code,
                "result_count": cast(int, failure_summary["issue_count"]),
            },
        )
        self._commit()
        error_type = (
            HousingReportEmpty
            if failure_code == "housing_report_empty"
            else HousingExportIncomplete
        )
        raise error_type(batch.id, failure_summary, issues)

    def _commit(self) -> None:
        try:
            self._db.commit()
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise HousingPersistenceFailure("unable to commit housing report") from exc

    def _summary(
        self,
        batch: HousingReportBatch,
        *,
        has_correction: bool,
        visible_corrects_batch_id: UUID | None,
        scopes: tuple[ReportBatchScopeSummary, ...] | None = None,
        account_names: dict[UUID, str] | None = None,
    ) -> ReportBatchSummary:
        resolved_account_names = account_names or self._account_names(
            {
                account_id
                for account_id in (
                    batch.created_by_user_id,
                    batch.submitted_by_user_id,
                    batch.voided_by_user_id,
                )
                if account_id is not None
            }
        )
        return ReportBatchSummary(
            id=batch.id,
            batch_number=batch.batch_number,
            status=cast(ReportBatchStatus, batch.status),
            version=batch.version,
            range_start=batch.range_start,
            range_end=batch.range_end,
            cohort_filter=tuple(UUID(value) for value in batch.cohort_filter),
            person_type_filter=tuple(
                cast(ResidentPersonType, value) for value in batch.person_type_filter
            ),
            purpose=batch.purpose,
            resident_count=batch.resident_count,
            stay_row_count=batch.stay_row_count,
            guardian_row_count=batch.guardian_row_count,
            scopes=(
                scopes
                if scopes is not None
                else self._scope_summaries(batch.id)
            ),
            created_at=batch.created_at,
            created_by_user_id=batch.created_by_user_id,
            created_by_name=resolved_account_names[batch.created_by_user_id],
            submitted_at=batch.submitted_at,
            submitted_by_user_id=batch.submitted_by_user_id,
            submitted_by_name=(
                resolved_account_names[batch.submitted_by_user_id]
                if batch.submitted_by_user_id is not None
                else None
            ),
            voided_at=batch.voided_at,
            voided_by_user_id=batch.voided_by_user_id,
            voided_by_name=(
                resolved_account_names[batch.voided_by_user_id]
                if batch.voided_by_user_id is not None
                else None
            ),
            corrects_batch_id=visible_corrects_batch_id,
            failure_code=batch.failure_code,
            allowed_actions=allowed_actions(
                batch.status,
                has_correction=has_correction,
            ),
        )

    def _scope_summaries(
        self,
        batch_id: UUID,
    ) -> tuple[ReportBatchScopeSummary, ...]:
        scopes = self._db.execute(
            select(
                HousingReportBatchScope.building_id,
                HousingBuilding.name,
                HousingReportBatchScope.floor_id,
                HousingFloor.name,
            )
            .join(
                HousingBuilding,
                HousingBuilding.id == HousingReportBatchScope.building_id,
            )
            .join(
                HousingFloor,
                HousingFloor.id == HousingReportBatchScope.floor_id,
            )
            .where(HousingReportBatchScope.batch_id == batch_id)
            .order_by(
                HousingReportBatchScope.building_id,
                HousingReportBatchScope.floor_id,
            )
        ).tuples()
        return tuple(
            ReportBatchScopeSummary(
                building_id=building_id,
                building_name=building_name,
                floor_id=floor_id,
                floor_name=floor_name,
            )
            for building_id, building_name, floor_id, floor_name in scopes
        )

    def _account_names(self, account_ids: set[UUID]) -> dict[UUID, str]:
        if not account_ids:
            return {}
        return {
            account_id: (
                "敏感内容已隐藏"
                if contains_phone_or_identity(full_name)
                else full_name
            )
            for account_id, full_name in self._db.execute(
                select(UserAccount.id, Person.full_name)
                .join(Person, Person.id == UserAccount.person_id)
                .where(UserAccount.id.in_(account_ids))
            ).tuples().all()
        }

    def _filter_options(self, access: HousingAccessService) -> ReportFilterOptions:
        floor_rows = list(
            self._db.execute(
                select(
                    HousingBuilding.id,
                    HousingBuilding.name,
                    HousingFloor.id,
                    HousingFloor.name,
                )
                .join(HousingFloor, HousingFloor.building_id == HousingBuilding.id)
                .where(
                    HousingBuilding.is_active.is_(True),
                    HousingFloor.is_active.is_(True),
                    access.report_floor_criteria(),
                )
                .order_by(
                    HousingBuilding.display_order,
                    HousingBuilding.id,
                    HousingFloor.display_order,
                    HousingFloor.number,
                    HousingFloor.id,
                )
            ).tuples()
        )
        buildings_by_id: dict[UUID, ReportBuildingOption] = {}
        floors: list[ReportFloorOption] = []
        for building_id, building_name, floor_id, floor_name in floor_rows:
            buildings_by_id.setdefault(
                building_id,
                ReportBuildingOption(id=building_id, name=building_name),
            )
            floors.append(
                ReportFloorOption(
                    id=floor_id,
                    building_id=building_id,
                    name=floor_name,
                )
            )
        floor_ids = tuple(option.id for option in floors)
        cohorts: tuple[ReportCohortOption, ...] = ()
        if floor_ids:
            cohorts = tuple(
                ReportCohortOption(id=cohort_id, name=cohort_name)
                for cohort_id, cohort_name in self._db.execute(
                    select(Cohort.id, Cohort.name)
                    .join(HousingStay, HousingStay.cohort_id == Cohort.id)
                    .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
                    .where(HousingRoom.floor_id.in_(floor_ids))
                    .distinct()
                    .order_by(Cohort.name, Cohort.id)
                ).tuples()
            )
        return ReportFilterOptions(
            buildings=tuple(buildings_by_id.values()),
            floors=tuple(floors),
            cohorts=cohorts,
        )
