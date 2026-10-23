from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.time import business_today
from app.core.transactions import write_transaction
from app.identity.membership_locks import (
    lock_person_memberships,
    person_membership_lock_keys,
)
from app.identity.models import (
    GuardianProfile,
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.normalization import (
    IdentityValidationError,
    normalize_cn_id,
    normalize_email,
    normalize_phone,
)
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
    PersonBaseInput,
    PersonCounts,
    PersonDetail,
    PersonRole,
    PersonSummary,
    PersonType,
    PersonUpdate,
    SelfProfile,
    SelfProfileUpdate,
    Sex,
    StaffCreate,
    StaffType,
    StudentCreate,
    StudentStatus,
    VisitorCreate,
)
from app.rbac.models import Role, UserRole
from app.security.encryption import EnvelopeCipher, LookupDigest

if TYPE_CHECKING:
    from app.auth.service import AuthContext

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
        email: str | None,
    ) -> Person:
        normalized_identity, identity_digest = self._normalized_identity(
            identity_number,
            birth_date,
            sex,
        )
        normalized_phone = normalize_phone(phone) if phone is not None else None
        normalized_email = normalize_email(email) if email is not None else None
        phone_digest = (
            self.lookup.digest(normalized_phone) if normalized_phone is not None else None
        )
        email_digest = (
            self.lookup.digest_domain("person-email", normalized_email)
            if normalized_email is not None
            else None
        )
        lock_person_memberships(
            self.db,
            person_membership_lock_keys(
                identity_digests={identity_digest},
                phone_digests={digest for digest in (phone_digest,) if digest},
                email_digests={digest for digest in (email_digest,) if digest},
            ),
        )
        self._ensure_identity_available(identity_digest)
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
            phone_digest=phone_digest,
            email_ciphertext=(
                self.cipher.encrypt(normalized_email, field_name="email")
                if normalized_email is not None
                else None
            ),
            email_digest=email_digest,
            sex=sex,
            date_of_birth=birth_date,
        )
        self.db.add(person)
        self.db.flush()
        return person

    def _decrypt_email(self, person: Person) -> str | None:
        if (person.email_ciphertext is None) != (person.email_digest is None):
            raise IdentityServiceError("person email is invalid")
        if person.email_ciphertext is not None:
            try:
                value = self.cipher.decrypt(person.email_ciphertext, field_name="email")
                normalized = normalize_email(value)
            except (IdentityValidationError, ValueError) as exc:
                raise IdentityServiceError("person email is invalid") from exc
            if person.email_digest != self.lookup.digest_domain("person-email", normalized):
                raise IdentityServiceError("person email is invalid")
            return normalized
        # Rows created before the encrypted-email migration may still be present in
        # isolated preflight databases. Production migration 0017 clears this column.
        return normalize_email(person.email) if person.email is not None else None

    def _decrypt_phone(self, person: Person) -> str | None:
        if (person.phone_ciphertext is None) != (person.phone_digest is None):
            raise IdentityServiceError("person phone is invalid")
        if person.phone_ciphertext is None:
            return None
        try:
            value = self.cipher.decrypt(person.phone_ciphertext, field_name="phone")
            normalized = normalize_phone(value)
        except (IdentityValidationError, ValueError) as exc:
            raise IdentityServiceError("person phone is invalid") from exc
        if person.phone_digest != self.lookup.digest(normalized):
            raise IdentityServiceError("person phone is invalid")
        return normalized

    def decrypt_phone(self, person: Person) -> str | None:
        """Return a normalized phone for an already-authorized internal caller."""
        return self._decrypt_phone(person)

    def decrypt_email(self, person: Person) -> str | None:
        """Return a normalized email for an already-authorized internal caller."""
        return self._decrypt_email(person)

    def decrypt_email_for_authorized_person(self, person_id: UUID) -> str | None:
        """Decrypt email after the caller has authorized access to this person."""
        return self._decrypt_email(self._require_person(person_id))

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

    def create_base_person(self, input: PersonBaseInput) -> PersonSummary:
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
                result = self._summary(person, [])
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
                self.db.add(GuardianProfile(person_id=person.id))
                self.db.flush()
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

    def _types_for(
        self,
        person_ids: Iterable[UUID],
        *,
        staff_types: dict[UUID, str] | None = None,
    ) -> dict[UUID, list[PersonType]]:
        ids = list(person_ids)
        result: dict[UUID, list[PersonType]] = {person_id: [] for person_id in ids}
        if not ids:
            return result
        for person_id in self.db.scalars(
            select(StudentProfile.person_id).where(StudentProfile.person_id.in_(ids))
        ):
            result[person_id].append("student")
        for person_id in self.db.scalars(
            select(GuardianProfile.person_id).where(GuardianProfile.person_id.in_(ids))
        ):
            result[person_id].append("guardian")
        for person_id, staff_type in self.db.execute(
            select(StaffProfile.person_id, StaffProfile.staff_type).where(
                StaffProfile.person_id.in_(ids)
            )
        ):
            result[person_id].append("staff")
            if staff_types is not None:
                staff_types[person_id] = staff_type
        for person_id in self.db.scalars(
            select(VisitorProfile.person_id).where(VisitorProfile.person_id.in_(ids))
        ):
            result[person_id].append("visitor")
        return result

    @staticmethod
    def _mask_identity(value: str) -> str:
        return f"{value[:6]}********{value[-4:]}"

    @staticmethod
    def _mask_phone(value: str) -> str:
        return f"{value[:3]}****{value[-4:]}"

    @staticmethod
    def _mask_email(value: str) -> str:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"

    def _summary(
        self,
        person: Person,
        person_types: list[PersonType],
        *,
        projection: PersonReadinessProjection | None = None,
        include_email: bool = True,
        staff_types: dict[UUID, str] | None = None,
        roles: list[PersonRole] | None = None,
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
        staff_type = None
        if "staff" in person_types:
            if staff_types is not None:
                staff_type = staff_types.get(person.id)
            else:
                staff = self.db.get(StaffProfile, person.id)
                staff_type = staff.staff_type if staff else None
        return PersonSummary(
            id=person.id,
            full_name=person.full_name,
            nickname=person.nickname,
            person_types=person_types,
            masked_identity=self._mask_identity(identity_number),
            masked_phone=self._mask_phone(phone) if phone is not None else None,
            email=self._decrypt_email(person) if include_email else None,
            sex=cast(Sex, person.sex),
            date_of_birth=person.date_of_birth,
            staff_type=cast(StaffType | None, staff_type),
            created_at=person.created_at,
            age_status=projection.age_status,
            guardian_status=projection.guardian_status,
            roles=roles if roles is not None else self._roles_for([person.id])[person.id],
        )

    def _roles_for(self, person_ids: Iterable[UUID]) -> dict[UUID, list[PersonRole]]:
        ids = list(person_ids)
        result: dict[UUID, list[PersonRole]] = {key: [] for key in ids}
        if ids:
            for person_id, key, name in self.db.execute(
                select(UserAccount.person_id, Role.key, Role.name)
                .join(UserRole, UserRole.user_id == UserAccount.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserAccount.person_id.in_(ids))
                .order_by(Role.key)
            ):
                result[person_id].append(PersonRole(key=key, name=name))
        return result

    def search_people(
        self,
        *,
        phone: str | None = None,
        name: str | None = None,
        person_type: PersonType | None = None,
        staff_type: StaffType | None = None,
        role: str | None = None,
        actor: AuthContext | None = None,
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
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    Person.full_name.ilike(pattern, escape="\\"),
                    Person.nickname.ilike(pattern, escape="\\"),
                )
            )
        if person_type == "student":
            query = query.where(Person.id.in_(select(StudentProfile.person_id)))
        elif person_type == "guardian":
            query = query.where(Person.id.in_(select(GuardianProfile.person_id)))
        elif person_type == "staff":
            query = query.where(Person.id.in_(select(StaffProfile.person_id)))
        elif person_type == "visitor":
            query = query.where(Person.id.in_(select(VisitorProfile.person_id)))
        if staff_type is not None:
            query = query.where(
                Person.id.in_(
                    select(StaffProfile.person_id).where(
                        StaffProfile.staff_type == staff_type,
                    )
                )
            )

        if role is not None:
            query = query.where(
                Person.id.in_(
                    select(UserAccount.person_id)
                    .join(UserRole, UserRole.user_id == UserAccount.id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(Role.key == role)
                )
            )

        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        people = list(
            self.db.scalars(
                query.order_by(Person.created_at.desc(), Person.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        staff_types: dict[UUID, str] = {}
        types = self._types_for((person.id for person in people), staff_types=staff_types)
        ids = [person.id for person in people]
        roles = self._roles_for(ids)
        from app.team_management.projection import project_person_teams

        teams = project_person_teams(self.db, actor, ids) if actor is not None else {}
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
                    roles=roles[person.id],
                    staff_types=staff_types,
                ).model_copy(update={"team": teams[person.id]} if person.id in teams else {})
                for person in people
            ],
            page=page,
            page_size=page_size,
            total=total,
        )

    def count_people(self, *, actor: AuthContext | None = None) -> PersonCounts:
        """Return global counts for the personnel directory.

        Counts are based on active (non-deleted) identity records. A person can
        belong to more than one profile, so each profile count is intentionally
        independent rather than partitioning the total into mutually exclusive
        buckets.
        """
        active_people = Person.deleted_at.is_(None)
        total = self.db.scalar(
            select(func.count()).select_from(Person).where(active_people)
        ) or 0
        student = self.db.scalar(
            select(func.count())
            .select_from(StudentProfile)
            .join(Person, Person.id == StudentProfile.person_id)
            .where(active_people)
        ) or 0
        guardian = self.db.scalar(
            select(func.count())
            .select_from(GuardianProfile)
            .join(Person, Person.id == GuardianProfile.person_id)
            .where(active_people)
        ) or 0
        staff = self.db.scalar(
            select(func.count())
            .select_from(StaffProfile)
            .join(Person, Person.id == StaffProfile.person_id)
            .where(active_people)
        ) or 0
        visitor = self.db.scalar(
            select(func.count())
            .select_from(VisitorProfile)
            .join(Person, Person.id == VisitorProfile.person_id)
            .where(active_people)
        ) or 0
        coach = self.db.scalar(
            select(func.count())
            .select_from(StaffProfile)
            .join(Person, Person.id == StaffProfile.person_id)
            .where(active_people, StaffProfile.staff_type == "coach")
        ) or 0
        life_teacher = self.db.scalar(
            select(func.count())
            .select_from(StaffProfile)
            .join(Person, Person.id == StaffProfile.person_id)
            .where(active_people, StaffProfile.staff_type == "life_teacher")
        ) or 0
        customer_service = self.db.scalar(
            select(func.count())
            .select_from(StaffProfile)
            .join(Person, Person.id == StaffProfile.person_id)
            .where(active_people, StaffProfile.staff_type == "customer_service")
        ) or 0
        administration = self.db.scalar(
            select(func.count())
            .select_from(StaffProfile)
            .join(Person, Person.id == StaffProfile.person_id)
            .where(active_people, StaffProfile.staff_type == "administration")
        ) or 0
        other_staff = self.db.scalar(
            select(func.count())
            .select_from(StaffProfile)
            .join(Person, Person.id == StaffProfile.person_id)
            .where(active_people, StaffProfile.staff_type == "other")
        ) or 0
        role_counts = dict(
            self.db.execute(
                select(Role.key, func.count(func.distinct(Person.id)))
                .select_from(Person)
                .join(UserAccount, UserAccount.person_id == Person.id)
                .join(UserRole, UserRole.user_id == UserAccount.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(active_people, Role.key.in_(["team_member", "team_manager"]))
                .group_by(Role.key)
            ).tuples().all()
        )
        in_team = 0
        if actor is not None:
            from app.team_management.models import TeamMembership
            from app.team_management.projection import current_membership_read_filter
            in_team = (
                self.db.scalar(
                    select(func.count(func.distinct(Person.id)))
                    .join(
                        TeamMembership,
                        TeamMembership.person_id == Person.id,
                    )
                    .where(
                        active_people,
                        TeamMembership.ended_at.is_(None),
                        current_membership_read_filter(self.db, actor),
                    )
                )
                or 0
            )
        return PersonCounts(
            total=int(total),
            student=int(student),
            guardian=int(guardian),
            staff=int(staff),
            coach=int(coach),
            life_teacher=int(life_teacher),
            customer_service=int(customer_service),
            administration=int(administration),
            other_staff=int(other_staff),
            visitor=int(visitor),
            team_member=role_counts.get("team_member", 0),
            team_manager=role_counts.get("team_manager", 0),
            in_team=in_team,
        )

    def search_account_candidates(
        self,
        *,
        name: str,
        authorize_person_ids: Callable[[set[UUID]], set[UUID]],
        limit: int = 20,
    ) -> list[AccountCandidateSummary]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        batch_size = max(100, limit * 5)
        last_name: str | None = None
        last_id: UUID | None = None
        result: list[AccountCandidateSummary] = []
        while len(result) < limit:
            candidate_query = (
                select(Person.id, Person.full_name)
                .where(
                    Person.deleted_at.is_(None),
                    Person.id.not_in(select(UserAccount.person_id)),
                    Person.full_name.ilike(f"%{escaped}%", escape="\\"),
                )
                .order_by(Person.full_name, Person.id)
                .limit(batch_size)
            )
            if last_name is not None and last_id is not None:
                candidate_query = candidate_query.where(
                    or_(
                        Person.full_name > last_name,
                        and_(Person.full_name == last_name, Person.id > last_id),
                    )
                )
            candidate_rows = list(self.db.execute(candidate_query).tuples())
            if not candidate_rows:
                break
            last_id, last_name = candidate_rows[-1]
            batch_ids = {person_id for person_id, _full_name in candidate_rows}
            allowed_ids = authorize_person_ids(batch_ids) & batch_ids
            if allowed_ids:
                # Sensitive contact columns are selected only after authorization.
                people_by_id = {
                    person.id: person
                    for person in self.db.scalars(
                        select(Person).where(
                            Person.id.in_(allowed_ids),
                            Person.phone_ciphertext.is_not(None),
                            Person.phone_digest.is_not(None),
                            or_(
                                and_(
                                    Person.email_ciphertext.is_not(None),
                                    Person.email_digest.is_not(None),
                                ),
                                and_(
                                    Person.email_ciphertext.is_(None),
                                    Person.email_digest.is_(None),
                                    Person.email.is_not(None),
                                ),
                            ),
                        )
                    )
                }
                types = self._types_for(people_by_id)
                for person_id, _full_name in candidate_rows:
                    person = people_by_id.get(person_id)
                    if person is None or person.phone_ciphertext is None:
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
                    if len(result) == limit:
                        break
            if len(candidate_rows) < batch_size:
                break
        return result

    def get_person(self, person_id: UUID, *, actor: AuthContext | None = None) -> PersonDetail:
        person = self.db.scalar(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
        if person is None:
            raise PersonNotFound("person not found")
        detail = self._detail(person)
        if actor is not None:
            from app.team_management.projection import project_person_teams

            detail = detail.model_copy(
                update={
                    "team": project_person_teams(self.db, actor, [person_id])[person_id],
                }
            )
        return detail

    def _self_profile(self, person: Person) -> SelfProfile:
        summary = self._summary(person, self._types_for([person.id])[person.id])
        email = str(summary.email) if summary.email is not None else None
        return SelfProfile(
            person_id=person.id,
            full_name=person.full_name,
            nickname=person.nickname,
            person_types=summary.person_types,
            staff_type=summary.staff_type,
            sex=summary.sex,
            date_of_birth=person.date_of_birth,
            age_status=summary.age_status,
            masked_identity=summary.masked_identity,
            masked_phone=summary.masked_phone,
            masked_email=self._mask_email(email) if email is not None else None,
        )

    def get_self_profile(self, person_id: UUID) -> SelfProfile:
        person = self.db.scalar(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
        if person is None:
            raise PersonNotFound("person not found")
        return self._self_profile(person)

    def update_self_profile(
        self,
        person_id: UUID,
        input: SelfProfileUpdate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> SelfProfile:
        with write_transaction(self.db):
            person = self.db.scalar(
                select(Person)
                .where(Person.id == person_id, Person.deleted_at.is_(None))
                .with_for_update()
            )
            if person is None:
                raise PersonNotFound("person not found")
            nickname = input.nickname or None
            if person.nickname != nickname:
                person.nickname = nickname
                self.db.flush()
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="person.nickname.update",
                    resource_type="person",
                    resource_id=person.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={"person_id": str(person.id)},
                )
            result = self._self_profile(person)
        return result

    def _detail(self, person: Person) -> PersonDetail:
        types = self._types_for([person.id])[person.id]
        summary = self._summary(person, types)
        student = self.db.get(StudentProfile, person.id)
        visitor = self.db.get(VisitorProfile, person.id)
        return PersonDetail(
            **summary.model_dump(),
            student_number=student.student_number if student else None,
            student_status=cast(StudentStatus | None, student.status if student else None),
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
                locked_people = readiness_policy.lock_students(dependent_person_ids)
                person = next(
                    (
                        locked_person
                        for locked_person in locked_people
                        if locked_person.id == person_id
                    ),
                    None,
                )
                if person is None or person.deleted_at is not None:
                    raise PersonNotFound("person not found")
                fields = input.model_fields_set
                old_identity_digest = person.identity_digest
                old_phone_digest = person.phone_digest
                old_email_digest = person.email_digest
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
                normalized_phone = (
                    normalize_phone(input.phone)
                    if "phone" in fields and input.phone is not None
                    else None
                )
                normalized_email = (
                    normalize_email(str(input.email))
                    if "email" in fields and input.email is not None
                    else None
                )
                phone_digest = (
                    self.lookup.digest(normalized_phone)
                    if normalized_phone is not None
                    else None
                )
                if "phone" not in fields:
                    phone_digest = old_phone_digest
                email_digest = (
                    self.lookup.digest_domain("person-email", normalized_email)
                    if normalized_email is not None
                    else None
                )
                if "email" not in fields:
                    email_digest = old_email_digest
                lock_person_memberships(
                    self.db,
                    person_membership_lock_keys(
                        identity_digests={
                            digest
                            for digest in (old_identity_digest, identity_digest)
                            if digest
                        },
                        phone_digests={
                            digest
                            for digest in (old_phone_digest, phone_digest)
                            if digest
                        },
                        email_digests={
                            digest
                            for digest in (old_email_digest, email_digest)
                            if digest
                        },
                    ),
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
                    person.phone_ciphertext = (
                        self.cipher.encrypt(normalized_phone, field_name="phone")
                        if normalized_phone is not None
                        else None
                    )
                    person.phone_digest = phone_digest
                if "email" in fields:
                    person.email = None
                    person.email_ciphertext = (
                        self.cipher.encrypt(normalized_email, field_name="email")
                        if normalized_email is not None
                        else None
                    )
                    person.email_digest = email_digest
                    account = self.db.scalar(
                        select(UserAccount).where(UserAccount.person_id == person.id)
                    )
                    if account is not None:
                        account.email_normalized = None

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
        include_email: bool = True,
        roles: list[PersonRole] | None = None,
        staff_types: dict[UUID, str] | None = None,
    ) -> GuardianSummary:
        return GuardianSummary(
            **self._summary(
                guardian,
                person_types,
                projection=projection,
                include_email=include_email,
                roles=roles,
                staff_types=staff_types,
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
                if self.db.get(GuardianProfile, guardian_id) is None:
                    self.db.add(GuardianProfile(person_id=guardian_id))
                    self.db.flush()
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
        *,
        include_email: bool = True,
        authorized_guardian_ids: set[UUID] | None = None,
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
        if authorized_guardian_ids is not None:
            if not authorized_guardian_ids:
                return []
            query = query.where(
                GuardianRelationship.guardian_person_id.in_(authorized_guardian_ids)
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
        staff_types: dict[UUID, str] = {}
        types = self._types_for(people_by_id, staff_types=staff_types)
        roles = self._roles_for(people_by_id)
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
                include_email=include_email,
                roles=roles[person.id],
                staff_types=staff_types,
            )
            for relation, person in rows
        ]

    def list_guardian_person_ids(
        self,
        student_id: UUID,
        reference_date: date | None,
    ) -> list[UUID]:
        self._require_student(student_id)
        query = (
            select(GuardianRelationship.guardian_person_id)
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
                or_(
                    GuardianRelationship.valid_until.is_(None),
                    GuardianRelationship.valid_until >= reference_date,
                ),
            )
        return list(dict.fromkeys(self.db.scalars(query.order_by(GuardianRelationship.id))))

    def end_guardian_relationship(
        self,
        student_id: UUID,
        relationship_id: UUID,
        *,
        ended_by_user_id: UUID | None,
        reason: str,
        ended_at: datetime,
        request_id: str,
        include_email: bool = True,
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
                include_email=include_email,
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
