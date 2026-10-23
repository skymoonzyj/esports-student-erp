from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.api_support import (
    admissions_class_visibility,
    audit_amount_access,
    authorized_enrollment,
    enrollment_permission_allowed,
    raise_admissions_error,
    require_enrollment_permission,
    require_target_enrollment_permission,
    target_enrollment_permission_allowed,
)
from app.admissions.dependencies import (
    get_enrollment_service,
    get_payment_service,
    get_withdrawal_service,
)
from app.admissions.enrollment_service import EnrollmentService
from app.admissions.errors import AdmissionsServiceError
from app.admissions.models import Enrollment
from app.admissions.payment_service import PaymentService
from app.admissions.routing import PrivateAdmissionsRoute
from app.admissions.schemas import (
    CancelEnrollment,
    EnrollmentActionContextResponse,
    EnrollmentResponse,
    EnrollmentStatus,
    EnrollmentTransferOptionResponse,
    Page,
    TransferEnrollment,
    WithdrawalConfirmRequest,
    WithdrawalPreview,
    WithdrawalPreviewRequest,
)
from app.admissions.withdrawal_service import WithdrawalService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.identity.models import Person
from app.teaching.models import Cohort, TeachingClass

router = APIRouter(tags=["admissions"], route_class=PrivateAdmissionsRoute)


def _enrollment_action_context(
    context: AuthContext,
    db: Session,
    enrollment: Enrollment,
) -> EnrollmentActionContextResponse:
    cohort = db.get(Cohort, enrollment.cohort_id)
    if cohort is None:
        raise ProblemError(
            status=404,
            code="enrollment_not_found",
            detail="正式报名不存在",
        )
    archived = cohort.status == "archived"
    manage_allowed = (
        enrollment.status == "active"
        and not archived
        and enrollment_permission_allowed(
            context,
            db,
            enrollment,
            "enrollments.manage",
        )
    )
    refund_allowed = (
        manage_allowed
        and enrollment_permission_allowed(
            context,
            db,
            enrollment,
            "refunds.manage",
        )
        and enrollment_permission_allowed(
            context,
            db,
            enrollment,
            "payments.read",
        )
    )
    return EnrollmentActionContextResponse(
        manage_allowed=manage_allowed,
        refund_allowed=refund_allowed,
        read_only_reason="archived" if archived else None,
    )


def _enrollment_response(
    db: Session,
    payment_service: PaymentService,
    enrollment: Enrollment,
    *,
    can_read_amounts: bool,
) -> EnrollmentResponse:
    student = db.get(Person, enrollment.student_person_id)
    cohort = db.get(Cohort, enrollment.cohort_id)
    teaching_class = db.get(TeachingClass, enrollment.class_id)
    if student is None or cohort is None or teaching_class is None:
        raise ProblemError(
            status=404,
            code="enrollment_not_found",
            detail="正式报名不存在",
        )
    summary = payment_service.payment_summary(enrollment.admission_case_id)
    return EnrollmentResponse(
        id=enrollment.id,
        admission_case_id=enrollment.admission_case_id,
        student_person_id=enrollment.student_person_id,
        student_name=student.full_name,
        cohort_id=enrollment.cohort_id,
        cohort_name=cohort.name,
        class_id=enrollment.class_id,
        class_name=teaching_class.name,
        agreed_tuition_cents=(
            enrollment.agreed_tuition_cents if can_read_amounts else None
        ),
        payment_status=summary.status,
        paid_cents=summary.paid_cents if can_read_amounts else None,
        refunded_cents=summary.refunded_cents if can_read_amounts else None,
        net_paid_cents=summary.net_paid_cents if can_read_amounts else None,
        due_cents=summary.due_cents if can_read_amounts else None,
        overpaid_cents=summary.overpaid_cents if can_read_amounts else None,
        status=cast(EnrollmentStatus, enrollment.status),
        confirmed_at=enrollment.confirmed_at,
        cancelled_at=enrollment.cancelled_at,
        completed_at=enrollment.completed_at,
        created_at=enrollment.created_at,
        updated_at=enrollment.updated_at,
    )


