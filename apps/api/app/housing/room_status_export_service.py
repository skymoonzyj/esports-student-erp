from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.errors import ProblemError
from app.housing.access import REPORT_PERMISSION, HousingAccessService
from app.housing.api_support import _not_found
from app.housing.history_validity import effective_stay_clause
from app.housing.inventory_service import HousingInventoryService, HousingOverviewFilters
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.occupancy_export_service import decrypt_identity, decrypt_phone
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.room_status_export_schemas import HousingRoomStatusExportRequest
from app.housing.schemas import CleaningFrequency, CleaningStatus
from app.housing.sorting import building_order_expressions
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.personnel_intake.models import AssistedPersonProfile
from app.security.encryption import EnvelopeCipher, LookupDigest

MAX_EXPORT_ROOMS = 10_000
MAX_EXPORT_ROWS = 50_000


class _PersonFields(TypedDict, total=False):
    person_name: str | None
    sex: str | None
    identity_number: str | None
    phone: str | None
    actual_check_in_at: datetime | None
    planned_check_out_at: datetime | None
    person_type: str | None
    account_status: str | None
    profile_source: str | None


@dataclass(frozen=True, slots=True)
class RoomStatusExportRow:
    building_name: str
    floor_name: str
    room_number: str
    room_type: str
    capacity: int
    current_occupants: int
    occupancy_status: str
    verification_status: str
    service_status: str
    is_assignable: bool
    daily_cleaning_enabled: bool
    cleaning_status: CleaningStatus
    cleaning_frequency: CleaningFrequency = "unspecified"
    person_name: str | None = None
    sex: str | None = None
    identity_number: str | None = None
    phone: str | None = None
    actual_check_in_at: datetime | None = None
    planned_check_out_at: datetime | None = None
    person_type: str | None = None
    account_status: str | None = None
    profile_source: str | None = None


