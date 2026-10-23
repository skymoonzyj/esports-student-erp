"""Independent trusted approval, housing and activation-completion decisions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.personnel_intake.approved_facts import load_approved_facts
from app.personnel_intake.schemas import PersonKind
from app.security.encryption import EnvelopeCipher, LookupDigest


@dataclass(frozen=True)
class AssistedReadiness:
    approval_valid: bool
    housing_ready: bool
    completion_allowed: bool
    activation_profile_ready: bool
    approval_id: UUID | None
    version: int | None
    kind: PersonKind | None
    profile_missing: tuple[str, ...]
    housing_missing: tuple[str, ...]
    activation_missing: tuple[str, ...]


class AssistedReadinessPolicy:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        self.db, self.cipher, self.lookup = db, cipher, lookup

    def evaluate_many(
        self,
        person_ids: Sequence[UUID],
        *,
        reference_date: date,
        original_approvals: Mapping[UUID, UUID | None] | None = None,
    ) -> dict[UUID, AssistedReadiness]:
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
            base = (
                ("assisted_approval",)
                if not fact.approval_valid
                else (
                    ("assisted_provenance",)
                    if not fact.provenance_valid
                    else (
                        ("assisted_profile",) if not fact.profile_matches or person is None else ()
                    )
                )
            )
            pending = bool(person and person.intake_mode == "pending")
            profile = list(base)
            if person and not base:
                for field, value in (
                    ("phone", person.phone_ciphertext),
                    ("date_of_birth", person.date_of_birth),
                    ("sex", person.sex),
                    (
                        "document_issuing_country",
                        person.document_type != "passport" or person.document_issuing_country,
                    ),
                ):
                    if not value:
                        profile.append(field)
                if not fact.guardian_ready:
                    profile.append("guardian" if person.date_of_birth else "guardian_age")
            housing = list(base)
            if person and not base:
                if not person.sex:
                    housing.append("sex")
                if not pending and not fact.guardian_ready:
                    housing.append("guardian")
            activation = list(profile)
            if person and not person.email_ciphertext:
                activation.append("email")
            valid = fact.approval_valid and fact.provenance_valid and fact.profile_matches
            output[identifier] = AssistedReadiness(
                fact.approval_valid,
                not housing,
                valid and pending and not fact.has_account,
                valid and not activation,
                fact.approval_id,
                fact.version,
                fact.kind,
                tuple(profile),
                tuple(housing),
                tuple(activation),
            )
        return output
