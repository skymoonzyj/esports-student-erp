from __future__ import annotations

import csv
import inspect
import re
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime, timedelta
from io import BytesIO, StringIO
from typing import get_type_hints
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from sqlalchemy import Connection, Engine, delete, event, func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.housing import reporting_service as reporting_service_module
from app.housing.access import HousingAccessService
from app.housing.errors import HousingResourceNotFound
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.reporting_models import HousingReportBatch, HousingReportBatchScope
from app.housing.reporting_service import (
    GenerateReportCommand,
    HousingAuditFailure,
    HousingExportIncomplete,
    HousingReportCorrectionInvalid,
    HousingReportEmpty,
    HousingReportingService,
    HousingReportRenderFailed,
    HousingReportScopeDenied,
    HousingReportSnapshotUnavailable,
    HousingReportStateConflict,
    MarkSubmittedCommand,
    ReportBatchCorrectionLink,
    ReportBatchDetail,
    ReportBatchPage,
    ReportBatchQuery,
    ReportBatchScopeSummary,
    ReportBatchSummary,
    ReportDownload,
    ReportFormat,
    VoidReportCommand,
    allowed_actions,
    transition_allowed,
)
from app.housing.reporting_snapshot import SnapshotIntegrityError
from app.identity.models import Person, StaffProfile
from app.rbac.models import Permission, Role, UserPermissionOverride, UserRole
from app.security.encryption import EnvelopeCipher

START = datetime(2026, 8, 6, tzinfo=UTC)
END = datetime(2026, 8, 7, tzinfo=UTC)
BUILDING_ID = UUID("52000000-0000-4000-8000-000000000010")
FLOOR_ID = UUID("52000000-0000-4000-8000-000000000001")


def _identity_for(birth_date: date, *, male: bool) -> str:
    body = f"110105{birth_date:%Y%m%d}00{1 if male else 2}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    weighted = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[weighted % 11]


@dataclass(frozen=True, slots=True)
class ReportingContext:
    connection: Connection
    cipher: EnvelopeCipher
    actor_id: UUID
    resident_id: UUID
    building_id: UUID
    floor_id: UUID
    room_id: UUID


@pytest.fixture
def reporting_context(postgres_engine: Engine) -> ReportingContext:
    connection = postgres_engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    )
    outer_transaction = connection.begin()
    cipher = EnvelopeCipher(b"r" * 32)
    actor_id = uuid4()
    resident_id = uuid4()
    building_id = uuid4()
    floor_id = uuid4()
    room_id = uuid4()
    marker = uuid4().hex[:8].upper()
    resident_birth_date = date(1990, 1, 2)
    with Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as seed_db:
        actor_person = Person(
            id=uuid4(),
            full_name="报送操作员",
            identity_ciphertext=f"actor-{uuid4().hex}",
            identity_digest=f"actor-{uuid4().hex}",
            sex="female",
            date_of_birth=date(1988, 1, 2),
        )
        actor = UserAccount(
            id=actor_id,
            person_id=actor_person.id,
            login_phone_digest=f"login-{uuid4().hex}",
            email_normalized=f"{uuid4().hex}@reporting.example.test",
            password_hash="reporting-password-hash",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        seed_db.add(actor_person)
        seed_db.flush()
        seed_db.add(actor)
        seed_db.flush()
        admin_role_id = seed_db.scalar(select(Role.id).where(Role.key == "admin"))
        assert admin_role_id is not None
        seed_db.add(UserRole(user_id=actor_id, role_id=admin_role_id))

        building = HousingBuilding(
            id=building_id,
            code=f"T4{marker}",
            name=f"报送测试楼{marker}",
            display_order=1,
            is_active=True,
        )
        floor = HousingFloor(
            id=floor_id,
            building_id=building_id,
            number=2,
            name=f"报送二层{marker}",
            display_order=2,
            is_active=True,
        )
        room = HousingRoom(
            id=room_id,
            building_id=building_id,
            floor_id=floor_id,
            room_number=f"{marker}-201",
            normalized_key=f"{marker}-201",
            room_type="custom",
            capacity=10,
            verification_status="verified",
            service_status="available",
            version=1,
        )
        resident = Person(
            id=resident_id,
            full_name="完整资料住户",
            identity_ciphertext=cipher.encrypt(
                _identity_for(resident_birth_date, male=True),
                field_name="identity_number",
            ),
            identity_digest=f"resident-{uuid4().hex}",
            phone_ciphertext=cipher.encrypt("13800138000", field_name="phone"),
            phone_digest=f"resident-phone-{uuid4().hex}",
            sex="male",
            date_of_birth=resident_birth_date,
        )
        stay = HousingStay(
            id=uuid4(),
            person_id=resident_id,
            room_id=room_id,
            stay_purpose="permanent",
            duration_mode="long_term",
            planned_check_in_at=START - timedelta(days=1),
            actual_check_in_at=START - timedelta(hours=12),
            status="checked_in",
            room_name_snapshot=room.room_number,
            floor_name_snapshot=floor.name,
            building_name_snapshot=building.name,
            created_by_user_id=actor_id,
            checked_in_by_user_id=actor_id,
            version=1,
        )
        seed_db.add(building)
        seed_db.flush()
        seed_db.add(floor)
        seed_db.flush()
        seed_db.add(room)
        seed_db.flush()
        seed_db.add(resident)
        seed_db.flush()
        seed_db.add(StaffProfile(person_id=resident_id, staff_type="other"))
        seed_db.flush()
        seed_db.add(stay)
        seed_db.commit()
    try:
        yield ReportingContext(
            connection=connection,
            cipher=cipher,
            actor_id=actor_id,
            resident_id=resident_id,
            building_id=building_id,
            floor_id=floor_id,
            room_id=room_id,
        )
    finally:
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


def _service(context: ReportingContext, db: Session) -> HousingReportingService:
    return HousingReportingService(
        db,
        context.cipher,
        context.actor_id,
        END,
        f"reporting-test-{uuid4().hex}",
    )


def _floor_command(context: ReportingContext, **overrides: object) -> GenerateReportCommand:
    values: dict[str, object] = {
        "range_start": START,
        "range_end": END,
        "scope_mode": "floors",
        "floor_ids": (context.floor_id,),
        "purpose": "派出所人工报送",
    }
    values.update(overrides)
    return GenerateReportCommand(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "action", "allowed"),
    [
        ("generated", "mark_submitted", True),
        ("generated", "void", True),
        ("generated", "download", True),
        ("marked_submitted", "void", True),
        ("marked_submitted", "download", True),
        ("marked_submitted", "mark_submitted", False),
        ("voided", "download", False),
        ("voided", "correct", True),
        ("failed", "download", False),
        ("failed", "correct", False),
    ],
)
def test_report_state_matrix(status: str, action: str, allowed: bool) -> None:
    assert transition_allowed(status, action) is allowed


