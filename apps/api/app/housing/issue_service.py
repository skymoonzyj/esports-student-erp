from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import NoReturn, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.housing.errors import (
    HousingIssueNotOpen,
    HousingOpenIssueExists,
    HousingResourceNotFound,
    HousingServiceError,
    HousingStaleVersion,
)
from app.housing.models import HousingRoom, HousingRoomIssue
from app.housing.schemas import RoomIssueCreate, RoomIssueResolve

_OPEN_ISSUE_CONSTRAINT = "uq_housing_room_issues_open_room"
RoomAuthorizer = Callable[[HousingRoom], None]


class HousingIssueService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.utcoffset() is None:
            raise HousingServiceError("housing timestamps must be timezone-aware")

    @staticmethod
    def _integrity_constraint(exc: IntegrityError) -> str | None:
        diagnostic = getattr(exc.orig, "diag", None)
        return cast(str | None, getattr(diagnostic, "constraint_name", None))

    @classmethod
    def _raise_mapped_integrity(cls, exc: IntegrityError) -> NoReturn:
        if cls._integrity_constraint(exc) == _OPEN_ISSUE_CONSTRAINT:
            raise HousingOpenIssueExists(
                "housing room already has an open issue"
            ) from exc
        raise exc

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

    def _locked_open_issue(self, room_id: UUID) -> HousingRoomIssue | None:
        return self.db.scalar(
            select(HousingRoomIssue)
            .where(
                HousingRoomIssue.room_id == room_id,
                HousingRoomIssue.status == "open",
            )
            .order_by(HousingRoomIssue.reported_at, HousingRoomIssue.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _locked_issue(self, issue_id: UUID) -> HousingRoomIssue:
        issue = self.db.scalar(
            select(HousingRoomIssue)
            .where(HousingRoomIssue.id == issue_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if issue is None:
            raise HousingResourceNotFound("housing room issue not found")
        return issue

    def report(
        self,
        room_id: UUID,
        input: RoomIssueCreate,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: RoomAuthorizer,
    ) -> HousingRoomIssue:
        self._require_aware(occurred_at)
        if not isinstance(input.description, str) or not input.description.strip():
            raise HousingServiceError("housing issue description is required")
        description = input.description.strip()
        try:
            with write_transaction(self.db):
                room = self._locked_room(room_id)
                authorize_room(room)
                if self._locked_open_issue(room.id) is not None:
                    raise HousingOpenIssueExists(
                        "housing room already has an open issue"
                    )
                if room.version != input.room_version:
                    raise HousingStaleVersion("housing room version is stale")

                issue = HousingRoomIssue(
                    room_id=room.id,
                    description=description,
                    previous_service_status=room.service_status,
                    reported_at=occurred_at,
                    reported_by_user_id=actor_user_id,
                    status="open",
                )
                self.db.add(issue)
                room.service_status = "problem"
                room.version += 1
                room.updated_by_user_id = actor_user_id
                self.db.flush()
            return issue
        except IntegrityError as exc:
            self._raise_mapped_integrity(exc)

    def resolve(
        self,
        issue_id: UUID,
        input: RoomIssueResolve,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: RoomAuthorizer,
    ) -> HousingRoomIssue:
        self._require_aware(occurred_at)
        if not isinstance(input.resolution_note, str) or not input.resolution_note.strip():
            raise HousingServiceError("housing issue resolution note is required")
        resolution_note = input.resolution_note.strip()

        with write_transaction(self.db):
            preliminary = self.db.get(HousingRoomIssue, issue_id)
            if preliminary is None:
                raise HousingResourceNotFound("housing room issue not found")
            room = self._locked_room(preliminary.room_id)
            authorize_room(room)
            issue = self._locked_issue(issue_id)
            if issue.status != "open":
                raise HousingIssueNotOpen("housing room issue is not open")
            if room.version != input.room_version:
                raise HousingStaleVersion("housing room version is stale")
            if occurred_at < issue.reported_at:
                raise HousingServiceError(
                    "housing issue resolution cannot precede its report"
                )

            issue.status = "resolved"
            issue.resolved_at = occurred_at
            issue.resolved_by_user_id = actor_user_id
            issue.resolution_note = resolution_note
            room.service_status = issue.previous_service_status
            room.version += 1
            room.updated_by_user_id = actor_user_id
            self.db.flush()
        return issue
