from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.housing.reporting_projection import (
    GuardianCandidateRecord,
    HousingReportProjectionBuilder,
    ResidentCandidateRecord,
    StayCandidate,
    build_projection_from_candidates,
    classify_person_type,
    effective_guardian_relationship,
    mask_report_phone,
    overlaps_actual_stay,
    projection_sort_key,
    reporting_candidate_statement,
)
from app.housing.reporting_types import ResidentReportRow
from app.security.encryption import EnvelopeCipher


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


_DEFAULT_CHECK_IN = dt("2026-08-01T00:00:00+08:00")


def candidate(
    *,
    status: str = "checked_in",
    actual_check_in_at: datetime | None = _DEFAULT_CHECK_IN,
    actual_check_out_at: datetime | None = None,
    checkout_requested_actual_at: datetime | None = None,
) -> StayCandidate:
    return StayCandidate(
        status=status,
        actual_check_in_at=actual_check_in_at,
        actual_check_out_at=actual_check_out_at,
        checkout_requested_actual_at=checkout_requested_actual_at,
    )


def resident(**overrides: object) -> ResidentReportRow:
    values: dict[str, object] = {
        "row_id": "row-1",
        "person_id": UUID("10000000-0000-4000-8000-000000000001"),
        "full_name": "测试学员",
        "phone": "13800138000",
        "identity_number": "11010519491231002X",
        "sex": "male",
        "date_of_birth": date(2008, 8, 1),
        "person_type": "student",
        "building_id": UUID("20000000-0000-4000-8000-000000000001"),
        "building_name": "1号楼",
        "building_order": 1,
        "floor_id": UUID("30000000-0000-4000-8000-000000000001"),
        "floor_name": "二层",
        "floor_order": 2,
        "room_id": UUID("40000000-0000-4000-8000-000000000001"),
        "room_number": "0201",
        "stay_id": UUID("50000000-0000-4000-8000-000000000001"),
        "stay_purpose": "camp",
        "actual_check_in_at": dt("2026-08-01T00:00:00+08:00"),
        "actual_check_out_at": None,
        "stay_status": "checked_in",
        "cohort_id": UUID("60000000-0000-4000-8000-000000000001"),
        "cohort_name": "英雄联盟一期",
        "visit_reason": None,
        "is_minor_at_check_in": False,
        "guardian_count": 0,
    }
    values.update(overrides)
    return ResidentReportRow.model_validate(values)


_CIPHER = EnvelopeCipher(b"r" * 32)


def identity_for(birth_date: date, *, male: bool) -> str:
    body = f"110105{birth_date:%Y%m%d}00{1 if male else 2}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(digit) * weight for digit, weight in zip(body, weights)) % 11]


def resident_candidate(**overrides: object) -> ResidentCandidateRecord:
    person_id = UUID("10000000-0000-4000-8000-000000000011")
    birth_date = date(2010, 1, 2)
    values: dict[str, object] = {
        "stay_id": UUID("50000000-0000-4000-8000-000000000011"),
        "person_id": person_id,
        "full_name": "未成年学员",
        "phone_ciphertext": _CIPHER.encrypt("13800138000", field_name="phone"),
        "identity_ciphertext": _CIPHER.encrypt(
            identity_for(birth_date, male=True),
            field_name="identity_number",
        ),
        "sex": "male",
        "date_of_birth": birth_date,
        "is_student": True,
        "staff_type": None,
        "is_visitor": False,
        "has_verified_phone": True,
        "building_id": UUID("20000000-0000-4000-8000-000000000011"),
        "building_name": "1号楼",
        "building_order": 1,
        "floor_id": UUID("30000000-0000-4000-8000-000000000011"),
        "floor_name": "二层",
        "floor_order": 2,
        "room_id": UUID("40000000-0000-4000-8000-000000000011"),
        "room_number": "0201",
        "stay_purpose": "camp",
        "actual_check_in_at": dt("2026-08-01T00:00:00+08:00"),
        "actual_check_out_at": None,
        "stay_status": "checked_in",
        "checkout_requested_actual_at": None,
        "cohort_id": UUID("60000000-0000-4000-8000-000000000011"),
        "cohort_name": "英雄联盟一期",
        "visit_reason": None,
    }
    values.update(overrides)
    return ResidentCandidateRecord(**values)  # type: ignore[arg-type]


