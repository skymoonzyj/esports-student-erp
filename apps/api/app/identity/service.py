from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.time import business_today
from app.core.transactions import write_transaction
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.normalization import IdentityValidationError, normalize_cn_id, normalize_phone
from app.identity.readiness import (
    AgeStatus,
    GuardianIssue,
    GuardianReadinessPolicy,
    GuardianStatus,
    PersonReadinessProjection,
)
from app.identity.readiness import is_minor as _is_minor
from app.identity.schemas import (
    AccountCandidateSummary,
    AccountReadinessCandidate,
    GuardianCreate,
    GuardianRelationshipStatus,
    GuardianSummary,
    Page,
    PersonDetail,
    PersonSummary,
    PersonType,
    PersonUpdate,
    Sex,
    StaffCreate,
    StaffType,
    StudentCreate,
    StudentStatus,
    VisitorCreate,
)
from app.security.encryption import EnvelopeCipher, LookupDigest

is_minor = _is_minor


class IdentityServiceError(ValueError):
    code = "identity_service_error"


class IdentityAlreadyExists(IdentityServiceError):
    code = "identity_already_exists"


class StudentNumberAlreadyExists(IdentityServiceError):
    code = "student_number_already_exists"


class PersonNotFound(IdentityServiceError):
    code = "person_not_found"


class InvalidLinkedStudent(IdentityServiceError):
    code = "invalid_linked_student"


class GuardianSelfRelationship(IdentityServiceError):
    code = "guardian_self_relationship"


class GuardianCycle(IdentityServiceError):
    code = "guardian_cycle"


class GuardianRelationshipAlreadyExists(IdentityServiceError):
    code = "guardian_relationship_overlaps"


class GuardianRelationshipAlreadyEnded(IdentityServiceError):
    code = "guardian_relationship_already_ended"


class GuardianUnderage(IdentityServiceError):
    code = "guardian_underage"


@dataclass(frozen=True)
class IdentityReadiness:
    ready: bool
    missing: list[str]
    age_status: AgeStatus
    guardian_status: GuardianStatus
    guardian_issues: tuple[GuardianIssue, ...] = ()


def relationship_status(
    relationship: GuardianRelationship,
    reference_date: date,
) -> GuardianRelationshipStatus:
    if relationship.ended_at is not None:
        return "ended"
    if relationship.valid_from > reference_date:
        return "scheduled"
    if relationship.valid_until is not None and relationship.valid_until < reference_date:
        return "expired"
    return "active"


