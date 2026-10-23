from __future__ import annotations

import threading
import time
import tomllib
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.auth.service import AuthService, InvalidCredentials
from app.core.errors import install_exception_handlers
from app.evaluations.errors import (
    AuxiliaryPhoneConflict,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    GameAccountAlreadyUnbound,
    GameAccountLimitInvalid,
    GameAccountLimitReached,
    GameIdAlreadyBound,
    InvalidGameId,
)
from app.evaluations.game_account_service import GameAccountService, normalize_game_id
from app.evaluations.game_id_digest import student_game_id_digest
from app.evaluations.game_models import StudentAuxiliaryPhone, StudentGameAccount
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.schemas import (
    AuxiliaryPhoneCreate,
    AuxiliaryPhoneDeactivate,
    AuxiliaryPhoneMatch,
    AuxiliaryPhoneReplace,
    GameAccountBind,
    GameAccountLimitUpdate,
    GameAccountUnbind,
    StudentGameAccountItem,
)
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.catalog_service import TeachingCatalogService
from app.teaching.models import Cohort, GameProject, TeachingClass
from app.teaching.schemas import ProjectUpdate
from app.teaching.seed import INITIAL_PROJECTS

NOW = datetime(2026, 8, 20, 8, tzinfo=UTC)
END = NOW + timedelta(days=7)
CIPHER_KEY = bytes(range(32))
LOOKUP_KEY = bytes(reversed(range(32)))


class RecordingCipher(EnvelopeCipher):
    def __init__(self, key: bytes) -> None:
        super().__init__(key)
        self.decrypt_calls = 0
        self.encrypted_payloads: list[str] = []

    def encrypt(self, plaintext: str, *, field_name: str) -> str:
        payload = super().encrypt(plaintext, field_name=field_name)
        self.encrypted_payloads.append(payload)
        return payload

    def decrypt(self, payload: str, *, field_name: str) -> str:
        self.decrypt_calls += 1
        return super().decrypt(payload, field_name=field_name)


@dataclass(frozen=True)
class GameAccountScenario:
    db: Session
    service: GameAccountService
    people: dict[str, Person]
    accounts: dict[str, UserAccount]
    relationship: GuardianRelationship


def test_game_history_models_declare_student_descending_indexes() -> None:
    expected_indexes = {
        StudentGameAccount: "ix_student_game_accounts_student_history",
        StudentAuxiliaryPhone: "ix_student_auxiliary_phones_student_history",
    }

    for model, expected_name in expected_indexes.items():
        assert expected_name in {index.name for index in model.__table__.indexes}


def _person(db: Session, name: str) -> Person:
    marker = uuid4().hex
    person = Person(
        full_name=name,
        identity_ciphertext=f"cipher:{marker}",
        identity_digest=(marker * 2)[:64],
        sex="female",
        date_of_birth=date(2000, 1, 2),
    )
    db.add(person)
    db.flush()
    return person


def _account(db: Session, person: Person, key: str) -> UserAccount:
    marker = uuid4().hex
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=(marker * 2)[:64],
        email_normalized=f"{key}-{marker[:8]}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    return account


def _student(db: Session, name: str, key: str) -> tuple[Person, UserAccount]:
    person = _person(db, name)
    db.add(
        StudentProfile(
            person_id=person.id,
            student_number=f"GAME-{uuid4().hex[:12]}",
            status="active",
        )
    )
    account = _account(db, person, key)
    db.add(UserRole(user_id=account.id, role_id=ROLES["student"].id))
    db.flush()
    return person, account


@pytest.fixture
def game_scenario(db: Session) -> GameAccountScenario:
    admin_person = _person(db, "游戏账号管理员")
    admin = _account(db, admin_person, "game-admin")
    db.add(UserRole(user_id=admin.id, role_id=ROLES["admin"].id))
    student, student_account = _student(db, "游戏账号学员", "game-student")
    other, other_account = _student(db, "其他游戏账号学员", "other-student")
    guardian_person = _person(db, "游戏账号监护人")
    guardian = _account(db, guardian_person, "game-guardian")
    db.add(UserRole(user_id=guardian.id, role_id=ROLES["guardian"].id))
    relationship = GuardianRelationship(
        guardian_person_id=guardian_person.id,
        student_person_id=student.id,
        relationship="mother",
        is_primary=True,
        valid_from=NOW.date(),
    )
    db.add(relationship)
    db.flush()
    return GameAccountScenario(
        db=db,
        service=GameAccountService(
            db,
            cipher=EnvelopeCipher(CIPHER_KEY),
            lookup=LookupDigest(LOOKUP_KEY),
        ),
        people={
            "admin": admin_person,
            "student": student,
            "other": other,
            "guardian": guardian_person,
        },
        accounts={
            "admin": admin,
            "student": student_account,
            "other": other_account,
            "guardian": guardian,
        },
        relationship=relationship,
    )


def _bind(
    scenario: GameAccountScenario,
    *,
    student: str = "student",
    actor: str = "admin",
    project_index: int = 0,
    game_id: str | None = None,
    occurred_at: datetime = NOW,
) -> StudentGameAccount:
    return scenario.service.bind_game_account(
        scenario.people[student].id,
        GameAccountBind(
            project_id=INITIAL_PROJECTS[project_index].id,
            game_id=game_id or f"Player-{uuid4().hex[:8]}",
        ),
        actor_user_id=scenario.accounts[actor].id,
        occurred_at=occurred_at,
        request_id=f"game-bind-{uuid4().hex}",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  PlayerＡ  ", "PlayerA"),
        ("player a", "player a"),
        ("PlayerA", "PlayerA"),
        ("playera", "playera"),
    ],
)
def test_game_id_normalization_uses_nfkc_and_trim_but_preserves_case(
    raw: str,
    expected: str,
) -> None:
    assert normalize_game_id(raw) == expected


@pytest.mark.parametrize("length", [2, 64])
def test_game_account_bind_normalizes_before_length_validation(length: int) -> None:
    expected = "A" * length
    raw = f"{' ' * 100}{expected}{' ' * 100}"

    item = GameAccountBind(project_id=uuid4(), game_id=raw)

    assert item.game_id == expected


@pytest.mark.parametrize("raw", ["🎮", "A" * 65, "Player\x00Identifier"])
def test_game_account_bind_rejects_normalized_length_and_control_boundaries(
    raw: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        GameAccountBind(project_id=uuid4(), game_id=raw)

    assert raw not in str(error.value)
    assert raw not in repr(error.value)


@pytest.mark.parametrize(
    ("input_type", "extra"),
    [
        (GameAccountUnbind, {}),
        (GameAccountLimitUpdate, {"max_active_accounts": 4}),
        (AuxiliaryPhoneReplace, {"phone": "13800138000"}),
        (AuxiliaryPhoneDeactivate, {}),
    ],
)
def test_reason_inputs_trim_before_the_200_character_limit(
    input_type,
    extra: dict[str, object],
) -> None:
    expected = "改" * 200
    raw = f"{' ' * 100}{expected}{' ' * 100}"

    item = input_type(reason=raw, **extra)

    assert item.reason == expected


def test_sensitive_input_validation_and_repr_never_echo_raw_values() -> None:
    raw_game_id = "SecretPlayerIdentifier"
    with pytest.raises(ValidationError) as game_error:
        GameAccountBind(game_id=raw_game_id)  # type: ignore[call-arg]
    assert raw_game_id not in str(game_error.value)
    assert raw_game_id not in repr(game_error.value)
    game_input = GameAccountBind(project_id=uuid4(), game_id=raw_game_id)
    assert raw_game_id not in repr(game_input)

    raw_phone = "13800138000"
    with pytest.raises(ValidationError) as phone_error:
        AuxiliaryPhoneReplace(phone=raw_phone)  # type: ignore[call-arg]
    assert raw_phone not in str(phone_error.value)
    assert raw_phone not in repr(phone_error.value)
    phone_input = AuxiliaryPhoneCreate(phone=raw_phone)
    assert raw_phone not in repr(phone_input)


def test_global_validation_response_never_echoes_raw_game_id() -> None:
    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/game-account-bind")
    def bind_game_account(input: GameAccountBind) -> dict[str, bool]:
        return {"accepted": bool(input.game_id)}

    raw_game_id = "PrivateIdentifier" * 5
    response = TestClient(app).post(
        "/game-account-bind",
        json={"project_id": str(uuid4()), "game_id": raw_game_id},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert raw_game_id not in response.text


def test_game_account_response_repr_hides_game_id_but_serializes_it() -> None:
    game_id = "SecretPlayerIdentifier"
    item = StudentGameAccountItem(
        id=uuid4(),
        project_id=uuid4(),
        project_name="测试项目",
        game_id=game_id,
        status="active",
        valid_from=NOW,
        unbound_at=None,
        unbind_reason=None,
    )

    assert game_id not in repr(item)
    assert item.model_dump()["game_id"] == game_id


def test_api_declares_pydantic_floor_for_exclude_if_serialization_contract() -> None:
    api_pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    project = tomllib.loads(api_pyproject.read_text(encoding="utf-8"))["project"]

    assert "pydantic>=2.12,<3" in project["dependencies"]


def test_application_engine_hides_sensitive_parameters_in_non_integrity_errors() -> None:
    from app.core.database import engine

    sensitive_values = {
        "identifier_ciphertext": "private-ciphertext-payload",
        "identifier_digest": "private-deterministic-digest",
    }
    error = OperationalError(
        "INSERT INTO student_game_accounts (identifier_ciphertext, identifier_digest) "
        "VALUES (:identifier_ciphertext, :identifier_digest)",
        sensitive_values,
        Exception("connection lost"),
        hide_parameters=engine.hide_parameters,
    )

    formatted = str(error)
    assert engine.hide_parameters is True
    assert all(value not in formatted for value in sensitive_values.values())


def test_default_limit_is_three_across_all_projects(game_scenario: GameAccountScenario) -> None:
    profile = game_scenario.db.get(StudentProfile, game_scenario.people["student"].id)
    assert profile is not None and profile.game_account_limit == 3
    for project_index in range(3):
        _bind(game_scenario, project_index=project_index)
    with pytest.raises(GameAccountLimitReached) as error:
        _bind(game_scenario, project_index=3)
    assert error.value.code == "game_account_limit_reached"


def test_bind_rejects_inactive_project_without_creating_history(
    game_scenario: GameAccountScenario,
) -> None:
    project = game_scenario.db.get(GameProject, INITIAL_PROJECTS[0].id)
    assert project is not None
    project.is_active = False
    game_scenario.db.flush()

    with pytest.raises(EvaluationResourceNotFound):
        _bind(game_scenario, game_id="InactiveProjectPlayer")

    assert game_scenario.db.scalar(select(func.count(StudentGameAccount.id))) == 0
    assert game_scenario.db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "student_game_account.bind"
        )
    ) == 0


