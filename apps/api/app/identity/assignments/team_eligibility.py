"""Only the new accountless player exception needs additional signed evidence.

Legacy approved-player and active-account eligibility remains owned by teams.
Missing encryption dependencies fail closed for independent business markers.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import business_today
from app.identity.assignments.models import PersonAssignmentState
from app.personnel_intake.approved_facts import load_approved_facts
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.security.encryption import EnvelopeCipher, LookupDigest


def trusted_business_player(
    db: Session,
    person_id: UUID,
    *,
    cipher: EnvelopeCipher | None,
    lookup: LookupDigest | None,
) -> bool:
    marked = db.scalar(
        select(PersonAssignmentState.is_player).where(PersonAssignmentState.person_id == person_id)
    )
    if not marked:
        return True  # The unchanged legacy SQL predicate remains authoritative.
    if cipher is None or lookup is None:
        return False
    try:
        fact = load_approved_facts(
            db, [person_id], reference_date=business_today(), cipher=cipher, lookup=lookup
        )[person_id]
    except ApprovalEvidenceError:
        return False
    return bool(
        fact.approval_valid
        and fact.profile_matches
        and fact.provenance_valid
        and fact.guardian_ready
        and not fact.has_account
    )
