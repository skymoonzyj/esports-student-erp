"""Strict eligibility; pending housing never relaxes this policy."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.personnel_intake.approved_facts import load_approved_facts
from app.personnel_intake.schemas import PersonKind
from app.security.encryption import EnvelopeCipher, LookupDigest


@dataclass(frozen=True)
class AssistedEligibility:
    approved: bool
    eligible: bool
    kind: PersonKind | None
    approval_id: UUID | None
    version: int | None
    missing: tuple[str, ...]


class AssistedEligibilityPolicy:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        self.db, self.cipher, self.lookup = db, cipher, lookup

    def evaluate_many(
        self, person_ids: Sequence[UUID], *, reference_date: date
    ) -> dict[UUID, AssistedEligibility]:
        return self.evaluate_many_with_provenance(
            person_ids, reference_date=reference_date, original_approvals={}
        )

    def evaluate_many_with_provenance(
        self,
        person_ids: Sequence[UUID],
        *,
        reference_date: date,
        original_approvals: Mapping[UUID, UUID | None],
    ) -> dict[UUID, AssistedEligibility]:
        output = {}
        for identifier, fact in load_approved_facts(
            self.db,
            person_ids,
            reference_date=reference_date,
            cipher=self.cipher,
            lookup=self.lookup,
            original_approvals=original_approvals,
        ).items():
            person = fact.person
            missing: tuple[str, ...]
            if not fact.approval_valid:
                missing = ("assisted_approval",)
            elif not fact.provenance_valid:
                missing = ("assisted_provenance",)
            elif (
                not fact.profile_matches
                or person is None
                or person.intake_mode == "pending"
                and (
                    not person.phone_ciphertext
                    or not person.date_of_birth
                    or not person.sex
                    or (person.document_type == "passport" and not person.document_issuing_country)
                )
            ):
                missing = ("assisted_profile",)
            elif not fact.guardian_ready:
                missing = ("guardian",)
            else:
                missing = ()
            output[identifier] = AssistedEligibility(
                fact.approval_valid,
                fact.approval_valid and not missing,
                fact.kind,
                fact.approval_id,
                fact.version,
                missing,
            )
        return output