@pytest.mark.parametrize(
    ("status", "has_correction", "expected"),
    [
        (
            "generated",
            False,
            {
                "download": True,
                "mark_submitted": True,
                "void": True,
                "correct": False,
            },
        ),
        (
            "marked_submitted",
            False,
            {
                "download": True,
                "mark_submitted": False,
                "void": True,
                "correct": False,
            },
        ),
        (
            "voided",
            False,
            {
                "download": False,
                "mark_submitted": False,
                "void": False,
                "correct": True,
            },
        ),
        (
            "voided",
            True,
            {
                "download": False,
                "mark_submitted": False,
                "void": False,
                "correct": False,
            },
        ),
        (
            "failed",
            False,
            {
                "download": False,
                "mark_submitted": False,
                "void": False,
                "correct": False,
            },
        ),
    ],
)
def test_allowed_actions_are_exact(
    status: str,
    has_correction: bool,
    expected: dict[str, bool],
) -> None:
    assert asdict(allowed_actions(status, has_correction=has_correction)) == expected


def test_service_exposes_only_the_six_confirmed_public_methods() -> None:
    public_methods = {
        name
        for name, member in vars(HousingReportingService).items()
        if not name.startswith("_") and inspect.isfunction(member)
    }

    assert public_methods == {
        "generate",
        "list",
        "get",
        "download",
        "mark_submitted",
        "void",
    }
    expected = {
        "generate": ({"command": GenerateReportCommand}, ReportBatchSummary),
        "list": ({"query": ReportBatchQuery}, ReportBatchPage),
        "get": ({"batch_id": UUID}, ReportBatchDetail),
        "download": (
            {"batch_id": UUID, "format": ReportFormat},
            ReportDownload,
        ),
        "mark_submitted": (
            {"batch_id": UUID, "command": MarkSubmittedCommand},
            ReportBatchSummary,
        ),
        "void": (
            {"batch_id": UUID, "command": VoidReportCommand},
            ReportBatchSummary,
        ),
    }
    for method_name, (parameters, return_type) in expected.items():
        hints = get_type_hints(getattr(HousingReportingService, method_name))
        assert {name: hints[name] for name in parameters} == parameters
        assert hints["return"] is return_type


