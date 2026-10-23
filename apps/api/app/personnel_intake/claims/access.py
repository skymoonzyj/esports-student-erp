"""Live administrator capability and target-scope checks; no cached role authority."""

from uuid import UUID

from sqlalchemy import ColumnElement, and_, false, or_, select, true
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.personnel_intake.access import IntakeAccessSnapshot, PersonnelIntakeAccess
from app.personnel_intake.claims.errors import ProfileClaimError
from app.personnel_intake.claims.models import ProfileClaim
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelIntakeRequest,
)
from app.rbac.policy import PolicyRule


class ClaimReviewAccess:
    def __init__(self, db: Session):
        self.db = db
        self.intake = PersonnelIntakeAccess(db)

    def snapshot(self, actor: AuthContext) -> IntakeAccessSnapshot:
        return self.intake.snapshot(actor)

    @staticmethod
    def capable(state: IntakeAccessSnapshot) -> bool:
        return bool(
            state.person_id
            and state.roles & {"admin", "super_admin"}
            and state.decisions.decide("accounts.manage", {"global"}).allowed
        )

    def scope_predicate(self, state: IntakeAccessSnapshot) -> ColumnElement[bool]:
        if not self.capable(state):
            return false()
        request = PersonnelIntakeRequest

        def request_scope(rule: PolicyRule) -> ColumnElement[bool]:
            kind, identifier = rule.scope_kind, rule.scope_id
            if kind == "global":
                return true()
            if kind == "person":
                return (
                    ProfileClaim.target_person_id.is_not_distinct_from(identifier)
                    if identifier
                    else ProfileClaim.target_person_id.is_not(None)
                )
            if kind == "self":
                return ProfileClaim.target_person_id.is_not_distinct_from(state.person_id)
            if kind in {"assigned_class", "assigned_team"}:
                if kind == "assigned_class":
                    if identifier is not None:
                        return false()
                    condition = request.target_coach_person_ids.contains([state.person_id])
                else:
                    condition = (
                        request.target_team_id == identifier
                        if identifier
                        else request.target_team_id.is_not(None)
                    )
                return (
                    select(request.id)
                    .join(AssistedApproval, AssistedApproval.request_id == request.id)
                    .join(
                        AssistedPersonProfile,
                        AssistedPersonProfile.current_approval_id == AssistedApproval.id,
                    )
                    .where(
                        AssistedPersonProfile.person_id == ProfileClaim.target_person_id, condition
                    )
                    .exists()
                )
            return false()

        permission = "personnel_intake.review"
        grants = [
            request_scope(r)
            for r in [*state.decisions.role_rules, *state.decisions.overrides]
            if r.permission == permission and r.effect == "allow"
        ]
        denies = [
            request_scope(r)
            for r in state.decisions.overrides
            if r.permission == permission and r.effect == "deny"
        ]
        # NULL target only matches global rules. Other grants cannot expose an
        # unmatched applicant even when the administrator has a scoped queue.
        return and_(or_(*grants) if grants else false(), ~(or_(*denies) if denies else false()))

    def require_review(self, actor: AuthContext, person_id: UUID | None) -> None:
        state = self.snapshot(actor)
        if not self.capable(state):
            raise ProfileClaimError(
                status=403, code="profile_claim_forbidden", detail="无权审核认领申请"
            )
        request = (
            self.db.scalar(
                select(PersonnelIntakeRequest)
                .join(AssistedApproval, AssistedApproval.request_id == PersonnelIntakeRequest.id)
                .join(
                    AssistedPersonProfile,
                    AssistedPersonProfile.current_approval_id == AssistedApproval.id,
                )
                .where(AssistedPersonProfile.person_id == person_id)
            )
            if person_id
            else None
        )
        if not state.allowed(
            "personnel_intake.review",
            target_person_id=person_id,
            coaches=request.target_coach_person_ids if request else (),
            team_id=request.target_team_id if request else None,
        ):
            raise ProfileClaimError(
                status=403, code="profile_claim_forbidden", detail="无权审核认领申请"
            )

    def can_enter(self, actor: AuthContext) -> bool:
        state = self.snapshot(actor)
        return self.capable(state) and state.can_enter_review_queue()
