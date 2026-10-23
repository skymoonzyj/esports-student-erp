from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AfterValidator
from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, aliased

from app.audit.service import AuditMetadataRejected
from app.auth.dependencies import require_standard_account
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_date_at
from app.housing.access import HousingAccessService
from app.housing.api_support import (
    LockedHousingAuthorizationDenied,
    PrivateHousingRoute,
    _audit,
    _deny_mutation,
    _failed_mutation,
    _mark_request_audited,
    _not_found,
    _permission_denied,
    _raise_housing_error,
    _register_sensitive_read_audit,
    _set_sensitive_read_result_count,
    _set_sensitive_read_room,
    _standalone_audit,
)
from app.housing.dependencies import get_housing_cipher, get_housing_lookup
from app.housing.errors import (
    HousingPersistenceFailure,
    HousingResourceNotFound,
    HousingServiceError,
)
from app.housing.history_validity import effective_stay_clause
from app.housing.history_void_access import require_history_void_authority
from app.housing.inventory_service import HousingInventoryService
from app.housing.issue_service import HousingIssueService
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingRoomIssue,
    HousingRoomPhoto,
    HousingStay,
    HousingTransfer,
)
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.resident_readiness import HousingResidentReadinessPolicy
from app.housing.schemas import (
    CancelReservationRequest,
    CheckoutConfirmation,
    CheckoutRequest,
    CohortOptionsResponse,
    ConfirmCheckInRequest,
    HousingRoomDetail,
    IssueAllowedActions,
    ResidentOptionsResponse,
    RoomIssueCreate,
    RoomIssueResolve,
    RoomIssueSummary,
    RoomPhotoSummary,
    StayAllowedActions,
    StayCreate,
    StayPlannedDepartureUpdate,
    StaySummary,
    StayUpdate,
    StayVoidInfo,
    TransferRoomOptionsResponse,
    TransferStayRequest,
    TransferSummary,
)
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.security.encryption import EnvelopeCipher, LookupDigest

router = APIRouter(
    prefix="/housing",
    tags=["housing"],
    route_class=PrivateHousingRoute,
)


def _access(db: Session, context: AuthContext, occurred_at: datetime) -> HousingAccessService:
    return HousingAccessService(db, context.account.id, occurred_at)


def _mutation_context(action: str) -> Callable[..., AuthContext]:
    def dependency(
        request: Request,
        context: Annotated[AuthContext, Depends(require_standard_account)],
        db: Annotated[Session, Depends(get_db)],
    ) -> AuthContext:
        request.state.housing_mutation_context = context
        request.state.housing_mutation_db = db
        request.state.housing_mutation_action = action
        return context

    return dependency


def _sensitive_read_context(action: str) -> Callable[..., AuthContext]:
    def dependency(
        request: Request,
        context: Annotated[AuthContext, Depends(require_standard_account)],
        db: Annotated[Session, Depends(get_db)],
    ) -> AuthContext:
        _register_sensitive_read_audit(
            request,
            db,
            context,
            action=action,
        )
        return context

    return dependency


