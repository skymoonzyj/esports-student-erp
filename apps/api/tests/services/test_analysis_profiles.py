from __future__ import annotations

import copy
import json
import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, delete, event, func, inspect, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.analysis_profile_service import AnalysisProfileService
from app.evaluations.errors import (
    AnalysisAdapterNotAccepted,
    AnalysisProfileVersionConflict,
    InvalidAnalysisProfile,
)
from app.evaluations.schemas import AnalysisProfileCreate
from app.identity.models import Person
from app.teaching.catalog_service import TeachingCatalogService
from app.teaching.models import GameProject
from app.teaching.schemas import ProjectCreate
from app.teaching.seed import INITIAL_PROJECTS
from tests.account_identifiers import add_test_primary_login_identifier

NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def _service(db: Session) -> AnalysisProfileService:
    return AnalysisProfileService(db)


def _profile_input(**overrides: Any) -> AnalysisProfileCreate:
    values: dict[str, Any] = {
        "expected_current_version": 1,
        "mode": "manual_only",
        "adapter_id": None,
        "adapter_version": None,
        "scoring_rule_version": None,
        "required_fields": [],
        "confidence_thresholds": {},
        "auto_publish_warning_levels": [],
        "publish_mode": "review_required",
        "effective_from": NOW,
        "change_reason": "manual review remains required",
    }
    values.update(overrides)
    return AnalysisProfileCreate(**values)


@pytest.mark.parametrize(
    "private_key",
    [
        "13800138000",
        "11010519491231002X",
        "lease_token_correct_horse_battery_staple",
        "player_game_id_alpha123",
        "游戏账号-PlayerOne",
    ],
)
def test_profile_schema_rejects_private_or_non_public_result_keys(
    private_key: str,
) -> None:
    with pytest.raises(ValidationError):
        _profile_input(
            mode="automatic",
            adapter_id="lol",
            adapter_version="1.0.0",
            scoring_rule_version="1",
            required_fields=[private_key],
            confidence_thresholds={private_key: 0.8},
        )


