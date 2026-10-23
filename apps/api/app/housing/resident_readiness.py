"""Pure housing eligibility. Never creates, enables, or suspends an account."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.identity.readiness import GuardianReadinessPolicy
from app.personnel_intake.readiness import AssistedReadinessPolicy
from app.security.encryption import EnvelopeCipher, LookupDigest


@dataclass(frozen=True)
class HousingResidentReadiness:
    housing_ready: bool
    account_ready: bool
    profile_ready: bool
    guardian_ready: bool
    missing: tuple[str, ...]


class HousingResidentReadinessPolicy:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        self.db = db
        self.assisted = AssistedReadinessPolicy(db, cipher=cipher, lookup=lookup)

    def evaluate_many(
        self, person_ids: Sequence[UUID], *, reference_date: date
    ) -> dict[UUID, HousingResidentReadiness]:
        ids = set(person_ids)
        if not ids:
            return {}
        rows = self.db.execute(
            select(Person, UserAccount, StudentProfile, StaffProfile, VisitorProfile)
            .outerjoin(UserAccount, UserAccount.person_id == Person.id)
            .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
            .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
            .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
            .where(Person.id.in_(ids))
            .execution_options(populate_existing=True)
        ).all()
        assisted_ids = [
            person.id
            for person, account, *_ in rows
            if account is None or account.account_access_origin == "assisted_activation"
        ]
        assisted = self.assisted.evaluate_many(assisted_ids, reference_date=reference_date)
        legacy = [row for row in rows if row[0].id not in assisted]
        guardians = GuardianReadinessPolicy(self.db).evaluate_many(
            [row[0] for row in legacy],
            student_ids={row[0].id for row in legacy if row[2] is not None},
            reference_date=reference_date,
        )
        email_accounts = set(
            self.db.scalars(
                select(AccountLoginIdentifier.account_id).where(
                    AccountLoginIdentifier.account_id.in_(
                        [row[1].id for row in rows if row[1] is not None]
                    ),
                    AccountLoginIdentifier.kind == "email",
                )
            )
        )
        result = {
            person_id: HousingResidentReadiness(False, False, False, False, ("profile",))
            for person_id in ids
        }
        for person, account, student, staff, visitor in rows:
            account_ready = (
                account is not None and account.status == "active" and account.id in email_accounts
            )
            if person.id in assisted:
                value = assisted[person.id]
                profile_ready = value.approval_valid and not any(
                    key != "guardian" for key in value.housing_missing
                )
                guardian_ready = value.approval_valid and "guardian" not in value.housing_missing
                result[person.id] = HousingResidentReadiness(
                    value.housing_ready,
                    account_ready,
                    profile_ready,
                    guardian_ready,
                    value.housing_missing,
                )
                continue
            complete = bool(
                person.deleted_at is None
                and person.full_name.strip()
                and person.identity_ciphertext
                and person.identity_digest
                and person.email_ciphertext
                and person.email_digest
                and person.sex
                and person.date_of_birth
            )
            if student is not None:
                complete = (
                    complete and student.status == "active" and bool(student.student_number.strip())
                )
            elif staff is not None:
                complete = complete and bool(staff.staff_type.strip())
            elif visitor is not None:
                complete = complete and bool(visitor.visit_reason.strip())
            else:
                complete = False
            guardian_ready = student is None or guardians[person.id].guardian_status != "pending"
            missing = tuple(
                key
                for key, ready in (
                    ("account", account_ready),
                    ("profile", complete),
                    ("guardian", guardian_ready),
                )
                if not ready
            )
            result[person.id] = HousingResidentReadiness(
                not missing, account_ready, complete, guardian_ready, missing
            )
        return result
