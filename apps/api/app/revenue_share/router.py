from __future__ import annotations

import csv
import unicodedata
from io import StringIO
from typing import Annotated, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.transactions import write_transaction
from app.identity.models import Person
from app.rbac.service import RbacService
from app.revenue_share.errors import (
    ReferralCodeInvalid,
    RevenueShareConflict,
    RevenueShareCursorInvalid,
    RevenueShareError,
    RevenueShareNotFound,
    RevenueShareReconcileBusy,
    RevenueShareSettlementExceedsBalance,
    RevenueShareSettlementOperationConflict,
    RevenueShareSettlementReferenceConflict,
)
from app.revenue_share.models import (
    ReferralCode,
    RevenueShareOrganization,
    RevenueShareRule,
)
from app.revenue_share.schemas import (
    BeneficiaryKind,
    CalculationType,
    ReferralCodeCreate,
    ReferralCodeResponse,
    RevenueShareBalanceItem,
    RevenueShareBeneficiaryOption,
    RevenueShareConfigurationResponse,
    RevenueShareEntryKind,
    RevenueShareExportRequest,
    RevenueShareFinanceSummary,
    RevenueShareLedgerItem,
    RevenueShareOrganizationCreate,
    RevenueShareOrganizationResponse,
    RevenueShareProjectOption,
    RevenueShareReconcileRequest,
    RevenueShareReconcileResponse,
    RevenueShareRole,
    RevenueShareRuleCreate,
    RevenueShareRuleDisableResponse,
    RevenueShareRuleResponse,
    RevenueShareSelfResponse,
    RevenueShareSettlementCreate,
    RevenueShareSettlementResponse,
)
from app.revenue_share.service import Balance, RevenueShareService
from app.teaching.models import GameProject

router = APIRouter(prefix="/revenue-share", tags=["revenue-share"])

Context = Annotated[AuthContext, Depends(require_standard_account)]
Db = Annotated[Session, Depends(get_db)]
_DANGEROUS_CSV_TEXT_PREFIXES = ("=", "+", "-", "@")
_CONTROL_CSV_TEXT_PREFIXES = ("\t", "\r")


def _safe_csv_text(value: str) -> str:
    normalized_prefix = unicodedata.normalize("NFKC", value).lstrip()
    dangerous = value.startswith(_CONTROL_CSV_TEXT_PREFIXES) or normalized_prefix.startswith(
        _DANGEROUS_CSV_TEXT_PREFIXES
    )
    return f"'{value}" if dangerous else value


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _deny() -> NoReturn:
    raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")


def _require_permission(
    context: AuthContext,
    db: Session,
    permission: str,
    *,
    tokens: set[str] | None = None,
) -> None:
    decision = RbacService(db).decide(
        user_id=context.account.id,
        permission=permission,
        resource_tokens=tokens or {"global"},
    )
    if not decision.allowed:
        _deny()


def _raise_revenue_share_error(exc: RevenueShareError) -> NoReturn:
    if isinstance(exc, RevenueShareCursorInvalid):
        status = 422
        detail = "分页位置无效，请刷新页面后重新查询或开始重算"
    elif isinstance(exc, RevenueShareReconcileBusy):
        status = 409
        detail = "关联付款或报名记录正在更新，请稍后继续重算；已完成的记录已保存"
    elif isinstance(exc, RevenueShareNotFound):
        status = 404
        detail = "分成记录或关联对象不存在"
    elif isinstance(exc, RevenueShareSettlementExceedsBalance):
        status = 409
        detail = "结算金额超过当前未结算余额"
    elif isinstance(exc, RevenueShareSettlementOperationConflict):
        status = 409
        detail = "本次操作已登记成功，但重试内容与原记录不同；请先核对结算余额，不要重复登记"
    elif isinstance(exc, RevenueShareSettlementReferenceConflict):
        status = 409
        detail = "该收款对象的打款凭证号已登记，请核对原结算记录，不要重复登记"
    elif isinstance(exc, (RevenueShareConflict, ReferralCodeInvalid)):
        status = 409
        detail = "分成配置冲突或邀请码不可用"
    else:
        status = 422
        detail = "分成操作失败"
    raise ProblemError(status=status, code=exc.code, detail=detail) from exc