def _normalize_resident_query(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("resident query must contain at least one character")
    return normalized


def _visible_room(
    db: Session,
    access: HousingAccessService,
    room_id: UUID,
) -> HousingRoom:
    room = db.get(HousingRoom, room_id)
    if room is None or not access.can_read_room(room):
        _not_found()
    return room


def _candidate_room(
    db: Session,
    access: HousingAccessService,
    room_id: UUID,
) -> HousingRoom:
    room = _visible_room(db, access, room_id)
    if not access.can_manage_stays(room):
        _permission_denied()
    return room


def _reauthorize_candidate_room(
    db: Session,
    context: AuthContext,
    occurred_at: datetime,
    room: HousingRoom,
) -> str:
    current_access = _access(db, context, occurred_at)
    if not current_access.can_read_room(room):
        _not_found()
    if not current_access.can_manage_stays(room):
        _permission_denied()
    return current_access.access_revision


def _stay_actions(
    access: HousingAccessService,
    room: HousingRoom,
    stay: StaySummary,
    as_of: datetime,
) -> StayAllowedActions:
    manages_stays = access.can_manage_stays(room)
    manages_transfers = access.can_manage_transfers(room)
    manages_checkouts = access.can_manage_checkouts(room)
    manual_checkout_request = (
        stay.checkout_requested_actual_at is not None and stay.checkout_requested_at is not None
    )
    current_without_manual_checkout = (
        stay.status in {"checked_in", "checkout_pending"} and not manual_checkout_request
    )
    has_remaining_transfer_interval = (
        stay.duration_mode == "long_term"
        or stay.planned_check_out_at is not None
        and as_of < stay.planned_check_out_at
    )
    return StayAllowedActions(
        edit_planned_departure=manages_stays and stay.status == "checked_in",
        edit_reservation=manages_stays and stay.status == "planned",
        confirm_check_in=manages_stays and stay.status == "planned" and stay.sex is not None,
        cancel_reservation=manages_checkouts and stay.status == "planned",
        transfer=(
            manages_transfers
            and stay.sex is not None
            and current_without_manual_checkout
            and has_remaining_transfer_interval
        ),
        request_checkout=manages_checkouts and current_without_manual_checkout,
        confirm_checkout=(
            manages_checkouts and stay.status == "checkout_pending" and manual_checkout_request
        ),
    )


def _can_manage(
    access: HousingAccessService,
    room: HousingRoom,
    permission: str,
) -> bool:
    checks: dict[str, Callable[[HousingRoom], bool]] = {
        "housing.rooms.manage": access.can_manage_room,
        "housing.stays.manage": access.can_manage_stays,
        "housing.transfers.manage": access.can_manage_transfers,
        "housing.checkouts.manage": access.can_manage_checkouts,
        "housing.issues.manage": access.can_manage_issues,
    }
    return checks[permission](room)


def _authorize_locked_room(
    db: Session,
    context: AuthContext,
    occurred_at: datetime,
    room: HousingRoom,
    *permissions: str,
) -> None:
    locked_access = _access(db, context, occurred_at)
    if not locked_access.can_read_room(room):
        raise LockedHousingAuthorizationDenied(not_found=True)
    if any(not _can_manage(locked_access, room, permission) for permission in permissions):
        raise LockedHousingAuthorizationDenied(not_found=False)


def _require_current_care_actor(db: Session, context: AuthContext) -> None:
    """Read scalar facts so identity-map/auth prechecks cannot grant stale authority.

    Room-only care writes must never acquire a person row lock in reverse order.
    """
    now = datetime.now(UTC)
    account = db.execute(
        select(UserAccount.person_id, UserAccount.status, UserAccount.force_password_change)
        .where(UserAccount.id == context.account.id)
    ).one_or_none()
    session = db.execute(
        select(AuthSession.user_id, AuthSession.expires_at, AuthSession.revoked_at)
        .where(AuthSession.id == context.session.id)
    ).one_or_none()
    person = db.execute(
        select(Person.id, Person.deleted_at).where(Person.id == context.person.id)
    ).one_or_none()
    if (
        account is None or account.person_id != context.person.id
        or account.status != "active" or account.force_password_change
        or person is None or person.deleted_at is not None
        or session is None or session.user_id != context.account.id
        or session.revoked_at is not None or session.expires_at <= now
    ):
        raise LockedHousingAuthorizationDenied(not_found=False)


def _authorize_locked_room_care(
    db: Session, context: AuthContext, occurred_at: datetime, room: HousingRoom
) -> None:
    _require_current_care_actor(db, context)
    access = _access(db, context, datetime.now(UTC))
    if not access.can_read_room(room):
        raise LockedHousingAuthorizationDenied(not_found=True)
    if not access.can_manage_room_care(room):
        raise LockedHousingAuthorizationDenied(not_found=False)


def _precheck_room_mutation(
    db: Session,
    request: Request,
    context: AuthContext,
    access: HousingAccessService,
    room_id: UUID,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    metadata: dict[str, Any],
    permissions: tuple[str, ...],
) -> HousingRoom:
    room = db.get(HousingRoom, room_id)
    if room is None or not access.can_read_room(room):
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
            not_found=True,
        )
    if any(not _can_manage(access, room, permission) for permission in permissions):
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
        )
    return room


