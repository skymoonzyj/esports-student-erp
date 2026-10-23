from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.admissions.api_support import (
    admission_case_permission_allowed,
    admissions_class_visibility,
    audit_amount_access,
    authorized_admission_case,
    raise_admissions_error,
    require_payment_permission,
    require_permission,
)
from app.admissions.credential_service import CredentialService, VerifiedMachineRequest
from app.admissions.dependencies import (
    get_credential_service,
    get_lookup_digest,
    get_payment_service,
)
from app.admissions.errors import (
    AdmissionsServiceError,
    PaymentImportConflict,
    PaymentRateLimited,
    PaymentReplayRejected,
    PaymentRequestExpired,
    PaymentSignatureInvalid,
    PaymentSourceNotAllowed,
    ServiceAccountDisabled,
    ServiceCredentialDisabled,
)
from app.admissions.models import ServiceAccount
from app.admissions.payment_service import PaymentListItem, PaymentService
from app.admissions.routing import PrivateAdmissionsRoute
from app.admissions.schemas import (
    CreatedCredentialResponse,
    CredentialCreate,
    CredentialResponse,
    ManualPaymentCreate,
    Page,
    PaymentConflictResolve,
    PaymentConflictResponse,
    PaymentImportRequest,
    PaymentImportResponse,
    PaymentResponse,
    RefundCreate,
    RefundResponse,
    ServiceAccountCreate,
    ServiceAccountResponse,
)
from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.identity.normalization import IdentityValidationError, normalize_phone
from app.security.encryption import LookupDigest

router = APIRouter(tags=["admissions"], route_class=PrivateAdmissionsRoute)


def _payment_response(
    item: PaymentListItem,
    *,
    can_read_amounts: bool,
) -> PaymentResponse:
    payment = item.payment
    return PaymentResponse(
        id=payment.id,
        admission_case_id=item.admission_case_id,
        source_kind=cast(Literal["api", "manual"], payment.source_kind),
        source_system=payment.source_system,
        external_order_id=payment.external_order_id,
        payer_person_id=payment.payer_person_id,
        amount_cents=payment.amount_cents if can_read_amounts else None,
        refunded_cents=(
            payment.amount_cents - item.available_refund_cents
            if can_read_amounts
            else None
        ),
        available_refund_cents=(
            item.available_refund_cents if can_read_amounts else None
        ),
        currency="CNY",
        paid_at=payment.paid_at,
        created_at=payment.created_at,
    )


