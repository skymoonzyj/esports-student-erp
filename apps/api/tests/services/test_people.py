from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, delete, event, func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.schemas import (
    GuardianCreate,
    PersonUpdate,
    StaffCreate,
    StudentCreate,
    VisitorCreate,
)
from app.identity.service import (
    IdentityAlreadyExists,
    IdentityService,
    StudentNumberAlreadyExists,
)
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

STUDENT_PAYLOAD = {
    "full_name": "测试学员",
    "identity_number": "11010519491231002X",
    "sex": "female",
    "date_of_birth": "1949-12-31",
    "phone": "13800138000",
    "email": "student@example.com",
    "student_number": "S20260001",
}

STAFF_WITH_SAME_ID = {
    "full_name": "重复人员",
    "identity_number": "11010519491231002X",
    "sex": "female",
    "date_of_birth": "1949-12-31",
    "phone": "13900139000",
    "email": "staff@example.com",
    "staff_type": "coach",
}


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


@pytest.fixture
def identity_service(db: Session) -> IdentityService:
    return IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(reversed(range(32)))),
    )


def test_create_student_and_search_by_phone(identity_service: IdentityService, db: Session) -> None:
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    found = identity_service.search_people(phone="13800138000", page=1, page_size=20)

    assert [item.id for item in found.items] == [person.id]
    assert found.items[0].masked_phone == "138****8000"
    assert found.items[0].masked_identity == "110105********002X"
    assert found.items[0].person_types == ["student"]
    assert "ciphertext" not in found.items[0].model_dump_json()
    assert "digest" not in found.items[0].model_dump_json()
    stored = db.get(Person, person.id)
    assert stored is not None
    assert "11010519491231002X" not in stored.identity_ciphertext
    assert "13800138000" not in (stored.phone_ciphertext or "")


def test_duplicate_identity_is_rejected(identity_service: IdentityService) -> None:
    identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))

    with pytest.raises(IdentityAlreadyExists) as error:
        identity_service.create_staff(StaffCreate(**STAFF_WITH_SAME_ID))
    assert error.value.code == "identity_already_exists"


def test_profile_creation_and_guardian_without_empty_profile(
    identity_service: IdentityService,
    db: Session,
) -> None:
    guardian_id = make_cn_id("19800102", 101)
    guardian = identity_service.create_guardian(
        GuardianCreate(
            full_name="测试监护人",
            identity_number=guardian_id,
            sex="male",
            date_of_birth=date(1980, 1, 2),
            phone="13900139001",
            email="guardian@example.com",
        )
    )
    staff = identity_service.create_staff(
        StaffCreate(
            full_name="测试教练",
            identity_number=make_cn_id("19900102", 103),
            sex="male",
            date_of_birth=date(1990, 1, 2),
            phone="13900139002",
            email="coach@example.com",
            staff_type="coach",
        )
    )
    visitor = identity_service.create_visitor(
        VisitorCreate(
            full_name="测试访客",
            identity_number=make_cn_id("19920102", 105),
            sex="male",
            date_of_birth=date(1992, 1, 2),
            phone="13900139003",
            email="visitor@example.com",
            visit_reason="探望学员",
        )
    )

    assert guardian.person_types == ["guardian"]
    assert identity_service.get_person(guardian.id).person_types == ["guardian"]
    guardian_results = identity_service.search_people(
        person_type="guardian",
        page=1,
        page_size=20,
    )
    assert [item.id for item in guardian_results.items] == [guardian.id]
    assert db.get(StudentProfile, guardian.id) is None
    assert db.get(StaffProfile, staff.id).staff_type == "coach"  # type: ignore[union-attr]
    assert db.get(VisitorProfile, visitor.id).visit_reason == "探望学员"  # type: ignore[union-attr]


def test_duplicate_student_number_rolls_back_person_insert(
    identity_service: IdentityService,
    db: Session,
) -> None:
    identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    second = dict(STUDENT_PAYLOAD)
    second.update(
        full_name="另一学员",
        identity_number=make_cn_id("20000102", 102),
        sex="female",
        date_of_birth="2000-01-02",
        phone="13800138001",
        email="other@example.com",
    )

    with pytest.raises(StudentNumberAlreadyExists):
        identity_service.create_student(StudentCreate(**second))

    assert db.scalar(select(func.count(Person.id))) == 1


def test_name_search_escapes_sql_wildcards(identity_service: IdentityService) -> None:
    first = dict(STUDENT_PAYLOAD)
    first["full_name"] = "百分%学员"
    identity_service.create_student(StudentCreate(**first))
    second = dict(STUDENT_PAYLOAD)
    second.update(
        full_name="百分X学员",
        identity_number=make_cn_id("20000102", 102),
        sex="female",
        date_of_birth="2000-01-02",
        phone="13800138001",
        email="other@example.com",
        student_number="S20260002",
    )
    identity_service.create_student(StudentCreate(**second))

    found = identity_service.search_people(name="%", page=1, page_size=100)

    assert [item.full_name for item in found.items] == ["百分%学员"]


