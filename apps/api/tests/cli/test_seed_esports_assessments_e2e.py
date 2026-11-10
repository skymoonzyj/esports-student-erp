from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.cli.seed_esports_assessments_e2e as seed_module
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_ADMIN_PERSON_ID,
    E2E_COACH_ACCOUNT_ID,
    E2E_COACH_FAMILY_RELATIONSHIP_ID,
    E2E_COACH_PERSON_ID,
    E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
    E2E_GUARDIAN_ACCOUNT_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_LATER_COHORT_ID,
    E2E_LIFE_TEACHER_ACCOUNT_ID,
    E2E_LIFE_TEACHER_PERSON_ID,
    E2E_SELF_STUDENT_ACCOUNT_ID,
    E2E_SELF_STUDENT_PERSON_ID,
)
from app.cli.seed_esports_assessments_e2e import (
    EsportsAssessmentE2EEvidenceError,
    EsportsAssessmentE2EEvidenceSummary,
    EsportsAssessmentE2ESeedRefused,
    EsportsAssessmentE2ESeedResult,
    seed_esports_assessments_e2e,
    validate_esports_assessments_e2e_evidence,
    verify_esports_assessments_e2e_evidence,
)
from app.core.config import Settings
from app.esports_assessments.catalog import get_suite
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.identity.models import GuardianRelationship, StaffProfile
from app.rbac.models import Role, UserPermissionOverride, UserRole
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import CoachAssignment, Cohort, TeachingClass

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
SUITE_TESTS = {
    "pc-fps": ("gridshot", "sixshot", "spheretrack90"),
    "pc-moba": ("last-hit",),
    "mobile-fps": ("precision", "tracking", "switching", "move-fire"),
    "mobile-moba": ("dodge", "skill-aim", "move-attack", "map-awareness"),
}


def settings(*, app_env: str = "test", database: str = "erp_e2e") -> Settings:
    return Settings(
        app_env=app_env,
        database_url=f"postgresql+psycopg://erp:erp@db:5432/{database}",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token="evaluation-local-service-token-2026-one",
        evaluation_tool_web_service_token="evaluation-local-web-token-2026-two",
        evaluation_tool_worker_service_token="evaluation-local-worker-token-2026-three",
    )


def _run_seed(db: object, value: Settings) -> EsportsAssessmentE2ESeedResult:
    return seed_esports_assessments_e2e(
        cast(Session, db),
        settings=value,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
    )


class _UntouchedDatabase:
    def __getattribute__(self, name: str) -> Any:
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError(f"database was touched through {name}")


@pytest.mark.parametrize(
    "unsafe_settings",
    [
        settings(app_env="production"),
        settings(database="production"),
        settings(database="erp_e2e?dbname=production"),
    ],
)
def test_seed_refuses_unsafe_environment_before_seed_or_database(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_settings: Settings,
) -> None:
    seed_called = False

    def forbidden_seed(*args: object, **kwargs: object) -> None:
        nonlocal seed_called
        seed_called = True

    monkeypatch.setattr(seed_module, "seed_e2e", forbidden_seed)

    with pytest.raises(EsportsAssessmentE2ESeedRefused, match="isolated"):
        _run_seed(_UntouchedDatabase(), unsafe_settings)

    assert seed_called is False


def test_seed_validates_public_database_guard_before_generic_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    db = object()
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    passwords = PasswordService()

    def validate_url(value: str) -> None:
        assert value.endswith("/erp_e2e")
        events.append("database-validated")

    def generic_seed(
        received_db: object,
        *,
        settings: Settings,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        passwords: PasswordService,
    ) -> object:
        assert received_db is db
        assert settings.app_env == "test"
        assert cipher is cipher_value
        assert lookup is lookup_value
        assert passwords is passwords_value
        events.append("generic-seed")
        raise RuntimeError("stop-after-generic-seed")

    cipher_value = cipher
    lookup_value = lookup
    passwords_value = passwords
    monkeypatch.setattr(seed_module, "require_e2e_database_url", validate_url)
    monkeypatch.setattr(seed_module, "seed_e2e", generic_seed)

    with pytest.raises(RuntimeError, match="stop-after-generic-seed"):
        seed_esports_assessments_e2e(
            cast(Session, db),
            settings=settings(),
            cipher=cipher,
            lookup=lookup,
            passwords=passwords,
        )

    assert events == ["database-validated", "generic-seed"]


