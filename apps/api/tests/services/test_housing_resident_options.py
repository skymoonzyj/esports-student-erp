from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.housing.errors import (
    HousingResourceNotFound,
    HousingSensitiveDataUnavailable,
    HousingServiceError,
)
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.seed_inventory import INITIAL_ROOMS
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort

NOW = datetime(2026, 8, 4, 8, 0, tzinfo=UTC)
REFERENCE_DATE = NOW.date()
_CIPHER = EnvelopeCipher(bytes(range(32)))
_LOOKUP = LookupDigest(bytes(range(32)))
_ROOM_ID = INITIAL_ROOMS[0].id


def _resident(
    db: Session,
    *,
    name: str,
    phone: str | None,
    profile: str | tuple[str, ...],
    account_status: str | None = "active",
    deleted: bool = False,
    birth_date: date = date(1990, 1, 2),
    staff_type: str = "coach",
) -> Person:
    person_id = uuid4()
    person = Person(
        id=person_id,
        full_name=name,
        identity_ciphertext=_CIPHER.encrypt(f"identity-{person_id}", field_name="identity_number"),
        identity_digest=_LOOKUP.digest(f"identity-{person_id}"),
        phone_ciphertext=(
            _CIPHER.encrypt(phone, field_name="phone") if phone is not None else None
        ),
        phone_digest=_LOOKUP.digest(phone) if phone is not None else None,
        email=f"{person_id}@example.test",
        sex="female",
        date_of_birth=birth_date,
        deleted_at=NOW if deleted else None,
    )
    db.add(person)
    db.flush()
    profile_keys = (profile,) if isinstance(profile, str) else profile
    if "student" in profile_keys:
        db.add(
            StudentProfile(
                person_id=person_id,
                student_number=f"H-{person_id.hex[:10]}",
                status="active",
            )
        )
    if "staff" in profile_keys:
        db.add(StaffProfile(person_id=person_id, staff_type=staff_type))
    if "visitor" in profile_keys:
        db.add(
            VisitorProfile(
                person_id=person_id,
                linked_student_id=None,
                visit_reason="housing option test",
            )
        )
    if account_status is not None:
        db.add(
            UserAccount(
                person_id=person_id,
                login_phone_digest=_LOOKUP.digest(f"login-{person_id}"),
                email_normalized=f"{person_id}@example.test",
                password_hash="not-used-by-service-tests",
                status=account_status,
                force_password_change=False,
            )
        )
    db.flush()
    return person


def _authorized_room(_room) -> None:
    return None


def _resident_options(
    db: Session,
    query: str,
    *,
    reference_date: date = REFERENCE_DATE,
):
    return HousingOccupancyService(db).list_resident_options(
        _ROOM_ID,
        query,
        cipher=_CIPHER,
        lookup=_LOOKUP,
        authorize_room=_authorized_room,
        reference_date=reference_date,
    )


def test_resident_options_authorize_room_before_candidate_queries(
    db: Session,
) -> None:
    statements: list[str] = []
    decrypt_calls = 0

    class CipherNeverUsed:
        def decrypt(self, *_args, **_kwargs):
            nonlocal decrypt_calls
            decrypt_calls += 1
            raise AssertionError("room authorization must run before decryption")

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    def hidden_room(_room) -> None:
        raise HousingResourceNotFound("housing room not found")

    def denied_room(_room) -> None:
        raise PermissionError("housing room denied")

    event.listen(db.bind, "after_cursor_execute", capture)
    try:
        with pytest.raises(HousingResourceNotFound):
            HousingOccupancyService(db).list_resident_options(
                _ROOM_ID,
                "候选",
                cipher=CipherNeverUsed(),
                lookup=_LOOKUP,
                authorize_room=hidden_room,
                reference_date=NOW.date(),
            )
        with pytest.raises(HousingResourceNotFound):
            HousingOccupancyService(db).list_resident_options(
                uuid4(),
                "候选",
                cipher=CipherNeverUsed(),
                lookup=_LOOKUP,
                authorize_room=_authorized_room,
                reference_date=NOW.date(),
            )
        with pytest.raises(PermissionError):
            HousingOccupancyService(db).list_resident_options(
                _ROOM_ID,
                "候选",
                cipher=CipherNeverUsed(),
                lookup=_LOOKUP,
                authorize_room=denied_room,
                reference_date=NOW.date(),
            )
    finally:
        event.remove(db.bind, "after_cursor_execute", capture)

    assert any("housing_rooms" in statement for statement in statements)
    assert not any(
        table in statement
        for statement in statements
        for table in ("people", "student_profiles", "staff_profiles", "visitor_profiles")
    )
    assert decrypt_calls == 0


