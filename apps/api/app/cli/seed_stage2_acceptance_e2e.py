from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from app.cli.seed_camp_surveys_e2e import (
    CampSurveysE2ESeedResult,
    seed_camp_surveys_e2e,
)
from app.cli.seed_system_evaluation_e2e import (
    SystemEvaluationE2ESeedResult,
    seed_system_evaluation_e2e,
)
from app.cli.seed_teaching_reports_e2e import (
    TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
    TeachingReportsE2ESeedResult,
    seed_teaching_reports_e2e,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def seed_stage2_acceptance_e2e(
    *,
    settings: Settings,
    occurred_at: datetime,
) -> tuple[
    SystemEvaluationE2ESeedResult,
    TeachingReportsE2ESeedResult,
    CampSurveysE2ESeedResult,
]:
    settings.validate_runtime_secrets()
    cipher = EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key"))
    lookup = LookupDigest(_secret_bytes(settings, "field_lookup_key"))
    passwords = PasswordService()

    with SessionLocal() as db:
        system_result = seed_system_evaluation_e2e(db, settings=settings)
    with SessionLocal() as db:
        report_result = seed_teaching_reports_e2e(
            db,
            settings=settings,
            cipher=cipher,
            lookup=lookup,
            passwords=passwords,
            occurred_at=occurred_at,
        )
    with SessionLocal() as db:
        survey_result = seed_camp_surveys_e2e(
            db,
            settings=settings,
            occurred_at=occurred_at,
            second_guardian_relationship_id=(
                TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID
            ),
            cipher=cipher,
            lookup=lookup,
            passwords=passwords,
        )
    return system_result, report_result, survey_result


def _public_payload(
    *,
    system_result: SystemEvaluationE2ESeedResult,
    report_result: TeachingReportsE2ESeedResult,
    survey_result: CampSurveysE2ESeedResult,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "modules": [
            "system_evaluation",
            "teaching_reports",
            "camp_surveys",
        ],
        "system_evaluation_enrollment_ids": [
            str(system_result.automatic_enrollment_id),
            str(system_result.manual_enrollment_id),
        ],
        "teaching_report_cohort_ids": [
            str(value) for value in report_result.cohort_ids
        ],
        "teaching_report_enrollment_ids": [
            str(value) for value in report_result.enrollment_ids
        ],
        "camp_survey_cycle_id": str(survey_result.cycle_id),
        "camp_survey_enrollment_id": str(survey_result.enrollment_id),
    }


def main() -> int:
    settings = get_settings()
    occurred_at = datetime.now(UTC)
    system_result, report_result, survey_result = seed_stage2_acceptance_e2e(
        settings=settings,
        occurred_at=occurred_at,
    )
    print(
        json.dumps(
            _public_payload(
                system_result=system_result,
                report_result=report_result,
                survey_result=survey_result,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