def _organization_response(
    organization: RevenueShareOrganization,
) -> RevenueShareOrganizationResponse:
    return RevenueShareOrganizationResponse(
        id=organization.id,
        name=organization.name,
        is_active=organization.is_active,
    )


def _referral_code_response(
    code: ReferralCode,
    *,
    source_name: str,
    registration_base_url: str | None = None,
) -> ReferralCodeResponse:
    path = f"/register?invite={code.code}"
    return ReferralCodeResponse(
        id=code.id,
        code=code.code,
        label=code.label,
        source_kind=cast(BeneficiaryKind, code.source_kind),
        source_name=source_name,
        is_active=code.is_active,
        registration_url=(
            f"{registration_base_url.rstrip('/')}{path}" if registration_base_url else path
        ),
    )


def _person_names(db: Session, person_ids: set[UUID]) -> dict[UUID, str]:
    if not person_ids:
        return {}
    return {
        person_id: name
        for person_id, name in db.execute(
            select(Person.id, Person.full_name).where(Person.id.in_(person_ids))
        ).tuples()
    }


def _organization_names(
    db: Session,
    organization_ids: set[UUID],
) -> dict[UUID, str]:
    if not organization_ids:
        return {}
    return {
        organization_id: name
        for organization_id, name in db.execute(
            select(RevenueShareOrganization.id, RevenueShareOrganization.name).where(
                RevenueShareOrganization.id.in_(organization_ids)
            )
        ).tuples()
    }


def _configuration(
    db: Session,
    *,
    registration_base_url: str | None = None,
) -> RevenueShareConfigurationResponse:
    service = RevenueShareService(db)
    organizations = list(
        db.scalars(
            select(RevenueShareOrganization).order_by(
                RevenueShareOrganization.name,
                RevenueShareOrganization.id,
            )
        )
    )
    codes = list(
        db.scalars(select(ReferralCode).order_by(ReferralCode.created_at.desc(), ReferralCode.id))
    )
    rules = list(
        db.scalars(
            select(RevenueShareRule).order_by(
                RevenueShareRule.is_active.desc(),
                RevenueShareRule.effective_from.desc(),
                RevenueShareRule.created_at.desc(),
            )
        )
    )
    person_ids = {
        value
        for value in [
            *(code.referrer_person_id for code in codes),
            *(rule.recipient_person_id for rule in rules),
        ]
        if value is not None
    }
    organization_ids = {
        value
        for value in [
            *(code.referrer_organization_id for code in codes),
            *(rule.recipient_organization_id for rule in rules),
        ]
        if value is not None
    }
    person_names = _person_names(db, person_ids)
    organization_names = _organization_names(db, organization_ids)
    projects = list(db.scalars(select(GameProject).order_by(GameProject.name, GameProject.id)))
    project_names = {project.id: project.name for project in projects}

    def beneficiary_name(
        person_id: UUID | None,
        organization_id: UUID | None,
    ) -> str | None:
        if person_id is not None:
            return person_names.get(person_id, "已删除人员")
        if organization_id is not None:
            return organization_names.get(organization_id, "已停用机构")
        return None

    return RevenueShareConfigurationResponse(
        organizations=[_organization_response(item) for item in organizations],
        referral_codes=[
            _referral_code_response(
                code,
                source_name=beneficiary_name(
                    code.referrer_person_id,
                    code.referrer_organization_id,
                )
                or "未知来源",
                registration_base_url=registration_base_url,
            )
            for code in codes
        ],
        rules=[
            RevenueShareRuleResponse(
                id=rule.id,
                project_id=rule.project_id,
                project_name=(project_names.get(rule.project_id) if rule.project_id else None),
                role_kind=cast(RevenueShareRole, rule.role_kind),
                recipient_person_id=rule.recipient_person_id,
                recipient_organization_id=rule.recipient_organization_id,
                recipient_name=beneficiary_name(
                    rule.recipient_person_id,
                    rule.recipient_organization_id,
                ),
                label=rule.label,
                calculation_type=cast(CalculationType, rule.calculation_type),
                percentage_basis_points=rule.percentage_basis_points,
                fixed_amount_cents=rule.fixed_amount_cents,
                effective_from=rule.effective_from,
                effective_to=rule.effective_to,
                is_active=rule.is_active,
            )
            for rule in rules
        ],
        people=[
            RevenueShareBeneficiaryOption(id=person_id, name=name)
            for person_id, name in service.people_options()
        ],
        projects=[
            RevenueShareProjectOption(id=project.id, name=project.name) for project in projects
        ],
    )