def _precheck_stay_mutation(
    db: Session,
    request: Request,
    context: AuthContext,
    access: HousingAccessService,
    stay_id: UUID,
    *,
    action: str,
    permission: str,
    metadata: dict[str, Any],
    room_metadata_key: str = "room_id",
) -> tuple[HousingStay, HousingRoom]:
    stay = db.get(HousingStay, stay_id)
    if stay is None:
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type="housing_stay",
            resource_id=stay_id,
            metadata=metadata,
            not_found=True,
        )
    room = db.get(HousingRoom, stay.room_id)
    if room is None or not access.can_read_room(room):
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type="housing_stay",
            resource_id=stay_id,
            metadata=metadata,
            not_found=True,
        )
    metadata[room_metadata_key] = str(room.id)
    if not _can_manage(access, room, permission):
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type="housing_stay",
            resource_id=stay_id,
            metadata=metadata,
        )
    return stay, room


def _execute_mutation[MutationResult, MutationResponse](
    db: Session,
    request: Request,
    context: AuthContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    metadata: dict[str, Any],
    operation: Callable[[], MutationResult],
    serialize: Callable[[MutationResult], MutationResponse],
    success_audit: Callable[[MutationResult], tuple[str, UUID | None, dict[str, Any]]]
    | None = None,
    audit_when: Callable[[MutationResult], bool] | None = None,
) -> MutationResponse:
    try:
        db.commit()
        with db.begin():
            result = operation()
            response = serialize(result)
            success_resource_type = resource_type
            success_resource_id = resource_id
            success_metadata = metadata
            if success_audit is not None:
                (
                    success_resource_type,
                    success_resource_id,
                    success_metadata,
                ) = success_audit(result)
            if audit_when is None or audit_when(result):
                _audit(
                    db,
                    request,
                    context,
                    action=action,
                    resource_type=success_resource_type,
                    resource_id=success_resource_id,
                    outcome="allowed",
                    metadata=success_metadata,
                )
        _mark_request_audited(request)
    except LockedHousingAuthorizationDenied as exc:
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
            not_found=exc.not_found,
        )
    except HousingResourceNotFound:
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
            not_found=True,
        )
    except HousingServiceError as exc:
        _failed_mutation(
            db,
            request,
            context,
            exc,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
        )
    except ApprovalEvidenceError as exc:
        _standalone_audit(
            db,
            request,
            context,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="failed",
            metadata={"error_code": exc.code},
        )
        raise ProblemError(status=503, code=exc.code, detail="审核资料校验暂不可用") from exc
    except DBAPIError:
        _failed_mutation(
            db,
            request,
            context,
            HousingPersistenceFailure("housing persistence operation failed"),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="housing_audit_failed",
            detail="房态审计记录失败",
        ) from exc
    return response


def _stay_response(
    db: Session,
    context: AuthContext,
    occurred_at: datetime,
    stay: HousingStay,
) -> StaySummary:
    room = db.get(HousingRoom, stay.room_id)
    if room is None:
        raise HousingResourceNotFound("housing room not found")
    access = _access(db, context, occurred_at)
    can_void = False
    try:
        require_history_void_authority(db, context, room)
        can_void = True
    except LockedHousingAuthorizationDenied:
        pass
    summary = next(
        (
            item
            for item in _summaries_for_room(db, access, room, occurred_at, can_void)
            if item.id == stay.id
        ),
        None,
    )
    if summary is None:
        raise HousingResourceNotFound("housing stay not found")
    return summary


def _issue_response(
    access: HousingAccessService,
    room: HousingRoom,
    issue: HousingRoomIssue,
) -> RoomIssueSummary:
    return RoomIssueSummary.model_validate(issue).model_copy(
        update={
            "allowed_actions": IssueAllowedActions(
                resolve=issue.status == "open" and access.can_manage_issues(room)
            )
        }
    )