def test_generate_command_normalizes_safe_filters_and_purpose() -> None:
    command = GenerateReportCommand(
        range_start=START,
        range_end=END,
        scope_mode="buildings",
        building_ids=(BUILDING_ID,),
        cohort_ids=(UUID("72000000-0000-4000-8000-000000000001"),),
        person_types=("student", "coach"),
        purpose="  派出所人工报送  ",
    )

    assert command.purpose == "派出所人工报送"
    assert command.building_ids == (BUILDING_ID,)
    assert command.floor_ids == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"range_start": END},
        {"range_start": START.replace(tzinfo=None)},
        {"scope_mode": "authorized", "building_ids": (BUILDING_ID,)},
        {"scope_mode": "buildings", "building_ids": ()},
        {"scope_mode": "buildings", "floor_ids": (FLOOR_ID,)},
        {"scope_mode": "floors", "floor_ids": ()},
        {"scope_mode": "floors", "building_ids": (BUILDING_ID,)},
        {"purpose": "   "},
        {"purpose": "短"},
        {"purpose": "报送用途 13800138000"},
        {"purpose": "身份证 11010519491231002X"},
        {"purpose": "用" * 201},
    ],
)
def test_generate_command_rejects_invalid_boundary_combinations(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "range_start": START,
        "range_end": END,
        "scope_mode": "authorized",
        "purpose": "派出所人工报送",
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        GenerateReportCommand(**values)  # type: ignore[arg-type]


def test_state_commands_are_strict_and_trim_business_text() -> None:
    submitted = MarkSubmittedCommand(
        submitted_at=START,
        note="  已人工报送  ",
        expected_version=2,
    )
    voided = VoidReportCommand(reason="  名单有误  ", expected_version=3)

    assert submitted.note == "已人工报送"
    assert voided.reason == "名单有误"
    with pytest.raises(ValueError):
        MarkSubmittedCommand(
            submitted_at=START.replace(tzinfo=None),
            note=None,
            expected_version=1,
        )
    with pytest.raises(ValueError):
        VoidReportCommand(reason="  ", expected_version=1)
    with pytest.raises(ValueError):
        VoidReportCommand(reason="错", expected_version=1)
    with pytest.raises(ValueError):
        MarkSubmittedCommand(
            submitted_at=START,
            note="身份证 11010519491231002X",
            expected_version=1,
        )
    with pytest.raises(ValueError):
        VoidReportCommand(reason="联系电话 13800138000", expected_version=1)
    with pytest.raises(ValueError):
        VoidReportCommand(reason="原因", expected_version=0)


def test_query_and_response_boundaries_exclude_snapshot_material() -> None:
    query = ReportBatchQuery(page=2, page_size=25)
    assert (query.page, query.page_size) == (2, 25)
    ranged = ReportBatchQuery(range_start=START, range_end=END)
    assert (ranged.range_start, ranged.range_end) == (START, END)
    with pytest.raises(ValueError):
        ReportBatchQuery(page=0)
    with pytest.raises(ValueError):
        ReportBatchQuery(page_size=101)
    with pytest.raises(ValueError):
        ReportBatchQuery(range_start=END, range_end=START)

    forbidden = {
        "snapshot_ciphertext",
        "snapshot_key_version",
        "snapshot_digest",
        "content",
    }
    assert forbidden.isdisjoint(field.name for field in fields(ReportBatchSummary))
    assert forbidden.isdisjoint(field.name for field in fields(ReportBatchDetail))

    summary_fields = {field.name for field in fields(ReportBatchSummary)}
    detail_fields = {field.name for field in fields(ReportBatchDetail)}
    assert {"scopes", "allowed_actions"} <= summary_fields
    assert {"submission_note", "void_reason", "failure_summary"}.isdisjoint(
        summary_fields
    )
    assert {"submission_note", "void_reason", "failure_summary"} <= detail_fields


def test_generate_persists_snapshot_scopes_and_allowed_audit_in_one_rr_transaction(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as reporting_db:
        summary = _service(context, reporting_db).generate(_floor_command(context))

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        batch = observer.get(HousingReportBatch, summary.id)
        assert batch is not None
        scopes = list(
            observer.scalars(
                select(HousingReportBatchScope).where(
                    HousingReportBatchScope.batch_id == summary.id
                )
            )
        )
        audits = list(
            observer.scalars(
                select(AuditEvent).where(AuditEvent.resource_id == summary.id)
            )
        )

    assert summary.status == "generated"
    assert (summary.resident_count, summary.stay_row_count) == (1, 1)
    assert batch.snapshot_ciphertext is not None
    assert batch.snapshot_digest is not None
    assert [(scope.building_id, scope.floor_id) for scope in scopes] == [
        (context.building_id, context.floor_id)
    ]
    assert [(event.action, event.outcome) for event in audits] == [
        ("housing.report_batch.generate", "allowed")
    ]
    assert context.connection.get_isolation_level() == "REPEATABLE READ"


def test_generate_configures_repeatable_read_before_access_service_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class StopAfterAccess(RuntimeError):
        pass

    class FakeConnection:
        def get_isolation_level(self) -> str:
            return "REPEATABLE READ"

    class RecordingSession:
        def in_transaction(self) -> bool:
            return False

        def get_bind(self) -> object:
            return object()

        def connection(self, *, execution_options: dict[str, str]) -> FakeConnection:
            calls.append(("connection", execution_options))
            return FakeConnection()

    class AccessProbe:
        def __init__(self, db: object, account_id: UUID, occurred_at: datetime) -> None:
            del db, account_id, occurred_at
            calls.append("access")
            raise StopAfterAccess

    monkeypatch.setattr(
        reporting_service_module,
        "HousingAccessService",
        AccessProbe,
    )
    service = HousingReportingService(
        RecordingSession(),  # type: ignore[arg-type]
        EnvelopeCipher(b"r" * 32),
        uuid4(),
        END,
        "rr-order-test",
    )
    with pytest.raises(StopAfterAccess):
        service.generate(
            GenerateReportCommand(
                range_start=START,
                range_end=END,
                scope_mode="authorized",
                purpose="顺序测试",
            )
        )

    assert calls == [
        ("connection", {"isolation_level": "REPEATABLE READ"}),
        "access",
    ]


@pytest.mark.parametrize("scope_mode", ["authorized", "buildings"])
def test_generate_expands_authorized_and_building_scopes_without_partial_selection(
    reporting_context: ReportingContext,
    scope_mode: str,
) -> None:
    context = reporting_context
    command_values: dict[str, object] = {
        "range_start": START,
        "range_end": END,
        "scope_mode": scope_mode,
        "purpose": "派出所人工报送",
    }
    if scope_mode == "buildings":
        command_values["building_ids"] = (context.building_id,)
    command = GenerateReportCommand(**command_values)  # type: ignore[arg-type]

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as reporting_db:
        summary = _service(context, reporting_db).generate(command)

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        persisted = set(
            observer.execute(
                select(
                    HousingReportBatchScope.building_id,
                    HousingReportBatchScope.floor_id,
                ).where(HousingReportBatchScope.batch_id == summary.id)
            ).tuples()
        )

    assert (context.building_id, context.floor_id) in persisted
    if scope_mode == "buildings":
        assert persisted == {(context.building_id, context.floor_id)}


@pytest.mark.parametrize("denial_kind", ["explicit_deny", "missing"])
def test_generate_scope_denial_rolls_back_for_route_audit_and_writes_no_batch(
    reporting_context: ReportingContext,
    denial_kind: str,
) -> None:
    context = reporting_context
    requested_floor_id = context.floor_id
    if denial_kind == "explicit_deny":
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
        ) as setup_db:
            permission_id = setup_db.scalar(
                select(Permission.id).where(
                    Permission.key == "housing.reports.export_sensitive"
                )
            )
            assert permission_id is not None
            setup_db.add(
                UserPermissionOverride(
                    user_id=context.actor_id,
                    permission_id=permission_id,
                    effect="deny",
                    scope_kind="floor",
                    scope_id=context.floor_id,
                    created_by_user_id=context.actor_id,
                    grant_reason="Task 4 范围拒绝测试",
                )
            )
            setup_db.commit()
    else:
        requested_floor_id = uuid4()

    command = GenerateReportCommand(
        range_start=START,
        range_end=END,
        scope_mode="floors",
        floor_ids=(requested_floor_id,),
        purpose="派出所人工报送",
    )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as reporting_db, pytest.raises(HousingReportScopeDenied) as caught:
        _service(context, reporting_db).generate(command)

    assert caught.value.code == "housing_report_scope_denied"
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        assert observer.scalar(select(func.count(HousingReportBatch.id))) == 0
        audits = list(observer.scalars(select(AuditEvent)))
    assert audits == []


@pytest.mark.parametrize("failure_kind", ["empty", "incomplete"])
def test_generate_commits_only_safe_failed_batch_then_raises_public_error(
    reporting_context: ReportingContext,
    failure_kind: str,
) -> None:
    context = reporting_context
    command = _floor_command(context, person_types=("student",))
    expected_error: type[HousingReportEmpty | HousingExportIncomplete]
    expected_summary: dict[str, object]
    if failure_kind == "empty":
        expected_error = HousingReportEmpty
        expected_summary = {"issue_count": 0, "field_counts": {}}
    else:
        expected_error = HousingExportIncomplete
        expected_summary = {"issue_count": 1, "field_counts": {"phone": 1}}
        command = _floor_command(context)
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
        ) as setup_db:
            resident = setup_db.get(Person, context.resident_id)
            assert resident is not None
            resident.phone_ciphertext = None
            setup_db.commit()

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as reporting_db, pytest.raises(expected_error) as caught:
        _service(context, reporting_db).generate(command)

    assert caught.value.code == expected_error.code
    assert caught.value.failure_summary == expected_summary
    if failure_kind == "incomplete":
        assert len(caught.value.issues) == 1
        issue = caught.value.issues[0]
        assert issue.person_id == context.resident_id
        assert issue.person_name == "完整资料住户"
        assert issue.masked_phone is None
        assert issue.missing_fields == ("phone",)
    else:
        assert caught.value.issues == ()
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        batch = observer.scalar(select(HousingReportBatch))
        assert batch is not None
        scopes = list(observer.scalars(select(HousingReportBatchScope)))
        audit = observer.scalar(select(AuditEvent))
        assert audit is not None

    assert batch.id == caught.value.batch_id
    assert batch.status == "failed"
    assert batch.failure_code == expected_error.code
    assert batch.failure_summary == expected_summary
    assert batch.snapshot_ciphertext is None
    assert batch.snapshot_key_version is None
    assert batch.snapshot_digest is None
    assert [(scope.building_id, scope.floor_id) for scope in scopes] == [
        (context.building_id, context.floor_id)
    ]
    assert (audit.action, audit.outcome, audit.event_metadata) == (
        "housing.report_batch.generate",
        "failed",
        {
            "error_code": expected_error.code,
            "result_count": expected_summary["issue_count"],
        },
    )
    persisted_text = str(batch.failure_summary)
    for forbidden in (
        "完整资料住户",
        "13800138000",
        _identity_for(date(1990, 1, 2), male=True),
        context.room_id.hex,
    ):
        assert forbidden not in persisted_text