def _role_keys(db: Session, account_id: UUID) -> set[str]:
    return set(
        db.scalars(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == account_id)
        )
    )


def test_seed_is_idempotent_and_builds_authorization_fixture_with_zero_runs(
    db: Session,
) -> None:
    first = _run_seed(db, settings())
    first_relationship_created_at = db.scalar(
        select(GuardianRelationship.created_at).where(
            GuardianRelationship.id == E2E_COACH_FAMILY_RELATIONSHIP_ID
        )
    )
    second = _run_seed(db, settings())

    assert first == second
    relationship = db.get(
        GuardianRelationship,
        E2E_COACH_FAMILY_RELATIONSHIP_ID,
    )
    assert relationship is not None
    assert relationship.guardian_person_id == E2E_GUARDIAN_PERSON_ID
    assert relationship.student_person_id == E2E_SELF_STUDENT_PERSON_ID
    assert relationship.valid_until is None
    assert relationship.ended_at is None
    assert relationship.created_at == first_relationship_created_at

    assert _role_keys(db, E2E_SELF_STUDENT_ACCOUNT_ID) == {"student"}
    assert _role_keys(db, E2E_GUARDIAN_ACCOUNT_ID) == {"guardian"}
    assert _role_keys(db, E2E_COACH_ACCOUNT_ID) == {"coach"}
    assert _role_keys(db, E2E_LIFE_TEACHER_ACCOUNT_ID) == {"coach"}
    assert _role_keys(db, E2E_CUSTOMER_SERVICE_ACCOUNT_ID) == {"admin"}
    assert _role_keys(db, E2E_ADMIN_ACCOUNT_ID) == {"super_admin"}

    assigned_coach = db.get(StaffProfile, E2E_COACH_PERSON_ID)
    unrelated_coach = db.get(StaffProfile, E2E_LIFE_TEACHER_PERSON_ID)
    ordinary_admin = db.get(UserAccount, E2E_CUSTOMER_SERVICE_ACCOUNT_ID)
    assert assigned_coach is not None
    assert unrelated_coach is not None
    assert ordinary_admin is not None
    ordinary_admin_profile = db.get(StaffProfile, ordinary_admin.person_id)
    assert ordinary_admin_profile is not None
    assert assigned_coach.staff_type == "coach"
    assert unrelated_coach.staff_type == "coach"
    assert ordinary_admin_profile.staff_type == "administration"

    assert db.scalar(
        select(func.count())
        .select_from(CoachAssignment)
        .where(CoachAssignment.coach_person_id == E2E_LIFE_TEACHER_PERSON_ID)
    ) == 0
    assigned = db.scalar(
        select(CoachAssignment)
        .join(TeachingClass, TeachingClass.id == CoachAssignment.class_id)
        .where(CoachAssignment.coach_person_id == E2E_COACH_PERSON_ID)
        .where(TeachingClass.cohort_id == E2E_LATER_COHORT_ID)
        .order_by(CoachAssignment.id)
    )
    assert assigned is not None
    now = datetime.now(assigned.valid_from.tzinfo)
    assert assigned.valid_from < now
    assert assigned.valid_until is not None
    assert assigned.valid_until > now
    cohort_id = db.scalar(
        select(TeachingClass.cohort_id).where(TeachingClass.id == assigned.class_id)
    )
    assert cohort_id is not None
    cohort = db.get(Cohort, cohort_id)
    assert cohort is not None
    assert cohort.status == "active"
    assert cohort.starts_at < datetime.now(cohort.starts_at.tzinfo) < cohort.ends_at

    assert db.scalar(
        select(func.count())
        .select_from(UserPermissionOverride)
        .where(
            UserPermissionOverride.user_id.in_(
                (E2E_CUSTOMER_SERVICE_ACCOUNT_ID, E2E_LIFE_TEACHER_ACCOUNT_ID)
            )
        )
    ) == 0
    for model in (
        AssessmentSession,
        AssessmentAttempt,
        AssessmentResult,
        AssessmentReport,
        AssessmentPracticeRun,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action.like("esports_assessments.%"))
    ) == 0
    assert verify_esports_assessments_e2e_evidence(
        db,
        "capabilities-webkit",
    ) == EsportsAssessmentE2EEvidenceSummary(
        mode="capabilities-webkit",
        session_count=0,
        attempt_count=0,
        result_count=0,
        report_count=0,
        practice_count=0,
        audit_count=0,
    )

