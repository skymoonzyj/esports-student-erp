from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import Session

from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    Payment,
    PaymentRefund,
)
from app.audit.models import AuditEvent
from app.auth.dependencies import require_standard_account
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.identity.models import Person
from app.main import create_app
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.rbac.service import RbacService
from app.revenue_share.models import (
    RevenueShareLedgerEntry,
    RevenueSharePaymentStatus,
    RevenueShareReconcileQueue,
)
from app.teaching.models import Cohort, TeachingClass
from tests.db.test_housing_reporting_concurrency import concurrent_context

financial_context = concurrent_context
NOW = datetime(2026, 10, 27, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


@pytest.fixture
def money_client(financial_context, test_settings):
    context = financial_context
    with Session(context.engine, expire_on_commit=False) as db:
        account = db.get(UserAccount, context.actor_id)
        person = db.get(Person, account.person_id)
        # The shared housing fixture gives this synthetic actor the admin role,
        # which independently grants admissions money permissions. Isolate these
        # tests to the explicit grants below so deleting one really revokes it.
        db.execute(delete(UserRole).where(UserRole.user_id == context.actor_id))
        auth_session = AuthSession(
            user_id=account.id, token_digest=uuid4().hex, csrf_digest=uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        cohort = Cohort(
            name=f"财务并发营期-{uuid4().hex}", starts_at=NOW,
            ends_at=NOW + timedelta(days=10), default_check_in_at=NOW,
            default_check_out_at=NOW + timedelta(days=10), status="open",
            created_by_user_id=account.id,
        )
        db.add_all([auth_session, cohort])
        db.flush()
        classes = [TeachingClass(
            cohort_id=cohort.id, project_id=PROJECT_ID, name=name,
            class_type="standard", capacity=20, default_tuition_cents=10000,
            is_active=True, created_by_user_id=account.id,
        ) for name in ("允许班级", "禁止班级")]
        case = AdmissionCase(
            student_person_id=context.resident_id, status="ready",
            agreed_tuition_cents=10000, created_by_user_id=account.id,
        )
        payment = Payment(
            source_kind="manual", payer_person_id=context.resident_id,
            amount_cents=10000, currency="CNY", paid_at=NOW,
            manual_note="虚拟收款", created_by_user_id=account.id,
        )
        db.add_all([*classes, case, payment])
        db.flush()
        db.add(AdmissionCasePayment(
            admission_case_id=case.id, payment_id=payment.id,
            attached_by_user_id=account.id,
        ))
        for permission in ("payments.manage", "refunds.manage", "payments.read"):
            db.add(UserPermissionOverride(
                user_id=account.id, permission_id=PERMISSIONS[permission].id,
                effect="allow", scope_kind="global",
            ))
            db.add(UserPermissionOverride(
                user_id=account.id, permission_id=PERMISSIONS[permission].id,
                effect="deny", scope_kind="assigned_class", scope_id=classes[1].id,
            ))
        db.commit()
        auth = AuthContext(session=auth_session, account=account, person=person)
        ids = {"case": case.id, "payment": payment.id, "cohort": cohort.id, "session": auth_session.id,
               "allowed": classes[0].id, "denied": classes[1].id}

    app = create_app(test_settings)

    def fresh_db():
        with Session(context.engine) as db:
            yield db

    app.dependency_overrides[get_db] = fresh_db
    app.dependency_overrides[require_standard_account] = lambda: auth
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, context, ids
    finally:
        with Session(context.engine) as db, db.begin():
            payments = select(Payment.id).where(Payment.created_by_user_id == context.actor_id)
            for model in (RevenueShareReconcileQueue, RevenueShareLedgerEntry,
                          RevenueSharePaymentStatus, PaymentRefund):
                db.execute(delete(model).where(model.payment_id.in_(payments)))
            db.execute(delete(AdmissionCasePayment).where(AdmissionCasePayment.admission_case_id == ids["case"]))
            db.execute(delete(Payment).where(Payment.created_by_user_id == context.actor_id))
            case = db.get(AdmissionCase, ids["case"])
            case.current_enrollment_id = None
            case.status = "ready"
            db.flush()
            db.execute(delete(Enrollment).where(Enrollment.admission_case_id == ids["case"]))
            db.execute(delete(AdmissionCase).where(AdmissionCase.id == ids["case"]))
            db.execute(delete(TeachingClass).where(TeachingClass.cohort_id == ids["cohort"]))
            db.execute(delete(Cohort).where(Cohort.id == ids["cohort"]))


def _enroll(db, context, ids):
    enrollment = Enrollment(
        admission_case_id=ids["case"], student_person_id=context.resident_id,
        cohort_id=ids["cohort"], class_id=ids["allowed"], agreed_tuition_cents=10000,
        status="active", confirmed_at=NOW, created_by_user_id=context.actor_id,
    )
    db.add(enrollment)
    db.flush()
    case = db.get(AdmissionCase, ids["case"])
    case.status = "enrolled"
    case.current_enrollment_id = enrollment.id
    db.flush()
    return enrollment.id


def _submit(client, ids, operation, operation_key):
    if operation == "payment":
        return client.post("/api/v1/payments", json={
            "idempotency_key": str(operation_key), "admission_case_id": str(ids["case"]),
            "amount": "10.00", "paid_at": NOW.isoformat(), "note": "虚拟并发收款",
        })
    return client.post(f"/api/v1/payments/{ids['payment']}/refunds", json={
        "idempotency_key": str(operation_key), "amount": "10.00",
        "refunded_at": NOW.isoformat(), "reason": "虚拟并发退款",
    })


def _wait_blocked(engine, waiter, blocker):
    with Session(engine) as db:
        deadline = monotonic() + 5
        while monotonic() < deadline:
            if blocker in db.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter}):
                return
            sleep(0.01)
    pytest.fail("request did not wait on the independent transaction")