def _summaries_for_room(
    db: Session,
    access: HousingAccessService,
    room: HousingRoom,
    as_of: datetime,
    can_void: bool,
    *,
    voided: bool = False,
    cipher: EnvelopeCipher | None = None,
) -> list[StaySummary]:
    rows = db.execute(
        select(
            HousingStay,
            Person.full_name,
            Person.sex,
            StudentProfile.person_id,
            StaffProfile.staff_type,
            VisitorProfile.person_id,
            HousingOccupancyService.assisted_kind_expression(),
        )
        .join(Person, Person.id == HousingStay.person_id)
        .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
        .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
        .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
        .where(
            HousingStay.room_id == room.id,
            HousingStay.voided_at.is_not(None) if voided else effective_stay_clause(),
        )
        .order_by(HousingStay.planned_check_in_at, HousingStay.id)
    ).all()
    summaries: list[StaySummary] = []
    for stay, person_name, sex, student_id, staff_type, visitor_id, assisted_kind in rows:
        summary = HousingOccupancyService._stay_summary(
            stay,
            person_name,
            sex,
            student_id,
            staff_type,
            visitor_id,
            assisted_kind,
        )
        if voided:
            if (
                cipher is None
                or stay.voided_at is None
                or stay.voided_by_user_id is None
                or stay.void_reason_ciphertext is None
            ):
                raise HousingPersistenceFailure("void history cipher is required")
            summary = summary.model_copy(
                update={
                    "void_info": StayVoidInfo(
                        voided_at=stay.voided_at,
                        voided_by_user_id=stay.voided_by_user_id,
                        reason=cipher.decrypt(
                            stay.void_reason_ciphertext, field_name="housing_stay_void_reason"
                        ),
                    ),
                }
            )
        else:
            summary = summary.model_copy(
                update={
                    "allowed_actions": _stay_actions(access, room, summary, as_of).model_copy(
                        update={
                            "void_history": can_void
                            and stay.status == "checked_out"
                            and stay.voided_at is None
                        }
                    ),
                }
            )
        summaries.append(summary)
    return summaries


@router.get("/rooms/{room_id}/detail", response_model=HousingRoomDetail)
def room_detail(
    room_id: UUID,
    request: Request,
    context: Annotated[
        AuthContext,
        Depends(_sensitive_read_context("housing.room.detail.read")),
    ],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    include_voided: bool = False,
) -> HousingRoomDetail:
    _set_sensitive_read_room(request, room_id)
    as_of = datetime.now(UTC)
    access = _access(db, context, as_of)
    room = _visible_room(db, access, room_id)
    can_view_voided_history = False
    try:
        require_history_void_authority(db, context, room)
        can_view_voided_history = True
    except LockedHousingAuthorizationDenied as exc:
        if include_voided:
            if exc.not_found:
                _not_found()
            _permission_denied()
    building = db.get(HousingBuilding, room.building_id)
    floor = db.get(HousingFloor, room.floor_id)
    if building is None or floor is None:
        _not_found()
    projection = HousingOccupancyService(db).room_projections([room.id], as_of)[room.id]
    card = HousingInventoryService._room_card(
        room,
        building,
        floor,
        access,
        projection,
    )
    room_actions = card.allowed_actions.model_copy(
        update={
            "create_stay": access.can_manage_stays(room) and card.is_assignable,
            "report_issue": access.can_manage_issues(room) and projection.open_issue is None,
            "view_detail": True,
        }
    )
    card = card.model_copy(update={"allowed_actions": room_actions})
    summaries = _summaries_for_room(db, access, room, as_of, can_view_voided_history)
    voided_history = (
        _summaries_for_room(
            db, access, room, as_of, can_view_voided_history, voided=True, cipher=cipher
        )
        if include_voided
        else []
    )
    current = [
        summary for summary in summaries if summary.status in {"checked_in", "checkout_pending"}
    ]
    future = [summary for summary in summaries if summary.status == "planned"]
    history = [
        summary
        for summary in summaries
        if summary.status not in {"planned", "checked_in", "checkout_pending"}
    ]
    card = card.model_copy(update={"occupants": current})
    from_stay = aliased(HousingStay)
    to_stay = aliased(HousingStay)
    transfers = list(
        db.execute(
            select(HousingTransfer, from_stay.voided_at, to_stay.voided_at)
            .join(from_stay, from_stay.id == HousingTransfer.from_stay_id)
            .join(to_stay, to_stay.id == HousingTransfer.to_stay_id)
            .where(or_(from_stay.room_id == room.id, to_stay.room_id == room.id))
            .order_by(HousingTransfer.transferred_at, HousingTransfer.id)
        )
    )
    issues = list(
        db.scalars(
            select(HousingRoomIssue)
            .where(HousingRoomIssue.room_id == room.id)
            .order_by(HousingRoomIssue.reported_at, HousingRoomIssue.id)
        )
    )
    open_issue = next((issue for issue in issues if issue.status == "open"), None)
    if open_issue is not None:
        card = card.model_copy(
            update={"open_issue_summary": _issue_response(access, room, open_issue)}
        )
    return HousingRoomDetail(
        access_revision=access.access_revision,
        as_of=as_of,
        room=card,
        photos=[
            RoomPhotoSummary(
                id=photo.id, slot=photo.slot, mime_type=photo.mime_type,
                byte_size=photo.byte_size, updated_at=photo.updated_at,
                url=f"/api/v1/housing/rooms/{room.id}/photos/{photo.id}",
                thumbnail_url=f"/api/v1/housing/rooms/{room.id}/photos/{photo.id}?variant=thumbnail",
            )
            for photo in db.execute(
                select(HousingRoomPhoto.id, HousingRoomPhoto.slot, HousingRoomPhoto.mime_type,
                       HousingRoomPhoto.byte_size, HousingRoomPhoto.updated_at)
                .where(HousingRoomPhoto.room_id == room.id).order_by(HousingRoomPhoto.slot)
            )
        ],
        current_stays=current,
        future_reservations=future,
        history=history,
        voided_history=voided_history,
        can_view_voided_history=can_view_voided_history,
        transfers=[
            TransferSummary.model_validate(item).model_copy(
                update={
                    "from_stay_voided": from_voided_at is not None,
                    "to_stay_voided": to_voided_at is not None,
                }
            )
            for item, from_voided_at, to_voided_at in transfers
        ],
        issues=[_issue_response(access, room, item) for item in issues],
    )


