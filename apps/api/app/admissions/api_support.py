from __future__ import annotations

from typing import NoReturn
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions.errors import AdmissionsServiceError
from app.admissions.models import AdmissionCase, AdmissionCasePayment, Enrollment
from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.rbac.service import RbacService
from app.teaching.access import TeachingVisibility

_NOT_FOUND_CODES = {
    "admission_case_not_found",
    "admission_class_not_found",
    "admission_student_not_found",
    "enrollment_not_found",
    "payment_not_found",
    "payment_conflict_not_found",
    "service_account_not_found",
    "service_credential_not_found",
}
_CONFLICT_CODES = {
    "admission_case_closed",
    "admission_case_not_ready",
    "admission_payment_required",
    "archived_cohort_locked",
    "class_capacity_reached",
    "cohort_not_open",
    "duplicate_cohort_enrollment",
    "enrollment_changed",
    "enrollment_not_active",
    "minor_guardian_required",
    "payment_import_conflict",
    "payment_conflict_not_open",
    "refund_exceeds_paid_amount",
    "withdrawal_nothing_to_refund",
    "withdrawal_refund_changed",
    "duplicate_service_account",
}


def permission_allowed(
    context: AuthContext,
    db: Session,
    permission: str,
    *,
    resource_tokens: set[str] | None = None,
) -> bool:
    return RbacService(db).decide(
        user_id=context.account.id,
        permission=permission,
        resource_tokens=(
            resource_tokens if resource_tokens is not None else {"global"}
        ),
    ).allowed


def require_permission(
    context: AuthContext,
    db: Session,
    permission: str,
    *,
    resource_tokens: set[str] | None = None,
) -> None:
    if not permission_allowed(
        context,
        db,
        permission,
        resource_tokens=resource_tokens,
    ):
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )


def require_available_permission(
    context: AuthContext,
    db: Session,
    permission: str,
) -> None:
    if permission in RbacService(db).available_permissions(user_id=context.account.id):
        return
    raise ProblemError(
        status=403,
        code="permission_denied",
        detail="没有执行此操作的权限",
    )


def enrollment_resource_tokens(
    context: AuthContext,
    db: Session,
    enrollment: Enrollment,
) -> set[str]:
    return RbacService(db).enrollment_resource_tokens(
        actor_person_id=context.account.person_id,
        student_person_id=enrollment.student_person_id,
        class_id=enrollment.class_id,
    )


def enrollment_permission_allowed(
    context: AuthContext,
    db: Session,
    enrollment: Enrollment,
    permission: str,
) -> bool:
    return permission_allowed(
        context,
        db,
        permission,
        resource_tokens=enrollment_resource_tokens(context, db, enrollment),
    )


def require_enrollment_permission(
    context: AuthContext,
    db: Session,
    enrollment: Enrollment,
    permission: str,
) -> None:
    if enrollment_permission_allowed(context, db, enrollment, permission):
        return
    _raise_masked_or_denied(
        context,
        db,
        permission,
        not_found_code="enrollment_not_found",
        not_found_detail="正式报名不存在",
    )


def authorized_enrollment(
    context: AuthContext,
    db: Session,
    enrollment_id: UUID,
    permission: str,
) -> Enrollment:
    require_available_permission(context, db, permission)
    enrollment = db.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise ProblemError(
            status=404,
            code="enrollment_not_found",
            detail="正式报名不存在",
        )
    require_enrollment_permission(context, db, enrollment, permission)
    return enrollment


def _raise_masked_or_denied(
    context: AuthContext,
    db: Session,
    permission: str,
    *,
    not_found_code: str,
    not_found_detail: str,
) -> NoReturn:
    if permission in RbacService(db).available_permissions(user_id=context.account.id):
        raise ProblemError(
            status=404,
            code=not_found_code,
            detail=not_found_detail,
        )
    raise ProblemError(
        status=403,
        code="permission_denied",
        detail="没有执行此操作的权限",
    )


def admissions_class_visibility(
    context: AuthContext,
    db: Session,
    permission: str,
) -> TeachingVisibility:
    rbac = RbacService(db)
    visibility = rbac.teaching_visibility(
        user_id=context.account.id,
        permission=permission,
        actor_person_id=context.account.person_id,
    )
    if (
        not visibility.global_access
        and not visibility.class_ids
        and permission not in rbac.available_permissions(user_id=context.account.id)
    ):
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )
    return visibility