def _uuid(value: int) -> UUID:
    return UUID(int=value)


def _valid_evidence(mode: str) -> dict[str, list[SimpleNamespace]]:
    rows: dict[str, list[SimpleNamespace]] = {
        "sessions": [],
        "attempts": [],
        "results": [],
        "reports": [],
        "practices": [],
        "audits": [],
    }
    operation_number = 10_000

    def operation() -> UUID:
        nonlocal operation_number
        operation_number += 1
        return _uuid(operation_number)

    def suite_snapshot(suite_id: str) -> dict[str, Any]:
        return get_suite(
            suite_id,
            catalog_version=f"{suite_id}-v{5 if suite_id in {'pc-fps', 'pc-moba'} else 3}",
        ).model_dump(mode="json")

    session_specs: tuple[tuple[str, str], ...]
    if mode == "pc":
        session_specs = (("pc-fps", "completed"), ("pc-moba", "completed"))
    elif mode == "touch":
        session_specs = (
            ("mobile-fps", "completed"),
            ("mobile-moba", "completed"),
        )
    elif mode == "recovery":
        session_specs = (("pc-fps", "in_progress"),)
    else:
        session_specs = ()

    for session_index, (suite_id, state) in enumerate(session_specs, start=1):
        session_id = _uuid(session_index)
        creation_key = operation()
        frozen_suite = suite_snapshot(suite_id)
        session = SimpleNamespace(
            id=session_id,
            person_id=E2E_ADMIN_PERSON_ID,
            suite_id=suite_id,
            status=state,
            creation_key=creation_key,
            abandon_key=None,
            catalog_snapshot=deepcopy(frozen_suite),
            scoring_snapshot=deepcopy(frozen_suite),
            report_rules_snapshot=deepcopy(frozen_suite),
        )
        rows["sessions"].append(session)
        rows["audits"].append(
            SimpleNamespace(
                id=_uuid(30_000 + len(rows["audits"])),
                action="esports_assessments.started",
                resource_type="esports_assessment_session",
                resource_id=session_id,
                outcome="allowed",
                event_metadata={
                    "session_id": str(session_id),
                    "person_id": str(E2E_ADMIN_PERSON_ID),
                    "operation_key": str(creation_key),
                    "suite_id": suite_id,
                    "catalog_version": frozen_suite["catalog_version"],
                    "scoring_version": frozen_suite["tasks"][SUITE_TESTS[suite_id][0]][
                        "scoring_version"
                    ],
                    "result_count": 0,
                },
            )
        )

        if mode in {"pc", "touch"}:
            test_states = tuple((test_id, "submitted") for test_id in SUITE_TESTS[suite_id])
        else:
            test_states = (("gridshot", "submitted"), ("sixshot", "interrupted"))
        final_submission_key: UUID | None = None
        for test_index, (test_id, attempt_state) in enumerate(test_states, start=1):
            attempt_id = _uuid(session_index * 100 + test_index)
            creation = operation()
            interrupt = operation() if attempt_state == "interrupted" else None
            rows["attempts"].append(
                SimpleNamespace(
                    id=attempt_id,
                    session_id=session_id,
                    test_id=test_id,
                    state=attempt_state,
                    creation_key=creation,
                    interrupt_key=interrupt,
                    config_snapshot=deepcopy(frozen_suite["tasks"][test_id]),
                )
            )
            if attempt_state == "submitted":
                submission = operation()
                final_submission_key = submission
                rows["results"].append(
                    SimpleNamespace(
                        id=_uuid(session_index * 1_000 + test_index),
                        attempt_id=attempt_id,
                        session_id=session_id,
                        test_id=test_id,
                        submission_key=submission,
                    )
                )
        if state == "completed":
            report_id = _uuid(session_index * 10_000)
            rows["reports"].append(
                SimpleNamespace(
                    id=report_id,
                    session_id=session_id,
                    report_snapshot={
                        "suite": deepcopy(frozen_suite),
                        "results": [
                            {
                                "test_id": test_id,
                                "config": deepcopy(frozen_suite["tasks"][test_id]),
                            }
                            for test_id in SUITE_TESTS[suite_id]
                        ],
                    },
                )
            )
            assert final_submission_key is not None
            rows["audits"].append(
                SimpleNamespace(
                    id=_uuid(30_000 + len(rows["audits"])),
                    action="esports_assessments.completed",
                    resource_type="esports_assessment_session",
                    resource_id=session_id,
                    outcome="allowed",
                    event_metadata={
                        "session_id": str(session_id),
                        "person_id": str(E2E_ADMIN_PERSON_ID),
                        "operation_key": str(final_submission_key),
                        "suite_id": suite_id,
                        "catalog_version": frozen_suite["catalog_version"],
                        "scoring_version": frozen_suite["tasks"][SUITE_TESTS[suite_id][0]][
                            "scoring_version"
                        ],
                        "result_count": len(SUITE_TESTS[suite_id]),
                        "report_id": str(report_id),
                    },
                )
            )

    practice_specs = {
        "pc": (
            ("pc-fps", "gridshot", "submitted"),
            ("pc-moba", "last-hit", "submitted"),
        ),
        "touch": (
            ("mobile-fps", "precision", "submitted"),
            ("mobile-moba", "dodge", "submitted"),
        ),
        "recovery": (("pc-fps", "gridshot", "submitted"),),
        "capabilities-desktop": (("pc-fps", "gridshot", "interrupted"),),
        "capabilities-touch": (("mobile-fps", "move-fire", "interrupted"),),
        "capabilities-webkit": (),
    }[mode]
    for practice_index, (suite_id, test_id, state) in enumerate(practice_specs, start=1):
        rows["practices"].append(
            SimpleNamespace(
                id=_uuid(20_000 + practice_index),
                person_id=E2E_ADMIN_PERSON_ID,
                suite_id=suite_id,
                test_id=test_id,
                state=state,
                creation_key=operation(),
                submission_key=operation() if state == "submitted" else None,
                interrupt_key=operation() if state == "interrupted" else None,
                config_snapshot=deepcopy(suite_snapshot(suite_id)["tasks"][test_id]),
            )
        )
    return rows


