from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, false, not_, or_, select, true
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_standard_account
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.identity.models import Person
from app.rbac.service import RbacService
from app.talent_tests.access import TalentAccess


class ReactionxAccess:
    def __init__(self, db: Session, actor: AuthContext) -> None:
        self.db, self.actor = db, actor

    def _require(self, permission: str, person_id: UUID | None = None) -> None:
        require_current_standard_account(self.db, self.actor)
        rbac = RbacService(self.db)
        tokens = {"global"}
        if person_id is not None:
            tokens |= rbac.person_resource_tokens(
                actor_person_id=self.actor.person.id, target_person_id=person_id
            )
        decision = rbac.decision_context(self.actor.account.id)
        candidate_tokens = [tokens]
        if permission == "reactionx.conduct" and person_id is None:
            candidate_tokens.extend(
                {"global", f"person:{rule.scope_id}"}
                for rule in [*decision.role_rules, *decision.overrides]
                if rule.permission == permission
                and rule.scope_kind == "person"
                and rule.scope_id is not None
            )
        if not rbac.account_has_role(self.actor.account.id, "super_admin") or not any(
            decision.decide(permission, candidate).allowed for candidate in candidate_tokens
        ):
            raise ProblemError(
                status=403,
                code="reactionx_forbidden",
                detail="只有具备试测授权的有效超级管理员可以操作",
            )

    def require_manage(self) -> None:
        self._require("reactionx.manage")

    def require_conduct(self, person_id: UUID | None = None) -> None:
        self._require("reactionx.conduct", person_id)
        if (
            person_id is not None
            and self.db.scalar(
                select(Person.id).where(Person.id == person_id, Person.deleted_at.is_(None))
            )
            is None
        ):
            raise ProblemError(status=404, code="reactionx_person_not_found", detail="人员不存在")

    def require_report(self, person_id: UUID) -> None:
        require_current_standard_account(self.db, self.actor)
        TalentAccess(self.db, self.actor, datetime.now(UTC)).require_person(person_id)

    def conduct_person_ids(self) -> Select[tuple[UUID]]:
        self.require_conduct()
        context = RbacService(self.db).decision_context(self.actor.account.id)
        allows: list[ColumnElement[bool]] = []
        denies: list[ColumnElement[bool]] = []
        for rule in [*context.role_rules, *context.overrides]:
            if rule.permission != "reactionx.conduct":
                continue
            predicate = (
                true()
                if rule.scope_kind == "global" and rule.scope_id is None
                else Person.id == rule.scope_id
                if rule.scope_kind == "person" and rule.scope_id is not None
                else false()
            )
            (allows if rule.effect == "allow" else denies).append(predicate)
        return select(Person.id).where(
            Person.deleted_at.is_(None), and_(or_(false(), *allows), not_(or_(false(), *denies)))
        )
