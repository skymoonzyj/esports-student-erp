from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.dependencies import require_standard_account
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.housing import router as housing_router
from app.housing.dependencies import get_housing_cipher, get_housing_lookup
from app.housing.models import HousingRoom, HousingStay, HousingTransfer
from app.housing.seed_inventory import INITIAL_ROOMS
from app.identity.models import Person, StudentProfile
from app.main import create_app
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.security.encryption import LookupDigest
from tests.api.conftest import make_cn_id
from tests.api.test_housing import sign_in
from tests.api.test_housing_occupancy import verified_housing_emails  # noqa: F401
from tests.db.test_housing_reporting_concurrency import concurrent_context

security_context = concurrent_context


@pytest.fixture
def boundary_client(security_context, test_settings):
    """Committed disposable data and a separate session for each HTTP request."""
    context = security_context
    with Session(context.engine) as db:
        account = db.get(UserAccount, context.actor_id)
        person = db.get(Person, account.person_id)
        session = AuthSession(
            id=uuid4(), user_id=account.id, token_digest=uuid4().hex,
            csrf_digest=uuid4().hex, expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        room = HousingRoom(
            id=uuid4(), building_id=context.building_id, floor_id=context.floor_id,
            room_number="安全边界空房", normalized_key="安全边界空房", room_type="single",
            capacity=1, verification_status="pending_verification", service_status="available",
        )
        db.add_all([session, room])
        db.commit()
        auth = AuthContext(session=session, account=account, person=person)
        room_id = room.id
        _ = auth.account.id, auth.person.id, auth.session.id

    app = create_app(test_settings)

    def fresh_db():
        with Session(context.engine) as db:
            yield db

    app.dependency_overrides[get_db] = fresh_db
    # Initial authentication is intentionally frozen; production must recheck
    # live database facts at the protected write/download boundary.
    app.dependency_overrides[require_standard_account] = lambda: auth
    app.dependency_overrides[get_housing_cipher] = lambda: context.cipher
    app.dependency_overrides[get_housing_lookup] = lambda: LookupDigest(b"c" * 32)
    try:
        with TestClient(app) as client:
            yield client, context, room_id
    finally:
        # Buggy verification sets updated_by_user_id. Remove our extra empty
        # room before the shared context removes its actor account, even on RED.
        with Session(context.engine) as db:
            db.execute(delete(HousingTransfer).where(
                HousingTransfer.transferred_by_user_id == context.actor_id,
            ))
            db.execute(delete(HousingStay).where(HousingStay.room_id == room_id))
            db.execute(delete(HousingRoom).where(HousingRoom.id == room_id))
            db.commit()


def _deny(db, context, permission):
    db.add(UserPermissionOverride(
        user_id=context.actor_id, permission_id=PERMISSIONS[permission].id,
        effect="deny", scope_kind="floor", scope_id=context.floor_id,
    ))
    db.commit()


@pytest.mark.parametrize("operation", ["create", "transfer", "request_checkout"])
@pytest.mark.parametrize("authority_change", [
    "session_revoked", "account_disabled", "grant_expired", "unchanged",
])
def test_occupancy_mutation_rechecks_live_actor_after_real_room_lock_wait(
    boundary_client, operation, authority_change,
):
    # Removing the final actor check lets a revoked actor persist occupancy facts.
    client, context, target_room_id = boundary_client
    now = datetime.now(UTC)
    with Session(context.engine) as db:
        if authority_change == "grant_expired":
            db.execute(delete(UserRole).where(UserRole.user_id == context.actor_id))
            for permission in (
                "housing.read", "housing.stays.manage", "housing.transfers.manage",
                "housing.checkouts.manage",
            ):
                db.add(UserPermissionOverride(
                    user_id=context.actor_id, permission_id=PERMISSIONS[permission].id,
                    effect="allow", scope_kind="floor", scope_id=context.floor_id,
                    valid_from=now - timedelta(days=1), valid_until=now + timedelta(days=1),
                ))
        room = db.get(HousingRoom, target_room_id)
        room.verification_status = "verified"
        resident = db.get(Person, context.resident_id)
        # The reporting fixture's minor deliberately has no guardian account.
        # Use a complete adult resident here so authorization is the only gate.
        resident.date_of_birth = date(2000, 1, 2)
        resident.identity_ciphertext = context.cipher.encrypt(
            make_cn_id("20000102", 901), field_name="identity_number",
        )
        resident.email_ciphertext = context.cipher.encrypt(
            "resident@housing-boundary.example.test", field_name="email",
        )
        resident.email_digest = uuid4().hex
        db.get(StudentProfile, context.resident_id).status = "active"
        db.add(AccountLoginIdentifier(
            account_id=context.resident_account_id, kind="email",
            identifier_digest=uuid4().hex + uuid4().hex,
            verified_at=now, verification_source="test", is_primary=False,
        ))
        stay = db.scalar(select(HousingStay).where(HousingStay.person_id == context.resident_id))
        stay_id, source_room_id = stay.id, stay.room_id
        if operation == "create":
            stay.status = "checked_out"
            stay.actual_check_out_at = now - timedelta(days=1)
            stay.checked_out_by_user_id = context.actor_id
        db.commit()
        before_stays = db.execute(select(
            HousingStay.id, HousingStay.room_id, HousingStay.status, HousingStay.version,
            HousingStay.actual_check_out_at, HousingStay.checkout_requested_actual_at,
        ).where(HousingStay.person_id == context.resident_id).order_by(HousingStay.id)).all()
        before_rooms = db.execute(select(
            HousingRoom.id, HousingRoom.version, HousingRoom.cleaning_status,
        ).where(HousingRoom.id.in_([source_room_id, target_room_id])).order_by(HousingRoom.id)).all()

    lock_room_id = target_room_id if operation == "create" else source_room_id
    lock_attempted = Event()
    request_pids = []

    def observe(connection, _cursor, statement, _params, _execution, _many):
        if "FROM housing_rooms" in statement and "FOR UPDATE" in statement:
            request_pids.append(connection.connection.driver_connection.info.backend_pid)
            lock_attempted.set()

    def mutate():
        if operation == "create":
            return client.post(f"/api/v1/housing/rooms/{target_room_id}/stays", json={
                "person_id": str(context.resident_id), "stay_purpose": "permanent",
                "duration_mode": "long_term", "planned_check_in_at": now.isoformat(),
            })
        body = {"version": 1}
        if operation == "transfer":
            body.update(target_room_id=str(target_room_id), transferred_at=now.isoformat(),
                        reason="虚拟安全边界换房")
        else:
            body.update(actual_check_out_at=now.isoformat())
        return client.post(f"/api/v1/housing/stays/{stay_id}/{operation.replace('_', '-')}", json=body)

    with Session(context.engine) as blocker:
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        blocker.scalar(select(HousingRoom).where(HousingRoom.id == lock_room_id).with_for_update())
        event.listen(context.engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                request = pool.submit(mutate)
                try:
                    assert lock_attempted.wait(5), "request did not attempt the room lock"
                    with Session(context.engine) as revoker:
                        deadline = monotonic() + 2
                        while monotonic() < deadline:
                            blockers = revoker.scalar(text("SELECT pg_blocking_pids(:pid)"), {
                                "pid": request_pids[0],
                            })
                            if blocker_pid in blockers:
                                break
                            sleep(0.01)
                        else:
                            pytest.fail("request never waited on the independent room lock")
                        assert not request.done()
                        if authority_change == "session_revoked":
                            revoker.scalar(select(AuthSession).where(
                                AuthSession.user_id == context.actor_id,
                            )).revoked_at = datetime.now(UTC)
                        elif authority_change == "account_disabled":
                            revoker.get(UserAccount, context.actor_id).status = "disabled"
                        elif authority_change == "grant_expired":
                            # Expiry is after request authentication, but before the
                            # final locked check. A request-start RBAC clock is stale.
                            expired_at = datetime.now(UTC)
                            for grant in revoker.scalars(select(UserPermissionOverride).where(
                                UserPermissionOverride.user_id == context.actor_id,
                                UserPermissionOverride.permission_id != PERMISSIONS["housing.read"].id,
                            )):
                                grant.valid_until = expired_at
                        revoker.commit()
                finally:
                    blocker.rollback()
                response = request.result(timeout=10)
        finally:
            event.remove(context.engine, "before_cursor_execute", observe)

    if authority_change == "unchanged":
        assert response.status_code == (201 if operation == "create" else 200), response.text
        return
    assert response.status_code == 403, response.text
    assert response.headers["cache-control"] == "no-store, private"
    with Session(context.engine) as db:
        assert db.execute(select(
            HousingStay.id, HousingStay.room_id, HousingStay.status, HousingStay.version,
            HousingStay.actual_check_out_at, HousingStay.checkout_requested_actual_at,
        ).where(HousingStay.person_id == context.resident_id).order_by(HousingStay.id)).all() == before_stays
        assert db.execute(select(
            HousingRoom.id, HousingRoom.version, HousingRoom.cleaning_status,
        ).where(HousingRoom.id.in_([source_room_id, target_room_id])).order_by(HousingRoom.id)).all() == before_rooms
        assert db.scalar(select(HousingTransfer.id).where(
            HousingTransfer.transferred_by_user_id == context.actor_id,
        )) is None
        assert db.scalar(select(AuditEvent.id).where(
            AuditEvent.actor_user_id == context.actor_id,
            AuditEvent.action == f"housing.stay.{operation}", AuditEvent.outcome == "allowed",
        )) is None


@pytest.mark.parametrize("operation", ["verify", "batch_verify", "batch_service_status", "delete"])
@pytest.mark.parametrize("revoked_permission,status", [
    ("housing.rooms.manage", 403), ("housing.read", 404),
])
def test_room_mutation_rechecks_committed_revocation_after_real_lock_wait(
    boundary_client, operation, revoked_permission, status,
):
    # A frozen precheck would incorrectly mutate/delete the room and audit allow.
    client, context, room_id = boundary_client
    lock_attempted = Event()
    request_pids = []

    def observe(connection, _cursor, statement, _params, _execution, _many):
        if "FROM housing_rooms" in statement and "FOR UPDATE" in statement:
            request_pids.append(connection.connection.driver_connection.info.backend_pid)
            lock_attempted.set()

    def mutate():
        if operation == "delete":
            return client.delete(f"/api/v1/housing/rooms/{room_id}")
        if operation == "batch_service_status":
            return client.post("/api/v1/housing/rooms/batch-service-status", json={
                "rooms": [{"room_id": str(room_id), "version": 1}],
                "service_status": "maintenance",
            })
        if operation == "batch_verify":
            return client.post("/api/v1/housing/rooms/batch-verify", json={
                "rooms": [{"room_id": str(room_id), "version": 1}],
            })
        return client.post(f"/api/v1/housing/rooms/{room_id}/verify", json={"version": 1})

    with Session(context.engine) as blocker:
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        blocker.scalar(select(HousingRoom).where(HousingRoom.id == room_id).with_for_update())
        event.listen(context.engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                request = pool.submit(mutate)
                try:
                    assert lock_attempted.wait(5), "request did not pass precheck and attempt lock"
                    with Session(context.engine) as revoker:
                        deadline = monotonic() + 2
                        blocked = False
                        while monotonic() < deadline:
                            blockers = revoker.scalar(text("SELECT pg_blocking_pids(:pid)"), {
                                "pid": request_pids[0],
                            })
                            if blocker_pid in blockers:
                                blocked = True
                                break
                            sleep(0.01)
                        assert blocked, "request never waited on the independent room lock"
                        assert not request.done()
                        _deny(revoker, context, revoked_permission)
                finally:
                    blocker.rollback()
                response = request.result(timeout=10)
        finally:
            event.remove(context.engine, "before_cursor_execute", observe)

    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store, private"
    with Session(context.engine) as db:
        room = db.get(HousingRoom, room_id)
        assert room is not None
        assert (room.verification_status, room.version) == ("pending_verification", 1)
        assert room.service_status == "available"
        action = "housing.room.verify_batch" if operation == "batch_verify" else f"housing.room.{operation}"
        if operation == "batch_service_status":
            action = "housing.room.service_status_batch.maintenance"
        assert db.scalar(select(AuditEvent.id).where(
            AuditEvent.actor_user_id == context.actor_id,
            AuditEvent.action == action, AuditEvent.outcome == "allowed",
        )) is None


def _legacy_export(client, context):
    return client.post("/api/v1/housing/exports", json={
        "format": "csv", "purpose": "虚拟安全边界验证", "building_id": str(context.building_id),
    })


@pytest.mark.parametrize("change,status,code", [
    ("permission", 409, "housing_access_changed"), ("disabled", 401, "invalid_session"),
])
def test_legacy_export_discards_real_document_when_authority_changes_during_render(
    boundary_client, monkeypatch, change, status, code,
):
    # Removing the post-render reauthorization would leak this real CSV and audit allow.
    client, context, _ = boundary_client
    original = housing_router.render_occupancy_export

    def render_then_revoke(*args, **kwargs):
        document = original(*args, **kwargs)
        with Session(context.engine) as revoker:
            if change == "permission":
                _deny(revoker, context, "housing.reports.export_sensitive")
            else:
                revoker.get(UserAccount, context.actor_id).status = "disabled"
                revoker.commit()
        return document

    monkeypatch.setattr(housing_router, "render_occupancy_export", render_then_revoke)
    response = _legacy_export(client, context)
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert "content-disposition" not in response.headers
    assert "修改前住户" not in response.text
    with Session(context.engine) as db:
        assert db.scalar(select(AuditEvent.id).where(
            AuditEvent.actor_user_id == context.actor_id,
            AuditEvent.action == "housing.occupancy.export", AuditEvent.outcome == "allowed",
        )) is None


def test_legacy_export_with_unchanged_authority_returns_document_and_allowed_audit(boundary_client):
    client, context, _ = boundary_client
    response = _legacy_export(client, context)
    assert response.status_code == 200, response.text
    assert "修改前住户" in response.content.decode("utf-8-sig")
    assert "attachment" in response.headers["content-disposition"]
    with Session(context.engine) as db:
        assert db.scalar(select(AuditEvent.id).where(
            AuditEvent.actor_user_id == context.actor_id,
            AuditEvent.action == "housing.occupancy.export", AuditEvent.outcome == "allowed",
        )) is not None


@pytest.mark.usefixtures("verified_housing_emails")
def test_only_pending_cleaning_room_is_not_a_person_housing_choice_until_ready(
    db, db_client, platform_users,
):
    # Missing cleaning eligibility makes a sole pending room advertise create_stay.
    room = db.get(HousingRoom, INITIAL_ROOMS[0].id)
    room.verification_status = "verified"
    room.cleaning_status = "pending"
    room.cleaning_pending_at = datetime.now(UTC)
    person_id = platform_users["people"]["self_student"].id
    db.get(StudentProfile, person_id).status = "active"
    db.commit()
    sign_in(db_client, platform_users, "admin")
    base = f"/api/v1/people/{person_id}/housing"
    summary = db_client.get(base)
    assert summary.status_code == 200, summary.text
    assert summary.json()["allowed_actions"]["create_stay"] is False
    assert db_client.get(base + "/check-in-options").status_code == 403

    room.cleaning_status = "ready"
    room.cleaning_pending_at = None
    db.commit()
    summary = db_client.get(base)
    assert summary.status_code == 200, summary.text
    assert summary.json()["allowed_actions"]["create_stay"] is True
    options = db_client.get(base + "/check-in-options")
    assert options.status_code == 200, options.text
    assert {item["id"] for item in options.json()["items"]} == {str(room.id)}


@pytest.mark.usefixtures("verified_housing_emails")
def test_person_housing_choices_include_ready_room_but_not_pending_sibling(
    db, db_client, platform_users,
):
    # Returning all available verified rooms would leak the pending sibling into the chooser.
    pending, ready = [db.get(HousingRoom, seed.id) for seed in INITIAL_ROOMS[:2]]
    for room in (pending, ready):
        room.verification_status = "verified"
    pending.cleaning_status = "pending"
    pending.cleaning_pending_at = datetime.now(UTC)
    person_id = platform_users["people"]["self_student"].id
    db.get(StudentProfile, person_id).status = "active"
    db.commit()
    sign_in(db_client, platform_users, "admin")
    response = db_client.get(f"/api/v1/people/{person_id}/housing/check-in-options")
    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {str(ready.id)}