def _service_account_response(
    service: CredentialService,
    account: ServiceAccount,
) -> ServiceAccountResponse:
    return ServiceAccountResponse(
        id=account.id,
        name=account.name,
        source_system=account.source_system,
        is_active=account.is_active,
        credentials=[
            CredentialResponse(
                id=credential.id,
                service_account_id=credential.service_account_id,
                public_id=credential.public_id,
                name=credential.name,
                is_active=credential.is_active,
                disabled_at=credential.disabled_at,
            )
            for credential in service.list_credentials(account.id)
        ],
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _raise_machine_error(exc: AdmissionsServiceError) -> NoReturn:
    if isinstance(exc, PaymentRateLimited):
        raise ProblemError(
            status=429,
            code=exc.code,
            detail="付款导入请求过于频繁",
        ) from exc
    if isinstance(exc, PaymentReplayRejected):
        raise ProblemError(
            status=409,
            code=exc.code,
            detail="付款导入请求已使用",
        ) from exc
    if isinstance(
        exc,
        (
            PaymentSignatureInvalid,
            PaymentRequestExpired,
            ServiceAccountDisabled,
            ServiceCredentialDisabled,
        ),
    ):
        raise ProblemError(
            status=401,
            code=exc.code,
            detail="付款导入身份校验失败",
        ) from exc
    raise ProblemError(status=401, code=exc.code, detail="付款导入身份校验失败") from exc


async def require_machine_request(
    request: Request,
    service: Annotated[CredentialService, Depends(get_credential_service)],
) -> VerifiedMachineRequest:
    try:
        return service.verify_request(
            public_id=request.headers.get("X-Service-Account", ""),
            method=request.method,
            path=request.url.path,
            timestamp=request.headers.get("X-Timestamp", ""),
            nonce=request.headers.get("X-Nonce", ""),
            signature=request.headers.get("X-Signature", ""),
            raw_body=await request.body(),
        )
    except AdmissionsServiceError as exc:
        _raise_machine_error(exc)


@router.post("/payment-imports", response_model=PaymentImportResponse)
def import_payment(
    input: PaymentImportRequest,
    request: Request,
    response: Response,
    machine: Annotated[VerifiedMachineRequest, Depends(require_machine_request)],
    service: Annotated[PaymentService, Depends(get_payment_service)],
) -> PaymentImportResponse:
    try:
        result = service.import_payment(
            input,
            machine=machine,
            request_id=str(request.state.request_id),
        )
    except PaymentImportConflict as exc:
        raise ProblemError(
            status=409,
            code=exc.code,
            detail="付款导入内容与已有记录冲突",
        ) from exc
    except PaymentSourceNotAllowed as exc:
        raise ProblemError(
            status=403,
            code=exc.code,
            detail="付款接口无权使用该来源",
        ) from exc
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
    response.status_code = 201 if result.created else 200
    response.headers["Cache-Control"] = "no-store, private"
    return PaymentImportResponse.from_result(result)


@router.get("/service-accounts", response_model=Page[ServiceAccountResponse])
def list_service_accounts(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CredentialService, Depends(get_credential_service)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ServiceAccountResponse]:
    require_permission(context, db, "payment_imports.manage")
    accounts, total = service.list_service_accounts(page=page, page_size=page_size)
    return Page[ServiceAccountResponse](
        items=[_service_account_response(service, account) for account in accounts],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/service-accounts", response_model=ServiceAccountResponse, status_code=201)
def create_service_account(
    input: ServiceAccountCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CredentialService, Depends(get_credential_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ServiceAccountResponse:
    require_permission(context, db, "payment_imports.manage")
    try:
        db.commit()
        with db.begin():
            account = service.create_service_account(
                name=input.name,
                source_system=input.source_system,
                actor_user_id=context.account.id,
            )
            AuditService(db).append(
                actor_user_id=context.account.id,
                action="admissions.service_account.create",
                resource_type="service_account",
                resource_id=account.id,
                purpose=None,
                outcome="allowed",
                request_id=str(request.state.request_id),
                metadata={"service_account_id": str(account.id)},
            )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
    return _service_account_response(service, account)


@router.post("/service-accounts/{service_account_id}/disable", status_code=204)
def disable_service_account(
    service_account_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CredentialService, Depends(get_credential_service)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    require_permission(context, db, "payment_imports.manage")
    try:
        db.commit()
        with db.begin():
            service.disable_service_account(
                service_account_id,
                actor_user_id=context.account.id,
            )
            AuditService(db).append(
                actor_user_id=context.account.id,
                action="admissions.service_account.disable",
                resource_type="service_account",
                resource_id=service_account_id,
                purpose=None,
                outcome="allowed",
                request_id=str(request.state.request_id),
                metadata={"service_account_id": str(service_account_id)},
            )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
    return Response(status_code=204)


@router.post(
    "/service-accounts/{service_account_id}/credentials",
    response_model=CreatedCredentialResponse,
    status_code=201,
)
def create_service_credential(
    service_account_id: UUID,
    input: CredentialCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CredentialService, Depends(get_credential_service)],
    db: Annotated[Session, Depends(get_db)],
) -> CreatedCredentialResponse:
    require_permission(context, db, "payment_imports.manage")
    try:
        db.commit()
        with db.begin():
            created = service.create_credential(
                service_account_id=service_account_id,
                actor_user_id=context.account.id,
                name=input.name,
            )
            AuditService(db).append(
                actor_user_id=context.account.id,
                action="admissions.service_credential.create",
                resource_type="service_account_credential",
                resource_id=created.id,
                purpose=None,
                outcome="allowed",
                request_id=str(request.state.request_id),
                metadata={
                    "service_account_id": str(created.service_account_id),
                    "credential_id": str(created.id),
                },
            )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
    return CreatedCredentialResponse(
        id=created.id,
        service_account_id=created.service_account_id,
        public_id=created.public_id,
        name=created.name,
        secret=created.secret,
    )


@router.post("/service-account-credentials/{credential_id}/disable", status_code=204)
def disable_service_credential(
    credential_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CredentialService, Depends(get_credential_service)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    require_permission(context, db, "payment_imports.manage")
    try:
        db.commit()
        with db.begin():
            summary = service.get_credential(credential_id)
            service.disable_credential(
                credential_id,
                actor_user_id=context.account.id,
            )
            AuditService(db).append(
                actor_user_id=context.account.id,
                action="admissions.service_credential.disable",
                resource_type="service_account_credential",
                resource_id=credential_id,
                purpose=None,
                outcome="allowed",
                request_id=str(request.state.request_id),
                metadata={
                    "service_account_id": str(summary.service_account_id),
                    "credential_id": str(credential_id),
                },
            )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="招生业务审计记录失败",
        ) from exc
    return Response(status_code=204)


@router.get("/payment-conflicts", response_model=Page[PaymentConflictResponse])
def list_payment_conflicts(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
    status: Literal["open", "resolved", "dismissed"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[PaymentConflictResponse]:
    require_permission(context, db, "payment_imports.manage")
    conflicts, total = service.list_conflicts(
        status=status,
        page=page,
        page_size=page_size,
    )
    return Page[PaymentConflictResponse](
        items=[
            PaymentConflictResponse(
                id=conflict.id,
                original_import_event_id=conflict.original_import_event_id,
                conflict_type=cast(
                    Literal[
                        "external_order_payload_mismatch",
                        "idempotency_payload_mismatch",
                        "ambiguous_payer",
                    ],
                    conflict.conflict_type,
                ),
                status=cast(
                    Literal["open", "resolved", "dismissed"],
                    conflict.status,
                ),
                resolution_note=conflict.resolution_note,
                resolved_at=conflict.resolved_at,
                created_at=conflict.created_at,
                updated_at=conflict.updated_at,
            )
            for conflict in conflicts
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/payment-conflicts/{conflict_id}/resolve",
    response_model=PaymentConflictResponse,
)
def resolve_payment_conflict(
    conflict_id: UUID,
    input: PaymentConflictResolve,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> PaymentConflictResponse:
    require_permission(context, db, "payment_imports.manage")
    try:
        conflict = service.resolve_conflict(
            conflict_id,
            action=input.action,
            note=input.note,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    return PaymentConflictResponse(
        id=conflict.id,
        original_import_event_id=conflict.original_import_event_id,
        conflict_type=cast(
            Literal[
                "external_order_payload_mismatch",
                "idempotency_payload_mismatch",
                "ambiguous_payer",
            ],
            conflict.conflict_type,
        ),
        status=cast(
            Literal["open", "resolved", "dismissed"],
            conflict.status,
        ),
        resolution_note=conflict.resolution_note,
        resolved_at=conflict.resolved_at,
        created_at=conflict.created_at,
        updated_at=conflict.updated_at,
    )


@router.get("/payments", response_model=Page[PaymentResponse])
def list_payments(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PaymentService, Depends(get_payment_service)],
    lookup: Annotated[LookupDigest, Depends(get_lookup_digest)],
    db: Annotated[Session, Depends(get_db)],
    source_kind: Literal["api", "manual"] | None = None,
    source_system: Annotated[str | None, Query(max_length=80)] = None,
    payer_phone: Annotated[str | None, Query(max_length=32)] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[PaymentResponse]:
    visibility = admissions_class_visibility(context, db, "payments.read")
    payer_phone_digest = None
    if payer_phone is not None:
        try:
            normalized_phone = normalize_phone(payer_phone)
        except IdentityValidationError as exc:
            raise ProblemError(
                status=422,
                code="validation_error",
                detail="手机号格式不正确",
            ) from exc
        payer_phone_digest = lookup.digest(normalized_phone)
    items, total = service.list_payments(
        source_kind=source_kind,
        source_system=source_system,
        payer_phone_digest=payer_phone_digest,
        date_from=date_from,
        date_to=date_to,
        visible_class_ids=(None if visibility.global_access else visibility.class_ids),
        excluded_class_ids=visibility.excluded_class_ids,
        page=page,
        page_size=page_size,
    )
    result = Page[PaymentResponse](
        items=[_payment_response(item, can_read_amounts=True) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )
    audit_amount_access(
        db,
        request,
        context,
        action="admissions.payment.list_amounts",
        result_count=len(items),
    )
    return result


@router.post("/payments", response_model=PaymentResponse, status_code=201)
def create_manual_payment(
    input: ManualPaymentCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> PaymentResponse:
    if input.admission_case_id is not None:
        authorized_admission_case(
            context,
            db,
            input.admission_case_id,
            "payments.manage",
        )
    else:
        require_permission(context, db, "payments.manage")
    try:
        result = service.create_manual_payment(
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    item = PaymentListItem(
        payment=result.payment,
        admission_case_id=result.admission_case.id,
        available_refund_cents=result.payment.amount_cents,
    )
    return _payment_response(
        item,
        can_read_amounts=admission_case_permission_allowed(
            context,
            db,
            result.admission_case,
            "payments.read",
        ),
    )


@router.post("/payments/{payment_id}/refunds", response_model=RefundResponse, status_code=201)
def create_refund(
    payment_id: UUID,
    input: RefundCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PaymentService, Depends(get_payment_service)],
    db: Annotated[Session, Depends(get_db)],
) -> RefundResponse:
    require_payment_permission(context, db, payment_id, "refunds.manage")
    require_payment_permission(context, db, payment_id, "payments.read")
    try:
        refund = service.create_refund(
            payment_id,
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    return RefundResponse(
        id=refund.id,
        payment_id=refund.payment_id,
        amount_cents=refund.amount_cents,
        refunded_at=refund.refunded_at,
        refund_type=cast(
            Literal["manual", "withdrawal_prorated", "withdrawal_full"],
            refund.refund_type,
        ),
        reason=refund.reason,
        created_at=refund.created_at,
    )
