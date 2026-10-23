"""Current-owner housing summaries; no invitation, family or global-room authority."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.orm import Session, aliased

from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.housing.invited_projection import (
    InvitedHousingProjectionService,
    invited_housing_status_expression,
    require_consistent_current_stays,
)
from app.identity.models import Person, StudentProfile
from app.personnel_intake.access import IntakeAccessSnapshot, PersonnelIntakeAccess
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import CoachPersonResponsibility
from app.personnel_intake.schemas import (
    ManagedAffiliation,
    ManagedPeopleFilters,
    ManagedPeoplePage,
    ManagedPerson,
)
from app.rbac.policy import PolicyRule
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.models import TeamManagerAssignment, TeamMembership

PERMISSION = "personnel_intake.housing.read"


class ManagedPeopleQuery:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _affiliations(state: IntakeAccessSnapshot, as_of: datetime) -> Any:
        coach = aliased(Person)
        coaches = (
            select(
                CoachPersonResponsibility.person_id.label("person_id"),
                literal("coach").label("kind"),
                coach.id.label("id"),
                coach.full_name.label("label"),
            )
            .join(coach, coach.id == CoachPersonResponsibility.coach_person_id)
            .join(StudentProfile, StudentProfile.person_id == CoachPersonResponsibility.person_id)
            .where(
                CoachPersonResponsibility.coach_person_id == state.person_id,
                CoachPersonResponsibility.ended_at.is_(None),
                true() if "coach" in state.roles and state.own_coach_eligible else false(),
            )
        )
        teams = (
            select(
                TeamMembership.person_id.label("person_id"),
                literal("team").label("kind"),
                ProfessionalTeam.id.label("id"),
                ProfessionalTeam.name.label("label"),
            )
            .join(ProfessionalTeam, ProfessionalTeam.id == TeamMembership.team_id)
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
            .join(TeamManagerAssignment, TeamManagerAssignment.team_id == ProfessionalTeam.id)
            .where(
                TeamMembership.ended_at.is_(None),
                TeamMembership.joined_on <= business_date_at(as_of),
                TeamManagerAssignment.account_id == state.account_id,
                TeamManagerAssignment.ended_at.is_(None),
                ProfessionalTeam.is_active,
                GameProject.is_active,
                GameProject.is_team_division,
                true() if "team_manager" in state.roles else false(),
            )
        )
        current = union_all(coaches, teams).subquery()

        def scope(rule: PolicyRule) -> ColumnElement[bool]:
            if rule.scope_kind == "global":
                return true()
            if rule.scope_kind == "person":
                return current.c.person_id == rule.scope_id if rule.scope_id else true()
            if rule.scope_kind == "self":
                return (
                    current.c.person_id == state.person_id
                    if rule.scope_id in {None, state.person_id}
                    else false()
                )
            if rule.scope_kind == "assigned_class":
                return current.c.kind == "coach" if rule.scope_id is None else false()
            if rule.scope_kind == "assigned_team":
                return and_(
                    current.c.kind == "team",
                    current.c.id == rule.scope_id if rule.scope_id else true(),
                )
            return false()

        allows = [
            scope(r)
            for r in [*state.decisions.role_rules, *state.decisions.overrides]
            if r.permission == PERMISSION and r.effect == "allow"
        ]
        denies = [
            scope(r)
            for r in state.decisions.overrides
            if r.permission == PERMISSION and r.effect == "deny"
        ]
        # A deny attached to any current owner path denies the subject, not
        # merely that affiliation row; another role must not rescue the read.
        denied_people = (
            select(current.c.person_id).where(or_(*denies) if denies else false()).correlate(None)
        )
        return (
            select(current)
            .where(
                or_(*allows) if allows else false(),
                current.c.person_id.not_in(denied_people),
            )
            .subquery()
        )

    @staticmethod
    def _cursor_scope(actor: AuthContext, filters: ManagedPeopleFilters) -> str:
        serialized = json.dumps(
            {
                "account": str(actor.account.id),
                "filters": filters.model_dump(mode="json", exclude={"cursor"}),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def _decode_cursor(cursor: str | None, scope: str) -> UUID | None:
        if not cursor:
            return None
        try:
            payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (
                not isinstance(payload, dict)
                or set(payload) != {"scope", "last"}
                or payload["scope"] != scope
                or not isinstance(payload["last"], str)
            ):
                raise ValueError
            return UUID(payload["last"])
        except (ValueError, TypeError, KeyError) as exc:
            raise PersonnelIntakeError(
                "managed_people_cursor_invalid", status=422, detail="分页已失效，请重新查询"
            ) from exc

    def list(self, actor: AuthContext, filters: ManagedPeopleFilters) -> ManagedPeoplePage:
        as_of = datetime.now(UTC)
        state = PersonnelIntakeAccess(self.db).snapshot(actor)
        if not state.can_enter_managed_people():
            raise PersonnelIntakeError(
                "personnel_intake_forbidden", status=403, detail="无权查看负责人员住宿"
            )
        scope = self._cursor_scope(actor, filters)
        after = self._decode_cursor(filters.cursor, scope)
        affiliations = self._affiliations(state, as_of)
        is_student = exists().where(StudentProfile.person_id == Person.id)
        is_player = exists(
            select(TeamMembership.id)
            .join(ProfessionalTeam, ProfessionalTeam.id == TeamMembership.team_id)
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
            .where(
                TeamMembership.person_id == Person.id,
                TeamMembership.ended_at.is_(None),
                TeamMembership.joined_on <= business_date_at(as_of),
                ProfessionalTeam.is_active,
                GameProject.is_active,
                GameProject.is_team_division,
            )
        )
        predicates = [
            Person.deleted_at.is_(None),
            Person.id.in_(select(affiliations.c.person_id)),
            or_(is_student, is_player),
        ]
        if filters.query:
            escaped = filters.query.replace("/", "//").replace("%", "/%").replace("_", "/_")
            predicates.append(
                or_(
                    Person.full_name.ilike(f"%{escaped}%", escape="/"),
                    Person.nickname.ilike(f"%{escaped}%", escape="/"),
                )
            )
        if filters.kind == "student":
            predicates.append(and_(is_student, ~is_player))
        elif filters.kind == "player":
            predicates.append(and_(is_player, ~is_student))
        elif filters.kind == "both":
            predicates.append(and_(is_student, is_player))
        if filters.team_id is not None:
            predicates.append(
                Person.id.in_(
                    select(affiliations.c.person_id).where(
                        affiliations.c.kind == "team", affiliations.c.id == filters.team_id
                    )
                )
            )
        authorized_ids: Select[Any] = select(Person.id).where(*predicates)
        require_consistent_current_stays(self.db, authorized_ids)
        if filters.housing_status:
            predicates.append(
                invited_housing_status_expression(cast(ColumnElement[UUID], Person.id), as_of=as_of)
                == filters.housing_status
            )
        total = self.db.scalar(select(func.count()).select_from(Person).where(*predicates)) or 0
        statement = select(
            Person.id,
            Person.full_name,
            Person.nickname,
            is_student.label("is_student"),
            is_player.label("is_player"),
        ).where(*predicates)
        if after is not None:
            statement = statement.where(Person.id > after)
        rows = self.db.execute(statement.order_by(Person.id).limit(filters.page_size + 1)).all()
        has_more = len(rows) > filters.page_size
        rows = rows[: filters.page_size]
        ids = [row.id for row in rows]
        related: dict[UUID, list[ManagedAffiliation]] = {pid: [] for pid in ids}
        if ids:
            for row in self.db.execute(
                select(affiliations)
                .where(affiliations.c.person_id.in_(ids))
                .order_by(affiliations.c.kind, affiliations.c.id)
            ):
                related[row.person_id].append(
                    ManagedAffiliation(kind=row.kind, id=row.id, label=row.label)
                )
        summaries = InvitedHousingProjectionService(self.db).summaries_for_people(
            authorized_person_ids=ids, as_of=as_of
        )
        items = []
        for row in rows:
            kinds: list[Literal["student", "player"]] = []
            if row.is_student:
                kinds.append("student")
            if row.is_player:
                kinds.append("player")
            items.append(
                ManagedPerson(
                    person_id=row.id,
                    full_name=row.full_name,
                    nickname=row.nickname,
                    kinds=kinds,
                    authorized_affiliations=related[row.id],
                    housing=summaries[row.id],
                )
            )
        cursor = (
            base64.urlsafe_b64encode(
                json.dumps({"scope": scope, "last": str(ids[-1])}, separators=(",", ":")).encode()
            ).decode()
            if has_more
            else None
        )
        return ManagedPeoplePage(items=items, next_cursor=cursor, total=total)
