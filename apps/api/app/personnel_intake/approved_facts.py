"""Bounded authenticated approval graph shared by strict and pending policies."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from hmac import compare_digest
from typing import TypeVar
from uuid import UUID

from sqlalchemy import ColumnElement, ScalarResult, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person
from app.identity.readiness import is_minor
from app.personnel_intake.codec import decode_payload
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
from app.personnel_intake.schemas import PersonKind
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest

T = TypeVar("T")


@dataclass(frozen=True)
class ApprovedFacts:
    approval_valid: bool
    person: Person | None
    kind: PersonKind | None
    approval_id: UUID | None
    version: int | None
    profile_matches: bool
    guardian_ready: bool
    provenance_valid: bool
    has_account: bool


def load_approved_facts(
    db: Session,
    person_ids: Sequence[UUID],
    *,
    reference_date: date,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    original_approvals: Mapping[UUID, UUID | None] | None = None,
) -> dict[UUID, ApprovedFacts]:
    ids = set(person_ids)
    if not ids:
        return {}

    def rows(model: type[T], predicate: ColumnElement[bool]) -> ScalarResult[T]:
        return db.scalars(select(model).where(predicate).execution_options(populate_existing=True))

    profiles = {
        p.person_id: p
        for p in rows(AssistedPersonProfile, AssistedPersonProfile.person_id.in_(ids))
    }
    approvals = {a.id: a for a in rows(AssistedApproval, AssistedApproval.person_id.in_(ids))}
    request_ids = {a.request_id for a in approvals.values()}
    requests = {
        r.id: r for r in rows(PersonnelIntakeRequest, PersonnelIntakeRequest.id.in_(request_ids))
    }
    revisions: dict[tuple[UUID, int], PersonKind] = {}
    for revision in rows(
        PersonnelIntakeRevision, PersonnelIntakeRevision.request_id.in_(request_ids)
    ):
        try:
            raw = cipher.decrypt(revision.payload_ciphertext, field_name="personnel_intake_payload")
            if not compare_digest(
                lookup.digest_domain("personnel-intake-payload", raw), revision.payload_digest
            ):
                raise ApprovalEvidenceError()
            revisions[(revision.request_id, revision.version)] = decode_payload(raw).kind
        except (EncryptedFieldError, ValueError, TypeError) as exc:
            raise ApprovalEvidenceError() from exc
    edge_ids = {edge for a in approvals.values() for edge in a.guardian_relationship_ids}
    edges = {e.id: e for e in rows(GuardianRelationship, GuardianRelationship.id.in_(edge_ids))}
    people = {
        p.id: p
        for p in rows(Person, Person.id.in_(ids | {e.guardian_person_id for e in edges.values()}))
    }
    facts = {
        a.id: authenticated_approval_facts(a, cipher=cipher, lookup=lookup)
        for a in approvals.values()
    }
    accounts = set(db.scalars(select(UserAccount.person_id).where(UserAccount.person_id.in_(ids))))
    result = {}
    for person_id in ids:
        profile, person = profiles.get(person_id), people.get(person_id)
        current = approvals.get(profile.current_approval_id) if profile else None
        request = requests.get(current.request_id) if current else None
        kind = revisions.get((current.request_id, current.revision_version)) if current else None
        lineage: set[UUID] = set()
        cursor, valid = current, current is not None
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
                valid = False
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
                valid = False
                break
            cursor = previous
        approved = bool(
            profile
            and current
            and request
            and kind
            and valid
            and current.revision_version == request.version
        )
        provenance = (
            person_id not in (original_approvals or {})
            or (original_approvals or {})[person_id] in lineage
        )
        matches, guardian_ready = False, False
        if approved and current and person and person.deleted_at is None:
            stored_person, stored_guardians = facts[current.id]
            try:
                matches = person_facts(person, cipher) == stored_person
                if person.date_of_birth is not None:
                    guardian_ready = not is_minor(person.date_of_birth, reference_date)
                    for snapshot in stored_guardians:
                        edge = edges.get(UUID(str(snapshot["relationship_id"])))
                        guardian = people.get(edge.guardian_person_id) if edge else None
                        if (
                            edge is not None
                            and guardian is not None
                            and guardian.date_of_birth is not None
                            and edge.student_person_id == person_id
                            and guardian.deleted_at is None
                            and edge.ended_at is None
                            and edge.valid_from <= reference_date
                            and (edge.valid_until is None or edge.valid_until >= reference_date)
                            and not is_minor(guardian.date_of_birth, reference_date)
                            and guardian_facts(edge, guardian, cipher) == snapshot
                        ):
                            guardian_ready = True
            except EncryptedFieldError as exc:
                raise ApprovalEvidenceError() from exc
        result[person_id] = ApprovedFacts(
            approved,
            person,
            kind,
            current.id if current else None,
            profile.version if profile else None,
            matches,
            guardian_ready,
            provenance,
            person_id in accounts,
        )
    return result
