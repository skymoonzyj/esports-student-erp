from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.housing.reporting_models import (
    HousingReportBatch,
    HousingReportBatchScope,
)

REPORT_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000043")
UTC_START = datetime(2026, 8, 6, tzinfo=UTC)
UTC_END = datetime(2026, 8, 7, tzinfo=UTC)
ADMIN_ID = UUID("61000000-0000-4000-8000-000000000001")


def _success_batch(**overrides: object) -> HousingReportBatch:
    values: dict[str, object] = {
        "batch_number": "HR-20260806-000001",
        "status": "generated",
        "version": 1,
        "range_start": UTC_START,
        "range_end": UTC_END,
        "cohort_filter": [],
        "person_type_filter": [],
        "purpose": "派出所人工报送",
        "projection_schema_version": 1,
        "snapshot_ciphertext": "ciphertext",
        "snapshot_key_version": 1,
        "snapshot_digest": "a" * 64,
        "resident_count": 1,
        "stay_row_count": 1,
        "guardian_row_count": 0,
        "created_by_user_id": ADMIN_ID,
    }
    values.update(overrides)
    return HousingReportBatch(**values)


def _insert_test_account(db: Session) -> UUID:
    person_id = uuid4()
    account_id = uuid4()
    db.execute(
        text(
            "INSERT INTO people ("
            "id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth"
            ") VALUES ("
            ":person_id, '报送测试操作人', 'operator-ciphertext', "
            ":identity_digest, 'female', DATE '1988-01-02')"
        ),
        {"person_id": person_id, "identity_digest": f"operator-{person_id}"},
    )
    db.execute(
        text(
            "INSERT INTO user_accounts ("
            "id, person_id, login_phone_digest, email_normalized, password_hash, "
            "status, force_password_change, failed_login_count"
            ") VALUES ("
            ":account_id, :person_id, :phone_digest, :email, 'operator-password', "
            "'active', false, 0)"
        ),
        {
            "account_id": account_id,
            "person_id": person_id,
            "phone_digest": f"phone-{account_id}",
            "email": f"operator-{account_id}@example.test",
        },
    )
    return account_id