def test_generate_audit_failure_rolls_back_every_write_and_session_remains_usable(
    reporting_context: ReportingContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = reporting_context

    def reject_audit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("forced audit outage")

    monkeypatch.setattr(AuditService, "append", reject_audit)
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as reporting_db:
        with pytest.raises(HousingAuditFailure) as caught:
            _service(context, reporting_db).generate(_floor_command(context))
        assert caught.value.code == "housing_audit_failed"
        assert reporting_db.scalar(select(func.count(HousingReportBatch.id))) == 0
        assert reporting_db.scalar(select(func.count(AuditEvent.id))) == 0

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        assert observer.scalar(select(func.count(HousingReportBatch.id))) == 0
        assert observer.scalar(select(func.count(HousingReportBatchScope.batch_id))) == 0
        assert observer.scalar(select(func.count(AuditEvent.id))) == 0


def test_list_counts_and_detail_share_safe_batch_summary_boundaries(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as list_db:
        page = _service(context, list_db).list(ReportBatchQuery(page_size=10))
        expected_access_revision = HousingAccessService(
            list_db,
            context.actor_id,
            END,
        ).access_revision
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as detail_db:
        detail = _service(context, detail_db).get(generated.id)

    assert page.items == (generated,)
    assert (page.total, page.page, page.page_size) == (1, 1, 10)
    assert page.access_revision == expected_access_revision
    building_options = {item.id: item.name for item in page.filter_options.buildings}
    floor_options = {
        item.id: (item.building_id, item.name)
        for item in page.filter_options.floors
    }
    assert context.building_id in building_options
    assert floor_options[context.floor_id][0] == context.building_id
    assert page.filter_options.cohorts == ()
    assert asdict(page.status_counts) == {
        "total": 1,
        "generated": 1,
        "marked_submitted": 0,
        "voided": 0,
        "failed": 0,
    }
    assert detail.id == generated.id
    assert detail.scopes == (
        ReportBatchScopeSummary(
            building_id=context.building_id,
            building_name=building_options[context.building_id],
            floor_id=context.floor_id,
            floor_name=floor_options[context.floor_id][1],
        ),
    )
    assert detail.created_by_name == "报送操作员"
    assert detail.submitted_by_name is None
    assert detail.voided_by_name is None
    assert all(
        isinstance(link, ReportBatchCorrectionLink)
        for link in detail.correction_chain
    )
    assert tuple(link.id for link in detail.correction_chain) == (generated.id,)
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        actions = list(observer.scalars(select(AuditEvent.action).order_by(AuditEvent.id)))
    assert set(actions) == {
        "housing.report_batch.generate",
        "housing.report_batch.list",
        "housing.report_batch.read",
    }


def test_list_option_query_failure_does_not_commit_allowed_audit(
    reporting_context: ReportingContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = reporting_context

    def fail_filter_options(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise reporting_service_module.SQLAlchemyError("forced option query failure")

    monkeypatch.setattr(
        HousingReportingService,
        "_filter_options",
        fail_filter_options,
    )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as list_db, pytest.raises(reporting_service_module.HousingPersistenceFailure):
        _service(context, list_db).list(ReportBatchQuery())

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        assert observer.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "housing.report_batch.list"
            )
        ) == 0


def test_list_sanitizes_phone_like_operator_name(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as mutation_db:
        actor_person_id = mutation_db.scalar(
            select(UserAccount.person_id).where(UserAccount.id == context.actor_id)
        )
        assert actor_person_id is not None
        actor_person = mutation_db.get(Person, actor_person_id)
        assert actor_person is not None
        actor_person.full_name = "13800138000"
        mutation_db.commit()
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        _service(context, generation_db).generate(_floor_command(context))
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as list_db:
        page = _service(context, list_db).list(ReportBatchQuery())

    assert page.items[0].created_by_name == "敏感内容已隐藏"


def test_revoking_one_of_multiple_scopes_hides_batch_and_private_get_is_equivalent(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    revoked_floor_id = uuid4()
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as setup_db:
        setup_db.add(
            HousingFloor(
                id=revoked_floor_id,
                building_id=context.building_id,
                number=3,
                name=f"待撤权楼层{uuid4().hex[:8]}",
                display_order=3,
                is_active=True,
            )
        )
        setup_db.commit()
    command = GenerateReportCommand(
        range_start=START,
        range_end=END,
        scope_mode="buildings",
        building_ids=(context.building_id,),
        purpose="派出所人工报送",
    )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(command)

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as setup_db:
        permission_id = setup_db.scalar(
            select(Permission.id).where(
                Permission.key == "housing.reports.export_sensitive"
            )
        )
        assert permission_id is not None
        setup_db.add(
            UserPermissionOverride(
                user_id=context.actor_id,
                permission_id=permission_id,
                effect="deny",
                scope_kind="floor",
                scope_id=revoked_floor_id,
                created_by_user_id=context.actor_id,
                grant_reason="Task 4 撤权测试",
            )
        )
        setup_db.commit()

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as list_db:
        page = _service(context, list_db).list(ReportBatchQuery())
    assert page.items == ()
    assert page.total == 0
    assert page.status_counts.total == 0

    errors: list[HousingResourceNotFound] = []
    for batch_id in (generated.id, uuid4()):
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as detail_db, pytest.raises(HousingResourceNotFound) as caught:
            _service(context, detail_db).get(batch_id)
        errors.append(caught.value)
    assert [(error.code, str(error)) for error in errors] == [
        ("housing_resource_not_found", "housing report batch not found"),
        ("housing_resource_not_found", "housing report batch not found"),
    ]


def test_mark_submitted_then_void_locks_and_updates_only_state_fields(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))

    lock_statements: list[str] = []

    def capture_lock(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, execution_context, executemany
        if "FOR UPDATE" in statement.upper():
            lock_statements.append(statement)

    event.listen(context.connection, "before_cursor_execute", capture_lock)
    try:
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as submit_db:
            submitted = _service(context, submit_db).mark_submitted(
                generated.id,
                MarkSubmittedCommand(
                    submitted_at=END - timedelta(hours=1),
                    note="  已人工报送  ",
                    expected_version=1,
                ),
            )
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as void_db:
            voided = _service(context, void_db).void(
                generated.id,
                VoidReportCommand(reason="  名单有误  ", expected_version=2),
            )
    finally:
        event.remove(context.connection, "before_cursor_execute", capture_lock)

    assert submitted.status == "marked_submitted"
    assert submitted.version == 2
    assert submitted.submitted_at == END - timedelta(hours=1)
    assert submitted.voided_at is None
    assert voided.status == "voided"
    assert voided.version == 3
    assert voided.submitted_at == submitted.submitted_at
    assert voided.voided_at == END
    assert len(lock_statements) == 2
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        batch = observer.get(HousingReportBatch, generated.id)
        assert batch is not None
        state_actions = list(
            observer.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.action.in_(
                        (
                            "housing.report_batch.mark_submitted",
                            "housing.report_batch.void",
                        )
                    )
                )
            )
        )
    assert (batch.status, batch.version) == ("voided", 3)
    assert batch.submission_note == "已人工报送"
    assert batch.void_reason == "名单有误"
    assert set(state_actions) == {
        "housing.report_batch.mark_submitted",
        "housing.report_batch.void",
    }


@pytest.mark.parametrize("conflict_kind", ["stale_version", "illegal_state"])
def test_state_conflict_is_stable_audited_and_leaves_session_usable(
    reporting_context: ReportingContext,
    conflict_kind: str,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as submit_db:
        submitted = _service(context, submit_db).mark_submitted(
            generated.id,
            MarkSubmittedCommand(
                submitted_at=END - timedelta(hours=1),
                note=None,
                expected_version=1,
            ),
        )

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as conflict_db:
        service = _service(context, conflict_db)
        with pytest.raises(HousingReportStateConflict) as caught:
            if conflict_kind == "stale_version":
                service.void(
                    generated.id,
                    VoidReportCommand(reason="名单有误", expected_version=1),
                )
            else:
                service.mark_submitted(
                    generated.id,
                    MarkSubmittedCommand(
                        submitted_at=END - timedelta(minutes=30),
                        note=None,
                        expected_version=2,
                    ),
                )
        assert caught.value.code == "housing_report_state_conflict"
        persisted = conflict_db.get(HousingReportBatch, generated.id)
        assert persisted is not None
        assert (persisted.status, persisted.version) == (
            submitted.status,
            submitted.version,
        )

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        conflict_audits = list(
            observer.scalars(
                select(AuditEvent).where(
                    AuditEvent.outcome == "failed",
                    AuditEvent.event_metadata["error_code"].astext
                    == "housing_report_state_conflict",
                )
            )
        )
    assert len(conflict_audits) == 1


def test_download_uses_only_historical_snapshot_for_both_formats_and_audits_each(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as mutation_db:
        resident = mutation_db.get(Person, context.resident_id)
        assert resident is not None
        resident.full_name = "生成后变更姓名"
        resident.phone_ciphertext = context.cipher.encrypt(
            "13900139000",
            field_name="phone",
        )
        mutation_db.commit()

    lock_statements: list[str] = []

    def capture_lock(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, execution_context, executemany
        if "FOR UPDATE" in statement.upper():
            lock_statements.append(statement)

    event.listen(context.connection, "before_cursor_execute", capture_lock)
    try:
        downloads = {}
        for report_format in ("xlsx", "csv"):
            with Session(
                bind=context.connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            ) as download_db:
                downloads[report_format] = _service(context, download_db).download(
                    generated.id,
                    report_format,
                )
    finally:
        event.remove(context.connection, "before_cursor_execute", capture_lock)

    xlsx = downloads["xlsx"]
    csv_zip = downloads["csv"]
    assert xlsx.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert csv_zip.media_type == "application/zip"
    assert re.fullmatch(r"housing-report-HR-\d{8}-\d{6}\.xlsx", xlsx.filename)
    assert re.fullmatch(r"housing-report-HR-\d{8}-\d{6}\.zip", csv_zip.filename)

    workbook = load_workbook(BytesIO(xlsx.content), read_only=True, data_only=True)
    resident_sheet = workbook["入住名单"]
    xlsx_rows = list(resident_sheet.iter_rows(values_only=True))
    xlsx_name_index = xlsx_rows[0].index("姓名")
    assert xlsx_rows[1][xlsx_name_index] == "完整资料住户"
    workbook.close()
    with ZipFile(BytesIO(csv_zip.content)) as archive:
        csv_rows = list(
            csv.reader(
                StringIO(archive.read("入住名单.csv").decode("utf-8-sig"))
            )
        )
    csv_name_index = csv_rows[0].index("姓名")
    assert csv_rows[1][csv_name_index] == "完整资料住户"
    assert len(lock_statements) == 2
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        download_actions = set(
            observer.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.action.like("housing.report_batch.download.%")
                )
            )
        )
    assert download_actions == {
        "housing.report_batch.download.xlsx",
        "housing.report_batch.download.csv",
    }


@pytest.mark.parametrize("failure_kind", ["snapshot", "render"])
def test_download_infrastructure_failure_is_stable_audited_and_session_usable(
    reporting_context: ReportingContext,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))

    expected_error: type[
        HousingReportSnapshotUnavailable | HousingReportRenderFailed
    ]
    if failure_kind == "snapshot":
        expected_error = HousingReportSnapshotUnavailable

        def fail_snapshot(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise SnapshotIntegrityError("forced corrupt snapshot")

        monkeypatch.setattr(reporting_service_module, "open_projection", fail_snapshot)
    else:
        expected_error = HousingReportRenderFailed

        def fail_render(*args: object, **kwargs: object) -> bytes:
            del args, kwargs
            raise RuntimeError("forced renderer outage")

        monkeypatch.setattr(reporting_service_module, "render_xlsx", fail_render)

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as download_db:
        with pytest.raises(expected_error) as caught:
            _service(context, download_db).download(generated.id, "xlsx")
        assert caught.value.code == expected_error.code
        assert download_db.scalar(select(func.count(HousingReportBatch.id))) == 1

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        failures = list(
            observer.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "housing.report_batch.download.xlsx",
                    AuditEvent.outcome == "failed",
                )
            )
        )
    assert len(failures) == 1
    assert failures[0].event_metadata == {"error_code": expected_error.code}


def test_download_rejects_projection_that_does_not_match_batch_metadata(
    reporting_context: ReportingContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as snapshot_db:
        batch = snapshot_db.get(HousingReportBatch, generated.id)
        assert batch is not None
        assert batch.snapshot_ciphertext is not None
        assert batch.snapshot_key_version is not None
        assert batch.snapshot_digest is not None
        projection = reporting_service_module.open_projection(
            reporting_service_module.SealedSnapshot(
                ciphertext=batch.snapshot_ciphertext,
                key_version=batch.snapshot_key_version,
                digest=batch.snapshot_digest,
            ),
            cipher=context.cipher,
        )
    mismatched = projection.model_copy(
        update={"range_end": projection.range_end + timedelta(days=1)}
    )
    monkeypatch.setattr(
        reporting_service_module,
        "open_projection",
        lambda *args, **kwargs: mismatched,
    )

    with (
        Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as download_db,
        pytest.raises(HousingReportSnapshotUnavailable) as caught,
    ):
        _service(context, download_db).download(generated.id, "xlsx")
    assert caught.value.code == "housing_report_snapshot_unavailable"


def test_correction_locks_voided_predecessor_and_builds_direct_chain(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        predecessor = _service(context, generation_db).generate(
            _floor_command(context)
        )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as void_db:
        voided = _service(context, void_db).void(
            predecessor.id,
            VoidReportCommand(reason="原名单有误", expected_version=1),
        )
    assert voided.status == "voided"

    lock_statements: list[str] = []

    def capture_lock(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, execution_context, executemany
        if "FOR UPDATE" in statement.upper():
            lock_statements.append(statement)

    event.listen(context.connection, "before_cursor_execute", capture_lock)
    try:
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as correction_db:
            correction = _service(context, correction_db).generate(
                _floor_command(context, corrects_batch_id=predecessor.id)
            )
    finally:
        event.remove(context.connection, "before_cursor_execute", capture_lock)

    assert correction.corrects_batch_id == predecessor.id
    assert len(lock_statements) == 1
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as detail_db:
        predecessor_detail = _service(context, detail_db).get(predecessor.id)
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as detail_db:
        correction_detail = _service(context, detail_db).get(correction.id)

    assert predecessor_detail.allowed_actions.correct is False
    assert tuple(link.id for link in predecessor_detail.correction_chain) == (
        predecessor.id,
        correction.id,
    )
    assert tuple(link.id for link in correction_detail.correction_chain) == (
        predecessor.id,
        correction.id,
    )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        correction_audit = observer.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "housing.report_batch.correct",
                AuditEvent.outcome == "allowed",
            )
        )
    assert correction_audit is not None
    assert correction_audit.resource_id == correction.id


def test_correction_relationships_do_not_reveal_batches_outside_current_scope(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        predecessor = _service(context, generation_db).generate(
            _floor_command(context)
        )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as void_db:
        _service(context, void_db).void(
            predecessor.id,
            VoidReportCommand(reason="原名单有误", expected_version=1),
        )

    successor_floor_id = uuid4()
    successor_id = uuid4()
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as setup_db:
        setup_db.add(
            HousingFloor(
                id=successor_floor_id,
                building_id=context.building_id,
                number=3,
                name=f"更正楼层{uuid4().hex[:8]}",
                display_order=3,
                is_active=True,
            )
        )
        source = setup_db.get(HousingReportBatch, predecessor.id)
        assert source is not None
        setup_db.add(
            HousingReportBatch(
                id=successor_id,
                status="generated",
                version=1,
                range_start=source.range_start,
                range_end=source.range_end,
                cohort_filter=list(source.cohort_filter),
                person_type_filter=list(source.person_type_filter),
                purpose=source.purpose,
                projection_schema_version=source.projection_schema_version,
                snapshot_ciphertext=source.snapshot_ciphertext,
                snapshot_key_version=source.snapshot_key_version,
                snapshot_digest=source.snapshot_digest,
                resident_count=source.resident_count,
                stay_row_count=source.stay_row_count,
                guardian_row_count=source.guardian_row_count,
                created_at=END,
                created_by_user_id=context.actor_id,
                corrects_batch_id=source.id,
            )
        )
        setup_db.flush()
        setup_db.add(
            HousingReportBatchScope(
                batch_id=successor_id,
                building_id=context.building_id,
                floor_id=successor_floor_id,
            )
        )
        permission_id = setup_db.scalar(
            select(Permission.id).where(
                Permission.key == "housing.reports.export_sensitive"
            )
        )
        assert permission_id is not None
        hidden_successor_rule = UserPermissionOverride(
            user_id=context.actor_id,
            permission_id=permission_id,
            effect="deny",
            scope_kind="floor",
            scope_id=successor_floor_id,
            created_by_user_id=context.actor_id,
            grant_reason="隐藏更正后继",
        )
        setup_db.add(hidden_successor_rule)
        setup_db.commit()

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as parent_db:
        parent_page = _service(context, parent_db).list(ReportBatchQuery())
    assert tuple(item.id for item in parent_page.items) == (predecessor.id,)
    assert parent_page.items[0].allowed_actions.correct is True
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as parent_db:
        parent_detail = _service(context, parent_db).get(predecessor.id)
    assert parent_detail.allowed_actions.correct is True
    assert tuple(link.id for link in parent_detail.correction_chain) == (
        predecessor.id,
    )

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as setup_db:
        setup_db.execute(
            delete(UserPermissionOverride).where(
                UserPermissionOverride.id == hidden_successor_rule.id
            )
        )
        setup_db.add(
            UserPermissionOverride(
                user_id=context.actor_id,
                permission_id=permission_id,
                effect="deny",
                scope_kind="floor",
                scope_id=context.floor_id,
                created_by_user_id=context.actor_id,
                grant_reason="隐藏更正前驱",
            )
        )
        setup_db.commit()

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as child_db:
        child_page = _service(context, child_db).list(ReportBatchQuery())
    assert tuple(item.id for item in child_page.items) == (successor_id,)
    assert child_page.items[0].corrects_batch_id is None
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as child_db:
        child_detail = _service(context, child_db).get(successor_id)
    assert child_detail.corrects_batch_id is None
    assert tuple(link.id for link in child_detail.correction_chain) == (successor_id,)


def test_list_then_scope_revoke_blocks_download_and_state_action_as_private_not_found(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as list_db:
        page = _service(context, list_db).list(ReportBatchQuery())
    assert [item.id for item in page.items] == [generated.id]

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as setup_db:
        permission_id = setup_db.scalar(
            select(Permission.id).where(
                Permission.key == "housing.reports.export_sensitive"
            )
        )
        assert permission_id is not None
        setup_db.add(
            UserPermissionOverride(
                user_id=context.actor_id,
                permission_id=permission_id,
                effect="deny",
                scope_kind="floor",
                scope_id=context.floor_id,
                created_by_user_id=context.actor_id,
                grant_reason="Task 4 list 后撤权",
            )
        )
        setup_db.commit()

    caught_errors: list[HousingResourceNotFound] = []
    for operation in ("download", "void", "missing_download"):
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as operation_db:
            service = _service(context, operation_db)
            with pytest.raises(HousingResourceNotFound) as caught:
                if operation == "void":
                    service.void(
                        generated.id,
                        VoidReportCommand(reason="不应作废", expected_version=1),
                    )
                else:
                    service.download(
                        generated.id if operation == "download" else uuid4(),
                        "xlsx",
                    )
            caught_errors.append(caught.value)

    assert {(error.code, str(error)) for error in caught_errors} == {
        ("housing_resource_not_found", "housing report batch not found")
    }
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        batch = observer.get(HousingReportBatch, generated.id)
        assert batch is not None
        allowed_downloads = observer.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "housing.report_batch.download.xlsx",
                AuditEvent.outcome == "allowed",
            )
        )
    assert (batch.status, batch.version) == ("generated", 1)
    assert allowed_downloads == 0


def test_mark_submitted_rejects_future_instant_before_state_write(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as submit_db, pytest.raises(ValueError, match="future"):
        _service(context, submit_db).mark_submitted(
            generated.id,
            MarkSubmittedCommand(
                submitted_at=END + timedelta(microseconds=1),
                note=None,
                expected_version=1,
            ),
        )

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        batch = observer.get(HousingReportBatch, generated.id)
        assert batch is not None
        mark_audits = observer.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "housing.report_batch.mark_submitted"
            )
        )
    assert (batch.status, batch.version) == ("generated", 1)
    assert mark_audits == 0


def test_voided_batch_download_is_stable_state_conflict(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as void_db:
        voided = _service(context, void_db).void(
            generated.id,
            VoidReportCommand(reason="名单有误", expected_version=1),
        )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as download_db, pytest.raises(HousingReportStateConflict) as caught:
        _service(context, download_db).download(voided.id, "xlsx")
    assert caught.value.code == "housing_report_state_conflict"
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        batch = observer.get(HousingReportBatch, generated.id)
        assert batch is not None
        conflict = observer.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "housing.report_batch.download.xlsx",
                AuditEvent.outcome == "failed",
            )
        )
    assert (batch.status, batch.version) == ("voided", 2)
    assert conflict is not None
    assert conflict.event_metadata == {"error_code": "housing_report_state_conflict"}


