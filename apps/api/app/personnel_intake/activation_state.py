from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.personnel_intake.activation_models import AssistedActivationSession
from app.registration.models import RegistrationVerificationChallenge


def invalidate_in_transaction(db: Session, *, person_id: UUID, occurred_at: datetime) -> None:
    """Caller owns person/account locks; close outstanding proofs, retain history."""
    sessions = list(
        db.scalars(
            select(AssistedActivationSession)
            .where(
                AssistedActivationSession.person_id == person_id,
                AssistedActivationSession.state != "completed",
            )
            .order_by(AssistedActivationSession.id)
            .with_for_update()
        )
    )
    challenge_ids = [row.challenge_id for row in sessions]
    for challenge in db.scalars(
        select(RegistrationVerificationChallenge)
        .where(RegistrationVerificationChallenge.id.in_(challenge_ids))
        .order_by(RegistrationVerificationChallenge.id)
        .with_for_update()
    ):
        if challenge.consumed_at is None:
            challenge.consumed_at = occurred_at
    for row in sessions:
        row.expires_at = min(
            row.expires_at, max(occurred_at, row.created_at + timedelta(microseconds=1))
        )
    db.flush()
