"""Claim reservation expiry; caller holds this email's shared credential lock.

Does not acquire person, approval or another credential lock, and never touches
approved bindings. Evidence rows are retained for audit and historical proof.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.personnel_intake.claims.models import ProfileClaim
from app.personnel_intake.models import PersonnelCredentialReservation


def expire_claim_reservations_in_transaction(
    db: Session, *, email_digest: str, now: datetime
) -> None:
    expired = list(
        db.scalars(
            select(ProfileClaim)
            .where(
                ProfileClaim.email_digest == email_digest,
                ProfileClaim.status == "pending",
                ProfileClaim.expires_at <= now,
            )
            .order_by(ProfileClaim.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if not expired:
        return
    db.execute(
        update(PersonnelCredentialReservation)
        .where(
            PersonnelCredentialReservation.profile_claim_id.in_([row.id for row in expired]),
            PersonnelCredentialReservation.kind == "email",
            PersonnelCredentialReservation.digest == email_digest,
            PersonnelCredentialReservation.ended_at.is_(None),
        )
        .values(ended_at=now)
    )
    for row in expired:
        row.status = "expired"
        row.version += 1
        AuditService(db).append(
            actor_user_id=None,
            action="profile_claim.expire",
            resource_type="profile_claim",
            resource_id=row.id,
            purpose=None,
            outcome="allowed",
            request_id=str(uuid4()),
            metadata={"new_version": row.version},
        )
    db.flush()
