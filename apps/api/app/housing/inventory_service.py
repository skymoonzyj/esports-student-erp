from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import NoReturn, cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.housing.access import HousingAccessService
from app.housing.errors import (
    HousingCatalogHasChildren,
    HousingDuplicateCatalogEntry,
    HousingInvalidRoomConfiguration,
    HousingIssueWorkflowRequired,
    HousingParentInactive,
    HousingResourceNotFound,
    HousingRoomHasActiveStays,
    HousingServiceError,
    HousingStaleVersion,
)
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingRoomIssue,
    HousingStay,
)
from app.housing.occupancy_service import (
    HousingOccupancyService,
    RoomOccupancyProjection,
)
from app.housing.schemas import (
    BuildingCreate,
    BuildingUpdate,
    FloorCreate,
    FloorUpdate,
    HousingAllowedActions,
    HousingBuildingItem,
    HousingCatalogResponse,
    HousingFloorItem,
    HousingOverviewResponse,
    HousingRoomCard,
    HousingSummary,
    RoomCreate,
    RoomType,
    RoomUpdate,
    RoomVersionInput,
    validate_room_type_capacity,
)
from app.housing.sorting import building_order_expressions

_DUPLICATE_CONSTRAINTS = frozenset(
    {
        "housing_buildings_code_key",
        "housing_buildings_name_key",
        "housing_buildings_pkey",
        "housing_floors_pkey",
        "uq_housing_floors_building_number",
        "uq_housing_floors_building_name",
        "uq_housing_floors_id_building",
        "housing_rooms_pkey",
        "uq_housing_rooms_building_key",
    }
)
_CATALOG_CONFIGURATION_CONSTRAINTS = frozenset(
    {
        "ck_housing_buildings_code",
        "ck_housing_buildings_name",
        "ck_housing_buildings_display_order",
        "ck_housing_floors_number",
        "ck_housing_floors_name",
        "ck_housing_floors_display_order",
    }
)
_ROOM_CONFIGURATION_CONSTRAINTS = frozenset(
    {
        "ck_housing_rooms_number",
        "ck_housing_rooms_key",
        "ck_housing_rooms_type",
        "ck_housing_rooms_type_capacity",
        "ck_housing_rooms_area",
        "ck_housing_rooms_verification",
        "ck_housing_rooms_version",
    }
)
_STRUCTURAL_ROOM_FIELDS = frozenset(
    {"floor_id", "room_number", "room_type", "capacity", "area_square_meters"}
)


@dataclass(frozen=True)
class HousingOverviewFilters:
    building_id: UUID | None = None
    floor_id: UUID | None = None
    query: str | None = None
    room_types: frozenset[str] = frozenset()
    verification_statuses: frozenset[str] = frozenset()
    service_statuses: frozenset[str] = frozenset()
    current_gender: str | None = None
    occupancy: str | None = None
    assignability: str | None = None
    cohort_id: UUID | None = None
    person_type: str | None = None


RoomAuthorizer = Callable[[HousingRoom], None]
RoomBatchAuthorizer = Callable[[Sequence[HousingRoom]], None]
FloorAuthorizer = Callable[[HousingFloor], None]


