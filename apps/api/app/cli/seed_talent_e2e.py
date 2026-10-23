from __future__ import annotations

import base64
import os
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
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
    E2ESeedRefused,
    _require_e2e_environment,
    seed_e2e,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.identity.models import GuardianRelationship, StaffProfile
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.talent_tests.catalog import get_rules
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from app.talent_tests.schemas import SessionCreateRequest
from app.talent_tests.scoring import fingerprint
from app.teaching.models import CoachAssignment, Cohort

TALENT_E2E_GUARDIAN_RELATIONSHIP_ID = E2E_COACH_FAMILY_RELATIONSHIP_ID
TALENT_E2E_LEGACY_SESSION_ID = UUID("e2700000-0000-4000-8000-000000000001")
_TALENT_E2E_LEGACY_CREATION_KEY = UUID("e2700000-0000-4000-8000-000000000002")

_FIXTURE_CREATED_AT = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
_ACTIVE_FROM = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
_ACTIVE_UNTIL = datetime(2099, 1, 1, 0, 0, tzinfo=UTC)


class TalentE2ESeedRefused(RuntimeError):
    pass


class TalentE2EEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class TalentE2ESeedResult:
    student_account_id: UUID
    student_person_id: UUID
    guardian_account_id: UUID
    guardian_person_id: UUID
    assigned_coach_account_id: UUID
    assigned_coach_person_id: UUID
    unrelated_coach_account_id: UUID
    unrelated_coach_person_id: UUID
    ordinary_admin_account_id: UUID
    super_admin_account_id: UUID


@dataclass(frozen=True)
class TalentE2EEvidenceSummary:
    mode: Literal["dynamic", "full", "double-full"]
    result_count: int
    report_count: int
    audit_count: int


_DYNAMIC_RAW_KEYS = {
    "selected_level",
    "outcomes",
    "measured_display_ms",
    "font_size_px",
}
_DEVICE_KEYS = {"input_kind", "viewport_width", "viewport_height", "geometry"}
_GEOMETRY_KEYS = {"width", "height", "target_diameter"}
_AUDIT_METADATA_KEYS = {
    "session_id",
    "person_id",
    "catalog_version",
    "scoring_version",
    "operation_key",
    "result_count",
}
_AUDIT_COMPLETED_METADATA_KEYS = _AUDIT_METADATA_KEYS | {"report_id"}
_EXPECTED_AUDIT_COUNTS: dict[
    Literal["dynamic", "full", "double-full"], Counter[str]
] = {
    "dynamic": Counter(
        {"talent_tests.started": 1, "talent_tests.abandoned": 1}
    ),
    "full": Counter(
        {"talent_tests.started": 1, "talent_tests.completed": 1}
    ),
    "double-full": Counter(
        {
            "talent_tests.started": 3,
            "talent_tests.abandoned": 1,
            "talent_tests.completed": 2,
        }
    ),
}


def validate_talent_e2e_evidence_payload(
    raw_result: object,
    normalized_raw: object,
    device_snapshot: object,
) -> None:
    if not isinstance(raw_result, dict) or set(raw_result) != _DYNAMIC_RAW_KEYS:
        raise TalentE2EEvidenceError("dynamic_raw_keyset")
    if not isinstance(normalized_raw, dict) or set(normalized_raw) != _DYNAMIC_RAW_KEYS:
        raise TalentE2EEvidenceError("dynamic_normalized_raw_keyset")
    if not isinstance(device_snapshot, dict) or set(device_snapshot) != _DEVICE_KEYS:
        raise TalentE2EEvidenceError("dynamic_device_shape")
    geometry = device_snapshot.get("geometry")
    if not isinstance(geometry, dict) or set(geometry) != _GEOMETRY_KEYS:
        raise TalentE2EEvidenceError("dynamic_device_shape")


def validate_talent_e2e_audit_events(
    mode: Literal["dynamic", "full", "double-full"],
    events: Iterable[tuple[str, object]],
) -> None:
    event_rows = list(events)
    if Counter(action for action, _ in event_rows) != _EXPECTED_AUDIT_COUNTS[mode]:
        raise TalentE2EEvidenceError("audit_event_counts")
    for action, metadata in event_rows:
        expected_keys = (
            _AUDIT_COMPLETED_METADATA_KEYS
            if action == "talent_tests.completed"
            else _AUDIT_METADATA_KEYS
        )
        if not isinstance(metadata, dict) or set(metadata) != expected_keys:
            raise TalentE2EEvidenceError("audit_metadata_keyset")


def verify_talent_e2e_evidence(
    db: Session,
    mode: Literal["dynamic", "full", "double-full"],
) -> TalentE2EEvidenceSummary:
    expected_results, expected_reports, expected_dynamic = {
        "dynamic": (1, 0, 1),
        "full": (8, 1, 1),
        "double-full": (16, 2, 2),
    }[mode]
    result_count = db.scalar(select(func.count()).select_from(TalentTestResult)) or 0
    report_count = db.scalar(select(func.count()).select_from(TalentTestReport)) or 0
    if result_count != expected_results:
        raise TalentE2EEvidenceError("result_count")
    if report_count != expected_reports:
        raise TalentE2EEvidenceError("report_count")

    dynamic_rows = list(
        db.scalars(
            select(TalentTestResult).where(TalentTestResult.test_id == "dynamic-vision")
        )
    )
    if len(dynamic_rows) != expected_dynamic:
        raise TalentE2EEvidenceError("dynamic_result_count")

    audit_rows = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_type == "talent_test_session",
                AuditEvent.action.like("talent_tests.%"),
            )
        )
    )
    validate_talent_e2e_audit_events(
        mode,
        ((event.action, event.event_metadata) for event in audit_rows),
    )
    for row in dynamic_rows:
        normalized_raw = (
            row.normalized_result.get("raw")
            if isinstance(row.normalized_result, dict)
            else None
        )
        validate_talent_e2e_evidence_payload(
            row.raw_result,
            normalized_raw,
            row.device_snapshot,
        )

    return TalentE2EEvidenceSummary(
        mode=mode,
        result_count=result_count,
        report_count=report_count,
        audit_count=len(audit_rows),
    )


