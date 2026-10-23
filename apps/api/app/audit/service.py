from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.core.privacy import (
    contains_email,
    contains_labeled_sensitive_value,
    contains_phone_or_identity,
    contains_sensitive_request_id,
    looks_like_raw_secret,
)
from app.operations.cloud_auth_errors import SAFE_ERROR_CODES as _CLOUD_AUTH_ERROR_CODES

AuditOutcome = Literal["allowed", "denied", "failed"]

_PUBLIC_METADATA_KEYS = frozenset(
    {
        "session_id",
        "operation_key",
        "allocation_old_status",
        "allocation_new_status",
        "operation_id",
        "membership_id",
        "closed_membership_id",
        "assignment_id",
        "organization_id",
        "source_kind",
        "target_account_id",
        "source_team_id",
        "target_team_id",
        "suite_id",
        "catalog_version",
        "scoring_version",
        "field",
        "error_code",
        "result_count",
        "role_key",
        "permission_key",
        "effect",
        "scope_kind",
        "scope_id",
        "student_person_id",
        "coach_person_id",
        "guardian_person_id",
        "relationship_id",
        "project_id",
        "program_id",
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
        "payment_preference",
        "client_id",
        "login_request_id",
        "game_account_id",
        "auxiliary_phone_id",
        "staff_role_id",
        "staff_assignment_id",
        "analysis_profile_id",
        "media_id",
        "media_blob_id",
        "upload_session_id",
        "task_id",
        "attempt_id",
        "result_version_id",
        "report_id",
        "adapter_certification_id",
        "idempotency_key_id",
        "batch_id",
        "building_id",
        "floor_id",
        "room_id",
        "photo_id",
        "version",
        "slot",
        "stay_id",
        "issue_id",
        "transfer_id",
        "resident_person_id",
        "source_room_id",
        "target_room_id",
        "person_id",
        "profile_version",
        "old_version",
        "new_version",
        "cuisine_count",
        "dish_count",
        "population",
        "include_inactive",
        "person_count",
        "filled_count",
        "file_sha256",
        "source",
        "analysis_mode",
        "task_status",
        "attempt_status",
        "review_decision",
        "evaluation_action",
        "task_version",
        "attempt",
        "result_version",
        "report_kind",
        "report_period_start",
        "report_status",
        "report_version",
        "report_window_kind",
        "media_count",
        "adapter_version",
        "scoring_rule_version",
        "result_schema_version",
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
        "export_scope",
        "settlement_center_id",
        "staff_person_id",
        "identity_mode",
        "snapshot_version",
        "item_count",
        "application_id",
        "purpose",
        "state",
        "identifier_kind",
        "schedule_mode",
        "is_reimbursed",
        "uses_reserve_fund",
        "custom_starts_at",
        "custom_ends_at",
        "effective_starts_at",
        "effective_ends_at",
        "conflict_code",
        "changed_fields",
        "old_nickname",
        "new_nickname",
        "previous_duration_mode",
        "duration_mode",
        "previous_planned_check_out_at",
        "planned_check_out_at",
        "previous_daily_cleaning_enabled",
        "daily_cleaning_enabled",
    }
)
_PUBLIC_FIELDS = frozenset({"phone", "identity_number"})
_PUBLIC_ERROR_CODES = frozenset(
    {
        "decrypt_failed",
        "assisted_evidence_unavailable",
        "minor_guardian_required",
        "verified_phone_required",
        "verified_contact_required",
        "verified_email_required",
        "permission_denied",
        "housing_resource_not_found",
        "housing_duplicate_catalog_entry",
        "housing_parent_inactive",
        "housing_catalog_has_children",
        "housing_invalid_room_configuration",
        "housing_issue_workflow_required",
        "housing_stale_version",
        "housing_void_operation_conflict",
        "housing_stay_already_voided",
        "housing_void_reason_invalid",
        "housing_account_required",
        "housing_profile_incomplete",
        "housing_room_not_verified",
        "housing_room_unavailable",
        "housing_room_pending_cleaning",
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
        "evaluation_resource_not_found",
        "evaluation_permission_denied",
        "evaluation_invalid_transition",
        "evaluation_stale_version",
        "evaluation_idempotency_conflict",
        "evaluation_lease_invalid",
        "evaluation_adapter_not_supported",
        "evaluation_adapter_certification_conflict",
        "evaluation_result_invalid",
        "evaluation_review_conflict",
        "validation_error",
    }
)
_PUBLIC_ERROR_CODES |= _CLOUD_AUTH_ERROR_CODES
_PUBLIC_EFFECTS = frozenset({"allow", "deny"})
_PUBLIC_SCOPE_KINDS = frozenset(
    {
        "global",
        "self",
        "family",
        "person",
        "assigned_class",
        "assigned_team",
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
_PUBLIC_CAMP_SURVEY_IDENTITY_MODES = frozenset({"hidden", "real_name"})
_PUBLIC_EVALUATION_TASK_STATUSES = frozenset(
    {
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
    }
)
_PUBLIC_EVALUATION_ATTEMPT_STATUSES = frozenset({"active", "succeeded", "failed", "expired"})
_PUBLIC_EVALUATION_REVIEW_DECISIONS = frozenset({"pending", "approved", "rejected"})
_PUBLIC_TEACHING_REPORT_KINDS = frozenset({"daily", "weekly"})
_PUBLIC_TEACHING_REPORT_STATUSES = frozenset(
    {
        "draft",
        "pending_review",
        "rejected",
        "correction_draft",
        "published",
        "revision_pending_review",
    }
)
_PUBLIC_EVALUATION_ACTIONS = frozenset(
    {
        "create",
        "identify",
        "confirm_match",
        "queue",
        "claim",
        "renew",
        "submit_result",
        "record_failure",
        "save_draft",
        "manual_submit",
        "submit",
        "approve",
        "reject",
        "publish",
        "archive",
        "reopen",
        "create_correction",
        "create_revision",
        "certify",
    }
)
_FORBIDDEN_KEY_PARTS = (
    "phone",
    "identity",
    "password",
    "verification_code",
    "identifier",
    "email",
    "cookie",
    "session",
    "token",
    "csrf",
    "ciphertext",
)
_SAFE_SENSITIVE_IDENTIFIER_KEYS = frozenset(
    {
        "session_id",
        "auxiliary_phone_id",
        "upload_session_id",
        "identity_mode",
        "identifier_kind",
    }
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|temporary[_ -]?password|token|bearer|csrf|cipher(?:text)?|"
    r"密码|临时密码|令牌)\s*(?::|：|=|\s)\s*\S{8,}"
)
_SAFE_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,119}\Z")
_SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z")
_CANONICAL_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_POSTGRESQL_INTEGER_MAX = 2_147_483_647
_PUBLIC_REGISTRATION_PURPOSES = frozenset(
    {
        "student",
        "guardian",
        "staff_applicant",
        "visitor",
        "registration",
        "password_reset",
        "guardian_invitation",
        "add_login_identifier",
    }
)
_PUBLIC_REGISTRATION_STATES = frozenset(
    {
        "draft",
        "contact_verified",
        "identity_completed",
        "pending_guardian",
        "ready_to_finalize",
        "finalized",
        "conflict",
        "rejected",
        "expired",
    }
)
_PUBLIC_REGISTRATION_IDENTIFIER_KINDS = frozenset({"phone", "email"})
_PUBLIC_REGISTRATION_CONFLICT_CODES = frozenset(
    {
        "deleted_person_match",
        "multiple_person_match",
        "split_identifier_match",
        "contact_already_used",
        "identity_profile_mismatch",
        "invalid_person_contact",
        "contact_mismatch",
        "purpose_conflict",
        "concurrent_claim",
        "review_rejected",
    }
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
            or contains_email(value)
            or _SECRET_ASSIGNMENT.search(value)
            or contains_labeled_sensitive_value(value)
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
            # The allowlisted digest is typed data, not free-form text. Random
            # canonical hashes can contain phone-like runs of decimal digits.
            if not (
                key == "file_sha256" and isinstance(item, str) and _LOWER_SHA256.fullmatch(item)
            )
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_value(item) for item in value)
    return False


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def validate_metadata(metadata: dict[str, Any]) -> None:
        if _contains_sensitive_value(metadata):
            raise AuditMetadataRejected("audit metadata contains sensitive data")
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
        changed_fields = metadata.get("changed_fields")
        if (
            isinstance(changed_fields, list)
            and 1 <= len(changed_fields) <= 2
            and all(isinstance(value, str) for value in changed_fields)
            and len(set(changed_fields)) == len(changed_fields)
            and set(changed_fields) <= {"nickname", "profile_notes"}
        ):
            safe["changed_fields"] = changed_fields
        for key in ("old_nickname", "new_nickname"):
            if key not in metadata:
                continue
            value = metadata[key]
            if value is None or (
                isinstance(value, str)
                and 1 <= len(value) <= 40
                and value == value.strip()
                and not _contains_sensitive_value(value)
            ):
                safe[key] = value
        for key in ("previous_duration_mode", "duration_mode"):
            value = metadata.get(key)
            if isinstance(value, str) and value in {"fixed_term", "long_term"}:
                safe[key] = value
        for key in ("previous_planned_check_out_at", "planned_check_out_at"):
            if key not in metadata:
                continue
            value = metadata[key]
            if value is None:
                safe[key] = None
            elif isinstance(value, str):
                try:
                    parsed_departure = datetime.fromisoformat(value)
                except ValueError:
                    continue
                if parsed_departure.tzinfo is not None and parsed_departure.utcoffset() is not None:
                    safe[key] = parsed_departure.isoformat()
        for key, versions in (
            (
                "catalog_version",
                frozenset(
                    {
                        "talent-training-v1",
                        "talent-training-v2",
                        "talent-training-v3",
                        "pc-fps-v1",
                        "pc-fps-v2",
                        "pc-fps-v3",
                        "mobile-fps-v1",
                        "mobile-fps-v2",
                        "mobile-fps-v3",
                        "pc-moba-v1",
                        "pc-moba-v2",
                        "pc-moba-v3",
                        "mobile-moba-v1",
                        "mobile-moba-v2",
                        "mobile-moba-v3",
                    }
                ),
            ),
            (
                "scoring_version",
                frozenset(
                    {
                        "talent-score-v1",
                        "talent-score-v2",
                        "talent-score-v3",
                        "pc-fps-v1",
                        "pc-fps-v2",
                        "mobile-fps-v1",
                        "mobile-fps-v2",
                        "pc-moba-v1",
                        "pc-moba-v2",
                        "mobile-moba-v1",
                        "mobile-moba-v2",
                    }
                ),
            ),
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value in versions:
                safe[key] = value
        suite_id = metadata.get("suite_id")
        if isinstance(suite_id, str) and suite_id in {
            "pc-fps",
            "mobile-fps",
            "pc-moba",
            "mobile-moba",
        }:
            safe["suite_id"] = suite_id
        field = metadata.get("field")
        if isinstance(field, str) and field in _PUBLIC_FIELDS:
            safe["field"] = field
        error_code = metadata.get("error_code")
        if isinstance(error_code, str) and error_code in _PUBLIC_ERROR_CODES:
            safe["error_code"] = error_code
        result_count = metadata.get("result_count")
        source_kind = metadata.get("source_kind")
        if isinstance(source_kind, str) and source_kind in {"all", "person", "organization"}:
            safe["source_kind"] = source_kind
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
            "session_id",
            "operation_key",
            "operation_id",
            "membership_id",
            "closed_membership_id",
            "assignment_id",
            "organization_id",
            "target_account_id",
            "source_team_id",
            "target_team_id",
            "student_person_id",
            "coach_person_id",
            "guardian_person_id",
            "relationship_id",
            "project_id",
            "program_id",
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
            "upload_session_id",
            "task_id",
            "attempt_id",
            "result_version_id",
            "report_id",
            "adapter_certification_id",
            "idempotency_key_id",
            "batch_id",
            "building_id",
            "floor_id",
            "room_id",
            "photo_id",
            "stay_id",
            "issue_id",
            "transfer_id",
            "resident_person_id",
            "source_room_id",
            "target_room_id",
            "person_id",
            "application_id",
            "settlement_center_id",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and _CANONICAL_UUID.fullmatch(value):
                safe[key] = str(UUID(value))
        scope_ids = _public_uuid_list(metadata.get("scope_ids"), max_length=500)
        if scope_ids is not None:
            safe["scope_ids"] = scope_ids
        for key in (
            "resident_count",
            "stay_row_count",
            "guardian_row_count",
            "person_count",
            "filled_count",
        ):
            value = metadata.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe[key] = value
        for key in ("old_version", "new_version", "version"):
            value = metadata.get(key)
            minimum = 0 if key == "old_version" else 1
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and minimum <= value <= _POSTGRESQL_INTEGER_MAX
            ):
                safe[key] = value
        slot = metadata.get("slot")
        if isinstance(slot, int) and not isinstance(slot, bool) and 1 <= slot <= 3:
            safe["slot"] = slot
        for key in ("cuisine_count", "dish_count"):
            value = metadata.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3:
                safe[key] = value
        population = metadata.get("population")
        if isinstance(population, str) and population in {"students", "all_people"}:
            safe["population"] = population
        for key in ("previous_daily_cleaning_enabled", "daily_cleaning_enabled"):
            if isinstance(metadata.get(key), bool):
                safe[key] = metadata[key]
        include_inactive = metadata.get("include_inactive")
        if isinstance(include_inactive, bool):
            safe["include_inactive"] = include_inactive
        is_reimbursed = metadata.get("is_reimbursed")
        if isinstance(is_reimbursed, bool):
            safe["is_reimbursed"] = is_reimbursed
        uses_reserve_fund = metadata.get("uses_reserve_fund")
        if isinstance(uses_reserve_fund, bool):
            safe["uses_reserve_fund"] = uses_reserve_fund
        file_sha256 = metadata.get("file_sha256")
        if isinstance(file_sha256, str) and _LOWER_SHA256.fullmatch(file_sha256):
            safe["file_sha256"] = file_sha256
        source = metadata.get("source")
        if isinstance(source, str) and source in {
            "profile_self",
            "profile_guardian",
            "profile_admin",
            "camp_survey",
        }:
            safe["source"] = source
        report_format = metadata.get("format")
        if isinstance(report_format, str) and report_format in {
            "xlsx",
            "csv",
            "csv_zip",
        }:
            safe["format"] = report_format
        export_scope = metadata.get("export_scope")
        if isinstance(export_scope, str) and export_scope in {
            "current",
            "all",
            "unreimbursed",
            "reimbursed",
            "unsettled",
            "settled",
        }:
            safe["export_scope"] = export_scope
        staff_person_id = metadata.get("staff_person_id")
        if isinstance(staff_person_id, str) and _CANONICAL_UUID.fullmatch(staff_person_id):
            safe["staff_person_id"] = str(UUID(staff_person_id))
        identity_mode = metadata.get("identity_mode")
        if isinstance(identity_mode, str) and identity_mode in _PUBLIC_CAMP_SURVEY_IDENTITY_MODES:
            safe["identity_mode"] = identity_mode
        snapshot_version = metadata.get("snapshot_version")
        if (
            isinstance(snapshot_version, int)
            and not isinstance(snapshot_version, bool)
            and 1 <= snapshot_version <= _POSTGRESQL_INTEGER_MAX
        ):
            safe["snapshot_version"] = snapshot_version
        item_count = metadata.get("item_count")
        if (
            isinstance(item_count, int)
            and not isinstance(item_count, bool)
            and 0 <= item_count <= _POSTGRESQL_INTEGER_MAX
        ):
            safe["item_count"] = item_count
        registration_purpose = metadata.get("purpose")
        if (
            isinstance(registration_purpose, str)
            and registration_purpose in _PUBLIC_REGISTRATION_PURPOSES
        ):
            safe["purpose"] = registration_purpose
        registration_state = metadata.get("state")
        if (
            isinstance(registration_state, str)
            and registration_state in _PUBLIC_REGISTRATION_STATES
        ):
            safe["state"] = registration_state
        identifier_kind = metadata.get("identifier_kind")
        if (
            isinstance(identifier_kind, str)
            and identifier_kind in _PUBLIC_REGISTRATION_IDENTIFIER_KINDS
        ):
            safe["identifier_kind"] = identifier_kind
        conflict_code = metadata.get("conflict_code")
        if isinstance(conflict_code, str) and conflict_code in _PUBLIC_REGISTRATION_CONFLICT_CODES:
            safe["conflict_code"] = conflict_code
        schedule_mode = metadata.get("schedule_mode")
        if isinstance(schedule_mode, str) and schedule_mode in {"cohort", "custom"}:
            safe["schedule_mode"] = schedule_mode
        for key in (
            "custom_starts_at",
            "custom_ends_at",
            "effective_starts_at",
            "effective_ends_at",
        ):
            value = metadata.get(key)
            if not isinstance(value, str):
                continue
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                continue
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                safe[key] = parsed.isoformat()
        mode = metadata.get("mode")
        if isinstance(mode, str) and mode in {"standalone", "embedded"}:
            safe["mode"] = mode
        payment_preference = metadata.get("payment_preference")
        if payment_preference == "offline":
            safe["payment_preference"] = payment_preference
        analysis_mode = metadata.get("analysis_mode")
        if isinstance(analysis_mode, str) and analysis_mode in {
            "manual_only",
            "automatic",
        }:
            safe["analysis_mode"] = analysis_mode
        task_status = metadata.get("task_status")
        if isinstance(task_status, str) and task_status in _PUBLIC_EVALUATION_TASK_STATUSES:
            safe["task_status"] = task_status
        attempt_status = metadata.get("attempt_status")
        if (
            isinstance(attempt_status, str)
            and attempt_status in _PUBLIC_EVALUATION_ATTEMPT_STATUSES
        ):
            safe["attempt_status"] = attempt_status
        review_decision = metadata.get("review_decision")
        if (
            isinstance(review_decision, str)
            and review_decision in _PUBLIC_EVALUATION_REVIEW_DECISIONS
        ):
            safe["review_decision"] = review_decision
        report_kind = metadata.get("report_kind")
        if isinstance(report_kind, str) and report_kind in _PUBLIC_TEACHING_REPORT_KINDS:
            safe["report_kind"] = report_kind
        report_period_start = metadata.get("report_period_start")
        if isinstance(report_period_start, str):
            try:
                parsed_report_period_start = date.fromisoformat(report_period_start)
            except ValueError:
                parsed_report_period_start = None
            if (
                parsed_report_period_start is not None
                and parsed_report_period_start.isoformat() == report_period_start
            ):
                safe["report_period_start"] = report_period_start
        report_status = metadata.get("report_status")
        if isinstance(report_status, str) and report_status in _PUBLIC_TEACHING_REPORT_STATUSES:
            safe["report_status"] = report_status
        report_window_kind = metadata.get("report_window_kind")
        if isinstance(report_window_kind, str) and report_window_kind in {"correction", "revision"}:
            safe["report_window_kind"] = report_window_kind
        evaluation_action = metadata.get("evaluation_action")
        if isinstance(evaluation_action, str) and evaluation_action in _PUBLIC_EVALUATION_ACTIONS:
            safe["evaluation_action"] = evaluation_action
        for key in ("task_version", "result_version", "report_version"):
            value = metadata.get(key)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 1 <= value <= _POSTGRESQL_INTEGER_MAX
            ):
                safe[key] = value
        attempt = metadata.get("attempt")
        if isinstance(attempt, int) and not isinstance(attempt, bool) and 1 <= attempt <= 3:
            safe["attempt"] = attempt
        media_count = metadata.get("media_count")
        if (
            isinstance(media_count, int)
            and not isinstance(media_count, bool)
            and 0 <= media_count <= 3
        ):
            safe["media_count"] = media_count
        for key in (
            "adapter_version",
            "scoring_rule_version",
            "result_schema_version",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and _SAFE_VERSION.fullmatch(value):
                safe[key] = value
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
        for key in ("allocation_old_status", "allocation_new_status"):
            value = metadata.get(key)
            if isinstance(value, str) and value in {
                "untracked",
                "pending",
                "assigned",
                "not_required",
            }:
                safe[key] = value
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
        occurred_at: datetime | None = None,
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
        if occurred_at is not None:
            event.created_at = occurred_at
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