@router.get("/rooms/{room_id}/resident-options", response_model=ResidentOptionsResponse)
def resident_options(
    room_id: UUID,
    request: Request,
    context: Annotated[
        AuthContext,
        Depends(_sensitive_read_context("housing.resident_options.read")),
    ],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
    query: Annotated[
        str,
        Query(min_length=1, max_length=100),
        AfterValidator(_normalize_resident_query),
    ],
) -> ResidentOptionsResponse:
    _set_sensitive_read_room(request, room_id)
    occurred_at = datetime.now(UTC)
    access = _access(db, context, occurred_at)
    _candidate_room(db, access, room_id)
    current_access_revision: str | None = None

    def authorize_candidate_room(room: HousingRoom) -> None:
        nonlocal current_access_revision
        current_access_revision = _reauthorize_candidate_room(
            db,
            context,
            occurred_at,
            room,
        )

    try:
        items = HousingOccupancyService(db).list_resident_options(
            room_id,
            query,
            cipher=cipher,
            lookup=lookup,
            authorize_room=authorize_candidate_room,
            reference_date=business_date_at(occurred_at),
        )
        if current_access_revision is None:
            raise HousingServiceError("housing candidate authorization was not completed")
    except ApprovalEvidenceError as exc:
        raise ProblemError(status=503, code=exc.code, detail="审核资料校验暂不可用") from exc
    except HousingServiceError as exc:
        _raise_housing_error(exc)
    _set_sensitive_read_result_count(request, len(items))
    return ResidentOptionsResponse(access_revision=current_access_revision, items=items)