def _normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _normalized_compact_code(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(normalized.split()).upper()


class HousingInventoryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _catalog_rows(
        self,
        *,
        include_rooms: bool = True,
    ) -> tuple[list[HousingBuilding], list[HousingFloor], list[HousingRoom]]:
        buildings = list(
            self.db.scalars(
                select(HousingBuilding).order_by(*building_order_expressions())
            )
        )
        floors = list(
            self.db.scalars(
                select(HousingFloor).order_by(
                    HousingFloor.building_id,
                    HousingFloor.display_order,
                    HousingFloor.number,
                    HousingFloor.id,
                )
            )
        )
        rooms: list[HousingRoom] = []
        if include_rooms:
            rooms = list(
                self.db.scalars(
                    select(HousingRoom).order_by(
                        HousingRoom.building_id,
                        HousingRoom.floor_id,
                        HousingRoom.room_number,
                        HousingRoom.id,
                    )
                )
            )
        building_order = {
            building.id: index for index, building in enumerate(buildings)
        }
        floors.sort(
            key=lambda floor: (
                building_order[floor.building_id],
                floor.display_order,
                floor.number,
                floor.id,
            )
        )
        floor_order = {floor.id: index for index, floor in enumerate(floors)}
        if include_rooms:
            rooms.sort(
                key=lambda room: (
                    building_order[room.building_id],
                    floor_order[room.floor_id],
                    room.room_number,
                    room.id,
                )
            )
        return buildings, floors, rooms

    def visible_catalog(self, access: HousingAccessService) -> HousingCatalogResponse:
        buildings, floors, _ = self._catalog_rows(include_rooms=False)
        visible_buildings = access.filter_buildings(buildings, floors)
        visible_floors = access.filter_floors(floors)
        building_by_id = {building.id: building for building in buildings}
        return HousingCatalogResponse(
            access_revision=access.access_revision,
            buildings=[
                HousingBuildingItem(
                    **building.__dict__,
                    allowed_actions=access.building_actions(building),
                )
                for building in visible_buildings
            ],
            floors=[
                HousingFloorItem(
                    **floor.__dict__,
                    building_name=building_by_id[floor.building_id].name,
                    allowed_actions=access.floor_actions(floor),
                )
                for floor in visible_floors
            ],
            can_create_building=access.can_create_building,
        )

    @staticmethod
    def _is_assignable(
        room: HousingRoom,
        building: HousingBuilding,
        floor: HousingFloor,
    ) -> bool:
        return bool(
            building.is_active
            and floor.is_active
            and room.verification_status == "verified"
            and room.service_status == "available"
            and room.room_type != "functional"
            and room.capacity > 0
        )

    @classmethod
    def _status_text(
        cls,
        room: HousingRoom,
        building: HousingBuilding,
        floor: HousingFloor,
    ) -> str:
        if room.verification_status == "pending_verification":
            return "待核对"
        if room.room_type == "functional":
            return "功能间，不可入住"
        if not building.is_active or not floor.is_active:
            return "楼栋或楼层已停用"
        if room.service_status == "maintenance":
            return "维修中"
        if room.service_status == "disabled":
            return "已停用"
        if room.service_status == "problem":
            return "问题房"
        if cls._is_assignable(room, building, floor):
            return "空闲，可入住"
        return "已停用"

    @classmethod
    def _room_card(
        cls,
        room: HousingRoom,
        building: HousingBuilding,
        floor: HousingFloor,
        access: HousingAccessService,
        projection: RoomOccupancyProjection | None = None,
    ) -> HousingRoomCard:
        projection = projection or RoomOccupancyProjection((), 0, None)
        current_occupants = len(projection.current_stays)
        base_assignable = cls._is_assignable(room, building, floor)
        manageable = access.can_manage_room(room)
        return HousingRoomCard(
            **room.__dict__,
            building_code=building.code,
            building_name=building.name,
            building_is_active=building.is_active,
            floor_number=floor.number,
            floor_name=floor.name,
            floor_is_active=floor.is_active,
            current_occupants=current_occupants,
            remaining_capacity=max(0, room.capacity - current_occupants),
            current_gender=projection.current_gender,
            has_future_reservation=projection.future_reservation_count > 0,
            occupancy_status="occupied" if current_occupants else "unoccupied",
            is_assignable=base_assignable and current_occupants < room.capacity,
            status_text=cls._status_text(room, building, floor),
            allowed_actions=HousingAllowedActions(
                edit=manageable,
                delete=manageable,
                verify=manageable
                and room.verification_status == "pending_verification",
            ),
            occupants=list(projection.current_stays),
            future_reservation_count=projection.future_reservation_count,
            open_issue_summary=projection.open_issue,
            pending_transfer_count=(
                current_occupants if projection.open_issue is not None else 0
            ),
        )

    @staticmethod
    def _matches_filters(
        card: HousingRoomCard,
        filters: HousingOverviewFilters,
        projection: RoomOccupancyProjection,
    ) -> bool:
        if filters.building_id is not None and card.building_id != filters.building_id:
            return False
        if filters.floor_id is not None and card.floor_id != filters.floor_id:
            return False
        query = unicodedata.normalize("NFKC", filters.query or "").strip().casefold()
        if query and not any(
            query in value.casefold()
            for value in (
                card.room_number,
                card.building_code,
                card.building_name,
                card.floor_name,
            )
        ):
            return False
        if filters.room_types and card.room_type not in filters.room_types:
            return False
        if (
            filters.verification_statuses
            and card.verification_status not in filters.verification_statuses
        ):
            return False
        if filters.service_statuses and card.service_status not in filters.service_statuses:
            return False
        if filters.current_gender == "unspecified" and card.current_gender is not None:
            return False
        if (
            filters.current_gender in {"male", "female"}
            and card.current_gender != filters.current_gender
        ):
            return False
        if filters.occupancy is not None and card.occupancy_status != filters.occupancy:
            return False
        active_stays = (*projection.current_stays, *projection.future_stays)
        if filters.cohort_id is not None and not any(
            stay.cohort_id == filters.cohort_id for stay in active_stays
        ):
            return False
        if filters.person_type is not None and not any(
            stay.person_type == filters.person_type for stay in active_stays
        ):
            return False
        if filters.assignability == "assignable" and not card.is_assignable:
            return False
        return not (
            filters.assignability == "unassignable" and card.is_assignable
        )

    @staticmethod
    def _summary(cards: Sequence[HousingRoomCard]) -> HousingSummary:
        total = len(cards)
        assignable = sum(card.is_assignable for card in cards)
        total_capacity = sum(card.capacity for card in cards)
        current_occupants = sum(card.current_occupants for card in cards)
        occupied = sum(card.occupancy_status == "occupied" for card in cards)
        remaining_capacity = sum(card.remaining_capacity for card in cards)
        occupancy_rate = (
            (Decimal(current_occupants) * Decimal(100) / Decimal(total_capacity)).quantize(
                Decimal("0.01")
            )
            if total_capacity
            else Decimal("0.00")
        )
        return HousingSummary(
            total_rooms=total,
            occupied_rooms=occupied,
            unoccupied_rooms=total - occupied,
            assignable_rooms=assignable,
            unassignable_rooms=total - assignable,
            problem_rooms=sum(card.service_status == "problem" for card in cards),
            pending_verification_rooms=sum(
                card.verification_status == "pending_verification" for card in cards
            ),
            total_capacity=total_capacity,
            current_occupants=current_occupants,
            remaining_capacity=remaining_capacity,
            occupancy_rate=occupancy_rate,
            single_rooms=sum(card.room_type == "single" for card in cards),
            double_rooms=sum(card.room_type == "double" for card in cards),
            triple_rooms=sum(card.room_type == "triple" for card in cards),
            custom_rooms=sum(card.room_type == "custom" for card in cards),
            functional_rooms=sum(card.room_type == "functional" for card in cards),
        )

    def overview(
        self,
        access: HousingAccessService,
        filters: HousingOverviewFilters,
        page: int,
        page_size: int,
        as_of: datetime,
    ) -> HousingOverviewResponse:
        buildings, floors, rooms = self._catalog_rows()
        building_by_id = {building.id: building for building in buildings}
        floor_by_id = {floor.id: floor for floor in floors}
        visible_rooms = access.filter_rooms(rooms)
        projections = HousingOccupancyService(self.db).room_projections(
            [room.id for room in visible_rooms], as_of
        )
        cards = [
            self._room_card(
                room,
                building_by_id[room.building_id],
                floor_by_id[room.floor_id],
                access,
                projections[room.id],
            )
            for room in visible_rooms
        ]
        filtered = [
            card
            for card in cards
            if self._matches_filters(card, filters, projections[card.id])
        ]
        offset = (page - 1) * page_size
        return HousingOverviewResponse(
            access_revision=access.access_revision,
            as_of=as_of,
            summary=self._summary(filtered),
            rooms=filtered[offset : offset + page_size],
            page=page,
            page_size=page_size,
            total=len(filtered),
        )

    @staticmethod
    def _mapped_integrity_error(exc: IntegrityError) -> HousingServiceError | None:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in _DUPLICATE_CONSTRAINTS:
            return HousingDuplicateCatalogEntry("housing catalog entry already exists")
        if constraint in _CATALOG_CONFIGURATION_CONSTRAINTS:
            return HousingServiceError("housing catalog configuration is invalid")
        if constraint in _ROOM_CONFIGURATION_CONSTRAINTS:
            return HousingInvalidRoomConfiguration("room configuration is invalid")
        if constraint == "ck_housing_rooms_service_status":
            return HousingIssueWorkflowRequired(
                "problem status must use the housing issue workflow"
            )
        return None

    @classmethod
    def _raise_mapped_integrity_error(cls, exc: IntegrityError) -> NoReturn:
        mapped = cls._mapped_integrity_error(exc)
        if mapped is None:
            raise exc
        raise mapped from exc

    def _locked_building(self, building_id: UUID) -> HousingBuilding:
        building = self.db.scalar(
            select(HousingBuilding)
            .where(HousingBuilding.id == building_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if building is None:
            raise HousingResourceNotFound("housing building not found")
        return building

    def _locked_floor(self, floor_id: UUID) -> HousingFloor:
        floor = self.db.scalar(
            select(HousingFloor)
            .where(HousingFloor.id == floor_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if floor is None:
            raise HousingResourceNotFound("housing floor not found")
        return floor

    def _locked_room(self, room_id: UUID) -> HousingRoom:
        room = self.db.scalar(
            select(HousingRoom)
            .where(HousingRoom.id == room_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if room is None:
            raise HousingResourceNotFound("housing room not found")
        return room

    def _locked_active_or_future_stays(
        self,
        room_id: UUID,
        as_of: datetime,
    ) -> list[HousingStay]:
        return list(
            self.db.scalars(
                select(HousingStay)
                .where(HousingStay.room_id == room_id)
                .where(
                    or_(
                        HousingStay.status.in_(("checked_in", "checkout_pending")),
                        (HousingStay.status == "planned")
                        & or_(
                            HousingStay.planned_check_out_at.is_(None),
                            HousingStay.planned_check_out_at > as_of,
                        ),
                    )
                )
                .order_by(HousingStay.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def _maximum_concurrent_occupancy(stays: Sequence[HousingStay]) -> int:
        events: list[tuple[datetime, int]] = []
        for stay in stays:
            starts_at = stay.actual_check_in_at or stay.planned_check_in_at
            ends_at = stay.actual_check_out_at or stay.planned_check_out_at
            events.append((starts_at, 1))
            if ends_at is not None:
                events.append((ends_at, -1))
        events.sort(key=lambda event: (event[0], event[1]))
        occupancy = 0
        peak = 0
        for _, delta in events:
            occupancy += delta
            peak = max(peak, occupancy)
        current_occupants = sum(
            stay.status in {"checked_in", "checkout_pending"} for stay in stays
        )
        return max(peak, current_occupants)

    def _guard_room_mutation(
        self,
        room: HousingRoom,
        *,
        target_room_type: RoomType,
        target_capacity: int,
        target_floor_id: UUID,
        target_service_status: str,
    ) -> None:
        stays = self._locked_active_or_future_stays(room.id, datetime.now(UTC))
        if not stays:
            return
        if (
            (
                target_capacity != room.capacity
                and target_capacity < self._maximum_concurrent_occupancy(stays)
            )
            or (room.room_type != "functional" and target_room_type == "functional")
            or target_floor_id != room.floor_id
            or (
                target_service_status != room.service_status
                and target_service_status in {"maintenance", "disabled"}
            )
        ):
            raise HousingRoomHasActiveStays(
                "housing room has current or future stays"
            )

    def _active_floor_and_building(
        self,
        floor_id: UUID,
    ) -> tuple[HousingFloor, HousingBuilding]:
        floor, building = self._locked_floor_and_building(floor_id)
        self._require_active_parents(floor, building)
        return floor, building

    def _locked_floor_and_building(
        self,
        floor_id: UUID,
    ) -> tuple[HousingFloor, HousingBuilding]:
        row = self.db.execute(
            select(HousingFloor, HousingBuilding)
            .join(HousingBuilding, HousingBuilding.id == HousingFloor.building_id)
            .where(HousingFloor.id == floor_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            raise HousingResourceNotFound("housing floor not found")
        return row[0], row[1]

    @staticmethod
    def _require_active_parents(
        floor: HousingFloor,
        building: HousingBuilding,
    ) -> None:
        if not floor.is_active or not building.is_active:
            raise HousingParentInactive("housing parent is inactive")

    def create_building(
        self,
        input: BuildingCreate,
        *,
        actor_user_id: UUID,
    ) -> HousingBuilding:
        try:
            with write_transaction(self.db):
                building = HousingBuilding(
                    code=_normalized_compact_code(input.code),
                    name=_normalized_name(input.name),
                    display_order=input.display_order,
                    is_active=True,
                    notes=input.notes,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(building)
                self.db.flush()
            return building
        except IntegrityError as exc:
            self._raise_mapped_integrity_error(exc)

    def update_building(
        self,
        building_id: UUID,
        input: BuildingUpdate,
        *,
        actor_user_id: UUID,
    ) -> HousingBuilding:
        try:
            with write_transaction(self.db):
                building = self._locked_building(building_id)
                if input.code is not None:
                    building.code = _normalized_compact_code(input.code)
                if input.name is not None:
                    building.name = _normalized_name(input.name)
                if input.display_order is not None:
                    building.display_order = input.display_order
                if input.is_active is not None:
                    building.is_active = input.is_active
                if "notes" in input.model_fields_set:
                    building.notes = input.notes
                building.updated_by_user_id = actor_user_id
                self.db.flush()
            return building
        except IntegrityError as exc:
            self._raise_mapped_integrity_error(exc)

    def delete_building(self, building_id: UUID) -> None:
        with write_transaction(self.db):
            building = self._locked_building(building_id)
            child = self.db.scalar(
                select(HousingFloor.id)
                .where(HousingFloor.building_id == building.id)
                .order_by(HousingFloor.id)
                .limit(1)
                .with_for_update()
            )
            if child is not None:
                raise HousingCatalogHasChildren("housing building has floors")
            self.db.delete(building)
            self.db.flush()

    def create_floor(
        self,
        input: FloorCreate,
        *,
        actor_user_id: UUID,
    ) -> HousingFloor:
        try:
            with write_transaction(self.db):
                building = self._locked_building(input.building_id)
                if not building.is_active:
                    raise HousingParentInactive("housing building is inactive")
                floor = HousingFloor(
                    building_id=building.id,
                    number=input.number,
                    name=_normalized_name(input.name),
                    display_order=input.display_order,
                    is_active=True,
                    notes=input.notes,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(floor)
                self.db.flush()
            return floor
        except IntegrityError as exc:
            self._raise_mapped_integrity_error(exc)

    def update_floor(
        self,
        floor_id: UUID,
        input: FloorUpdate,
        *,
        actor_user_id: UUID,
    ) -> HousingFloor:
        try:
            with write_transaction(self.db):
                floor = self._locked_floor(floor_id)
                if input.number is not None:
                    floor.number = input.number
                if input.name is not None:
                    floor.name = _normalized_name(input.name)
                if input.display_order is not None:
                    floor.display_order = input.display_order
                if input.is_active is not None:
                    floor.is_active = input.is_active
                if "notes" in input.model_fields_set:
                    floor.notes = input.notes
                floor.updated_by_user_id = actor_user_id
                self.db.flush()
            return floor
        except IntegrityError as exc:
            self._raise_mapped_integrity_error(exc)

    def delete_floor(self, floor_id: UUID) -> None:
        with write_transaction(self.db):
            floor = self._locked_floor(floor_id)
            child = self.db.scalar(
                select(HousingRoom.id)
                .where(HousingRoom.floor_id == floor.id)
                .order_by(HousingRoom.id)
                .limit(1)
                .with_for_update()
            )
            if child is not None:
                raise HousingCatalogHasChildren("housing floor has rooms")
            self.db.delete(floor)
            self.db.flush()

    def create_room(
        self,
        input: RoomCreate,
        *,
        actor_user_id: UUID,
    ) -> HousingRoom:
        try:
            with write_transaction(self.db):
                floor, building = self._active_floor_and_building(input.floor_id)
                try:
                    validate_room_type_capacity(input.room_type, input.capacity)
                except ValueError as exc:
                    raise HousingInvalidRoomConfiguration(str(exc)) from exc
                room_number = _normalized_compact_code(input.room_number)
                room = HousingRoom(
                    building_id=building.id,
                    floor_id=floor.id,
                    room_number=room_number,
                    normalized_key=room_number,
                    room_type=input.room_type,
                    capacity=input.capacity,
                    area_square_meters=input.area_square_meters,
                    verification_status="pending_verification",
                    service_status="available",
                    notes=input.notes,
                    version=1,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(room)
                self.db.flush()
            return room
        except IntegrityError as exc:
            self._raise_mapped_integrity_error(exc)

    def update_room(
        self,
        room_id: UUID,
        input: RoomUpdate,
        *,
        actor_user_id: UUID,
        authorize_room: RoomAuthorizer | None = None,
        authorize_floor: FloorAuthorizer | None = None,
    ) -> HousingRoom:
        try:
            with write_transaction(self.db):
                room = self._locked_room(room_id)
                if authorize_room is not None:
                    authorize_room(room)
                target_floor = None
                target_building = None
                if input.floor_id is not None:
                    target_floor, target_building = self._locked_floor_and_building(
                        input.floor_id
                    )
                    if authorize_floor is not None:
                        authorize_floor(target_floor)
                    self._require_active_parents(target_floor, target_building)
                if room.version != input.version:
                    raise HousingStaleVersion("housing room version is stale")
                if input.service_status is not None:
                    open_issue_id = self.db.scalar(
                        select(HousingRoomIssue.id)
                        .where(
                            HousingRoomIssue.room_id == room.id,
                            HousingRoomIssue.status == "open",
                        )
                        .with_for_update()
                    )
                    if open_issue_id is not None:
                        raise HousingIssueWorkflowRequired(
                            "open housing issue must use the housing issue workflow"
                        )
                if input.model_dump().get("service_status") == "problem":
                    raise HousingIssueWorkflowRequired(
                        "problem status must use the housing issue workflow"
                    )
                room_type = (
                    input.room_type
                    if input.room_type is not None
                    else cast(RoomType, room.room_type)
                )
                capacity = input.capacity if input.capacity is not None else room.capacity
                try:
                    validate_room_type_capacity(room_type, capacity)
                except ValueError as exc:
                    raise HousingInvalidRoomConfiguration(str(exc)) from exc
                self._guard_room_mutation(
                    room,
                    target_room_type=room_type,
                    target_capacity=capacity,
                    target_floor_id=(
                        target_floor.id if target_floor is not None else room.floor_id
                    ),
                    target_service_status=(
                        input.service_status
                        if input.service_status is not None
                        else room.service_status
                    ),
                )

                structural_changed = False
                if (
                    target_floor is not None
                    and target_building is not None
                    and target_floor.id != room.floor_id
                ):
                    room.floor_id = target_floor.id
                    room.building_id = target_building.id
                    structural_changed = True
                if input.room_number is not None:
                    normalized_number = _normalized_compact_code(input.room_number)
                    if normalized_number != room.room_number:
                        structural_changed = True
                    room.room_number = normalized_number
                    room.normalized_key = normalized_number
                if input.room_type is not None:
                    if input.room_type != room.room_type:
                        structural_changed = True
                    room.room_type = input.room_type
                if input.capacity is not None:
                    if input.capacity != room.capacity:
                        structural_changed = True
                    room.capacity = input.capacity
                if "area_square_meters" in input.model_fields_set:
                    if input.area_square_meters != room.area_square_meters:
                        structural_changed = True
                    room.area_square_meters = input.area_square_meters
                if input.service_status is not None:
                    room.service_status = input.service_status
                if "notes" in input.model_fields_set:
                    room.notes = input.notes
                if structural_changed:
                    room.verification_status = "pending_verification"
                room.version += 1
                room.updated_by_user_id = actor_user_id
                self.db.flush()
            return room
        except IntegrityError as exc:
            self._raise_mapped_integrity_error(exc)

    def delete_room(
        self,
        room_id: UUID,
        *,
        authorize_room: RoomAuthorizer | None = None,
    ) -> None:
        with write_transaction(self.db):
            room = self._locked_room(room_id)
            if authorize_room is not None:
                authorize_room(room)
            historical_issue = self.db.scalar(
                select(HousingRoomIssue.id)
                .where(HousingRoomIssue.room_id == room.id)
                .order_by(HousingRoomIssue.reported_at, HousingRoomIssue.id)
                .limit(1)
                .with_for_update()
            )
            if historical_issue is not None:
                raise HousingCatalogHasChildren("housing room has issue history")
            if self._locked_active_or_future_stays(room.id, datetime.now(UTC)):
                raise HousingRoomHasActiveStays(
                    "housing room has current or future stays"
                )
            historical_stay = self.db.scalar(
                select(HousingStay.id)
                .where(HousingStay.room_id == room.id)
                .order_by(HousingStay.id)
                .limit(1)
                .with_for_update()
            )
            if historical_stay is not None:
                raise HousingCatalogHasChildren("housing room has stay history")
            self.db.delete(room)
            self.db.flush()

    def verify_room(
        self,
        room_id: UUID,
        *,
        expected_version: int,
        actor_user_id: UUID,
        authorize_room: RoomAuthorizer | None = None,
    ) -> HousingRoom:
        with write_transaction(self.db):
            room = self._locked_room(room_id)
            if authorize_room is not None:
                authorize_room(room)
            if room.version != expected_version:
                raise HousingStaleVersion("housing room version is stale")
            if room.verification_status != "pending_verification":
                raise HousingInvalidRoomConfiguration(
                    "housing room verification requires pending status"
                )
            room.verification_status = "verified"
            room.version += 1
            room.updated_by_user_id = actor_user_id
            self.db.flush()
        return room

    def verify_rooms(
        self,
        inputs: Sequence[RoomVersionInput],
        *,
        actor_user_id: UUID,
        authorize_rooms: RoomBatchAuthorizer | None = None,
    ) -> list[HousingRoom]:
        if not inputs:
            raise HousingInvalidRoomConfiguration("room verification batch is empty")
        requested_ids = [item.room_id for item in inputs]
        if len(requested_ids) != len(set(requested_ids)):
            raise HousingInvalidRoomConfiguration(
                "room verification batch contains duplicate rooms"
            )
        with write_transaction(self.db):
            sorted_ids = sorted(requested_ids)
            locked_rooms = list(
                self.db.scalars(
                    select(HousingRoom)
                    .where(HousingRoom.id.in_(sorted_ids))
                    .order_by(HousingRoom.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            rooms_by_id = {room.id: room for room in locked_rooms}
            if set(rooms_by_id) != set(requested_ids):
                raise HousingResourceNotFound("one or more housing rooms were not found")
            if authorize_rooms is not None:
                authorize_rooms([rooms_by_id[item.room_id] for item in inputs])
            for item in inputs:
                if rooms_by_id[item.room_id].version != item.version:
                    raise HousingStaleVersion("housing room version is stale")
            if any(
                rooms_by_id[item.room_id].verification_status
                != "pending_verification"
                for item in inputs
            ):
                raise HousingInvalidRoomConfiguration(
                    "housing room verification requires pending rooms"
                )
            for item in inputs:
                room = rooms_by_id[item.room_id]
                room.verification_status = "verified"
                room.version += 1
                room.updated_by_user_id = actor_user_id
            self.db.flush()
            result = [rooms_by_id[item.room_id] for item in inputs]
        return result