def test_admin_limit_change_requires_valid_value_and_audits_only_safe_values(
    game_scenario: GameAccountScenario,
) -> None:
    service = game_scenario.service
    student_id = game_scenario.people["student"].id
    service.update_game_account_limit(
        student_id,
        GameAccountLimitUpdate(max_active_accounts=4, reason="批准增加一个账号"),
        actor_user_id=game_scenario.accounts["admin"].id,
        request_id="game-limit-four",
    )
    for project_index in range(4):
        _bind(game_scenario, project_index=project_index)
    with pytest.raises(GameAccountLimitInvalid):
        service.update_game_account_limit(
            student_id,
            GameAccountLimitUpdate(max_active_accounts=3, reason="错误降低额度"),
            actor_user_id=game_scenario.accounts["admin"].id,
            request_id="game-limit-invalid",
        )
    audit = game_scenario.db.scalar(
        select(AuditEvent).where(AuditEvent.request_id == "game-limit-four")
    )
    assert audit is not None
    assert audit.actor_user_id == game_scenario.accounts["admin"].id
    assert audit.purpose is None
    assert audit.event_metadata == {
        "old_limit": 3,
        "new_limit": 4,
    }


def test_limit_change_rejects_missing_or_blank_reason_and_non_admin_actor(
    game_scenario: GameAccountScenario,
) -> None:
    with pytest.raises(ValidationError):
        GameAccountLimitUpdate(max_active_accounts=4, reason="   ")
    with pytest.raises(ValidationError):
        GameAccountLimitUpdate(max_active_accounts=2, reason="额度过低")
    assert GameAccountLimitUpdate(max_active_accounts=4, reason="改").reason == "改"
    with pytest.raises(EvaluationOperationDenied):
        game_scenario.service.update_game_account_limit(
            game_scenario.people["student"].id,
            GameAccountLimitUpdate(max_active_accounts=4, reason="学员不能自行提高额度"),
            actor_user_id=game_scenario.accounts["student"].id,
            occurred_at=NOW,
            request_id="game-limit-self-denied",
        )


def test_same_normalized_id_is_unique_per_project_but_case_remains_significant(
    game_scenario: GameAccountScenario,
) -> None:
    first = _bind(game_scenario, game_id="  PlayerＡ  ")
    assert first.identifier_digest == student_game_id_digest(
        LookupDigest(LOOKUP_KEY),
        "PlayerA",
    )
    with pytest.raises(GameIdAlreadyBound) as error:
        _bind(game_scenario, student="other", game_id="PlayerA")
    assert error.value.code == "game_id_already_bound"
    assert "PlayerA" not in str(error.value)
    cross_project = _bind(
        game_scenario,
        student="other",
        project_index=1,
        game_id="PlayerA",
    )
    assert cross_project.identifier_digest == first.identifier_digest
    assert cross_project.identifier_ciphertext != first.identifier_ciphertext
    _bind(game_scenario, student="other", project_index=2, game_id="playera")


def test_game_id_digest_is_domain_separated_from_same_auxiliary_phone(
    game_scenario: GameAccountScenario,
) -> None:
    shared_value = "13800138000"
    first = _bind(game_scenario, game_id=shared_value)
    with pytest.raises(GameIdAlreadyBound):
        _bind(game_scenario, student="other", game_id=shared_value)
    cross_project = _bind(
        game_scenario,
        student="other",
        project_index=1,
        game_id=shared_value,
    )
    phone = game_scenario.service.add_auxiliary_phone(
        game_scenario.people["student"].id,
        AuxiliaryPhoneCreate(phone=shared_value),
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW,
        request_id="game-phone-domain-separation",
    )

    assert cross_project.identifier_digest == first.identifier_digest
    assert phone.phone_digest != first.identifier_digest