@router.get("/rooms/{room_id}/cohort-options", response_model=CohortOptionsResponse)
def cohort_options(
    room_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> CohortOptionsResponse:
    occurred_at = datetime.now(UTC)
    access = _access(db, context, occurred_at)
    _candidate_room(db, access, room_id)
    current_access_revision: str | None = None

    def authorize_candidate_room(room: HousingRoom) -> None:
        nonlocal current_access_revision
        current_access_revision = _reauthorize_candidate_room(
            db,
            context,
            occurred_at,
            room,
        )

    try:
        items = HousingOccupancyService(db).list_cohort_options(
            room_id,
            authorize_room=authorize_candidate_room,
        )
        if current_access_revision is None:
            raise HousingServiceError("housing candidate authorization was not completed")
    except HousingServiceError as exc:
        _raise_housing_error(exc)
    return CohortOptionsResponse(access_revision=current_access_revision, items=items)


@router.get("/stays/{stay_id}/transfer-options", response_model=TransferRoomOptionsResponse)
def transfer_options(
    stay_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> TransferRoomOptionsResponse:
    occurred_at = datetime.now(UTC)
    access = _access(db, context, occurred_at)
    try:
        return HousingOccupancyService(db).list_transfer_options(
            stay_id,
            occurred_at=occurred_at,
            access=access,
        )
    except HousingServiceError as exc:
        _raise_housing_error(exc)


@router.post("/rooms/{room_id}/stays", response_model=StaySummary, status_code=201)
def create_stay(
    room_id: UUID,
    input: StayCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.stay.create"))],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.create"
    metadata = {"room_id": str(room_id), "resident_person_id": str(input.person_id)}
    access = _access(db, context, occurred_at)
    _precheck_room_mutation(
        db,
        request,
        context,
        access,
        room_id,
        action=action,
        resource_type="housing_stay",
        resource_id=None,
        metadata=metadata,
        permissions=("housing.stays.manage",),
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=None,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(
            db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=cipher, lookup=lookup)
        ).create_stay(
            room_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.stays.manage"
            ),
        ),
        serialize=lambda stay: _stay_response(db, context, occurred_at, stay),
        success_audit=lambda stay: (
            "housing_stay",
            stay.id,
            {**metadata, "stay_id": str(stay.id)},
        ),
    )


@router.post("/rooms/{room_id}/stays/backfill", response_model=StaySummary, status_code=201)
def backfill_stay(
    room_id: UUID,
    input: StayCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.stay.backfill"))],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.backfill"
    metadata = {"room_id": str(room_id), "resident_person_id": str(input.person_id)}
    access = _access(db, context, occurred_at)
    permissions = ("housing.rooms.manage", "housing.stays.manage")
    _precheck_room_mutation(
        db,
        request,
        context,
        access,
        room_id,
        action=action,
        resource_type="housing_stay",
        resource_id=None,
        metadata=metadata,
        permissions=permissions,
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=None,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(
            db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=cipher, lookup=lookup)
        ).backfill_existing_stay(
            room_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room_and_stay=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, *permissions
            ),
        ),
        serialize=lambda stay: _stay_response(db, context, occurred_at, stay),
        success_audit=lambda stay: (
            "housing_stay",
            stay.id,
            {**metadata, "stay_id": str(stay.id)},
        ),
    )


@router.patch("/stays/{stay_id}", response_model=StaySummary)
def update_stay(
    stay_id: UUID,
    input: StayUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.stay.update"))],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.update"
    metadata = {"stay_id": str(stay_id)}
    access = _access(db, context, occurred_at)
    _precheck_stay_mutation(
        db,
        request,
        context,
        access,
        stay_id,
        action=action,
        permission="housing.stays.manage",
        metadata=metadata,
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=stay_id,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(
            db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=cipher, lookup=lookup)
        ).update_reservation(
            stay_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.stays.manage"
            ),
        ),
        serialize=lambda stay: _stay_response(db, context, occurred_at, stay),
    )


