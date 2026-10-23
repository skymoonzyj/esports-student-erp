from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.sql import ColumnElement

from app.audit.service import AuditService
from app.auth.models import AuthSession, UserAccount
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import Role, UserRole

AgeStatus = Literal["adult", "minor"]
GuardianStatus = Literal["ready", "pending", "not_applicable"]
GuardianIssueCode = Literal[
    "guardian_underage",
    "guardian_profile_incomplete",
    "guardian_account_missing",
    "guardian_account_disabled",
    "guardian_role_missing",
    "relationship_not_started",
    "relationship_expired",
    "relationship_ended",
]


def is_minor(birth_date: date, reference_date: date) -> bool:
    try:
        eighteenth_birthday = birth_date.replace(year=birth_date.year + 18)
    except ValueError:
        eighteenth_birthday = date(birth_date.year + 18, 2, 28)
    return reference_date < eighteenth_birthday


@dataclass(frozen=True)
class GuardianIssue:
    relationship_id: UUID
    guardian_person_id: UUID
    account_id: UUID | None
    reason_codes: tuple[GuardianIssueCode, ...]


@dataclass(frozen=True)
class PersonReadinessProjection:
    age_status: AgeStatus
    guardian_status: GuardianStatus
    missing: tuple[str, ...]
    issues: tuple[GuardianIssue, ...] = ()
    active_relationship_ids: tuple[UUID, ...] = ()