def test_report_permission_is_sensitive_and_admin_only(postgres_engine) -> None:
    from app.rbac.models import Permission, Role, RolePermission

    with Session(postgres_engine) as db:
        permission = db.scalar(
            select(Permission).where(
                Permission.key == "housing.reports.export_sensitive"
            )
        )
        assert permission is not None
        assert (permission.id, permission.sensitive) == (REPORT_PERMISSION_ID, True)
        grants = set(
            db.execute(
                select(Role.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .where(RolePermission.permission_id == REPORT_PERMISSION_ID)
            ).tuples()
        )
    assert grants == {("admin", "global")}


def test_success_batch_requires_snapshot_and_state_fields(db: Session) -> None:
    with pytest.raises(IntegrityError):
        db.add(
            _success_batch(
                created_by_user_id=_insert_test_account(db),
                snapshot_ciphertext=None,
            )
        )
        db.flush()


def test_failed_batch_cannot_store_snapshot(db: Session) -> None:
    with pytest.raises(IntegrityError):
        db.add(
            _success_batch(
                batch_number="HR-20260806-000002",
                status="failed",
                created_by_user_id=_insert_test_account(db),
                snapshot_ciphertext="ciphertext",
                snapshot_key_version=1,
                snapshot_digest="a" * 64,
            )
        )
        db.flush()


def test_batch_scope_requires_floor_building_pair_and_is_unique(
    db: Session,
) -> None:
    batch = _success_batch(
        batch_number="HR-20260806-000003",
        created_by_user_id=_insert_test_account(db),
    )
    db.add(batch)
    db.flush()
    floor_id, building_id = db.execute(
        text(
            "SELECT id, building_id FROM housing_floors "
            "ORDER BY building_id, number, id LIMIT 1"
        )
    ).one()
    db.add(
        HousingReportBatchScope(
            batch_id=batch.id,
            building_id=building_id,
            floor_id=floor_id,
        )
    )
    db.flush()
    with pytest.raises(IntegrityError):
        db.add(
            HousingReportBatchScope(
                batch_id=batch.id,
                building_id=building_id,
                floor_id=floor_id,
            )
        )
        db.flush()


@pytest.mark.parametrize(
    "overrides",
    [
        {"range_start": UTC_END},
        {"purpose": "   "},
        {"version": 0},
        {"resident_count": -1},
        {"snapshot_digest": "A" * 64},
    ],
)
def test_report_batch_rejects_invalid_constraints(
    db: Session,
    overrides: dict[str, object],
) -> None:
    with pytest.raises(IntegrityError):
        db.add(
            _success_batch(
                batch_number=f"HR-20260806-{uuid4().hex[:6]}",
                created_by_user_id=_insert_test_account(db),
                **overrides,
            )
        )
        db.flush()


def test_report_batch_rejects_self_correction(db: Session) -> None:
    with pytest.raises(IntegrityError):
        batch = _success_batch(
            batch_number=f"HR-20260806-{uuid4().hex[:6]}",
            created_by_user_id=_insert_test_account(db),
        )
        db.add(batch)
        db.flush()
        batch.corrects_batch_id = batch.id
        db.flush()


def test_report_batch_insert_self_correction_uses_stable_constraint(
    db: Session,
) -> None:
    batch_id = uuid4()
    with pytest.raises(IntegrityError) as caught:
        db.add(
            _success_batch(
                id=batch_id,
                batch_number=f"HR-20260806-{uuid4().hex[:6]}",
                created_by_user_id=_insert_test_account(db),
                corrects_batch_id=batch_id,
            )
        )
        db.flush()

    assert caught.value.orig.diag.constraint_name == (
        "ck_housing_report_batches_corrects_not_self"
    )


def test_report_batch_rejects_purpose_over_200_characters(db: Session) -> None:
    with pytest.raises(DBAPIError):
        db.add(
            _success_batch(
                batch_number=f"HR-20260806-{uuid4().hex[:6]}",
                created_by_user_id=_insert_test_account(db),
                purpose="用" * 201,
            )
        )
        db.flush()


@pytest.mark.parametrize(
    "batch",
    [
        _success_batch(
            batch_number="HR-20260806-state-1",
            status="marked_submitted",
        ),
        _success_batch(
            batch_number="HR-20260806-state-2",
            status="voided",
        ),
        _success_batch(
            batch_number="HR-20260806-state-3",
            submission_note="不能提前填写",
        ),
    ],
)
def test_report_batch_requires_exact_state_field_groups(
    db: Session,
    batch: HousingReportBatch,
) -> None:
    with pytest.raises(IntegrityError):
        batch.created_by_user_id = _insert_test_account(db)
        db.add(batch)
        db.flush()


@pytest.mark.parametrize(
    "overrides",
    [
        {"failure_code": None, "failure_summary": {"issue_count": 1}},
        {"failure_code": "   ", "failure_summary": {"issue_count": 1}},
        {"failure_code": "housing_export_incomplete", "failure_summary": None},
        {"failure_code": "housing_export_incomplete", "failure_summary": []},
        {"failure_code": "housing_export_incomplete", "failure_summary": 1},
    ],
)
def test_failed_batch_requires_safe_failure_code_and_object_summary(
    db: Session,
    overrides: dict[str, object],
) -> None:
    with pytest.raises(IntegrityError):
        batch = _success_batch(
            batch_number=f"HR-20260806-{uuid4().hex[:6]}",
            status="failed",
            snapshot_ciphertext=None,
            snapshot_key_version=None,
            snapshot_digest=None,
            created_by_user_id=_insert_test_account(db),
            **overrides,
        )
        db.add(batch)
        db.flush()


def test_success_batch_rejects_failure_fields(db: Session) -> None:
    with pytest.raises(IntegrityError):
        db.add(
            _success_batch(
                batch_number=f"HR-20260806-{uuid4().hex[:6]}",
                created_by_user_id=_insert_test_account(db),
                failure_code="housing_export_incomplete",
                failure_summary={"issue_count": 1},
            )
        )
        db.flush()


def test_success_batch_accepts_explicit_none_failure_summary(db: Session) -> None:
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=_insert_test_account(db),
        failure_code=None,
        failure_summary=None,
    )
    db.add(batch)
    db.flush()

    assert batch.failure_summary is None


