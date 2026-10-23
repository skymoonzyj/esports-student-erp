from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from io import BytesIO, StringIO
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.food_preferences.errors import FoodPreferencePermissionDenied
from app.food_preferences.export_service import (
    FoodPreferenceExportFailed,
    FoodPreferenceExportService,
    PreparedFoodPreferenceExport,
    PrepareFoodPreferenceExportCommand,
)
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceCuisine,
    PersonFoodPreferenceDish,
)
from app.food_preferences.reporting import FoodPreferenceReportingService
from app.identity.models import Person, StaffProfile, StudentProfile
from app.rbac.models import Permission, Role, UserPermissionOverride, UserRole

NOW = datetime(2026, 8, 11, 12, 13, 14, tzinfo=UTC)


@dataclass
class _FailingTransactionSession:
    commit_error: Exception | None = None
    rollback_error: Exception | None = None
    commit_calls: int = 0
    rollback_calls: int = 0

    def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    @staticmethod
    def in_transaction() -> bool:
        return True


def _prepared_with_db(db: _FailingTransactionSession) -> PreparedFoodPreferenceExport:
    return PreparedFoodPreferenceExport(
        content=b"test-export",
        media_type="application/zip",
        filename="food-preferences-students-20260811T121314Z.zip",
        file_sha256=sha256(b"test-export").hexdigest(),
        db=cast(Session, db),
    )