def test_audit_errors_and_logs_never_contain_raw_game_ids_or_auxiliary_phones(
    game_scenario: GameAccountScenario,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_game_id = "PrivateGameIdentifier"
    raw_phone = "13500135000"
    _bind(game_scenario, game_id=raw_game_id)
    with pytest.raises(GameIdAlreadyBound) as game_error:
        _bind(game_scenario, student="other", game_id=raw_game_id)
    assert raw_game_id not in str(game_error.value)
    invalid_game_id = "X" * 65
    with pytest.raises(InvalidGameId) as invalid_error:
        normalize_game_id(invalid_game_id)
    assert invalid_game_id not in str(invalid_error.value)

    phone = game_scenario.service.add_auxiliary_phone(
        game_scenario.people["student"].id,
        AuxiliaryPhoneCreate(phone=raw_phone),
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW,
        request_id="aux-phone-private-audit",
    )
    with pytest.raises(AuxiliaryPhoneConflict) as phone_error:
        game_scenario.service.add_auxiliary_phone(
            game_scenario.people["student"].id,
            AuxiliaryPhoneCreate(phone=raw_phone),
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="aux-phone-private-conflict",
        )
    assert raw_phone not in str(phone_error.value)
    audits = list(
        game_scenario.db.scalars(
            select(AuditEvent).where(
                AuditEvent.actor_user_id == game_scenario.accounts["admin"].id
            )
        )
    )
    audit_text = repr(
        [
            (audit.action, audit.purpose, audit.request_id, audit.event_metadata)
            for audit in audits
        ]
    )
    assert phone.id
    for raw in (raw_game_id, invalid_game_id, raw_phone):
        assert raw not in audit_text
        assert raw not in caplog.text


def test_integrity_translation_suppresses_sensitive_sqlalchemy_exception_chains(
    game_scenario: GameAccountScenario,
) -> None:
    cipher = RecordingCipher(CIPHER_KEY)
    lookup = LookupDigest(LOOKUP_KEY)
    service = GameAccountService(game_scenario.db, cipher=cipher, lookup=lookup)
    admin_id = game_scenario.accounts["admin"].id
    student_id = game_scenario.people["student"].id

    game_id = "TracebackPrivateGameId"
    service.bind_game_account(
        student_id,
        GameAccountBind(project_id=INITIAL_PROJECTS[0].id, game_id=game_id),
        actor_user_id=admin_id,
        occurred_at=NOW,
        request_id="traceback-game-first",
    )
    with pytest.raises(GameIdAlreadyBound) as game_error:
        service.bind_game_account(
            game_scenario.people["other"].id,
            GameAccountBind(project_id=INITIAL_PROJECTS[0].id, game_id=game_id),
            actor_user_id=admin_id,
            occurred_at=NOW,
            request_id="traceback-game-conflict",
        )
    game_sensitive = (
        cipher.encrypted_payloads[-1],
        student_game_id_digest(lookup, game_id),
    )

    phone = "13500135000"
    service.add_auxiliary_phone(
        student_id,
        AuxiliaryPhoneCreate(phone=phone),
        actor_user_id=admin_id,
        occurred_at=NOW,
        request_id="traceback-phone-first",
    )
    with pytest.raises(AuxiliaryPhoneConflict) as phone_error:
        service.add_auxiliary_phone(
            student_id,
            AuxiliaryPhoneCreate(phone=phone),
            actor_user_id=admin_id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="traceback-phone-conflict",
        )
    phone_sensitive = (cipher.encrypted_payloads[-1], lookup.digest(phone))

    source_phone = service.add_auxiliary_phone(
        student_id,
        AuxiliaryPhoneCreate(phone="13600136000"),
        actor_user_id=admin_id,
        occurred_at=NOW,
        request_id="traceback-phone-replace-source",
    )
    with pytest.raises(AuxiliaryPhoneConflict) as replace_error:
        service.replace_auxiliary_phone(
            student_id,
            source_phone.id,
            AuxiliaryPhoneReplace(phone=phone, reason="触发替换唯一约束"),
            actor_user_id=admin_id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="traceback-phone-replace-conflict",
        )
    replace_sensitive = (cipher.encrypted_payloads[-1], lookup.digest(phone))

    for error, sensitive_values in (
        (game_error, game_sensitive),
        (phone_error, phone_sensitive),
        (replace_error, replace_sensitive),
    ):
        formatted = "".join(
            traceback.format_exception(error.type, error.value, error.tb)
        )
        for sensitive_value in sensitive_values:
            assert sensitive_value not in formatted
        assert error.value.__cause__ is None


def test_unbind_keeps_history_and_allows_new_owner_only_at_half_open_boundary(
    game_scenario: GameAccountScenario,
) -> None:
    account = _bind(game_scenario, game_id="HistoricPlayer")
    immutable_before = (
        account.student_person_id,
        account.project_id,
        account.identifier_ciphertext,
        account.identifier_digest,
        account.valid_from,
    )
    unbound_at = NOW + timedelta(hours=1)
    game_scenario.service.unbind_game_account(
        game_scenario.people["student"].id,
        account.id,
        GameAccountUnbind(reason="学员自行解除绑定"),
        actor_user_id=game_scenario.accounts["student"].id,
        occurred_at=unbound_at,
        request_id="game-unbind-history",
    )
    game_scenario.db.expire_all()
    stored = game_scenario.db.get(StudentGameAccount, account.id)
    assert stored is not None
    assert (stored.student_person_id, stored.unbound_at, stored.unbind_reason) == (
        game_scenario.people["student"].id,
        unbound_at,
        "学员自行解除绑定",
    )
    assert (
        stored.student_person_id,
        stored.project_id,
        stored.identifier_ciphertext,
        stored.identifier_digest,
        stored.valid_from,
    ) == immutable_before
    with pytest.raises(GameIdAlreadyBound):
        _bind(
            game_scenario,
            student="other",
            game_id="HistoricPlayer",
            occurred_at=unbound_at - timedelta(microseconds=1),
        )
    rebound = _bind(
        game_scenario,
        student="other",
        game_id="HistoricPlayer",
        occurred_at=unbound_at,
    )
    assert rebound.student_person_id == game_scenario.people["other"].id


def test_unbind_rejects_soft_deleted_student_without_changing_history(
    game_scenario: GameAccountScenario,
) -> None:
    account = _bind(game_scenario, game_id="DeletedStudentHistory")
    game_scenario.people["student"].deleted_at = NOW + timedelta(hours=1)
    game_scenario.db.flush()

    with pytest.raises(EvaluationResourceNotFound):
        game_scenario.service.unbind_game_account(
            game_scenario.people["student"].id,
            account.id,
            GameAccountUnbind(reason="软删除学员不得改写账号历史"),
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=2),
            request_id="deleted-student-unbind-denied",
        )

    game_scenario.db.expire_all()
    stored = game_scenario.db.get(StudentGameAccount, account.id)
    assert stored is not None
    assert (stored.unbound_at, stored.unbound_by_user_id, stored.unbind_reason) == (
        None,
        None,
        None,
    )
    assert game_scenario.db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.request_id == "deleted-student-unbind-denied"
        )
    ) == 0


def test_unbind_never_moves_preexisting_media_to_the_new_owner(
    game_scenario: GameAccountScenario,
) -> None:
    account = _bind(game_scenario, game_id="MediaOwner")
    admin_id = game_scenario.accounts["admin"].id
    cohort = Cohort(
        name=f"媒体归属营期-{uuid4().hex[:8]}",
        starts_at=NOW,
        ends_at=END,
        default_check_in_at=NOW,
        default_check_out_at=END,
        status="active",
        created_by_user_id=admin_id,
    )
    game_scenario.db.add(cohort)
    game_scenario.db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=INITIAL_PROJECTS[0].id,
        name=f"媒体归属班-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        created_by_user_id=admin_id,
    )
    admission_case = AdmissionCase(
        student_person_id=game_scenario.people["student"].id,
        status="ready",
        created_by_user_id=admin_id,
    )
    game_scenario.db.add_all([teaching_class, admission_case])
    game_scenario.db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=game_scenario.people["student"].id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=0,
        status="active",
        confirmed_at=NOW,
        created_by_user_id=admin_id,
    )
    blob = EvaluationMediaBlob(
        storage_key=f"objects/aa/bb/{uuid4()}.png",
        preview_storage_key=f"previews/aa/bb/{uuid4()}.webp",
        sha256=uuid4().hex + uuid4().hex,
        mime_type="image/png",
        canonical_extension="png",
        size_bytes=100,
        width_pixels=10,
        height_pixels=10,
        storage_status="available",
        validation_status="validated",
        retention_status="retained",
    )
    game_scenario.db.add_all([enrollment, blob])
    game_scenario.db.flush()
    media = EvaluationMediaAsset(
        blob_id=blob.id,
        uploaded_by_user_id=admin_id,
        project_id=INITIAL_PROJECTS[0].id,
        student_person_id=game_scenario.people["student"].id,
        enrollment_id=enrollment.id,
        class_id=teaching_class.id,
        matched_at=NOW,
        matched_by_user_id=admin_id,
        match_reason="游戏账号命中后确认",
    )
    game_scenario.db.add(media)
    game_scenario.db.flush()

    boundary = NOW + timedelta(hours=1)
    game_scenario.service.unbind_game_account(
        game_scenario.people["student"].id,
        account.id,
        GameAccountUnbind(reason="更换游戏账号归属"),
        actor_user_id=game_scenario.accounts["student"].id,
        occurred_at=boundary,
        request_id="game-unbind-media",
    )
    _bind(
        game_scenario,
        student="other",
        game_id="MediaOwner",
        occurred_at=boundary,
    )
    assert game_scenario.db.scalars(
        select(EvaluationMediaAsset).where(
            EvaluationMediaAsset.student_person_id == game_scenario.people["student"].id
        )
    ).all() == [media]
    assert game_scenario.db.scalars(
        select(EvaluationMediaAsset).where(
            EvaluationMediaAsset.student_person_id == game_scenario.people["other"].id
        )
    ).all() == []


def _family_override(
    scenario: GameAccountScenario,
    *,
    valid_until: datetime,
) -> UserPermissionOverride:
    permission = scenario.db.scalar(
        select(Permission).where(Permission.key == "student_game_accounts.manage")
    )
    assert permission is not None
    override = UserPermissionOverride(
        user_id=scenario.accounts["guardian"].id,
        permission_id=permission.id,
        effect="allow",
        scope_kind="family",
        valid_from=NOW,
        valid_until=valid_until,
        created_by_user_id=scenario.accounts["admin"].id,
        grant_reason="临时允许监护人管理游戏账号",
    )
    scenario.db.add(override)
    scenario.db.flush()
    return override


def _person_deny(
    scenario: GameAccountScenario,
    *,
    student: str,
) -> UserPermissionOverride:
    permission = scenario.db.scalar(
        select(Permission).where(Permission.key == "student_game_accounts.manage")
    )
    assert permission is not None
    override = UserPermissionOverride(
        user_id=scenario.accounts["admin"].id,
        permission_id=permission.id,
        effect="deny",
        scope_kind="person",
        scope_id=scenario.people[student].id,
        valid_from=NOW,
        created_by_user_id=scenario.accounts["admin"].id,
        grant_reason="显式收回指定学员的游戏账号管理权限",
    )
    scenario.db.add(override)
    scenario.db.flush()
    return override