class IdentityService:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup

    def _normalized_identity(
        self,
        identity_number: str,
        birth_date: date,
        sex: str,
    ) -> tuple[str, str]:
        normalized = normalize_cn_id(identity_number, expected_birth_date=birth_date)
        if normalized.sex != sex:
            raise IdentityValidationError(
                field="sex",
                code="identity_sex_mismatch",
                message="性别与身份证不一致",
            )
        return normalized.number, self.lookup.digest(normalized.number)

    def _ensure_identity_available(self, digest: str, *, exclude_id: UUID | None = None) -> None:
        query = select(Person.id).where(Person.identity_digest == digest)
        if exclude_id is not None:
            query = query.where(Person.id != exclude_id)
        if self.db.scalar(query) is not None:
            raise IdentityAlreadyExists("identity already exists")

    def _ensure_student_number_available(
        self,
        student_number: str,
        *,
        exclude_id: UUID | None = None,
    ) -> None:
        query = select(StudentProfile.person_id).where(
            StudentProfile.student_number == student_number
        )
        if exclude_id is not None:
            query = query.where(StudentProfile.person_id != exclude_id)
        if self.db.scalar(query) is not None:
            raise StudentNumberAlreadyExists("student number already exists")

    def _new_person(
        self,
        *,
        full_name: str,
        identity_number: str,
        sex: str,
        birth_date: date,
        phone: str | None,
        email: object | None,
    ) -> Person:
        normalized_identity, identity_digest = self._normalized_identity(
            identity_number,
            birth_date,
            sex,
        )
        self._ensure_identity_available(identity_digest)
        normalized_phone = normalize_phone(phone) if phone is not None else None
        person = Person(
            full_name=full_name,
            identity_ciphertext=self.cipher.encrypt(
                normalized_identity,
                field_name="identity_number",
            ),
            identity_digest=identity_digest,
            phone_ciphertext=(
                self.cipher.encrypt(normalized_phone, field_name="phone")
                if normalized_phone is not None
                else None
            ),
            phone_digest=(
                self.lookup.digest(normalized_phone) if normalized_phone is not None else None
            ),
            email=str(email).strip().lower() if email is not None else None,
            sex=sex,
            date_of_birth=birth_date,
        )
        self.db.add(person)
        self.db.flush()
        return person

    def _translate_integrity_error(self, exc: IntegrityError) -> IdentityServiceError:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in {"people_identity_digest_key"}:
            return IdentityAlreadyExists("identity already exists")
        if constraint in {"student_profiles_student_number_key"}:
            return StudentNumberAlreadyExists("student number already exists")
        return IdentityServiceError("identity transaction failed")

    def create_student(self, input: StudentCreate) -> PersonSummary:
        try:
            with write_transaction(self.db):
                self._ensure_student_number_available(input.student_number)
                person = self._new_person(
                    full_name=input.full_name,
                    identity_number=input.identity_number,
                    sex=input.sex,
                    birth_date=input.date_of_birth,
                    phone=input.phone,
                    email=input.email,
                )
                self.db.add(
                    StudentProfile(
                        person_id=person.id,
                        student_number=input.student_number,
                        status="draft",
                    )
                )
                self.db.flush()
                result = self._summary(person, ["student"])
            return result
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def create_guardian(self, input: GuardianCreate) -> PersonSummary:
        try:
            with write_transaction(self.db):
                person = self._new_person(
                    full_name=input.full_name,
                    identity_number=input.identity_number,
                    sex=input.sex,
                    birth_date=input.date_of_birth,
                    phone=input.phone,
                    email=input.email,
                )
                result = self._summary(person, ["guardian"])
            return result
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def create_staff(self, input: StaffCreate) -> PersonSummary:
        try:
            with write_transaction(self.db):
                person = self._new_person(
                    full_name=input.full_name,
                    identity_number=input.identity_number,
                    sex=input.sex,
                    birth_date=input.date_of_birth,
                    phone=input.phone,
                    email=input.email,
                )
                self.db.add(StaffProfile(person_id=person.id, staff_type=input.staff_type))
                self.db.flush()
                result = self._summary(person, ["staff"])
            return result
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def create_visitor(self, input: VisitorCreate) -> PersonSummary:
        try:
            with write_transaction(self.db):
                if (
                    input.linked_student_id is not None
                    and self.db.get(StudentProfile, input.linked_student_id) is None
                ):
                    raise InvalidLinkedStudent("linked student does not exist")
                person = self._new_person(
                    full_name=input.full_name,
                    identity_number=input.identity_number,
                    sex=input.sex,
                    birth_date=input.date_of_birth,
                    phone=input.phone,
                    email=input.email,
                )
                self.db.add(
                    VisitorProfile(
                        person_id=person.id,
                        linked_student_id=input.linked_student_id,
                        visit_reason=input.visit_reason,
                    )
                )
                self.db.flush()
                result = self._summary(person, ["visitor"])
            return result
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def _types_for(self, person_ids: Iterable[UUID]) -> dict[UUID, list[PersonType]]:
        ids = list(person_ids)
        result: dict[UUID, list[PersonType]] = {person_id: [] for person_id in ids}
        if not ids:
            return result
        for person_id in self.db.scalars(
            select(StudentProfile.person_id).where(StudentProfile.person_id.in_(ids))
        ):
            result[person_id].append("student")
        for person_id in self.db.scalars(
            select(GuardianRelationship.guardian_person_id)
            .where(GuardianRelationship.guardian_person_id.in_(ids))
            .distinct()
        ):
            result[person_id].append("guardian")
        for person_id in self.db.scalars(
            select(StaffProfile.person_id).where(StaffProfile.person_id.in_(ids))
        ):
            result[person_id].append("staff")
        for person_id in self.db.scalars(
            select(VisitorProfile.person_id).where(VisitorProfile.person_id.in_(ids))
        ):
            result[person_id].append("visitor")
        # Guardians intentionally have no empty profile table. A person created through
        # the guardian endpoint remains a guardian before the first relationship is
        # added, and after the last relationship is removed.
        for person_types in result.values():
            if not person_types:
                person_types.append("guardian")
        return result

    @staticmethod
    def _mask_identity(value: str) -> str:
        return f"{value[:6]}********{value[-4:]}"

    @staticmethod
    def _mask_phone(value: str) -> str:
        return f"{value[:3]}****{value[-4:]}"

    def _summary(
        self,
        person: Person,
        person_types: list[PersonType],
        *,
        projection: PersonReadinessProjection | None = None,
    ) -> PersonSummary:
        if projection is None:
            projection = GuardianReadinessPolicy(self.db).evaluate_many(
                [person],
                student_ids={person.id} if "student" in person_types else set(),
                reference_date=business_today(),
            )[person.id]
        identity_number = self.cipher.decrypt(
            person.identity_ciphertext,
            field_name="identity_number",
        )
        phone = (
            self.cipher.decrypt(person.phone_ciphertext, field_name="phone")
            if person.phone_ciphertext is not None
            else None
        )
        return PersonSummary(
            id=person.id,
            full_name=person.full_name,
            person_types=person_types,
            masked_identity=self._mask_identity(identity_number),
            masked_phone=self._mask_phone(phone) if phone is not None else None,
            email=person.email,
            sex=cast(Sex, person.sex),
            date_of_birth=person.date_of_birth,
            created_at=person.created_at,
            age_status=projection.age_status,
            guardian_status=projection.guardian_status,
        )

    def search_people(
        self,
        *,
        phone: str | None = None,
        name: str | None = None,
        person_type: PersonType | None = None,
        page: int,
        page_size: int,
    ) -> Page[PersonSummary]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        query = select(Person).where(Person.deleted_at.is_(None))
        if phone is not None:
            query = query.where(Person.phone_digest == self.lookup.digest(normalize_phone(phone)))
        if name is not None:
            escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(Person.full_name.ilike(f"%{escaped}%", escape="\\"))
        if person_type == "student":
            query = query.where(Person.id.in_(select(StudentProfile.person_id)))
        elif person_type == "guardian":
            relationship_guardians = Person.id.in_(
                select(GuardianRelationship.guardian_person_id)
            )
            unprofiled_guardians = (
                Person.id.not_in(select(StudentProfile.person_id))
                & Person.id.not_in(select(StaffProfile.person_id))
                & Person.id.not_in(select(VisitorProfile.person_id))
            )
            query = query.where(relationship_guardians | unprofiled_guardians)
        elif person_type == "staff":
            query = query.where(Person.id.in_(select(StaffProfile.person_id)))
        elif person_type == "visitor":
            query = query.where(Person.id.in_(select(VisitorProfile.person_id)))

        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        people = list(
            self.db.scalars(
                query.order_by(Person.created_at.desc(), Person.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        types = self._types_for(person.id for person in people)
        projections = GuardianReadinessPolicy(self.db).evaluate_many(
            people,
            student_ids={
                person.id for person in people if "student" in types[person.id]
            },
            reference_date=business_today(),
        )
        return Page[PersonSummary](
            items=[
                self._summary(
                    person,
                    types[person.id],
                    projection=projections[person.id],
                )
                for person in people
            ],
            page=page,
            page_size=page_size,
            total=total,
        )

    def search_account_candidates(self, *, name: str, limit: int = 20) -> list[AccountCandidateSummary]:
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        people = list(
            self.db.scalars(
                select(Person)
                .where(
                    Person.deleted_at.is_(None),
                    Person.phone_ciphertext.is_not(None),
                    Person.email.is_not(None),
                    Person.id.not_in(select(UserAccount.person_id)),
                    Person.full_name.ilike(f"%{escaped}%", escape="\\"),
                )
                .order_by(Person.full_name, Person.id)
                .limit(limit)
            )
        )
        types = self._types_for(person.id for person in people)
        result: list[AccountCandidateSummary] = []
        for person in people:
            if person.phone_ciphertext is None:
                continue
            phone = self.cipher.decrypt(person.phone_ciphertext, field_name="phone")
            result.append(
                AccountCandidateSummary(
                    id=person.id,
                    full_name=person.full_name,
                    person_types=types[person.id],
                    masked_phone=self._mask_phone(phone),
                )
            )
        return result

    def get_person(self, person_id: UUID) -> PersonDetail:
        person = self.db.scalar(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
        if person is None:
            raise PersonNotFound("person not found")
        return self._detail(person)

    def _detail(self, person: Person) -> PersonDetail:
        types = self._types_for([person.id])[person.id]
        summary = self._summary(person, types)
        student = self.db.get(StudentProfile, person.id)
        staff = self.db.get(StaffProfile, person.id)
        visitor = self.db.get(VisitorProfile, person.id)
        return PersonDetail(
            **summary.model_dump(),
            student_number=student.student_number if student else None,
            student_status=cast(StudentStatus | None, student.status if student else None),
            staff_type=cast(StaffType | None, staff.staff_type if staff else None),
            visit_reason=visitor.visit_reason if visitor else None,
            linked_student_id=visitor.linked_student_id if visitor else None,
        )

    def update_person(
        self,
        person_id: UUID,
        input: PersonUpdate,
        *,
        actor_user_id: UUID | None,
        request_id: str,
    ) -> PersonDetail:
        try:
            with write_transaction(self.db):
                readiness_policy = GuardianReadinessPolicy(self.db)
                dependent_person_ids = readiness_policy.guardian_and_dependent_person_ids(
                    person_id
                )
                readiness_policy.lock_students(dependent_person_ids)
                person = self.db.scalar(
                    select(Person)
                    .where(Person.id == person_id, Person.deleted_at.is_(None))
                    .execution_options(populate_existing=True)
                )
                if person is None:
                    raise PersonNotFound("person not found")
                fields = input.model_fields_set
                identity_number = (
                    input.identity_number
                    if "identity_number" in fields and input.identity_number is not None
                    else self.cipher.decrypt(
                        person.identity_ciphertext,
                        field_name="identity_number",
                    )
                )
                birth_date = (
                    input.date_of_birth
                    if "date_of_birth" in fields and input.date_of_birth is not None
                    else person.date_of_birth
                )
                sex = input.sex if "sex" in fields and input.sex is not None else person.sex
                normalized_identity, identity_digest = self._normalized_identity(
                    identity_number,
                    birth_date,
                    sex,
                )
                self._ensure_identity_available(identity_digest, exclude_id=person.id)
                if "full_name" in fields and input.full_name is not None:
                    person.full_name = input.full_name
                person.identity_ciphertext = self.cipher.encrypt(
                    normalized_identity,
                    field_name="identity_number",
                )
                person.identity_digest = identity_digest
                person.date_of_birth = birth_date
                person.sex = sex
                if "phone" in fields:
                    normalized_phone = (
                        normalize_phone(input.phone) if input.phone is not None else None
                    )
                    person.phone_ciphertext = (
                        self.cipher.encrypt(normalized_phone, field_name="phone")
                        if normalized_phone is not None
                        else None
                    )
                    person.phone_digest = (
                        self.lookup.digest(normalized_phone)
                        if normalized_phone is not None
                        else None
                    )
                if "email" in fields:
                    person.email = (
                        str(input.email).strip().lower() if input.email is not None else None
                    )

                student = self.db.get(StudentProfile, person.id)
                if student is not None:
                    if "student_number" in fields and input.student_number is not None:
                        self._ensure_student_number_available(
                            input.student_number,
                            exclude_id=person.id,
                        )
                        student.student_number = input.student_number
                    if "student_status" in fields and input.student_status is not None:
                        student.status = input.student_status
                staff = self.db.get(StaffProfile, person.id)
                if staff is not None and "staff_type" in fields and input.staff_type is not None:
                    staff.staff_type = input.staff_type
                visitor = self.db.get(VisitorProfile, person.id)
                if visitor is not None:
                    if "visit_reason" in fields and input.visit_reason is not None:
                        visitor.visit_reason = input.visit_reason
                    if "linked_student_id" in fields:
                        if (
                            input.linked_student_id is not None
                            and self.db.get(StudentProfile, input.linked_student_id) is None
                        ):
                            raise InvalidLinkedStudent("linked student does not exist")
                        visitor.linked_student_id = input.linked_student_id
                self.db.flush()
                readiness_policy.disable_unready_dependents(
                    person.id,
                    reference_date=business_today(),
                    revoked_at=datetime.now(UTC),
                    audit_actor_user_id=actor_user_id,
                    audit_request_id=request_id,
                    dependent_person_ids=dependent_person_ids,
                )
                result = self._detail(person)
            return result
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def _require_student(self, student_id: UUID) -> None:
        student = self.db.scalar(
            select(StudentProfile.person_id)
            .join(Person, Person.id == StudentProfile.person_id)
            .where(StudentProfile.person_id == student_id, Person.deleted_at.is_(None))
        )
        if student is None:
            raise PersonNotFound("student not found")

    def _require_person(self, person_id: UUID) -> Person:
        person = self.db.scalar(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
        if person is None:
            raise PersonNotFound("person not found")
        return person

    def _would_create_guardian_cycle(self, student_id: UUID, guardian_id: UUID) -> bool:
        frontier = {student_id}
        visited: set[UUID] = set()
        while frontier:
            if guardian_id in frontier:
                return True
            visited.update(frontier)
            descendants = set(
                self.db.scalars(
                    select(GuardianRelationship.student_person_id).where(
                        GuardianRelationship.guardian_person_id.in_(frontier),
                        GuardianRelationship.ended_at.is_(None),
                    )
                )
            )
            frontier = descendants - visited
        return False

    def _guardian_summary(
        self,
        relationship: GuardianRelationship,
        guardian: Person,
        person_types: list[PersonType],
        *,
        reference_date: date,
        projection: PersonReadinessProjection | None = None,
    ) -> GuardianSummary:
        return GuardianSummary(
            **self._summary(
                guardian,
                person_types,
                projection=projection,
            ).model_dump(),
            relationship_id=relationship.id,
            relationship=relationship.relationship,
            is_primary=relationship.is_primary,
            valid_from=relationship.valid_from,
            valid_until=relationship.valid_until,
            status=relationship_status(relationship, reference_date),
            ended_at=relationship.ended_at,
            end_reason=relationship.end_reason,
        )

    def add_guardian(
        self,
        student_id: UUID,
        guardian_id: UUID,
        relationship: str,
        is_primary: bool,
        valid_from: date,
        valid_until: date | None,
    ) -> GuardianSummary:
        if student_id == guardian_id:
            raise GuardianSelfRelationship("guardian cannot be the student")
        if not 2 <= len(relationship.strip()) <= 40:
            raise ValueError("relationship must be between 2 and 40 characters")
        if valid_until is not None and valid_until < valid_from:
            raise ValueError("valid_until must not precede valid_from")
        try:
            with write_transaction(self.db):
                readiness_policy = GuardianReadinessPolicy(self.db)
                readiness_policy.lock_guardian_relationships(guardian_id)
                readiness_policy.lock_students([student_id, guardian_id])
                self._require_student(student_id)
                guardian = self._require_person(guardian_id)
                if is_minor(guardian.date_of_birth, valid_from):
                    raise GuardianUnderage("guardian must be an adult on valid_from")
                if self._would_create_guardian_cycle(student_id, guardian_id):
                    raise GuardianCycle("guardian relationship would create a cycle")
                relation = GuardianRelationship(
                    guardian_person_id=guardian_id,
                    student_person_id=student_id,
                    relationship=relationship.strip(),
                    is_primary=is_primary,
                    valid_from=valid_from,
                    valid_until=valid_until,
                )
                self.db.add(relation)
                self.db.flush()
                person_types = self._types_for([guardian_id])[guardian_id]
                result = self._guardian_summary(
                    relation,
                    guardian,
                    person_types,
                    reference_date=business_today(),
                )
            return result
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "excl_guardian_relationships_overlapping_dates":
                raise GuardianRelationshipAlreadyExists(
                    "guardian relationship overlaps an existing relationship"
                ) from exc
            raise IdentityServiceError("guardian transaction failed") from exc

    def list_guardians(
        self,
        student_id: UUID,
        reference_date: date | None,
    ) -> list[GuardianSummary]:
        self._require_student(student_id)
        query = (
            select(GuardianRelationship, Person)
            .join(Person, Person.id == GuardianRelationship.guardian_person_id)
            .where(
                GuardianRelationship.student_person_id == student_id,
                Person.deleted_at.is_(None),
            )
        )
        if reference_date is not None:
            query = query.where(
                GuardianRelationship.ended_at.is_(None),
                GuardianRelationship.valid_from <= reference_date,
                (
                    GuardianRelationship.valid_until.is_(None)
                    | (GuardianRelationship.valid_until >= reference_date)
                ),
            )
        if reference_date is None:
            query = query.order_by(
                GuardianRelationship.valid_from.desc(),
                GuardianRelationship.created_at.desc(),
                GuardianRelationship.id.desc(),
            )
        else:
            query = query.order_by(
                GuardianRelationship.is_primary.desc(),
                Person.full_name,
                Person.id,
            )
        rows = self.db.execute(query).all()
        people_by_id = {person.id: person for _, person in rows}
        people = list(people_by_id.values())
        types = self._types_for(people_by_id)
        summary_reference_date = reference_date or business_today()
        projections = GuardianReadinessPolicy(self.db).evaluate_many(
            people,
            student_ids={person.id for person in people if "student" in types[person.id]},
            reference_date=summary_reference_date,
        )
        return [
            self._guardian_summary(
                relation,
                person,
                types[person.id],
                reference_date=summary_reference_date,
                projection=projections[person.id],
            )
            for relation, person in rows
        ]

    def end_guardian_relationship(
        self,
        student_id: UUID,
        relationship_id: UUID,
        *,
        ended_by_user_id: UUID | None,
        reason: str,
        ended_at: datetime,
        request_id: str,
    ) -> GuardianSummary:
        normalized_reason = reason.strip()
        if not 2 <= len(normalized_reason) <= 200:
            raise ValueError("reason must be between 2 and 200 characters")
        with write_transaction(self.db):
            guardian_person_id = self.db.scalar(
                select(GuardianRelationship.guardian_person_id).where(
                    GuardianRelationship.id == relationship_id,
                    GuardianRelationship.student_person_id == student_id,
                )
            )
            readiness_policy = GuardianReadinessPolicy(self.db)
            person_ids = (
                readiness_policy.guardian_and_dependent_person_ids(guardian_person_id)
                if guardian_person_id is not None
                else (student_id,)
            )
            readiness_policy.lock_students(person_ids)
            self._require_student(student_id)
            relationship = self.db.scalar(
                select(GuardianRelationship)
                .where(
                    GuardianRelationship.id == relationship_id,
                    GuardianRelationship.student_person_id == student_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if relationship is None:
                raise PersonNotFound("guardian relationship not found")
            if relationship.ended_at is not None:
                raise GuardianRelationshipAlreadyEnded(
                    "guardian relationship has already ended"
                )
            relationship.ended_at = ended_at
            relationship.ended_by_user_id = ended_by_user_id
            relationship.end_reason = normalized_reason
            self.db.flush()
            readiness_policy.disable_unready_dependents(
                relationship.guardian_person_id,
                reference_date=business_today(),
                revoked_at=ended_at,
                audit_actor_user_id=ended_by_user_id,
                audit_request_id=request_id,
                dependent_person_ids=person_ids,
            )
            guardian = self._require_person(relationship.guardian_person_id)
            person_types = self._types_for([guardian.id])[guardian.id]
            result = self._guardian_summary(
                relationship,
                guardian,
                person_types,
                reference_date=ended_at.date(),
            )
        return result

    def readiness(
        self,
        person_id: UUID,
        reference_date: date,
        account_candidate: AccountReadinessCandidate | None = None,
        *,
        include_guardian_issues: bool = False,
    ) -> IdentityReadiness:
        person = self._require_person(person_id)
        missing: list[str] = []
        if not person.full_name.strip():
            missing.append("full_name")
        if not person.identity_ciphertext or not person.identity_digest:
            missing.append("identity_number")
        if person.sex not in {"male", "female"}:
            missing.append("sex")
        if person.date_of_birth is None:
            missing.append("date_of_birth")

        if account_candidate is not None:
            if not account_candidate.phone:
                missing.append("phone")
            else:
                try:
                    normalize_phone(account_candidate.phone)
                except IdentityValidationError:
                    missing.append("phone")
            if not account_candidate.email:
                missing.append("email")

        projection = GuardianReadinessPolicy(self.db).evaluate_one(
            person_id,
            reference_date,
            include_issues=include_guardian_issues,
        )
        missing.extend(item for item in projection.missing if item not in missing)
        return IdentityReadiness(
            ready=not missing,
            missing=missing,
            age_status=projection.age_status,
            guardian_status=projection.guardian_status,
            guardian_issues=projection.issues,
        )