def test_resident_options_searches_name_or_exact_phone_and_has_minimum_query_length(
    db: Session,
) -> None:
    named = _resident(db, name="张三候选", phone="13812348000", profile="student")
    _resident(db, name="张四候选", phone="13812340001", profile="staff")

    by_name = _resident_options(db, "  三候选 ")
    by_phone = _resident_options(db, " 13812348000 ")

    assert [item.person_id for item in by_name] == [named.id]
    assert [item.person_id for item in by_phone] == [named.id]
    with pytest.raises(HousingServiceError):
        _resident_options(db, " ")
    with pytest.raises(HousingServiceError):
        _resident_options(db, "张")


def test_resident_options_treat_like_wildcards_as_literal_name_substrings(
    db: Session,
) -> None:
    ordinary = _resident(db, name="普通姓名候选", phone="13812348002", profile="student")
    percent = _resident(db, name="百分%%姓名", phone="13812348003", profile="staff")
    underscore = _resident(db, name="下划__姓名", phone="13812348004", profile="visitor")

    percent_results = _resident_options(db, "%%")
    underscore_results = _resident_options(db, "__")

    assert [item.person_id for item in percent_results] == [percent.id]
    assert [item.person_id for item in underscore_results] == [underscore.id]
    assert ordinary.id not in {
        item.person_id for item in [*percent_results, *underscore_results]
    }


def test_resident_options_filter_candidates_bound_results_and_minimize_contract(
    db: Session,
) -> None:
    expected = [
        _resident(db, name=f"排序候选 {index:02d}", phone=f"1381234{index:04d}", profile="student")
        for index in range(22)
    ]
    _resident(
        db,
        name="排序候选 禁用",
        phone="13819990001",
        profile="staff",
        account_status="disabled",
    )
    _resident(db, name="排序候选 已删除", phone="13819990002", profile="visitor", deleted=True)
    _resident(db, name="排序候选 仅家长", phone="13819990003", profile=())

    results = _resident_options(db, "排序候选")

    assert [item.person_id for item in results] == [person.id for person in expected[:20]]
    assert all(item.account_ready and item.profile_ready for item in results)
    serialized = results[0].model_dump()
    assert {"phone", "email", "identity_number", "account_status"}.isdisjoint(serialized)


def test_resident_options_sort_equal_names_by_uuid(db: Session) -> None:
    first = _resident(db, name="同名稳定候选", phone="13812348005", profile="student")
    second = _resident(db, name="同名稳定候选", phone="13812348006", profile="staff")

    results = _resident_options(db, "同名稳定")

    assert [item.person_id for item in results] == sorted([first.id, second.id])