@pytest.mark.parametrize(
    ("mode", "counts"),
    [
        ("pc", (2, 4, 4, 2, 2, 4)),
        ("touch", (2, 8, 8, 2, 2, 4)),
        ("recovery", (1, 2, 1, 0, 1, 1)),
        ("capabilities-desktop", (0, 0, 0, 0, 1, 0)),
        ("capabilities-touch", (0, 0, 0, 0, 1, 0)),
        ("capabilities-webkit", (0, 0, 0, 0, 0, 0)),
    ],
)
def test_every_fixed_verifier_mode_accepts_only_its_exact_evidence(
    mode: str,
    counts: tuple[int, int, int, int, int, int],
) -> None:
    rows = _valid_evidence(mode)

    summary = validate_esports_assessments_e2e_evidence(mode=mode, **rows)

    assert summary == EsportsAssessmentE2EEvidenceSummary(
        mode=mode,
        session_count=counts[0],
        attempt_count=counts[1],
        result_count=counts[2],
        report_count=counts[3],
        practice_count=counts[4],
        audit_count=counts[5],
    )


def _remove_last(rows: list[SimpleNamespace]) -> None:
    rows.pop()


def _add_extra(rows: list[SimpleNamespace]) -> None:
    rows.append(deepcopy(rows[0]))
    rows[-1].id = _uuid(90_000)


def _wrong_state(rows: list[SimpleNamespace]) -> None:
    rows[0].state = "running"