def guardian_candidate(
    name: str,
    *,
    guardian_id: UUID | None = None,
    phone_ciphertext: str | None = None,
) -> GuardianCandidateRecord:
    birth_date = date(1980, 2, 3)
    resolved_id = guardian_id or uuid4()
    return GuardianCandidateRecord(
        relationship_id=uuid4(),
        student_id=UUID("10000000-0000-4000-8000-000000000011"),
        guardian_id=resolved_id,
        valid_from=date(2010, 1, 1),
        valid_until=None,
        ended_at=None,
        guardian_name=name,
        guardian_phone_ciphertext=(
            phone_ciphertext
            if phone_ciphertext is not None
            else _CIPHER.encrypt("13900139000", field_name="phone")
        ),
        guardian_identity_ciphertext=_CIPHER.encrypt(
            identity_for(birth_date, male=False),
            field_name="identity_number",
        ),
        guardian_sex="female",
        guardian_date_of_birth=birth_date,
    )


@pytest.mark.parametrize(
    ("stay", "start", "end", "expected"),
    [
        (
            candidate(actual_check_out_at=dt("2026-08-03T00:00:00+08:00"), status="checked_out"),
            dt("2026-08-03T00:00:00+08:00"),
            dt("2026-08-04T00:00:00+08:00"),
            False,
        ),
        (
            candidate(actual_check_in_at=dt("2026-08-04T00:00:00+08:00")),
            dt("2026-08-03T00:00:00+08:00"),
            dt("2026-08-04T00:00:00+08:00"),
            False,
        ),
        (
            candidate(actual_check_out_at=dt("2026-08-03T00:00:00+08:00"), status="checked_out"),
            dt("2026-08-02T23:59:59+08:00"),
            dt("2026-08-04T00:00:00+08:00"),
            True,
        ),
        (
            candidate(status="planned", actual_check_in_at=None),
            dt("2026-08-01T00:00:00+08:00"),
            dt("2026-08-02T00:00:00+08:00"),
            False,
        ),
    ],
)
def test_actual_stay_overlap_uses_half_open_boundaries(
    stay: StayCandidate,
    start: datetime,
    end: datetime,
    expected: bool,
) -> None:
    assert overlaps_actual_stay(stay, start=start, end=end) is expected


def test_checkout_pending_request_does_not_cut_off_actual_stay() -> None:
    stay = candidate(
        status="checkout_pending",
        checkout_requested_actual_at=dt("2026-08-03T00:00:00+08:00"),
    )

    assert overlaps_actual_stay(
        stay,
        start=dt("2026-08-05T00:00:00+08:00"),
        end=dt("2026-08-06T00:00:00+08:00"),
    )


@pytest.mark.parametrize(
    ("student", "staff", "visitor", "expected"),
    [
        (True, None, False, "student"),
        (False, "coach", False, "coach"),
        (False, "administration", False, "administration"),
        (False, None, True, "visitor"),
        (False, None, False, None),
        (True, "coach", False, None),
    ],
)
def test_person_type_requires_exactly_one_profile_kind(
    student: bool,
    staff: str | None,
    visitor: bool,
    expected: str | None,
) -> None:
    assert (
        classify_person_type(
            is_student=student,
            staff_type=staff,
            is_visitor=visitor,
        )
        == expected
    )


def test_guardian_relationship_date_range_is_inclusive_but_ended_at_is_exclusive() -> None:
    check_in = dt("2026-08-06T00:00:00+08:00")

    assert (
        effective_guardian_relationship(
            valid_from=date(2026, 8, 6),
            valid_until=date(2026, 8, 6),
            ended_at=check_in.astimezone(UTC),
            check_in_at=check_in,
        )
        is False
    )
    assert (
        effective_guardian_relationship(
            valid_from=date(2026, 8, 6),
            valid_until=date(2026, 8, 6),
            ended_at=check_in.replace(hour=1),
            check_in_at=check_in,
        )
        is True
    )


def test_phone_mask_never_returns_full_phone() -> None:
    assert mask_report_phone("13800138000") == "138****8000"
    assert mask_report_phone(None) is None


@pytest.mark.parametrize("kind,expected", [("player", "player"), ("both", "student_player")])
def test_reviewed_minor_phone_is_not_misrepresented_as_verified(kind, expected):
    value = resident_candidate(
        is_student=kind == "both",
        assisted_kind=kind,
        has_verified_phone=False,
        has_approved_phone=True,
    )
    result = build_projection_from_candidates(
        residents=[value],
        guardians=[guardian_candidate("监护人")],
        cipher=_CIPHER,
        cohort_ids=(),
        person_types=(),
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
    )
    assert result.issues == ()
    row = result.projection.residents[0]
    assert row.person_type == expected
    assert row.phone_verified is False
    assert row.phone_evidence == "approved_profile"
    assert row.is_minor_at_check_in and row.guardian_count == 1


def test_generation_command_accepts_both_new_person_type_filters():
    from app.housing.reporting_service import GenerateReportCommand

    command = GenerateReportCommand(
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
        scope_mode="authorized",
        purpose="隔离名单核对",
        person_types=("player", "student_player"),
    )
    assert command.person_types == ("player", "student_player")


