from fastapi import APIRouter, Request

from app.personnel_intake.quick_import_schemas import (
    CommitInput,
    CommitResult,
    PreviewInput,
    PreviewResult,
)
from app.personnel_intake.quick_import_service import QuickIntakeService
from app.personnel_intake.router import Actor, ApprovalService, PrivateIntakeRoute

router = APIRouter(prefix="/personnel-intake/quick-import", tags=["personnel-intake"], route_class=PrivateIntakeRoute)


@router.post("/preview", response_model=PreviewResult)
def preview(actor: Actor, service: ApprovalService, input: PreviewInput) -> PreviewResult:
    return QuickIntakeService(service).preview(actor, input)


@router.post("/commit", response_model=CommitResult, status_code=201)
def commit(actor: Actor, service: ApprovalService, input: CommitInput, request: Request) -> CommitResult:
    return QuickIntakeService(service).commit(actor, input, request)