@pytest.mark.parametrize(
    ("collection", "mutate", "reason"),
    [
        ("sessions", _remove_last, "session_shape"),
        ("attempts", _add_extra, "attempt_shape"),
        ("results", _remove_last, "result_shape"),
        ("reports", _add_extra, "report_shape"),
        ("practices", _wrong_state, "practice_shape"),
    ],
)
def test_each_persisted_model_rejects_missing_extra_or_wrong_state_rows(
    collection: str,
    mutate: Callable[[list[SimpleNamespace]], None],
    reason: str,
) -> None:
    rows = _valid_evidence("pc")
    mutate(rows[collection])

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match=reason):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


def test_verifier_rejects_running_formal_rows_even_when_counts_match() -> None:
    rows = _valid_evidence("recovery")
    rows["attempts"][1].state = "running"

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="pending_attempt"):
        validate_esports_assessments_e2e_evidence(mode="recovery", **rows)


def test_verifier_rejects_cross_table_identity_and_operation_reuse() -> None:
    identity_rows = _valid_evidence("recovery")
    identity_rows["results"][0].attempt_id = identity_rows["attempts"][1].id

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="result_identity"):
        validate_esports_assessments_e2e_evidence(mode="recovery", **identity_rows)

    operation_rows = _valid_evidence("capabilities-desktop")
    operation_rows["practices"][0].interrupt_key = operation_rows["practices"][0].creation_key

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="operation_identity"):
        validate_esports_assessments_e2e_evidence(
            mode="capabilities-desktop",
            **operation_rows,
        )


def test_verifier_rejects_non_preview_assessment_owner() -> None:
    rows = _valid_evidence("pc")
    for row in rows["sessions"]:
        row.person_id = E2E_SELF_STUDENT_PERSON_ID
    for row in rows["practices"]:
        row.person_id = E2E_SELF_STUDENT_PERSON_ID
    for row in rows["audits"]:
        row.event_metadata["person_id"] = str(E2E_SELF_STUDENT_PERSON_ID)

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="session_identity"):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


@pytest.mark.parametrize(
    ("collection", "mutate", "reason"),
    [
        (
            "sessions",
            lambda row: row.catalog_snapshot.__setitem__("catalog_version", f"{row.suite_id}-v2"),
            "session_config_identity",
        ),
        (
            "attempts",
            lambda row: row.config_snapshot.__setitem__(
                "catalog_version", f"{row.config_snapshot['suite_id']}-v2"
            ),
            "attempt_config_identity",
        ),
        (
            "reports",
            lambda row: row.report_snapshot["suite"].__setitem__(
                "catalog_version",
                f"{row.report_snapshot['suite']['suite_id']}-v2",
            ),
            "report_config_identity",
        ),
        (
            "practices",
            lambda row: row.config_snapshot.__setitem__("catalog_version", f"{row.suite_id}-v2"),
            "practice_config_identity",
        ),
    ],
)
def test_verifier_rejects_stale_frozen_v2_configuration_identity(
    collection: str,
    mutate: Callable[[SimpleNamespace], None],
    reason: str,
) -> None:
    rows = _valid_evidence("pc")
    mutate(rows[collection][0])

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match=reason):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


@pytest.mark.parametrize("attack_mode", ["melee", "ranged"])
def test_verifier_pins_both_lane_modes_to_their_entire_registered_snapshot(attack_mode: str) -> None:
    task = get_suite("pc-moba", attack_mode=attack_mode).model_dump(mode="json")["tasks"]["last-hit"]
    assert seed_module._frozen_task(task, suite_id="pc-moba", test_id="last-hit", reason="lane_identity") == task
    task["params"]["attack_damage"] += 1
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="lane_identity"):
        seed_module._frozen_task(task, suite_id="pc-moba", test_id="last-hit", reason="lane_identity")


def test_current_acceptance_rejects_frozen_no_tower_lane_snapshot() -> None:
    legacy = get_suite("pc-moba", catalog_version="pc-moba-v4", attack_mode="melee")
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="lane_identity"):
        seed_module._frozen_task(
            legacy.model_dump(mode="json")["tasks"]["last-hit"],
            suite_id="pc-moba",
            test_id="last-hit",
            reason="lane_identity",
        )


