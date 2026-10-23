from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, case, exists, literal, or_, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person, StudentProfile
from app.rbac.service import RbacDecisionContext, RbacService
from app.referrals.errors import InvitationSourceNotFound, InvitedHousingPermissionDenied
from app.referrals.schemas import InvitationSource, SourceKind
from app.revenue_share.models import (
    ReferralCode,
    RevenueShareOrganization,
    RevenueShareOrganizationManagerAssignment,
    StudentReferralBinding,
)

PERMISSION = "referrals.read_invited_housing"


class InvitedHousingAccess:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _require_actor(
        self, user_id: UUID, actor_person_id: UUID, as_of: datetime
    ) -> tuple[str, RbacDecisionContext]:
        name = self.db.scalar(
            select(Person.full_name)
            .join(UserAccount, UserAccount.person_id == Person.id)
            .where(
                UserAccount.id == user_id,
                UserAccount.person_id == actor_person_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        )
        if name is None:
            raise InvitedHousingPermissionDenied()
        decisions = RbacService(self.db).decision_context(user_id, occurred_at=as_of)
        if not decisions.decide(PERMISSION, {"global", f"self:{actor_person_id}"}).allowed:
            raise InvitedHousingPermissionDenied()
        return name, decisions

    def _sources(self, user_id: UUID, actor_person_id: UUID, name: str) -> list[InvitationSource]:
        personal = self.db.scalar(
            select(
                or_(
                    exists().where(
                        ReferralCode.source_kind == "person",
                        ReferralCode.referrer_person_id == actor_person_id,
                    ),
                    exists().where(
                        StudentReferralBinding.source_kind == "person",
                        StudentReferralBinding.referrer_person_id == actor_person_id,
                    ),
                )
            )
        )
        sources = (
            [InvitationSource(source_kind="person", organization_id=None, name=name)]
            if personal
            else []
        )
        organizations = self.db.execute(
            select(RevenueShareOrganization.id, RevenueShareOrganization.name)
            .where(
                RevenueShareOrganization.is_active.is_(True),
                exists().where(
                    RevenueShareOrganizationManagerAssignment.organization_id
                    == RevenueShareOrganization.id,
                    RevenueShareOrganizationManagerAssignment.account_id == user_id,
                    RevenueShareOrganizationManagerAssignment.revoked_at.is_(None),
                ),
            )
            .order_by(RevenueShareOrganization.name, RevenueShareOrganization.id)
        )
        sources.extend(
            InvitationSource(source_kind="organization", organization_id=org_id, name=org_name)
            for org_id, org_name in organizations
        )
        return sources

    def has_sources(self, *, user_id: UUID, actor_person_id: UUID, as_of: datetime) -> bool:
        try:
            return bool(
                self.list_sources(user_id=user_id, actor_person_id=actor_person_id, as_of=as_of)
            )
        except InvitedHousingPermissionDenied:
            return False

    def list_sources(
        self, *, user_id: UUID, actor_person_id: UUID, as_of: datetime
    ) -> list[InvitationSource]:
        name, _ = self._require_actor(user_id, actor_person_id, as_of)
        return self._sources(user_id, actor_person_id, name)

    def authorized_people_statement(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        source_kind: SourceKind,
        organization_id: UUID | None,
        as_of: datetime,
    ) -> Select[Any]:
        if (source_kind == "organization") != (organization_id is not None):
            from app.core.errors import ProblemError

            raise ProblemError(status=422, code="validation_error", detail="邀请来源参数无效")
        name, decisions = self._require_actor(user_id, actor_person_id, as_of)
        sources = self._sources(user_id, actor_person_id, name)
        if (
            not sources
            or (
                source_kind == "organization"
                and not any(s.organization_id == organization_id for s in sources)
            )
            or (source_kind == "person" and not any(s.source_kind == "person" for s in sources))
        ):
            raise InvitationSourceNotFound()
        binding = StudentReferralBinding
        own = and_(binding.source_kind == "person", binding.referrer_person_id == actor_person_id)
        managed = and_(
            binding.source_kind == "organization",
            exists()
            .where(
                RevenueShareOrganization.id == binding.referrer_organization_id,
                RevenueShareOrganization.is_active.is_(True),
                exists().where(
                    RevenueShareOrganizationManagerAssignment.organization_id
                    == RevenueShareOrganization.id,
                    RevenueShareOrganizationManagerAssignment.account_id == user_id,
                    RevenueShareOrganizationManagerAssignment.revoked_at.is_(None),
                ),
            )
            .correlate(binding),
        )
        relation = (
            own
            if source_kind == "person"
            else and_(managed, binding.referrer_organization_id == organization_id)
            if source_kind == "organization"
            else or_(own, managed)
        )
        statement = (
            select(
                binding.student_person_id,
                binding.bound_at,
                binding.source_kind,
                binding.referrer_organization_id.label("organization_id"),
                Person.full_name,
                Person.nickname,
                case(
                    (binding.source_kind == "person", literal(name)),
                    else_=RevenueShareOrganization.name,
                ).label("source_name"),
            )
            .join(Person, Person.id == binding.student_person_id)
            .join(StudentProfile, StudentProfile.person_id == Person.id)
            .outerjoin(
                RevenueShareOrganization,
                RevenueShareOrganization.id == binding.referrer_organization_id,
            )
            .where(relation, Person.deleted_at.is_(None))
        )
        denied = {
            rule.scope_id
            for rule in decisions.overrides
            if rule.permission == PERMISSION
            and rule.effect == "deny"
            and rule.scope_kind == "person"
        }
        if None in denied:
            statement = statement.where(literal(False))
        elif denied:
            statement = statement.where(binding.student_person_id.not_in(denied))
        return statement
