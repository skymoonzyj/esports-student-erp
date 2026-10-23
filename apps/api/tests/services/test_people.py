from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, delete, event, func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.identity.membership_locks import person_membership_lock_keys
from app.identity.models import (
    GuardianProfile,
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.schemas import (
    GuardianCreate,
    PersonBaseInput,
    PersonUpdate,
    StaffCreate,
    StudentCreate,
    VisitorCreate,
)
from app.identity.service import (
    IdentityAlreadyExists,
    IdentityService,
    IdentityServiceError,
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


def _sql_statement_index(statements: list[tuple[str, object]], predicate) -> int | None:
    return next(
        (
            index
            for index, (statement, parameters) in enumerate(statements)
            if predicate(statement, parameters)
        ),
        None,
    )


def _membership_lock_indices(
    statements: list[tuple[str, object]],
    membership_keys: set[int],
) -> tuple[list[int], set[int]]:
    lock_indices: list[int] = []
    observed_keys: set[int] = set()
    for index, (statement, parameters) in enumerate(statements):
        if "pg_advisory_xact_lock" not in statement or not isinstance(parameters, dict):
            continue
        keys = {
            value for value in parameters.values() if isinstance(value, int) and value in membership_keys
        }
        if keys:
            lock_indices.append(index)
            observed_keys.update(keys)
    return lock_indices, observed_keys


def _people_for_update_indices(statements: list[tuple[str, object]]) -> list[int]:
    return [
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "FROM people" in statement and "FOR UPDATE" in statement
    ]


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


def test_count_people_reports_profiles_and_staff_types(
    identity_service: IdentityService,
) -> None:
    student = identity_service.create_student(
        StudentCreate(
            full_name="统计学员",
            identity_number=make_cn_id("20000102", 802),
            sex="female",
            date_of_birth=date(2000, 1, 2),
            phone="13900139801",
            email="count-student@example.com",
            student_number="COUNT-STUDENT-001",
        )
    )
    identity_service.create_guardian(
        GuardianCreate(
            full_name="统计监护人",
            identity_number=make_cn_id("19700102", 803),
            sex="male",
            date_of_birth=date(1970, 1, 2),
            phone="13900139802",
            email="count-guardian@example.com",
        )
    )
    identity_service.create_staff(
        StaffCreate(
            full_name="统计教练",
            identity_number=make_cn_id("19800102", 805),
            sex="male",
            date_of_birth=date(1980, 1, 2),
            phone="13900139803",
            email="count-coach@example.com",
            staff_type="coach",
        )
    )
    identity_service.create_staff(
        StaffCreate(
            full_name="统计生活老师",
            identity_number=make_cn_id("19810102", 804),
            sex="female",
            date_of_birth=date(1981, 1, 2),
            phone="13900139804",
            email="count-life@example.com",
            staff_type="life_teacher",
        )
    )
    identity_service.create_visitor(
        VisitorCreate(
            full_name="统计访客",
            identity_number=make_cn_id("19820102", 806),
            sex="female",
            date_of_birth=date(1982, 1, 2),
            phone="13900139805",
            email="count-visitor@example.com",
            visit_reason="探访",
            linked_student_id=student.id,
        )
    )

    counts = identity_service.count_people()

    assert counts.total == 5
    assert counts.student == 1
    assert counts.guardian == 1
    assert counts.staff == 2
    assert counts.coach == 1
    assert counts.life_teacher == 1
    assert counts.customer_service == 0
    assert counts.administration == 0
    assert counts.other_staff == 0
    assert counts.visitor == 1


def test_new_person_membership_lock_precedes_people_insert(
    identity_service: IdentityService,
    db: Session,
) -> None:
    payload = StudentCreate(
        full_name="摘要锁新学员",
        identity_number=make_cn_id("20010102", 802),
        sex="female",
        date_of_birth=date(2001, 1, 2),
        phone="13900139801",
        email="membership-new@example.com",
        student_number="MEMBERSHIP-NEW-001",
    )
    statements: list[tuple[str, object]] = []

    def capture_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append((statement, _parameters))

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_sql)
    try:
        identity_service.create_student(payload)
    finally:
        event.remove(bind, "before_cursor_execute", capture_sql)

    identity_digest = identity_service.lookup.digest(payload.identity_number)
    phone_digest = identity_service.lookup.digest(payload.phone or "")
    email_digest = identity_service.lookup.digest_domain("person-email", str(payload.email))
    membership_keys = set(
        person_membership_lock_keys(
            identity_digests={identity_digest},
            phone_digests={phone_digest},
            email_digests={email_digest},
        )
    )
    membership_lock_indices, observed_membership_keys = _membership_lock_indices(
        statements,
        membership_keys,
    )
    people_for_update_indices = _people_for_update_indices(statements)
    people_insert_index = _sql_statement_index(
        statements,
        lambda statement, _parameters: "INSERT INTO people" in statement,
    )
    captured_sql = "\n".join(statement for statement, _parameters in statements)

    assert observed_membership_keys == membership_keys
    assert people_insert_index is not None
    assert membership_lock_indices[-1] < people_insert_index
    assert not [
        index
        for index in people_for_update_indices
        if index > membership_lock_indices[-1]
    ]
    assert payload.identity_number not in captured_sql
    assert payload.phone not in captured_sql
    assert str(payload.email) not in captured_sql
    assert identity_digest not in captured_sql
    assert phone_digest not in captured_sql
    assert email_digest not in captured_sql


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


def test_base_person_has_no_implicit_person_type_and_guardian_is_explicit(
    identity_service: IdentityService,
    db: Session,
) -> None:
    base = identity_service.create_base_person(
        PersonBaseInput(
            full_name="基础账号申请人",
            identity_number=make_cn_id("19900102", 122),
            sex="female",
            date_of_birth=date(1990, 1, 2),
            phone=None,
            email=" Base@Example.COM ",
        )
    )
    guardian = identity_service.create_guardian(
        GuardianCreate(
            full_name="显式监护人",
            identity_number=make_cn_id("19800102", 123),
            sex="male",
            date_of_birth=date(1980, 1, 2),
            phone="13900139021",
            email=" Guardian@Example.COM ",
        )
    )

    assert base.person_types == []
    assert identity_service.get_person(base.id).person_types == []
    assert guardian.person_types == ["guardian"]
    assert db.get(GuardianProfile, guardian.id) is not None

    stored_base = db.get(Person, base.id)
    stored_guardian = db.get(Person, guardian.id)
    assert stored_base is not None and stored_guardian is not None
    assert stored_base.email is None
    assert stored_guardian.email is None
    assert stored_base.email_ciphertext is not None
    assert stored_guardian.email_ciphertext is not None
    assert "base@example.com" not in stored_base.email_ciphertext


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


def test_name_search_matches_nickname_and_includes_it_in_summary(
    identity_service: IdentityService,
    db: Session,
) -> None:
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    stored = db.get(Person, person.id)
    assert stored is not None
    stored.nickname = "飞鱼"
    db.commit()

    found = identity_service.search_people(name="飞鱼", page=1, page_size=20)

    assert found.total == 1
    assert found.items[0].full_name == "测试学员"
    assert found.items[0].nickname == "飞鱼"


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


def test_update_person_clears_contact_ciphertexts_and_digests(
    identity_service: IdentityService,
    db: Session,
) -> None:
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))

    identity_service.update_person(
        person.id,
        PersonUpdate(phone=None, email=None),
        actor_user_id=None,
        request_id="clear-contact-membership-locks",
    )

    stored = db.get(Person, person.id)
    assert stored is not None
    assert stored.phone_ciphertext is None
    assert stored.phone_digest is None
    assert stored.email is None
    assert stored.email_ciphertext is None
    assert stored.email_digest is None


