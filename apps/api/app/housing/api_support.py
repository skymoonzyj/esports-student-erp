from __future__ import annotations

import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, NoReturn
from uuid import UUID

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.types import Message, Receive, Scope, Send

from app.audit.service import AuditMetadataRejected, AuditOutcome, AuditService
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.privacy import contains_phone_or_identity
from app.housing.errors import (
    HousingExportIncomplete,
    HousingPersistenceFailure,
    HousingReportEmpty,
    HousingReportingError,
    HousingResourceNotFound,
    HousingServiceError,
    HousingVerifiedEmailRequired,
)

_KNOWN_HOUSING_CONFLICT_CODES = frozenset(
    {
        "decrypt_failed",
        "housing_duplicate_catalog_entry",
        "housing_parent_inactive",
        "housing_catalog_has_children",
        "housing_invalid_room_configuration",
        "housing_issue_workflow_required",
        "housing_stale_version",
        "housing_account_required",
        "housing_profile_incomplete",
        "minor_guardian_required",
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
    }
)

_REPORTING_ERROR_PROBLEMS: dict[str, tuple[int, str]] = {
    "housing_report_empty": (422, "报送范围内没有符合条件的入住记录"),
    "housing_export_incomplete": (422, "报送资料不完整，请补齐后重试"),
    "housing_report_scope_denied": (403, "没有所选报送范围的权限"),
    "housing_report_state_conflict": (409, "报送批次状态已发生变化"),
    "housing_report_correction_invalid": (409, "报送更正关系无效"),
    "housing_report_snapshot_unavailable": (503, "报送快照暂时不可用"),
    "housing_report_render_failed": (503, "报送文件生成失败，请稍后重试"),
    "housing_audit_failed": (500, "房态审计记录失败"),
}
_SAFE_MASKED_REPORT_PHONE = re.compile(r"1[0-9]{2}\*{4}[0-9]{4}\Z")
_SAFE_REPORT_MISSING_FIELDS = frozenset(
    {
        "phone",
        "identity_number",
        "sex",
        "person_type",
        "full_name",
        "building",
        "floor",
        "room",
        "stay_purpose",
        "cohort",
        "visit_reason",
        "actual_check_out_at",
        "stay_status",
        "guardian_relationship",
        "guardian.phone",
        "guardian.identity_number",
        "guardian.sex",
        "guardian.full_name",
        "guardian.age",
    }
)