@pytest.fixture
def actor_user_id(db: Session) -> UUID:
    marker = uuid4().hex
    person = Person(
        full_name="分析档案测试操作人",
        identity_ciphertext=f"analysis-profile-ciphertext-{marker}",
        identity_digest=f"analysis-profile-identity-{marker}",
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="female",
        date_of_birth=date(1988, 1, 2),
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"analysis-profile-phone-{marker}",
        email_normalized=f"analysis-profile-{marker}@example.test",
        password_hash="analysis-profile-test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    add_test_primary_login_identifier(db, account)
    return account.id


@pytest.fixture
def profile_project(db: Session, actor_user_id: UUID) -> GameProject:
    return TeachingCatalogService(db).create_project(
        ProjectCreate(name=f"分析档案项目-{uuid4().hex[:8]}"),
        actor_user_id=actor_user_id,
    )


def _snapshot(profile: ProjectAnalysisProfile) -> dict[str, Any]:
    return copy.deepcopy(
        {
            attribute.key: getattr(profile, attribute.key)
            for attribute in inspect(profile).mapper.column_attrs
        }
    )


def test_every_migrated_project_has_exactly_one_manual_only_version_one(
    db: Session,
) -> None:
    project_ids = {project.id for project in INITIAL_PROJECTS}
    profiles = list(
        db.scalars(
            select(ProjectAnalysisProfile).where(
                ProjectAnalysisProfile.project_id.in_(project_ids)
            )
        )
    )

    assert len(profiles) == len(project_ids)
    assert {profile.project_id for profile in profiles} == project_ids
    assert {
        (profile.version, profile.mode, profile.publish_mode) for profile in profiles
    } == {(1, "manual_only", "review_required")}


def test_profile_change_appends_version_without_mutating_version_one(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
) -> None:
    version_one = db.scalar(
        select(ProjectAnalysisProfile).where(
            ProjectAnalysisProfile.project_id == profile_project.id,
            ProjectAnalysisProfile.version == 1,
        )
    )
    assert version_one is not None
    original = _snapshot(version_one)

    version_two = _service(db).create_version(
        profile_project.id,
        _profile_input(effective_from=version_one.effective_from + timedelta(hours=1)),
        actor_user_id=actor_user_id,
        request_id="analysis-profile-version-two",
    )

    db.expire_all()
    stored_version_one = db.scalar(
        select(ProjectAnalysisProfile).where(ProjectAnalysisProfile.id == version_one.id)
    )
    assert stored_version_one is not None
    assert version_two.version == 2
    assert _snapshot(stored_version_one) == original
    assert db.scalar(
        select(func.count(ProjectAnalysisProfile.id)).where(
            ProjectAnalysisProfile.project_id == profile_project.id
        )
    ) == 2


def test_current_at_uses_effective_instant_then_version_as_tiebreaker(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
) -> None:
    version_one = db.scalar(
        select(ProjectAnalysisProfile).where(
            ProjectAnalysisProfile.project_id == profile_project.id,
            ProjectAnalysisProfile.version == 1,
        )
    )
    assert version_one is not None
    future = version_one.effective_from + timedelta(days=1)
    service = _service(db)
    version_two = service.create_version(
        profile_project.id,
        _profile_input(effective_from=future),
        actor_user_id=actor_user_id,
        request_id="analysis-profile-future-two",
    )
    version_three = service.create_version(
        profile_project.id,
        _profile_input(
            expected_current_version=2,
            effective_from=future,
            change_reason="same instant takes higher version",
        ),
        actor_user_id=actor_user_id,
        request_id="analysis-profile-future-three",
    )

    assert service.current_at(
        profile_project.id,
        occurred_at=version_one.effective_from - timedelta(microseconds=1),
    ) is None
    assert service.current_at(
        profile_project.id,
        occurred_at=version_one.effective_from,
    ).id == version_one.id
    assert service.current_at(
        profile_project.id,
        occurred_at=future - timedelta(microseconds=1),
    ).id == version_one.id
    assert version_two.version == 2
    assert service.current_at(profile_project.id, occurred_at=future).id == version_three.id


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"adapter_id": "adapter"},
        {"adapter_version": "v1"},
        {"scoring_rule_version": "rules-v1"},
        {"confidence_thresholds": {"score": 0.9}},
        {"auto_publish_warning_levels": ["warning"]},
        {"publish_mode": "auto_publish"},
    ],
)
def test_manual_only_rejects_automatic_configuration(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
    invalid_fields: dict[str, Any],
) -> None:
    with pytest.raises(InvalidAnalysisProfile) as error:
        _service(db).create_version(
            profile_project.id,
            _profile_input(**invalid_fields),
            actor_user_id=actor_user_id,
            request_id="analysis-profile-invalid-manual",
        )

    assert error.value.code == "invalid_analysis_profile"
    assert "adapter" not in str(error.value).lower()
    assert db.scalar(
        select(func.count(ProjectAnalysisProfile.id)).where(
            ProjectAnalysisProfile.project_id == profile_project.id
        )
    ) == 1


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"adapter_id": None},
        {"adapter_version": None},
        {"scoring_rule_version": None},
        {"required_fields": ["score", "score"]},
        {"confidence_thresholds": {}},
        {"confidence_thresholds": {"score": float("nan")}},
        {"confidence_thresholds": {"score": -0.1}},
        {"confidence_thresholds": {"score": 1.1}},
    ],
)
def test_automatic_profile_requires_complete_finite_exact_configuration(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
    invalid_fields: dict[str, Any],
) -> None:
    values = {
        "mode": "automatic",
        "adapter_id": "accepted-later",
        "adapter_version": "v1",
        "scoring_rule_version": "rules-v1",
        "required_fields": ["score"],
        "confidence_thresholds": {"score": 0.9},
    }
    values.update(invalid_fields)

    with pytest.raises(InvalidAnalysisProfile):
        _service(db).create_version(
            profile_project.id,
            _profile_input(**values),
            actor_user_id=actor_user_id,
            request_id="analysis-profile-invalid-automatic",
        )


def test_unknown_automatic_adapter_version_is_rejected(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
) -> None:
    with pytest.raises(AnalysisAdapterNotAccepted) as error:
        _service(db).create_version(
            profile_project.id,
            _profile_input(
                mode="automatic",
                adapter_id="not-accepted",
                adapter_version="v1",
                scoring_rule_version="rules-v1",
                required_fields=["score"],
                confidence_thresholds={"score": 0.9},
            ),
            actor_user_id=actor_user_id,
            request_id="analysis-profile-empty-registry",
        )

    assert error.value.code == "analysis_adapter_not_accepted"
    assert "not-accepted" not in str(error.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"adapter_version": "latest"},
        {"scoring_rule_version": "2"},
        {"result_schema_version": "2"},
    ],
)
def test_automatic_profile_requires_the_complete_registered_version_tuple(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
    overrides: dict[str, str],
) -> None:
    values = {
        "mode": "automatic",
        "adapter_id": "lol",
        "adapter_version": "1.0.0",
        "scoring_rule_version": "1",
        "result_schema_version": "1",
        "required_fields": ["score"],
        "confidence_thresholds": {"score": 0.9},
    }
    values.update(overrides)

    with pytest.raises(AnalysisAdapterNotAccepted):
        _service(db).create_version(
            profile_project.id,
            _profile_input(**values),
            actor_user_id=actor_user_id,
            request_id="analysis-profile-registry-tuple",
        )


