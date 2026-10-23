from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected
from app.auth.dependencies import require_standard_account
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.housing.access import HousingAccessService
from app.housing.api_support import PrivateHousingRoute, _audit
from app.housing.dependencies import (
    get_housing_cipher,
    get_housing_lookup,
    get_housing_reporting_db,
)
from app.housing.room_status_export_renderer import render_room_status_export
from app.housing.room_status_export_schemas import HousingRoomStatusExportRequest
from app.housing.room_status_export_service import HousingRoomStatusExportService
from app.identity.models import Person
from app.security.encryption import EnvelopeCipher, LookupDigest

router = APIRouter(prefix="/housing", tags=["housing"], route_class=PrivateHousingRoute)


def _current_access(db: Session, context: AuthContext) -> HousingAccessService:
    """Fresh READ COMMITTED reads, never the export's historical authorization."""
    db.expire_all()
    now = datetime.now(UTC)
    account = db.scalar(
        select(UserAccount)
        .where(UserAccount.id == context.account.id)
        .execution_options(populate_existing=True)
    )
    session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == context.session.id)
        .execution_options(populate_existing=True)
    )
    person = db.scalar(
        select(Person)
        .where(Person.id == context.person.id)
        .execution_options(populate_existing=True)
    )
    if (
        account is None
        or account.status != "active"
        or account.person_id != context.person.id
        or person is None
        or person.deleted_at is not None
        or session is None
        or session.user_id != context.account.id
        or session.revoked_at is not None
        or session.expires_at <= now
    ):
        raise ProblemError(status=401, code="invalid_session", detail="登录已失效，请重新登录")
    if account.force_password_change:
        raise ProblemError(
            status=403, code="force_password_change_required", detail="请先修改临时密码"
        )
    return HousingAccessService(db, account.id, now)


@router.post("/room-status/exports")
def export_room_status(
    input: HousingRoomStatusExportRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    snapshot_db: Annotated[Session, Depends(get_housing_reporting_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> Response:
    occurred_at = datetime.now(UTC)
    try:
        initial_access = _current_access(db, context)
        service = HousingRoomStatusExportService(
            snapshot_db,
            cipher=cipher,
            lookup=lookup,
            actor_user_id=context.account.id,
        )
        rows = service.rows(input, as_of=occurred_at)
        content, media_type, filename = render_room_status_export(
            rows,
            format=input.format,
            occurred_at=occurred_at,
        )
        current_access = _current_access(db, context)
        if not (
            initial_access.access_revision
            == service.access_revision
            == current_access.access_revision
        ):
            raise ProblemError(
                status=409,
                code="housing_access_changed",
                detail="房态权限已发生变化，请刷新后重新导出",
            )
        HousingRoomStatusExportService(
            db,
            cipher=cipher,
            lookup=lookup,
            actor_user_id=context.account.id,
        )._validate_scope(input, current_access)
        _audit(
            db,
            request,
            context,
            action="housing.room_status.export",
            resource_type="housing_room_status_export",
            resource_id=None,
            outcome="allowed",
            purpose=input.purpose,
            metadata={
                "format": input.format,
                "item_count": len(rows),
                "file_sha256": sha256(content).hexdigest(),
            },
        )
        db.commit()
    except AuditMetadataRejected as exc:
        db.rollback()
        raise ProblemError(
            status=500, code="housing_audit_failed", detail="房态审计记录失败"
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise ProblemError(
            status=503,
            code="housing_export_snapshot_unavailable",
            detail="房态导出暂时不可用，请稍后重试",
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise ProblemError(
            status=422,
            code="housing_export_failed",
            detail="房态导出失败，请检查入住实名资料后重试",
        ) from exc
    finally:
        snapshot_db.rollback()
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )
