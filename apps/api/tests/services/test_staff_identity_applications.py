from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.identity.models import StaffProfile
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.rbac.models import Role, UserRole
from app.rbac.seed import ROLES
from app.registration.models import StaffIdentityApplication
from app.registration.staff_service import (
    StaffApplicationNotFound,
    StaffIdentityApplicationService,
    StaffReviewReasonInvalid,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
REQUEST_ID = "staff-identity-service-test"


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


@dataclass(frozen=True)
class StaffScenario:
    application_id: UUID
    person_id: UUID
    account_id: UUID


def _service(db: Session) -> StaffIdentityApplicationService:
    return StaffIdentityApplicationService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
        clock=lambda: NOW,
    )


def _pending_scenario(
    db: Session,
    *,
    staff_type: str = "coach",
    sequence: int = 901,
) -> StaffScenario:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    identity = IdentityService(db, cipher=cipher, lookup=lookup)
    phone = f"1380000{sequence:04d}"
    email = f"staff-{sequence}@example.com"
    person = identity.create_base_person(
        PersonBaseInput(
            full_name=f"工作人员申请人{sequence}",
            identity_number=make_cn_id("19900102", sequence),
            sex="male",
            date_of_birth=date(1990, 1, 2),
            phone=phone,
            email=email,
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
            login_phone=phone,
            email=email,
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
        requested_staff_type=staff_type,
        application_reason=f"申请担任{staff_type}岗位",
        status="pending",
    )
    db.add(application)
    db.commit()
    return StaffScenario(
        application_id=application.id,
        person_id=person.id,
        account_id=account.id,
    )


def _role_keys(db: Session, account_id: UUID) -> set[str]:
    return set(
        db.scalars(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == account_id)
        )
    )


def test_staff_applicant_has_no_staff_profile_or_role_before_review(
    db: Session,
) -> None:
    scenario = _pending_scenario(db)

    own_status = _service(db).self_status(scenario.account_id)

    assert db.get(StaffProfile, scenario.person_id) is None
    assert _role_keys(db, scenario.account_id) == {"registered_user"}
    assert own_status.application_id == scenario.application_id
    assert own_status.status == "pending"
    assert not hasattr(own_status, "review_reason")
    assert not hasattr(own_status, "reviewed_by_user_id")


@pytest.mark.parametrize(
    ("requested_type", "expected_role", "sequence"),
    [
        ("coach", "coach", 903),
        ("life_teacher", "life_teacher", 905),
        ("customer_service", "customer_service", 907),
        ("administration", "administration", 909),
        ("other", "staff", 911),
    ],
)
def test_approval_creates_exact_staff_profile_and_baseline_role(
    db: Session,
    requested_type: str,
    expected_role: str,
    sequence: int,
) -> None:
    scenario = _pending_scenario(
        db,
        staff_type=requested_type,
        sequence=sequence,
    )
    actor_id = scenario.account_id

    first = _service(db).approve(
        scenario.application_id,
        actor_user_id=actor_id,
        reason="资料核验通过",
        request_id=REQUEST_ID,
    )
    repeated = _service(db).approve(
        scenario.application_id,
        actor_user_id=actor_id,
        reason="重复请求不应改写首次决定",
        request_id=f"{REQUEST_ID}-repeat",
    )

    db.expire_all()
    profile = db.get(StaffProfile, scenario.person_id)
    stored = db.get(StaffIdentityApplication, scenario.application_id)
    assert profile is not None and profile.staff_type == requested_type
    assert _role_keys(db, scenario.account_id) == {
        "registered_user",
        expected_role,
    }
    assert "admin" not in _role_keys(db, scenario.account_id)
    assert stored is not None and stored.status == "approved"
    assert stored.review_reason == "资料核验通过"
    assert first == repeated
    assert first.assigned_role == expected_role
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action == "staff_identity_application.approve",
        )
    ) == 1