def test_existing_person_lock_precedes_membership_lock_and_people_update(
    identity_service: IdentityService,
    db: Session,
) -> None:
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    stored = db.get(Person, person.id)
    assert stored is not None
    old_phone_digest = stored.phone_digest
    replacement_phone = "13900139999"
    replacement_phone_digest = identity_service.lookup.digest(replacement_phone)
    statements: list[tuple[str, object]] = []

    def capture_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append((statement, _parameters))

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_sql)
    try:
        identity_service.update_person(
            person.id,
            PersonUpdate(phone=replacement_phone),
            actor_user_id=None,
            request_id="membership-lock-update-order",
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_sql)

    membership_keys = set(
        person_membership_lock_keys(
            identity_digests={stored.identity_digest},
            phone_digests={old_phone_digest, replacement_phone_digest},
            email_digests={stored.email_digest},
        )
    )
    people_for_update_indices = _people_for_update_indices(statements)
    membership_lock_indices, observed_membership_keys = _membership_lock_indices(
        statements,
        membership_keys,
    )
    people_update_index = _sql_statement_index(
        statements,
        lambda statement, _parameters: "UPDATE people" in statement,
    )
    captured_sql = "\n".join(statement for statement, _parameters in statements)

    assert people_for_update_indices
    assert observed_membership_keys == membership_keys
    assert people_update_index is not None
    assert max(people_for_update_indices) < membership_lock_indices[0] < people_update_index
    assert membership_lock_indices[-1] < people_update_index
    assert not [
        index
        for index in people_for_update_indices
        if index > membership_lock_indices[-1]
    ]
    assert STUDENT_PAYLOAD["phone"] not in captured_sql
    assert replacement_phone not in captured_sql
    assert old_phone_digest is not None
    assert old_phone_digest not in captured_sql
    assert replacement_phone_digest not in captured_sql


def test_update_email_clears_stale_legacy_account_plaintext(
    identity_service: IdentityService,
    db: Session,
) -> None:
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    account_service = AccountService(
        db,
        identity_service=identity_service,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 26),
    )
    created = account_service.create_account(AccountCreate(person_id=person.id))
    account = db.get(UserAccount, created.account.id)
    assert account is not None
    account.email_normalized = "stale@example.com"
    db.flush()

    updated = identity_service.update_person(
        person.id,
        PersonUpdate(email="Current@Example.com"),
        actor_user_id=None,
        request_id="clear-stale-account-email",
    )

    assert updated.email == "current@example.com"
    assert account.email_normalized is None


def test_email_projection_rejects_legacy_plaintext_with_orphan_digest(
    identity_service: IdentityService,
    db: Session,
) -> None:
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    stored = db.get(Person, person.id)
    assert stored is not None
    stored.email = "legacy@example.com"
    stored.email_ciphertext = None
    stored.email_digest = "orphan-digest"

    with db.no_autoflush, pytest.raises(IdentityServiceError):
        identity_service.get_person(person.id)


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
    record_property,
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
    small_page_queries = count_queries(1)
    record_property("large_people_queries", large_page_queries)
    record_property("small_people_queries", small_page_queries)
    assert large_page_queries == small_page_queries
    assert large_page_queries <= 10