@router.get("/enrollments", response_model=Page[EnrollmentResponse])
def list_enrollments(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
    status: Literal["active", "cancelled", "completed"] | None = None,
    cohort_id: UUID | None = None,
    class_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[EnrollmentResponse]:
    visibility = admissions_class_visibility(context, db, "enrollments.read")
    query = select(Enrollment)
    if visibility.global_access:
        if visibility.excluded_class_ids:
            query = query.where(
                Enrollment.class_id.not_in(visibility.excluded_class_ids)
            )
    else:
        query = query.where(Enrollment.class_id.in_(visibility.class_ids))
    if status is not None:
        query = query.where(Enrollment.status == status)
    if cohort_id is not None:
        query = query.where(Enrollment.cohort_id == cohort_id)
    if class_id is not None:
        query = query.where(Enrollment.class_id == class_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    enrollments = list(
        db.scalars(
            query.order_by(Enrollment.created_at.desc(), Enrollment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    result = Page[EnrollmentResponse](
        items=[
            _enrollment_response(
                db,
                payment_service,
                enrollment,
                can_read_amounts=enrollment_permission_allowed(
                    context,
                    db,
                    enrollment,
                    "payments.read",
                ),
            )
            for enrollment in enrollments
        ],
        page=page,
        page_size=page_size,
        total=total,
    )
    amount_read_count = sum(
        enrollment_permission_allowed(
            context,
            db,
            enrollment,
            "payments.read",
        )
        for enrollment in enrollments
    )
    if amount_read_count:
        audit_amount_access(
            db,
            request,
            context,
            action="admissions.enrollment.list_amounts",
            result_count=amount_read_count,
            resource_type="enrollment",
        )
    return result


@router.get(
    "/enrollments/{enrollment_id}/action-context",
    response_model=EnrollmentActionContextResponse,
)
def get_enrollment_action_context(
    enrollment_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> EnrollmentActionContextResponse:
    enrollment = authorized_enrollment(
        context,
        db,
        enrollment_id,
        "enrollments.read",
    )
    return _enrollment_action_context(context, db, enrollment)


@router.get(
    "/enrollments/{enrollment_id}/transfer-options",
    response_model=Page[EnrollmentTransferOptionResponse],
)
def list_enrollment_transfer_options(
    enrollment_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[EnrollmentTransferOptionResponse]:
    enrollment = authorized_enrollment(
        context,
        db,
        enrollment_id,
        "enrollments.read",
    )
    action_context = _enrollment_action_context(context, db, enrollment)
    if not action_context.manage_allowed:
        return Page[EnrollmentTransferOptionResponse](
            items=[],
            page=page,
            page_size=page_size,
            total=0,
        )

    candidates = [
        teaching_class
        for teaching_class in db.scalars(
            select(TeachingClass)
            .where(
                TeachingClass.cohort_id == enrollment.cohort_id,
                TeachingClass.is_active.is_(True),
                TeachingClass.id != enrollment.class_id,
            )
            .order_by(TeachingClass.name.asc(), TeachingClass.id.asc())
        )
        if target_enrollment_permission_allowed(
            context,
            db,
            student_person_id=enrollment.student_person_id,
            class_id=teaching_class.id,
            permission="enrollments.manage",
        )
    ]
    total = len(candidates)
    selected = candidates[(page - 1) * page_size : page * page_size]
    selected_ids = [teaching_class.id for teaching_class in selected]
    active_counts = (
        {
            class_id: count
            for class_id, count in db.execute(
                select(Enrollment.class_id, func.count(Enrollment.id))
                .where(
                    Enrollment.class_id.in_(selected_ids),
                    Enrollment.status == "active",
                )
                .group_by(Enrollment.class_id)
            )
        }
        if selected_ids
        else {}
    )
    return Page[EnrollmentTransferOptionResponse](
        items=[
            EnrollmentTransferOptionResponse(
                class_id=teaching_class.id,
                class_name=teaching_class.name,
                capacity=teaching_class.capacity,
                active_enrollment_count=active_counts.get(teaching_class.id, 0),
                is_full=(
                    active_counts.get(teaching_class.id, 0)
                    >= teaching_class.capacity
                ),
                capacity_override_allowed=target_enrollment_permission_allowed(
                    context,
                    db,
                    student_person_id=enrollment.student_person_id,
                    class_id=teaching_class.id,
                    permission="enrollments.capacity_override",
                ),
            )
            for teaching_class in selected
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
def get_enrollment(
    enrollment_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> EnrollmentResponse:
    enrollment = authorized_enrollment(
        context,
        db,
        enrollment_id,
        "enrollments.read",
    )
    can_read_amounts = enrollment_permission_allowed(
        context,
        db,
        enrollment,
        "payments.read",
    )
    result = _enrollment_response(
        db,
        payment_service,
        enrollment,
        can_read_amounts=can_read_amounts,
    )
    if can_read_amounts:
        audit_amount_access(
            db,
            request,
            context,
            action="admissions.enrollment.read_amounts",
            result_count=1,
            resource_id=enrollment.id,
            resource_type="enrollment",
        )
    return result


@router.post("/enrollments/{enrollment_id}/transfer", response_model=EnrollmentResponse)
def transfer_enrollment(
    enrollment_id: UUID,
    input: TransferEnrollment,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[EnrollmentService, Depends(get_enrollment_service)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> EnrollmentResponse:
    current_enrollment = authorized_enrollment(
        context,
        db,
        enrollment_id,
        "enrollments.manage",
    )
    require_target_enrollment_permission(
        context,
        db,
        student_person_id=current_enrollment.student_person_id,
        class_id=input.target_class_id,
        permission="enrollments.manage",
    )
    try:
        enrollment = service.transfer(
            enrollment_id,
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
            capacity_override_allowed=target_enrollment_permission_allowed(
                context,
                db,
                student_person_id=current_enrollment.student_person_id,
                class_id=input.target_class_id,
                permission="enrollments.capacity_override",
            ),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    return _enrollment_response(
        db,
        payment_service,
        enrollment,
        can_read_amounts=enrollment_permission_allowed(
            context,
            db,
            enrollment,
            "payments.read",
        ),
    )


@router.post("/enrollments/{enrollment_id}/cancel", response_model=EnrollmentResponse)
def cancel_enrollment(
    enrollment_id: UUID,
    input: CancelEnrollment,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[EnrollmentService, Depends(get_enrollment_service)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> EnrollmentResponse:
    authorized_enrollment(
        context,
        db,
        enrollment_id,
        "enrollments.manage",
    )
    try:
        enrollment = service.cancel(
            enrollment_id,
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    return _enrollment_response(
        db,
        payment_service,
        enrollment,
        can_read_amounts=enrollment_permission_allowed(
            context,
            db,
            enrollment,
            "payments.read",
        ),
    )


@router.post(
    "/enrollments/{enrollment_id}/withdrawal-refund-preview",
    response_model=WithdrawalPreview,
)
def preview_withdrawal_refund(
    enrollment_id: UUID,
    input: WithdrawalPreviewRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[WithdrawalService, Depends(get_withdrawal_service)],
    db: Annotated[Session, Depends(get_db)],
) -> WithdrawalPreview:
    enrollment = authorized_enrollment(
        context,
        db,
        enrollment_id,
        "enrollments.manage",
    )
    require_enrollment_permission(context, db, enrollment, "refunds.manage")
    require_enrollment_permission(context, db, enrollment, "payments.read")
    try:
        preview = service.preview(enrollment_id, input)
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    audit_amount_access(
        db,
        request,
        context,
        action="admissions.withdrawal.preview_amounts",
        result_count=1,
        resource_id=enrollment_id,
        resource_type="enrollment",
    )
    return preview


@router.post(
    "/enrollments/{enrollment_id}/withdrawal-refund-confirm",
    response_model=EnrollmentResponse,
)
def confirm_withdrawal_refund(
    enrollment_id: UUID,
    input: WithdrawalConfirmRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[WithdrawalService, Depends(get_withdrawal_service)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> EnrollmentResponse:
    current_enrollment = authorized_enrollment(
        context,
        db,
        enrollment_id,
        "enrollments.manage",
    )
    require_enrollment_permission(context, db, current_enrollment, "refunds.manage")
    require_enrollment_permission(context, db, current_enrollment, "payments.read")
    try:
        service.confirm(
            enrollment_id,
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    enrollment = db.get(Enrollment, enrollment_id)
    if enrollment is None:  # pragma: no cover - protected by successful confirmation
        raise ProblemError(
            status=404,
            code="enrollment_not_found",
            detail="正式报名不存在",
        )
    return _enrollment_response(
        db,
        payment_service,
        enrollment,
        can_read_amounts=enrollment_permission_allowed(
            context,
            db,
            enrollment,
            "payments.read",
        ),
    )
