"""Safe combined personnel/account roster; SQL deduplicates before pagination."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import case, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.identity.models import Person
from app.identity.name_search import is_name_initial_query, name_matches_query, normalize_name_query
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import AssistedPersonProfile
from app.personnel_intake.role_grant_models import ActivationRolePlan
from app.personnel_intake.role_grants import ActivationRoleService
from app.rbac.models import Role, UserRole
from app.rbac.prospective_batch import ProspectiveRoleBatch, ProspectiveRoleCheck
from app.security.encryption import LookupDigest
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.access import TeamAccessService
from app.team_management.models import TeamStaffAssignment
from app.team_management.person_staff_models import (
    PersonTeamStaffAuthorization,
    PersonTeamStaffPosition,
)
from app.team_management.person_staff_schemas import (
    PersonStaffCandidate,
    PersonStaffCandidatePage,
    PersonStaffRow,
    StaffRole,
    StaffTeamOption,
    StaffTeamOptions,
)
from app.team_management.person_staff_service import authentic_authorization, require_staff_manager
from app.team_management.query_service import _invalid, _pagination
from app.team_management.schemas import TeamPage
from app.team_management.staff_service import STAFF_ROLE_KEYS
from app.team_management.write_support import require_active_team


@dataclass(frozen=True)
class PersonStaffState:
    authorization_status: Literal["valid", "reconfirmation_required", "pending_role_approval"]
    can_reconfirm: bool


def personnel_staff_states(
    db: Session, *, lookup: LookupDigest, positions: list[PersonTeamStaffPosition]
) -> dict[UUID, PersonStaffState]:
    """Bounded SQL batch, including all grantors and current role qualifications."""
    if not positions:
        return {}
    pids = {p.person_id for p in positions}
    proofs = {
        p.position_id: p
        for p in db.scalars(
            select(PersonTeamStaffAuthorization)
            .where(PersonTeamStaffAuthorization.position_id.in_([p.id for p in positions]))
            .distinct(PersonTeamStaffAuthorization.position_id)
            .order_by(
                PersonTeamStaffAuthorization.position_id,
                PersonTeamStaffAuthorization.version.desc(),
            )
        )
    }
    latest = {
        p.person_id: p
        for p in db.scalars(
            select(ActivationRolePlan)
            .where(ActivationRolePlan.person_id.in_(pids))
            .distinct(ActivationRolePlan.person_id)
            .order_by(ActivationRolePlan.person_id, ActivationRolePlan.version.desc())
        )
    }
    plans = {
        p.id: p
        for p in db.scalars(
            select(ActivationRolePlan).where(
                ActivationRolePlan.id.in_(
                    {p.role_plan_id for p in proofs.values() if p.role_plan_id}
                )
            )
        )
    }
    plans.update({p.id: p for p in latest.values()})
    checks = {
        ProspectiveRoleCheck(p.authorizer_user_id, p.person_id, key)
        for p in plans.values()
        for key in set(p.role_keys) & set(STAFF_ROLE_KEYS.values())
    }
    checks.update(
        ProspectiveRoleCheck(
            p.authorizer_user_id, position.person_id, STAFF_ROLE_KEYS[position.staff_role]
        )
        for position in positions
        if (p := proofs.get(position.id)) is not None
    )
    authority = ProspectiveRoleBatch(db, checks)
    role_service = ActivationRoleService(db, lookup=lookup)
    authenticated = set()
    for plan in plans.values():
        try:
            role_service.authenticate(plan)
            authenticated.add(plan.id)
        except PersonnelIntakeError:
            pass
    accounts = {
        r.person_id: r
        for r in db.execute(
            select(
                UserAccount.id,
                UserAccount.person_id,
                UserAccount.status,
                UserAccount.force_password_change,
                Person.deleted_at,
            )
            .join(Person, Person.id == UserAccount.person_id)
            .where(UserAccount.person_id.in_(pids))
        )
    }
    deleted = set(
        db.scalars(select(Person.id).where(Person.id.in_(pids), Person.deleted_at.is_not(None)))
    )
    role_keys: dict[UUID, set[str]] = {}
    for uid, key in db.execute(
        select(UserRole.user_id, Role.key)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_([a.id for a in accounts.values()]))
    ):
        role_keys.setdefault(uid, set()).add(key)
    assignments = {
        a.id: a
        for a in db.scalars(
            select(TeamStaffAssignment).where(
                TeamStaffAssignment.id.in_(
                    [p.account_assignment_id for p in positions if p.account_assignment_id]
                )
            )
        )
    }
    active_teams = set(
        db.scalars(
            select(ProfessionalTeam.id)
            .join(GameProject)
            .where(
                ProfessionalTeam.id.in_({p.team_id for p in positions}),
                ProfessionalTeam.is_active.is_(True),
                GameProject.is_active.is_(True),
                GameProject.is_team_division.is_(True),
            )
        )
    )
    result = {}
    from app.personnel_intake.role_intents import RoleIntentService, pending_intents
    intents = pending_intents(db, pids)
    intent_service = RoleIntentService(db, lookup=lookup)
    for position in positions:
        proof, current = proofs.get(position.id), latest.get(position.person_id)
        original = plans.get(proof.role_plan_id) if proof and proof.role_plan_id else None
        key = STAFF_ROLE_KEYS[position.staff_role]
        team_active = position.team_id in active_teams and position.person_id not in deleted
        intact = authentic_authorization(lookup, position, proof)
        if team_active and intent_service.pending_position(intents.get(position.person_id), position, proof):
            result[position.id] = PersonStaffState("pending_role_approval", False)
            continue
        if position.account_assignment_id is not None:
            assignment, account = (
                assignments.get(position.account_assignment_id),
                accounts.get(position.person_id),
            )
            source = bool(
                account
                and assignment
                and account.id == assignment.account_id
                and assignment.team_id == position.team_id
                and assignment.staff_role == position.staff_role
                and assignment.ended_at is None
                and account.status == "active"
                and not account.force_password_change
                and account.deleted_at is None
                and key in role_keys.get(account.id, set())
            )
            valid = bool(team_active and source and intact)
            reconfirm = bool(team_active and source)
        else:
            latest_intact = bool(
                current and current.id in authenticated and key in current.role_keys
            )
            original_intact = bool(
                original
                and original.id in authenticated
                and original.person_id == position.person_id
                and key in original.role_keys
                and authority.allowed(
                    ProspectiveRoleCheck(original.authorizer_user_id, position.person_id, key)
                )
            )
            valid = bool(
                team_active
                and position.person_id not in accounts
                and intact
                and latest_intact
                and original_intact
                and proof
                and authority.can_assign_team(proof.authorizer_user_id, position.team_id)
            )
            reconfirm = bool(
                team_active
                and position.person_id not in accounts
                and latest_intact
                and current
                and authority.allowed(
                    ProspectiveRoleCheck(current.authorizer_user_id, position.person_id, key)
                )
            )
        result[position.id] = PersonStaffState(
            "valid" if valid else "reconfirmation_required", reconfirm
        )
    return result


class PersonStaffQuery:
    def __init__(self, db: Session, *, lookup: LookupDigest) -> None:
        self.db, self.lookup = db, lookup

    def list(
        self,
        actor: AuthContext,
        team_id: UUID,
        *,
        status: Literal["current", "ended"],
        page: int,
        page_size: int,
    ) -> TeamPage[PersonStaffRow]:
        _pagination(page, page_size)
        require_staff_manager(self.db, actor, team_id)
        if status not in {"current", "ended"}:
            raise _invalid("工作人员状态无效")
        person = (
            select(
                PersonTeamStaffPosition.id.label("source_id"),
                literal("person").label("source_kind"),
                PersonTeamStaffPosition.id.label("person_position_id"),
                PersonTeamStaffPosition.account_assignment_id,
                Person.id.label("person_id"),
                UserAccount.id.label("account_id"),
                Person.full_name,
                Person.nickname,
                PersonTeamStaffPosition.staff_role,
                case(
                    (Person.deleted_at.is_not(None), "deleted"),
                    else_=func.coalesce(UserAccount.status, "not_activated"),
                ).label("account_status"),
                PersonTeamStaffPosition.started_at.label("started_at"),
                PersonTeamStaffPosition.ended_at,
                PersonTeamStaffPosition.version,
                literal(0).label("priority"),
                literal(False).label("account_valid"),
            )
            .select_from(PersonTeamStaffPosition)
            .join(Person, Person.id == PersonTeamStaffPosition.person_id)
        )
        person = person.outerjoin(UserAccount, UserAccount.person_id == Person.id).where(
            PersonTeamStaffPosition.team_id == team_id
        )
        role_exists = exists(
            select(UserRole.user_id)
            .join(Role)
            .where(
                UserRole.user_id == UserAccount.id,
                or_(
                    *(
                        (TeamStaffAssignment.staff_role == role) & (Role.key == key)
                        for role, key in STAFF_ROLE_KEYS.items()
                    )
                ),
            )
        )
        account = (
            select(
                TeamStaffAssignment.id.label("source_id"),
                literal("account").label("source_kind"),
                literal(None, type_=PersonTeamStaffPosition.id.type).label("person_position_id"),
                TeamStaffAssignment.id.label("account_assignment_id"),
                Person.id.label("person_id"),
                UserAccount.id.label("account_id"),
                Person.full_name,
                Person.nickname,
                TeamStaffAssignment.staff_role,
                case((Person.deleted_at.is_not(None), "deleted"), else_=UserAccount.status).label(
                    "account_status"
                ),
                TeamStaffAssignment.created_at.label("started_at"),
                TeamStaffAssignment.ended_at,
                literal(None, type_=PersonTeamStaffPosition.version.type).label("version"),
                literal(1).label("priority"),
                (
                    role_exists
                    & (UserAccount.status == "active")
                    & ~UserAccount.force_password_change
                    & Person.deleted_at.is_(None)
                    & ProfessionalTeam.is_active
                    & GameProject.is_active
                    & GameProject.is_team_division
                ).label("account_valid"),
            )
            .select_from(TeamStaffAssignment)
            .join(UserAccount, UserAccount.id == TeamStaffAssignment.account_id)
        )
        account = (
            account.join(Person, Person.id == UserAccount.person_id)
            .join(ProfessionalTeam, ProfessionalTeam.id == TeamStaffAssignment.team_id)
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
            .where(TeamStaffAssignment.team_id == team_id)
        )
        if status == "current":
            person = person.where(PersonTeamStaffPosition.ended_at.is_(None))
            account = account.where(TeamStaffAssignment.ended_at.is_(None))
        else:
            person = person.where(PersonTeamStaffPosition.ended_at.is_not(None))
            account = account.where(
                TeamStaffAssignment.ended_at.is_not(None),
                ~exists(
                    select(PersonTeamStaffPosition.id).where(
                        PersonTeamStaffPosition.account_assignment_id == TeamStaffAssignment.id
                    )
                ),
            )
        sources = union_all(person, account).subquery()
        if status == "current":
            ranked = select(
                *sources.c,
                func.row_number()
                .over(
                    partition_by=(sources.c.person_id, sources.c.staff_role),
                    order_by=(sources.c.priority, sources.c.started_at.desc(), sources.c.source_id),
                )
                .label("rank"),
            ).subquery()
            statement = select(*[ranked.c[c.name] for c in sources.c]).where(ranked.c.rank == 1)
        else:
            statement = select(sources)
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        filtered = statement.subquery()
        rows = list(
            self.db.execute(
                select(filtered)
                .order_by(filtered.c.started_at.desc(), filtered.c.source_id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).mappings()
        )
        positions = list(
            self.db.scalars(
                select(PersonTeamStaffPosition).where(
                    PersonTeamStaffPosition.id.in_(
                        [r["person_position_id"] for r in rows if r["person_position_id"]]
                    )
                )
            )
        )
        states = personnel_staff_states(self.db, lookup=self.lookup, positions=positions)
        items = []
        for row in rows:
            data = dict(row)
            sid, kind = data.pop("source_id"), data["source_kind"]
            data.pop("priority")
            account_valid = data.pop("account_valid")
            state = states.get(sid)
            started, ended = data.pop("started_at"), data.pop("ended_at")
            items.append(
                PersonStaffRow(
                    **data,
                    row_key=f"{kind}:{sid}",
                    started_on=business_date_at(started),
                    ended_on=business_date_at(ended) if ended else None,
                    authorization_status=state.authorization_status
                    if state
                    else "valid"
                    if account_valid
                    else "reconfirmation_required",
                    allowed_actions=(
                        []
                        if ended
                        else ["end", "reconfirm"]
                        if state and state.can_reconfirm
                        else ["end"]
                    ),
                )
            )
        return TeamPage(items=items, total=total, page=page, page_size=page_size)

    def candidates(
        self,
        actor: AuthContext,
        team_id: UUID,
        *,
        staff_role: StaffRole,
        query: str,
        cursor: str | None,
        page_size: int = 20,
    ) -> PersonStaffCandidatePage:
        require_staff_manager(self.db, actor, team_id)
        team = self.db.get(ProfessionalTeam, team_id)
        assert team is not None
        require_active_team(self.db, team)
        normalized = normalize_name_query(query)
        if (
            staff_role not in STAFF_ROLE_KEYS
            or not normalized
            or len(query) > 120
            or not 1 <= page_size <= 20
        ):
            raise _invalid("请输入姓名、昵称或首字母，每页数量须为 1 至 20")
        after = None
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
                if (
                    not isinstance(value, list)
                    or len(value) != 4
                    or value[:3] != [str(team_id), staff_role, normalized]
                ):
                    raise ValueError
                after = UUID(value[3])
            except (ValueError, TypeError, UnicodeError) as exc:
                raise _invalid("分页游标无效，请重新搜索") from exc
        plan = (
            select(
                ActivationRolePlan.person_id.label("pid"), ActivationRolePlan.id.label("plan_id")
            )
            .distinct(ActivationRolePlan.person_id)
            .order_by(ActivationRolePlan.person_id, ActivationRolePlan.version.desc())
            .subquery()
        )
        statement = (
            select(Person.id, Person.full_name, Person.nickname, ActivationRolePlan)
            .join(AssistedPersonProfile, AssistedPersonProfile.person_id == Person.id)
            .join(plan, plan.c.pid == Person.id)
            .join(ActivationRolePlan, ActivationRolePlan.id == plan.c.plan_id)
            .where(
                Person.deleted_at.is_(None),
                ActivationRolePlan.role_keys.contains([STAFF_ROLE_KEYS[staff_role]]),
                ~exists(select(UserAccount.id).where(UserAccount.person_id == Person.id)),
                ~exists(
                    select(PersonTeamStaffPosition.id).where(
                        PersonTeamStaffPosition.person_id == Person.id,
                        PersonTeamStaffPosition.team_id == team_id,
                        PersonTeamStaffPosition.staff_role == staff_role,
                        PersonTeamStaffPosition.ended_at.is_(None),
                    )
                ),
            )
        )
        if not is_name_initial_query(normalized):
            escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(
                or_(
                    Person.full_name.ilike(f"%{escaped}%", escape="\\"),
                    Person.nickname.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        roles = ActivationRoleService(self.db, lookup=self.lookup)
        matches = []
        while True:
            # Initial-letter/name and current authorization checks can reject
            # SQL candidates. Scan bounded UUID chunks, not a page-size LIMIT
            # which could hide eligible people behind rejected rows.
            chunk = statement.order_by(Person.id).limit(200)
            if after is not None:
                chunk = chunk.where(Person.id > after)
            rows = list(self.db.execute(chunk))
            if not rows:
                break
            batch = ProspectiveRoleBatch(
                self.db,
                [
                    ProspectiveRoleCheck(
                        r.ActivationRolePlan.authorizer_user_id, r.id, STAFF_ROLE_KEYS[staff_role]
                    )
                    for r in rows
                ],
            )
            for row in rows:
                if not any(
                    name_matches_query(v, normalized) for v in (row.full_name, row.nickname or "")
                ):
                    continue
                try:
                    roles.authenticate(row.ActivationRolePlan)
                except PersonnelIntakeError:
                    continue
                if batch.allowed(
                    ProspectiveRoleCheck(
                        row.ActivationRolePlan.authorizer_user_id,
                        row.id,
                        STAFF_ROLE_KEYS[staff_role],
                    )
                ):
                    matches.append(row)
                    if len(matches) > page_size:
                        break
            if len(matches) > page_size or len(rows) < 200:
                break
            after = rows[-1].id
        items = [
            PersonStaffCandidate(person_id=r.id, full_name=r.full_name, nickname=r.nickname)
            for r in matches[:page_size]
        ]
        token = (
            base64.urlsafe_b64encode(
                json.dumps(
                    [str(team_id), staff_role, normalized, str(items[-1].person_id)]
                ).encode()
            ).decode()
            if len(matches) > page_size
            else None
        )
        return PersonStaffCandidatePage(items=items, next_cursor=token)

    def staff_team_options(self, actor: AuthContext, *, for_intake: bool = False) -> StaffTeamOptions:
        access = TeamAccessService(self.db)
        if for_intake:
            from app.personnel_intake.access import PersonnelIntakeAccess
            from app.personnel_intake.role_intent_policy import require_direct_intake
            require_direct_intake(self.db, actor)
            state = PersonnelIntakeAccess(self.db).snapshot(actor)
            items = [StaffTeamOption(**row) for row in self.db.execute(
                select(ProfessionalTeam.id, ProfessionalTeam.project_id,
                    GameProject.name.label("project_name"), ProfessionalTeam.name)
                .join(GameProject).where(ProfessionalTeam.is_active.is_(True),
                    GameProject.is_active.is_(True), GameProject.is_team_division.is_(True))
                .order_by(GameProject.name, ProfessionalTeam.name, ProfessionalTeam.id)).mappings()
                if state.allowed("personnel_intake.review", team_id=row["id"])]
            return StaffTeamOptions(can_assign=True, reason=None, items=items)
        if not access._actor_roles(actor).intersection({"administration", "super_admin"}):
            return StaffTeamOptions(
                can_assign=False, reason="仅行政或超级管理员可分配工作人员队伍", items=[]
            )
        visible = access.visible_team_ids(actor, "assign_managers")
        items = [
            StaffTeamOption(**row)
            for row in self.db.execute(
                select(
                    ProfessionalTeam.id,
                    ProfessionalTeam.project_id,
                    GameProject.name.label("project_name"),
                    ProfessionalTeam.name,
                )
                .join(GameProject)
                .where(
                    ProfessionalTeam.id.in_(visible),
                    ProfessionalTeam.is_active.is_(True),
                    GameProject.is_active.is_(True),
                    GameProject.is_team_division.is_(True),
                )
                .order_by(GameProject.name, ProfessionalTeam.name, ProfessionalTeam.id)
            ).mappings()
        ]
        decision = access.rbac.decision_context(actor.account.id).decide(
            "team_manager_assignments.manage", {"global"}
        )
        allowed = bool(items or decision.allowed)
        return StaffTeamOptions(
            can_assign=allowed,
            reason=None if allowed else "当前无工作人员队伍分配权限",
            items=items,
        )
