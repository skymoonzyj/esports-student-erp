"""Already-locked guardian writes and canonical approval facts shared with eligibility.

Callers own the atomic transaction and acquire the complete credential, guardian
membership, person and account lock sets before calling any write here.
"""

import json
from collections.abc import Mapping
from datetime import date, datetime
from hmac import compare_digest
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import business_date_at
from app.identity.document_identity import (
    DocumentIdentityKeys,
    DocumentType,
    document_identity_keys,
    normalize_document_identity,
)
from app.identity.models import GuardianProfile, GuardianRelationship, Person
from app.identity.readiness import is_minor
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import AssistedApproval
from app.personnel_intake.schemas import (
    AssistedGuardianInput,
    AssistedGuardianView,
    IntakePayloadView,
    PendingIntakePayloadView,
)
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest

PERSON_SNAPSHOT_AAD = "assisted_person_snapshot"
GUARDIAN_SNAPSHOT_AAD = "assisted_guardian_snapshot"
PERSON_SNAPSHOT_DOMAIN = "assisted-person-snapshot"
GUARDIAN_SNAPSHOT_DOMAIN = "assisted-guardian-snapshot"


class ApprovalEvidenceError(PersonnelIntakeError):
    """Integrity/key/configuration failure, never ordinary business ineligibility."""

    def __init__(self) -> None:
        super().__init__("assisted_evidence_unavailable", status=503, detail="审核资料校验暂不可用")


class _PersonEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: UUID
    full_name: str
    identity_number: str
    identity_digest: str
    document_type: DocumentType = "cn_id"
    document_issuing_country: str | None = None
    passport_number_digest: str | None = None
    sex: str
    date_of_birth: date
    phone: str | None
    email: str | None
    deleted_at: datetime | None


class _PendingPersonEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: UUID
    full_name: str
    identity_number: str
    identity_digest: str
    phone: str | None
    email: str | None
    deleted_at: datetime | None
    material_version: Literal[2]
    sex: Literal["male", "female"] | None
    date_of_birth: date | None
    document_type: DocumentType
    document_issuing_country: str | None
    passport_number_digest: str | None


class _GuardianEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relationship_id: UUID
    dependent_person_id: UUID
    relationship: str
    is_primary: bool
    valid_from: date
    valid_until: date | None
    ended_at: datetime | None
    guardian: _PersonEvidence | _PendingPersonEvidence