class GuardianReadinessPolicy:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._lock_transaction: tuple[
            SessionTransaction | None,
            SessionTransaction | None,
        ] | None = None
        self._locked_person_ids: set[UUID] = set()
        self._locked_guardian_ids: set[UUID] = set()

    def _refresh_lock_transaction(self) -> None:
        transaction = (self.db.get_transaction(), self.db.get_nested_transaction())
        if transaction != self._lock_transaction:
            self._lock_transaction = transaction
            self._locked_person_ids.clear()
            self._locked_guardian_ids.clear()

    @staticmethod
    def _guardian_profile_complete_expression() -> ColumnElement[bool]:
        phone_absent_expression = and_(
            Person.phone_ciphertext.is_(None),
            Person.phone_digest.is_(None),
        )
        phone_valid_expression = and_(
            Person.phone_ciphertext.is_not(None),
            Person.phone_digest.is_not(None),
            func.length(Person.phone_ciphertext) > 0,
            func.length(Person.phone_digest) > 0,
        )
        email_absent_expression = and_(
            Person.email_ciphertext.is_(None),
            Person.email_digest.is_(None),
            Person.email.is_(None),
        )
        encrypted_email_valid_expression = and_(
            Person.email.is_(None),
            Person.email_ciphertext.is_not(None),
            Person.email_digest.is_not(None),
            func.length(Person.email_ciphertext) > 0,
            func.length(Person.email_digest) > 0,
        )
        legacy_email_valid_expression = and_(
            Person.email_ciphertext.is_(None),
            Person.email_digest.is_(None),
            Person.email.is_not(None),
            func.length(func.trim(Person.email)) > 0,
        )
        return and_(
            Person.deleted_at.is_(None),
            func.length(func.trim(Person.full_name)) > 0,
            func.length(Person.identity_ciphertext) > 0,
            func.length(Person.identity_digest) > 0,
            or_(phone_absent_expression, phone_valid_expression),
            or_(
                email_absent_expression,
                encrypted_email_valid_expression,
                legacy_email_valid_expression,
            ),
            or_(
                phone_valid_expression,
                encrypted_email_valid_expression,
                legacy_email_valid_expression,
            ),
        )

    def guardian_profile_complete(self, person_id: UUID) -> bool:
        return bool(
            self.db.scalar(
                select(self._guardian_profile_complete_expression()).where(
                    Person.id == person_id
                )
            )
        )

    @staticmethod
    def _guardian_lock_key(guardian_person_id: UUID) -> int:
        unsigned = (guardian_person_id.int >> 64) ^ (
            guardian_person_id.int & ((1 << 64) - 1)
        )
        return unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned

    def lock_guardian_relationships(self, guardian_person_id: UUID) -> None:
        self._refresh_lock_transaction()
        if guardian_person_id in self._locked_guardian_ids:
            return
        self.db.execute(
            select(
                func.pg_advisory_xact_lock(
                    self._guardian_lock_key(guardian_person_id)
                )
            )
        )
        self._refresh_lock_transaction()
        self._locked_guardian_ids.add(guardian_person_id)

    def lock_students(self, student_person_ids: Iterable[UUID]) -> list[Person]:
        self._refresh_lock_transaction()
        requested_ids = set(student_person_ids)
        ordered_ids = sorted(requested_ids - self._locked_person_ids)
        if not ordered_ids:
            return []
        if self._locked_person_ids and ordered_ids[0] < max(self._locked_person_ids):
            raise RuntimeError("person locks must be acquired in globally sorted order")
        people = list(
            self.db.scalars(
                select(Person)
                .where(Person.id.in_(ordered_ids))
                .order_by(Person.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        self._refresh_lock_transaction()
        self._locked_person_ids.update(person.id for person in people)
        return people

    def lock_accounts(self, person_ids: Iterable[UUID]) -> list[UserAccount]:
        ordered_person_ids = sorted(set(person_ids))
        if not ordered_person_ids:
            return []
        return list(
            self.db.scalars(
                select(UserAccount)
                .where(UserAccount.person_id.in_(ordered_person_ids))
                .order_by(UserAccount.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def guardian_and_dependent_person_ids(
        self,
        guardian_person_id: UUID,
    ) -> tuple[UUID, ...]:
        self.lock_guardian_relationships(guardian_person_id)
        student_ids = set(
            self.db.scalars(
                select(GuardianRelationship.student_person_id).where(
                    GuardianRelationship.guardian_person_id == guardian_person_id,
                )
            )
        )
        return tuple(sorted({guardian_person_id, *student_ids}))

    def evaluate_one(
        self,
        person_id: UUID,
        reference_date: date,
        *,
        include_issues: bool = False,
    ) -> PersonReadinessProjection:
        person = self.db.get(Person, person_id)
        if person is None or person.deleted_at is not None:
            raise ValueError("person not found")
        is_student = self.db.get(StudentProfile, person_id) is not None
        return self.evaluate_many(
            [person],
            student_ids={person_id} if is_student else set(),
            reference_date=reference_date,
            include_issues=include_issues,
        )[person_id]

    def disable_student_account_if_pending(
        self,
        student_person_id: UUID,
        *,
        reference_date: date,
        revoked_at: datetime,
        audit_actor_user_id: UUID | None,
        audit_request_id: str,
        locked_account: UserAccount | None = None,
    ) -> UUID | None:
        self.lock_students([student_person_id])
        projection = self.evaluate_one(student_person_id, reference_date)
        if projection.age_status != "minor" or projection.guardian_status != "pending":
            return None
        account = locked_account
        if account is None:
            accounts = self.lock_accounts([student_person_id])
            account = accounts[0] if accounts else None
        elif account.person_id != student_person_id:
            raise ValueError("locked account does not belong to student")
        if account is None or account.status != "active":
            return None
        account.status = "disabled"
        self.db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == account.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        AuditService(self.db).append(
            actor_user_id=audit_actor_user_id,
            action="account.auto_disable.guardian_missing",
            resource_type="account",
            resource_id=account.id,
            purpose=None,
            outcome="allowed",
            request_id=audit_request_id,
            metadata={"error_code": "minor_guardian_required"},
        )
        return account.id

    def disable_unready_dependents(
        self,
        guardian_person_id: UUID,
        *,
        reference_date: date,
        revoked_at: datetime,
        audit_actor_user_id: UUID | None,
        audit_request_id: str,
        dependent_person_ids: Iterable[UUID] | None = None,
        locked_accounts: Iterable[UserAccount] | None = None,
        additional_session_account_ids: Iterable[UUID] = (),
    ) -> tuple[UUID, ...]:
        self.lock_guardian_relationships(guardian_person_id)
        person_ids = tuple(
            sorted(
                set(
                    dependent_person_ids
                    if dependent_person_ids is not None
                    else self.guardian_and_dependent_person_ids(guardian_person_id)
                )
            )
        )
        self.lock_students(person_ids)
        accounts = sorted(
            list(locked_accounts)
            if locked_accounts is not None
            else self.lock_accounts(person_ids),
            key=lambda account: account.id,
        )
        student_ids = {person_id for person_id in person_ids if person_id != guardian_person_id}
        people = [
            person
            for person_id in sorted(student_ids)
            if (person := self.db.get(Person, person_id)) is not None
            and person.deleted_at is None
        ]
        projections = self.evaluate_many(
            people,
            student_ids=student_ids,
            reference_date=reference_date,
        )
        pending_student_ids = {
            person_id
            for person_id, projection in projections.items()
            if projection.age_status == "minor" and projection.guardian_status == "pending"
        }
        disabled_accounts = [
            account
            for account in accounts
            if account.person_id in pending_student_ids and account.status == "active"
        ]
        for account in disabled_accounts:
            account.status = "disabled"

        session_account_ids = {
            *(account.id for account in disabled_accounts),
            *additional_session_account_ids,
        }
        if session_account_ids:
            self.db.execute(
                update(AuthSession)
                .where(
                    AuthSession.user_id.in_(sorted(session_account_ids)),
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            )
        for account in disabled_accounts:
            AuditService(self.db).append(
                actor_user_id=audit_actor_user_id,
                action="account.auto_disable.guardian_missing",
                resource_type="account",
                resource_id=account.id,
                purpose=None,
                outcome="allowed",
                request_id=audit_request_id,
                metadata={"error_code": "minor_guardian_required"},
            )
        return tuple(account.id for account in disabled_accounts)

    def evaluate_many(
        self,
        people: list[Person],
        *,
        student_ids: set[UUID],
        reference_date: date,
        include_issues: bool = False,
    ) -> dict[UUID, PersonReadinessProjection]:
        if not people:
            return {}

        person_ids = {person.id for person in people}
        relevant_student_ids = person_ids & student_ids
        relationship_rows: list[tuple[GuardianRelationship, UUID, date, bool]] = []
        if relevant_student_ids:
            profile_complete_expression = (
                self._guardian_profile_complete_expression().label(
                    "guardian_profile_complete"
                )
            )
            relationship_query = (
                select(
                    GuardianRelationship,
                    Person.id,
                    Person.date_of_birth,
                    profile_complete_expression,
                )
                .join(Person, Person.id == GuardianRelationship.guardian_person_id)
                .where(GuardianRelationship.student_person_id.in_(relevant_student_ids))
            )
            if not include_issues:
                relationship_query = relationship_query.where(
                    GuardianRelationship.ended_at.is_(None),
                    GuardianRelationship.valid_from <= reference_date,
                    (
                        GuardianRelationship.valid_until.is_(None)
                        | (GuardianRelationship.valid_until >= reference_date)
                    ),
                )
            relationship_rows = [
                (
                    relationship,
                    guardian_person_id,
                    guardian_birth_date,
                    bool(guardian_profile_complete),
                )
                for (
                    relationship,
                    guardian_person_id,
                    guardian_birth_date,
                    guardian_profile_complete,
                ) in self.db.execute(
                    relationship_query.order_by(
                        GuardianRelationship.student_person_id,
                        GuardianRelationship.id,
                    )
                ).tuples()
            ]

        guardian_ids = {
            guardian_person_id for _, guardian_person_id, _, _ in relationship_rows
        }
        accounts: dict[UUID, UserAccount] = {}
        if guardian_ids:
            accounts = {
                account.person_id: account
                for account in self.db.scalars(
                    select(UserAccount).where(UserAccount.person_id.in_(guardian_ids))
                )
            }

        account_ids = {account.id for account in accounts.values()}
        guardian_role_users: set[UUID] = set()
        if account_ids:
            guardian_role_users = set(
                self.db.scalars(
                    select(UserRole.user_id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(Role.key == "guardian", UserRole.user_id.in_(account_ids))
                )
            )

        relations_by_student: dict[
            UUID,
            list[tuple[GuardianRelationship, UUID, date, bool]],
        ] = {}
        for relationship, guardian_person_id, guardian_birth_date, is_profile_complete in (
            relationship_rows
        ):
            relations_by_student.setdefault(relationship.student_person_id, []).append(
                (
                    relationship,
                    guardian_person_id,
                    guardian_birth_date,
                    is_profile_complete,
                )
            )

        output: dict[UUID, PersonReadinessProjection] = {}
        for person in people:
            age_status: AgeStatus = (
                "minor" if is_minor(person.date_of_birth, reference_date) else "adult"
            )
            relationships = relations_by_student.get(person.id, [])
            if person.id not in student_ids or age_status == "adult":
                active_relationship_ids = tuple(
                    relationship.id
                    for relationship, _, _, _ in relationships
                    if relationship.ended_at is None
                    and relationship.valid_from <= reference_date
                    and (
                        relationship.valid_until is None
                        or relationship.valid_until >= reference_date
                    )
                )
                output[person.id] = PersonReadinessProjection(
                    age_status=age_status,
                    guardian_status="not_applicable",
                    missing=(),
                    active_relationship_ids=active_relationship_ids,
                )
                continue

            issues: list[GuardianIssue] = []
            valid_relationship_ids: list[UUID] = []
            for (
                relationship,
                guardian_person_id,
                guardian_birth_date,
                is_profile_complete,
            ) in relationships:
                account = accounts.get(guardian_person_id)
                reasons: list[GuardianIssueCode] = []
                if relationship.ended_at is not None:
                    reasons.append("relationship_ended")
                elif relationship.valid_from > reference_date:
                    reasons.append("relationship_not_started")
                elif (
                    relationship.valid_until is not None
                    and relationship.valid_until < reference_date
                ):
                    reasons.append("relationship_expired")
                if is_minor(guardian_birth_date, reference_date):
                    reasons.append("guardian_underage")
                if not is_profile_complete:
                    reasons.append("guardian_profile_incomplete")
                if account is None:
                    reasons.append("guardian_account_missing")
                elif account.status != "active":
                    reasons.append("guardian_account_disabled")
                elif account.id not in guardian_role_users:
                    reasons.append("guardian_role_missing")

                if reasons:
                    if include_issues:
                        issues.append(
                            GuardianIssue(
                                relationship_id=relationship.id,
                                guardian_person_id=guardian_person_id,
                                account_id=account.id if account is not None else None,
                                reason_codes=tuple(reasons),
                            )
                        )
                else:
                    valid_relationship_ids.append(relationship.id)

            has_valid_guardian = bool(valid_relationship_ids)
            output[person.id] = PersonReadinessProjection(
                age_status="minor",
                guardian_status="ready" if has_valid_guardian else "pending",
                missing=() if has_valid_guardian else ("guardian",),
                issues=tuple(issues),
                active_relationship_ids=tuple(valid_relationship_ids),
            )

        return output