def _require_talent_e2e_environment(settings: Settings) -> None:
    try:
        _require_e2e_environment(settings)
    except E2ESeedRefused as exc:
        raise TalentE2ESeedRefused(
            "Talent E2E seed requires APP_ENV=test and an isolated test database"
        ) from exc


def _replace_role(db: Session, account_id: UUID, role_key: str) -> None:
    db.execute(delete(UserRole).where(UserRole.user_id == account_id))
    db.add(UserRole(user_id=account_id, role_id=ROLES[role_key].id))


def _require_fixture[T](value: T | None, name: str) -> T:
    if value is None:
        raise RuntimeError(f"base E2E fixture is missing: {name}")
    return value


def seed_talent_e2e(
    db: Session,
    *,
    settings: Settings,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
    include_legacy_v1: bool = False,
) -> TalentE2ESeedResult:
    _require_talent_e2e_environment(settings)

    # Rebuild the canonical E2E identities first. This keeps every talent run
    # isolated and reuses the single credential source instead of introducing
    # talent-only passwords or production seed data.
    seed_e2e(
        db,
        settings=settings,
        cipher=cipher,
        lookup=lookup,
        passwords=passwords,
    )

    with write_transaction(db):
        relationship = _require_fixture(
            db.get(GuardianRelationship, TALENT_E2E_GUARDIAN_RELATIONSHIP_ID),
            "talent guardian relationship",
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
            "talent student cohort",
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
        if include_legacy_v1:
            legacy_rules = get_rules("talent-training-v1")
            db.add(
                TalentTestSession(
                    id=TALENT_E2E_LEGACY_SESSION_ID,
                    person_id=E2E_SELF_STUDENT_PERSON_ID,
                    created_by_user_id=E2E_SELF_STUDENT_ACCOUNT_ID,
                    creation_key=_TALENT_E2E_LEGACY_CREATION_KEY,
                    creation_request_digest=fingerprint(
                        SessionCreateRequest(
                            request_id=_TALENT_E2E_LEGACY_CREATION_KEY,
                            catalog_version=legacy_rules.catalog_version,
                        )
                    ),
                    catalog_version=legacy_rules.catalog_version,
                    scoring_version=legacy_rules.scoring_version,
                    report_version=legacy_rules.report_version,
                    rules_snapshot=legacy_rules.model_dump(mode="json"),
                    status="in_progress",
                    created_at=_FIXTURE_CREATED_AT,
                    updated_at=_FIXTURE_CREATED_AT,
                    results=[],
                    report=None,
                )
            )
        db.flush()

    return TalentE2ESeedResult(
        student_account_id=E2E_SELF_STUDENT_ACCOUNT_ID,
        student_person_id=E2E_SELF_STUDENT_PERSON_ID,
        guardian_account_id=E2E_GUARDIAN_ACCOUNT_ID,
        guardian_person_id=E2E_GUARDIAN_PERSON_ID,
        assigned_coach_account_id=E2E_COACH_ACCOUNT_ID,
        assigned_coach_person_id=E2E_COACH_PERSON_ID,
        unrelated_coach_account_id=E2E_LIFE_TEACHER_ACCOUNT_ID,
        unrelated_coach_person_id=E2E_LIFE_TEACHER_PERSON_ID,
        ordinary_admin_account_id=E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
        super_admin_account_id=E2E_ADMIN_ACCOUNT_ID,
    )


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    _require_talent_e2e_environment(settings)
    evidence_mode = os.environ.get("TALENT_E2E_VERIFY_MODE")
    if evidence_mode:
        if evidence_mode not in {"dynamic", "full", "double-full"}:
            raise TalentE2EEvidenceError("verify_mode")
        mode: Literal["dynamic", "full", "double-full"]
        if evidence_mode == "dynamic":
            mode = "dynamic"
        elif evidence_mode == "full":
            mode = "full"
        else:
            mode = "double-full"
        with SessionLocal() as db:
            evidence = verify_talent_e2e_evidence(db, mode)
        print(
            "Talent E2E evidence verified: "
            f"mode={evidence.mode} results={evidence.result_count} "
            f"reports={evidence.report_count} audits={evidence.audit_count}"
        )
        return 0
    with SessionLocal() as db:
        result = seed_talent_e2e(
            db,
            settings=settings,
            cipher=EnvelopeCipher(
                _secret_bytes(settings, "field_encryption_master_key")
            ),
            lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
            include_legacy_v1=os.environ.get("TALENT_E2E_LEGACY_V1") == "1",
        )
    print(f"Talent E2E fixtures rebuilt: student={result.student_account_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