@pytest.mark.parametrize("attack_mode", ["melee", "ranged"])
def test_current_acceptance_requires_75_opportunity_tower_lane(attack_mode: str) -> None:
    task = get_suite("pc-moba", catalog_version="pc-moba-v5", attack_mode=attack_mode).model_dump(mode="json")["tasks"]["last-hit"]
    assert seed_module._frozen_task(task, suite_id="pc-moba", test_id="last-hit", reason="lane_identity") == task
    assert task["params"]["wave_count"] == 15
    assert task["params"]["wave_interval_ms"] == 20000
    assert task["params"]["kind"] == "lane_last_hit_towers"
    task["params"]["tower_damage"] = 1
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="lane_identity"):
        seed_module._frozen_task(task, suite_id="pc-moba", test_id="last-hit", reason="lane_identity")


def test_verifier_rejects_complete_old_four_member_moba_snapshot_in_current_pc_run() -> None:
    rows = _valid_evidence("pc")
    session = next(row for row in rows["sessions"] if row.suite_id == "pc-moba")
    legacy = get_suite("pc-moba", catalog_version="pc-moba-v3").model_dump(mode="json")
    session.catalog_snapshot = legacy
    session.scoring_snapshot = deepcopy(legacy)
    session.report_rules_snapshot = deepcopy(legacy)
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="session_config_identity"):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


def test_verifier_rejects_stale_v2_raw_kind_behind_v3_identity() -> None:
    rows = _valid_evidence("touch")
    practice = next(row for row in rows["practices"] if row.suite_id == "mobile-moba")
    assert practice.config_snapshot["params"]["kind"] == "dodge_choice"
    practice.config_snapshot["params"]["kind"] = "dodge"

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="practice_config_identity"):
        validate_esports_assessments_e2e_evidence(mode="touch", **rows)


def test_verifier_rejects_registered_parameter_drift_behind_v3_identity() -> None:
    rows = _valid_evidence("capabilities-touch")
    config = rows["practices"][0].config_snapshot
    assert config["params"]["kind"] == "move_fire_v3"
    config["params"]["min_moving_ms"] = 99

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="practice_config_identity"):
        validate_esports_assessments_e2e_evidence(
            mode="capabilities-touch",
            **rows,
        )


@pytest.mark.parametrize(
    "field", ["catalog_version", "engine_version", "scoring_version", "report_version"]
)
def test_verifier_rejects_v5_task_version_drift(field: str) -> None:
    rows = _valid_evidence("capabilities-desktop")
    rows["practices"][0].config_snapshot[field] = "pc-fps-v3"

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="practice_config_identity"):
        validate_esports_assessments_e2e_evidence(mode="capabilities-desktop", **rows)


@pytest.mark.parametrize(
    ("test_id", "field", "value"),
    [
        ("gridshot", "target_count", 4),
        ("sixshot", "reference_hits", 300),
        ("spheretrack90", "kind", "tracking"),
    ],
)
def test_verifier_rejects_registered_v5_parameter_drift(
    test_id: str,
    field: str,
    value: object,
) -> None:
    rows = _valid_evidence("pc")
    attempt = next(row for row in rows["attempts"] if row.test_id == test_id)
    attempt.config_snapshot["params"][field] = value

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="attempt_config_identity"):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


def test_verifier_rejects_legacy_four_count_audit_for_three_item_pc_fps() -> None:
    rows = _valid_evidence("pc")
    completed = next(row for row in rows["audits"] if row.action.endswith("completed"))
    completed.event_metadata["result_count"] = 4

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="audit_identity"):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


def test_verifier_rejects_complete_legacy_snapshot_in_current_pc_run() -> None:
    rows = _valid_evidence("pc")
    legacy = get_suite("pc-fps", catalog_version="pc-fps-v3").model_dump(mode="json")
    session = rows["sessions"][0]
    session.catalog_snapshot = deepcopy(legacy)
    session.scoring_snapshot = deepcopy(legacy)
    session.report_rules_snapshot = deepcopy(legacy)

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="session_config_identity"):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


