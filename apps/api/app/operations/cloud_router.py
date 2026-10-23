from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import _secret_bytes, require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.operations.cloud_schemas import (
    CloudBackupResponse,
    CloudJobResponse,
    CloudTargetResponse,
    CloudTargetUpdate,
    Provider,
)
from app.operations.cloud_service import CloudBackupService
from app.rbac.service import RbacService
from app.security.encryption import EncryptedFieldError, EnvelopeCipher

router = APIRouter(prefix="/cloud-backups", tags=["operations"])


def _audit(
    service: CloudBackupService,
    request: Request,
    context: AuthContext,
    provider: Provider,
    action: str,
    resource_id: UUID | None = None,
) -> None:
    AuditService(service.db).append(
        actor_user_id=context.account.id,
        action=f"operations.cloud_backup.{provider}.{action}",
        resource_type="cloud_backup",
        resource_id=resource_id,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata={},
        purpose=None,
    )


def _service(
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> CloudBackupService:
    rbac = RbacService(db)
    if (
        not rbac.account_has_role(context.account.id, "super_admin")
        or not rbac.decide(
            user_id=context.account.id,
            permission="operations.monitor.read",
            resource_tokens={"global"},
        ).allowed
    ):
        raise ProblemError(
            status=403, code="permission_denied", detail="只有超级管理员可以管理云备份"
        )
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return CloudBackupService(
        db,
        cipher=EnvelopeCipher(
            _secret_bytes(request.app.state.settings, "field_encryption_master_key")
        ),
    )


@router.get("", response_model=CloudBackupResponse)
def get_cloud_backups(
    service: Annotated[CloudBackupService, Depends(_service)],
) -> CloudBackupResponse:
    try:
        return service.public()
    except EncryptedFieldError:
        raise ProblemError(
            status=503, code="cloud_credentials_invalid", detail="云备份凭据无法读取，请联系管理员"
        ) from None


@router.put("/{provider}", response_model=CloudTargetResponse)
def save_cloud_target(
    provider: Provider,
    input: CloudTargetUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CloudBackupService, Depends(_service)],
) -> CloudTargetResponse:
    try:
        with atomic_write_transaction(service.db):
            result = service.save(provider, input)
            _audit(service, request, context, provider, "update")
        return result
    except EncryptedFieldError:
        raise ProblemError(
            status=503, code="cloud_credentials_invalid", detail="云备份凭据无法读取，请联系管理员"
        ) from None


@router.post("/{provider}/test", response_model=CloudJobResponse)
def test_cloud_target(
    provider: Provider,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CloudBackupService, Depends(_service)],
) -> CloudJobResponse:
    with atomic_write_transaction(service.db):
        result = service.enqueue(provider, "test")
        _audit(service, request, context, provider, "test", result.id)
    return result


@router.post("/{provider}/copy", response_model=CloudJobResponse)
def copy_cloud_target(
    provider: Provider,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CloudBackupService, Depends(_service)],
) -> CloudJobResponse:
    with atomic_write_transaction(service.db):
        result = service.enqueue(provider, "copy")
        _audit(service, request, context, provider, "copy", result.id)
    return result