def _project_override(
    scenario: GameAccountScenario,
    *,
    account: str,
    effect: str,
    project_index: int,
) -> UserPermissionOverride:
    permission = scenario.db.scalar(
        select(Permission).where(Permission.key == "student_game_accounts.manage")
    )
    assert permission is not None
    override = UserPermissionOverride(
        user_id=scenario.accounts[account].id,
        permission_id=permission.id,
        effect=effect,
        scope_kind="project",
        scope_id=INITIAL_PROJECTS[project_index].id,
        valid_from=NOW,
        valid_until=END,
        created_by_user_id=scenario.accounts["admin"].id,
        grant_reason="按项目控制游戏账号管理权限",
    )
    scenario.db.add(override)
    scenario.db.flush()
    return override


def test_project_allow_authorizes_only_matching_bind_list_and_unbind(
    game_scenario: GameAccountScenario,
) -> None:
    _project_override(
        game_scenario,
        account="guardian",
        effect="allow",
        project_index=0,
    )

    empty_items = game_scenario.service.list_game_accounts(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["guardian"].id,
        occurred_at=NOW,
        include_unbound=True,
    )

    assert empty_items == []

    account = _bind(
        game_scenario,
        actor="guardian",
        project_index=0,
        game_id="ProjectGranted",
    )
    with pytest.raises(EvaluationOperationDenied):
        _bind(
            game_scenario,
            actor="guardian",
            project_index=1,
            game_id="OtherProjectDenied",
        )

    items = game_scenario.service.list_game_accounts(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["guardian"].id,
        occurred_at=NOW,
        include_unbound=True,
    )
    assert [(item.project_id, item.game_id) for item in items] == [
        (INITIAL_PROJECTS[0].id, "ProjectGranted")
    ]

    game_scenario.service.unbind_game_account(
        game_scenario.people["student"].id,
        account.id,
        GameAccountUnbind(reason="项目授权范围内解除绑定"),
        actor_user_id=game_scenario.accounts["guardian"].id,
        occurred_at=NOW + timedelta(hours=1),
        request_id="project-granted-unbind",
    )


def test_project_allow_cannot_cross_the_current_family_target_boundary(
    game_scenario: GameAccountScenario,
) -> None:
    _project_override(
        game_scenario,
        account="guardian",
        effect="allow",
        project_index=0,
    )
    existing = _bind(
        game_scenario,
        student="other",
        project_index=0,
        game_id="UnrelatedProjectHistory",
    )

    with pytest.raises(EvaluationOperationDenied):
        game_scenario.service.list_game_accounts(
            game_scenario.people["other"].id,
            actor_user_id=game_scenario.accounts["guardian"].id,
            occurred_at=NOW,
            include_unbound=True,
        )
    with pytest.raises(EvaluationOperationDenied):
        _bind(
            game_scenario,
            student="other",
            actor="guardian",
            project_index=0,
            game_id="UnrelatedProjectBind",
        )
    with pytest.raises(EvaluationOperationDenied):
        game_scenario.service.unbind_game_account(
            game_scenario.people["other"].id,
            existing.id,
            GameAccountUnbind(reason="不得跨学员解绑"),
            actor_user_id=game_scenario.accounts["guardian"].id,
            occurred_at=NOW + timedelta(seconds=1),
            request_id="project-cross-target-unbind",
        )

    game_scenario.db.refresh(existing)
    assert existing.unbound_at is None
    assert (
        game_scenario.db.scalar(
            select(func.count(StudentGameAccount.id)).where(
                StudentGameAccount.student_person_id
                == game_scenario.people["other"].id
            )
        )
        == 1
    )


def test_project_allow_returns_empty_when_only_unauthorized_history_exists(
    game_scenario: GameAccountScenario,
) -> None:
    _project_override(
        game_scenario,
        account="guardian",
        effect="allow",
        project_index=0,
    )
    _bind(
        game_scenario,
        project_index=1,
        game_id="OtherProjectHistory",
    )

    items = game_scenario.service.list_game_accounts(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["guardian"].id,
        occurred_at=NOW,
        include_unbound=True,
    )

    assert items == []


def test_project_deny_filters_admin_list_before_decryption_and_blocks_mutation(
    game_scenario: GameAccountScenario,
) -> None:
    denied = _bind(game_scenario, project_index=0, game_id="DeniedProjectSecret")
    _bind(game_scenario, project_index=1, game_id="AllowedProjectVisible")
    _project_override(
        game_scenario,
        account="admin",
        effect="deny",
        project_index=0,
    )
    recording_cipher = RecordingCipher(CIPHER_KEY)
    service = GameAccountService(
        game_scenario.db,
        cipher=recording_cipher,
        lookup=LookupDigest(LOOKUP_KEY),
    )

    items = service.list_game_accounts(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        include_unbound=True,
    )
    assert [(item.project_id, item.game_id) for item in items] == [
        (INITIAL_PROJECTS[1].id, "AllowedProjectVisible")
    ]
    assert recording_cipher.decrypt_calls == 1

    with pytest.raises(EvaluationOperationDenied):
        service.bind_game_account(
            game_scenario.people["student"].id,
            GameAccountBind(
                project_id=INITIAL_PROJECTS[0].id,
                game_id="DeniedProjectMutation",
            ),
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="project-denied-bind",
        )
    with pytest.raises(EvaluationOperationDenied):
        service.unbind_game_account(
            game_scenario.people["student"].id,
            denied.id,
            GameAccountUnbind(reason="项目拒绝必须覆盖管理员全局角色"),
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="project-denied-unbind",
        )


def test_project_authorization_filters_history_before_pagination(
    game_scenario: GameAccountScenario,
) -> None:
    allowed = _bind(
        game_scenario,
        project_index=1,
        game_id="OlderAllowedHistory",
        occurred_at=NOW,
    )
    _bind(
        game_scenario,
        project_index=0,
        game_id="NewerDeniedHistory",
        occurred_at=NOW + timedelta(minutes=1),
    )
    _project_override(
        game_scenario,
        account="admin",
        effect="deny",
        project_index=0,
    )

    items = game_scenario.service.list_game_accounts(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        include_unbound=True,
        page=1,
        page_size=1,
    )

    assert [item.id for item in items] == [allowed.id]


def test_project_deny_overrides_student_self_unbind(
    game_scenario: GameAccountScenario,
) -> None:
    account = _bind(game_scenario, project_index=0, game_id="SelfProjectDenied")
    _project_override(
        game_scenario,
        account="student",
        effect="deny",
        project_index=0,
    )

    with pytest.raises(EvaluationOperationDenied):
        game_scenario.service.unbind_game_account(
            game_scenario.people["student"].id,
            account.id,
            GameAccountUnbind(reason="项目拒绝必须覆盖学员自身权限"),
            actor_user_id=game_scenario.accounts["student"].id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="self-project-denied-unbind",
        )


def test_self_succeeds_default_guardian_fails_and_finite_family_allow_expires(
    game_scenario: GameAccountScenario,
) -> None:
    _bind(game_scenario, actor="student", game_id="SelfManaged")
    with pytest.raises(EvaluationOperationDenied):
        game_scenario.service.list_game_accounts(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["guardian"].id,
            occurred_at=NOW,
            include_unbound=True,
        )
    _family_override(game_scenario, valid_until=END)
    items = game_scenario.service.list_game_accounts(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["guardian"].id,
        occurred_at=END - timedelta(microseconds=1),
        include_unbound=True,
    )
    assert [item.game_id for item in items] == ["SelfManaged"]
    with pytest.raises(EvaluationOperationDenied):
        game_scenario.service.list_game_accounts(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["guardian"].id,
            occurred_at=END,
            include_unbound=True,
        )


