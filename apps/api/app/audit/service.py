from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.core.privacy import (
    contains_phone_or_identity,
    contains_sensitive_request_id,
    looks_like_raw_secret,
)

AuditOutcome = Literal["allowed", "denied", "failed"]

_PUBLIC_METADATA_KEYS = frozenset(
    {
        "field",
        "error_code",
        "result_count",
        "role_key",
        "permission_key",
        "effect",
        "scope_kind",
        "scope_id",
        "student_person_id",
        "guardian_person_id",
        "relationship_id",
        "project_id",
        "professional_team_id",
        "cohort_id",
        "class_id",
        "admission_case_id",
        "enrollment_id",
        "payment_id",
        "refund_id",
        "withdrawal_id",
        "service_account_id",
        "credential_id",
        "payment_conflict_id",
        "import_event_id",
        "mode",
        "client_id",
        "login_request_id",
        "game_account_id",
        "auxiliary_phone_id",
        "staff_role_id",
        "staff_assignment_id",
        "analysis_profile_id",
        "media_id",
        "media_blob_id",
        "batch_id",
        "building_id",
        "floor_id",
        "room_id",
        "stay_id",
        "issue_id",
        "transfer_id",
        "resident_person_id",
        "source_room_id",
        "target_room_id",
        "profile_version",
        "analysis_mode",
        "old_limit",
        "new_limit",
        "refund_type",
        "old_status",
        "new_status",
        "transition_trigger",
        "affected_account_count",
        "scope_ids",
        "resident_count",
        "stay_row_count",
        "guardian_row_count",
        "format",
    }
)
_PUBLIC_FIELDS = frozenset({"phone", "identity_number"})
_PUBLIC_ERROR_CODES = frozenset(
    {
        "decrypt_failed",
        "minor_guardian_required",
        "permission_denied",
        "housing_resource_not_found",
        "housing_duplicate_catalog_entry",
        "housing_parent_inactive",
        "housing_catalog_has_children",
        "housing_invalid_room_configuration",
        "housing_issue_workflow_required",
        "housing_stale_version",
        "housing_account_required",
        "housing_profile_incomplete",
        "housing_room_not_verified",
        "housing_room_unavailable",
        "housing_capacity_conflict",
        "housing_gender_conflict",
        "housing_person_time_conflict",
        "housing_checkout_confirmation_required",
        "housing_room_has_active_stays",
        "housing_invalid_stay_transition",
        "housing_open_issue_exists",
        "housing_issue_not_open",
        "housing_report_empty",
        "housing_export_incomplete",
        "housing_report_scope_denied",
        "housing_report_state_conflict",
        "housing_report_correction_invalid",
        "housing_report_snapshot_unavailable",
        "housing_report_render_failed",
        "housing_audit_failed",
        "housing_persistence_failure",
        "housing_service_failure",
        "validation_error",
    }
)
_PUBLIC_EFFECTS = frozenset({"allow", "deny"})
_PUBLIC_SCOPE_KINDS = frozenset(
    {
        "global",
        "self",
        "family",
        "person",
        "assigned_class",
        "cohort",
        "project",
        "enrollment",
        "building",
        "floor",
    }
)
_PUBLIC_COHORT_STATUSES = frozenset(
    {"draft", "open", "upcoming", "active", "completed", "archived"}
)
_PUBLIC_TRANSITION_TRIGGERS = frozenset({"manual", "scheduler", "request_reconciliation"})
_PUBLIC_REFUND_TYPES = frozenset({"manual", "withdrawal_prorated", "withdrawal_full"})
_FORBIDDEN_KEY_PARTS = (
    "phone",
    "identity",
    "password",
    "session",
    "token",
    "csrf",
    "ciphertext",
)
_SAFE_SENSITIVE_IDENTIFIER_KEYS = frozenset({"auxiliary_phone_id"})
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|temporary[_ -]?password|token|bearer|csrf|cipher(?:text)?|"
    r"密码|临时密码|令牌)\s*(?::|：|=|\s)\s*\S{8,}"
)
_SAFE_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,119}\Z")
_CANONICAL_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


