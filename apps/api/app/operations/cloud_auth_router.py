from collections.abc import Callable, Coroutine, Iterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import _secret_bytes, require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.operations.cloud_auth_errors import auth_problem
from app.operations.cloud_auth_requests import (
    CloudDirectoryRequest,
    CloudDirectoryResult,
    CloudDirectorySelection,
    CloudImportCommand,
    CloudOperationCommand,
    CloudRevisionCommand,
    CloudSessionCreate,
    CloudUnbindCommand,
    CloudUnbindResponse,
)
from app.operations.cloud_auth_schemas import (
    AuthActor,
    AuthProvider,
    CloudAppUpdate,
    CloudAppView,
    CloudAuthSessionView,
    CloudImportOperationView,
    QrPresentation,
)
from app.operations.cloud_auth_security import require_auth_actor
from app.operations.cloud_auth_service import CloudAuthorizationService
from app.security.encryption import EncryptedFieldError, EnvelopeCipher


def _private(request: Request, response: Response) -> None:
    request.state.private_portal_response = True
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"


class PrivateAuthorizationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            # Body decoding precedes dependencies; mark privacy before decoding.
            request.state.private_portal_response = True
            # Count actual ASGI bytes before JSON parsing, including chunked requests.
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 16384:
                    raise auth_problem("cloud_auth_body_too_large", 413)
                body.extend(chunk)
            request._body = bytes(body)
            try:
                return await original(request)
            except RequestValidationError as exc:
                allowed = {
                    "access_token",
                    "refresh_token",
                    "operation_id",
                    "expected_revision",
                    "mode",
                    "client_id",
                    "client_secret",
                    "clear_secret",
                    "parent",
                    "directory",
                    "provider",
                    "session_id",
                }
                fields = set()
                for error in exc.errors():
                    loc = error.get("loc", ())
                    field = loc[1] if len(loc) == 2 and loc[1] in allowed else "request"
                    fields.add(field)
                raise ProblemError(
                    status=422,
                    code="validation_error",
                    detail="授权请求格式无效",
                    errors=[
                        {"field": field, "message": "字段格式无效"} for field in sorted(fields)
                    ],
                ) from None

        return private_handler


router = APIRouter(
    prefix="/cloud-backups/{provider}/authorization",
    tags=["operations"],
    dependencies=[Depends(_private)],
    route_class=PrivateAuthorizationRoute,
)


def _actor(context: AuthContext) -> AuthActor:
    return AuthActor(account_id=context.account.id, login_session_id=context.session.id)


