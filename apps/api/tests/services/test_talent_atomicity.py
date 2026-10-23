from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.talent_tests.catalog import CURRENT_CATALOG_VERSION, TEST_IDS
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from tests.services.talent_helpers import cleanup_actor, reload_actor, student_actor, submission

NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)


@pytest.mark.parametrize("action", ["started", "abandoned", "completed"])
def test_audit_failure_rolls_back_business_on_fresh_connection(
    postgres_engine, monkeypatch, action
):
    from app.talent_tests.service import TalentTestService

    with Session(postgres_engine, expire_on_commit=False) as db:
        actor = student_actor(db)
        account_id, person_id = actor.account.id, actor.person.id
        svc = TalentTestService(db)
        current = None
        if action != "started":
            current, _ = svc.start(
                actor=actor,
                request_id=uuid4(),
                catalog_version=CURRENT_CATALOG_VERSION,
                now=NOW,
            )
        if action == "completed":
            for test_id in TEST_IDS[:-1]:
                svc.submit(actor=actor, session_id=current.id, value=submission(test_id), now=NOW)

    original_append = AuditService.append

    def fail_after_insert(self, **kwargs):
        result = original_append(self, **kwargs)
        if kwargs["action"] == f"talent_tests.{action}":
            raise RuntimeError("injected audit sink failure")
        return result

    try:
        monkeypatch.setattr(AuditService, "append", fail_after_insert)
        with Session(postgres_engine) as db:
            svc, actor = TalentTestService(db), reload_actor(db, account_id)
            with pytest.raises(RuntimeError, match="injected audit"):
                if action == "started":
                    svc.start(
                        actor=actor,
                        request_id=uuid4(),
                        catalog_version=CURRENT_CATALOG_VERSION,
                        now=NOW,
                    )
                elif action == "abandoned":
                    svc.abandon(actor=actor, session_id=current.id, request_id=uuid4(), now=NOW)
                else:
                    svc.submit(
                        actor=actor, session_id=current.id, value=submission(TEST_IDS[-1]), now=NOW
                    )
            assert db.scalar(select(1)) == 1
        with Session(postgres_engine) as fresh:
            rows = list(
                fresh.scalars(
                    select(TalentTestSession).where(TalentTestSession.person_id == person_id)
                )
            )
            assert len(rows) == (0 if action == "started" else 1)
            if rows:
                assert rows[0].status == "in_progress"
                assert rows[0].completed_at is None and rows[0].abandoned_at is None
                assert rows[0].abandon_request_id is None
            sessions = select(TalentTestSession.id).where(TalentTestSession.person_id == person_id)
            assert fresh.scalar(
                select(func.count())
                .select_from(TalentTestResult)
                .where(TalentTestResult.session_id.in_(sessions))
            ) == (7 if action == "completed" else 0)
            assert (
                fresh.scalar(
                    select(func.count())
                    .select_from(TalentTestReport)
                    .where(TalentTestReport.session_id.in_(sessions))
                )
                == 0
            )
            assert (
                fresh.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.actor_user_id == account_id,
                        AuditEvent.action == f"talent_tests.{action}",
                    )
                )
                == 0
            )
    finally:
        with Session(postgres_engine) as db:
            cleanup_actor(db, account_id)


def test_lost_response_after_commit_replays_without_second_completion(postgres_engine):
    from app.talent_tests.service import TalentTestService

    with Session(postgres_engine, expire_on_commit=False) as db:
        actor = student_actor(db)
        account_id = actor.account.id
        svc = TalentTestService(db)
        current, _ = svc.start(
            actor=actor,
            request_id=uuid4(),
            catalog_version=CURRENT_CATALOG_VERSION,
            now=NOW,
        )
        for test_id in TEST_IDS[:-1]:
            svc.submit(actor=actor, session_id=current.id, value=submission(test_id), now=NOW)
    value = submission(TEST_IDS[-1])

    def response_lost(_db):
        raise ConnectionError("response lost after durable commit")

    try:
        with Session(postgres_engine) as db:
            actor = reload_actor(db, account_id)
            event.listen(db, "after_commit", response_lost, once=True)
            with pytest.raises(ConnectionError, match="response lost"):
                TalentTestService(db).submit(
                    actor=actor, session_id=current.id, value=value, now=NOW
                )
        with Session(postgres_engine) as fresh:
            saved, created = TalentTestService(fresh).submit(
                actor=reload_actor(fresh, account_id), session_id=current.id, value=value, now=NOW
            )
            assert not created and saved.session.status == "completed"
            assert len(saved.session.results) == 8 and saved.session.report_id is not None
            assert (
                fresh.scalar(
                    select(func.count())
                    .select_from(TalentTestReport)
                    .where(TalentTestReport.session_id == current.id)
                )
                == 1
            )
            assert (
                fresh.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.resource_id == current.id,
                        AuditEvent.action == "talent_tests.completed",
                    )
                )
                == 1
            )
    finally:
        with Session(postgres_engine) as db:
            cleanup_actor(db, account_id)