def test_resident_options_use_profile_priority_masked_phone_and_guardian_readiness(
    db: Session,
) -> None:
    multi = _resident(
        db,
        name="多档案候选",
        phone="13812348000",
        profile=("student", "staff", "visitor"),
        staff_type="administration",
    )
    visitor = _resident(db, name="访客候选", phone=None, profile="visitor")

    results = _resident_options(db, "候选")
    by_id = {item.person_id: item for item in results}

    assert by_id[multi.id].person_type == "student"
    assert by_id[multi.id].masked_phone == "138****8000"
    assert by_id[visitor.id].person_type == "visitor"
    minor_person = _resident(
        db,
        name="监护就绪候选",
        phone="13812348002",
        profile="student",
        birth_date=date(2015, 1, 2),
    )
    guardian = _resident(
        db,
        name="住宿监护人",
        phone="13812348003",
        profile=(),
    )
    guardian_account = db.scalar(
        select(UserAccount).where(UserAccount.person_id == guardian.id)
    )
    assert guardian_account is not None
    relationship = GuardianRelationship(
        guardian_person_id=guardian.id,
        student_person_id=minor_person.id,
        relationship="parent",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    db.add_all(
        [
            relationship,
            UserRole(user_id=guardian_account.id, role_id=ROLES["guardian"].id),
        ]
    )
    db.flush()
    minor = _resident_options(db, "监护就绪")
    assert minor[0].guardian_ready is True
    relationship.ended_at = NOW
    db.flush()
    assert _resident_options(db, "监护就绪")[0].guardian_ready is False


@pytest.mark.parametrize(
    ("phone_ciphertext", "expected_plaintext"),
    [
        ("not an envelope", None),
        (_CIPHER.encrypt("abc", field_name="phone"), "abc"),
        (_CIPHER.encrypt("1381234", field_name="phone"), "1381234"),
    ],
)
def test_resident_options_sensitive_phone_failure_has_no_partial_result(
    db: Session,
    phone_ciphertext: str,
    expected_plaintext: str | None,
) -> None:
    first = _resident(db, name="解密候选甲", phone="13812348000", profile="student")
    broken = _resident(db, name="解密候选乙", phone="13812348001", profile="staff")
    broken.phone_ciphertext = phone_ciphertext
    db.flush()

    with pytest.raises(HousingSensitiveDataUnavailable) as raised:
        _resident_options(db, "解密候选")

    assert raised.value.code == "decrypt_failed"
    assert "cipher" not in str(raised.value).lower()
    assert "phone" not in str(raised.value).lower()
    if expected_plaintext is not None:
        assert expected_plaintext not in str(raised.value)
    assert first.id != broken.id


def test_cohort_options_are_room_authorized_and_include_selectable_statuses_only(
    db: Session,
) -> None:
    admin = _resident(
        db,
        name="住宿营期管理员",
        phone="13812348004",
        profile="staff",
    )
    admin_id = db.scalar(select(UserAccount.id).where(UserAccount.person_id == admin.id))
    assert admin_id is not None
    cohort_ids: dict[str, UUID] = {}
    for status in ("draft", "open", "upcoming", "active", "completed", "archived"):
        cohort = Cohort(
            name=f"住宿营期-{status}",
            starts_at=NOW + timedelta(days=10),
            ends_at=NOW + timedelta(days=20),
            default_check_in_at=NOW + timedelta(days=10),
            default_check_out_at=NOW + timedelta(days=20),
            status=status,
            auto_start_enabled=False,
            auto_complete_enabled=False,
            created_by_user_id=admin_id,
        )
        db.add(cohort)
        db.flush()
        cohort_ids[status] = cohort.id

    invalid_cohort_ids: set[UUID] = set()
    for status in ("upcoming", "active"):
        cohort = Cohort(
            name=f"住宿营期-{status}-零时长",
            starts_at=NOW + timedelta(days=10),
            ends_at=NOW + timedelta(days=20),
            default_check_in_at=NOW + timedelta(days=12),
            default_check_out_at=NOW + timedelta(days=12),
            status=status,
            auto_start_enabled=False,
            auto_complete_enabled=False,
            created_by_user_id=admin_id,
        )
        db.add(cohort)
        db.flush()
        invalid_cohort_ids.add(cohort.id)

    options = HousingOccupancyService(db).list_cohort_options(
        _ROOM_ID,
        authorize_room=_authorized_room,
    )

    returned = {option.id for option in options}
    assert returned.issuperset(
        {cohort_ids["open"], cohort_ids["upcoming"], cohort_ids["active"]}
    )
    assert not returned.intersection(
        {cohort_ids["draft"], cohort_ids["completed"], cohort_ids["archived"]}
    )
    assert not returned.intersection(invalid_cohort_ids)
    assert all(option.default_check_in_at < option.default_check_out_at for option in options)