def _money_snapshot(context):
    with Session(context.engine) as db:
        payments = select(Payment.id).where(Payment.created_by_user_id == context.actor_id)
        models = (Payment, PaymentRefund, RevenueShareLedgerEntry,
                  RevenueSharePaymentStatus, RevenueShareReconcileQueue, AuditEvent)
        result = []
        for model in models:
            if model is Payment:
                predicate = model.created_by_user_id == context.actor_id
            elif model is AuditEvent:
                predicate = model.actor_user_id == context.actor_id
            else:
                predicate = model.payment_id.in_(payments)
            result.append(list(db.execute(select(model.__table__).where(predicate))))
        return result


@pytest.mark.parametrize("operation", ["payment", "refund"])
@pytest.mark.parametrize("change", ["class", "session", "account", "permission"])
def test_money_reauthorizes_class_scope_after_cohort_lock_wait(money_client, operation, change):
    """Stale authority must not permit money writes after a real cohort lock wait."""
    client, context, ids = money_client
    with Session(context.engine) as db:
        enrollment_id = _enroll(db, context, ids)
        db.commit()
    before = _money_snapshot(context)
    attempted = Event()
    request_pids = []

    def observe(connection, _cursor, statement, _params, _execution, _many):
        if "FROM cohorts" in statement and "FOR UPDATE" in statement:
            request_pids.append(connection.connection.driver_connection.info.backend_pid)
            attempted.set()

    with Session(context.engine) as mover:
        mover_pid = mover.scalar(text("SELECT pg_backend_pid()"))
        mover.scalar(select(Cohort).where(Cohort.id == ids["cohort"]).with_for_update())
        event.listen(context.engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_submit, client, ids, operation, uuid4())
                try:
                    assert attempted.wait(5)
                    _wait_blocked(context.engine, request_pids[0], mover_pid)
                    if change == "class":
                        enrollment = mover.scalar(select(Enrollment).where(
                            Enrollment.id == enrollment_id).with_for_update())
                        mover.scalar(select(AdmissionCase).where(
                            AdmissionCase.id == ids["case"]).with_for_update())
                        enrollment.class_id = ids["denied"]
                    elif change == "session":
                        mover.get(AuthSession, ids["session"]).revoked_at = datetime.now(UTC)
                    elif change == "account":
                        mover.get(UserAccount, context.actor_id).status = "disabled"
                    else:
                        permission = "payments.manage" if operation == "payment" else "refunds.manage"
                        mover.execute(delete(UserPermissionOverride).where(
                            UserPermissionOverride.user_id == context.actor_id,
                            UserPermissionOverride.permission_id == PERMISSIONS[permission].id,
                            UserPermissionOverride.effect == "allow",
                        ))
                        tokens = RbacService(mover).enrollment_resource_tokens(
                            actor_person_id=mover.get(UserAccount, context.actor_id).person_id,
                            student_person_id=context.resident_id,
                            class_id=ids["allowed"],
                        )
                        assert not RbacService(mover).decide(
                            user_id=context.actor_id, permission=permission, resource_tokens=tokens,
                        ).allowed
                    mover.commit()
                finally:
                    mover.rollback()
                response = future.result(timeout=10)
        finally:
            event.remove(context.engine, "before_cursor_execute", observe)
    expected = (401,) if change in ("session", "account") else (403, 404)
    assert response.status_code in expected, response.text
    assert _money_snapshot(context) == before