def _report_generation_errors(
    exc: HousingReportEmpty | HousingExportIncomplete,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for issue in exc.issues:
        person_name = (
            "敏感内容已隐藏"
            if contains_phone_or_identity(issue.person_name)
            else issue.person_name
        )
        masked_phone = (
            issue.masked_phone
            if issue.masked_phone is not None
            and _SAFE_MASKED_REPORT_PHONE.fullmatch(issue.masked_phone)
            else ""
        )
        missing_fields = tuple(
            field
            for field in issue.missing_fields
            if field in _SAFE_REPORT_MISSING_FIELDS
        )
        missing_fields_text = ",".join(missing_fields) or "unknown"
        message_fields = "、".join(missing_fields) or "unknown"
        phone_context = masked_phone or "手机号未提供"
        errors.append(
            {
                "field": f"report_issue.{issue.person_id}",
                "message": (
                    f"{person_name}（{phone_context}，住宿 {issue.stay_id}）"
                    f"缺少：{message_fields}"
                ),
                "batch_id": str(exc.batch_id),
                "person_id": str(issue.person_id),
                "person_name": person_name,
                "masked_phone": masked_phone,
                "stay_id": str(issue.stay_id),
                "missing_fields": missing_fields_text,
            }
        )
    return errors or [
        {
            "field": "report_batch",
            "message": f"已记录失败批次 {exc.batch_id}",
            "batch_id": str(exc.batch_id),
        }
    ]


class LockedHousingAuthorizationDenied(Exception):
    def __init__(self, *, not_found: bool) -> None:
        self.not_found = not_found
        super().__init__("locked housing authorization denied")


@dataclass
class SensitiveHousingReadAudit:
    db: Session
    context: AuthContext
    action: str
    resource_type: str = "housing_request"
    resource_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class PrivateHousingRoute(APIRoute):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope.setdefault("state", {})["private_portal_response"] = True

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store, private"
            await send(message)

        await super().handle(scope, receive, send_private)

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            request.state.private_portal_response = True
            try:
                response = await original(request)
                _audit_sensitive_read(request, outcome="allowed")
            except RequestValidationError:
                if _sensitive_read_audit(request) is not None:
                    _audit_sensitive_read(
                        request,
                        outcome="failed",
                        error_code="validation_error",
                    )
                else:
                    db = getattr(request.state, "housing_mutation_db", None)
                    context = getattr(request.state, "housing_mutation_context", None)
                    action = getattr(request.state, "housing_mutation_action", None)
                    if (
                        isinstance(db, Session)
                        and isinstance(context, AuthContext)
                        and isinstance(action, str)
                    ):
                        _standalone_audit(
                            db,
                            request,
                            context,
                            action=action,
                            resource_type="housing_request",
                            resource_id=None,
                            outcome="failed",
                            metadata={"error_code": "validation_error"},
                        )
                raise
            except ProblemError as exc:
                _audit_sensitive_read(
                    request,
                    outcome="denied" if exc.status in {403, 404} else "failed",
                    error_code=exc.code,
                )
                raise
            except HousingServiceError as exc:
                error_code = _public_error_code(exc)
                _audit_sensitive_read(
                    request,
                    outcome="denied" if isinstance(exc, HousingResourceNotFound) else "failed",
                    error_code=error_code,
                )
                _audit_unhandled_mutation_failure(request, error_code=error_code)
                _raise_housing_error(exc)
            except DBAPIError:
                _audit_sensitive_read(
                    request,
                    outcome="failed",
                    error_code="housing_persistence_failure",
                )
                _audit_unhandled_mutation_failure(
                    request,
                    error_code="housing_persistence_failure",
                )
                raise ProblemError(
                    status=503,
                    code="housing_persistence_failure",
                    detail="房态服务暂时不可用，请稍后重试",
                ) from None
            except Exception:  # noqa: BLE001 - matched route boundary sanitizes failures
                _audit_sensitive_read(
                    request,
                    outcome="failed",
                    error_code="housing_service_failure",
                )
                _audit_unhandled_mutation_failure(
                    request,
                    error_code="housing_service_failure",
                )
                raise ProblemError(
                    status=500,
                    code="housing_service_failure",
                    detail="房态服务暂时不可用",
                ) from None
            response.headers["Cache-Control"] = "no-store, private"
            return response

        return route_handler


def _not_found() -> NoReturn:
    raise ProblemError(
        status=404,
        code="housing_resource_not_found",
        detail="房态资源不存在",
    )


def _permission_denied() -> NoReturn:
    raise ProblemError(
        status=403,
        code="permission_denied",
        detail="没有执行此操作的权限",
    )


def _raise_housing_error(exc: HousingServiceError) -> NoReturn:
    if isinstance(exc, HousingResourceNotFound):
        _not_found()
    if isinstance(exc, HousingPersistenceFailure):
        raise ProblemError(
            status=503,
            code="housing_persistence_failure",
            detail="房态服务暂时不可用，请稍后重试",
        ) from exc
    if isinstance(exc, HousingVerifiedEmailRequired):
        raise ProblemError(
            status=422,
            code=exc.code,
            detail="请先绑定并验证邮箱",
        ) from exc
    if isinstance(exc, HousingReportingError) and exc.code in _REPORTING_ERROR_PROBLEMS:
        status, detail = _REPORTING_ERROR_PROBLEMS[exc.code]
        errors = (
            _report_generation_errors(exc)
            if isinstance(exc, (HousingReportEmpty, HousingExportIncomplete))
            else None
        )
        raise ProblemError(
            status=status,
            code=exc.code,
            detail=detail,
            errors=errors,
        ) from exc
    if exc.code not in _KNOWN_HOUSING_CONFLICT_CODES:
        raise ProblemError(
            status=500,
            code="housing_service_failure",
            detail="房态服务暂时不可用",
        ) from exc
    raise ProblemError(
        status=409,
        code=exc.code,
        detail="房态目录状态已发生变化",
    ) from exc


def _public_error_code(exc: HousingServiceError) -> str:
    if isinstance(exc, HousingResourceNotFound):
        return "housing_resource_not_found"
    if isinstance(exc, HousingPersistenceFailure):
        return "housing_persistence_failure"
    if isinstance(exc, HousingVerifiedEmailRequired):
        return exc.code
    if isinstance(exc, HousingReportingError) and exc.code in _REPORTING_ERROR_PROBLEMS:
        return exc.code
    if exc.code in _KNOWN_HOUSING_CONFLICT_CODES:
        return exc.code
    return "housing_service_failure"


def _audit(
    db: Session,
    request: Request,
    context: AuthContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    outcome: AuditOutcome,
    metadata: dict[str, Any],
) -> None:
    AuditService(db).append(
        actor_user_id=context.account.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        purpose=None,
        outcome=outcome,
        request_id=str(request.state.request_id),
        metadata=metadata,
    )


def _mark_request_audited(request: Request) -> None:
    request.state.housing_request_audited = True


def _register_sensitive_read_audit(
    request: Request,
    db: Session,
    context: AuthContext,
    *,
    action: str,
) -> None:
    request.state.housing_sensitive_read_audit = SensitiveHousingReadAudit(
        db=db,
        context=context,
        action=action,
    )


def _sensitive_read_audit(request: Request) -> SensitiveHousingReadAudit | None:
    audit = getattr(request.state, "housing_sensitive_read_audit", None)
    return audit if isinstance(audit, SensitiveHousingReadAudit) else None


def _set_sensitive_read_room(request: Request, room_id: UUID) -> None:
    audit = _sensitive_read_audit(request)
    if audit is None:
        return
    audit.resource_type = "housing_room"
    audit.resource_id = room_id
    audit.metadata = {"room_id": str(room_id)}


def _set_sensitive_read_result_count(request: Request, result_count: int) -> None:
    audit = _sensitive_read_audit(request)
    if audit is not None:
        audit.metadata["result_count"] = result_count


def _audit_sensitive_read(
    request: Request,
    *,
    outcome: AuditOutcome,
    error_code: str | None = None,
) -> None:
    audit = _sensitive_read_audit(request)
    if (
        audit is None
        or getattr(request.state, "housing_request_audited", False)
        or getattr(request.state, "housing_sensitive_read_audit_attempted", False)
    ):
        return
    request.state.housing_sensitive_read_audit_attempted = True
    metadata = {
        key: value
        for key, value in audit.metadata.items()
        if outcome == "allowed" or key != "result_count"
    }
    if error_code is not None:
        metadata["error_code"] = error_code
    _standalone_audit(
        audit.db,
        request,
        audit.context,
        action=audit.action,
        resource_type=audit.resource_type,
        resource_id=audit.resource_id,
        outcome=outcome,
        metadata=metadata,
    )


def _audit_unhandled_mutation_failure(
    request: Request,
    *,
    error_code: str,
) -> None:
    if getattr(request.state, "housing_request_audited", False):
        return
    db = getattr(request.state, "housing_mutation_db", None)
    context = getattr(request.state, "housing_mutation_context", None)
    action = getattr(request.state, "housing_mutation_action", None)
    if not (isinstance(db, Session) and isinstance(context, AuthContext) and isinstance(action, str)):
        return
    _standalone_audit(
        db,
        request,
        context,
        action=action,
        resource_type="housing_request",
        resource_id=None,
        outcome="failed",
        metadata={"error_code": error_code},
    )


def _standalone_audit(
    db: Session,
    request: Request,
    context: AuthContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    outcome: AuditOutcome,
    metadata: dict[str, Any],
) -> None:
    try:
        db.rollback()
        with db.begin():
            _audit(
                db,
                request,
                context,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                metadata=metadata,
            )
        _mark_request_audited(request)
    except (AuditMetadataRejected, DBAPIError) as exc:
        raise ProblemError(
            status=500,
            code="housing_audit_failed",
            detail="房态审计记录失败",
        ) from exc


def _deny_mutation(
    db: Session,
    request: Request,
    context: AuthContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    metadata: dict[str, Any],
    not_found: bool = False,
) -> NoReturn:
    error_code = "housing_resource_not_found" if not_found else "permission_denied"
    _standalone_audit(
        db,
        request,
        context,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="denied",
        metadata={**metadata, "error_code": error_code},
    )
    if not_found:
        _not_found()
    _permission_denied()


def _failed_mutation(
    db: Session,
    request: Request,
    context: AuthContext,
    exc: HousingServiceError,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    metadata: dict[str, Any],
) -> NoReturn:
    error_code = _public_error_code(exc)
    audit_metadata = (
        {"error_code": error_code}
        if error_code in {"housing_persistence_failure", "housing_service_failure"}
        else {**metadata, "error_code": error_code}
    )
    _standalone_audit(
        db,
        request,
        context,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="failed",
        metadata=audit_metadata,
    )
    _raise_housing_error(exc)