def test_projection_sort_key_uses_catalog_order_room_time_name_and_row_id() -> None:
    earlier_floor = resident(floor_order=1, room_number="0901", row_id="row-z")
    later_room = resident(room_number="0202", row_id="row-a")
    earlier_name = resident(full_name="阿明", row_id="row-b")

    assert sorted(
        [later_room, earlier_name, earlier_floor],
        key=projection_sort_key,
    ) == [earlier_floor, earlier_name, later_room]


def test_full_projection_exports_every_effective_guardian_in_stable_order() -> None:
    result = build_projection_from_candidates(
        residents=[resident_candidate()],
        guardians=[guardian_candidate("母亲"), guardian_candidate("父亲")],
        cipher=_CIPHER,
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
        cohort_ids=(),
        person_types=(),
        row_id_factory=lambda: "opaque-row-1",
    )

    assert result.issues == ()
    assert result.projection is not None
    assert result.projection.resident_count == 1
    assert result.projection.stay_row_count == 1
    assert result.projection.guardian_row_count == 2
    assert result.projection.residents[0].guardian_count == 2
    assert [row.guardian_name for row in result.projection.guardians] == ["母亲", "父亲"]


def test_adult_student_does_not_export_preserved_guardian_relationship() -> None:
    adult_birth = date(2000, 1, 2)
    candidate_row = resident_candidate(
        date_of_birth=adult_birth,
        identity_ciphertext=_CIPHER.encrypt(
            identity_for(adult_birth, male=True),
            field_name="identity_number",
        ),
    )

    result = build_projection_from_candidates(
        residents=[candidate_row],
        guardians=[guardian_candidate("仍绑定监护人")],
        cipher=_CIPHER,
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
        cohort_ids=(),
        person_types=(),
        row_id_factory=lambda: "opaque-row-1",
    )

    assert result.projection is not None
    assert result.projection.guardians == ()
    assert result.projection.residents[0].guardian_count == 0


def test_effective_guardian_with_missing_phone_fails_without_plaintext_issue() -> None:
    broken = guardian_candidate("缺手机号监护人", phone_ciphertext="")

    result = build_projection_from_candidates(
        residents=[resident_candidate()],
        guardians=[broken],
        cipher=_CIPHER,
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
        cohort_ids=(),
        person_types=(),
        row_id_factory=lambda: "opaque-row-1",
    )

    assert result.projection is None
    assert len(result.issues) == 1
    assert result.issues[0].missing_fields == ("guardian.phone",)
    assert result.issues[0].masked_phone == "138****8000"
    assert "13900139000" not in result.model_dump_json()


@pytest.mark.parametrize("field", ["date_of_birth", "sex"])
@pytest.mark.parametrize("guardian", [False, True])
def test_missing_person_facts_reject_report_without_inventing_age(field, guardian):
    resident_row = resident_candidate()
    guardian_row = guardian_candidate("测试监护人")
    if guardian:
        guardian_row = replace(guardian_row, **{f"guardian_{field}": None})
    else:
        resident_row = replace(resident_row, **{field: None})
    result = build_projection_from_candidates(
        residents=[resident_row],
        guardians=[guardian_row],
        cipher=_CIPHER,
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
        cohort_ids=(),
        person_types=(),
    )
    assert result.projection is None
    assert result.issues[0].missing_fields == (("guardian." if guardian else "") + field,)


@pytest.mark.parametrize("guardian", [False, True])
@pytest.mark.parametrize("document_type", ["passport", "hk_id", "hk_mo_permit"])
def test_cn_only_report_rejects_non_cn_even_when_number_looks_like_cn_id(guardian, document_type):
    resident_row = resident_candidate()
    guardian_row = guardian_candidate("测试监护人")
    if guardian:
        guardian_row = replace(guardian_row, guardian_document_type=document_type)
    else:
        resident_row = replace(resident_row, document_type=document_type)
    result = build_projection_from_candidates(
        residents=[resident_row],
        guardians=[guardian_row],
        cipher=_CIPHER,
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
        cohort_ids=(),
        person_types=(),
    )
    assert result.projection is None
    assert result.issues[0].missing_fields == (
        "guardian.identity_number" if guardian else "identity_number",
    )


