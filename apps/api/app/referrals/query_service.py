from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.housing.invited_projection import (
    InvitedHousingProjectionService,
    invited_housing_status_expression,
    require_consistent_current_stays,
)
from app.referrals.access import InvitedHousingAccess
from app.referrals.schemas import (
    InvitationSource,
    InvitedHousingPage,
    InvitedHousingStatus,
    InvitedPersonHousing,
    SourceKind,
)


class InvitedHousingQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_housing(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        source_kind: SourceKind,
        organization_id: UUID | None,
        status: InvitedHousingStatus | None,
        page: int,
        page_size: int,
        as_of: datetime,
        request_id: str,
    ) -> InvitedHousingPage:
        people = (
            InvitedHousingAccess(self.db)
            .authorized_people_statement(
                user_id=user_id,
                actor_person_id=actor_person_id,
                source_kind=source_kind,
                organization_id=organization_id,
                as_of=as_of,
            )
            .subquery()
        )
        # Check the whole authorized source BEFORE filtering or pagination. An
        # inconsistent resident must never silently disappear under another filter.
        require_consistent_current_stays(self.db, select(people.c.student_person_id))
        statement = select(people)
        if status is not None:
            statement = statement.where(
                invited_housing_status_expression(people.c.student_person_id, as_of=as_of) == status
            )
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = (
            self.db.execute(
                statement.order_by(people.c.bound_at.desc(), people.c.student_person_id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
            .mappings()
            .all()
        )
        summaries = InvitedHousingProjectionService(self.db).summaries_for_people(
            authorized_person_ids=[row["student_person_id"] for row in rows], as_of=as_of
        )
        items = [
            InvitedPersonHousing(
                person_id=row["student_person_id"],
                full_name=row["full_name"],
                nickname=row["nickname"],
                source=InvitationSource(
                    source_kind=row["source_kind"],
                    organization_id=row["organization_id"],
                    name=row["source_name"],
                ),
                housing=summaries[row["student_person_id"]],
            )
            for row in rows
        ]
        metadata: dict[str, object] = {"source_kind": source_kind, "result_count": len(items)}
        if organization_id is not None:
            metadata["organization_id"] = str(organization_id)
        AuditService(self.db).append(
            actor_user_id=user_id,
            action="referrals.housing.read",
            resource_type="invited_housing",
            resource_id=None,
            purpose="查看邀请住宿",
            outcome="allowed",
            request_id=request_id,
            metadata=metadata,
        )
        self.db.commit()
        return InvitedHousingPage(
            as_of=as_of, items=items, total=total, page=page, page_size=page_size
        )
