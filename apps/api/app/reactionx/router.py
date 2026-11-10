from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field, StrictBool
from sqlalchemy import or_, select
from starlette.background import BackgroundTask
from starlette.types import Receive, Scope, Send

from app.assessment_rankings.schemas import ItemRankingView
from app.core.errors import ProblemError
from app.identity.models import Person
from app.reactionx import service_factory
from app.reactionx.access import ReactionxAccess
from app.reactionx.dependencies import Actor, Database, pairing_service
from app.reactionx.http_support import ReactionxRoute
from app.reactionx.pairing import ClaimView, PairingView
from app.reactionx.queries import (
    CommandView,
    ConnectorView,
    DeviceCommandRequest,
    LeaseRequest,
    LeaseView,
    ManualRequest,
    OperationRequest,
    PairRequest,
    PrepareRequest,
    RunView,
    mode_availability,
)
from app.reactionx.recovery import RecoveryRequest, RecoveryService, UploadPermit
from app.reactionx.release_bundle import BundleError
from app.reactionx.releases import ReleaseService, VerifiedFile
from app.reactionx.results import ResultPage, ResultReport, ResultService
from app.reactionx.runs import RunService
from app.reactionx.schemas import Digest, StrictModel

router = APIRouter(prefix="/reactionx", tags=["reactionx"], route_class=ReactionxRoute)


class ConfirmRequest(StrictModel):
    verification_phrase: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9-]+$")


@router.post("/pairings", response_model=PairingView)
def create_pairing(
    request: Request, db: Database, actor: Actor, command: PairRequest | None = None
) -> PairingView:
    return pairing_service(request, db).create(
        actor,
        str(request.state.request_id),
        browser_tab_id=UUID(command.browser_tab_id) if command and command.browser_tab_id else None,
    )


@router.get("/pairings/{pairing_id}", response_model=ClaimView)
def pairing_status(pairing_id: UUID, request: Request, db: Database, actor: Actor) -> ClaimView:
    return pairing_service(request, db).status(pairing_id, actor=actor)


@router.post("/pairings/{pairing_id}/confirm", response_model=ClaimView)
def confirm_pairing(
    pairing_id: UUID, command: ConfirmRequest, request: Request, db: Database, actor: Actor
) -> ClaimView:
    return pairing_service(request, db).confirm_browser(
        pairing_id, actor, command.verification_phrase, request_id=str(request.state.request_id)
    )


@router.delete("/connectors/{connector_id}", status_code=204)
def revoke_connector(connector_id: UUID, request: Request, db: Database, actor: Actor) -> Response:
    pairing_service(request, db).revoke(
        connector_id, actor, request_id=str(request.state.request_id)
    )
    return Response(status_code=204)