def test_resident_reader_keeps_passport_and_null_facts_for_report_rejection():
    value = resident_candidate()
    stay = SimpleNamespace(
        id=value.stay_id,
        building_name_snapshot=value.building_name,
        floor_name_snapshot=value.floor_name,
        room_name_snapshot=value.room_number,
        **{
            field: getattr(value, field)
            for field in (
                "stay_purpose",
                "actual_check_in_at",
                "actual_check_out_at",
                "checkout_requested_actual_at",
                "cohort_id",
                "visit_reason",
            )
        },
        status=value.stay_status,
    )
    person = SimpleNamespace(
        id=value.person_id,
        full_name=value.full_name,
        phone_ciphertext=value.phone_ciphertext,
        identity_ciphertext=value.identity_ciphertext,
        sex=None,
        date_of_birth=None,
        document_type="passport",
    )
    mapped = HousingReportProjectionBuilder._resident_candidate(
        (
            stay,
            person,
            SimpleNamespace(id=value.room_id),
            SimpleNamespace(id=value.floor_id, display_order=value.floor_order),
            SimpleNamespace(id=value.building_id, display_order=value.building_order, code="1"),
            value.person_id,
            None,
            None,
            None,
            True,
        )
    )
    result = build_projection_from_candidates(
        residents=[mapped],
        guardians=[],
        cipher=_CIPHER,
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
        cohort_ids=(),
        person_types=(),
    )
    assert result.projection is None
    assert result.issues[0].missing_fields == ("date_of_birth", "identity_number", "sex")


def test_guardian_reader_keeps_passport_type_for_report_rejection():
    value = guardian_candidate("测试监护人")
    relationship = SimpleNamespace(
        id=value.relationship_id,
        student_person_id=value.student_id,
        valid_from=value.valid_from,
        valid_until=None,
        ended_at=None,
    )
    guardian = SimpleNamespace(
        id=value.guardian_id,
        full_name=value.guardian_name,
        phone_ciphertext=value.guardian_phone_ciphertext,
        identity_ciphertext=value.guardian_identity_ciphertext,
        sex=value.guardian_sex,
        date_of_birth=value.guardian_date_of_birth,
        document_type="passport",
    )
    db = Mock()
    db.execute.return_value.all.return_value = [(relationship, guardian)]
    mapped = HousingReportProjectionBuilder(db, cipher=_CIPHER)._guardian_candidates(
        [value.student_id]
    )
    result = build_projection_from_candidates(
        residents=[resident_candidate()],
        guardians=mapped,
        cipher=_CIPHER,
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-01T00:00:00+08:00"),
        range_end=dt("2026-08-22T00:00:00+08:00"),
        cohort_ids=(),
        person_types=(),
    )
    assert result.projection is None
    assert result.issues[0].missing_fields == ("guardian.identity_number",)


def test_filters_apply_before_projection_and_keep_checkout_pending_open() -> None:
    excluded_cohort = resident_candidate(
        stay_id=UUID("50000000-0000-4000-8000-000000000012"),
        cohort_id=UUID("60000000-0000-4000-8000-000000000012"),
        staff_type="coach",
        is_student=False,
    )
    pending = resident_candidate(
        stay_status="checkout_pending",
        checkout_requested_actual_at=dt("2026-08-03T00:00:00+08:00"),
        cohort_id=None,
        cohort_name=None,
        stay_purpose="permanent",
        date_of_birth=date(1990, 1, 2),
        identity_ciphertext=_CIPHER.encrypt(
            identity_for(date(1990, 1, 2), male=True),
            field_name="identity_number",
        ),
    )

    result = build_projection_from_candidates(
        residents=[excluded_cohort, pending],
        guardians=[],
        cipher=_CIPHER,
        generated_at=dt("2026-08-06T12:00:00+08:00"),
        range_start=dt("2026-08-05T00:00:00+08:00"),
        range_end=dt("2026-08-06T00:00:00+08:00"),
        cohort_ids=(),
        person_types=("student",),
        row_id_factory=lambda: "opaque-row-1",
    )

    assert result.projection is not None
    assert [row.stay_status for row in result.projection.residents] == ["checkout_pending"]


def test_candidate_statement_uses_authoritative_actual_checkout_only() -> None:
    statement = reporting_candidate_statement(
        floor_ids=(UUID("30000000-0000-4000-8000-000000000011"),),
        range_start=dt("2026-08-05T00:00:00+08:00"),
        range_end=dt("2026-08-06T00:00:00+08:00"),
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    normalized_sql = " ".join(sql.split())
    where_sql = normalized_sql.split(" where ", 1)[1].split(" order by ", 1)[0]

    assert "housing_stays.actual_check_in_at is not null" in normalized_sql
    assert "housing_stays.actual_check_in_at <" in normalized_sql
    assert "housing_stays.actual_check_out_at >" in normalized_sql
    assert "housing_stays.status != 'checked_out'" in normalized_sql
    assert "checkout_requested_actual_at" not in where_sql
    assert "housing_floors.id in" in normalized_sql