class HousingRoomStatusExportService:
    """Read inventory, occupancy and identity facts from one dedicated RR snapshot."""

    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        actor_user_id: UUID,
    ) -> None:
        self.db, self.cipher, self.lookup = db, cipher, lookup
        self.actor_user_id = actor_user_id
        self.access_revision: str | None = None

    def _begin_snapshot(self) -> None:
        if self.db.in_transaction():
            self._snapshot_unavailable()
        bind = self.db.get_bind()
        connection = (
            self.db.connection()
            if isinstance(bind, Connection) and bind.in_transaction()
            else self.db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        )
        if connection.get_isolation_level().upper() != "REPEATABLE READ":
            self.db.rollback()
            self._snapshot_unavailable()

    @staticmethod
    def _snapshot_unavailable() -> None:
        raise ProblemError(
            status=503,
            code="housing_export_snapshot_unavailable",
            detail="房态快照暂时不可用，请稍后重试",
        )

    @staticmethod
    def _limit_exceeded() -> None:
        raise ProblemError(
            status=422,
            code="housing_export_limit_exceeded",
            detail="导出范围超过上限，请缩小范围后重试",
        )

    def _validate_scope(
        self,
        request: HousingRoomStatusExportRequest,
        access: HousingAccessService,
    ) -> None:
        for building_id in request.building_ids:
            building = self.db.get(HousingBuilding, building_id)
            tokens = access.building_tokens(building_id)
            direct = all(
                access.can(permission, tokens) for permission in ("housing.read", REPORT_PERMISSION)
            )
            scoped_floor = self.db.scalar(
                select(HousingFloor.id)
                .where(
                    HousingFloor.building_id == building_id,
                    access.floor_permissions_criteria("housing.read", REPORT_PERMISSION),
                )
                .limit(1)
            )
            if building is None or not (direct or scoped_floor is not None):
                _not_found()
        for floor_id in request.floor_ids:
            floor = self.db.get(HousingFloor, floor_id)
            if (
                floor is None
                or not access.can_read_floor(floor)
                or not access.can_export_report_floor(floor)
                or request.building_ids
                and floor.building_id not in request.building_ids
            ):
                _not_found()
        if (
            self.db.scalar(
                select(HousingFloor.id)
                .where(access.floor_permissions_criteria("housing.read", REPORT_PERMISSION))
                .limit(1)
            )
            is None
        ):
            raise ProblemError(status=403, code="permission_denied", detail="没有房态导出权限")

    def rows(
        self,
        request: HousingRoomStatusExportRequest,
        *,
        as_of: datetime,
    ) -> list[RoomStatusExportRow]:
        self._begin_snapshot()
        access = HousingAccessService(self.db, self.actor_user_id, as_of)
        self.access_revision = access.access_revision
        self._validate_scope(request, access)
        criteria = [access.room_permissions_criteria("housing.read", REPORT_PERMISSION)]
        if request.building_ids:
            criteria.append(HousingRoom.building_id.in_(request.building_ids))
        if request.floor_ids:
            criteria.append(HousingRoom.floor_id.in_(request.floor_ids))
        room_rows = self.db.execute(
            select(HousingRoom, HousingBuilding, HousingFloor)
            .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
            .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
            .where(*criteria)
            .order_by(
                *building_order_expressions(),
                HousingBuilding.name,
                HousingFloor.display_order,
                HousingFloor.number,
                HousingRoom.room_number,
                HousingRoom.id,
            )
        ).all()
        projections = HousingOccupancyService(self.db).room_projections(
            [room.id for room, _, _ in room_rows],
            as_of,
        )
        filters = HousingOverviewFilters(
            query=request.query,
            room_types=frozenset(request.room_types),
            verification_statuses=frozenset(request.verification_statuses),
            service_statuses=frozenset(request.service_statuses),
            current_gender=request.current_gender,
            occupancy=request.occupancy,
            assignability=request.assignability,
            quick_filter=request.quick_filter,
            cleaning_statuses=frozenset(request.cleaning_statuses),
            daily_cleaning_enabled=request.daily_cleaning_enabled,
            cleaning_frequencies=frozenset(request.cleaning_frequencies),
        )
        inventory = HousingInventoryService(self.db)
        selected = []
        for room, building, floor in room_rows:
            projection = projections[room.id]
            card = inventory._room_card(room, building, floor, access, projection)
            if inventory._matches_filters(card, filters, projection) and inventory._matches_quick_filter(
                card, filters.quick_filter
            ):
                selected.append((room, card))
        if len(selected) > MAX_EXPORT_ROOMS:
            self._limit_exceeded()
        row_count = sum(max(1, card.current_occupants) for _, card in selected)
        if row_count > MAX_EXPORT_ROWS:
            self._limit_exceeded()
        if not selected:
            raise ProblemError(
                status=422, code="housing_export_empty", detail="所选范围内没有符合条件的房间"
            )
        stays = self.db.execute(
            select(
                HousingStay,
                Person,
                StudentProfile.person_id,
                StaffProfile.staff_type,
                VisitorProfile.person_id,
                HousingOccupancyService.assisted_kind_expression(),
                UserAccount.status,
                AssistedPersonProfile.person_id,
            )
            .join(Person, Person.id == HousingStay.person_id)
            .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
            .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
            .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
            .outerjoin(UserAccount, UserAccount.person_id == Person.id)
            .outerjoin(AssistedPersonProfile, AssistedPersonProfile.person_id == Person.id)
            .where(
                HousingStay.room_id.in_([room.id for room, _ in selected]),
                effective_stay_clause(),
                HousingStay.status.in_(("checked_in", "checkout_pending")),
            )
            .order_by(HousingStay.actual_check_in_at, HousingStay.id)
        ).all()
        by_room: dict[UUID, list[_PersonFields]] = {}
        for stay, person, student_id, staff_type, visitor_id, kind, status, approved_id in stays:
            by_room.setdefault(stay.room_id, []).append(
                _PersonFields(
                    person_name=person.full_name,
                    sex=person.sex,
                    identity_number=decrypt_identity(
                        self.cipher,
                        person.identity_ciphertext,
                        person.date_of_birth,
                        document_type=person.document_type,
                        issuing_country=person.document_issuing_country,
                        sex=person.sex,
                    ),
                    phone=decrypt_phone(self.cipher, person.phone_ciphertext),
                    actual_check_in_at=stay.actual_check_in_at,
                    planned_check_out_at=stay.planned_check_out_at,
                    person_type=HousingOccupancyService._person_type(
                        student_id,
                        staff_type,
                        visitor_id,
                        kind,
                    ),
                    account_status=status or "not_activated",
                    profile_source="approved_profile" if approved_id else "legacy",
                )
            )
        result = []
        for room, card in selected:
            for person_fields in by_room.get(room.id) or [{}]:
                result.append(
                    RoomStatusExportRow(
                        building_name=card.building_name,
                        floor_name=card.floor_name,
                        room_number=card.room_number,
                        room_type=card.room_type,
                        capacity=card.capacity,
                        current_occupants=card.current_occupants,
                        occupancy_status=card.occupancy_status,
                        verification_status=card.verification_status,
                        service_status=card.service_status,
                        is_assignable=card.is_assignable,
                        daily_cleaning_enabled=card.daily_cleaning_enabled,
                        cleaning_frequency=card.cleaning_frequency,
                        cleaning_status=cast(CleaningStatus, room.cleaning_status),
                        **person_fields,
                    )
                )
        if len(result) > MAX_EXPORT_ROWS:
            self._limit_exceeded()
        return result