@pytest.mark.parametrize(
    ("failure_code", "failure_summary"),
    [
        ("housing_report_empty", {"issue_count": 0, "field_counts": {}}),
        (
            "housing_export_incomplete",
            {"issue_count": 1, "field_counts": {"phone": 1}},
        ),
    ],
)
def test_failed_batch_accepts_only_safe_count_summary(
    db: Session,
    failure_code: str,
    failure_summary: dict[str, object],
) -> None:
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        status="failed",
        snapshot_ciphertext=None,
        snapshot_key_version=None,
        snapshot_digest=None,
        created_by_user_id=_insert_test_account(db),
        failure_code=failure_code,
        failure_summary=failure_summary,
    )
    db.add(batch)
    db.flush()

    assert batch.failure_summary == failure_summary


def test_failed_batch_cannot_consume_correction_slot(db: Session) -> None:
    actor_id = _insert_test_account(db)
    predecessor = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=actor_id,
    )
    db.add(predecessor)
    db.flush()
    predecessor.status = "voided"
    predecessor.version = 2
    predecessor.voided_at = UTC_END
    predecessor.voided_by_user_id = actor_id
    predecessor.void_reason = "原批次错误"
    db.flush()

    with pytest.raises(IntegrityError) as caught:
        db.add(
            _success_batch(
                batch_number=f"HR-20260806-{uuid4().hex[:6]}",
                status="failed",
                snapshot_ciphertext=None,
                snapshot_key_version=None,
                snapshot_digest=None,
                created_by_user_id=actor_id,
                corrects_batch_id=predecessor.id,
                failure_code="housing_export_incomplete",
                failure_summary={
                    "issue_count": 1,
                    "field_counts": {"phone": 1},
                },
            )
        )
        db.flush()

    assert caught.value.orig.diag.constraint_name == (
        "ck_housing_report_batches_failed_not_correction"
    )


@pytest.mark.parametrize(
    ("failure_code", "failure_summary"),
    [
        (
            "unknown_failure",
            {"issue_count": 1, "field_counts": {"phone": 1}},
        ),
        (
            "housing_export_incomplete",
            {
                "issue_count": 1,
                "field_counts": {"phone": 1},
                "full_name": "张三",
            },
        ),
        (
            "housing_export_incomplete",
            {"issue_count": 1, "field_counts": {"张三": 1}},
        ),
        (
            "housing_export_incomplete",
            {"issue_count": 1, "field_counts": {"phone": "13800138000"}},
        ),
        (
            "housing_report_empty",
            {"issue_count": 1, "field_counts": {}},
        ),
        (
            "housing_export_incomplete",
            {"issue_count": 0, "field_counts": {}},
        ),
        (
            "housing_export_incomplete",
            {"issue_count": 1, "field_counts": {"phone": 0}},
        ),
        (
            "housing_export_incomplete",
            {"issue_count": 1, "field_counts": {"phone": 2}},
        ),
    ],
)
def test_failed_batch_rejects_unsafe_or_inconsistent_summary(
    db: Session,
    failure_code: str,
    failure_summary: dict[str, object],
) -> None:
    with pytest.raises(IntegrityError):
        db.add(
            _success_batch(
                batch_number=f"HR-20260806-{uuid4().hex[:6]}",
                status="failed",
                snapshot_ciphertext=None,
                snapshot_key_version=None,
                snapshot_digest=None,
                created_by_user_id=_insert_test_account(db),
                failure_code=failure_code,
                failure_summary=failure_summary,
            )
        )
        db.flush()


def test_batch_snapshot_and_business_fields_are_immutable_but_state_transition_is_allowed(
    db: Session,
) -> None:
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=_insert_test_account(db),
    )
    db.add(batch)
    db.flush()
    batch.status = "marked_submitted"
    batch.version = 2
    batch.submitted_at = UTC_START
    batch.submitted_by_user_id = batch.created_by_user_id
    db.flush()
    batch.purpose = "试图覆盖原始用途"

    with pytest.raises(DBAPIError):
        db.flush()