@router.patch("/stays/{stay_id}/planned-departure", response_model=StaySummary)
def update_planned_departure(
    stay_id: UUID,
    input: StayPlannedDepartureUpdate,
    request: Request,
    context: Annotated[
        AuthContext, Depends(_mutation_context("housing.stay.update_planned_departure"))
    ],
    db: Annotated[Session, Depends(get_db)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.update_planned_departure"
    metadata = {"stay_id": str(stay_id)}
    _precheck_stay_mutation(
        db,
        request,
        context,
        _access(db, context, occurred_at),
        stay_id,
        action=action,
        permission="housing.stays.manage",
        metadata=metadata,
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=stay_id,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(db).update_planned_departure(
            stay_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.stays.manage"
            ),
        ),
        serialize=lambda change: _stay_response(db, context, occurred_at, change.stay),
        success_audit=lambda change: (
            "housing_stay",
            change.stay.id,
            {
                **metadata,
                "previous_duration_mode": change.previous_duration_mode,
                "duration_mode": change.stay.duration_mode,
                "previous_planned_check_out_at": change.previous_planned_check_out_at.isoformat()
                if change.previous_planned_check_out_at is not None
                else None,
                "planned_check_out_at": change.stay.planned_check_out_at.isoformat()
                if change.stay.planned_check_out_at is not None
                else None,
            },
        ),
    )


@router.post("/stays/{stay_id}/confirm-check-in", response_model=StaySummary)
def confirm_check_in(
    stay_id: UUID,
    input: ConfirmCheckInRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.stay.confirm_check_in"))],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.confirm_check_in"
    metadata = {"stay_id": str(stay_id)}
    access = _access(db, context, occurred_at)
    _precheck_stay_mutation(
        db,
        request,
        context,
        access,
        stay_id,
        action=action,
        permission="housing.stays.manage",
        metadata=metadata,
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=stay_id,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(
            db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=cipher, lookup=lookup)
        ).confirm_check_in(
            stay_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.stays.manage"
            ),
        ),
        serialize=lambda stay: _stay_response(db, context, occurred_at, stay),
    )


@router.post("/stays/{stay_id}/cancel", response_model=StaySummary)
def cancel_stay(
    stay_id: UUID,
    input: CancelReservationRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.stay.cancel"))],
    db: Annotated[Session, Depends(get_db)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.cancel"
    metadata = {"stay_id": str(stay_id)}
    access = _access(db, context, occurred_at)
    _precheck_stay_mutation(
        db,
        request,
        context,
        access,
        stay_id,
        action=action,
        permission="housing.checkouts.manage",
        metadata=metadata,
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=stay_id,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(db).cancel_reservation(
            stay_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.checkouts.manage"
            ),
        ),
        serialize=lambda stay: _stay_response(db, context, occurred_at, stay),
    )


@router.post("/stays/{stay_id}/transfer", response_model=StaySummary)
def transfer_stay(
    stay_id: UUID,
    input: TransferStayRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.stay.transfer"))],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.transfer"
    metadata = {
        "stay_id": str(stay_id),
        "target_room_id": str(input.target_room_id),
    }
    access = _access(db, context, occurred_at)
    _precheck_stay_mutation(
        db,
        request,
        context,
        access,
        stay_id,
        action=action,
        permission="housing.transfers.manage",
        metadata=metadata,
        room_metadata_key="source_room_id",
    )
    _precheck_room_mutation(
        db,
        request,
        context,
        access,
        input.target_room_id,
        action=action,
        resource_type="housing_stay",
        resource_id=stay_id,
        metadata=metadata,
        permissions=("housing.transfers.manage",),
    )

    def serialize_transfer(result: tuple[HousingStay, HousingStay, HousingTransfer]) -> StaySummary:
        _source, destination, _transfer = result
        return _stay_response(db, context, occurred_at, destination)

    def transfer_audit(
        result: tuple[HousingStay, HousingStay, HousingTransfer],
    ) -> tuple[str, UUID, dict[str, Any]]:
        source, destination, transfer = result
        return (
            "housing_transfer",
            transfer.id,
            {
                "stay_id": str(source.id),
                "transfer_id": str(transfer.id),
                "source_room_id": str(source.room_id),
                "target_room_id": str(destination.room_id),
            },
        )

    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=stay_id,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(
            db, resident_readiness=HousingResidentReadinessPolicy(db, cipher=cipher, lookup=lookup)
        ).transfer(
            stay_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_source=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.transfers.manage"
            ),
            authorize_target=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.transfers.manage"
            ),
        ),
        serialize=serialize_transfer,
        success_audit=transfer_audit,
    )


