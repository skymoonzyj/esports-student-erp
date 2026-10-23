from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.guardian_records import conflict
from app.personnel_intake.models import AssistedPersonProfile, CoachPersonResponsibility
from app.teaching.coach_eligibility import CoachEligibility
from app.team_management.write_support import lock_participants


def replace_in_transaction(
    db: Session, actor: AuthContext, person_id: UUID, coach_person_ids: list[UUID], now: datetime
) -> None:
    desired = set(coach_person_ids)
    if person_id in desired or any(
        CoachEligibility(db).ineligibility_reason(coach) for coach in desired
    ):
        raise conflict()
    current = list(
        db.scalars(
            select(CoachPersonResponsibility).where(
                CoachPersonResponsibility.person_id == person_id,
                CoachPersonResponsibility.ended_at.is_(None),
            )
        )
    )
    for row in current:
        if row.coach_person_id not in desired:
            row.ended_at, row.ended_by_user_id = now, actor.account.id
    for coach in sorted(desired - {row.coach_person_id for row in current}):
        db.add(
            CoachPersonResponsibility(
                person_id=person_id,
                coach_person_id=coach,
                created_by_user_id=actor.account.id,
                created_at=now,
            )
        )
    db.flush()


class CoachResponsibilityService:
    def __init__(self, db: Session):
        self.db = db

    def replace(
        self,
        actor: AuthContext,
        person_id: UUID,
        coach_person_ids: list[UUID],
        expected_version: int,
        reason: str,
    ) -> None:
        access = PersonnelIntakeAccess(self.db)

        def authorize() -> None:
            roles, _ = access.actor_state(actor)
            if not roles & {"admin", "super_admin"} or not access.allowed(
                actor, "people.update", target_person_id=person_id
            ):
                raise PersonnelIntakeError(
                    "personnel_intake_forbidden", status=403, detail="无权维护负责人"
                )

        with atomic_write_transaction(self.db):
            authorize()
            reason = reason.strip()
            if not reason or len(reason) > 200:
                raise PersonnelIntakeError(
                    "validation_error", status=422, detail="变更原因须为1至200字"
                )
            try:
                AuditService.validate_purpose(reason)
            except AuditMetadataRejected as exc:
                raise PersonnelIntakeError(
                    "validation_error",
                    status=422,
                    detail="变更原因不能包含身份证、联系方式或其他敏感内容",
                ) from exc
            old_ids = set(
                self.db.scalars(
                    select(CoachPersonResponsibility.coach_person_id).where(
                        CoachPersonResponsibility.person_id == person_id,
                        CoachPersonResponsibility.ended_at.is_(None),
                    )
                )
            )
            ids = {actor.person.id, person_id, *coach_person_ids, *old_ids}
            accounts = set(
                self.db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(ids)))
            )
            lock_participants(self.db, sorted(ids), sorted(accounts))
            authorize()
            profile = self.db.scalar(
                select(AssistedPersonProfile)
                .where(AssistedPersonProfile.person_id == person_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if profile is None or profile.version != expected_version:
                raise conflict()
            replace_in_transaction(self.db, actor, person_id, coach_person_ids, datetime.now(UTC))
            profile.version += 1
            AuditService(self.db).append(
                actor_user_id=actor.account.id,
                action="personnel_intake.coaches_replace",
                resource_type="person",
                resource_id=person_id,
                purpose=reason,
                outcome="allowed",
                request_id=str(uuid4()),
                metadata={"new_version": profile.version},
            )