def admission_case_resource_tokens(
    context: AuthContext,
    db: Session,
    admission_case: AdmissionCase,
) -> set[str]:
    rbac = RbacService(db)
    tokens = {"global"}
    if admission_case.student_person_id is not None:
        tokens.update(
            rbac.person_resource_tokens(
                actor_person_id=context.account.person_id,
                target_person_id=admission_case.student_person_id,
            )
        )
    for student_person_id, class_id in db.execute(
        select(Enrollment.student_person_id, Enrollment.class_id).where(
            Enrollment.admission_case_id == admission_case.id
        )
    ):
        tokens.update(
            rbac.enrollment_resource_tokens(
                actor_person_id=context.account.person_id,
                student_person_id=student_person_id,
                class_id=class_id,
            )
        )
    return tokens


def admission_case_permission_allowed(
    context: AuthContext,
    db: Session,
    admission_case: AdmissionCase,
    permission: str,
) -> bool:
    return permission_allowed(
        context,
        db,
        permission,
        resource_tokens=admission_case_resource_tokens(context, db, admission_case),
    )


def require_admission_case_permission(
    context: AuthContext,
    db: Session,
    admission_case: AdmissionCase,
    permission: str,
) -> None:
    if admission_case_permission_allowed(context, db, admission_case, permission):
        return
    _raise_masked_or_denied(
        context,
        db,
        permission,
        not_found_code="admission_case_not_found",
        not_found_detail="待分配单不存在",
    )


def authorized_admission_case(
    context: AuthContext,
    db: Session,
    admission_case_id: UUID,
    permission: str,
) -> AdmissionCase:
    require_available_permission(context, db, permission)
    admission_case = db.get(AdmissionCase, admission_case_id)
    if admission_case is None:
        raise ProblemError(
            status=404,
            code="admission_case_not_found",
            detail="待分配单不存在",
        )
    require_admission_case_permission(context, db, admission_case, permission)
    return admission_case


def require_payment_permission(
    context: AuthContext,
    db: Session,
    payment_id: UUID,
    permission: str,
) -> AdmissionCase:
    require_available_permission(context, db, permission)
    admission_case = db.scalar(
        select(AdmissionCase)
        .join(
            AdmissionCasePayment,
            AdmissionCasePayment.admission_case_id == AdmissionCase.id,
        )
        .where(AdmissionCasePayment.payment_id == payment_id)
    )
    if admission_case is None:
        raise ProblemError(
            status=404,
            code="payment_not_found",
            detail="付款记录不存在",
        )
    if admission_case_permission_allowed(context, db, admission_case, permission):
        return admission_case
    _raise_masked_or_denied(
        context,
        db,
        permission,
        not_found_code="payment_not_found",
        not_found_detail="付款记录不存在",
    )


def target_enrollment_permission_allowed(
    context: AuthContext,
    db: Session,
    *,
    student_person_id: UUID,
    class_id: UUID,
    permission: str,
) -> bool:
    tokens = RbacService(db).enrollment_resource_tokens(
        actor_person_id=context.account.person_id,
        student_person_id=student_person_id,
        class_id=class_id,
    )
    return permission_allowed(
        context,
        db,
        permission,
        resource_tokens=tokens,
    )


def require_target_enrollment_permission(
    context: AuthContext,
    db: Session,
    *,
    student_person_id: UUID,
    class_id: UUID,
    permission: str,
) -> None:
    if target_enrollment_permission_allowed(
        context,
        db,
        student_person_id=student_person_id,
        class_id=class_id,
        permission=permission,
    ):
        return
    _raise_masked_or_denied(
        context,
        db,
        permission,
        not_found_code="enrollment_not_found",
        not_found_detail="正式报名不存在",
    )


def raise_admissions_error(exc: AdmissionsServiceError) -> NoReturn:
    if exc.code in _NOT_FOUND_CODES:
        raise ProblemError(
            status=404,
            code=exc.code,
            detail="招生业务资源不存在",
        ) from exc
    if exc.code in _CONFLICT_CODES:
        raise ProblemError(
            status=409,
            code=exc.code,
            detail="招生业务状态已发生变化",
        ) from exc
    raise ProblemError(
        status=422,
        code=exc.code,
        detail="招生业务请求无效",
    ) from exc


def audit_amount_access(
    db: Session,
    request: Request,
    context: AuthContext,
    *,
    action: str,
    result_count: int,
    resource_id: UUID | None = None,
    resource_type: str = "payment",
) -> None:
    try:
        AuditService(db).append(
            actor_user_id=context.account.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=None,
            outcome="allowed",
            request_id=str(request.state.request_id),
            metadata={"result_count": result_count},
        )
        db.commit()
    except AuditMetadataRejected as exc:
        db.rollback()
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
