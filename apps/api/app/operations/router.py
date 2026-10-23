from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.transactions import write_transaction
from app.operations.cloud_status import read_cloud_evidence, with_cloud_copy
from app.operations.models import OperationsAlert, OperationsRequestMinute
from app.operations.probes import read_backup_status
from app.operations.schemas import (
    OperationsAlertPage,
    OperationsAlertResponse,
    OperationsBackupStatus,
    OperationsHistoryPage,
    OperationsHistoryPoint,
    OperationsOverview,
)
from app.operations.service import OperationsMonitorService
from app.rbac.service import RbacService

router = APIRouter(prefix="/operations", tags=["operations"])

from app.operations.cloud_auth_router import router as cloud_authorization_router
from app.operations.cloud_router import router as cloud_backup_router

router.include_router(cloud_backup_router)
router.include_router(cloud_authorization_router)


def _authorize(context: AuthContext, db: Session, response: Response) -> None:
    rbac = RbacService(db)
    if not rbac.account_has_role(context.account.id, "super_admin"):
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="只有超级管理员可以查看运维监控",
        )
    decision = rbac.decide(
        user_id=context.account.id,
        permission="operations.monitor.read",
        resource_tokens={"global"},
    )
    if not decision.allowed:
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )

    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


@router.get("/overview", response_model=OperationsOverview)
def get_operations_overview(
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    window_minutes: Annotated[int, Query(ge=5, le=1440)] = 60,
) -> OperationsOverview:
    _authorize(context, db, response)
    return OperationsMonitorService(
        db,
        request_metrics=request.app.state.operations_request_metrics,
        runtime_sampler=request.app.state.operations_runtime_sampler,
        scheduler_interval_seconds=request.app.state.settings.cohort_scheduler_interval_seconds,
    ).overview(window_minutes=window_minutes)


@router.get("/history", response_model=OperationsHistoryPage)
def get_operations_history(
    request: Request, response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    starts_at: Annotated[datetime | None, Query()] = None,
    ends_at: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> OperationsHistoryPage:
    _authorize(context, db, response)
    now = datetime.now(UTC)
    retention = request.app.state.settings.operations_retention_days
    start = starts_at or now - timedelta(days=1)
    end = ends_at or now
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ProblemError(status=422, code="invalid_time_range", detail="请提供含时区且开始早于结束的时间范围")
    start = max(start, now - timedelta(days=retention))
    model = OperationsRequestMinute
    query = select(
        model.starts_at,
        func.sum(model.request_count).label("request_count"),
        func.sum(case((model.status_code.between(400, 499), model.request_count), else_=0)).label("client_error_count"),
        func.sum(case((model.status_code >= 500, model.request_count), else_=0)).label("server_error_count"),
        func.sum(model.latency_sum_ms).label("latency_sum_ms"),
        func.max(model.max_latency_ms).label("max_latency_ms"),
    ).where(model.starts_at >= start, model.starts_at < end).group_by(model.starts_at)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(model.starts_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).mappings()
    items = [OperationsHistoryPoint(
        starts_at=row["starts_at"], request_count=row["request_count"],
        client_error_count=row["client_error_count"], server_error_count=row["server_error_count"],
        average_latency_ms=round(row["latency_sum_ms"] / row["request_count"], 2),
        max_latency_ms=round(row["max_latency_ms"], 2),
    ) for row in rows]
    return OperationsHistoryPage(items=items, total=total, page=page, page_size=page_size,
                                 retention_days=retention)


@router.get("/alerts", response_model=OperationsAlertPage)
def get_operations_alerts(
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[str, Query(pattern="^(active|resolved|all)$")] = "active",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OperationsAlertPage:
    _authorize(context, db, response)
    query = select(OperationsAlert)
    if status != "all":
        query = query.where(OperationsAlert.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = db.scalars(query.order_by(OperationsAlert.first_seen_at.desc(), OperationsAlert.id)
                       .offset((page - 1) * page_size).limit(page_size))
    return OperationsAlertPage(items=[OperationsAlertResponse.model_validate(row) for row in items],
                               total=total, page=page, page_size=page_size)


@router.post("/alerts/{alert_id}/acknowledge", response_model=OperationsAlertResponse)
def acknowledge_operations_alert(
    alert_id: UUID, request: Request, response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> OperationsAlertResponse:
    _authorize(context, db, response)
    with write_transaction(db):
        alert = db.scalar(select(OperationsAlert).where(OperationsAlert.id == alert_id).with_for_update())
        if alert is None:
            raise ProblemError(status=404, code="not_found", detail="运维告警不存在")
        if alert.acknowledged_at is None:
            alert.acknowledged_at = datetime.now(UTC)
            alert.acknowledged_by = context.account.id
            AuditService(db).append(
                actor_user_id=context.account.id, action="operations.alert.acknowledge",
                resource_type="operations_alert", resource_id=alert.id, outcome="allowed",
                request_id=str(request.state.request_id), metadata={}, purpose=None,
            )
    return OperationsAlertResponse.model_validate(alert)


@router.get("/backup-status", response_model=OperationsBackupStatus)
def get_operations_backup_status(
    request: Request, response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> OperationsBackupStatus:
    _authorize(context, db, response)
    backup = read_backup_status(request.app.state.settings.operations_backup_status_path)
    targets, _ = read_cloud_evidence(db)
    return with_cloud_copy(backup, targets, now=datetime.now(UTC))
