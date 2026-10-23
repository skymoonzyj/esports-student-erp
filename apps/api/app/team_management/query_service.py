from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Select, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.time import business_date_at, business_today
from app.identity.models import Person
from app.identity.name_search import is_name_initial_query, name_matches_query, normalize_name_query
from app.rbac.models import Role, UserRole
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.access import TeamAccessService
from app.team_management.errors import TeamAccessDenied, TeamNotFound
from app.team_management.models import TeamManagerAssignment, TeamMembership
from app.team_management.projection import project_person_teams
from app.team_management.schemas import (
    MyTeamResponse,
    TeamAction,
    TeamAssignmentView,
    TeamCandidate,
    TeamCandidatePage,
    TeamMemberView,
    TeamPage,
    TeamSummary,
)


def _invalid(detail: str) -> ProblemError:
    return ProblemError(status=422, code="validation_error", detail=detail)


def _age(birth: date, today: date) -> int:
    return max(0, today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day)))


def _pagination(page: int, page_size: int) -> None:
    if page < 1 or not 1 <= page_size <= 100:
        raise _invalid("页码须大于零，每页数量须为 1 至 100")


class TeamQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.access = TeamAccessService(db)

    def _require_team(self, actor: AuthContext, team_id: UUID, action: TeamAction) -> None:
        try:
            self.access.require_team(actor, team_id, action)
        except TeamAccessDenied as exc:
            raise TeamNotFound() from exc

    def list_teams(
        self, actor: AuthContext, *, project_id: UUID | None, page: int, page_size: int
    ) -> TeamPage[TeamSummary]:
        _pagination(page, page_size)
        count = (
            select(func.count(TeamMembership.id))
            .where(
                TeamMembership.team_id == ProfessionalTeam.id,
                TeamMembership.ended_at.is_(None),
            )
            .correlate(ProfessionalTeam)
            .scalar_subquery()
        )
        actions: tuple[TeamAction, ...] = ("read", "manage", "transfer", "assign_managers")
        scopes = {action: self.access.visible_team_ids(actor, action) for action in actions}
        query = (
            select(
                ProfessionalTeam.id,
                ProfessionalTeam.project_id,
                GameProject.name.label("project_name"),
                ProfessionalTeam.name,
                (
                    ProfessionalTeam.is_active
                    & GameProject.is_active
                    & GameProject.is_team_division
                ).label("is_active"),
                count.label("member_count"),
                *(ProfessionalTeam.id.in_(scopes[action]).label(action) for action in actions),
            )
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
            .where(
                ProfessionalTeam.id.in_(scopes["read"]),
            )
        )
        if project_id is not None:
            query = query.where(ProfessionalTeam.project_id == project_id)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.execute(
            query.order_by(GameProject.name, ProfessionalTeam.name, ProfessionalTeam.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        items = []
        for row in rows:
            data = dict(row)
            data["allowed_actions"] = [action for action in actions if data.pop(action)]
            items.append(TeamSummary(**data))
        return TeamPage(items=items, total=total, page=page, page_size=page_size)

    def _members(self, *, reasons: bool) -> Select[Any]:
        # Select named safe columns only, never Person/UserAccount ORM entities.
        return (
            select(
                TeamMembership.id,
                TeamMembership.person_id,
                Person.full_name,
                Person.nickname,
                Person.date_of_birth,
                ProfessionalTeam.project_id,
                GameProject.name.label("project_name"),
                ProfessionalTeam.id.label("team_id"),
                ProfessionalTeam.name.label("team_name"),
                TeamMembership.joined_on,
                TeamMembership.left_on,
                TeamMembership.end_kind,
                case((Person.deleted_at.is_not(None), "deleted"), else_=UserAccount.status).label(
                    "account_status"
                ),
                (TeamMembership.transfer_reason if reasons else literal(None)).label(
                    "transfer_reason"
                ),
            )
            .select_from(TeamMembership)
            .join(Person, Person.id == TeamMembership.person_id)
        )

    def _member_query(self, *, reasons: bool) -> Select[Any]:
        return (
            self._members(reasons=reasons)
            .outerjoin(UserAccount, UserAccount.person_id == Person.id)
            .join(ProfessionalTeam, ProfessionalTeam.id == TeamMembership.team_id)
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
        )

    def _member_page(
        self, query: Select[Any], page: int, page_size: int
    ) -> TeamPage[TeamMemberView]:
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.execute(
            query.order_by(
                TeamMembership.joined_on.desc(),
                TeamMembership.created_at.desc(),
                TeamMembership.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        today = business_today()
        items = []
        for row in rows:
            data = dict(row)
            age = _age(data.pop("date_of_birth"), today)
            data["account_status"] = data["account_status"] or "deleted"
            items.append(
                TeamMemberView(
                    **data,
                    age=age,
                    is_minor=age < 18,
                    role="team_member",
                    status="current" if data["left_on"] is None else "left",
                )
            )
        return TeamPage(items=items, total=total, page=page, page_size=page_size)

    def list_memberships(
        self,
        actor: AuthContext,
        team_id: UUID,
        *,
        status: Literal["current", "left", "all"],
        page: int,
        page_size: int,
    ) -> TeamPage[TeamMemberView]:
        _pagination(page, page_size)
        self._require_team(actor, team_id, "read")
        is_admin = bool(
            self.db.scalar(
                select(UserRole.user_id)
                .join(Role)
                .where(UserRole.user_id == actor.account.id, Role.key == "super_admin")
            )
        )
        query = self._member_query(reasons=is_admin).where(TeamMembership.team_id == team_id)
        if status == "current":
            query = query.where(TeamMembership.ended_at.is_(None))
        elif status == "left":
            query = query.where(TeamMembership.ended_at.is_not(None))
        elif status != "all":
            raise _invalid("名单状态须为在队、已离队或全部")
        return self._member_page(query, page, page_size)

    def my_team(self, actor: AuthContext, *, page: int, page_size: int) -> MyTeamResponse:
        _pagination(page, page_size)
        person_id = self.access.require_self(actor)
        projection = project_person_teams(self.db, actor, [person_id])[person_id]
        if projection.visibility == "denied":
            raise TeamAccessDenied()
        current = projection.current
        query = self._member_query(reasons=True).where(
            TeamMembership.person_id == person_id,
            TeamMembership.ended_at.is_not(None),
            self.access.self_history_filter(actor),
        )
        return MyTeamResponse(current=current, history=self._member_page(query, page, page_size))

    def list_assignments(
        self, actor: AuthContext, team_id: UUID, *, page: int, page_size: int
    ) -> TeamPage[TeamAssignmentView]:
        _pagination(page, page_size)
        self._require_team(actor, team_id, "read")
        query = (
            select(
                TeamManagerAssignment.id,
                TeamManagerAssignment.account_id,
                Person.full_name,
                Person.nickname,
                UserAccount.status.label("account_status"),
                TeamManagerAssignment.created_at,
            )
            .select_from(TeamManagerAssignment)
            .join(UserAccount, UserAccount.id == TeamManagerAssignment.account_id)
            .join(
                Person,
                Person.id == UserAccount.person_id,
            )
            .where(
                TeamManagerAssignment.team_id == team_id, TeamManagerAssignment.ended_at.is_(None)
            )
        )
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = []
        for row in self.db.execute(
            query.order_by(TeamManagerAssignment.id).offset((page - 1) * page_size).limit(page_size)
        ).mappings():
            data = dict(row)
            data["started_on"] = business_date_at(data.pop("created_at"))
            items.append(TeamAssignmentView(**data, ended_on=None))
        return TeamPage(items=items, total=total, page=page, page_size=page_size)

    def candidates(
        self,
        actor: AuthContext,
        team_id: UUID,
        *,
        kind: Literal["member", "manager"],
        query: str,
        cursor: str | None,
        page_size: int = 20,
    ) -> TeamCandidatePage:
        normalized = normalize_name_query(query)
        if not normalized or len(query) > 120 or not 1 <= page_size <= 20:
            raise _invalid("请输入姓名、昵称或首字母，每页数量须为 1 至 20")
        if kind not in {"member", "manager"}:
            raise _invalid("候选类型无效")
        self._require_team(actor, team_id, "manage" if kind == "member" else "assign_managers")
        position = None
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
                if decoded[:3] != [str(team_id), kind, normalized] or len(decoded) != 4:
                    raise ValueError
                position = UUID(decoded[3])
            except (ValueError, TypeError, UnicodeError, IndexError) as exc:
                raise _invalid("分页游标无效，请重新搜索") from exc
        eligible_role = exists(
            select(UserRole.user_id)
            .join(Role)
            .where(
                UserRole.user_id == UserAccount.id,
                Role.key == f"team_{kind}",
            )
        )
        statement = (
            select(
                UserAccount.id.label("account_id"),
                Person.id.label("person_id"),
                Person.full_name,
                Person.nickname,
                Person.date_of_birth,
            )
            .select_from(Person)
            .join(
                UserAccount,
                UserAccount.person_id == Person.id,
            )
            .where(
                Person.deleted_at.is_(None),
                UserAccount.status == "active",
                UserAccount.force_password_change.is_(False),
                eligible_role,
            )
        )
        if kind == "member":
            statement = statement.where(
                ~exists(
                    select(TeamMembership.id).where(
                        TeamMembership.person_id == Person.id,
                        TeamMembership.ended_at.is_(None),
                    )
                )
            )
        initials = is_name_initial_query(normalized)
        if not initials:
            escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(
                or_(
                    Person.full_name.ilike(f"%{escaped}%", escape="\\"),
                    Person.nickname.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        items: list[TeamCandidate] = []
        today = business_today()
        last_match = None
        while True:
            batch = statement.order_by(Person.id).limit(200)
            if position is not None:
                batch = batch.where(Person.id > position)
            rows = list(self.db.execute(batch).mappings())
            for row in rows:
                position = row["person_id"]
                if initials and not any(
                    name_matches_query(value, normalized)
                    for value in (row["full_name"], row["nickname"] or "")
                ):
                    continue
                if len(items) == page_size:
                    token = base64.urlsafe_b64encode(
                        json.dumps(
                            [str(team_id), kind, normalized, str(last_match)],
                            separators=(",", ":"),
                            ensure_ascii=True,
                        ).encode("ascii")
                    ).decode("ascii")
                    return TeamCandidatePage(items=items, next_cursor=token)
                data = dict(row)
                age = _age(data.pop("date_of_birth"), today)
                items.append(TeamCandidate(**data, age=age, is_minor=age < 18))
                last_match = position
            if len(rows) < 200:
                return TeamCandidatePage(items=items, next_cursor=None)
