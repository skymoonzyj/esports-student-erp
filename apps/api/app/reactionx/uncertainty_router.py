"""Independent browser closure and original-credential evidence endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool

from app.reactionx.dependencies import Actor, Database
from app.reactionx.http_support import ReactionxRoute
from app.reactionx.results import strict_json
from app.reactionx.schemas import Digest, StrictModel, UUIDText
from app.reactionx.uncertainty import (
    ArchiveRequest,
    UncertaintyDeclaration,
    UncertaintyPage,
    UncertaintyReceipt,
    UncertaintyService,
    UncertaintyStatus,
    rejected,
)

browser_router = APIRouter(prefix="/reactionx", tags=["reactionx"], route_class=ReactionxRoute)
client_router = APIRouter(
    prefix="/reactionx-client", tags=["reactionx-client"], route_class=ReactionxRoute
)


class RequestArchiveBody(StrictModel):
    declaration_digest: Digest
    reason: str
    confirmation: bool


class ConfirmArchiveBody(StrictModel):
    request_id: UUIDText
    confirmation: bool


def service(request: Request, db: Database) -> UncertaintyService:
    return UncertaintyService(db, origin=request.app.state.settings.reactionx_public_origin)


async def body(request: Request, model: type[StrictModel]) -> StrictModel:
    try:
        return model.model_validate(strict_json(await request.body()))
    except (ValueError, UnicodeError, RecursionError):
        raise rejected(422, "reactionx_uncertainty_invalid") from None


@client_router.post("/runs/{run_id}/uncertainty", response_model=UncertaintyStatus)
async def declare(run_id: UUID, request: Request, db: Database) -> UncertaintyStatus:
    declaration = await body(request, UncertaintyDeclaration)
    assert isinstance(declaration, UncertaintyDeclaration)
    if declaration.run_id != str(run_id):
        raise rejected(422)

    def write() -> UncertaintyStatus:
        backend = service(request, db)
        connector = backend.recovery.authenticate_result(request.headers.get("Authorization"))
        return backend.declare(connector, run_id, declaration)

    return await run_in_threadpool(write)


@client_router.get("/runs/{run_id}/uncertainty", response_model=UncertaintyStatus)
def status(
    run_id: UUID,
    declaration_digest: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")],
    request: Request,
    db: Database,
) -> UncertaintyStatus:
    backend = service(request, db)
    connector = backend.recovery.authenticate_result(request.headers.get("Authorization"))
    return backend.status(connector, run_id, declaration_digest)


@browser_router.get("/uncertainties", response_model=UncertaintyPage)
def pending(
    request: Request,
    db: Database,
    actor: Actor,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> UncertaintyPage:
    return service(request, db).list_pending(actor, offset, limit)


@browser_router.post("/runs/{run_id}/uncertainty/request", response_model=ArchiveRequest)
async def request_archive(
    run_id: UUID, request: Request, db: Database, actor: Actor
) -> ArchiveRequest:
    command = await body(request, RequestArchiveBody)
    assert isinstance(command, RequestArchiveBody)
    return await run_in_threadpool(
        service(request, db).request_archive,
        actor,
        run_id,
        command.declaration_digest,
        command.reason,
        command.confirmation,
    )


@browser_router.post("/runs/{run_id}/uncertainty/confirm", response_model=UncertaintyReceipt)
async def confirm_archive(
    run_id: UUID, request: Request, db: Database, actor: Actor
) -> UncertaintyReceipt:
    command = await body(request, ConfirmArchiveBody)
    assert isinstance(command, ConfirmArchiveBody)
    return await run_in_threadpool(
        service(request, db).confirm_archive,
        actor,
        run_id,
        UUID(command.request_id),
        command.confirmation,
    )
