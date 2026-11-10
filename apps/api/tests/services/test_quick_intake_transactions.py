from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.housing.models import HousingRoom, HousingStay
from app.identity.models import Person
from app.personnel_intake.quick_import_models import QuickIntakeReceipt
from app.personnel_intake.quick_import_schemas import CommitInput, PreviewInput
from app.personnel_intake.quick_import_service import QuickIntakeService
from tests.api.conftest import platform_users as _users
from tests.api.test_intake_role_intents import administration
from tests.api.test_quick_intake_housing import line
from tests.services.test_personnel_approval import approvals
from tests.services.test_profile_claims import claim_db as _claim_db


@pytest.fixture
def committed_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


def context(db, actor_id, session_id):
    account = db.get(UserAccount, actor_id)
    return AuthContext(account=account, person=db.get(Person, account.person_id), session=db.get(AuthSession, session_id))


def request():
    value = Request({"type": "http", "method": "POST", "path": "/api/v1/personnel-intake/quick-import/commit", "headers": []})
    value.state.request_id = str(uuid4())
    return value


def prepare(db):
    users = _users.__wrapped__(db)
    administration(db, users)
    uid = users["accounts"]["coach"].id
    session = AuthSession(user_id=uid, token_digest=uuid4().hex, csrf_digest=uuid4().hex,
                          expires_at=datetime.now(UTC) + timedelta(hours=1))
    room = db.scalar(select(HousingRoom).where(HousingRoom.room_type == "double").order_by(HousingRoom.id))
    room.verification_status, room.service_status, room.cleaning_status = "verified", "available", "ready"
    db.add(session)
    db.commit()
    sid = session.id
    service = QuickIntakeService(approvals(db))
    row = service.preview(context(db, uid, sid), PreviewInput(text=SecretStr(line(room)))).rows[0]
    assert row.ticket and not row.errors
    body = CommitInput(ticket=SecretStr(row.ticket), idempotency_key=uuid4())
    db.commit()
    return uid, sid, body


def domain_counts(db):
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Person, HousingStay, QuickIntakeReceipt))


def test_concurrent_same_operation_has_one_profile_stay_and_receipt(committed_db, postgres_engine):
    db = committed_db
    uid, sid, body = prepare(db)
    before = domain_counts(db)
    db.commit()
    barrier = Barrier(2)

    def run():
        with Session(postgres_engine) as worker:
            actor = context(worker, uid, sid)
            barrier.wait(timeout=10)
            return QuickIntakeService(approvals(worker)).commit(actor, body, request())

    with ThreadPoolExecutor(max_workers=2) as executor:
        one, two = list(executor.map(lambda _: run(), range(2)))
    assert one == two
    db.expire_all()
    assert domain_counts(db) == tuple(n + 1 for n in before)


@pytest.mark.parametrize("failures", [2, 3])
def test_retryable_database_failure_restarts_whole_row_without_orphans(committed_db, monkeypatch, failures):
    import app.personnel_intake.quick_import_service as module
    db = committed_db
    uid, sid, body = prepare(db)
    before = domain_counts(db)
    db.commit()
    original = module._audit
    attempts = 0

    def audit_then_database_failure(*args, **kwargs):
        nonlocal attempts
        original(*args, **kwargs)
        attempts += 1
        if attempts <= failures:
            # Actual PostgreSQL transaction failure after both person and stay writes.
            args[0].execute(text("DO $$ BEGIN RAISE EXCEPTION 'isolated retry probe' USING ERRCODE='40001'; END $$"))

    monkeypatch.setattr(module, "_audit", audit_then_database_failure)
    service = QuickIntakeService(approvals(db))
    if failures == 3:
        with pytest.raises(ProblemError) as error:
            service.commit(context(db, uid, sid), body, request())
        assert error.value.status == 503
        assert domain_counts(db) == before
    else:
        service.commit(context(db, uid, sid), body, request())
        assert domain_counts(db) == tuple(n + 1 for n in before)
    assert attempts == 3


def test_expired_new_ticket_rejected_but_successful_receipt_can_replay(committed_db, monkeypatch):
    import app.personnel_intake.quick_import_service as module
    db = committed_db
    uid, sid, body = prepare(db)
    service = QuickIntakeService(approvals(db))
    actor = context(db, uid, sid)
    successful = service.commit(actor, body, request())
    real_datetime = datetime

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(minutes=20)

    monkeypatch.setattr(module, "datetime", Later)
    assert service.commit(actor, body, request()) == successful
    with pytest.raises(ProblemError) as error:
        service.commit(actor, body.model_copy(update={"idempotency_key": uuid4()}), request())
    assert error.value.code == "quick_intake_expired"


def test_import_key_cannot_reuse_an_unrelated_manual_creation(committed_db):
    from datetime import date

    from app.personnel_intake.identity_selection import ManagedIdentityInput
    from app.personnel_intake.schemas import PendingIntakeInput
    from tests.api.conftest import make_cn_id

    db = committed_db
    uid, sid, body = prepare(db)
    actor = context(db, uid, sid)
    approvals(db).direct_create_pending(actor, PendingIntakeInput(
        full_name="测试导入甲", identity_number=SecretStr(make_cn_id("19900101", 721)),
        phone=SecretStr("13800000721"), sex="male", date_of_birth=date(1990, 1, 1), kind="staff",
    ), body.idempotency_key, identity_selection=ManagedIdentityInput(primary_identity="assistant_coach"))
    before = domain_counts(db)
    db.commit()
    with pytest.raises(ProblemError):
        QuickIntakeService(approvals(db)).commit(actor, body, request())
    assert domain_counts(db) == before