def test_rejection_preserves_base_account_and_is_idempotent(db: Session) -> None:
    scenario = _pending_scenario(db, staff_type="coach", sequence=913)

    first = _service(db).reject(
        scenario.application_id,
        actor_user_id=scenario.account_id,
        reason="资料暂不符合要求",
        request_id=REQUEST_ID,
    )
    repeated = _service(db).reject(
        scenario.application_id,
        actor_user_id=scenario.account_id,
        reason="重复请求不得改写原因",
        request_id=f"{REQUEST_ID}-repeat",
    )

    db.expire_all()
    account = db.get(UserAccount, scenario.account_id)
    stored = db.get(StaffIdentityApplication, scenario.application_id)
    assert first == repeated
    assert first.status == "rejected"
    assert account is not None and account.status == "active"
    assert db.get(StaffProfile, scenario.person_id) is None
    assert _role_keys(db, scenario.account_id) == {"registered_user"}
    assert stored is not None and stored.review_reason == "资料暂不符合要求"
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action == "staff_identity_application.reject",
        )
    ) == 1


def test_approval_accepts_no_review_reason(db: Session) -> None:
    scenario = _pending_scenario(db, staff_type="coach", sequence=921)

    result = _service(db).approve(
        scenario.application_id,
        actor_user_id=scenario.account_id,
        reason=None,
        request_id=REQUEST_ID,
    )

    db.expire_all()
    stored = db.get(StaffIdentityApplication, scenario.application_id)
    assert result.status == "approved"
    assert stored is not None and stored.review_reason is None
    assert db.get(StaffProfile, scenario.person_id) is not None
    assert _role_keys(db, scenario.account_id) == {"registered_user", "coach"}


@pytest.mark.parametrize(
    ("reason", "sequence"),
    [
        ("联系 13800009999 后通过", 915),
        ("联系 用户@example.com 后通过", 925),
        ("联系 user@例子.公司 后通过", 935),
        ("验证码 verification-code-123456 不得写入", 945),
        ("验证码 verification code 123456 不得写入", 955),
        ("请求头 x-registration-csrf-token-abc123 不得写入", 965),
    ],
)
def test_review_reason_rejects_sensitive_text_without_writes(
    db: Session,
    reason: str,
    sequence: int,
) -> None:
    scenario = _pending_scenario(db, sequence=sequence)

    with pytest.raises(StaffReviewReasonInvalid):
        _service(db).approve(
            scenario.application_id,
            actor_user_id=scenario.account_id,
            reason=reason,
            request_id=REQUEST_ID,
        )

    db.expire_all()
    stored = db.get(StaffIdentityApplication, scenario.application_id)
    assert stored is not None and stored.status == "pending"
    assert db.get(StaffProfile, scenario.person_id) is None
    assert _role_keys(db, scenario.account_id) == {"registered_user"}


def test_non_staff_account_has_no_self_application(db: Session) -> None:
    scenario = _pending_scenario(db, sequence=917)
    account = db.get(UserAccount, scenario.account_id)
    assert account is not None
    account.self_service_primary_purpose = "guardian"
    db.commit()

    with pytest.raises(StaffApplicationNotFound):
        _service(db).self_status(scenario.account_id)


def test_audit_failure_rolls_back_profile_role_and_review_transition(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _pending_scenario(db, sequence=919)

    def fail_audit(_service: AuditService, **_values: object) -> None:
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr(AuditService, "append", fail_audit)

    with pytest.raises(RuntimeError, match="audit persistence failed"):
        _service(db).approve(
            scenario.application_id,
            actor_user_id=scenario.account_id,
            reason="资料审核通过",
            request_id=REQUEST_ID,
        )

    db.expire_all()
    stored = db.get(StaffIdentityApplication, scenario.application_id)
    assert stored is not None and stored.status == "pending"
    assert stored.reviewed_by_user_id is None
    assert db.get(StaffProfile, scenario.person_id) is None
    assert _role_keys(db, scenario.account_id) == {"registered_user"}