def test_report_batch_snapshot_digest_is_immutable(db: Session) -> None:
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=_insert_test_account(db),
    )
    db.add(batch)
    db.flush()
    batch.snapshot_digest = "b" * 64

    with pytest.raises(DBAPIError):
        db.flush()


def test_report_batch_cannot_be_deleted(db: Session) -> None:
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=_insert_test_account(db),
    )
    db.add(batch)
    db.flush()
    db.delete(batch)

    with pytest.raises(DBAPIError):
        db.flush()


def test_correction_requires_one_voided_direct_predecessor(db: Session) -> None:
    actor_id = _insert_test_account(db)
    predecessor = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=actor_id,
    )
    db.add(predecessor)
    db.flush()
    db.add(
        _success_batch(
            batch_number=f"HR-20260806-{uuid4().hex[:6]}",
            created_by_user_id=actor_id,
            corrects_batch_id=predecessor.id,
        )
    )

    with pytest.raises(DBAPIError):
        db.flush()


def test_voided_batch_has_at_most_one_direct_correction(db: Session) -> None:
    actor_id = _insert_test_account(db)
    predecessor = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=actor_id,
    )
    db.add(predecessor)
    db.flush()
    predecessor.status = "voided"
    predecessor.version = 2
    predecessor.voided_at = UTC_START
    predecessor.voided_by_user_id = actor_id
    predecessor.void_reason = "原批次错误"
    db.flush()
    first = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=actor_id,
        corrects_batch_id=predecessor.id,
    )
    db.add(first)
    db.flush()
    db.add(
        _success_batch(
            batch_number=f"HR-20260806-{uuid4().hex[:6]}",
            created_by_user_id=actor_id,
            corrects_batch_id=predecessor.id,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()


@pytest.mark.parametrize("next_version", [1, 3])
def test_report_batch_state_transition_requires_version_increment(
    db: Session,
    next_version: int,
) -> None:
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=_insert_test_account(db),
    )
    db.add(batch)
    db.flush()
    batch.status = "marked_submitted"
    batch.version = next_version
    batch.submitted_at = UTC_START
    batch.submitted_by_user_id = batch.created_by_user_id

    with pytest.raises(DBAPIError):
        db.flush()


def test_report_batch_rejects_generated_to_failed_update(db: Session) -> None:
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=_insert_test_account(db),
    )
    db.add(batch)
    db.flush()
    batch.status = "failed"
    batch.version = 2
    batch.snapshot_ciphertext = None
    batch.snapshot_key_version = None
    batch.snapshot_digest = None
    batch.failure_code = "housing_export_incomplete"
    batch.failure_summary = {"issue_count": 1}

    with pytest.raises(DBAPIError):
        db.flush()


def test_submitted_fields_are_immutable_when_voiding(db: Session) -> None:
    actor_id = _insert_test_account(db)
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=actor_id,
    )
    db.add(batch)
    db.flush()
    batch.status = "marked_submitted"
    batch.version = 2
    batch.submitted_at = UTC_START
    batch.submitted_by_user_id = actor_id
    batch.submission_note = "首次报送"
    db.flush()
    batch.status = "voided"
    batch.version = 3
    batch.submitted_at = UTC_END
    batch.voided_at = UTC_END
    batch.voided_by_user_id = actor_id
    batch.void_reason = "原批次错误"

    with pytest.raises(DBAPIError):
        db.flush()


def test_generated_to_voided_cannot_forge_submitted_history(db: Session) -> None:
    actor_id = _insert_test_account(db)
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=actor_id,
    )
    db.add(batch)
    db.flush()
    batch.status = "voided"
    batch.version = 2
    batch.submitted_at = UTC_START
    batch.submitted_by_user_id = actor_id
    batch.submission_note = "伪造报送历史"
    batch.voided_at = UTC_END
    batch.voided_by_user_id = actor_id
    batch.void_reason = "直接作废"

    with pytest.raises(DBAPIError):
        db.flush()