def test_update_phone_replaces_encrypted_value_and_lookup_digest(
    identity_service: IdentityService,
) -> None:
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))

    updated = identity_service.update_person(
        person.id,
        PersonUpdate(phone="13900139009"),
        actor_user_id=None,
        request_id="service-test",
    )

    assert updated.masked_phone == "139****9009"
    assert identity_service.search_people(phone="13800138000", page=1, page_size=20).total == 0
    assert identity_service.search_people(phone="13900139009", page=1, page_size=20).total == 1


def test_soft_deleted_people_are_omitted(identity_service: IdentityService, db: Session) -> None:
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    stored = db.get(Person, person.id)
    assert stored is not None
    stored.deleted_at = datetime.now(UTC)
    db.flush()

    assert identity_service.search_people(page=1, page_size=20).total == 0


def test_write_after_read_autobegin_is_committed(postgres_engine: Engine) -> None:
    first_number = "TX-S-001"
    second_number = "TX-S-002"
    created_ids = []
    with Session(postgres_engine) as session:
        service = IdentityService(
            session,
            cipher=EnvelopeCipher(bytes(range(32))),
            lookup=LookupDigest(bytes(reversed(range(32)))),
        )
        first = dict(STUDENT_PAYLOAD)
        first.update(
            identity_number=make_cn_id("20010102", 102),
            date_of_birth="2001-01-02",
            student_number=first_number,
            phone="13700137001",
        )
        created_ids.append(service.create_student(StudentCreate(**first)).id)
        service.search_people(page=1, page_size=20)
        second = dict(STUDENT_PAYLOAD)
        second.update(
            identity_number=make_cn_id("20020102", 104),
            date_of_birth="2002-01-02",
            student_number=second_number,
            phone="13700137002",
        )
        created_ids.append(service.create_student(StudentCreate(**second)).id)

    try:
        with Session(postgres_engine) as verification:
            persisted = verification.scalar(
                select(func.count(StudentProfile.person_id)).where(
                    StudentProfile.student_number.in_([first_number, second_number])
                )
            )
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(StudentProfile).where(StudentProfile.person_id.in_(created_ids))
            )
            cleanup.execute(delete(Person).where(Person.id.in_(created_ids)))

    assert persisted == 2


@pytest.mark.parametrize("page_size", [0, 101])
def test_page_size_is_bounded(identity_service: IdentityService, page_size: int) -> None:
    with pytest.raises(ValueError, match="page_size must be between 1 and 100"):
        identity_service.search_people(page=1, page_size=page_size)


def test_people_readiness_projection_has_constant_query_count(
    identity_service: IdentityService,
    db: Session,
) -> None:
    student_ids = []
    for index in range(100):
        student = identity_service.create_student(
            StudentCreate(
                full_name=f"批量学员 {index:03d}",
                identity_number=make_cn_id("20120102", 300 + index),
                sex="female" if index % 2 == 0 else "male",
                date_of_birth=date(2012, 1, 2),
                student_number=f"BATCH-{index:03d}",
            )
        )
        student_ids.append(student.id)

    guardian = identity_service.create_guardian(
        GuardianCreate(
            full_name="批量有效监护人",
            identity_number=make_cn_id("19800102", 450),
            sex="female",
            date_of_birth=date(1980, 1, 2),
            phone="13900139450",
            email="batch-guardian@example.com",
        )
    )
    accounts = AccountService(
        db,
        identity_service=identity_service,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 27),
    )
    created = accounts.create_account(AccountCreate(person_id=guardian.id))
    account = db.get(UserAccount, created.account.id)
    assert account is not None
    db.add(UserRole(user_id=account.id, role_id=ROLES["guardian"].id))
    db.add_all(
        GuardianRelationship(
            guardian_person_id=guardian.id,
            student_person_id=student_id,
            relationship="mother",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
        for student_id in student_ids
    )
    db.commit()

    identity_service.search_people(
        phone=None,
        name="批量学员",
        person_type="student",
        page=1,
        page_size=1,
    )

    def count_queries(page_size: int) -> int:
        count = 0

        def before_cursor_execute(*_: object) -> None:
            nonlocal count
            count += 1

        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", before_cursor_execute)
        try:
            identity_service.search_people(
                phone=None,
                name="批量学员",
                person_type="student",
                page=1,
                page_size=page_size,
            )
        finally:
            event.remove(bind, "before_cursor_execute", before_cursor_execute)
        return count

    large_page_queries = count_queries(100)
    assert large_page_queries == count_queries(1)
    assert large_page_queries <= 10