def test_verifier_rejects_complete_v4_snapshot_in_current_fixed_board_run() -> None:
    rows = _valid_evidence("pc")
    legacy = get_suite("pc-fps", catalog_version="pc-fps-v4").model_dump(mode="json")
    session = rows["sessions"][0]
    session.catalog_snapshot = deepcopy(legacy)
    session.scoring_snapshot = deepcopy(legacy)
    session.report_rules_snapshot = deepcopy(legacy)

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="session_config_identity"):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


def test_verifier_rejects_report_result_configuration_not_frozen_by_session() -> None:
    rows = _valid_evidence("pc")
    report = rows["reports"][0].report_snapshot
    report["results"][0]["config"] = deepcopy(report["results"][1]["config"])

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="report_config_identity"):
        validate_esports_assessments_e2e_evidence(mode="pc", **rows)


def test_verifier_requires_only_state_specific_operation_keys() -> None:
    recovery = _valid_evidence("recovery")
    recovery["attempts"][1].interrupt_key = None
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="operation_state"):
        validate_esports_assessments_e2e_evidence(mode="recovery", **recovery)

    capability = _valid_evidence("capabilities-touch")
    capability["practices"][0].interrupt_key = None
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="operation_state"):
        validate_esports_assessments_e2e_evidence(
            mode="capabilities-touch",
            **capability,
        )

    submitted_attempt = _valid_evidence("pc")
    submitted_attempt["attempts"][0].interrupt_key = _uuid(80_001)
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="operation_state"):
        validate_esports_assessments_e2e_evidence(mode="pc", **submitted_attempt)

    submitted_practice = _valid_evidence("pc")
    submitted_practice["practices"][0].interrupt_key = _uuid(80_002)
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="operation_state"):
        validate_esports_assessments_e2e_evidence(mode="pc", **submitted_practice)

    interrupted_practice = _valid_evidence("capabilities-desktop")
    interrupted_practice["practices"][0].submission_key = _uuid(80_003)
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="operation_state"):
        validate_esports_assessments_e2e_evidence(
            mode="capabilities-desktop",
            **interrupted_practice,
        )

    abandoned_session = _valid_evidence("recovery")
    abandoned_session["sessions"][0].abandon_key = _uuid(80_004)
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="operation_state"):
        validate_esports_assessments_e2e_evidence(
            mode="recovery",
            **abandoned_session,
        )


def test_verifier_binds_each_audit_action_to_its_own_session() -> None:
    duplicated = _valid_evidence("pc")
    first_session_id = duplicated["sessions"][0].id
    first_session_events = [
        deepcopy(event)
        for event in duplicated["audits"]
        if event.resource_id == first_session_id
    ]
    for index, event in enumerate(first_session_events, start=1):
        event.id = _uuid(81_000 + index)
    duplicated["audits"] = [
        *[event for event in duplicated["audits"] if event.resource_id == first_session_id],
        *first_session_events,
    ]

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="audit_event_shape"):
        validate_esports_assessments_e2e_evidence(mode="pc", **duplicated)


def test_verifier_rejects_extra_namespace_event_with_wrong_resource_type() -> None:
    rows = _valid_evidence("recovery")
    wrong_resource = deepcopy(rows["audits"][0])
    wrong_resource.id = _uuid(82_001)
    wrong_resource.resource_type = "wrong_esports_resource"
    rows["audits"].append(wrong_resource)

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="audit_event_shape"):
        validate_esports_assessments_e2e_evidence(mode="recovery", **rows)


def test_database_collector_reads_the_full_esports_audit_namespace() -> None:
    statements: list[Any] = []

    class EmptySession:
        def scalars(self, statement: Any) -> list[object]:
            statements.append(statement)
            return []

    summary = verify_esports_assessments_e2e_evidence(
        cast(Session, EmptySession()),
        "capabilities-webkit",
    )

    assert summary.audit_count == 0
    audit_where = str(statements[-1].whereclause)
    assert "audit_events.action LIKE" in audit_where
    assert "resource_type" not in audit_where