def test_targeted_administrator_operations_honor_person_scoped_deny(
    game_scenario: GameAccountScenario,
) -> None:
    service = game_scenario.service
    student_id = game_scenario.people["student"].id
    admin_id = game_scenario.accounts["admin"].id
    phone = service.add_auxiliary_phone(
        student_id,
        AuxiliaryPhoneCreate(phone="13800138000"),
        actor_user_id=admin_id,
        occurred_at=NOW,
        request_id="aux-phone-before-person-deny",
    )
    _person_deny(game_scenario, student="student")

    denied_calls = [
        lambda: service.update_game_account_limit(
            student_id,
            GameAccountLimitUpdate(max_active_accounts=4, reason="不应越过对象拒绝"),
            actor_user_id=admin_id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="limit-person-denied",
        ),
        lambda: service.add_auxiliary_phone(
            student_id,
            AuxiliaryPhoneCreate(phone="13900139000"),
            actor_user_id=admin_id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="aux-create-person-denied",
        ),
        lambda: service.list_auxiliary_phones(
            student_id,
            actor_user_id=admin_id,
            occurred_at=NOW + timedelta(hours=1),
            include_inactive=True,
        ),
        lambda: service.replace_auxiliary_phone(
            student_id,
            phone.id,
            AuxiliaryPhoneReplace(phone="13900139000", reason="不应越过对象拒绝"),
            actor_user_id=admin_id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="aux-replace-person-denied",
        ),
        lambda: service.deactivate_auxiliary_phone(
            student_id,
            phone.id,
            AuxiliaryPhoneDeactivate(reason="不应越过对象拒绝"),
            actor_user_id=admin_id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="aux-deactivate-person-denied",
        ),
    ]

    for denied_call in denied_calls:
        with pytest.raises(EvaluationOperationDenied):
            denied_call()

    game_scenario.db.expire_all()
    profile = game_scenario.db.get(StudentProfile, student_id)
    stored_phone = game_scenario.db.get(StudentAuxiliaryPhone, phone.id)
    assert profile is not None and profile.game_account_limit == 3
    assert stored_phone is not None and stored_phone.disabled_at is None
    assert game_scenario.db.scalar(
        select(func.count(StudentAuxiliaryPhone.id)).where(
            StudentAuxiliaryPhone.student_person_id == student_id
        )
    ) == 1


def test_family_allow_does_not_grant_administrator_only_phone_management(
    game_scenario: GameAccountScenario,
) -> None:
    _family_override(game_scenario, valid_until=END)

    with pytest.raises(EvaluationOperationDenied):
        game_scenario.service.add_auxiliary_phone(
            game_scenario.people["student"].id,
            AuxiliaryPhoneCreate(phone="13800138000"),
            actor_user_id=game_scenario.accounts["guardian"].id,
            occurred_at=NOW + timedelta(hours=1),
            request_id="guardian-family-not-phone-admin",
        )


def test_unauthorized_game_account_read_is_rejected_before_decryption(
    game_scenario: GameAccountScenario,
) -> None:
    _bind(game_scenario, game_id="NeverDecryptForDeniedActor")
    recording_cipher = RecordingCipher(CIPHER_KEY)
    service = GameAccountService(
        game_scenario.db,
        cipher=recording_cipher,
        lookup=LookupDigest(LOOKUP_KEY),
    )
    with pytest.raises(EvaluationOperationDenied):
        service.list_game_accounts(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["guardian"].id,
            occurred_at=NOW,
            include_unbound=True,
        )
    assert recording_cipher.decrypt_calls == 0


def test_game_account_history_is_bounded_and_paged(
    postgres_engine: Engine,
    game_scenario: GameAccountScenario,
) -> None:
    accounts = [
        _bind(
            game_scenario,
            project_index=index,
            game_id=f"PagedPlayer{index}",
            occurred_at=NOW + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    recording_cipher = RecordingCipher(CIPHER_KEY)
    service = GameAccountService(
        game_scenario.db,
        cipher=recording_cipher,
        lookup=LookupDigest(LOOKUP_KEY),
    )
    statements: list[str] = []
    discarded_count_statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if "from student_game_accounts join game_projects" in normalized:
            statements.append(normalized)
        if "select count(student_game_accounts.id)" in normalized:
            discarded_count_statements.append(normalized)

    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        first_page = service.list_game_accounts(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=1),
            include_unbound=True,
            page=1,
            page_size=2,
        )
        second_page = service.list_game_accounts(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=1),
            include_unbound=True,
            page=2,
            page_size=2,
        )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)

    assert [item.id for item in first_page] == [accounts[2].id, accounts[1].id]
    assert [item.id for item in second_page] == [accounts[0].id]
    assert recording_cipher.decrypt_calls == 3
    assert len(statements) == 2
    assert all(" limit " in statement for statement in statements)
    assert discarded_count_statements == []


@pytest.mark.parametrize(
    ("page", "page_size"),
    [(0, 20), (1, 0), (1, 101)],
)
def test_game_account_history_pagination_rejects_unbounded_values(
    game_scenario: GameAccountScenario,
    page: int,
    page_size: int,
) -> None:
    with pytest.raises(ValueError):
        game_scenario.service.list_game_accounts(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW,
            include_unbound=True,
            page=page,
            page_size=page_size,
        )


