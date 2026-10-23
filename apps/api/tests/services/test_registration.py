from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from app.auth.models import RegistrationRequest, UserAccount
from app.auth.registration import RegistrationCodeInvalid, RegistrationService
from app.identity.models import StudentProfile
from app.rbac.models import Role, UserRole
from app.security.passwords import PasswordService


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


class FakeSender:
    def __init__(self) -> None:
        self.codes: list[str] = []

    def send_verification(self, *, recipient: str, code: str, expires_at: datetime) -> None:
        self.codes.append(code)


def registration_service(platform_users, db, sender: FakeSender, *, today: date = date(2026, 7, 26)):
    identity = platform_users["identity_service"]
    return RegistrationService(
        db,
        cipher=identity.cipher,
        lookup=identity.lookup,
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        email_sender=sender,
        reference_date_provider=lambda: today,
        clock=lambda: datetime(2026, 7, 26, 8, 0, tzinfo=UTC),
    )


def test_email_verification_creates_active_adult_student_with_role(platform_users, db) -> None:
    sender = FakeSender()
    service = registration_service(platform_users, db, sender)
    started = service.start(
        full_name="自助成人学员",
        identity_number=make_cn_id("19990102", 181),
        sex="male",
        date_of_birth=date(1999, 1, 2),
        phone="13900139181",
        email="self-registration-181@example.test",
        password="StrongA1",
        person_type="student",
    )

    result = service.verify(registration_id=started.registration_id, code=sender.codes[-1])
    account = db.get(UserAccount, result.account_id)
    assert result.account_status == "active"
    assert result.requires_guardian is False
    assert account is not None and account.status == "active"
    assert db.scalar(
        select(UserRole).join(Role).where(UserRole.user_id == result.account_id, Role.key == "student")
    ) is not None
    request = db.get(RegistrationRequest, started.registration_id)
    assert request is not None and request.status == "completed"


def test_minor_registration_stays_disabled_until_guardian(platform_users, db) -> None:
    sender = FakeSender()
    service = registration_service(platform_users, db, sender, today=date(2026, 7, 26))
    started = service.start(
        full_name="自助未成年学员",
        identity_number=make_cn_id("20140102", 182),
        sex="female",
        date_of_birth=date(2014, 1, 2),
        phone="13900139182",
        email="self-registration-182@example.test",
        password="StrongA1",
        person_type="student",
    )

    result = service.verify(registration_id=started.registration_id, code=sender.codes[-1])
    profile = db.get(StudentProfile, result.person_id)
    account = db.get(UserAccount, result.account_id)
    assert result.requires_guardian is True
    assert result.account_status == "disabled"
    assert profile is not None and profile.status == "draft"
    assert account is not None and account.status == "disabled"


def test_staff_registration_is_created_disabled_for_admin_review(platform_users, db) -> None:
    sender = FakeSender()
    service = registration_service(platform_users, db, sender)
    started = service.start(
        full_name="自助教练申请",
        identity_number=make_cn_id("19940102", 184),
        sex="female",
        date_of_birth=date(1994, 1, 2),
        phone="13900139184",
        email="self-registration-184@example.test",
        password="StrongA1",
        person_type="staff",
        staff_type="coach",
    )

    result = service.verify(registration_id=started.registration_id, code=sender.codes[-1])
    account = db.get(UserAccount, result.account_id)
    assert result.account_status == "disabled"
    assert account is not None and account.status == "disabled"
    assert db.scalar(
        select(UserRole).join(Role).where(UserRole.user_id == result.account_id, Role.key == "coach")
    ) is not None


def test_invalid_code_is_counted_and_does_not_create_account(platform_users, db) -> None:
    sender = FakeSender()
    service = registration_service(platform_users, db, sender)
    started = service.start(
        full_name="验证码测试",
        identity_number=make_cn_id("19990102", 183),
        sex="male",
        date_of_birth=date(1999, 1, 2),
        phone="13900139183",
        email="self-registration-183@example.test",
        password="StrongA1",
        person_type="guardian",
    )

    for _ in range(4):
        try:
            service.verify(registration_id=started.registration_id, code="000000")
        except RegistrationCodeInvalid:
            pass
    try:
        service.verify(registration_id=started.registration_id, code="000000")
    except RegistrationCodeInvalid:
        pass
    request = db.get(RegistrationRequest, started.registration_id)
    assert request is not None and request.status == "expired"
    assert db.scalar(select(UserAccount).where(UserAccount.email_normalized == "self-registration-183@example.test")) is None
