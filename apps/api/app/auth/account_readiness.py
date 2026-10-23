"""Formal account access dispatch. Legacy/family readiness is intentionally unchanged."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person, StudentProfile
from app.identity.readiness import (
    AgeStatus,
    GuardianReadinessPolicy,
    PersonReadinessProjection,
    is_minor,
)
from app.personnel_intake.eligibility import AssistedEligibilityPolicy
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.security.encryption import EnvelopeCipher, LookupDigest


class AccountAccessReadinessPolicy:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        self.db, self.cipher, self.lookup = db, cipher, lookup

    def evaluate_one(
        self, account: UserAccount, *, reference_date: date
    ) -> PersonReadinessProjection:
        return self.evaluate_many([account], reference_date=reference_date)[account.id]

    def evaluate_many(
        self, accounts: Sequence[UserAccount], *, reference_date: date
    ) -> dict[UUID, PersonReadinessProjection]:
        if not accounts:
            return {}
        if any(a.account_access_origin not in {"legacy", "assisted_activation"} for a in accounts):
            raise ApprovalEvidenceError()
        ids = {a.person_id for a in accounts}
        people = {
            p.id: p
            for p in self.db.scalars(
                select(Person).where(Person.id.in_(ids)).execution_options(populate_existing=True)
            )
        }
        legacy_ids = {a.person_id for a in accounts if a.account_access_origin == "legacy"}
        student_ids = (
            set(
                self.db.scalars(
                    select(StudentProfile.person_id).where(StudentProfile.person_id.in_(legacy_ids))
                )
            )
            if legacy_ids
            else set()
        )
        legacy = GuardianReadinessPolicy(self.db).evaluate_many(
            [people[i] for i in legacy_ids if i in people and people[i].deleted_at is None],
            student_ids=student_ids,
            reference_date=reference_date,
        )
        assisted = [a for a in accounts if a.account_access_origin == "assisted_activation"]
        facts = AssistedEligibilityPolicy(
            self.db, cipher=self.cipher, lookup=self.lookup
        ).evaluate_many_with_provenance(
            [a.person_id for a in assisted],
            reference_date=reference_date,
            original_approvals={a.person_id: a.assisted_approval_id for a in assisted},
        )
        result: dict[UUID, PersonReadinessProjection] = {}
        for account in accounts:
            person = people.get(account.person_id)
            age: AgeStatus = (
                "minor"
                if person is None or is_minor(person.date_of_birth, reference_date)
                else "adult"
            )
            if account.account_access_origin == "legacy":
                result[account.id] = legacy.get(
                    account.person_id, PersonReadinessProjection(age, "pending", ("person",))
                )
            else:
                eligibility = facts[account.person_id]
                result[account.id] = PersonReadinessProjection(
                    age,
                    ("ready" if age == "minor" else "not_applicable")
                    if eligibility.eligible
                    else "pending",
                    eligibility.missing,
                )
        return result
