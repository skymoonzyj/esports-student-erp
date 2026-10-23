from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.access import TeamAccessService
from app.team_management.errors import TeamServiceError
from app.team_management.models import TeamMembership
from app.team_management.schemas import PersonTeamProjection, TeamAffiliation


def current_membership_read_filter(db: Session, actor: AuthContext) -> ColumnElement[bool]:
    """A bounded SQL scope shared by personnel projections and counts.

    Self and roster grants are alternative entrances, but neither may bypass a
    deny on the actor's actual current relationship. Other roster rows keep the
    access service's existing team scope; no self token is attached to others.
    """
    access = TeamAccessService(db)
    own_team_id = db.scalar(
        select(TeamMembership.team_id).where(
            TeamMembership.person_id == actor.person.id,
            TeamMembership.ended_at.is_(None),
        )
    )
    tokens = {"global", f"self:{actor.person.id}"}
    if own_team_id is not None:
        tokens.add(f"assigned_team:{own_team_id}")
    own_allowed = (
        access.rbac.decision_context(actor.account.id)
        .decide(
            "team_memberships.read",
            tokens,
        )
        .allowed
    )
    own_id = None
    if own_allowed:
        try:
            own_id = access.require_self(actor)
        except TeamServiceError:
            pass
    scope = or_(
        TeamMembership.team_id.in_(access.visible_team_ids(actor, "read")),
        TeamMembership.person_id == own_id,
    )
    if not own_allowed:
        scope = scope & (TeamMembership.person_id != actor.person.id)
    return scope


def project_person_teams(
    db: Session,
    actor: AuthContext,
    person_ids: Sequence[UUID],
) -> dict[UUID, PersonTeamProjection]:
    ids = set(person_ids)
    result = {key: PersonTeamProjection(visibility="denied", current=None) for key in ids}
    if not ids:
        return result
    access = TeamAccessService(db)
    # Existence is only used internally to prevent hidden memberships being
    # mislabeled as unassigned. No names or other private columns are fetched.
    assigned = set(
        db.scalars(
            select(TeamMembership.person_id).where(
                TeamMembership.person_id.in_(ids),
                TeamMembership.ended_at.is_(None),
            )
        )
    )
    unassigned = access.readable_unassigned_person_ids(actor, list(ids - assigned))
    for person_id in unassigned:
        result[person_id] = PersonTeamProjection(visibility="allowed", current=None)
    rows = db.execute(
        select(
            TeamMembership.person_id,
            TeamMembership.id.label("membership_id"),
            ProfessionalTeam.project_id,
            GameProject.name.label("project_name"),
            ProfessionalTeam.id.label("team_id"),
            ProfessionalTeam.name.label("team_name"),
            TeamMembership.joined_on,
        )
        .select_from(TeamMembership)
        .join(ProfessionalTeam, ProfessionalTeam.id == TeamMembership.team_id)
        .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
        .where(
            TeamMembership.person_id.in_(ids),
            TeamMembership.ended_at.is_(None),
            current_membership_read_filter(db, actor),
        )
    )
    for row in rows.mappings():
        data = dict(row)
        person_id = data.pop("person_id")
        result[person_id] = PersonTeamProjection(
            visibility="allowed",
            current=TeamAffiliation(**data),
        )
    return result