def test_game_account_history_authorizes_each_page_project_once(
    game_scenario: GameAccountScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(3):
        account = _bind(
            game_scenario,
            game_id=f"RepeatedProjectHistory{index}",
            occurred_at=NOW + timedelta(minutes=index * 2),
        )
        if index < 2:
            game_scenario.service.unbind_game_account(
                game_scenario.people["student"].id,
                account.id,
                GameAccountUnbind(reason="生成同项目历史分页数据"),
                actor_user_id=game_scenario.accounts["admin"].id,
                occurred_at=NOW + timedelta(minutes=index * 2 + 1),
                request_id=f"repeated-project-unbind-{index}",
            )

    authorized_batches: list[list[UUID | None]] = []
    original_filter = game_scenario.service.access.filter_resources

    def record_filter_resources(**kwargs):
        resources = list(kwargs["resources"])
        authorized_batches.append([resource.project_id for resource in resources])
        kwargs["resources"] = resources
        return original_filter(**kwargs)

    monkeypatch.setattr(
        game_scenario.service.access,
        "filter_resources",
        record_filter_resources,
    )

    items = game_scenario.service.list_game_accounts(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        include_unbound=True,
        page=1,
        page_size=3,
    )

    assert len(items) == 3
    assert authorized_batches == [[INITIAL_PROJECTS[0].id]]


def test_relationship_end_revokes_family_access_on_the_next_request(
    game_scenario: GameAccountScenario,
) -> None:
    _bind(game_scenario, game_id="FamilyVisible")
    _family_override(game_scenario, valid_until=END)
    ended_at = NOW + timedelta(hours=2)
    game_scenario.relationship.ended_at = ended_at
    game_scenario.relationship.ended_by_user_id = game_scenario.accounts["admin"].id
    game_scenario.relationship.end_reason = "监护关系解除"
    game_scenario.db.flush()
    assert game_scenario.service.list_game_accounts(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["guardian"].id,
        occurred_at=ended_at - timedelta(microseconds=1),
        include_unbound=True,
    )
    with pytest.raises(EvaluationOperationDenied):
        game_scenario.service.list_game_accounts(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["guardian"].id,
            occurred_at=ended_at,
            include_unbound=True,
        )


@contextmanager
def _committed_limit_setup(postgres_engine: Engine) -> Iterator[dict[str, UUID]]:
    with Session(postgres_engine) as setup, setup.begin():
        admin_person = _person(setup, "并发游戏账号管理员")
        admin = _account(setup, admin_person, "concurrent-game-admin")
        setup.add(UserRole(user_id=admin.id, role_id=ROLES["admin"].id))
        student, student_account = _student(
            setup,
            "并发游戏账号学员",
            "concurrent-game-student",
        )
        ids = {
            "admin_person_id": admin_person.id,
            "admin_id": admin.id,
            "student_id": student.id,
            "student_account_id": student_account.id,
        }
    try:
        with Session(postgres_engine) as seed:
            service = GameAccountService(
                seed,
                cipher=EnvelopeCipher(CIPHER_KEY),
                lookup=LookupDigest(LOOKUP_KEY),
            )
            for project_index in range(2):
                service.bind_game_account(
                    ids["student_id"],
                    GameAccountBind(
                        project_id=INITIAL_PROJECTS[project_index].id,
                        game_id=f"SeedPlayer{project_index}",
                    ),
                    actor_user_id=ids["admin_id"],
                    occurred_at=NOW,
                    request_id=f"concurrent-game-seed-{project_index}",
                )
        yield ids
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            try:
                cleanup.execute(
                    delete(AuditEvent).where(AuditEvent.actor_user_id == ids["admin_id"])
                )
            finally:
                cleanup.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            cleanup.execute(
                delete(StudentGameAccount).where(
                    StudentGameAccount.student_person_id == ids["student_id"]
                )
            )
            cleanup.execute(
                delete(UserRole).where(
                    UserRole.user_id.in_([ids["admin_id"], ids["student_account_id"]])
                )
            )
            cleanup.execute(
                delete(UserAccount).where(
                    UserAccount.id.in_([ids["admin_id"], ids["student_account_id"]])
                )
            )
            cleanup.execute(
                delete(StudentProfile).where(StudentProfile.person_id == ids["student_id"])
            )
            cleanup.execute(
                delete(Person).where(
                    Person.id.in_([ids["admin_person_id"], ids["student_id"]])
                )
            )


def _wait_for_database_lock(
    postgres_engine: Engine,
    *,
    backend_pid: int,
    worker: threading.Thread,
) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and worker.is_alive():
        with postgres_engine.connect() as observer:
            wait_event_type = observer.scalar(
                text(
                    "SELECT wait_event_type FROM pg_stat_activity "
                    "WHERE pid = :backend_pid"
                ),
                {"backend_pid": backend_pid},
            )
        if wait_event_type == "Lock":
            return True
        time.sleep(0.01)
    return False


def test_soft_delete_serializes_before_concurrent_unbind(
    postgres_engine: Engine,
) -> None:
    with _committed_limit_setup(postgres_engine) as ids:
        with Session(postgres_engine) as lookup_session:
            account_id = lookup_session.scalar(
                select(StudentGameAccount.id).where(
                    StudentGameAccount.student_person_id == ids["student_id"],
                    StudentGameAccount.project_id == INITIAL_PROJECTS[0].id,
                )
            )
            assert account_id is not None

        deletion_holds_lock = threading.Event()
        release_deletion = threading.Event()
        operation_started = threading.Event()
        backend_pids: list[int] = []
        outcomes: list[str] = []
        thread_errors: list[str] = []
        request_id = "concurrent-soft-delete-unbind"

        def soft_delete_student() -> None:
            try:
                with Session(postgres_engine) as session, session.begin():
                    person = session.scalar(
                        select(Person)
                        .where(Person.id == ids["student_id"])
                        .with_for_update()
                    )
                    assert person is not None
                    person.deleted_at = NOW + timedelta(hours=1)
                    session.flush()
                    deletion_holds_lock.set()
                    assert release_deletion.wait(timeout=10)
            except Exception:  # noqa: BLE001 - asserted after both workers join
                thread_errors.append(traceback.format_exc())
                deletion_holds_lock.set()

        def unbind_account() -> None:
            try:
                with Session(postgres_engine) as session:
                    backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(backend_pid, int)
                    backend_pids.append(backend_pid)
                    operation_started.set()
                    GameAccountService(
                        session,
                        cipher=EnvelopeCipher(CIPHER_KEY),
                        lookup=LookupDigest(LOOKUP_KEY),
                    ).unbind_game_account(
                        ids["student_id"],
                        account_id,
                        GameAccountUnbind(reason="软删除并发解绑必须拒绝"),
                        actor_user_id=ids["admin_id"],
                        occurred_at=NOW + timedelta(hours=2),
                        request_id=request_id,
                    )
            except EvaluationResourceNotFound:
                outcomes.append("not_found")
            except Exception:  # noqa: BLE001 - asserted after both workers join
                thread_errors.append(traceback.format_exc())
            else:
                outcomes.append("success")

        delete_thread = threading.Thread(target=soft_delete_student)
        unbind_thread = threading.Thread(target=unbind_account)
        observed_lock_wait = False
        try:
            delete_thread.start()
            assert deletion_holds_lock.wait(timeout=5)
            unbind_thread.start()
            assert operation_started.wait(timeout=5)
            observed_lock_wait = _wait_for_database_lock(
                postgres_engine,
                backend_pid=backend_pids[0],
                worker=unbind_thread,
            )
        finally:
            release_deletion.set()
            delete_thread.join(timeout=10)
            unbind_thread.join(timeout=10)

        assert observed_lock_wait
        assert not delete_thread.is_alive()
        assert not unbind_thread.is_alive()
        assert thread_errors == []
        assert outcomes == ["not_found"]
        with Session(postgres_engine) as verification:
            stored = verification.get(StudentGameAccount, account_id)
            assert stored is not None
            assert (stored.unbound_at, stored.unbound_by_user_id, stored.unbind_reason) == (
                None,
                None,
                None,
            )
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.request_id == request_id
                )
            ) == 0


def test_soft_delete_serializes_before_concurrent_bind(
    postgres_engine: Engine,
) -> None:
    with _committed_limit_setup(postgres_engine) as ids:
        deletion_holds_lock = threading.Event()
        release_deletion = threading.Event()
        operation_started = threading.Event()
        backend_pids: list[int] = []
        outcomes: list[str] = []
        thread_errors: list[str] = []
        project_id = INITIAL_PROJECTS[2].id
        request_id = "concurrent-soft-delete-bind"

        def soft_delete_student() -> None:
            try:
                with Session(postgres_engine) as session, session.begin():
                    person = session.scalar(
                        select(Person)
                        .where(Person.id == ids["student_id"])
                        .with_for_update()
                    )
                    assert person is not None
                    person.deleted_at = NOW + timedelta(hours=1)
                    session.flush()
                    deletion_holds_lock.set()
                    assert release_deletion.wait(timeout=10)
            except Exception:  # noqa: BLE001 - asserted after both workers join
                thread_errors.append(traceback.format_exc())
                deletion_holds_lock.set()

        def bind_account() -> None:
            try:
                with Session(postgres_engine) as session:
                    backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(backend_pid, int)
                    backend_pids.append(backend_pid)
                    operation_started.set()
                    GameAccountService(
                        session,
                        cipher=EnvelopeCipher(CIPHER_KEY),
                        lookup=LookupDigest(LOOKUP_KEY),
                    ).bind_game_account(
                        ids["student_id"],
                        GameAccountBind(
                            project_id=project_id,
                            game_id="ConcurrentDeletedStudent",
                        ),
                        actor_user_id=ids["admin_id"],
                        occurred_at=NOW + timedelta(hours=2),
                        request_id=request_id,
                    )
            except EvaluationResourceNotFound:
                outcomes.append("not_found")
            except Exception:  # noqa: BLE001 - asserted after both workers join
                thread_errors.append(traceback.format_exc())
            else:
                outcomes.append("success")

        delete_thread = threading.Thread(target=soft_delete_student)
        bind_thread = threading.Thread(target=bind_account)
        observed_lock_wait = False
        try:
            delete_thread.start()
            assert deletion_holds_lock.wait(timeout=5)
            bind_thread.start()
            assert operation_started.wait(timeout=5)
            observed_lock_wait = _wait_for_database_lock(
                postgres_engine,
                backend_pid=backend_pids[0],
                worker=bind_thread,
            )
        finally:
            release_deletion.set()
            delete_thread.join(timeout=10)
            bind_thread.join(timeout=10)

        assert observed_lock_wait
        assert not delete_thread.is_alive()
        assert not bind_thread.is_alive()
        assert thread_errors == []
        assert outcomes == ["not_found"]
        with Session(postgres_engine) as verification:
            assert verification.scalar(
                select(func.count(StudentGameAccount.id)).where(
                    StudentGameAccount.student_person_id == ids["student_id"],
                    StudentGameAccount.project_id == project_id,
                )
            ) == 0
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.request_id == request_id
                )
            ) == 0