def test_generated_can_void_without_submitted_history(db: Session) -> None:
    actor_id = _insert_test_account(db)
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=actor_id,
    )
    db.add(batch)
    db.flush()
    batch.status = "voided"
    batch.version = 2
    batch.voided_at = UTC_END
    batch.voided_by_user_id = actor_id
    batch.void_reason = "直接作废"
    db.flush()

    assert batch.submitted_at is None
    assert batch.submitted_by_user_id is None
    assert batch.submission_note is None


def test_marked_submitted_can_void_while_preserving_submitted_history(
    db: Session,
) -> None:
    actor_id = _insert_test_account(db)
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=actor_id,
    )
    db.add(batch)
    db.flush()
    batch.status = "marked_submitted"
    batch.version = 2
    batch.submitted_at = UTC_START
    batch.submitted_by_user_id = actor_id
    batch.submission_note = "首次报送"
    db.flush()
    batch.status = "voided"
    batch.version = 3
    batch.voided_at = UTC_END
    batch.voided_by_user_id = actor_id
    batch.void_reason = "原批次错误"
    db.flush()

    assert batch.submitted_at == UTC_START
    assert batch.submitted_by_user_id == actor_id
    assert batch.submission_note == "首次报送"


@pytest.mark.parametrize(
    "batch",
    [
        _success_batch(
            batch_number="HR-20260806-initial-submitted",
            status="marked_submitted",
            submitted_at=UTC_START,
            submitted_by_user_id=ADMIN_ID,
        ),
        _success_batch(
            batch_number="HR-20260806-initial-voided",
            status="voided",
            voided_at=UTC_START,
            voided_by_user_id=ADMIN_ID,
            void_reason="不得直接插入终态",
        ),
        _success_batch(
            batch_number="HR-20260806-initial-version",
            version=7,
        ),
    ],
)
def test_report_batch_insert_requires_initial_state_and_version(
    db: Session,
    batch: HousingReportBatch,
) -> None:
    with pytest.raises(IntegrityError) as caught:
        actor_id = _insert_test_account(db)
        batch.created_by_user_id = actor_id
        if batch.submitted_by_user_id is not None:
            batch.submitted_by_user_id = actor_id
        if batch.voided_by_user_id is not None:
            batch.voided_by_user_id = actor_id
        db.add(batch)
        db.flush()

    assert caught.value.orig.diag.constraint_name == (
        "ck_housing_report_batches_initial_state"
    )


def _insert_batch_scope(db: Session) -> HousingReportBatchScope:
    batch = _success_batch(
        batch_number=f"HR-20260806-{uuid4().hex[:6]}",
        created_by_user_id=_insert_test_account(db),
    )
    db.add(batch)
    db.flush()
    floor_id, building_id = db.execute(
        text(
            "SELECT id, building_id FROM housing_floors "
            "ORDER BY building_id, number, id LIMIT 1"
        )
    ).one()
    scope = HousingReportBatchScope(
        batch_id=batch.id,
        building_id=building_id,
        floor_id=floor_id,
    )
    db.add(scope)
    db.flush()
    return scope


def test_report_batch_scope_cannot_be_updated(db: Session) -> None:
    scope = _insert_batch_scope(db)

    with pytest.raises(DBAPIError):
        db.execute(
            text(
                "UPDATE housing_report_batch_scopes "
                "SET building_id = building_id "
                "WHERE batch_id = :batch_id AND floor_id = :floor_id"
            ),
            {"batch_id": scope.batch_id, "floor_id": scope.floor_id},
        )


def test_report_batch_scope_cannot_be_deleted(db: Session) -> None:
    scope = _insert_batch_scope(db)
    db.delete(scope)

    with pytest.raises(DBAPIError):
        db.flush()


def test_batch_number_uses_shanghai_calendar_date(postgres_engine) -> None:
    with postgres_engine.connect() as connection:
        default_expression = connection.scalar(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'housing_report_batches' "
                "AND column_name = 'batch_number'"
            )
        )

    assert default_expression is not None
    assert "Asia/Shanghai" in default_expression
