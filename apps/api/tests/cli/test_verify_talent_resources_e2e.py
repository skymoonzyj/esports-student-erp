from unittest.mock import Mock
from uuid import UUID

import pytest

from app.cli.seed_e2e import E2E_SELF_STUDENT_ACCOUNT_ID, E2E_SELF_STUDENT_PERSON_ID
from app.cli.verify_talent_resources_e2e import verify_resource_evidence
from app.talent_tests.models import TalentTestSession


def database():
    db = Mock()
    db.scalar.side_effect = [0, 0]
    session = TalentTestSession(id=UUID('e2700000-0000-4000-8000-000000000003'),
        person_id=E2E_SELF_STUDENT_PERSON_ID, created_by_user_id=E2E_SELF_STUDENT_ACCOUNT_ID,
        status='in_progress', catalog_version='talent-training-v3')
    event = Mock(action='talent_tests.started', event_metadata={'session_id': str(session.id), 'result_count': 0})
    db.scalars.side_effect = [[session], [event]]
    return db


def test_resource_evidence_accepts_exactly_one_started_session_without_results():
    assert verify_resource_evidence(database()) == {'results': 0, 'reports': 0, 'sessions': 1, 'started': 1}


@pytest.mark.parametrize('counts', [[1, 0], [0, 1], [1, 1]])
def test_resource_evidence_rejects_any_saved_result_or_report(counts):
    db = database()
    db.scalar.side_effect = counts
    with pytest.raises(RuntimeError, match='resource_results_or_reports'): verify_resource_evidence(db)


@pytest.mark.parametrize('kind', ['extra_session', 'no_session', 'wrong_owner', 'completed', 'old_rules', 'extra_audit', 'wrong_audit', 'wrong_session_audit', 'audit_results'])
def test_resource_evidence_rejects_wrong_session_or_audit(kind):
    db = database()
    rows, events = list(db.scalars.side_effect)
    if kind == 'extra_session': rows.append(rows[0])
    if kind == 'no_session': rows.clear()
    if kind == 'wrong_owner': rows[0].person_id = UUID(int=42)
    if kind == 'completed': rows[0].status = 'completed'
    if kind == 'old_rules': rows[0].catalog_version = 'talent-training-v1'
    if kind == 'extra_audit': events.append(events[0])
    if kind == 'wrong_audit': events[0].action = 'talent_tests.abandoned'
    if kind == 'wrong_session_audit': events[0].event_metadata['session_id'] = str(UUID(int=99))
    if kind == 'audit_results': events[0].event_metadata['result_count'] = 1
    db.scalars.side_effect = [rows, events]
    with pytest.raises(RuntimeError): verify_resource_evidence(db)
