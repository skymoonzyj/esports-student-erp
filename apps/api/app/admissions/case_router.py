from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.admissions.api_support import (
    admission_case_permission_allowed,
    admissions_class_visibility,
    audit_amount_access,
    authorized_admission_case,
    raise_admissions_error,
    require_target_enrollment_permission,
    target_enrollment_permission_allowed,
)
from app.admissions.case_service import AdmissionCaseService
from app.admissions.dependencies import (
    get_admission_case_service,
    get_envelope_cipher,
    get_lookup_digest,
    get_payment_service,
)
from app.admissions.enrollment_router import _enrollment_response
from app.admissions.errors import AdmissionsServiceError
from app.admissions.models import AdmissionCase, Enrollment
from app.admissions.payment_service import PaymentService
from app.admissions.routing import PrivateAdmissionsRoute
from app.admissions.schemas import (
    AdmissionCaseResponse,
    AdmissionCaseStatus,
    AdmissionEnrollmentOptionResponse,
    AdmissionStudentCandidateResponse,
    CloseAdmissionCaseRequest,
    ConfirmEnrollment,
    EnrollmentPaymentPreference,
    EnrollmentResponse,
    MatchStudentRequest,
    Page,
)
from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_date_at
from app.identity.models import Person, StudentProfile
from app.identity.normalization import IdentityValidationError, normalize_phone
from app.identity.readiness import GuardianReadinessPolicy
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort, TeachingClass

router = APIRouter(tags=["admissions"], route_class=PrivateAdmissionsRoute)


def _case_read_only_reason(
    db: Session,
    admission_case_id: UUID,
) -> Literal["archived"] | None:
    archived_cohort = db.scalar(
        select(Cohort.id)
        .join(Enrollment, Enrollment.cohort_id == Cohort.id)
        .where(
            Enrollment.admission_case_id == admission_case_id,
            Cohort.status == "archived",
        )
        .limit(1)
    )
    return "archived" if archived_cohort is not None else None


def _ensure_case_mutable(db: Session, admission_case: AdmissionCase) -> None:
    if _case_read_only_reason(db, admission_case.id) is None:
        return
    raise ProblemError(
        status=409,
        code="archived_cohort_locked",
        detail="已归档营期的招生记录不可修改",
    )


def _case_response(
    db: Session,
    payment_service: PaymentService,
    admission_case: AdmissionCase,
    *,
    context: AuthContext,
) -> AdmissionCaseResponse:
    student_name = (
        db.scalar(select(Person.full_name).where(Person.id == admission_case.student_person_id))
        if admission_case.student_person_id is not None
        else None
    )
    summary = payment_service.payment_summary(admission_case.id)
    can_read_amounts = admission_case_permission_allowed(
        context,
        db,
        admission_case,
        "payments.read",
    )
    read_only_reason = _case_read_only_reason(db, admission_case.id)
    return AdmissionCaseResponse(
        id=admission_case.id,
        order_number=admission_case.order_number,
        student_person_id=admission_case.student_person_id,
        student_name=student_name,
        status=cast(AdmissionCaseStatus, admission_case.status),
        current_enrollment_id=admission_case.current_enrollment_id,
        payment_status=summary.status,
        paid_cents=summary.paid_cents if can_read_amounts else None,
        refunded_cents=summary.refunded_cents if can_read_amounts else None,
        net_paid_cents=summary.net_paid_cents if can_read_amounts else None,
        agreed_tuition_cents=(summary.agreed_tuition_cents if can_read_amounts else None),
        due_cents=summary.due_cents if can_read_amounts else None,
        overpaid_cents=summary.overpaid_cents if can_read_amounts else None,
        application_source=cast(
            Literal["self_service"] | None,
            admission_case.application_source,
        ),
        selected_project_id=admission_case.selected_project_id,
        selected_project_name=admission_case.project_name_snapshot,
        selected_program_id=admission_case.selected_program_id,
        selected_program_name=admission_case.program_name_snapshot,
        selected_coach_person_id=admission_case.selected_coach_person_id,
        selected_coach_name=admission_case.coach_name_snapshot,
        payment_preference=cast(
            EnrollmentPaymentPreference | None,
            admission_case.payment_preference,
        ),
        manage_allowed=(
            read_only_reason is None
            and admission_case_permission_allowed(
                context,
                db,
                admission_case,
                "admission_cases.manage",
            )
        ),
        read_only_reason=read_only_reason,
        created_at=admission_case.created_at,
        updated_at=admission_case.updated_at,
    )