@pytest.mark.parametrize("invalid_kind", ["not_voided", "already_corrected"])
def test_correction_rejects_nonvoided_or_already_corrected_predecessor_stably(
    reporting_context: ReportingContext,
    invalid_kind: str,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        predecessor = _service(context, generation_db).generate(
            _floor_command(context)
        )
    expected_batch_count = 1
    if invalid_kind == "already_corrected":
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as void_db:
            _service(context, void_db).void(
                predecessor.id,
                VoidReportCommand(reason="原名单有误", expected_version=1),
            )
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as correction_db:
            _service(context, correction_db).generate(
                _floor_command(context, corrects_batch_id=predecessor.id)
            )
        expected_batch_count = 2

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as invalid_db, pytest.raises(HousingReportCorrectionInvalid) as caught:
        _service(context, invalid_db).generate(
            _floor_command(context, corrects_batch_id=predecessor.id)
        )
    assert caught.value.code == "housing_report_correction_invalid"
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        assert (
            observer.scalar(select(func.count(HousingReportBatch.id)))
            == expected_batch_count
        )
        invalid_audits = list(
            observer.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "housing.report_batch.correct",
                    AuditEvent.outcome == "failed",
                    AuditEvent.event_metadata["error_code"].astext
                    == "housing_report_correction_invalid",
                )
            )
        )
    assert len(invalid_audits) == 1


