"""Read-only verifier for the one fixed cold-resource dataset, not a suite mode."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.cli.seed_e2e import E2E_SELF_STUDENT_ACCOUNT_ID, E2E_SELF_STUDENT_PERSON_ID
from app.cli.seed_talent_e2e import TalentE2EEvidenceError, _require_talent_e2e_environment
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession


def verify_resource_evidence(db: Session) -> dict[str, int]:
    results = db.scalar(select(func.count()).select_from(TalentTestResult)) or 0
    reports = db.scalar(select(func.count()).select_from(TalentTestReport)) or 0
    if results != 0 or reports != 0:
        raise TalentE2EEvidenceError('resource_results_or_reports')
    sessions = list(db.scalars(select(TalentTestSession)))
    if len(sessions) != 1:
        raise TalentE2EEvidenceError('resource_session_count')
    session = sessions[0]
    if (session.status != 'in_progress' or session.catalog_version != 'talent-training-v3'
        or session.person_id != E2E_SELF_STUDENT_PERSON_ID
        or session.created_by_user_id != E2E_SELF_STUDENT_ACCOUNT_ID):
        raise TalentE2EEvidenceError('resource_session_shape')
    events = list(db.scalars(select(AuditEvent).where(
        AuditEvent.resource_type == 'talent_test_session', AuditEvent.action.like('talent_tests.%'))))
    if len(events) != 1 or events[0].action != 'talent_tests.started':
        raise TalentE2EEvidenceError('resource_audit_count')
    metadata = events[0].event_metadata
    if not isinstance(metadata, dict) or metadata.get('session_id') != str(session.id) or metadata.get('result_count') != 0:
        raise TalentE2EEvidenceError('resource_audit_shape')
    return {'results': 0, 'reports': 0, 'sessions': 1, 'started': 1}


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    _require_talent_e2e_environment(settings)
    with SessionLocal() as db:
        verify_resource_evidence(db)
    print('Talent resource evidence verified: results=0 reports=0 sessions=1 started=1')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