def _public_uuid_list(value: object, *, max_length: int) -> list[str] | None:
    if not isinstance(value, list) or len(value) > max_length:
        return None
    canonical: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _CANONICAL_UUID.fullmatch(item):
            return None
        canonical.append(str(UUID(item)))
    if len(canonical) != len(set(canonical)):
        return None
    return canonical


class AuditMetadataRejected(ValueError):
    pass


def _contains_sensitive_value(value: object) -> bool:
    if isinstance(value, str):
        if _CANONICAL_UUID.fullmatch(value):
            return False
        return bool(
            contains_phone_or_identity(value)
            or _SECRET_ASSIGNMENT.search(value)
            or looks_like_raw_secret(value)
        )
    if isinstance(value, dict):
        return any(
            (
                str(key) not in _SAFE_SENSITIVE_IDENTIFIER_KEYS
                and any(part in str(key).lower() for part in _FORBIDDEN_KEY_PARTS)
            )
            or _contains_sensitive_value(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_value(item) for item in value)
    return False


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def validate_metadata(metadata: dict[str, Any]) -> None:
        if any(key not in _PUBLIC_METADATA_KEYS for key in metadata):
            raise AuditMetadataRejected("audit metadata key is not allowlisted")
        if AuditService.public_metadata(metadata) != metadata:
            raise AuditMetadataRejected("audit metadata contains sensitive data")

    @staticmethod
    def validate_purpose(purpose: str | None) -> None:
        if purpose is not None and _contains_sensitive_value(purpose):
            raise AuditMetadataRejected("audit purpose contains sensitive data")

    @staticmethod
    def public_purpose(purpose: object) -> str | None:
        if not isinstance(purpose, str):
            return None
        return "敏感内容已隐藏" if _contains_sensitive_value(purpose) else purpose

    @staticmethod
    def public_metadata(metadata: object) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        safe: dict[str, Any] = {}
        field = metadata.get("field")
        if isinstance(field, str) and field in _PUBLIC_FIELDS:
            safe["field"] = field
        error_code = metadata.get("error_code")
        if isinstance(error_code, str) and error_code in _PUBLIC_ERROR_CODES:
            safe["error_code"] = error_code
        result_count = metadata.get("result_count")
        if (
            isinstance(result_count, int)
            and not isinstance(result_count, bool)
            and result_count >= 0
        ):
            safe["result_count"] = result_count
        role_key = metadata.get("role_key")
        if isinstance(role_key, str) and _SAFE_IDENTIFIER.fullmatch(role_key):
            safe["role_key"] = role_key
        permission_key = metadata.get("permission_key")
        if isinstance(permission_key, str) and _SAFE_IDENTIFIER.fullmatch(permission_key):
            safe["permission_key"] = permission_key
        effect = metadata.get("effect")
        if isinstance(effect, str) and effect in _PUBLIC_EFFECTS:
            safe["effect"] = effect
        scope_kind = metadata.get("scope_kind")
        if isinstance(scope_kind, str) and scope_kind in _PUBLIC_SCOPE_KINDS:
            safe["scope_kind"] = scope_kind
        scope_id = metadata.get("scope_id")
        if isinstance(scope_id, str) and _CANONICAL_UUID.fullmatch(scope_id):
            safe["scope_id"] = str(UUID(scope_id))
        for key in (
            "student_person_id",
            "guardian_person_id",
            "relationship_id",
            "project_id",
            "professional_team_id",
            "cohort_id",
            "class_id",
            "admission_case_id",
            "enrollment_id",
            "payment_id",
            "refund_id",
            "withdrawal_id",
            "service_account_id",
            "credential_id",
            "payment_conflict_id",
            "import_event_id",
            "login_request_id",
            "game_account_id",
            "auxiliary_phone_id",
            "staff_role_id",
            "staff_assignment_id",
            "analysis_profile_id",
            "media_id",
            "media_blob_id",
            "batch_id",
            "building_id",
            "floor_id",
            "room_id",
            "stay_id",
            "issue_id",
            "transfer_id",
            "resident_person_id",
            "source_room_id",
            "target_room_id",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and _CANONICAL_UUID.fullmatch(value):
                safe[key] = str(UUID(value))
        scope_ids = _public_uuid_list(metadata.get("scope_ids"), max_length=500)
        if scope_ids is not None:
            safe["scope_ids"] = scope_ids
        for key in ("resident_count", "stay_row_count", "guardian_row_count"):
            value = metadata.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe[key] = value
        report_format = metadata.get("format")
        if isinstance(report_format, str) and report_format in {"xlsx", "csv"}:
            safe["format"] = report_format
        mode = metadata.get("mode")
        if isinstance(mode, str) and mode in {"standalone", "embedded"}:
            safe["mode"] = mode
        analysis_mode = metadata.get("analysis_mode")
        if isinstance(analysis_mode, str) and analysis_mode in {
            "manual_only",
            "automatic",
        }:
            safe["analysis_mode"] = analysis_mode
        profile_version = metadata.get("profile_version")
        if (
            isinstance(profile_version, int)
            and not isinstance(profile_version, bool)
            and profile_version >= 1
        ):
            safe["profile_version"] = profile_version
        for key in ("old_limit", "new_limit"):
            value = metadata.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 3:
                safe[key] = value
        client_id = metadata.get("client_id")
        if client_id == "game-score-tool":
            safe["client_id"] = client_id
        for key in ("old_status", "new_status"):
            value = metadata.get(key)
            if isinstance(value, str) and value in _PUBLIC_COHORT_STATUSES:
                safe[key] = value
        transition_trigger = metadata.get("transition_trigger")
        if (
            isinstance(transition_trigger, str)
            and transition_trigger in _PUBLIC_TRANSITION_TRIGGERS
        ):
            safe["transition_trigger"] = transition_trigger
        refund_type = metadata.get("refund_type")
        if isinstance(refund_type, str) and refund_type in _PUBLIC_REFUND_TYPES:
            safe["refund_type"] = refund_type
        affected_account_count = metadata.get("affected_account_count")
        if (
            isinstance(affected_account_count, int)
            and not isinstance(affected_account_count, bool)
            and affected_account_count >= 0
        ):
            safe["affected_account_count"] = affected_account_count
        if _contains_sensitive_value(safe):
            return {}
        return safe

    def append(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        resource_type: str,
        resource_id: UUID | None,
        purpose: str | None,
        outcome: AuditOutcome,
        request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        safe_metadata = metadata or {}
        self.validate_metadata(safe_metadata)
        self.validate_purpose(purpose)
        if contains_sensitive_request_id(request_id):
            raise AuditMetadataRejected("audit request id contains sensitive data")
        event = AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=purpose,
            outcome=outcome,
            request_id=request_id,
            event_metadata=safe_metadata,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def list_events(
        self,
        *,
        actor_user_id: UUID | None,
        action: str | None,
        resource_type: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AuditEvent], int]:
        query = select(AuditEvent)
        if actor_user_id is not None:
            query = query.where(AuditEvent.actor_user_id == actor_user_id)
        if action is not None:
            query = query.where(AuditEvent.action == action)
        if resource_type is not None:
            query = query.where(AuditEvent.resource_type == resource_type)
        if date_from is not None:
            query = query.where(AuditEvent.created_at >= date_from)
        if date_to is not None:
            query = query.where(AuditEvent.created_at < date_to)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        events = list(
            self.db.scalars(
                query.order_by(AuditEvent.created_at.desc(), AuditEvent.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return events, total