def _balance_items(
    service: RevenueShareService,
    balances: list[Balance],
) -> list[RevenueShareBalanceItem]:
    names = service.beneficiary_names(balances)
    return [
        RevenueShareBalanceItem(
            beneficiary_kind=cast(BeneficiaryKind, balance.beneficiary.kind),
            beneficiary_id=balance.beneficiary.id,
            beneficiary_name=names.get(
                (balance.beneficiary.kind, balance.beneficiary.id),
                "已删除收款对象",
            ),
            earned_cents=balance.earned_cents,
            settled_cents=balance.settled_cents,
            outstanding_cents=balance.outstanding_cents,
        )
        for balance in sorted(
            balances,
            key=lambda item: (
                names.get((item.beneficiary.kind, item.beneficiary.id), ""),
                item.beneficiary.kind,
                str(item.beneficiary.id),
            ),
        )
    ]


@router.get("/configuration", response_model=RevenueShareConfigurationResponse)
def get_configuration(
    request: Request, context: Context, db: Db, response: Response
) -> RevenueShareConfigurationResponse:
    _require_permission(context, db, "revenue_share.manage")
    response.headers["Cache-Control"] = "no-store, private"
    return _configuration(
        db,
        registration_base_url=request.app.state.settings.erp_web_base_url,
    )


@router.post(
    "/organizations",
    response_model=RevenueShareOrganizationResponse,
    status_code=201,
)
def create_organization(
    input: RevenueShareOrganizationCreate,
    request: Request,
    context: Context,
    db: Db,
) -> RevenueShareOrganizationResponse:
    _require_permission(context, db, "revenue_share.manage")
    try:
        row = RevenueShareService(db).create_organization(
            input,
            actor_user_id=context.account.id,
            request_id=_request_id(request),
        )
    except RevenueShareError as exc:
        _raise_revenue_share_error(exc)
    return _organization_response(row)


@router.post("/referral-codes", response_model=ReferralCodeResponse, status_code=201)
def create_referral_code(
    input: ReferralCodeCreate,
    request: Request,
    context: Context,
    db: Db,
) -> ReferralCodeResponse:
    _require_permission(context, db, "revenue_share.manage")
    try:
        row = RevenueShareService(db).create_referral_code(
            input,
            actor_user_id=context.account.id,
            request_id=_request_id(request),
        )
    except RevenueShareError as exc:
        _raise_revenue_share_error(exc)
    source_name = (
        _person_names(db, {row.referrer_person_id}).get(row.referrer_person_id)
        if row.referrer_person_id is not None
        else _organization_names(db, {row.referrer_organization_id}).get(
            row.referrer_organization_id
        )
        if row.referrer_organization_id is not None
        else None
    )
    return _referral_code_response(
        row,
        source_name=source_name or "未知来源",
        registration_base_url=request.app.state.settings.erp_web_base_url,
    )


