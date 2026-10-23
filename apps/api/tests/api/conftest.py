from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.identity.models import GuardianRelationship
from app.identity.schemas import GuardianCreate, StaffCreate, StudentCreate
from app.identity.service import IdentityService
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


@pytest.fixture
def platform_users(db: Session):
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
    )
    passwords = PasswordService()
    accounts = AccountService(
        db,
        identity_service=identity,
        passwords=passwords,
        reference_date_provider=lambda: date(2026, 7, 26),
    )

    people = {
        "admin": identity.create_staff(
            StaffCreate(
                full_name="平台管理员",
                identity_number=make_cn_id("19800102", 102),
                sex="female",
                date_of_birth=date(1980, 1, 2),
                phone="19990000001",
                email="admin@example.com",
                staff_type="administration",
            )
        ),
        "customer_service": identity.create_staff(
            StaffCreate(
                full_name="平台客服",
                identity_number=make_cn_id("19820102", 104),
                sex="female",
                date_of_birth=date(1982, 1, 2),
                phone="19990000004",
                email="cs@example.com",
                staff_type="customer_service",
            )
        ),
        "coach": identity.create_staff(
            StaffCreate(
                full_name="平台教练",
                identity_number=make_cn_id("19860102", 116),
                sex="female",
                date_of_birth=date(1986, 1, 2),
                phone="19990000003",
                email="coach@example.com",
                staff_type="coach",
            )
        ),
        "guardian": identity.create_guardian(
            GuardianCreate(
                full_name="学员家长",
                identity_number=make_cn_id("19840102", 103),
                sex="male",
                date_of_birth=date(1984, 1, 2),
                phone="19990000005",
                email="guardian@example.com",
            )
        ),
        "own_student": identity.create_student(
            StudentCreate(
                full_name="已绑定学员",
                identity_number=make_cn_id("20120102", 106),
                sex="female",
                date_of_birth=date(2012, 1, 2),
                student_number="API-S-001",
            )
        ),
        "other_student": identity.create_student(
            StudentCreate(
                full_name="其他学员",
                identity_number=make_cn_id("20130102", 108),
                sex="female",
                date_of_birth=date(2013, 1, 2),
                student_number="API-S-002",
            )
        ),
        "adult_without_account": identity.create_student(
            StudentCreate(
                full_name="待开通账号人员",
                identity_number=make_cn_id("19900102", 110),
                sex="female",
                date_of_birth=date(1990, 1, 2),
                student_number="API-S-003",
            )
        ),
        "self_student": identity.create_student(
            StudentCreate(
                full_name="自助账号学员",
                identity_number=make_cn_id("19950102", 112),
                sex="female",
                date_of_birth=date(1995, 1, 2),
                phone="13800000004",
                email="student@example.com",
                student_number="API-S-004",
            )
        ),
        "minor_student": identity.create_student(
            StudentCreate(
                full_name="监护就绪学员",
                identity_number=make_cn_id("20150102", 114),
                sex="female",
                date_of_birth=date(2015, 1, 2),
                phone="13800000005",
                email="minor-student@example.com",
                student_number="API-S-005",
            )
        ),
    }
    credentials = {
        "admin": ("19990000001", "AdminA1x2026", "admin"),
        "customer_service": (
            "19990000004",
            "CustA1x2026",
            "customer_service",
        ),
        "guardian": ("19990000005", "GuardA1x26", "guardian"),
        "self_student": ("13800000004", "StudA1x2026", "student"),
        "minor_student": ("13800000005", "MinorA1x26", "student"),
        "coach": ("19990000003", "CoachA1x2026", "coach"),
    }

    identity.add_guardian(
        people["minor_student"].id,
        people["guardian"].id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )

    account_rows: dict[str, UserAccount] = {}
    for key, (phone, password, role_key) in credentials.items():
        created = accounts.create_account(
            AccountCreate(
                person_id=people[key].id,
                login_phone=phone,
                email={
                    "customer_service": "cs@example.com",
                    "self_student": "student@example.com",
                    "minor_student": "minor-student@example.com",
                    "coach": "coach@example.com",
                }.get(key, f"{key}@example.com"),
            )
        )
        account = db.get(UserAccount, created.account.id)
        assert account is not None
        account.password_hash = passwords.hash(password)
        account.force_password_change = False
        db.add(UserRole(user_id=account.id, role_id=ROLES[role_key].id))
        db.commit()
        account_rows[key] = account

    identity.add_guardian(
        people["own_student"].id,
        people["guardian"].id,
        relationship="father",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )

    return {
        "people": people,
        "accounts": account_rows,
        "credentials": credentials,
        "identity_service": identity,
        "account_service": accounts,
    }


@dataclass(frozen=True)
class MinorLoginScenario:
    account_id: UUID
    phone: str
    password: str
    relationship: GuardianRelationship

    def login(self, client: TestClient) -> Response:
        return client.post(
            "/api/v1/auth/login",
            json={"phone": self.phone, "password": self.password},
        )


@dataclass(frozen=True)
class LoginResult:
    account_id: UUID
    csrf_token: str


def _login_as(
    client: TestClient,
    platform_users,
    key: str,
) -> LoginResult:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert response.status_code == 200
    return LoginResult(
        account_id=platform_users["accounts"][key].id,
        csrf_token=response.json()["csrf_token"],
    )


@pytest.fixture
def login_as_admin(db_client: TestClient, platform_users) -> LoginResult:
    return _login_as(db_client, platform_users, "admin")


@pytest.fixture
def login_as_coach(db_client: TestClient, platform_users) -> LoginResult:
    return _login_as(db_client, platform_users, "coach")


@pytest.fixture
def login_as_customer_service(
    db_client: TestClient,
    platform_users,
) -> LoginResult:
    return _login_as(db_client, platform_users, "customer_service")


@pytest.fixture
def minor_login(platform_users, db: Session) -> MinorLoginScenario:
    account = platform_users["accounts"]["minor_student"]
    phone, password, _ = platform_users["credentials"]["minor_student"]
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.student_person_id
            == platform_users["people"]["minor_student"].id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    return MinorLoginScenario(account.id, phone, password, relationship)