def test_verifier_rejects_audit_shape_or_metadata_expansion() -> None:
    missing = _valid_evidence("pc")
    missing["audits"].pop()
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="audit_event_shape"):
        validate_esports_assessments_e2e_evidence(mode="pc", **missing)

    expanded = _valid_evidence("pc")
    expanded["audits"][0].event_metadata["score"] = 100
    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="audit_metadata_keyset"):
        validate_esports_assessments_e2e_evidence(mode="pc", **expanded)


def test_main_rejects_unknown_verify_mode_before_opening_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(seed_module, "get_settings", settings)
    monkeypatch.setenv("ESPORTS_E2E_VERIFY_MODE", "caller-count=0")

    def forbidden_session() -> None:
        raise AssertionError("session opened")

    monkeypatch.setattr(seed_module, "_open_session", forbidden_session)

    with pytest.raises(EsportsAssessmentE2EEvidenceError, match="verify_mode"):
        seed_module.main()


def test_main_prints_only_mode_and_aggregate_counts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = EsportsAssessmentE2EEvidenceSummary(
        mode="pc",
        session_count=2,
        attempt_count=4,
        result_count=4,
        report_count=2,
        practice_count=2,
        audit_count=4,
    )

    class Context:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(seed_module, "get_settings", settings)
    monkeypatch.setattr(seed_module, "_open_session", Context)
    monkeypatch.setattr(
        seed_module,
        "verify_esports_assessments_e2e_evidence",
        lambda db, mode: summary,
    )
    monkeypatch.setenv("ESPORTS_E2E_VERIFY_MODE", "pc")

    assert seed_module.main() == 0

    output = capsys.readouterr().out.strip()
    assert output == (
        "Esports assessment E2E evidence verified: mode=pc sessions=2 "
        "attempts=4 results=4 reports=2 practices=2 audits=4"
    )
    assert str(E2E_SELF_STUDENT_PERSON_ID) not in output


def test_seed_main_prints_zero_aggregate_without_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    zero = EsportsAssessmentE2EEvidenceSummary(
        mode="capabilities-webkit",
        session_count=0,
        attempt_count=0,
        result_count=0,
        report_count=0,
        practice_count=0,
        audit_count=0,
    )

    class Context:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(seed_module, "get_settings", settings)
    monkeypatch.setattr(seed_module, "_open_session", Context)
    monkeypatch.setattr(
        seed_module,
        "seed_esports_assessments_e2e",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        seed_module,
        "verify_esports_assessments_e2e_evidence",
        lambda db, mode: zero,
    )
    monkeypatch.delenv("ESPORTS_E2E_VERIFY_MODE", raising=False)

    assert seed_module.main() == 0

    output = capsys.readouterr().out.strip()
    assert output == (
        "Esports assessment E2E fixtures rebuilt: mode=seed sessions=0 "
        "attempts=0 results=0 reports=0 practices=0 audits=0"
    )
    for identity in (
        E2E_SELF_STUDENT_ACCOUNT_ID,
        E2E_SELF_STUDENT_PERSON_ID,
        E2E_GUARDIAN_ACCOUNT_ID,
        E2E_COACH_ACCOUNT_ID,
    ):
        assert str(identity) not in output


def test_executable_boundary_redacts_unexpected_failure_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_sentinel = "private-student-id raw-payload sql-parameter"

    def fail() -> int:
        raise RuntimeError(private_sentinel)

    monkeypatch.setattr(seed_module, "main", fail)

    assert seed_module._run_executable() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Esports assessment E2E command failed safely.\n"
    assert private_sentinel not in captured.out
    assert private_sentinel not in captured.err


def test_module_startup_redacts_settings_initialization_failure() -> None:
    private_sentinel = "private-startup-database-input"
    api_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "app.cli.seed_esports_assessments_e2e"],
        cwd=api_root,
        env={
            "APP_ENV": "test",
            "DATABASE_URL": private_sentinel,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(api_root),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Esports assessment E2E command failed safely.\n"
    assert private_sentinel not in result.stderr
    assert "Traceback" not in result.stderr