@router.post("/rules", response_model=RevenueShareRuleResponse, status_code=201)
def create_rule(
    input: RevenueShareRuleCreate,
    request: Request,
    context: Context,
    db: Db,
) -> RevenueShareRuleResponse:
    _require_permission(context, db, "revenue_share.manage")
    try:
        row = RevenueShareService(db).create_rule(
            input,
            actor_user_id=context.account.id,
            request_id=_request_id(request),
        )
    except RevenueShareError as exc:
        _raise_revenue_share_error(exc)
    return next(
        item
        for item in _configuration(
            db,
            registration_base_url=request.app.state.settings.erp_web_base_url,
        ).rules
        if item.id == row.id
    )


@router.post(
    "/rules/{rule_id}/disable",
    response_model=RevenueShareRuleDisableResponse,
)
def disable_rule(
    rule_id: UUID,
    request: Request,
    context: Context,
    db: Db,
) -> RevenueShareRuleDisableResponse:
    _require_permission(context, db, "revenue_share.manage")
    try:
        row = RevenueShareService(db).disable_rule(
            rule_id,
            actor_user_id=context.account.id,
            request_id=_request_id(request),
        )
    except RevenueShareError as exc:
        _raise_revenue_share_error(exc)
    return RevenueShareRuleDisableResponse(id=row.id, is_active=False)


@router.post("/reconcile", response_model=RevenueShareReconcileResponse)
def reconcile_payments(
    request: Request,
    context: Context,
    db: Db,
    input: RevenueShareReconcileRequest | None = None,
) -> RevenueShareReconcileResponse:
    _require_permission(context, db, "revenue_share.manage")
    options = input or RevenueShareReconcileRequest()
    try:
        result = RevenueShareService(db).reconcile_batch(
            batch_size=options.batch_size,
            cursor=options.cursor,
            actor_user_id=context.account.id,
            request_id=_request_id(request),
        )
    except RevenueShareError as exc:
        _raise_revenue_share_error(exc)
    return RevenueShareReconcileResponse(
        processed=result.processed,
        allocated=result.allocated,
        pending=result.pending,
        has_more=result.has_more,
        next_cursor=result.next_cursor,
    )


@router.get("/finance", response_model=RevenueShareFinanceSummary)
def finance_summary(context: Context, db: Db, response: Response) -> RevenueShareFinanceSummary:
    _require_permission(context, db, "revenue_share.finance")
    service = RevenueShareService(db)
    balances = service.balances()
    response.headers["Cache-Control"] = "no-store, private"
    return RevenueShareFinanceSummary(
        earned_cents=sum(item.earned_cents for item in balances),
        settled_cents=sum(item.settled_cents for item in balances),
        outstanding_cents=sum(item.outstanding_cents for item in balances),
        pending_payment_count=service.pending_payment_count(),
        items=_balance_items(service, balances),
    )


@router.post(
    "/settlements",
    response_model=RevenueShareSettlementResponse,
    status_code=201,
)
def create_settlement(
    input: RevenueShareSettlementCreate,
    request: Request,
    context: Context,
    db: Db,
) -> RevenueShareSettlementResponse:
    _require_permission(context, db, "revenue_share.finance")
    service = RevenueShareService(db)
    try:
        row = service.create_settlement(
            input,
            actor_user_id=context.account.id,
            request_id=_request_id(request),
        )
    except RevenueShareError as exc:
        _raise_revenue_share_error(exc)
    balance = service.balance_for(input.beneficiary_kind, input.beneficiary_id)
    name = service.beneficiary_names([balance]).get(
        (input.beneficiary_kind, input.beneficiary_id),
        "已删除收款对象",
    )
    return RevenueShareSettlementResponse(
        id=row.id,
        beneficiary_kind=input.beneficiary_kind,
        beneficiary_id=input.beneficiary_id,
        beneficiary_name=name,
        amount_cents=row.amount_cents,
        settled_on=row.settled_on,
        reference=row.reference,
        note=row.note,
        created_at=row.created_at,
    )