def _service(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> Iterator[CloudAuthorizationService]:
    require_auth_actor(db, _actor(context))
    service = CloudAuthorizationService(
        db,
        settings=request.app.state.settings,
        cipher=EnvelopeCipher(
            _secret_bytes(request.app.state.settings, "field_encryption_master_key")
        ),
    )
    try:
        yield service
    except EncryptedFieldError:
        raise auth_problem("cloud_credentials_invalid", 503) from None


Service = Annotated[CloudAuthorizationService, Depends(_service)]
Context = Annotated[AuthContext, Depends(require_standard_account)]


def _audit(
    service: CloudAuthorizationService,
    request: Request,
    context: AuthContext,
    provider: AuthProvider,
    action: str,
    resource_id: UUID | None = None,
) -> None:
    AuditService(service.db).append(
        actor_user_id=context.account.id,
        action=f"operations.cloud_authorization.{provider}.{action}",
        resource_type="cloud_authorization",
        resource_id=resource_id,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata={},
        purpose=None,
    )


def _owned(
    service: CloudAuthorizationService,
    provider: AuthProvider,
    session_id: UUID,
    context: AuthContext,
) -> CloudAuthSessionView:
    result = service.get_session(session_id, _actor(context))
    if result.provider != provider:
        raise auth_problem("cloud_auth_not_found", 404)
    return result


@router.get("/application", response_model=CloudAppView)
def get_application(provider: AuthProvider, context: Context, service: Service) -> CloudAppView:
    return service.get_application(provider, _actor(context))


@router.put("/application", response_model=CloudAppView)
def save_application(
    provider: AuthProvider,
    input: CloudAppUpdate,
    request: Request,
    context: Context,
    service: Service,
) -> CloudAppView:
    with atomic_write_transaction(service.db):
        result = service.save_application(provider, _actor(context), input)
        _audit(service, request, context, provider, "application_update")
        return result


@router.post("/sessions", response_model=CloudAuthSessionView, status_code=202)
def create_session(
    provider: AuthProvider,
    input: CloudSessionCreate,
    request: Request,
    context: Context,
    service: Service,
) -> CloudAuthSessionView:
    with atomic_write_transaction(service.db):
        result = service.create_session(
            provider, _actor(context), input.operation_id, mode=input.mode
        )
        _audit(service, request, context, provider, "create", result.id)
        return result


@router.get("/sessions/{session_id}", response_model=CloudAuthSessionView)
def get_session(
    provider: AuthProvider, session_id: UUID, context: Context, service: Service
) -> CloudAuthSessionView:
    return _owned(service, provider, session_id, context)


@router.post(
    "/sessions/{session_id}/import", response_model=CloudImportOperationView, status_code=202
)
def submit_import(
    provider: AuthProvider,
    session_id: UUID,
    input: CloudImportCommand,
    request: Request,
    response: Response,
    context: Context,
    service: Service,
) -> CloudImportOperationView:
    with atomic_write_transaction(service.db):
        _owned(service, provider, session_id, context)
        result = service.submit_import(session_id, _actor(context), input)
        response.status_code = 202 if result.status == "pending" else 200
        _audit(service, request, context, provider, "import", session_id)
        return result


@router.get(
    "/sessions/{session_id}/operations/{operation_id}", response_model=CloudImportOperationView
)
def get_import_operation(
    provider: AuthProvider,
    session_id: UUID,
    operation_id: UUID,
    response: Response,
    context: Context,
    service: Service,
) -> CloudImportOperationView:
    _owned(service, provider, session_id, context)
    result = service.get_import_operation(session_id, _actor(context), operation_id)
    response.status_code = 202 if result.status == "pending" else 200
    return result


@router.get("/sessions/{session_id}/qr", response_model=QrPresentation)
def get_qr(
    provider: AuthProvider, session_id: UUID, context: Context, service: Service
) -> QrPresentation:
    _owned(service, provider, session_id, context)
    return service.get_qr(session_id, _actor(context))


@router.post("/sessions/{session_id}/cancel", response_model=CloudAuthSessionView)
def cancel_session(
    provider: AuthProvider,
    session_id: UUID,
    input: CloudRevisionCommand,
    request: Request,
    context: Context,
    service: Service,
) -> CloudAuthSessionView:
    with atomic_write_transaction(service.db):
        _owned(service, provider, session_id, context)
        result = service.cancel_session(session_id, _actor(context), input.expected_revision)
        _audit(service, request, context, provider, "cancel", session_id)
        return result


@router.post(
    "/sessions/{session_id}/directories", response_model=CloudDirectoryResult, status_code=202
)
def queue_directories(
    provider: AuthProvider,
    session_id: UUID,
    input: CloudDirectoryRequest,
    request: Request,
    context: Context,
    service: Service,
) -> CloudDirectoryResult:
    with atomic_write_transaction(service.db):
        _owned(service, provider, session_id, context)
        result = service.queue_directories(session_id, _actor(context), input)
        _audit(service, request, context, provider, "directories", session_id)
        return result


@router.get("/sessions/{session_id}/directories", response_model=CloudDirectoryResult)
def get_directories(
    provider: AuthProvider,
    session_id: UUID,
    operation_id: Annotated[UUID, Query()],
    response: Response,
    context: Context,
    service: Service,
) -> CloudDirectoryResult:
    _owned(service, provider, session_id, context)
    result = service.get_directories(session_id, _actor(context), operation_id)
    response.status_code = 202 if result.status == "pending" else 200
    return result


@router.post(
    "/sessions/{session_id}/select-directory", response_model=CloudAuthSessionView, status_code=202
)
def select_directory(
    provider: AuthProvider,
    session_id: UUID,
    input: CloudDirectorySelection,
    request: Request,
    context: Context,
    service: Service,
) -> CloudAuthSessionView:
    with atomic_write_transaction(service.db):
        _owned(service, provider, session_id, context)
        result = service.select_directory(session_id, _actor(context), input)
        _audit(service, request, context, provider, "select_directory", session_id)
        return result


@router.post("/sessions/{session_id}/test", response_model=CloudAuthSessionView, status_code=202)
def queue_test(
    provider: AuthProvider,
    session_id: UUID,
    input: CloudOperationCommand,
    request: Request,
    context: Context,
    service: Service,
) -> CloudAuthSessionView:
    with atomic_write_transaction(service.db):
        _owned(service, provider, session_id, context)
        result = service.queue_test(session_id, _actor(context), input)
        _audit(service, request, context, provider, "test", session_id)
        return result


@router.post("/sessions/{session_id}/apply", response_model=CloudAuthSessionView, status_code=202)
def apply_session(
    provider: AuthProvider,
    session_id: UUID,
    input: CloudOperationCommand,
    request: Request,
    context: Context,
    service: Service,
) -> CloudAuthSessionView:
    from app.operations.cloud_auth_apply import request_apply

    with atomic_write_transaction(service.db):
        _owned(service, provider, session_id, context)
        result = request_apply(
            service.db, _actor(context), session_id, input.expected_revision, input.operation_id
        )
        _audit(service, request, context, provider, "apply", session_id)
        return result


@router.post("/unbind", response_model=CloudUnbindResponse, status_code=202)
def unbind(
    provider: AuthProvider,
    input: CloudUnbindCommand,
    request: Request,
    context: Context,
    service: Service,
) -> CloudUnbindResponse:
    from app.operations.cloud_auth_apply import request_unbind

    with atomic_write_transaction(service.db):
        result = request_unbind(
            service.db, _actor(context), provider, input.expected_revision, input.operation_id
        )
        _audit(service, request, context, provider, "unbind", result.id)
        return CloudUnbindResponse(operation_id=input.operation_id, session_id=result.id)