@pytest.mark.parametrize("operation", ["payment", "refund"])
def test_first_enrollment_during_case_wait_does_not_invert_financial_locks(money_client, operation):
    """A stale empty enrollment snapshot must not lead to case -> enrollment locks."""
    client, context, ids = money_client
    before = _money_snapshot(context)
    case_attempted, case_acquired, resume_request = Event(), Event(), Event()
    request_pids = []
    contender_attempted = Event()
    contender_pids = []

    def before_sql(connection, _cursor, statement, _params, _execution, _many):
        if "FROM admission_cases" not in statement or "FOR UPDATE" not in statement:
            return
        pid = connection.connection.driver_connection.info.backend_pid
        if not request_pids:
            request_pids.append(pid)
            case_attempted.set()
        elif pid != request_pids[0]:
            contender_pids.append(pid)
            contender_attempted.set()

    def after_sql(connection, _cursor, statement, _params, _execution, _many):
        if ("FROM admission_cases" in statement and "FOR UPDATE" in statement
                and connection.connection.driver_connection.info.backend_pid == request_pids[0]):
            case_acquired.set()
            assert resume_request.wait(10)

    def contend(enrollment_id):
        with Session(context.engine) as db, db.begin():
            db.scalar(select(Cohort).where(Cohort.id == ids["cohort"]).with_for_update())
            db.scalar(select(Enrollment).where(Enrollment.id == enrollment_id).with_for_update())
            db.scalar(select(AdmissionCase).where(AdmissionCase.id == ids["case"]).with_for_update())

    operation_key = uuid4()
    with Session(context.engine) as creator:
        creator_pid = creator.scalar(text("SELECT pg_backend_pid()"))
        creator.scalar(select(Cohort).where(Cohort.id == ids["cohort"]).with_for_update())
        creator.scalar(select(AdmissionCase).where(AdmissionCase.id == ids["case"]).with_for_update())
        event.listen(context.engine, "before_cursor_execute", before_sql)
        event.listen(context.engine, "after_cursor_execute", after_sql)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(_submit, client, ids, operation, operation_key)
                try:
                    assert case_attempted.wait(5)
                    _wait_blocked(context.engine, request_pids[0], creator_pid)
                    enrollment_id = _enroll(creator, context, ids)
                    creator.commit()
                    assert case_acquired.wait(5)
                    competing = pool.submit(contend, enrollment_id)
                    assert contender_attempted.wait(5)
                    _wait_blocked(context.engine, contender_pids[0], request_pids[0])
                finally:
                    creator.rollback()
                    resume_request.set()
                response = future.result(timeout=10)
                competing.result(timeout=10)
        finally:
            event.remove(context.engine, "before_cursor_execute", before_sql)
            event.remove(context.engine, "after_cursor_execute", after_sql)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "enrollment_changed"
    assert _money_snapshot(context) == before
    # The conflicting transaction has ended; retry the exact same operation safely.
    retry = _submit(client, ids, operation, operation_key)
    assert retry.status_code == 201, retry.text
    repeated = _submit(client, ids, operation, operation_key)
    assert repeated.status_code == 201, repeated.text
    assert retry.json()["id"] == repeated.json()["id"]
