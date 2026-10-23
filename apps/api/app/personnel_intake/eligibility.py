"""Approved assisted facts eligibility; never grants family/guardian authority."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from hmac import compare_digest
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity.models import GuardianRelationship, Person
from app.identity.readiness import is_minor
from app.personnel_intake.guardian_records import (
    ApprovalEvidenceError,
    authenticated_approval_facts,
    guardian_facts,
    person_facts,
)
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelIntakeRequest,
    PersonnelIntakeRevision,
)
from app.personnel_intake.schemas import IntakePayloadView, PersonKind
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest


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
        """Bounded batch, including immutable account-origin membership in current lineage."""
        ids = set(person_ids)
        if not ids:
            return {}
        profiles = {
            p.person_id: p
            for p in self.db.scalars(
                select(AssistedPersonProfile)
                .where(AssistedPersonProfile.person_id.in_(ids))
                .execution_options(populate_existing=True)
            )
        }
        approvals = {
            a.id: a
            for a in self.db.scalars(
                select(AssistedApproval)
                .where(AssistedApproval.person_id.in_(ids))
                .execution_options(populate_existing=True)
            )
        }
        request_ids = {a.request_id for a in approvals.values()}
        requests = {
            r.id: r
            for r in self.db.scalars(
                select(PersonnelIntakeRequest)
                .where(PersonnelIntakeRequest.id.in_(request_ids))
                .execution_options(populate_existing=True)
            )
        }
        revisions: dict[tuple[UUID, int], PersonKind] = {}
        for revision in self.db.scalars(
            select(PersonnelIntakeRevision)
            .where(PersonnelIntakeRevision.request_id.in_(request_ids))
            .execution_options(populate_existing=True)
        ):
            try:
                raw = self.cipher.decrypt(
                    revision.payload_ciphertext, field_name="personnel_intake_payload"
                )
                if not compare_digest(
                    self.lookup.digest_domain("personnel-intake-payload", raw),
                    revision.payload_digest,
                ):
                    raise ApprovalEvidenceError()
                revisions[(revision.request_id, revision.version)] = (
                    IntakePayloadView.model_validate_json(raw).kind
                )
            except (EncryptedFieldError, ValidationError) as exc:
                raise ApprovalEvidenceError() from exc
        edge_ids = {edge for a in approvals.values() for edge in a.guardian_relationship_ids}
        edges = {
            e.id: e
            for e in self.db.scalars(
                select(GuardianRelationship)
                .where(GuardianRelationship.id.in_(edge_ids))
                .execution_options(populate_existing=True)
            )
        }
        people = {
            p.id: p
            for p in self.db.scalars(
                select(Person)
                .where(Person.id.in_(ids | {e.guardian_person_id for e in edges.values()}))
                .execution_options(populate_existing=True)
            )
        }
        facts = {
            a.id: authenticated_approval_facts(a, cipher=self.cipher, lookup=self.lookup)
            for a in approvals.values()
        }
        output: dict[UUID, AssistedEligibility] = {}
        for person_id in ids:
            profile, person = profiles.get(person_id), people.get(person_id)
            current = approvals.get(profile.current_approval_id) if profile else None
            request = requests.get(current.request_id) if current else None
            kind = revisions.get((current.request_id, current.revision_version)) if current else None
            missing: tuple[str, ...] = ()
            lineage: set[UUID] = set()
            cursor = current
            valid_lineage = current is not None
            while cursor is not None:
                row = requests.get(cursor.request_id)
                if (
                    cursor.id in lineage
                    or cursor.person_id != person_id
                    or row is None
                    or row.person_id != person_id
                    or row.state != "approved"
                    or row.kind != kind
                    or revisions.get((cursor.request_id, cursor.revision_version)) != kind
                    or cursor.revision_version > row.version
                    or cursor.guardian_relationship_version != cursor.revision_version
                ):
                    valid_lineage = False
                    break
                lineage.add(cursor.id)
                previous = (
                    approvals.get(cursor.supersedes_approval_id)
                    if cursor.supersedes_approval_id
                    else None
                )
                if cursor.supersedes_approval_id and (
                    previous is None
                    or previous.request_id != cursor.request_id
                    or previous.revision_version >= cursor.revision_version
                ):
                    valid_lineage = False
                    break
                cursor = previous
            approved = bool(
                profile
                and current
                and request
                and kind
                and valid_lineage
                and current.revision_version == request.version
            )
            if not approved:
                missing = ("assisted_approval",)
            elif person_id in original_approvals and original_approvals[person_id] not in lineage:
                missing = ("assisted_provenance",)
            elif person is None or person.deleted_at is not None:
                missing = ("assisted_profile",)
            else:
                assert current is not None
                stored_person, stored_guardians = facts[current.id]
                try:
                    if person_facts(person, self.cipher) != stored_person:
                        missing = ("assisted_profile",)
                    elif is_minor(person.date_of_birth, reference_date):
                        matching = False
                        for snapshot in stored_guardians:
                            edge = edges.get(UUID(str(snapshot["relationship_id"])))
                            guardian = people.get(edge.guardian_person_id) if edge else None
                            if (
                                edge is not None
                                and guardian is not None
                                and edge.student_person_id == person_id
                                and guardian.deleted_at is None
                                and edge.ended_at is None
                                and edge.valid_from <= reference_date
                                and (edge.valid_until is None or edge.valid_until >= reference_date)
                                and not is_minor(guardian.date_of_birth, reference_date)
                                and guardian_facts(edge, guardian, self.cipher) == snapshot
                            ):
                                matching = True
                        if not matching:
                            missing = ("guardian",)
                except EncryptedFieldError as exc:
                    raise ApprovalEvidenceError() from exc
            output[person_id] = AssistedEligibility(
                approved,
                approved and not missing,
                kind,
                current.id if current else None,
                profile.version if profile else None,
                missing,
            )
        return output