@pytest.mark.parametrize("failure_stage", ("renderer", "audit"))
def test_prepare_constantizes_primary_failure_when_rollback_also_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    primary_secret = f"sensitive-{failure_stage}-failure"
    rollback_secret = "sensitive-prepare-rollback-failure"
    db = _FailingTransactionSession(
        rollback_error=RuntimeError(rollback_secret),
    )
    snapshot = SimpleNamespace(
        population="students",
        include_inactive=False,
        total_people=0,
        filled_people=0,
    )
    monkeypatch.setattr(
        FoodPreferenceReportingService,
        "build_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )

    def fail(*_args: object, **_kwargs: object) -> bytes:
        raise RuntimeError(primary_secret)

    if failure_stage == "renderer":
        monkeypatch.setattr(
            "app.food_preferences.export_service.render_xlsx",
            fail,
        )
    else:
        monkeypatch.setattr(
            "app.food_preferences.export_service.render_xlsx",
            lambda _snapshot: b"rendered-export",
        )
        monkeypatch.setattr(AuditService, "append", fail)
    service = FoodPreferenceExportService(
        cast(Session, db),
        account_id=uuid4(),
        occurred_at=NOW,
    )

    with pytest.raises(FoodPreferenceExportFailed) as caught:
        service.prepare(ExportScenario.command())

    assert str(caught.value) == "food preference export failed"
    assert caught.value.__cause__ is None
    assert primary_secret not in str(caught.value)
    assert rollback_secret not in str(caught.value)
    assert db.rollback_calls == 1


def test_prepare_preserves_food_error_when_rollback_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_error = FoodPreferencePermissionDenied("sensitive permission detail")
    db = _FailingTransactionSession(
        rollback_error=RuntimeError("sensitive prepare rollback failure"),
    )

    def deny(*_args: object, **_kwargs: object) -> object:
        raise original_error

    monkeypatch.setattr(FoodPreferenceReportingService, "build_snapshot", deny)
    service = FoodPreferenceExportService(
        cast(Session, db),
        account_id=uuid4(),
        occurred_at=NOW,
    )

    with pytest.raises(FoodPreferencePermissionDenied) as caught:
        service.prepare(ExportScenario.command())

    assert caught.value is original_error
    assert db.rollback_calls == 1


@pytest.mark.parametrize(
    "commit_error",
    (
        SQLAlchemyError("sensitive database commit failure"),
        RuntimeError("sensitive unexpected commit failure"),
    ),
    ids=("sqlalchemy", "unexpected"),
)
def test_complete_constantizes_commit_failure_when_rollback_also_fails(
    commit_error: Exception,
) -> None:
    rollback_secret = "sensitive complete rollback failure"
    db = _FailingTransactionSession(
        commit_error=commit_error,
        rollback_error=RuntimeError(rollback_secret),
    )

    with pytest.raises(FoodPreferenceExportFailed) as caught:
        _prepared_with_db(db).complete()

    assert str(caught.value) == "food preference export failed"
    assert caught.value.__cause__ is None
    assert str(commit_error) not in str(caught.value)
    assert rollback_secret not in str(caught.value)
    assert db.commit_calls == 1
    assert db.rollback_calls == 1


def test_cancel_constantizes_rollback_failure() -> None:
    rollback_secret = "sensitive cancel rollback failure"
    db = _FailingTransactionSession(
        rollback_error=RuntimeError(rollback_secret),
    )

    with pytest.raises(FoodPreferenceExportFailed) as caught:
        _prepared_with_db(db).cancel()

    assert str(caught.value) == "food preference export failed"
    assert caught.value.__cause__ is None
    assert rollback_secret not in str(caught.value)
    assert db.rollback_calls == 1


@dataclass(frozen=True, slots=True)
class ExportScenario:
    db: Session
    admin_account_id: UUID
    scoped_account_id: UUID

    def service(self, account_id: UUID | None = None) -> FoodPreferenceExportService:
        return FoodPreferenceExportService(
            self.db,
            account_id=account_id or self.admin_account_id,
            occurred_at=NOW,
        )

    @staticmethod
    def command(
        *,
        format: str = "xlsx",
        population: str = "students",
        include_inactive: bool = False,
        purpose: str = "安排下一期营地餐食",
        request_id: str = "food-export-request",
    ) -> PrepareFoodPreferenceExportCommand:
        return PrepareFoodPreferenceExportCommand(
            population=population,
            include_inactive=include_inactive,
            format=format,
            purpose=purpose,
            request_id=request_id,
        )


def _person(label: str) -> Person:
    person_id = uuid4()
    return Person(
        id=person_id,
        full_name=label,
        identity_ciphertext=f"food-export-cipher-{person_id.hex}",
        identity_digest=f"food-export-digest-{person_id.hex}",
        sex="female",
        date_of_birth=date(1990, 1, 1),
    )


@pytest.fixture
def export_scenario(db: Session) -> ExportScenario:
    admin = _person("导出管理员")
    scoped = _person("局部权限人员")
    student = _person("=公式学员")
    db.add_all([admin, scoped, student])
    db.flush()

    admin_account = UserAccount(
        id=uuid4(),
        person_id=admin.id,
        login_phone_digest=f"food-export-admin-phone-{uuid4().hex}",
        email_normalized=f"food-export-admin-{uuid4().hex}@example.test",
        password_hash="test-password",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    scoped_account = UserAccount(
        id=uuid4(),
        person_id=scoped.id,
        login_phone_digest=f"food-export-scoped-phone-{uuid4().hex}",
        email_normalized=f"food-export-scoped-{uuid4().hex}@example.test",
        password_hash="test-password",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add_all([admin_account, scoped_account])
    db.flush()
    other = db.scalar(
        select(FoodCuisineOption).where(FoodCuisineOption.is_other.is_(True))
    )
    admin_role_id = db.scalar(select(Role.id).where(Role.key == "admin"))
    export_permission_id = db.scalar(
        select(Permission.id).where(Permission.key == "food_preferences.export")
    )
    assert other is not None
    assert admin_role_id is not None
    assert export_permission_id is not None
    db.add_all(
        [
            StaffProfile(person_id=admin.id, staff_type="administration"),
            StaffProfile(person_id=scoped.id, staff_type="customer_service"),
            StudentProfile(
                person_id=student.id,
                student_number=f"FOOD-EXPORT-{uuid4().hex[:10]}",
                status="active",
            ),
            UserRole(user_id=admin_account.id, role_id=admin_role_id),
            UserPermissionOverride(
                user_id=scoped_account.id,
                permission_id=export_permission_id,
                effect="allow",
                scope_kind="person",
                scope_id=student.id,
            ),
            PersonFoodPreference(
                person_id=student.id,
                version=1,
                source="profile_admin",
                updated_by_user_id=admin_account.id,
                updated_at=NOW,
            ),
            PersonFoodPreferenceDish(
                person_id=student.id,
                position=1,
                display_name="@宫保鸡丁",
                normalized_name="@宫保鸡丁",
            ),
            PersonFoodPreferenceCuisine(
                person_id=student.id,
                position=1,
                cuisine_option_id=other.id,
                cuisine_name_snapshot="其他",
                custom_name="川菜",
                custom_normalized_name="川菜",
            ),
            PersonFoodPreferenceCuisine(
                person_id=student.id,
                position=2,
                cuisine_option_id=other.id,
                cuisine_name_snapshot="其他",
                custom_name="粤菜",
                custom_normalized_name="粤菜",
            ),
        ]
    )
    db.flush()
    return ExportScenario(
        db=db,
        admin_account_id=admin_account.id,
        scoped_account_id=scoped_account.id,
    )


@pytest.mark.parametrize(
    ("format", "extension", "media_type"),
    (
        (
            "xlsx",
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("csv_zip", "zip", "application/zip"),
    ),
)
def test_prepare_renders_current_snapshot_hash_and_uncommitted_allowlisted_audit(
    export_scenario: ExportScenario,
    format: str,
    extension: str,
    media_type: str,
) -> None:
    scenario = export_scenario
    before = scenario.db.scalar(select(func.count(AuditEvent.id))) or 0

    prepared = scenario.service().prepare(
        scenario.command(
            format=format,
            population="all_people",
            include_inactive=True,
            request_id=f"food-export-{format}",
        )
    )

    assert prepared.media_type == media_type
    assert prepared.filename == (
        f"food-preferences-all-people-20260811T121314Z.{extension}"
    )
    assert prepared.file_sha256 == sha256(prepared.content).hexdigest()
    assert prepared.db is scenario.db
    assert scenario.db.in_transaction()
    assert (scenario.db.scalar(select(func.count(AuditEvent.id))) or 0) == before + 1
    if format == "xlsx":
        workbook = load_workbook(BytesIO(prepared.content))
        assert workbook.sheetnames == [
            "人员偏好明细",
            "菜系统计",
            "菜品统计",
        ]
        people_rows = tuple(
            workbook["人员偏好明细"].iter_rows(values_only=True)
        )
        student_row = next(row for row in people_rows if row[0] == "'=公式学员")
    else:
        with ZipFile(BytesIO(prepared.content)) as archive:
            assert archive.namelist() == [
                "人员偏好明细.csv",
                "菜系统计.csv",
                "菜品统计.csv",
            ]
            people_csv = archive.read("人员偏好明细.csv").decode("utf-8-sig")
        people_rows = tuple(csv.reader(StringIO(people_csv)))
        student_row = next(row for row in people_rows if row[0] == "'=公式学员")

    cuisine_cells = tuple(value for value in student_row[4:7] if value)
    assert "、".join(cuisine_cells) == "川菜、粤菜"
    assert all("其他" not in value for value in cuisine_cells)

    prepared.complete()
    audit = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "food_preferences.export",
            AuditEvent.request_id == f"food-export-{format}",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == scenario.admin_account_id
    assert audit.resource_type == "food_preference_export"
    assert audit.resource_id is None
    assert audit.purpose == "安排下一期营地餐食"
    assert audit.outcome == "allowed"
    assert audit.event_metadata == {
        "population": "all_people",
        "include_inactive": True,
        "format": format,
        "person_count": 3,
        "filled_count": 1,
        "file_sha256": prepared.file_sha256,
    }


def test_scoped_export_permission_is_denied_before_renderer_or_audit(
    export_scenario: ExportScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = export_scenario
    render_calls = 0

    def forbidden_render(*args: object, **kwargs: object) -> bytes:
        nonlocal render_calls
        render_calls += 1
        return b"forbidden"

    monkeypatch.setattr(
        "app.food_preferences.export_service.render_xlsx",
        forbidden_render,
    )
    before = scenario.db.scalar(select(func.count(AuditEvent.id))) or 0

    with pytest.raises(Exception) as caught:
        scenario.service(scenario.scoped_account_id).prepare(scenario.command())

    assert getattr(caught.value, "code", None) == "food_preference_permission_denied"
    assert render_calls == 0
    assert (scenario.db.scalar(select(func.count(AuditEvent.id))) or 0) == before


def test_renderer_failure_is_constantized_and_rolls_back_without_audit(
    export_scenario: ExportScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = export_scenario

    def fail_render(*args: object, **kwargs: object) -> bytes:
        raise RuntimeError("=公式学员 @宫保鸡丁 secret-render-detail")

    monkeypatch.setattr(
        "app.food_preferences.export_service.render_xlsx",
        fail_render,
    )

    with pytest.raises(FoodPreferenceExportFailed) as caught:
        scenario.service().prepare(scenario.command())

    assert str(caught.value) == "food preference export failed"
    assert caught.value.__cause__ is None
    assert scenario.db.scalar(select(func.count(AuditEvent.id))) == 0


def test_audit_preflight_failure_returns_no_prepared_file_and_rolls_back(
    export_scenario: ExportScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = export_scenario

    def fail_audit(*args: object, **kwargs: object) -> AuditEvent:
        raise RuntimeError("secret-audit-detail")

    monkeypatch.setattr(AuditService, "append", fail_audit)

    with pytest.raises(FoodPreferenceExportFailed) as caught:
        scenario.service().prepare(scenario.command(format="csv_zip"))

    assert str(caught.value) == "food preference export failed"
    assert caught.value.__cause__ is None
    assert scenario.db.scalar(select(func.count(AuditEvent.id))) == 0


def test_cancel_rolls_back_pending_success_audit(export_scenario: ExportScenario) -> None:
    scenario = export_scenario
    prepared = scenario.service().prepare(scenario.command())
    assert scenario.db.scalar(select(func.count(AuditEvent.id))) == 1

    prepared.cancel()

    assert scenario.db.scalar(select(func.count(AuditEvent.id))) == 0


@pytest.mark.parametrize(
    "changes",
    (
        {"population": "coaches"},
        {"format": "csv"},
        {"purpose": "短"},
        {"purpose": "x" * 201},
        {"request_id": ""},
    ),
)
def test_export_command_rejects_invalid_internal_values(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "population": "students",
        "include_inactive": False,
        "format": "xlsx",
        "purpose": "安排下一期营地餐食",
        "request_id": "food-export-request",
    }
    values.update(changes)

    with pytest.raises(ValueError):
        PrepareFoodPreferenceExportCommand(**values)  # type: ignore[arg-type]
