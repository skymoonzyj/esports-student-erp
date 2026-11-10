from json import JSONDecodeError
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.client_ip import request_source_ip
from app.core.errors import ProblemError
from app.reactionx import service_factory
from app.reactionx.commands import ClientService
from app.reactionx.dependencies import Connector, Database, PairingConnector, pairing_service
from app.reactionx.http_support import ReactionxRoute
from app.reactionx.models import ReactionxConnector
from app.reactionx.pairing import ClaimView
from app.reactionx.queries import AckRequest, CommandView, HeartbeatRequest, HeartbeatView, PollView
from app.reactionx.recovery import RecoveryService
from app.reactionx.results import strict_json
from app.reactionx.router import ConfirmRequest
from app.reactionx.schemas import ResultEnvelope, ResultReceipt, StrictModel

router = APIRouter(
    prefix="/reactionx-client", tags=["reactionx-client"], route_class=ReactionxRoute
)


class ClientInfo(StrictModel):
    origin: str = Field(max_length=255)
    name: str = Field(min_length=1, max_length=80)


class ClaimRequest(StrictModel):
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    credential_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    client_info: ClientInfo


@router.post("/pairings/claim", response_model=ClaimView)
async def claim(request: Request, db: Database) -> ClaimView:
    # A declared body DTO would make FastAPI parse/validate before this limit.
    # Keep transport parsing here, after a durable attempt, including bad JSON.
    service = pairing_service(request, db)
    if not getattr(request.state, "reactionx_claim_rate_applied", False):
        # Direct route embedding without the ASGI middleware remains protected.
        await run_in_threadpool(
            service.rate_claim,
            request_source_ip(
                request, trusted_proxy_cidrs=request.app.state.settings.trusted_proxy_cidrs
            ),
        )
    try:
        command = ClaimRequest.model_validate(await request.json())
    except (JSONDecodeError, UnicodeDecodeError, ValidationError):
        raise ProblemError(
            status=422, code="validation_error", detail="ReactionX 请求格式无效"
        ) from None
    return await run_in_threadpool(
        service._claim_after_rate,
        command.challenge,
        command.credential_digest,
        command.client_info.model_dump(),
        request_id=str(request.state.request_id),
    )


@router.get("/pairings/{pairing_id}", response_model=ClaimView)
def pairing_status(
    pairing_id: UUID, request: Request, db: Database, connector: PairingConnector
) -> ClaimView:
    return pairing_service(request, db).status(pairing_id, connector=connector)


@router.post("/pairings/{pairing_id}/confirm", response_model=ClaimView)
def confirm(
    pairing_id: UUID,
    command: ConfirmRequest,
    request: Request,
    db: Database,
    connector: PairingConnector,
) -> ClaimView:
    return pairing_service(request, db).confirm_device(
        pairing_id, connector, command.verification_phrase, request_id=str(request.state.request_id)
    )


@router.get("/status")
def status(connector: Connector) -> dict[str, str]:
    return {"connector_id": str(connector.id), "status": connector.status}


def require_safety_connector(request: Request, db: Database) -> ReactionxConnector:
    # Exclusively these three safety endpoints: no personnel/results/lease rights.
    return pairing_service(request, db).authenticate(
        request.headers.get("Authorization"), safety_only=True
    )


SafetyConnector = Annotated[ReactionxConnector, Depends(require_safety_connector)]


def client_service(request: Request, db: Database) -> ClientService:
    return service_factory.client_service(db, request.app.state.settings)


@router.post("/heartbeat", response_model=HeartbeatView)
def heartbeat(
    command: HeartbeatRequest, request: Request, db: Database, connector: SafetyConnector
) -> HeartbeatView:
    return client_service(request, db).heartbeat(connector, command)


@router.get("/commands", response_model=PollView)
def poll_commands(
    request: Request,
    db: Database,
    connector: SafetyConnector,
    cursor: int = Query(default=0, ge=0, le=2147483647),
) -> PollView:
    return client_service(request, db).poll(connector, cursor)


@router.post("/commands/{operation_id}/ack", response_model=CommandView)
def acknowledge(
    operation_id: UUID,
    command: AckRequest,
    request: Request,
    db: Database,
    connector: SafetyConnector,
) -> CommandView:
    return client_service(request, db).ack(connector, operation_id, command)


@router.post("/runs/{run_id}/result", response_model=ResultReceipt)
async def submit_result(run_id: UUID, request: Request, db: Database) -> ResultReceipt:
    try:
        envelope = ResultEnvelope.model_validate(strict_json(await request.body()))
    except (ValueError, UnicodeError, RecursionError):
        raise ProblemError(
            status=422, code="reactionx_invalid_result", detail="ReactionX 结果格式无效"
        ) from None
    if envelope.run_id != str(run_id):
        raise ProblemError(
            status=422, code="reactionx_run_mismatch", detail="ReactionX 结果归属不符"
        )

    def submit() -> ResultReceipt:
        origin = request.app.state.settings.reactionx_public_origin
        connector = RecoveryService(db, origin=origin).authenticate_result(
            request.headers.get("Authorization")
        )
        return service_factory.result_service(db, request.app.state.settings).submit(
            connector, envelope
        )

    return await run_in_threadpool(submit)


@router.get("/runs/{run_id}/receipt", response_model=ResultReceipt)
def result_receipt(
    run_id: UUID, request: Request, db: Database, digest: str = Query(pattern=r"^[0-9a-f]{64}$")
) -> ResultReceipt:
    origin = request.app.state.settings.reactionx_public_origin
    connector = RecoveryService(db, origin=origin).authenticate_result(
        request.headers.get("Authorization")
    )
    return service_factory.result_service(db, request.app.state.settings).receipt(
        connector, run_id, digest
    )
