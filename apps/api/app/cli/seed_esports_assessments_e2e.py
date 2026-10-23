from __future__ import annotations

import base64
import os
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.cli.e2e_database_safety import (
    E2EDatabaseRefused,
    require_e2e_database_url,
)
from app.core.transactions import write_transaction
from app.esports_assessments.catalog import get_task
from app.esports_assessments.config_schemas import MEMBERS
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.identity.models import GuardianRelationship, StaffProfile
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import CoachAssignment, Cohort

if TYPE_CHECKING:
    from app.core.config import Settings

EvidenceMode = Literal[
    "pc",
    "touch",
    "recovery",
    "capabilities-desktop",
    "capabilities-touch",
    "capabilities-webkit",
]

_FIXTURE_CREATED_AT = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
_ACTIVE_FROM = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
_ACTIVE_UNTIL = datetime(2099, 1, 1, 0, 0, tzinfo=UTC)


class EsportsAssessmentE2ESeedRefused(RuntimeError):
    pass


class EsportsAssessmentE2EEvidenceError(RuntimeError):
    pass


def get_settings() -> Settings:
    from app.core.config import get_settings as load_settings

    return load_settings()


def seed_e2e(
    db: Session,
    *,
    settings: Settings,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> object:
    from app.cli.seed_e2e import seed_e2e as seed_base_e2e

    return seed_base_e2e(
        db,
        settings=settings,
        cipher=cipher,
        lookup=lookup,
        passwords=passwords,
    )


def _open_session() -> Session:
    from app.core.database import SessionLocal

    return SessionLocal()


def _preview_person_id() -> UUID:
    from app.cli.seed_e2e import E2E_ADMIN_PERSON_ID

    return E2E_ADMIN_PERSON_ID


@dataclass(frozen=True)
class EsportsAssessmentE2ESeedResult:
    student_account_id: UUID
    student_person_id: UUID
    guardian_account_id: UUID
    guardian_person_id: UUID
    assigned_coach_account_id: UUID
    assigned_coach_person_id: UUID
    unrelated_staff_account_id: UUID
    unrelated_staff_person_id: UUID
    ordinary_admin_account_id: UUID
    super_admin_account_id: UUID


@dataclass(frozen=True)
class EsportsAssessmentE2EEvidenceSummary:
    mode: str
    session_count: int
    attempt_count: int
    result_count: int
    report_count: int
    practice_count: int
    audit_count: int


@dataclass(frozen=True)
class _ExpectedEvidence:
    sessions: Counter[tuple[str, str]]
    attempts: Counter[tuple[str, str, str]]
    results: Counter[tuple[str, str]]
    reports: Counter[str]
    practices: Counter[tuple[str, str, str]]
    audits: Counter[str]


def _formal_rows(*suites: str) -> tuple[
    Counter[tuple[str, str]],
    Counter[tuple[str, str, str]],
    Counter[tuple[str, str]],
    Counter[str],
]:
    return (
        Counter((suite_id, "completed") for suite_id in suites),
        Counter(
            (suite_id, test_id, "submitted")
            for suite_id in suites
            for test_id in MEMBERS[suite_id]
        ),
        Counter(
            (suite_id, test_id)
            for suite_id in suites
            for test_id in MEMBERS[suite_id]
        ),
        Counter(suites),
    )


_PC_SESSIONS, _PC_ATTEMPTS, _PC_RESULTS, _PC_REPORTS = _formal_rows(
    "pc-fps", "pc-moba"
)
_TOUCH_SESSIONS, _TOUCH_ATTEMPTS, _TOUCH_RESULTS, _TOUCH_REPORTS = _formal_rows(
    "mobile-fps", "mobile-moba"
)
_EXPECTED: dict[EvidenceMode, _ExpectedEvidence] = {
    "pc": _ExpectedEvidence(
        sessions=_PC_SESSIONS,
        attempts=_PC_ATTEMPTS,
        results=_PC_RESULTS,
        reports=_PC_REPORTS,
        practices=Counter(
            {
                ("pc-fps", "precision", "submitted"): 1,
                ("pc-moba", "last-hit", "submitted"): 1,
            }
        ),
        audits=Counter(
            {
                "esports_assessments.started": 2,
                "esports_assessments.completed": 2,
            }
        ),
    ),
    "touch": _ExpectedEvidence(
        sessions=_TOUCH_SESSIONS,
        attempts=_TOUCH_ATTEMPTS,
        results=_TOUCH_RESULTS,
        reports=_TOUCH_REPORTS,
        practices=Counter(
            {
                ("mobile-fps", "precision", "submitted"): 1,
                ("mobile-moba", "dodge", "submitted"): 1,
            }
        ),
        audits=Counter(
            {
                "esports_assessments.started": 2,
                "esports_assessments.completed": 2,
            }
        ),
    ),
    "recovery": _ExpectedEvidence(
        sessions=Counter({("pc-fps", "in_progress"): 1}),
        attempts=Counter(
            {
                ("pc-fps", "precision", "submitted"): 1,
                ("pc-fps", "tracking", "interrupted"): 1,
            }
        ),
        results=Counter({("pc-fps", "precision"): 1}),
        reports=Counter(),
        practices=Counter({("pc-fps", "precision", "submitted"): 1}),
        audits=Counter({"esports_assessments.started": 1}),
    ),
    "capabilities-desktop": _ExpectedEvidence(
        sessions=Counter(),
        attempts=Counter(),
        results=Counter(),
        reports=Counter(),
        practices=Counter({("pc-fps", "precision", "interrupted"): 1}),
        audits=Counter(),
    ),
    "capabilities-touch": _ExpectedEvidence(
        sessions=Counter(),
        attempts=Counter(),
        results=Counter(),
        reports=Counter(),
        practices=Counter({("mobile-fps", "move-fire", "interrupted"): 1}),
        audits=Counter(),
    ),
    "capabilities-webkit": _ExpectedEvidence(
        sessions=Counter(),
        attempts=Counter(),
        results=Counter(),
        reports=Counter(),
        practices=Counter(),
        audits=Counter(),
    ),
}

_AUDIT_KEYS = {
    "session_id",
    "person_id",
    "operation_key",
    "suite_id",
    "catalog_version",
    "scoring_version",
    "result_count",
}


def _require_esports_e2e_environment(settings: Settings) -> None:
    if settings.app_env != "test":
        raise EsportsAssessmentE2ESeedRefused(
            "Esports assessment E2E seed requires APP_ENV=test and an isolated database"
        )
    try:
        require_e2e_database_url(settings.database_url)
    except E2EDatabaseRefused as exc:
        raise EsportsAssessmentE2ESeedRefused(
            "Esports assessment E2E seed requires APP_ENV=test and an isolated database"
        ) from exc


def _replace_role(db: Session, account_id: UUID, role_key: str) -> None:
    db.execute(delete(UserRole).where(UserRole.user_id == account_id))
    db.add(UserRole(user_id=account_id, role_id=ROLES[role_key].id))


def _require_fixture[T](value: T | None, name: str) -> T:
    if value is None:
        raise RuntimeError(f"base E2E fixture is missing: {name}")
    return value


def seed_esports_assessments_e2e(
    db: Session,
    *,
    settings: Settings,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> EsportsAssessmentE2ESeedResult:
    _require_esports_e2e_environment(settings)
    seed_e2e(
        db,
        settings=settings,
        cipher=cipher,
        lookup=lookup,
        passwords=passwords,
    )

    from app.cli.seed_e2e import (
        E2E_ADMIN_ACCOUNT_ID,
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

    with write_transaction(db):
        relationship = _require_fixture(
            db.get(GuardianRelationship, E2E_COACH_FAMILY_RELATIONSHIP_ID),
            "esports guardian relationship",
        )
        relationship.guardian_person_id = E2E_GUARDIAN_PERSON_ID
        relationship.student_person_id = E2E_SELF_STUDENT_PERSON_ID
        relationship.relationship = "father"
        relationship.is_primary = True
        relationship.valid_from = date(2020, 1, 1)
        relationship.valid_until = None
        relationship.ended_at = None
        relationship.ended_by_user_id = None
        relationship.end_reason = None
        relationship.created_at = _FIXTURE_CREATED_AT
        relationship.updated_at = _FIXTURE_CREATED_AT

        target_cohort = _require_fixture(
            db.get(Cohort, E2E_LATER_COHORT_ID),
            "esports student cohort",
        )
        target_cohort.starts_at = _ACTIVE_FROM
        target_cohort.ends_at = _ACTIVE_UNTIL
        target_cohort.default_check_in_at = _ACTIVE_FROM
        target_cohort.default_check_out_at = _ACTIVE_UNTIL
        target_cohort.status = "active"

        assignments = list(
            db.scalars(
                select(CoachAssignment).where(
                    CoachAssignment.coach_person_id == E2E_COACH_PERSON_ID
                )
            )
        )
        if not assignments:
            raise RuntimeError("base E2E fixture is missing: assigned coach")
        for assignment in assignments:
            assignment.valid_from = _ACTIVE_FROM
            assignment.valid_until = _ACTIVE_UNTIL
            assignment.ended_at = None
            assignment.ended_by_user_id = None
            assignment.end_reason = None

        assigned_coach = _require_fixture(
            db.get(StaffProfile, E2E_COACH_PERSON_ID),
            "assigned coach profile",
        )
        assigned_coach.staff_type = "coach"
        unrelated_coach = _require_fixture(
            db.get(StaffProfile, E2E_LIFE_TEACHER_PERSON_ID),
            "unrelated coach profile",
        )
        unrelated_coach.staff_type = "coach"
        ordinary_admin_account = _require_fixture(
            db.get(UserAccount, E2E_CUSTOMER_SERVICE_ACCOUNT_ID),
            "ordinary admin account",
        )
        ordinary_admin_profile = _require_fixture(
            db.get(StaffProfile, ordinary_admin_account.person_id),
            "ordinary admin profile",
        )
        ordinary_admin_profile.staff_type = "administration"

        _replace_role(db, E2E_SELF_STUDENT_ACCOUNT_ID, "student")
        _replace_role(db, E2E_GUARDIAN_ACCOUNT_ID, "guardian")
        _replace_role(db, E2E_COACH_ACCOUNT_ID, "coach")
        _replace_role(db, E2E_LIFE_TEACHER_ACCOUNT_ID, "coach")
        _replace_role(db, E2E_CUSTOMER_SERVICE_ACCOUNT_ID, "admin")
        _replace_role(db, E2E_ADMIN_ACCOUNT_ID, "super_admin")
        db.flush()

    return EsportsAssessmentE2ESeedResult(
        student_account_id=E2E_SELF_STUDENT_ACCOUNT_ID,
        student_person_id=E2E_SELF_STUDENT_PERSON_ID,
        guardian_account_id=E2E_GUARDIAN_ACCOUNT_ID,
        guardian_person_id=E2E_GUARDIAN_PERSON_ID,
        assigned_coach_account_id=E2E_COACH_ACCOUNT_ID,
        assigned_coach_person_id=E2E_COACH_PERSON_ID,
        unrelated_staff_account_id=E2E_LIFE_TEACHER_ACCOUNT_ID,
        unrelated_staff_person_id=E2E_LIFE_TEACHER_PERSON_ID,
        ordinary_admin_account_id=E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
        super_admin_account_id=E2E_ADMIN_ACCOUNT_ID,
    )


def _unique_ids(rows: list[object], *, reason: str) -> None:
    values = [getattr(row, "id", None) for row in rows]
    if None in values or len(values) != len(set(values)):
        raise EsportsAssessmentE2EEvidenceError(reason)


def _operation_keys(
    sessions: list[object],
    attempts: list[object],
    results: list[object],
    practices: list[object],
) -> list[object]:
    keys: list[object] = []
    for row, fields in (
        *((row, ("creation_key", "abandon_key")) for row in sessions),
        *((row, ("creation_key", "interrupt_key")) for row in attempts),
        *((row, ("submission_key",)) for row in results),
        *(
            (row, ("creation_key", "submission_key", "interrupt_key"))
            for row in practices
        ),
    ):
        keys.extend(
            value
            for field in fields
            if (value := getattr(row, field, None)) is not None
        )
    return keys


def _validate_operation_state(
    sessions: list[object],
    attempts: list[object],
    results: list[object],
    practices: list[object],
) -> None:
    if any(
        getattr(row, "creation_key", None) is None
        or getattr(row, "abandon_key", None) is not None
        for row in sessions
    ):
        raise EsportsAssessmentE2EEvidenceError("operation_state")
    for row in attempts:
        state = getattr(row, "state", None)
        interrupt_key = getattr(row, "interrupt_key", None)
        if getattr(row, "creation_key", None) is None or (
            (state == "interrupted") != (interrupt_key is not None)
        ):
            raise EsportsAssessmentE2EEvidenceError("operation_state")
    if any(getattr(row, "submission_key", None) is None for row in results):
        raise EsportsAssessmentE2EEvidenceError("operation_state")
    for row in practices:
        state = getattr(row, "state", None)
        submission_key = getattr(row, "submission_key", None)
        interrupt_key = getattr(row, "interrupt_key", None)
        if (
            getattr(row, "creation_key", None) is None
            or (state == "submitted") != (submission_key is not None)
            or (state == "interrupted") != (interrupt_key is not None)
        ):
            raise EsportsAssessmentE2EEvidenceError("operation_state")


def _frozen_v3_task(
    value: object,
    *,
    suite_id: str,
    test_id: str,
    reason: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EsportsAssessmentE2EEvidenceError(reason)
    catalog_version = f"{suite_id}-v3"
    expected = get_task(
        suite_id,
        test_id,
        catalog_version=catalog_version,
    ).model_dump(mode="json")
    if value != expected:
        raise EsportsAssessmentE2EEvidenceError(reason)
    return value


def _frozen_v3_suite(
    value: object,
    *,
    suite_id: str,
    reason: str,
) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        raise EsportsAssessmentE2EEvidenceError(reason)
    members = MEMBERS[suite_id]
    tasks = value.get("tasks")
    if (
        value.get("suite_id") != suite_id
        or value.get("catalog_version") != f"{suite_id}-v3"
        or value.get("test_ids") not in (list(members), members)
        or not isinstance(tasks, dict)
        or set(tasks) != set(members)
    ):
        raise EsportsAssessmentE2EEvidenceError(reason)
    return {
        test_id: _frozen_v3_task(
            tasks[test_id],
            suite_id=suite_id,
            test_id=test_id,
            reason=reason,
        )
        for test_id in members
    }


def validate_esports_assessments_e2e_evidence(
    *,
    mode: str,
    sessions: Iterable[object],
    attempts: Iterable[object],
    results: Iterable[object],
    reports: Iterable[object],
    practices: Iterable[object],
    audits: Iterable[object],
) -> EsportsAssessmentE2EEvidenceSummary:
    if mode not in _EXPECTED:
        raise EsportsAssessmentE2EEvidenceError("verify_mode")
    expected = _EXPECTED[mode]
    session_rows = list(sessions)
    attempt_rows = list(attempts)
    result_rows = list(results)
    report_rows = list(reports)
    practice_rows = list(practices)
    audit_rows = list(audits)

    if any(
        getattr(row, "state", None) in {"running", "awaiting_submission"}
        for row in attempt_rows
    ):
        raise EsportsAssessmentE2EEvidenceError("pending_attempt")

    sessions_by_id = {getattr(row, "id", None): row for row in session_rows}
    if Counter(
        (getattr(row, "suite_id", None), getattr(row, "status", None))
        for row in session_rows
    ) != expected.sessions:
        raise EsportsAssessmentE2EEvidenceError("session_shape")
    _unique_ids(session_rows, reason="session_identity")
    preview_person_id = _preview_person_id()
    if any(getattr(row, "person_id", None) != preview_person_id for row in session_rows):
        raise EsportsAssessmentE2EEvidenceError("session_identity")
    frozen_tasks_by_session: dict[object, dict[str, dict[str, object]]] = {}
    for row in session_rows:
        suite_id = getattr(row, "suite_id", None)
        if not isinstance(suite_id, str):
            raise EsportsAssessmentE2EEvidenceError("session_config_identity")
        snapshot = getattr(row, "catalog_snapshot", None)
        frozen_tasks_by_session[getattr(row, "id", None)] = _frozen_v3_suite(
            snapshot,
            suite_id=suite_id,
            reason="session_config_identity",
        )
        if (
            getattr(row, "scoring_snapshot", None) != snapshot
            or getattr(row, "report_rules_snapshot", None) != snapshot
        ):
            raise EsportsAssessmentE2EEvidenceError("session_config_identity")

    def suite_for_session(row: object, *, reason: str) -> str:
        session = sessions_by_id.get(getattr(row, "session_id", None))
        if session is None:
            raise EsportsAssessmentE2EEvidenceError(reason)
        suite_id = getattr(session, "suite_id", None)
        if not isinstance(suite_id, str):
            raise EsportsAssessmentE2EEvidenceError(reason)
        return suite_id

    attempt_shape = Counter(
        (
            suite_for_session(row, reason="attempt_identity"),
            getattr(row, "test_id", None),
            getattr(row, "state", None),
        )
        for row in attempt_rows
    )
    if attempt_shape != expected.attempts:
        raise EsportsAssessmentE2EEvidenceError("attempt_shape")
    _unique_ids(attempt_rows, reason="attempt_identity")
    for row in attempt_rows:
        session_id = getattr(row, "session_id", None)
        test_id = getattr(row, "test_id", None)
        if not isinstance(test_id, str):
            raise EsportsAssessmentE2EEvidenceError("attempt_config_identity")
        expected_task = frozen_tasks_by_session.get(session_id, {}).get(test_id)
        if expected_task is None or getattr(row, "config_snapshot", None) != expected_task:
            raise EsportsAssessmentE2EEvidenceError("attempt_config_identity")
    attempts_by_id = {getattr(row, "id", None): row for row in attempt_rows}

    result_shape: Counter[tuple[str, object]] = Counter()
    for row in result_rows:
        attempt = attempts_by_id.get(getattr(row, "attempt_id", None))
        if (
            attempt is None
            or getattr(attempt, "state", None) != "submitted"
            or getattr(row, "session_id", None) != getattr(attempt, "session_id", None)
            or getattr(row, "test_id", None) != getattr(attempt, "test_id", None)
        ):
            raise EsportsAssessmentE2EEvidenceError("result_identity")
        result_shape[
            (
                suite_for_session(row, reason="result_identity"),
                getattr(row, "test_id", None),
            )
        ] += 1
    if result_shape != expected.results:
        raise EsportsAssessmentE2EEvidenceError("result_shape")
    _unique_ids(result_rows, reason="result_identity")

    report_shape = Counter(
        suite_for_session(row, reason="report_identity") for row in report_rows
    )
    if report_shape != expected.reports:
        raise EsportsAssessmentE2EEvidenceError("report_shape")
    _unique_ids(report_rows, reason="report_identity")
    for row in report_rows:
        session_id = getattr(row, "session_id", None)
        session = sessions_by_id.get(session_id)
        snapshot = getattr(row, "report_snapshot", None)
        if session is None or not isinstance(snapshot, dict):
            raise EsportsAssessmentE2EEvidenceError("report_config_identity")
        suite = getattr(session, "catalog_snapshot", None)
        report_results = snapshot.get("results")
        tasks = frozen_tasks_by_session.get(session_id, {})
        if (
            snapshot.get("suite") != suite
            or not isinstance(report_results, list)
            or len(report_results) != len(tasks)
            or any(
                not isinstance(result, dict)
                or result.get("test_id") not in tasks
                or result.get("config") != tasks[result["test_id"]]
                for result in report_results
            )
            or {result["test_id"] for result in report_results} != set(tasks)
        ):
            raise EsportsAssessmentE2EEvidenceError("report_config_identity")
    reports_by_session = {
        getattr(row, "session_id", None): row for row in report_rows
    }

    practice_shape = Counter(
        (
            getattr(row, "suite_id", None),
            getattr(row, "test_id", None),
            getattr(row, "state", None),
        )
        for row in practice_rows
    )
    if practice_shape != expected.practices:
        raise EsportsAssessmentE2EEvidenceError("practice_shape")
    _unique_ids(practice_rows, reason="practice_identity")
    if any(getattr(row, "person_id", None) != preview_person_id for row in practice_rows):
        raise EsportsAssessmentE2EEvidenceError("practice_identity")
    for row in practice_rows:
        suite_id = getattr(row, "suite_id", None)
        test_id = getattr(row, "test_id", None)
        if not isinstance(suite_id, str) or not isinstance(test_id, str):
            raise EsportsAssessmentE2EEvidenceError("practice_config_identity")
        _frozen_v3_task(
            getattr(row, "config_snapshot", None),
            suite_id=suite_id,
            test_id=test_id,
            reason="practice_config_identity",
        )

    _validate_operation_state(
        session_rows,
        attempt_rows,
        result_rows,
        practice_rows,
    )
    operation_keys = _operation_keys(
        session_rows,
        attempt_rows,
        result_rows,
        practice_rows,
    )
    if len(operation_keys) != len(set(operation_keys)):
        raise EsportsAssessmentE2EEvidenceError("operation_identity")

    expected_audit_shape: Counter[tuple[object, str]] = Counter()
    for row in session_rows:
        session_id = getattr(row, "id", None)
        expected_audit_shape[(session_id, "esports_assessments.started")] += 1
        if getattr(row, "status", None) == "completed":
            expected_audit_shape[(session_id, "esports_assessments.completed")] += 1
    audit_shape = Counter(
        (getattr(row, "resource_id", None), getattr(row, "action", None))
        for row in audit_rows
    )
    if audit_shape != expected_audit_shape:
        raise EsportsAssessmentE2EEvidenceError("audit_event_shape")
    _unique_ids(audit_rows, reason="audit_identity")
    result_submission_keys = {
        getattr(row, "id", None): {
            getattr(candidate, "submission_key", None)
            for candidate in result_rows
            if getattr(candidate, "session_id", None)
            == getattr(row, "id", None)
        }
        for row in session_rows
    }
    for row in audit_rows:
        session_id = getattr(row, "resource_id", None)
        session = sessions_by_id.get(session_id)
        action = getattr(row, "action", None)
        if (
            session is None
            or getattr(row, "resource_type", None)
            != "esports_assessment_session"
            or getattr(row, "outcome", None) != "allowed"
        ):
            raise EsportsAssessmentE2EEvidenceError("audit_identity")
        metadata = getattr(row, "event_metadata", None)
        expected_keys = (
            _AUDIT_KEYS | {"report_id"}
            if action == "esports_assessments.completed"
            else _AUDIT_KEYS
        )
        if not isinstance(metadata, dict) or set(metadata) != expected_keys:
            raise EsportsAssessmentE2EEvidenceError("audit_metadata_keyset")
        suite_id = getattr(session, "suite_id", None)
        if not isinstance(suite_id, str):
            raise EsportsAssessmentE2EEvidenceError("audit_identity")
        first_test_id = MEMBERS[suite_id][0]
        first_task = frozen_tasks_by_session.get(session_id, {}).get(first_test_id)
        if (
            metadata["session_id"] != str(session_id)
            or metadata["person_id"] != str(preview_person_id)
            or metadata["suite_id"] != suite_id
            or metadata["catalog_version"]
            != getattr(session, "catalog_snapshot", {}).get("catalog_version")
            or first_task is None
            or metadata["scoring_version"] != first_task["scoring_version"]
        ):
            raise EsportsAssessmentE2EEvidenceError("audit_identity")
        if action == "esports_assessments.started":
            if (
                metadata["operation_key"] != str(getattr(session, "creation_key", None))
                or metadata["result_count"] != 0
            ):
                raise EsportsAssessmentE2EEvidenceError("audit_identity")
        elif action == "esports_assessments.completed":
            report = reports_by_session.get(session_id)
            if (
                report is None
                or metadata["operation_key"]
                not in {
                    str(value) for value in result_submission_keys.get(session_id, set())
                }
                or metadata["result_count"] != 4
                or metadata["report_id"] != str(getattr(report, "id", None))
            ):
                raise EsportsAssessmentE2EEvidenceError("audit_identity")
        else:
            raise EsportsAssessmentE2EEvidenceError("audit_event_counts")

    return EsportsAssessmentE2EEvidenceSummary(
        mode=mode,
        session_count=len(session_rows),
        attempt_count=len(attempt_rows),
        result_count=len(result_rows),
        report_count=len(report_rows),
        practice_count=len(practice_rows),
        audit_count=len(audit_rows),
    )


def verify_esports_assessments_e2e_evidence(
    db: Session,
    mode: EvidenceMode,
) -> EsportsAssessmentE2EEvidenceSummary:
    return validate_esports_assessments_e2e_evidence(
        mode=mode,
        sessions=db.scalars(select(AssessmentSession)),
        attempts=db.scalars(select(AssessmentAttempt)),
        results=db.scalars(select(AssessmentResult)),
        reports=db.scalars(select(AssessmentReport)),
        practices=db.scalars(select(AssessmentPracticeRun)),
        audits=db.scalars(
            select(AuditEvent).where(
                AuditEvent.action.like("esports_assessments.%"),
            )
        ),
    )


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def _print_summary(prefix: str, summary: EsportsAssessmentE2EEvidenceSummary) -> None:
    print(
        f"{prefix}: mode={summary.mode} sessions={summary.session_count} "
        f"attempts={summary.attempt_count} results={summary.result_count} "
        f"reports={summary.report_count} practices={summary.practice_count} "
        f"audits={summary.audit_count}"
    )


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    _require_esports_e2e_environment(settings)
    requested_mode = os.environ.get("ESPORTS_E2E_VERIFY_MODE")
    if requested_mode is not None and requested_mode not in _EXPECTED:
        raise EsportsAssessmentE2EEvidenceError("verify_mode")
    with _open_session() as db:
        if requested_mode is not None:
            mode = requested_mode
            summary = verify_esports_assessments_e2e_evidence(db, mode)
            _print_summary("Esports assessment E2E evidence verified", summary)
            return 0
        seed_esports_assessments_e2e(
            db,
            settings=settings,
            cipher=EnvelopeCipher(
                _secret_bytes(settings, "field_encryption_master_key")
            ),
            lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
        )
        summary = verify_esports_assessments_e2e_evidence(
            db,
            "capabilities-webkit",
        )
    summary = EsportsAssessmentE2EEvidenceSummary(
        mode="seed",
        session_count=summary.session_count,
        attempt_count=summary.attempt_count,
        result_count=summary.result_count,
        report_count=summary.report_count,
        practice_count=summary.practice_count,
        audit_count=summary.audit_count,
    )
    _print_summary("Esports assessment E2E fixtures rebuilt", summary)
    return 0


def _run_executable() -> int:
    try:
        return main()
    except Exception:  # noqa: BLE001 - executable boundary must redact every failure
        print("Esports assessment E2E command failed safely.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_run_executable())