def test_generate_encryption_failure_rolls_back_without_fake_failed_batch(
    reporting_context: ReportingContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = reporting_context

    def fail_seal(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("forced encryption outage")

    monkeypatch.setattr(reporting_service_module, "seal_projection", fail_seal)
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        with pytest.raises(HousingReportSnapshotUnavailable) as caught:
            _service(context, generation_db).generate(_floor_command(context))
        assert caught.value.code == "housing_report_snapshot_unavailable"
        assert generation_db.scalar(select(func.count(HousingReportBatch.id))) == 0

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        assert observer.scalar(select(func.count(HousingReportBatch.id))) == 0
        failure = observer.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "housing.report_batch.generate",
                AuditEvent.outcome == "failed",
            )
        )
    assert failure is not None
    assert failure.event_metadata == {
        "error_code": "housing_report_snapshot_unavailable"
    }


def test_correction_checks_concrete_predecessor_privately_before_new_scope_denial(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        predecessor = _service(context, generation_db).generate(
            _floor_command(context)
        )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as void_db:
        _service(context, void_db).void(
            predecessor.id,
            VoidReportCommand(reason="原名单有误", expected_version=1),
        )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as revoke_db:
        permission_id = revoke_db.scalar(
            select(Permission.id).where(
                Permission.key == "housing.reports.export_sensitive"
            )
        )
        assert permission_id is not None
        revoke_db.add(
            UserPermissionOverride(
                user_id=context.actor_id,
                permission_id=permission_id,
                effect="deny",
                scope_kind="floor",
                scope_id=context.floor_id,
                created_by_user_id=context.actor_id,
                grant_reason="更正前驱撤权",
            )
        )
        revoke_db.commit()

    with (
        Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as correction_db,
        pytest.raises(HousingResourceNotFound) as caught,
    ):
        _service(context, correction_db).generate(
            _floor_command(context, corrects_batch_id=predecessor.id)
        )
    assert caught.value.code == "housing_resource_not_found"
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        assert observer.scalar(select(func.count(HousingReportBatch.id))) == 1


def test_locked_state_action_refreshes_cached_batch_before_version_check(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as cached_db:
        generated = _service(context, cached_db).generate(_floor_command(context))
        assert cached_db.get(HousingReportBatch, generated.id) is not None
        with Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as competing_db:
            submitted = _service(context, competing_db).mark_submitted(
                generated.id,
                MarkSubmittedCommand(
                    submitted_at=END - timedelta(minutes=1),
                    note=None,
                    expected_version=1,
                ),
            )
        assert submitted.version == 2

        with pytest.raises(HousingReportStateConflict) as caught:
            _service(context, cached_db).void(
                generated.id,
                VoidReportCommand(reason="缓存状态不应覆盖数据库", expected_version=1),
            )
        assert caught.value.code == "housing_report_state_conflict"
        refreshed = cached_db.get(HousingReportBatch, generated.id)
        assert refreshed is not None
        assert (refreshed.status, refreshed.version) == ("marked_submitted", 2)


def test_failed_correction_does_not_consume_the_direct_correction_slot(
    reporting_context: ReportingContext,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        predecessor = _service(context, generation_db).generate(
            _floor_command(context)
        )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as void_db:
        _service(context, void_db).void(
            predecessor.id,
            VoidReportCommand(reason="原名单有误", expected_version=1),
        )
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as incomplete_db:
        resident = incomplete_db.get(Person, context.resident_id)
        assert resident is not None
        resident.phone_ciphertext = None
        incomplete_db.commit()

    with (
        Session(
            bind=context.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as failed_db,
        pytest.raises(HousingExportIncomplete) as failed,
    ):
        _service(context, failed_db).generate(
            _floor_command(context, corrects_batch_id=predecessor.id)
        )

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as repair_db:
        resident = repair_db.get(Person, context.resident_id)
        assert resident is not None
        resident.phone_ciphertext = context.cipher.encrypt(
            "13800138000",
            field_name="phone",
        )
        repair_db.commit()
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as retry_db:
        correction = _service(context, retry_db).generate(
            _floor_command(context, corrects_batch_id=predecessor.id)
        )

    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
    ) as observer:
        failed_batch = observer.get(HousingReportBatch, failed.value.batch_id)
        assert failed_batch is not None
        correction_batch = observer.get(HousingReportBatch, correction.id)
        assert correction_batch is not None
    assert failed_batch.status == "failed"
    assert failed_batch.corrects_batch_id is None
    assert correction_batch.corrects_batch_id == predecessor.id


def test_state_action_audit_failure_rolls_back_locked_transition(
    reporting_context: ReportingContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = reporting_context
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as generation_db:
        generated = _service(context, generation_db).generate(_floor_command(context))

    def reject_audit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("forced state audit outage")

    monkeypatch.setattr(AuditService, "append", reject_audit)
    with Session(
        bind=context.connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as state_db:
        with pytest.raises(HousingAuditFailure):
            _service(context, state_db).mark_submitted(
                generated.id,
                MarkSubmittedCommand(
                    submitted_at=END - timedelta(minutes=1),
                    note=None,
                    expected_version=1,
                ),
            )
        persisted = state_db.get(HousingReportBatch, generated.id)
        assert persisted is not None
        assert (persisted.status, persisted.version) == ("generated", 1)
        assert state_db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "housing.report_batch.mark_submitted",
                AuditEvent.resource_id == generated.id,
            )
        ) == 0
