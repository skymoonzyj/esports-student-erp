from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.housing.access import HousingAccessService
from app.housing.errors import HousingInvalidRoomConfiguration
from app.housing.inventory_service import HousingInventoryService
from app.housing.issue_service import HousingIssueService
from app.housing.models import HousingBuilding, HousingRoom
from app.housing.property_models import HousingPropertyChange
from app.housing.property_schemas import (
    PropertyChangeCreate,
    PropertyChangeItem,
    PropertyChangeList,
    PropertyReview,
)
from app.housing.schemas import (
    RoomIssueCreate,
    RoomUpdate,
    validate_room_type_capacity,
)
from app.identity.models import Person


def conflict(detail: str = "申请状态已发生变化，请刷新后重试") -> None:
    raise ProblemError(status=409, code="housing_property_conflict", detail=detail)


class HousingPropertyService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _access(self, account_id: UUID, occurred_at: datetime) -> HousingAccessService:
        return self._accesses({account_id}, occurred_at)[account_id]

    def _accesses(
        self, account_ids: set[UUID], occurred_at: datetime
    ) -> dict[UUID, HousingAccessService]:
        # Match account/role lifecycle locking: all persons first, then accounts.
        person_ids = list(
            self.db.scalars(select(UserAccount.person_id).where(UserAccount.id.in_(account_ids)))
        )
        people = list(
            self.db.scalars(
                select(Person)
                .where(Person.id.in_(person_ids))
                .order_by(Person.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        accounts = list(
            self.db.scalars(
                select(UserAccount)
                .where(UserAccount.id.in_(account_ids))
                .order_by(UserAccount.person_id, UserAccount.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if (
            len(accounts) != len(account_ids)
            or len(people) != len(account_ids)
            or any(account.status != "active" for account in accounts)
            or any(person.deleted_at is not None for person in people)
        ):
            raise ProblemError(status=403, code="permission_denied", detail="账号无权执行此操作")
        return {
            identity: HousingAccessService(self.db, identity, occurred_at)
            for identity in account_ids
        }

    def _room(self, room_id: UUID) -> HousingRoom:
        room = self.db.scalar(
            select(HousingRoom)
            .where(HousingRoom.id == room_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if room is None:
            raise ProblemError(status=404, code="housing_resource_not_found", detail="房间不存在")
        return room

    @staticmethod
    def _require(allowed: bool) -> None:
        if not allowed:
            raise ProblemError(status=403, code="permission_denied", detail="没有此操作权限")

    def capabilities(
        self, access: HousingAccessService, *, room_id: UUID | None = None
    ) -> dict[str, bool]:
        if room_id is not None:
            room = self.db.get(HousingRoom, room_id)
            can_export = room is not None and access.can_export_basic(room)
            return {
                "can_submit": room is not None and access.can_submit_changes(room),
                "can_review": room is not None and access.can_review_changes(room),
                "can_export": can_export,
                "show_basic_export": "property" in access.roles and can_export,
                "can_create_request": room is not None and access.can_create_property_request(room),
            }
        submit = access.submit_changes_criteria()
        criteria = {
            "can_submit": submit,
            "can_review": access.room_permissions_criteria(
                "housing.read", "housing.changes.review"
            ),
            "can_export": access.room_permissions_criteria("housing.read", "housing.exports.basic"),
            "can_create_request": access.create_property_request_criteria(),
        }
        result = {
            key: self.db.scalar(select(HousingRoom.id).where(condition).limit(1)) is not None
            for key, condition in criteria.items()
        }
        return {**result, "show_basic_export": "property" in access.roles and result["can_export"]}

    def item(
        self, row: HousingPropertyChange, access: HousingAccessService, actor_id: UUID
    ) -> PropertyChangeItem:
        room = self.db.get(HousingRoom, row.room_id)
        assert room is not None
        building = self.db.get(HousingBuilding, room.building_id)
        name = self.db.scalar(
            select(Person.full_name)
            .join(UserAccount, UserAccount.person_id == Person.id)
            .where(UserAccount.id == row.submitted_by_user_id)
        )
        return PropertyChangeItem(
            id=row.id,
            room_id=row.room_id,
            room_number=room.room_number,
            building_name=building.name if building else "",
            base_version=row.base_version,
            changes=row.changes,
            before=row.before,
            reason=row.reason,
            status=row.status,
            submitted_by_name=name or "",
            submitted_at=row.submitted_at,
            reviewed_at=row.reviewed_at,
            review_note=row.review_note,
            can_review=row.status == "pending"
            and actor_id != row.submitted_by_user_id
            and access.can_review_changes(room),
            can_withdraw=row.status == "pending"
            and actor_id == row.submitted_by_user_id
            and access.can_submit_changes(room),
        )

    def list(
        self,
        access: HousingAccessService,
        actor_id: UUID,
        *,
        status: str,
        room_id: UUID | None,
        page: int,
        page_size: int,
        view: Literal["all", "submitted", "review"] = "all",
    ) -> PropertyChangeList:
        review_visibility = access.room_permissions_criteria(
            "housing.read", "housing.changes.review"
        )
        submitted_visibility = (
            HousingPropertyChange.submitted_by_user_id == actor_id
        ) & access.room_permissions_criteria("housing.read", "housing.changes.submit")
        visibility = (
            (HousingPropertyChange.submitted_by_user_id == actor_id)
            & access.submit_changes_criteria()
            if view == "submitted"
            else review_visibility
            if view == "review"
            else or_(review_visibility, submitted_visibility)
        )
        stmt = (
            select(HousingPropertyChange)
            .join(HousingRoom, HousingRoom.id == HousingPropertyChange.room_id)
            .where(HousingPropertyChange.status == status, visibility)
        )
        if room_id:
            stmt = stmt.where(HousingPropertyChange.room_id == room_id)
        from sqlalchemy import func

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.db.scalars(
            stmt.order_by(HousingPropertyChange.submitted_at.desc(), HousingPropertyChange.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return PropertyChangeList(
            items=[self.item(row, access, actor_id) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def submit(
        self, room_id: UUID, input: PropertyChangeCreate, *, actor_id: UUID, occurred_at: datetime
    ) -> HousingPropertyChange:
        with atomic_write_transaction(self.db):
            access = self._access(actor_id, occurred_at)
            room = self._room(room_id)
            access = HousingAccessService(self.db, actor_id, occurred_at)
            if access.roles & {"administration", "super_admin"} or access.can_manage_room(room):
                raise ProblemError(
                    status=403,
                    code="permission_denied",
                    detail="当前账号请使用房间直接编辑，无需提交待审申请",
                )
            self._require(access.can_create_property_request(room))
            if room.version != input.expected_version:
                raise ProblemError(
                    status=409, code="housing_stale_version", detail="房间已更新，请刷新后重新提交"
                )
            if self.db.scalar(
                select(HousingPropertyChange.id).where(
                    HousingPropertyChange.room_id == room_id,
                    HousingPropertyChange.status == "pending",
                )
            ):
                conflict("该房间已有待审核申请")
            changes = input.changes.model_dump(exclude_unset=True)
            try:
                validate_room_type_capacity(
                    changes.get("room_type", room.room_type), changes.get("capacity", room.capacity)
                )
            except ValueError as exc:
                raise HousingInvalidRoomConfiguration(str(exc)) from exc
            row = HousingPropertyChange(
                room_id=room.id,
                base_version=room.version,
                changes=changes,
                before={key: getattr(room, key) for key in changes},
                reason=input.reason,
                status="pending",
                submitted_by_user_id=actor_id,
                submitted_at=occurred_at,
            )
            self.db.add(row)
            self.db.flush()
            return row

    def _locked_request(self, request_id: UUID) -> HousingPropertyChange:
        row = self.db.scalar(
            select(HousingPropertyChange)
            .where(HousingPropertyChange.id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ProblemError(status=404, code="housing_resource_not_found", detail="申请不存在")
        return row

    def review(
        self, request_id: UUID, input: PropertyReview, *, actor_id: UUID, occurred_at: datetime
    ) -> HousingPropertyChange:
        with atomic_write_transaction(self.db):
            row = self._locked_request(request_id)
            if row.status != "pending":
                conflict()
            self._accesses(
                {actor_id, row.submitted_by_user_id} if input.decision == "approve" else {actor_id},
                occurred_at,
            )
            room = self._room(row.room_id)
            identities = {
                identity: HousingAccessService(self.db, identity, occurred_at)
                for identity in {actor_id, row.submitted_by_user_id}
            }
            self._require(identities[actor_id].can_review_changes(room))
            self._require(actor_id != row.submitted_by_user_id)
            if input.decision == "approve":
                self._require(identities[row.submitted_by_user_id].can_submit_changes(room))
                if room.version != row.base_version:
                    raise ProblemError(
                        status=409,
                        code="housing_stale_version",
                        detail="房间已更新，请撤回并重新提交",
                    )
                self._apply(row, room, actor_id, occurred_at)
                self._require(
                    HousingAccessService(self.db, actor_id, occurred_at).can_review_changes(room)
                )
                self._require(
                    HousingAccessService(
                        self.db, row.submitted_by_user_id, occurred_at
                    ).can_submit_changes(room)
                )
            row.status = "approved" if input.decision == "approve" else "rejected"
            row.reviewed_by_user_id, row.reviewed_at, row.review_note = (
                actor_id,
                occurred_at,
                input.note,
            )
            self.db.flush()
            return row

    def _apply(
        self,
        row: HousingPropertyChange,
        room: HousingRoom,
        actor_id: UUID,
        occurred_at: datetime,
    ) -> None:
        changes = dict(row.changes)
        target_status = changes.pop("service_status", None)
        issue_service = HousingIssueService(self.db)
        issue = issue_service._locked_open_issue(room.id)
        if target_status and issue and target_status != "problem":
            # A service-status proposal is not evidence of issue resolution.
            raise ProblemError(
                status=409,
                code="housing_issue_workflow_required",
                detail="请行政先处理并解决原问题，再撤回申请并按最新版本重新提交",
            )
        if target_status == "problem":
            initial_version = room.version
            if changes:
                HousingInventoryService(self.db).update_room(
                    room.id, RoomUpdate(version=room.version, **changes), actor_user_id=actor_id
                )
            if issue is None:
                issue_service.report(
                    room.id,
                    RoomIssueCreate(
                        room_version=room.version,
                        description=row.changes.get("notes") or row.reason,
                    ),
                    actor_user_id=actor_id,
                    occurred_at=occurred_at,
                    authorize_room=lambda _: None,
                )
            room.verification_status = "verified"
            if room.version == initial_version:
                room.version += 1
            room.updated_by_user_id = actor_id
            self.db.flush()
            return
        if target_status is not None:
            changes["service_status"] = target_status
        if changes:
            HousingInventoryService(self.db).update_room(
                room.id, RoomUpdate(version=room.version, **changes), actor_user_id=actor_id
            )

    def withdraw(
        self, request_id: UUID, *, actor_id: UUID, occurred_at: datetime
    ) -> HousingPropertyChange:
        with atomic_write_transaction(self.db):
            row = self._locked_request(request_id)
            if row.status != "pending":
                conflict()
            access = self._access(actor_id, occurred_at)
            room = self._room(row.room_id)
            access = HousingAccessService(self.db, actor_id, occurred_at)
            self._require(row.submitted_by_user_id == actor_id and access.can_submit_changes(room))
            row.status, row.reviewed_at, row.reviewed_by_user_id = (
                "withdrawn",
                occurred_at,
                actor_id,
            )
            self.db.flush()
            return row
