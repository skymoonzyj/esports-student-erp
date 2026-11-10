from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.housing.api_support import PrivateHousingRoute, _audit, _standalone_audit
from app.housing.dependencies import get_housing_reporting_db
from app.housing.occupancy_router import _mutation_context
from app.housing.property_export_service import HousingPropertyExportService, render_property_export
from app.housing.property_schemas import PropertyExportRequest
from app.housing.room_status_export_router import _current_access

router = APIRouter(prefix="/housing", tags=["housing"], route_class=PrivateHousingRoute)


@router.post("/property/exports")
def export(
    input: PropertyExportRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.property.export"))],
    db: Annotated[Session, Depends(get_db)],
    snapshot_db: Annotated[Session, Depends(get_housing_reporting_db)],
) -> Response:
    try:
        initial = _current_access(db, context)
        service = HousingPropertyExportService(snapshot_db, context.account.id)
        headers, rows = service.rows(input, as_of=datetime.now(UTC))
        content, media_type, filename = render_property_export(
            headers, rows, format=input.format, occurred_at=datetime.now(UTC)
        )
        live_service = HousingPropertyExportService(db, context.account.id)
        locked_rooms = live_service.lock_exported_rooms(service.exported_room_scopes)
        # A room lock can wait while RBAC, account or session state changes.
        # Do not reuse any authority observed before acquiring those locks.
        current = _current_access(db, context)
        if (
            initial.access_revision != service.access_revision
            or current.access_revision != initial.access_revision
        ):
            raise ProblemError(
                status=409, code="housing_access_changed", detail="权限已变化，请重新导出"
            )
        live_service.validate_exported_rooms(service.exported_room_scopes, locked_rooms, current)
        live_service._validate_scope(input, current)
        _audit(
            db,
            request,
            context,
            action="housing.property.export",
            resource_type="housing_property_export",
            resource_id=None,
            outcome="allowed",
            purpose=input.purpose,
            metadata={
                "format": input.format,
                "housing_export_kind": input.kind,
                "item_count": len(rows),
                "file_sha256": sha256(content).hexdigest(),
            },
        )
        db.commit()
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store, private",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ProblemError as exc:
        _standalone_audit(
            db,
            request,
            context,
            action="housing.property.export",
            resource_type="housing_property_export",
            resource_id=None,
            outcome="denied" if exc.status in {401, 403, 404} else "failed",
            metadata={"error_code": exc.code},
        )
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        snapshot_db.rollback()