def authenticated_approval_facts(
    approval: AssistedApproval, *, cipher: EnvelopeCipher, lookup: LookupDigest
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Authenticate both complete envelopes before any age/subset policy decision."""
    try:
        person_raw = cipher.decrypt(approval.snapshot_ciphertext, field_name=PERSON_SNAPSHOT_AAD)
        guardians_raw = cipher.decrypt(
            approval.guardian_snapshot_ciphertext, field_name=GUARDIAN_SNAPSHOT_AAD
        )
        for raw, domain, digest in (
            (person_raw, PERSON_SNAPSHOT_DOMAIN, approval.snapshot_digest),
            (guardians_raw, GUARDIAN_SNAPSHOT_DOMAIN, approval.guardian_snapshot_digest),
        ):
            if not compare_digest(lookup.digest_domain(domain, raw), digest):
                raise ApprovalEvidenceError()
        person = json.loads(person_raw)
        guardians = json.loads(guardians_raw)
        if not isinstance(person, dict):
            raise ApprovalEvidenceError()
        evidence_type = (
            _PendingPersonEvidence if person.get("material_version") == 2 else _PersonEvidence
        )
        evidence_type.model_validate_json(person_raw, strict=True)
        if not isinstance(guardians, list):
            raise ApprovalEvidenceError()
        for guardian in guardians:
            _GuardianEvidence.model_validate_json(canonical_json(guardian), strict=True)
        ids = [UUID(item["relationship_id"]) for item in guardians]
        if (
            person["person_id"] != str(approval.person_id)
            or len(ids) != len(set(ids))
            or set(ids) != set(approval.guardian_relationship_ids)
            or len(ids) != len(approval.guardian_relationship_ids)
            or any(item["dependent_person_id"] != str(approval.person_id) for item in guardians)
            or canonical_json(person) != person_raw
            or canonical_json(guardians) != guardians_raw
        ):
            raise ApprovalEvidenceError()
        return person, guardians
    except (EncryptedFieldError, ValidationError, ValueError, TypeError, KeyError) as exc:
        raise ApprovalEvidenceError() from exc


def guardian_facts(
    relationship: GuardianRelationship, guardian: Person, cipher: EnvelopeCipher
) -> dict[str, object]:
    return {
        "relationship_id": str(relationship.id),
        "dependent_person_id": str(relationship.student_person_id),
        "relationship": relationship.relationship,
        "is_primary": relationship.is_primary,
        "valid_from": relationship.valid_from.isoformat(),
        "valid_until": relationship.valid_until.isoformat() if relationship.valid_until else None,
        "ended_at": relationship.ended_at.isoformat() if relationship.ended_at else None,
        "guardian": person_facts(guardian, cipher),
    }


def capture_prefetched_snapshots(
    *,
    person: Person,
    relationships: list[GuardianRelationship],
    guardians: Mapping[UUID, Person],
    cipher: EnvelopeCipher,
) -> tuple[str, str]:
    if any(
        row.student_person_id != person.id or row.guardian_person_id not in guardians
        for row in relationships
    ):
        raise conflict()
    return canonical_json(person_facts(person, cipher)), canonical_json(
        [
            guardian_facts(row, guardians[row.guardian_person_id], cipher)
            for row in sorted(relationships, key=lambda row: row.id)
        ]
    )


def conflict() -> PersonnelIntakeError:
    return PersonnelIntakeError(
        "personnel_intake_conflict", status=409, detail="资料存在冲突，请联系管理员核实"
    )


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def guardian_graph_component(
    db: Session, seeds: set[UUID]
) -> tuple[set[UUID], set[tuple[UUID, UUID, UUID]]]:
    """Discover only reachable unended edges, matching the legacy cycle semantics.

    Frontier queries use both endpoint indexes; there is no global graph scan or
    mutex. Call again after the complete sorted guardian/person lock set and
    reject drift instead of acquiring new locks out of order.
    """
    people, frontier = set(seeds), set(seeds)
    edges: set[tuple[UUID, UUID, UUID]] = set()
    while frontier:
        rows = db.execute(
            select(
                GuardianRelationship.id,
                GuardianRelationship.guardian_person_id,
                GuardianRelationship.student_person_id,
            ).where(
                GuardianRelationship.ended_at.is_(None),
                GuardianRelationship.guardian_person_id.in_(frontier)
                | GuardianRelationship.student_person_id.in_(frontier),
            )
        )
        discovered: set[UUID] = set()
        for identifier, guardian, dependent in rows:
            edges.add((identifier, guardian, dependent))
            discovered.update((guardian, dependent))
        frontier = discovered - people
        people.update(discovered)
    return people, edges


def person_facts(person: Person, cipher: EnvelopeCipher) -> dict[str, object]:
    facts: dict[str, object] = {
        "person_id": str(person.id),
        "full_name": person.full_name,
        "identity_number": cipher.decrypt(person.identity_ciphertext, field_name="identity_number"),
        "identity_digest": person.identity_digest,
        "sex": person.sex,
        "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
        "phone": cipher.decrypt(person.phone_ciphertext, field_name="phone")
        if person.phone_ciphertext
        else None,
        "email": cipher.decrypt(person.email_ciphertext, field_name="email")
        if person.email_ciphertext
        else None,
        "deleted_at": person.deleted_at.isoformat() if person.deleted_at else None,
    }
    if person.intake_mode == "pending":
        facts.update(
            material_version=2,
            document_type=person.document_type,
            document_issuing_country=person.document_issuing_country,
            passport_number_digest=person.passport_number_digest,
        )
    elif person.document_type != "cn_id":
        facts.update(
            document_type=person.document_type,
            document_issuing_country=person.document_issuing_country,
            passport_number_digest=person.passport_number_digest,
        )
    return facts


def capture_current_snapshots(
    db: Session,
    *,
    person_id: UUID,
    relationship_ids: tuple[UUID, ...] | list[UUID],
    cipher: EnvelopeCipher,
) -> tuple[str, str]:
    """No computed age/ordinal: freeze exact identity, contacts and concrete edges.

    The same function captures current facts for later equality/digest checking;
    callers separately evaluate dates, age, contact verification and provenance.
    """
    person = db.get(Person, person_id, populate_existing=True)
    if person is None:
        raise conflict()
    ids = set(relationship_ids)
    rows = list(
        db.scalars(
            select(GuardianRelationship)
            .where(GuardianRelationship.id.in_(ids))
            .order_by(GuardianRelationship.id)
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) != len(ids) or any(row.student_person_id != person_id for row in rows):
        raise conflict()
    guardians = {
        row.id: row
        for row in db.scalars(
            select(Person)
            .where(Person.id.in_({edge.guardian_person_id for edge in rows}))
            .execution_options(populate_existing=True)
        )
    }
    return capture_prefetched_snapshots(
        person=person, relationships=rows, guardians=guardians, cipher=cipher
    )


def approval_snapshots_match(
    db: Session,
    *,
    approval: AssistedApproval,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> bool:
    """Compare freshly loaded actual facts with authenticated immutable snapshots.

    Callers own authorization and graph locks; eligibility, dates and provenance
    remain separate policy checks. Missing/corrupt facts fail closed.
    """
    try:
        current = capture_current_snapshots(
            db,
            person_id=approval.person_id,
            relationship_ids=approval.guardian_relationship_ids,
            cipher=cipher,
        )
        person, guardians = authenticated_approval_facts(approval, cipher=cipher, lookup=lookup)
        stored = canonical_json(person), canonical_json(guardians)
    except (PersonnelIntakeError, EncryptedFieldError):
        return False
    return current == stored


def facts_match(
    person: Person,
    value: IntakePayloadView | PendingIntakePayloadView | AssistedGuardianView,
    cipher: EnvelopeCipher,
) -> bool:
    facts = person_facts(person, cipher)
    return (
        not person.deleted_at
        and person.document_type == value.document_type
        and person.document_issuing_country == value.document_issuing_country
        and all(
            facts[key] == expected
            for key, expected in {
                "full_name": value.full_name,
                "identity_number": value.identity_number,
                "sex": value.sex,
                "date_of_birth": value.date_of_birth.isoformat() if value.date_of_birth else None,
                "phone": value.phone,
                "email": value.email,
            }.items()
        )
    )


def payload_identity_keys(
    value: IntakePayloadView | PendingIntakePayloadView | AssistedGuardianView,
    lookup: LookupDigest,
) -> DocumentIdentityKeys:
    return document_identity_keys(
        normalize_document_identity(
            value.document_type,
            value.identity_number,
            issuing_country=value.document_issuing_country,
            sex=value.sex,
            date_of_birth=value.date_of_birth,
            allow_pending=isinstance(value, PendingIntakePayloadView),
        ),
        lookup,
    )


def insert_person(
    db: Session,
    value: IntakePayloadView | AssistedGuardianView,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> Person:
    keys = payload_identity_keys(value, lookup)
    person = Person(
        full_name=value.full_name,
        identity_ciphertext=cipher.encrypt(value.identity_number, field_name="identity_number"),
        identity_digest=keys.identity_digest,
        document_type=value.document_type,
        document_issuing_country=keys.issuing_country,
        passport_number_digest=keys.passport_number_digest,
        phone_ciphertext=cipher.encrypt(value.phone, field_name="phone"),
        phone_digest=lookup.digest(value.phone),
        email_ciphertext=cipher.encrypt(value.email, field_name="email") if value.email else None,
        email_digest=lookup.digest_domain("person-email", value.email) if value.email else None,
        sex=value.sex,
        date_of_birth=value.date_of_birth,
    )
    db.add(person)
    db.flush()
    return person


def end_replaced_assisted_relationships(
    db: Session,
    *,
    person_id: UUID,
    previous_ids: list[UUID],
    guardians: list[AssistedGuardianView],
    actor_id: UUID,
    now: datetime,
    reason: str,
    lookup: LookupDigest,
) -> None:
    """Explicit correction may close only its own still-unverified approved edges."""
    desired = {payload_identity_keys(value, lookup).identity_digest: value for value in guardians}
    today = business_date_at(now)
    rows = db.scalars(
        select(GuardianRelationship)
        .where(
            GuardianRelationship.id.in_(previous_ids),
            GuardianRelationship.student_person_id == person_id,
            GuardianRelationship.ended_at.is_(None),
        )
        .order_by(GuardianRelationship.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for row in rows:
        if not row.requires_verification:
            continue
        guardian = db.get(Person, row.guardian_person_id)
        value = desired.get(guardian.identity_digest) if guardian else None
        if (
            value is None
            or row.relationship != value.relationship.strip()
            or row.is_primary != value.is_primary
            or row.valid_from > today
            or (row.valid_until is not None and row.valid_until < today)
        ):
            row.ended_at, row.ended_by_user_id, row.end_reason = now, actor_id, reason[:200]
    db.flush()


class AssistedGuardianWriter:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        self.db, self.cipher, self.lookup = db, cipher, lookup

    def _cycle(self, dependent: UUID, guardian: UUID) -> bool:
        frontier, visited = {dependent}, set()
        while frontier:
            if guardian in frontier:
                return True
            visited.update(frontier)
            frontier = (
                set(
                    self.db.scalars(
                        select(GuardianRelationship.student_person_id).where(
                            GuardianRelationship.guardian_person_id.in_(frontier),
                            GuardianRelationship.ended_at.is_(None),
                        )
                    )
                )
                - visited
            )
        return False

    def apply_in_transaction(
        self,
        person_id: UUID,
        guardians: list[AssistedGuardianInput] | list[AssistedGuardianView],
        reference_date: date,
    ) -> tuple[UUID, ...]:
        from app.identity.normalization import normalize_email, normalize_phone

        result = []
        for raw in guardians:
            if isinstance(raw, AssistedGuardianInput):
                identity = normalize_document_identity(
                    raw.document_type,
                    raw.identity_number.get_secret_value(),
                    issuing_country=raw.document_issuing_country,
                    sex=raw.sex,
                    date_of_birth=raw.date_of_birth,
                    allow_pending=False,
                )
                if identity.sex != raw.sex:
                    raise conflict()
                value = AssistedGuardianView(
                    full_name=raw.full_name,
                    identity_number=identity.number,
                    document_type=raw.document_type,
                    document_issuing_country=identity.issuing_country,
                    phone=normalize_phone(raw.phone.get_secret_value()),
                    email=normalize_email(raw.email.get_secret_value()) if raw.email else None,
                    sex=raw.sex,
                    date_of_birth=raw.date_of_birth,
                    relationship=raw.relationship,
                    is_primary=raw.is_primary,
                )
            else:
                value = raw
            if (
                is_minor(value.date_of_birth, reference_date)
                or not 2 <= len(value.relationship.strip()) <= 40
            ):
                raise conflict()
            guardian = self.db.scalar(
                select(Person).where(
                    Person.identity_digest
                    == payload_identity_keys(value, self.lookup).identity_digest
                )
            )
            if guardian is None:
                guardian = insert_person(self.db, value, cipher=self.cipher, lookup=self.lookup)
            elif not facts_match(guardian, value, self.cipher):
                raise conflict()
            if guardian.id == person_id or self._cycle(person_id, guardian.id):
                raise conflict()
            if self.db.get(GuardianProfile, guardian.id) is None:
                self.db.add(GuardianProfile(person_id=guardian.id))
            rows = list(
                self.db.scalars(
                    select(GuardianRelationship).where(
                        GuardianRelationship.guardian_person_id == guardian.id,
                        GuardianRelationship.student_person_id == person_id,
                        GuardianRelationship.ended_at.is_(None),
                    )
                )
            )
            if rows:
                matching = [
                    row
                    for row in rows
                    if row.valid_from <= reference_date
                    and (row.valid_until is None or row.valid_until >= reference_date)
                    and row.relationship == value.relationship.strip()
                    and row.is_primary == value.is_primary
                ]
                if len(matching) != 1:
                    raise conflict()
                relation = matching[0]
            else:
                relation = GuardianRelationship(
                    guardian_person_id=guardian.id,
                    student_person_id=person_id,
                    relationship=value.relationship.strip(),
                    is_primary=value.is_primary,
                    valid_from=reference_date,
                    requires_verification=True,
                )
                self.db.add(relation)
            self.db.flush()
            result.append(relation.id)
        return tuple(sorted(result))