@router.post("/finance/exports")
def export_finance_summary(
    input: RevenueShareExportRequest,
    request: Request,
    context: Context,
    db: Db,
) -> Response:
    _require_permission(context, db, "revenue_share.export")
    service = RevenueShareService(db)
    items = _balance_items(service, service.balances())
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["收款对象类型", "收款对象", "累计分成（元）", "已结算（元）", "未结算（元）"])
    kind_labels = {"person": "个人", "organization": "机构"}
    for item in items:
        writer.writerow(
            [
                kind_labels[item.beneficiary_kind],
                _safe_csv_text(item.beneficiary_name),
                f"{item.earned_cents / 100:.2f}",
                f"{item.settled_cents / 100:.2f}",
                f"{item.outstanding_cents / 100:.2f}",
            ]
        )
    with write_transaction(db):
        AuditService(db).append(
            actor_user_id=context.account.id,
            action="revenue_share.finance.export",
            resource_type="revenue_share_finance_summary",
            resource_id=None,
            purpose=input.purpose,
            outcome="allowed",
            request_id=_request_id(request),
            metadata={"item_count": len(items)},
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store, private",
            "Content-Disposition": 'attachment; filename="revenue-share-summary.csv"',
        },
    )


@router.put("/me/referral-code", response_model=ReferralCodeResponse)
def ensure_my_referral_code(
    request: Request,
    context: Context,
    db: Db,
    response: Response,
) -> ReferralCodeResponse:
    tokens = RbacService(db).person_resource_tokens(
        actor_person_id=context.person.id,
        target_person_id=context.person.id,
    )
    _require_permission(
        context,
        db,
        "revenue_share.create_self_referral",
        tokens=tokens,
    )
    try:
        row = RevenueShareService(db).ensure_person_referral_code(
            person_id=context.person.id,
            actor_user_id=context.account.id,
            request_id=_request_id(request),
        )
    except RevenueShareError as exc:
        _raise_revenue_share_error(exc)
    response.headers["Cache-Control"] = "no-store, private"
    return _referral_code_response(
        row,
        source_name=context.person.full_name,
        registration_base_url=request.app.state.settings.erp_web_base_url,
    )


@router.get("/me", response_model=RevenueShareSelfResponse)
def my_revenue_share(
    request: Request,
    context: Context,
    db: Db,
    response: Response,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=768)] = None,
) -> RevenueShareSelfResponse:
    tokens = RbacService(db).person_resource_tokens(
        actor_person_id=context.person.id,
        target_person_id=context.person.id,
    )
    _require_permission(
        context,
        db,
        "revenue_share.read_self",
        tokens=tokens,
    )
    service = RevenueShareService(db)
    try:
        ledger = service.self_ledger(context.person.id, page_size=page_size, cursor=cursor)
    except RevenueShareError as exc:
        _raise_revenue_share_error(exc)
    balance = service.balance_for("person", context.person.id)
    referral_codes = list(
        db.scalars(
            select(ReferralCode)
            .where(
                ReferralCode.referrer_person_id == context.person.id,
                ReferralCode.is_active.is_(True),
            )
            .order_by(ReferralCode.created_at.desc(), ReferralCode.id)
        )
    )
    response.headers["Cache-Control"] = "no-store, private"
    return RevenueShareSelfResponse(
        earned_cents=balance.earned_cents,
        settled_cents=balance.settled_cents,
        outstanding_cents=balance.outstanding_cents,
        items=[
            RevenueShareLedgerItem(
                id=item.id,
                payment_id=item.payment_id,
                entry_kind=cast(RevenueShareEntryKind, item.entry_kind),
                role_kind=cast(RevenueShareRole, item.role_kind),
                amount_cents=item.amount_cents,
                occurred_at=item.occurred_at,
            )
            for item in ledger.items
        ],
        referral_codes=[
            _referral_code_response(
                item,
                source_name=context.person.full_name,
                registration_base_url=request.app.state.settings.erp_web_base_url,
            )
            for item in referral_codes
        ],
        total=ledger.total,
        page_size=page_size,
        has_more=ledger.has_more,
        next_cursor=ledger.next_cursor,
    )