def test_manual_only_profile_can_define_strict_result_fields_and_media_slots(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
) -> None:
    profile = _service(db).create_version(
        profile_project.id,
        _profile_input(
            required_fields=["score", "grade"],
            media_slots=[
                {"key": "result", "label": "结算截图", "required": True}
            ],
            result_schema_version="1",
        ),
        actor_user_id=actor_user_id,
        request_id="analysis-profile-manual-fields",
    )

    assert profile.mode == "manual_only"
    assert profile.required_fields == ["score", "grade"]
    assert profile.media_slots == [
        {"key": "result", "label": "结算截图", "required": True}
    ]
    assert profile.result_schema_version == "1"


def test_registered_automatic_adapter_versions_are_accepted(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
) -> None:
    profile = _service(db).create_version(
        profile_project.id,
        _profile_input(
            mode="automatic",
            adapter_id="lol",
            adapter_version="1.0.0",
            scoring_rule_version="1",
            result_schema_version="1",
            required_fields=["kills", "deaths", "assists"],
            confidence_thresholds={
                "kills": 0.8,
                "deaths": 0.8,
                "assists": 0.8,
            },
            media_slots=[
                {"key": "match_result", "label": "结算截图", "required": True}
            ],
        ),
        actor_user_id=actor_user_id,
        request_id="analysis-profile-lol-adapter",
    )

    assert (profile.adapter_id, profile.adapter_version) == ("lol", "1.0.0")
    assert profile.scoring_rule_version == "1"
    assert profile.result_schema_version == "1"


def test_profile_dto_requires_aware_effective_time_and_trimmed_reason() -> None:
    with pytest.raises(ValidationError):
        _profile_input(expected_current_version=0)
    with pytest.raises(ValidationError, match="offset"):
        _profile_input(effective_from=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        _profile_input(change_reason="  abc  ")
    with pytest.raises(ValidationError):
        _profile_input(change_reason="x" * 301)

    profile_input = _profile_input(change_reason="  valid reason  ")

    assert profile_input.change_reason == "valid reason"


@pytest.mark.parametrize(
    "media_slots",
    [
        [
            {"key": "result", "label": "结算截图", "required": True},
            {"key": "result", "label": "重复槽位", "required": False},
        ],
        [
            {"key": f"slot_{index}", "label": "截图", "required": True}
            for index in range(4)
        ],
        [{"key": "Bad Slot", "label": "截图", "required": True}],
        [{"key": "result", "label": " ", "required": True}],
    ],
)
def test_profile_dto_rejects_invalid_media_slot_contracts(
    media_slots: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        _profile_input(media_slots=media_slots)


def test_version_creation_requires_actor_and_audits_only_safe_identifiers(
    db: Session,
    actor_user_id: UUID,
    profile_project: GameProject,
) -> None:
    service = _service(db)
    unique_change_reason = "preserve this specific analysis decision on the profile"
    with pytest.raises(InvalidAnalysisProfile):
        service.create_version(
            profile_project.id,
            _profile_input(),
            actor_user_id=None,
            request_id="analysis-profile-missing-actor",
        )

    version_two = service.create_version(
        profile_project.id,
        _profile_input(change_reason=unique_change_reason),
        actor_user_id=actor_user_id,
        request_id="analysis-profile-safe-audit",
    )
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_type == "project_analysis_profile",
            AuditEvent.resource_id == version_two.id,
        )
    )

    assert audit is not None
    assert audit.actor_user_id == actor_user_id
    assert version_two.change_reason == unique_change_reason
    assert audit.purpose is None
    assert audit.event_metadata == {
        "project_id": str(profile_project.id),
        "analysis_profile_id": str(version_two.id),
        "profile_version": 2,
        "analysis_mode": "manual_only",
    }
    serialized_audit_fields = json.dumps(
        {"purpose": audit.purpose, "metadata": audit.event_metadata},
        sort_keys=True,
    )
    assert unique_change_reason not in serialized_audit_fields