def test_concurrent_fourth_binds_never_exceed_the_locked_limit(
    postgres_engine: Engine,
) -> None:
    with _committed_limit_setup(postgres_engine) as ids:
        first_has_profile_lock = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        backend_pids: dict[str, int] = {}
        successes: list[UUID] = []
        failures: list[Exception] = []

        def pause_first_after_profile_lock(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany: bool,
        ) -> None:
            normalized = statement.lower()
            if (
                threading.current_thread().name == "limit-first"
                and "student_profiles" in normalized
                and "for update" in normalized
                and not first_has_profile_lock.is_set()
            ):
                first_has_profile_lock.set()
                assert release_first.wait(timeout=10)

        def bind(project_index: int, worker_name: str) -> None:
            try:
                with Session(postgres_engine) as worker:
                    pid = worker.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(pid, int)
                    backend_pids[worker_name] = pid
                    if worker_name == "second":
                        second_started.set()
                    account = GameAccountService(
                        worker,
                        cipher=EnvelopeCipher(CIPHER_KEY),
                        lookup=LookupDigest(LOOKUP_KEY),
                    ).bind_game_account(
                        ids["student_id"],
                        GameAccountBind(
                            project_id=INITIAL_PROJECTS[project_index].id,
                            game_id=f"ConcurrentPlayer{project_index}",
                        ),
                        actor_user_id=ids["admin_id"],
                        occurred_at=NOW,
                        request_id=f"concurrent-game-bind-{project_index}",
                    )
                    successes.append(account.id)
            except Exception as exc:  # noqa: BLE001 - asserted after join
                failures.append(exc)

        first = threading.Thread(target=bind, args=(2, "first"), name="limit-first")
        second = threading.Thread(target=bind, args=(3, "second"), name="limit-second")
        event.listen(postgres_engine, "after_cursor_execute", pause_first_after_profile_lock)
        try:
            first.start()
            assert first_has_profile_lock.wait(timeout=5)
            second.start()
            assert second_started.wait(timeout=5)
            deadline = time.monotonic() + 5
            waiting_on_lock = False
            while time.monotonic() < deadline and second.is_alive():
                with postgres_engine.connect() as observer:
                    wait_event_type = observer.scalar(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid = :backend_pid"
                        ),
                        {"backend_pid": backend_pids["second"]},
                    )
                if wait_event_type == "Lock":
                    waiting_on_lock = True
                    break
                time.sleep(0.01)
            assert waiting_on_lock
        finally:
            release_first.set()
            first.join(timeout=10)
            if second.ident is not None:
                second.join(timeout=10)
            event.remove(
                postgres_engine,
                "after_cursor_execute",
                pause_first_after_profile_lock,
            )
        workers = [first, second]
        assert all(not worker.is_alive() for worker in workers)
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], GameAccountLimitReached)
        with Session(postgres_engine) as verification:
            assert verification.scalar(
                select(func.count(StudentGameAccount.id)).where(
                    StudentGameAccount.student_person_id == ids["student_id"],
                    StudentGameAccount.unbound_at.is_(None),
                )
            ) == 3


def test_project_deactivation_serializes_before_concurrent_bind(
    postgres_engine: Engine,
) -> None:
    with _committed_limit_setup(postgres_engine) as ids:
        project_id = INITIAL_PROJECTS[2].id
        deactivation_holds_lock = threading.Event()
        release_deactivation = threading.Event()
        bind_started = threading.Event()
        backend_pids: list[int] = []
        outcomes: list[str] = []
        thread_errors: list[str] = []

        def pause_deactivation_after_update(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany: bool,
        ) -> None:
            if (
                threading.current_thread().name == "concurrent-project-deactivation"
                and "update game_projects" in statement.lower()
            ):
                deactivation_holds_lock.set()
                assert release_deactivation.wait(timeout=10)

        def deactivate_project() -> None:
            try:
                with Session(postgres_engine) as session:
                    TeachingCatalogService(session).update_project(
                        project_id,
                        ProjectUpdate(is_active=False),
                        actor_user_id=ids["admin_id"],
                    )
            except Exception:  # noqa: BLE001 - asserted after both workers join
                thread_errors.append(traceback.format_exc())
                deactivation_holds_lock.set()

        def bind_account() -> None:
            try:
                with Session(postgres_engine) as session:
                    backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(backend_pid, int)
                    backend_pids.append(backend_pid)
                    bind_started.set()
                    service = GameAccountService(
                        session,
                        cipher=EnvelopeCipher(CIPHER_KEY),
                        lookup=LookupDigest(LOOKUP_KEY),
                    )
                    service.bind_game_account(
                        ids["student_id"],
                        GameAccountBind(
                            project_id=project_id,
                            game_id="ConcurrentInactiveProject",
                        ),
                        actor_user_id=ids["admin_id"],
                        occurred_at=NOW + timedelta(hours=1),
                        request_id="concurrent-project-bind",
                    )
            except EvaluationResourceNotFound:
                outcomes.append("not_found")
            except Exception:  # noqa: BLE001 - asserted after both workers join
                thread_errors.append(traceback.format_exc())
            else:
                outcomes.append("success")

        event.listen(
            postgres_engine,
            "after_cursor_execute",
            pause_deactivation_after_update,
        )
        deactivate_thread = threading.Thread(
            target=deactivate_project,
            name="concurrent-project-deactivation",
        )
        bind_thread = threading.Thread(target=bind_account, name="concurrent-project-bind")
        observed_lock_wait = False
        try:
            deactivate_thread.start()
            assert deactivation_holds_lock.wait(timeout=5)
            bind_thread.start()
            assert bind_started.wait(timeout=5)
            observed_lock_wait = _wait_for_database_lock(
                postgres_engine,
                backend_pid=backend_pids[0],
                worker=bind_thread,
            )
        finally:
            release_deactivation.set()
            deactivate_thread.join(timeout=10)
            bind_thread.join(timeout=10)
            event.remove(
                postgres_engine,
                "after_cursor_execute",
                pause_deactivation_after_update,
            )
            with Session(postgres_engine) as cleanup, cleanup.begin():
                project = cleanup.get(GameProject, project_id)
                assert project is not None
                project.is_active = True
                project.updated_by_user_id = None

        assert not deactivate_thread.is_alive()
        assert not bind_thread.is_alive()
        assert thread_errors == []
        assert observed_lock_wait
        assert outcomes == ["not_found"]


def test_auxiliary_phone_encryption_matching_and_replace_are_privacy_safe(
    game_scenario: GameAccountScenario,
) -> None:
    service = game_scenario.service
    phone = "13800138000"
    first = service.add_auxiliary_phone(
        game_scenario.people["student"].id,
        AuxiliaryPhoneCreate(phone=phone),
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW,
        request_id="aux-phone-first",
    )
    second = service.add_auxiliary_phone(
        game_scenario.people["other"].id,
        AuxiliaryPhoneCreate(phone=phone),
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW,
        request_id="aux-phone-second",
    )
    assert first.phone_ciphertext != second.phone_ciphertext
    assert first.phone_digest == second.phone_digest == LookupDigest(LOOKUP_KEY).digest(phone)
    assert game_scenario.db.scalar(
        select(func.count(UserAccount.id)).where(
            UserAccount.login_phone_digest == first.phone_digest
        )
    ) == 0
    match = service.match_auxiliary_phone(
        phone,
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW,
    )
    assert (match.student_person_id, match.match_count, match.ambiguous) == (None, 2, True)
    assert match.model_dump() == {"match_count": 2, "ambiguous": True}
    assert not hasattr(match, "student_names")
    old_ciphertext = first.phone_ciphertext
    replacement = service.replace_auxiliary_phone(
        game_scenario.people["student"].id,
        first.id,
        AuxiliaryPhoneReplace(phone="13900139000", reason="更新备用联系电话"),
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        request_id="aux-phone-replace",
    )
    game_scenario.db.expire_all()
    stored_old = game_scenario.db.get(StudentAuxiliaryPhone, first.id)
    assert stored_old is not None
    assert stored_old.phone_ciphertext == old_ciphertext
    assert stored_old.disabled_at == NOW + timedelta(hours=1)
    assert replacement.id != first.id
    assert service.list_auxiliary_phones(
        game_scenario.people["student"].id,
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        include_inactive=True,
    )[0].masked_phone in {"138****8000", "139****9000"}
    unique_old_phone = service.match_auxiliary_phone(
        phone,
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
    )
    assert (
        unique_old_phone.student_person_id,
        unique_old_phone.match_count,
        unique_old_phone.ambiguous,
    ) == (game_scenario.people["other"].id, 1, False)


def test_auxiliary_phone_history_is_bounded_and_paged(
    postgres_engine: Engine,
    game_scenario: GameAccountScenario,
) -> None:
    phones = [
        game_scenario.service.add_auxiliary_phone(
            game_scenario.people["student"].id,
            AuxiliaryPhoneCreate(phone=f"1380013800{index}"),
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(minutes=index),
            request_id=f"paged-auxiliary-phone-{index}",
        )
        for index in range(3)
    ]
    recording_cipher = RecordingCipher(CIPHER_KEY)
    service = GameAccountService(
        game_scenario.db,
        cipher=recording_cipher,
        lookup=LookupDigest(LOOKUP_KEY),
    )
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if "from student_auxiliary_phones" in normalized:
            statements.append(normalized)

    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        first_page = service.list_auxiliary_phones(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=1),
            include_inactive=True,
            page=1,
            page_size=2,
        )
        second_page = service.list_auxiliary_phones(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=1),
            include_inactive=True,
            page=2,
            page_size=2,
        )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)

    assert [item.id for item in first_page] == [phones[2].id, phones[1].id]
    assert [item.id for item in second_page] == [phones[0].id]
    assert recording_cipher.decrypt_calls == 3
    assert len(statements) == 2
    assert all(" limit " in statement for statement in statements)