@router.get("/people")
def people(
    db: Database, actor: Actor, query: str = Query(default="", max_length=80)
) -> list[dict[str, str | None]]:
    access = ReactionxAccess(db, actor)
    access.require_conduct()
    statement = select(Person).where(Person.id.in_(access.conduct_person_ids()))
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(
            or_(
                Person.full_name.ilike(f"%{escaped}%", escape="\\"),
                Person.nickname.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    result = [
        {"id": str(person.id), "name": person.full_name, "nickname": person.nickname}
        for person in db.scalars(statement.order_by(Person.full_name, Person.id).limit(50))
    ]
    access.require_conduct()
    return result


@router.delete("/pairings/{pairing_id}", status_code=204)
def cancel_pairing(pairing_id: UUID, request: Request, db: Database, actor: Actor) -> Response:
    pairing_service(request, db).cancel(pairing_id, actor, request_id=str(request.state.request_id))
    return Response(status_code=204)


def run_service(request: Request, db: Database) -> RunService:
    return service_factory.run_service(db, request.app.state.settings)


@router.get("/releases/current")
def current_release(db: Database, actor: Actor) -> dict[str, object]:
    service = ReleaseService(authorize=lambda: ReactionxAccess(db, actor).require_manage())
    try:
        return asdict(service.current())
    except BundleError:
        raise ProblemError(
            status=503, code="reactionx_release_unavailable", detail="安装包尚不可用"
        ) from None


@router.get("/releases/{release_id}/download")
def download_release(release_id: str, db: Database, actor: Actor) -> StreamingResponse:
    service = ReleaseService(authorize=lambda: ReactionxAccess(db, actor).require_manage())
    try:
        verified = service.open_verified(release_id)
    except BundleError:
        raise ProblemError(
            status=404, code="reactionx_release_unavailable", detail="安装包尚不可用"
        ) from None
    try:
        return VerifiedDownloadResponse(verified)
    except BaseException:
        verified.close()
        raise


class VerifiedDownloadResponse(StreamingResponse):
    """Close even when the transport fails before iteration/background starts."""

    def __init__(self, verified: VerifiedFile) -> None:
        self.verified = verified
        super().__init__(
            verified.chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="ReactionXConnectorSetup.exe"',
                "Cache-Control": "no-store, private",
            },
            background=BackgroundTask(verified.close),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.verified.close()


@router.get("/modes")
def modes(db: Database, actor: Actor) -> list[dict[str, str | bool | None]]:
    ReactionxAccess(db, actor).require_conduct()
    return mode_availability()


@router.get("/connectors", response_model=list[ConnectorView])
def connectors(request: Request, db: Database, actor: Actor) -> list[ConnectorView]:
    return run_service(request, db).connectors(actor)


@router.post("/connectors/{connector_id}/commands", status_code=202, response_model=CommandView)
def device_command(
    connector_id: UUID, command: DeviceCommandRequest, request: Request, db: Database, actor: Actor
) -> CommandView:
    return run_service(request, db).device_command(actor, connector_id, command)


@router.post("/runs/prepare", status_code=202, response_model=RunView)
def prepare_run(command: PrepareRequest, request: Request, db: Database, actor: Actor) -> RunView:
    return run_service(request, db).prepare(
        actor,
        UUID(command.person_id),
        UUID(command.connector_id),
        command,
        UUID(command.operation_id),
    )


@router.get("/runs/{run_id}", response_model=RunView)
def get_run(run_id: UUID, request: Request, db: Database, actor: Actor) -> RunView:
    return run_service(request, db).get(actor, run_id)


@router.post("/runs/{run_id}/lease", response_model=LeaseView)
def lease_run(
    run_id: UUID, command: LeaseRequest, request: Request, db: Database, actor: Actor
) -> LeaseView:
    return run_service(request, db).renew_lease(actor, run_id, UUID(command.tab_id))


@router.post("/runs/{run_id}/start", status_code=202, response_model=CommandView)
def start_run(
    run_id: UUID, command: OperationRequest, request: Request, db: Database, actor: Actor
) -> CommandView:
    return run_service(request, db).command(
        actor, run_id, "start", UUID(command.operation_id), tab_id=UUID(command.tab_id)
    )


@router.post("/runs/{run_id}/stop", status_code=202, response_model=CommandView)
def stop_run(
    run_id: UUID, command: OperationRequest, request: Request, db: Database, actor: Actor
) -> CommandView:
    return run_service(request, db).command(
        actor, run_id, "stop", UUID(command.operation_id), tab_id=UUID(command.tab_id)
    )


@router.post("/runs/{run_id}/manual-activate", status_code=202, response_model=CommandView)
def manual_activate(
    run_id: UUID, command: ManualRequest, request: Request, db: Database, actor: Actor
) -> CommandView:
    return run_service(request, db).command(
        actor,
        run_id,
        "manual_activate",
        UUID(command.operation_id),
        tab_id=UUID(command.tab_id),
        payload=command.payload,
    )


class UploadAuthorizationRequest(StrictModel):
    digest: Digest
    confirm_original_package: StrictBool


class RecoveryAuthorizationRequest(StrictModel):
    digest: Digest
    reason: str = Field(min_length=10, max_length=500)
    confirmation: StrictBool


class RecoveryConfirmationRequest(StrictModel):
    confirmation: StrictBool


def recovery_service(request: Request, db: Database) -> RecoveryService:
    return RecoveryService(db, origin=request.app.state.settings.reactionx_public_origin)


@router.post("/runs/{run_id}/upload-authorization", response_model=UploadPermit)
def authorize_upload(
    run_id: UUID, command: UploadAuthorizationRequest, request: Request, db: Database, actor: Actor
) -> UploadPermit:
    return recovery_service(request, db).authorize_upload(
        actor, run_id, command.digest, command.confirm_original_package
    )


@router.post("/runs/{run_id}/recovery", response_model=RecoveryRequest)
def request_recovery(
    run_id: UUID,
    command: RecoveryAuthorizationRequest,
    request: Request,
    db: Database,
    actor: Actor,
) -> RecoveryRequest:
    return recovery_service(request, db).authorize(
        actor, run_id, command.digest, command.reason, command.confirmation
    )


@router.post("/runs/{run_id}/recovery/{request_id}/confirm", response_model=UploadPermit)
def confirm_recovery(
    run_id: UUID,
    request_id: UUID,
    command: RecoveryConfirmationRequest,
    request: Request,
    db: Database,
    actor: Actor,
) -> UploadPermit:
    return recovery_service(request, db).confirm(actor, run_id, request_id, command.confirmation)


@router.get("/results", response_model=ResultPage)
def list_results(
    db: Database,
    actor: Actor,
    person_id: UUID | None = None,
    cursor: str | None = Query(default=None, max_length=2048),
    limit: int = Query(default=20, ge=1, le=100),
) -> ResultPage:
    return ResultService(db).list_visible(actor, person_id, cursor, limit)


@router.get("/results/{result_id}", response_model=ResultReport)
def result_report(result_id: UUID, db: Database, actor: Actor) -> ResultReport:
    return ResultService(db).report(actor, result_id)


@router.get("/results/{result_id}/ranking", response_model=ItemRankingView)
def result_ranking(result_id: UUID, db: Database, actor: Actor) -> ItemRankingView:
    from app.reactionx.ranking import ReactionxRankingService

    return ReactionxRankingService(db, ReactionxAccess(db, actor)).for_result(result_id)