def test_concurrent_expected_version_creates_one_version_two_and_one_conflict(
    postgres_engine: Engine,
) -> None:
    ids: dict[str, UUID] = {}
    marker = uuid4().hex
    with Session(postgres_engine) as setup, setup.begin():
        person = Person(
            full_name="分析档案并发操作人",
            identity_ciphertext=f"analysis-race-ciphertext-{marker}",
            identity_digest=f"analysis-race-identity-{marker}",
            phone_ciphertext=None,
            phone_digest=None,
            email=None,
            sex="female",
            date_of_birth=date(1988, 1, 2),
        )
        setup.add(person)
        setup.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=f"analysis-race-phone-{marker}",
            email_normalized=f"analysis-race-{marker}@example.test",
            password_hash="analysis-race-test-password-hash",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        setup.add(account)
        add_test_primary_login_identifier(setup, account)
        project = TeachingCatalogService(setup).create_project(
            ProjectCreate(name=f"分析档案并发项目-{marker[:8]}"),
            actor_user_id=account.id,
        )
        ids = {"person": person.id, "account": account.id, "project": project.id}

    first_has_project_lock = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_backend_pid: list[int] = []
    successes: list[UUID] = []
    failures: list[BaseException] = []

    def pause_first_after_project_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            threading.current_thread().name == "analysis-profile-first"
            and "FROM game_projects" in statement
            and "FOR UPDATE" in statement
        ):
            first_has_project_lock.set()
            if not release_first.wait(timeout=10):
                raise TimeoutError("first profile writer was not released")

    def create_profile(request_id: str) -> None:
        try:
            with Session(postgres_engine) as worker:
                if threading.current_thread().name == "analysis-profile-second":
                    backend_pid = worker.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(backend_pid, int)
                    second_backend_pid.append(backend_pid)
                    second_started.set()
                profile = _service(worker).create_version(
                    ids["project"],
                    _profile_input(change_reason=f"concurrent change {request_id}"),
                    actor_user_id=ids["account"],
                    request_id=request_id,
                )
                successes.append(profile.id)
        except BaseException as exc:  # noqa: BLE001 - asserted after both joins
            failures.append(exc)

    first = threading.Thread(
        target=create_profile,
        args=("analysis-profile-race-first",),
        name="analysis-profile-first",
    )
    second = threading.Thread(
        target=create_profile,
        args=("analysis-profile-race-second",),
        name="analysis-profile-second",
    )
    event.listen(postgres_engine, "after_cursor_execute", pause_first_after_project_lock)
    observed_lock_wait = False
    try:
        first.start()
        assert first_has_project_lock.wait(timeout=5)
        second.start()
        assert second_started.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and second.is_alive():
            with postgres_engine.connect() as observer:
                wait_event_type = observer.scalar(
                    text(
                        "SELECT wait_event_type FROM pg_stat_activity "
                        "WHERE pid = :backend_pid"
                    ),
                    {"backend_pid": second_backend_pid[0]},
                )
            if wait_event_type == "Lock":
                observed_lock_wait = True
                break
            time.sleep(0.01)
    finally:
        release_first.set()
        if first.ident is not None:
            first.join(timeout=10)
        if second.ident is not None:
            second.join(timeout=10)
        event.remove(
            postgres_engine,
            "after_cursor_execute",
            pause_first_after_project_lock,
        )

    try:
        assert observed_lock_wait
        assert not first.is_alive()
        assert not second.is_alive()
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], AnalysisProfileVersionConflict)
        with Session(postgres_engine) as verification:
            assert list(
                verification.scalars(
                    select(ProjectAnalysisProfile.version)
                    .where(ProjectAnalysisProfile.project_id == ids["project"])
                    .order_by(ProjectAnalysisProfile.version)
                )
            ) == [1, 2]
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            profile_ids = list(
                cleanup.scalars(
                    select(ProjectAnalysisProfile.id).where(
                        ProjectAnalysisProfile.project_id == ids["project"]
                    )
                )
            )
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            try:
                cleanup.execute(
                    delete(AuditEvent).where(AuditEvent.resource_id.in_(profile_ids))
                )
            finally:
                cleanup.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            cleanup.execute(
                delete(ProjectAnalysisProfile).where(
                    ProjectAnalysisProfile.project_id == ids["project"]
                )
            )
            cleanup.execute(delete(GameProject).where(GameProject.id == ids["project"]))
            cleanup.execute(delete(UserAccount).where(UserAccount.id == ids["account"]))
            cleanup.execute(delete(Person).where(Person.id == ids["person"]))
