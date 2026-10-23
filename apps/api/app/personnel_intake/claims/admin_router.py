from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.personnel_intake.approval_service import PersonnelApprovalService
from app.personnel_intake.claims.review_service import ProfileClaimReviewService
from app.personnel_intake.claims.schemas import (
    ClaimAdminContext,
    ClaimDecisionInput,
    ClaimDetail,
    ClaimPage,
    ClaimReasonInput,
    ClaimStatus,
)
from app.personnel_intake.dependencies import get_approval_service
from app.personnel_intake.router import PrivateIntakeRoute


def get_review_service(
    approval: Annotated[PersonnelApprovalService, Depends(get_approval_service)],
) -> ProfileClaimReviewService:
    return ProfileClaimReviewService(approval.db, cipher=approval.cipher, lookup=approval.lookup)


router = APIRouter(
    prefix="/personnel-intake/profile-claims",
    tags=["profile-claim-review"],
    route_class=PrivateIntakeRoute,
)
Actor = Annotated[AuthContext, Depends(require_standard_account)]
Service = Annotated[ProfileClaimReviewService, Depends(get_review_service)]


@router.get("/context", response_model=ClaimAdminContext)
def context(actor: Actor, service: Service) -> ClaimAdminContext:
    return service.context(actor)


@router.get("", response_model=ClaimPage)
def list_claims(
    actor: Actor,
    service: Service,
    status: ClaimStatus | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ClaimPage:
    return service.list_claims(actor, status=status, page=page, page_size=page_size)


@router.get("/{claim_id}", response_model=ClaimDetail)
def detail(claim_id: UUID, actor: Actor, service: Service) -> ClaimDetail:
    return service.detail(actor, claim_id)


@router.post("/{claim_id}/approve", response_model=ClaimDetail)
def approve(
    claim_id: UUID, input: ClaimDecisionInput, actor: Actor, service: Service
) -> ClaimDetail:
    return service.approve(actor, claim_id, input)


@router.post("/{claim_id}/reject", response_model=ClaimDetail)
def reject(claim_id: UUID, input: ClaimReasonInput, actor: Actor, service: Service) -> ClaimDetail:
    return service.reject(actor, claim_id, input)


@router.post("/{claim_id}/revoke", response_model=ClaimDetail)
def revoke(claim_id: UUID, input: ClaimReasonInput, actor: Actor, service: Service) -> ClaimDetail:
    return service.revoke(actor, claim_id, input)