@pytest.mark.parametrize(
    ("page", "page_size"),
    [(0, 20), (1, 0), (1, 101)],
)
def test_auxiliary_phone_history_pagination_rejects_unbounded_values(
    game_scenario: GameAccountScenario,
    page: int,
    page_size: int,
) -> None:
    with pytest.raises(ValueError):
        game_scenario.service.list_auxiliary_phones(
            game_scenario.people["student"].id,
            actor_user_id=game_scenario.accounts["admin"].id,
            occurred_at=NOW,
            include_inactive=True,
            page=page,
            page_size=page_size,
        )


def test_auxiliary_phone_matching_filters_person_denies_before_counting(
    game_scenario: GameAccountScenario,
) -> None:
    service = game_scenario.service
    admin_id = game_scenario.accounts["admin"].id
    phone = "13600136000"
    for student in ("student", "other"):
        service.add_auxiliary_phone(
            game_scenario.people[student].id,
            AuxiliaryPhoneCreate(phone=phone),
            actor_user_id=admin_id,
            occurred_at=NOW,
            request_id=f"aux-match-before-deny-{student}",
        )

    _person_deny(game_scenario, student="student")
    unique = service.match_auxiliary_phone(
        phone,
        actor_user_id=admin_id,
        occurred_at=NOW + timedelta(hours=1),
    )
    assert unique.model_dump() == {
        "student_person_id": game_scenario.people["other"].id,
        "match_count": 1,
        "ambiguous": False,
    }

    _person_deny(game_scenario, student="other")
    hidden = service.match_auxiliary_phone(
        phone,
        actor_user_id=admin_id,
        occurred_at=NOW + timedelta(hours=1),
    )
    assert hidden.model_dump() == {"match_count": 0, "ambiguous": False}


def test_auxiliary_phone_matching_excludes_soft_deleted_students(
    game_scenario: GameAccountScenario,
) -> None:
    service = game_scenario.service
    admin_id = game_scenario.accounts["admin"].id
    phone = "13300133000"
    for student in ("student", "other"):
        service.add_auxiliary_phone(
            game_scenario.people[student].id,
            AuxiliaryPhoneCreate(phone=phone),
            actor_user_id=admin_id,
            occurred_at=NOW,
            request_id=f"aux-match-soft-delete-{student}",
        )
    game_scenario.people["other"].deleted_at = NOW + timedelta(hours=1)
    game_scenario.db.flush()

    match = service.match_auxiliary_phone(
        phone,
        actor_user_id=admin_id,
        occurred_at=NOW + timedelta(hours=1),
    )

    assert match.model_dump() == {
        "student_person_id": game_scenario.people["student"].id,
        "match_count": 1,
        "ambiguous": False,
    }


def test_auxiliary_phone_matching_authorization_query_count_is_candidate_bounded(
    postgres_engine: Engine,
    game_scenario: GameAccountScenario,
) -> None:
    service = game_scenario.service
    admin_id = game_scenario.accounts["admin"].id
    small_phone = "13100131000"
    large_phone = "13200132000"
    service.add_auxiliary_phone(
        game_scenario.people["student"].id,
        AuxiliaryPhoneCreate(phone=small_phone),
        actor_user_id=admin_id,
        occurred_at=NOW,
        request_id="aux-match-query-small",
    )
    candidate_ids = [
        game_scenario.people["student"].id,
        game_scenario.people["other"].id,
    ]
    for index in range(4):
        person, _account_row = _student(
            game_scenario.db,
            f"查询上界候选学员{index}",
            f"query-bound-student-{index}",
        )
        candidate_ids.append(person.id)
    for index, student_id in enumerate(candidate_ids):
        service.add_auxiliary_phone(
            student_id,
            AuxiliaryPhoneCreate(phone=large_phone),
            actor_user_id=admin_id,
            occurred_at=NOW,
            request_id=f"aux-match-query-large-{index}",
        )

    def count_match_queries(phone: str) -> tuple[int, AuxiliaryPhoneMatch]:
        statements: list[str] = []

        def capture_statement(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(postgres_engine, "before_cursor_execute", capture_statement)
        try:
            match = service.match_auxiliary_phone(
                phone,
                actor_user_id=admin_id,
                occurred_at=NOW + timedelta(hours=1),
            )
        finally:
            event.remove(postgres_engine, "before_cursor_execute", capture_statement)
        return len(statements), match

    small_query_count, small_match = count_match_queries(small_phone)
    large_query_count, large_match = count_match_queries(large_phone)

    assert small_match.match_count == 1
    assert large_match.match_count == len(candidate_ids)
    assert large_query_count <= small_query_count + 1


def test_auxiliary_phone_matching_caps_high_cardinality_before_authorization(
    postgres_engine: Engine,
    game_scenario: GameAccountScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = game_scenario.service
    admin_id = game_scenario.accounts["admin"].id
    shared_phone = "13000130000"
    for index in range(101):
        person, _account_row = _student(
            game_scenario.db,
            f"高基数匹配候选学员{index}",
            f"high-cardinality-student-{index}",
        )
        service.add_auxiliary_phone(
            person.id,
            AuxiliaryPhoneCreate(phone=shared_phone),
            actor_user_id=admin_id,
            occurred_at=NOW,
            request_id=f"high-cardinality-phone-{index}",
        )

    def reject_unbounded_authorization(**_kwargs):
        pytest.fail("overflow candidates must not enter object authorization")

    monkeypatch.setattr(
        service.access,
        "filter_resources",
        reject_unbounded_authorization,
    )
    candidate_queries: list[tuple[str, object]] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        parameters,
        _context,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if (
            "from student_auxiliary_phones join people" in normalized
            and "phone_digest" in normalized
        ):
            candidate_queries.append((normalized, parameters))

    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        match = service.match_auxiliary_phone(
            shared_phone,
            actor_user_id=admin_id,
            occurred_at=NOW + timedelta(hours=1),
        )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)

    assert match.model_dump() == {"match_count": 2, "ambiguous": True}
    assert len(candidate_queries) == 1
    statement, parameters = candidate_queries[0]
    assert " limit " in statement
    assert len(parameters) <= 3


def test_authentication_never_queries_or_resolves_auxiliary_phones(
    postgres_engine: Engine,
    game_scenario: GameAccountScenario,
) -> None:
    phone = "13600136000"
    game_scenario.service.add_auxiliary_phone(
        game_scenario.people["student"].id,
        AuxiliaryPhoneCreate(phone=phone),
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW,
        request_id="aux-phone-auth-boundary",
    )
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lower())

    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        with pytest.raises(InvalidCredentials):
            AuthService(
                game_scenario.db,
                lookup=LookupDigest(LOOKUP_KEY),
                passwords=PasswordService(),
                session_token_pepper=b"a" * 32,
                session_ttl_minutes=60,
                clock=lambda: NOW,
            ).login(
                phone=phone,
                password="Not-a-real-password-2026",
                ip_address="127.0.0.1",
                request_id="aux-phone-login-must-fail",
            )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)
    assert statements
    assert all("student_auxiliary_phones" not in statement for statement in statements)


def test_auxiliary_phone_deactivation_removes_it_from_matching(
    game_scenario: GameAccountScenario,
) -> None:
    phone = "13700137000"
    row = game_scenario.service.add_auxiliary_phone(
        game_scenario.people["student"].id,
        AuxiliaryPhoneCreate(phone=phone),
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW,
        request_id="aux-phone-add-deactivate",
    )
    game_scenario.service.deactivate_auxiliary_phone(
        game_scenario.people["student"].id,
        row.id,
        AuxiliaryPhoneDeactivate(reason="不再使用该备用手机号"),
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        request_id="aux-phone-deactivate",
    )
    match = game_scenario.service.match_auxiliary_phone(
        phone,
        actor_user_id=game_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
    )
    assert (match.student_person_id, match.match_count, match.ambiguous) == (None, 0, False)


def test_unbind_is_idempotency_conflict_not_history_rewrite(
    game_scenario: GameAccountScenario,
) -> None:
    row = _bind(game_scenario, game_id="UnbindOnce")
    game_scenario.service.unbind_game_account(
        game_scenario.people["student"].id,
        row.id,
        GameAccountUnbind(reason="第一次解除绑定"),
        actor_user_id=game_scenario.accounts["student"].id,
        occurred_at=NOW + timedelta(hours=1),
        request_id="game-unbind-first",
    )
    with pytest.raises(GameAccountAlreadyUnbound) as error:
        game_scenario.service.unbind_game_account(
            game_scenario.people["student"].id,
            row.id,
            GameAccountUnbind(reason="重复解除绑定"),
            actor_user_id=game_scenario.accounts["student"].id,
            occurred_at=NOW + timedelta(hours=2),
            request_id="game-unbind-second",
        )
    assert error.value.code == "game_account_already_unbound"
