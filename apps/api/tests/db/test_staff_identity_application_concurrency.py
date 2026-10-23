from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.identity.models import Person, StaffProfile
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.registration.models import StaffIdentityApplication
from app.registration.staff_service import StaffIdentityApplicationService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
KEY = bytes(range(32))


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _service(db: Session) -> StaffIdentityApplicationService:
    return StaffIdentityApplicationService(
        db,
        cipher=EnvelopeCipher(KEY),
        lookup=LookupDigest(KEY),
        clock=lambda: NOW,
    )


def _setup(engine: Engine) -> tuple[UUID, UUID, UUID]:
    with Session(engine) as db:
        identity = IdentityService(
            db,
            cipher=EnvelopeCipher(KEY),
            lookup=LookupDigest(KEY),
        )
        person = identity.create_base_person(
            PersonBaseInput(
                full_name="并发工作人员申请人",
                identity_number=make_cn_id("19900102", 941),
                sex="male",
                date_of_birth=date(1990, 1, 2),
                phone="13800000941",
                email="staff-race-941@example.com",
            )
        )
        created = AccountService(
            db,
            identity_service=identity,
            passwords=PasswordService(),
            reference_date_provider=lambda: NOW.date(),
        ).create_account(
            AccountCreate(
                person_id=person.id,
                login_phone="13800000941",
                email="staff-race-941@example.com",
            )
        )
        account = db.get(UserAccount, created.account.id)
        assert account is not None
        account.force_password_change = False
        account.self_service_primary_purpose = "staff_applicant"
        db.add(
            UserRole(
                user_id=account.id,
                role_id=ROLES["registered_user"].id,
            )
        )
        application = StaffIdentityApplication(
            person_id=person.id,
            requested_staff_type="coach",
            application_reason="申请成为并发测试教练",
            status="pending",
        )
        db.add(application)
        db.commit()
        return application.id, person.id, account.id


def test_two_concurrent_approvals_are_idempotent_and_sessions_remain_usable(
    postgres_engine: Engine,
) -> None:
    application_id, person_id, account_id = _setup(postgres_engine)
    barrier = threading.Barrier(2)

    def approve(index: int) -> tuple[str, str, str | None, bool]:
        with Session(postgres_engine) as db:
            barrier.wait(timeout=5)
            result = _service(db).approve(
                application_id,
                actor_user_id=account_id,
                reason="并发审批资料通过",
                request_id=f"staff-application-race-{index}",
            )
            usable = (
                db.scalar(select(func.count()).select_from(StaffIdentityApplication))
                or 0
            ) >= 1
            return (
                str(result.application_id),
                result.status,
                result.assigned_role,
                usable,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(approve, range(2)))

        assert results[0][:3] == results[1][:3]
        assert results[0][1:] == ("approved", "coach", True)
        assert all(result[3] for result in results)
        with Session(postgres_engine) as verification:
            stored = verification.get(StaffIdentityApplication, application_id)
            profile = verification.get(StaffProfile, person_id)
            assert stored is not None and stored.status == "approved"
            assert profile is not None and profile.staff_type == "coach"
            assert verification.scalar(
                select(func.count(UserRole.user_id)).where(
                    UserRole.user_id == account_id,
                    UserRole.role_id == ROLES["coach"].id,
                )
            ) == 1
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == application_id,
                    AuditEvent.action == "staff_identity_application.approve",
                )
            ) == 1
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(AuditEvent).where(AuditEvent.resource_id == application_id)
            )
            cleanup.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(StaffIdentityApplication).where(
                    StaffIdentityApplication.id == application_id
                )
            )
            cleanup.execute(
                delete(StaffProfile).where(StaffProfile.person_id == person_id)
            )
            cleanup.execute(delete(UserRole).where(UserRole.user_id == account_id))
            cleanup.execute(
                delete(AccountLoginIdentifier).where(
                    AccountLoginIdentifier.account_id == account_id
                )
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id == account_id))
            cleanup.execute(delete(Person).where(Person.id == person_id))