@router.get("/admission-cases", response_model=Page[AdmissionCaseResponse])
def list_admission_cases(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    lookup: Annotated[LookupDigest, Depends(get_lookup_digest)],
    db: Annotated[Session, Depends(get_db)],
    status: Literal["pending_match", "ready", "enrolled", "closed"] | None = None,
    application_source: Literal["self_service"] | None = None,
    student_phone: Annotated[str | None, Query(max_length=32)] = None,
    order_number: Annotated[str | None, Query(max_length=64)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AdmissionCaseResponse]:
    visibility = admissions_class_visibility(context, db, "admission_cases.read")
    query = select(AdmissionCase)
    case_ids_for_visible_classes = select(Enrollment.admission_case_id).where(
        Enrollment.class_id.in_(visibility.class_ids)
    )
    case_ids_for_excluded_classes = select(Enrollment.admission_case_id).where(
        Enrollment.class_id.in_(visibility.excluded_class_ids)
    )
    if visibility.global_access:
        if visibility.excluded_class_ids:
            query = query.where(AdmissionCase.id.not_in(case_ids_for_excluded_classes))
    else:
        query = query.where(AdmissionCase.id.in_(case_ids_for_visible_classes))
    if status is not None:
        query = query.where(AdmissionCase.status == status)
    if application_source is not None:
        query = query.where(AdmissionCase.application_source == application_source)
    if order_number is not None and order_number.strip():
        query = query.where(AdmissionCase.order_number == order_number.strip().upper())
    if student_phone is not None:
        try:
            normalized_phone = normalize_phone(student_phone)
        except IdentityValidationError as exc:
            raise ProblemError(
                status=422,
                code="validation_error",
                detail="手机号格式不正确",
            ) from exc
        query = query.join(
            Person,
            Person.id == AdmissionCase.student_person_id,
        ).where(Person.phone_digest == lookup.digest(normalized_phone))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    cases = list(
        db.scalars(
            query.order_by(AdmissionCase.created_at.desc(), AdmissionCase.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    result = Page[AdmissionCaseResponse](
        items=[
            _case_response(
                db,
                payment_service,
                admission_case,
                context=context,
            )
            for admission_case in cases
        ],
        page=page,
        page_size=page_size,
        total=total,
    )
    amount_read_count = sum(
        admission_case_permission_allowed(
            context,
            db,
            admission_case,
            "payments.read",
        )
        for admission_case in cases
    )
    if amount_read_count:
        audit_amount_access(
            db,
            request,
            context,
            action="admissions.admission_case.list_amounts",
            result_count=amount_read_count,
            resource_type="admission_case",
        )
    return result


def _masked_phone(cipher: EnvelopeCipher, person: Person) -> str | None:
    if person.phone_ciphertext is None:
        return None
    phone = cipher.decrypt(person.phone_ciphertext, field_name="phone")
    return f"{phone[:3]}****{phone[-4:]}"


@router.get(
    "/admission-cases/{admission_case_id}/student-candidates",
    response_model=Page[AdmissionStudentCandidateResponse],
)
def list_admission_student_candidates(
    admission_case_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    cipher: Annotated[EnvelopeCipher, Depends(get_envelope_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_lookup_digest)],
    db: Annotated[Session, Depends(get_db)],
    query_text: Annotated[
        str,
        Query(alias="query", min_length=1, max_length=64),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AdmissionStudentCandidateResponse]:
    admission_case = authorized_admission_case(
        context,
        db,
        admission_case_id,
        "admission_cases.manage",
    )
    _ensure_case_mutable(db, admission_case)
    if admission_case.status not in {"pending_match", "ready"}:
        raise ProblemError(
            status=409,
            code="admission_case_not_ready",
            detail="当前待分配单不能重新匹配学员",
        )

    normalized_query = query_text.strip()
    match_conditions = [StudentProfile.student_number == normalized_query]
    try:
        normalized_phone = normalize_phone(normalized_query)
    except IdentityValidationError:
        pass
    else:
        match_conditions.append(Person.phone_digest == lookup.digest(normalized_phone))
    candidate_query = (
        select(Person, StudentProfile)
        .join(StudentProfile, StudentProfile.person_id == Person.id)
        .where(
            Person.deleted_at.is_(None),
            or_(*match_conditions),
        )
    )
    total = db.scalar(select(func.count()).select_from(candidate_query.subquery())) or 0
    rows = list(
        db.execute(
            candidate_query.order_by(StudentProfile.student_number, Person.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page[AdmissionStudentCandidateResponse](
        items=[
            AdmissionStudentCandidateResponse(
                id=person.id,
                full_name=person.full_name,
                student_number=profile.student_number,
                student_status=cast(
                    Literal["draft", "active", "inactive"],
                    profile.status,
                ),
                masked_phone=_masked_phone(cipher, person),
            )
            for person, profile in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/admission-cases/{admission_case_id}/enrollment-options",
    response_model=Page[AdmissionEnrollmentOptionResponse],
)
def list_admission_enrollment_options(
    admission_case_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AdmissionEnrollmentOptionResponse]:
    admission_case = authorized_admission_case(
        context,
        db,
        admission_case_id,
        "admission_cases.manage",
    )
    _ensure_case_mutable(db, admission_case)
    if admission_case.status != "ready" or admission_case.student_person_id is None:
        raise ProblemError(
            status=409,
            code="admission_case_not_ready",
            detail="请先为待分配单匹配学员",
        )
    student_id = admission_case.student_person_id
    if db.get(Person, student_id) is None or db.get(StudentProfile, student_id) is None:
        raise ProblemError(
            status=409,
            code="admission_case_not_ready",
            detail="已匹配学员档案不可用",
        )

    class_query = (
        select(TeachingClass, Cohort)
        .join(Cohort, Cohort.id == TeachingClass.cohort_id)
        .where(
            Cohort.status == "open",
            TeachingClass.is_active.is_(True),
        )
    )
    if admission_case.selected_project_id is not None:
        class_query = class_query.where(
            TeachingClass.project_id == admission_case.selected_project_id
        )
    class_rows = list(
        db.execute(
            class_query.order_by(
                Cohort.starts_at,
                Cohort.id,
                TeachingClass.name,
                TeachingClass.id,
            )
        )
    )
    allowed_rows = [
        (teaching_class, cohort)
        for teaching_class, cohort in class_rows
        if target_enrollment_permission_allowed(
            context,
            db,
            student_person_id=student_id,
            class_id=teaching_class.id,
            permission="enrollments.manage",
        )
    ]
    class_ids = [teaching_class.id for teaching_class, _ in allowed_rows]
    active_counts = (
        {
            class_id: int(count)
            for class_id, count in db.execute(
                select(Enrollment.class_id, func.count(Enrollment.id))
                .where(
                    Enrollment.class_id.in_(class_ids),
                    Enrollment.status == "active",
                )
                .group_by(Enrollment.class_id)
            )
        }
        if class_ids
        else {}
    )
    readiness_policy = GuardianReadinessPolicy(db)
    readiness_by_cohort: dict[UUID, Literal["ready", "minor_guardian_required"]] = {}
    for _, cohort in allowed_rows:
        if cohort.id in readiness_by_cohort:
            continue
        projection = readiness_policy.evaluate_one(
            student_id,
            business_date_at(cohort.starts_at),
        )
        readiness_by_cohort[cohort.id] = (
            "minor_guardian_required"
            if projection.age_status == "minor" and projection.guardian_status != "ready"
            else "ready"
        )

    total = len(allowed_rows)
    paged_rows = allowed_rows[(page - 1) * page_size : page * page_size]
    return Page[AdmissionEnrollmentOptionResponse](
        items=[
            AdmissionEnrollmentOptionResponse(
                cohort_id=cohort.id,
                cohort_name=cohort.name,
                cohort_starts_at=cohort.starts_at,
                cohort_ends_at=cohort.ends_at,
                cohort_status="open",
                class_id=teaching_class.id,
                class_name=teaching_class.name,
                class_is_active=True,
                capacity=teaching_class.capacity,
                active_enrollment_count=active_counts.get(teaching_class.id, 0),
                is_full=(active_counts.get(teaching_class.id, 0) >= teaching_class.capacity),
                default_tuition_cents=teaching_class.default_tuition_cents,
                readiness_at_start=readiness_by_cohort[cohort.id],
                manage_allowed=True,
                capacity_override_allowed=target_enrollment_permission_allowed(
                    context,
                    db,
                    student_person_id=student_id,
                    class_id=teaching_class.id,
                    permission="enrollments.capacity_override",
                ),
            )
            for teaching_class, cohort in paged_rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


def _audit_case_change(
    db: Session,
    request: Request,
    context: AuthContext,
    admission_case: AdmissionCase,
    *,
    action: str,
) -> None:
    metadata = {"admission_case_id": str(admission_case.id)}
    if admission_case.student_person_id is not None:
        metadata["student_person_id"] = str(admission_case.student_person_id)
    AuditService(db).append(
        actor_user_id=context.account.id,
        action=action,
        resource_type="admission_case",
        resource_id=admission_case.id,
        purpose=None,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata=metadata,
    )


@router.post(
    "/admission-cases/{admission_case_id}/match-student",
    response_model=AdmissionCaseResponse,
)
def match_admission_student(
    admission_case_id: UUID,
    input: MatchStudentRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[AdmissionCaseService, Depends(get_admission_case_service)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> AdmissionCaseResponse:
    authorized_admission_case(
        context,
        db,
        admission_case_id,
        "admission_cases.manage",
    )
    try:
        db.commit()
        with db.begin():
            admission_case = service.match_student(
                admission_case_id,
                input.student_person_id,
                actor_user_id=context.account.id,
            )
            _audit_case_change(
                db,
                request,
                context,
                admission_case,
                action="admissions.admission_case.match_student",
            )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
    return _case_response(
        db,
        payment_service,
        admission_case,
        context=context,
    )


@router.post(
    "/admission-cases/{admission_case_id}/close",
    response_model=AdmissionCaseResponse,
)
def close_admission_case(
    admission_case_id: UUID,
    input: CloseAdmissionCaseRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[AdmissionCaseService, Depends(get_admission_case_service)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> AdmissionCaseResponse:
    authorized_admission_case(
        context,
        db,
        admission_case_id,
        "admission_cases.manage",
    )
    try:
        db.commit()
        with db.begin():
            admission_case = service.close(
                admission_case_id,
                reason=input.reason,
                actor_user_id=context.account.id,
            )
            _audit_case_change(
                db,
                request,
                context,
                admission_case,
                action="admissions.admission_case.close",
            )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
    return _case_response(
        db,
        payment_service,
        admission_case,
        context=context,
    )


@router.post(
    "/admission-cases/{admission_case_id}/reopen",
    response_model=AdmissionCaseResponse,
)
def reopen_admission_case(
    admission_case_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[AdmissionCaseService, Depends(get_admission_case_service)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> AdmissionCaseResponse:
    authorized_admission_case(
        context,
        db,
        admission_case_id,
        "admission_cases.manage",
    )
    try:
        db.commit()
        with db.begin():
            admission_case = service.reopen(
                admission_case_id,
                actor_user_id=context.account.id,
            )
            _audit_case_change(
                db,
                request,
                context,
                admission_case,
                action="admissions.admission_case.reopen",
            )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
    return _case_response(
        db,
        payment_service,
        admission_case,
        context=context,
    )


@router.post(
    "/admission-cases/{admission_case_id}/confirm-enrollment",
    response_model=EnrollmentResponse,
    status_code=201,
)
def confirm_admission_enrollment(
    admission_case_id: UUID,
    input: ConfirmEnrollment,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[AdmissionCaseService, Depends(get_admission_case_service)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> EnrollmentResponse:
    current_case = authorized_admission_case(
        context,
        db,
        admission_case_id,
        "admission_cases.manage",
    )
    _ensure_case_mutable(db, current_case)
    if current_case.student_person_id is not None:
        require_target_enrollment_permission(
            context,
            db,
            student_person_id=current_case.student_person_id,
            class_id=input.class_id,
            permission="enrollments.manage",
        )
    try:
        enrollment = service.confirm_enrollment(
            admission_case_id,
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
            capacity_override_allowed=(
                current_case.student_person_id is not None
                and target_enrollment_permission_allowed(
                    context,
                    db,
                    student_person_id=current_case.student_person_id,
                    class_id=input.class_id,
                    permission="enrollments.capacity_override",
                )
            ),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    return _enrollment_response(
        db,
        payment_service,
        enrollment,
        can_read_amounts=admission_case_permission_allowed(
            context,
            db,
            current_case,
            "payments.read",
        ),
    )