@router.post("/stays/{stay_id}/request-checkout", response_model=StaySummary)
def request_checkout(
    stay_id: UUID,
    input: CheckoutRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.stay.request_checkout"))],
    db: Annotated[Session, Depends(get_db)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.request_checkout"
    metadata = {"stay_id": str(stay_id)}
    access = _access(db, context, occurred_at)
    _precheck_stay_mutation(
        db,
        request,
        context,
        access,
        stay_id,
        action=action,
        permission="housing.checkouts.manage",
        metadata=metadata,
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=stay_id,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(db).request_checkout(
            stay_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.checkouts.manage"
            ),
        ),
        serialize=lambda stay: _stay_response(db, context, occurred_at, stay),
    )


@router.post("/stays/{stay_id}/confirm-checkout", response_model=StaySummary)
def confirm_checkout(
    stay_id: UUID,
    input: CheckoutConfirmation,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.stay.confirm_checkout"))],
    db: Annotated[Session, Depends(get_db)],
) -> StaySummary:
    occurred_at = datetime.now(UTC)
    action = "housing.stay.confirm_checkout"
    metadata = {"stay_id": str(stay_id)}
    access = _access(db, context, occurred_at)
    _precheck_stay_mutation(
        db,
        request,
        context,
        access,
        stay_id,
        action=action,
        permission="housing.checkouts.manage",
        metadata=metadata,
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_stay",
        resource_id=stay_id,
        metadata=metadata,
        operation=lambda: HousingOccupancyService(db).confirm_checkout(
            stay_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.checkouts.manage"
            ),
        ),
        serialize=lambda stay: _stay_response(db, context, occurred_at, stay),
    )


@router.post("/rooms/{room_id}/issues", response_model=RoomIssueSummary, status_code=201)
def report_issue(
    room_id: UUID,
    input: RoomIssueCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.issue.report"))],
    db: Annotated[Session, Depends(get_db)],
) -> RoomIssueSummary:
    occurred_at = datetime.now(UTC)
    action = "housing.issue.report"
    metadata = {"room_id": str(room_id)}
    access = _access(db, context, occurred_at)
    _precheck_room_mutation(
        db,
        request,
        context,
        access,
        room_id,
        action=action,
        resource_type="housing_room_issue",
        resource_id=None,
        metadata=metadata,
        permissions=("housing.issues.manage",),
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_room_issue",
        resource_id=None,
        metadata=metadata,
        operation=lambda: HousingIssueService(db).report(
            room_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.issues.manage"
            ),
        ),
        serialize=lambda issue: _issue_response(
            _access(db, context, occurred_at),
            db.get(HousingRoom, issue.room_id) or (_not_found()),
            issue,
        ),
        success_audit=lambda issue: (
            "housing_room_issue",
            issue.id,
            {**metadata, "issue_id": str(issue.id)},
        ),
    )


@router.post("/issues/{issue_id}/resolve", response_model=RoomIssueSummary)
def resolve_issue(
    issue_id: UUID,
    input: RoomIssueResolve,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.issue.resolve"))],
    db: Annotated[Session, Depends(get_db)],
) -> RoomIssueSummary:
    occurred_at = datetime.now(UTC)
    action = "housing.issue.resolve"
    metadata = {"issue_id": str(issue_id)}
    access = _access(db, context, occurred_at)
    issue = db.get(HousingRoomIssue, issue_id)
    if issue is None:
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type="housing_room_issue",
            resource_id=issue_id,
            metadata=metadata,
            not_found=True,
        )
    metadata["room_id"] = str(issue.room_id)
    _precheck_room_mutation(
        db,
        request,
        context,
        access,
        issue.room_id,
        action=action,
        resource_type="housing_room_issue",
        resource_id=issue_id,
        metadata=metadata,
        permissions=("housing.issues.manage",),
    )
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_room_issue",
        resource_id=issue_id,
        metadata=metadata,
        operation=lambda: HousingIssueService(db).resolve(
            issue_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room(
                db, context, occurred_at, room, "housing.issues.manage"
            ),
        ),
        serialize=lambda resolved: _issue_response(
            _access(db, context, occurred_at),
            db.get(HousingRoom, resolved.room_id) or (_not_found()),
            resolved,
        ),
    )
