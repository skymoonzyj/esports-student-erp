"""Committed HTTP regressions for rental correction authorization and evidence."""

import base64
import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.asset_management.models import Asset, AssetAssignment
from app.asset_management.schemas import AssetAssignRequest, AssetCreate
from app.asset_management.service import AssetService
from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import AuthSession, UserAccount
from app.core.database import get_db
from app.core.time import business_today
from app.identity.models import Person, StudentProfile
from app.main import create_app
from app.rbac.models import UserRole
from tests.departure_helpers import committed_scene


@pytest.mark.parametrize("same_actor", [True, False])
@pytest.mark.parametrize("operation", ["correction", "return"])
def test_identity_locks_allow_legacy_assignment_foreign_keys_without_deadlock(
    postgres_engine, test_settings, same_actor, operation
):
    with (
        correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id),
        committed_scene(postgres_engine) as (setup, other, _subject, _stay),
    ):
        asset_locked, rental_waiting = Event(), Event()

        def after_sql(connection, _cursor, statement, _params, _ctx, _many):
            if (
                connection.info.get("legacy_assignment")
                and "FROM assets" in statement
                and "FOR UPDATE" in statement
            ):
                asset_locked.set()
                assert rental_waiting.wait(15), "rental never reached the contended asset"

        def before_sql(connection, _cursor, statement, _params, _ctx, _many):
            if (
                not connection.info.get("legacy_assignment")
                and "FROM assets" in statement
                and "FOR UPDATE" in statement
            ):
                rental_waiting.set()

        def assign():
            with Session(postgres_engine) as db:
                connection = db.connection()
                info = connection.info
                info["legacy_assignment"] = True
                try:
                    AssetService(db).assign(
                        asset_id,
                        AssetAssignRequest(
                            person_id=context.person.id,
                            assigned_at=datetime(2026, 1, 1, tzinfo=UTC),
                        ),
                        actor_user_id=context.account.id if same_actor else other.account.id,
                    )
                    db.commit()
                finally:
                    info.pop("legacy_assignment", None)

        event.listen(postgres_engine, "after_cursor_execute", after_sql)
        event.listen(postgres_engine, "before_cursor_execute", before_sql)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                old = pool.submit(assign)
                assert asset_locked.wait(15)
                if operation == "correction":
                    new = pool.submit(correction_request, client, asset_id)
                else:
                    new = pool.submit(
                        client.post,
                        f"/api/v1/assets/{asset_id}/rental-return",
                        json={"returned_on": "2026-01-02", "expected_rental_revision": 0},
                    )
                old.result(timeout=25)
                response = new.result(timeout=25)
            assert response.status_code == (200 if operation == "correction" else 409), (
                response.text
            )
            with Session(postgres_engine) as observer:
                assert observer.scalar(
                    select(AssetAssignment.id).where(AssetAssignment.asset_id == asset_id)
                )
                assert observer.get(Asset, asset_id).rental_returned_on is None
        finally:
            rental_waiting.set()
            event.remove(postgres_engine, "after_cursor_execute", after_sql)
            event.remove(postgres_engine, "before_cursor_execute", before_sql)
            setup.execute(delete(AssetAssignment).where(AssetAssignment.asset_id == asset_id))
            setup.execute(
                update(Asset)
                .where(Asset.id == asset_id)
                .values(updated_by_user_id=context.account.id)
            )
            setup.commit()


@contextmanager
def correction_http_scene(engine, settings):
    with committed_scene(engine) as (setup, context, _subject, _stay):
        session_token, csrf_token = uuid4().hex, uuid4().hex
        pepper = base64.urlsafe_b64decode(settings.session_token_pepper.get_secret_value())
        context.session.token_digest = hmac.new(
            pepper, session_token.encode(), hashlib.sha256
        ).hexdigest()
        context.session.csrf_digest = hmac.new(
            pepper, csrf_token.encode(), hashlib.sha256
        ).hexdigest()
        asset = AssetService(setup).create(
            AssetCreate(
                name="合成租约更正",
                ownership_type="rented",
                rental_started_on=date(2026, 1, 1),
                rental_expires_on=date(2027, 1, 31),
            ),
            actor_user_id=context.account.id,
        )
        asset_id = asset.id
        setup.commit()
        app = create_app(settings)

        def request_db():
            with Session(engine) as request_session:
                yield request_session

        app.dependency_overrides[get_db] = request_db
        try:
            with TestClient(app) as client:
                client.cookies.set("erp_session", session_token, path="/api")
                client.headers["X-CSRF-Token"] = csrf_token
                yield client, context, asset_id
        finally:
            setup.rollback()
            setup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            try:
                setup.execute(
                    delete(AuditEvent).where(AuditEvent.actor_user_id == context.account.id)
                )
            finally:
                setup.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            setup.execute(delete(Asset).where(Asset.created_by_user_id == context.account.id))
            setup.commit()


def correction_request(client, asset_id):
    return client.patch(
        f"/api/v1/assets/{asset_id}",
        json={"rental_expires_on": "2027-01-30", "expected_rental_revision": 0},
    )


def test_started_rental_cannot_be_unstarted_through_two_patches(postgres_engine, test_settings):
    with correction_http_scene(postgres_engine, test_settings) as (client, _context, asset_id):
        response = client.patch(
            f"/api/v1/assets/{asset_id}",
            json={
                "rental_started_on": (business_today() + timedelta(days=1)).isoformat(),
                "expected_rental_revision": 0,
            },
        )
        assert response.status_code == 409, response.text
        owned = client.patch(
            f"/api/v1/assets/{asset_id}",
            json={
                "ownership_type": "owned",
                "acquisition_method": "purchase",
                "acquisition_channel": "offline",
                "rental_started_on": None,
                "rental_expires_on": None,
                "expected_rental_revision": 0,
            },
        )
        assert owned.status_code == 409


def test_correction_persists_old_new_dates_and_revisions(postgres_engine, test_settings):
    with correction_http_scene(postgres_engine, test_settings) as (client, _context, asset_id):
        response = correction_request(client, asset_id)
        assert response.status_code == 200, response.text
        with Session(postgres_engine) as observer:
            audit = observer.scalar(
                select(AuditEvent).where(
                    AuditEvent.resource_id == asset_id, AuditEvent.action == "asset.update"
                )
            )
            assert audit is not None
            assert (
                audit.event_metadata["previous_rental_facts"]["rental_expires_on"] == "2027-01-31"
            )
            assert audit.event_metadata["previous_rental_facts"]["rental_revision"] == 0
            assert audit.event_metadata["rental_facts"]["rental_expires_on"] == "2027-01-30"
            assert audit.event_metadata["rental_facts"]["rental_revision"] == 1


def test_correction_audit_failure_rolls_back_committed_patch(
    postgres_engine, test_settings, monkeypatch
):
    with correction_http_scene(postgres_engine, test_settings) as (client, _context, asset_id):

        def fail(*args, **kwargs):
            raise RuntimeError("required correction audit unavailable")

        monkeypatch.setattr(AuditService, "append", fail)
        with pytest.raises(RuntimeError, match="required correction audit unavailable"):
            correction_request(client, asset_id)
        with Session(postgres_engine) as observer:
            asset = observer.get(Asset, asset_id)
            assert asset.rental_expires_on == date(2027, 1, 31)
            assert asset.rental_revision == 0


@pytest.mark.parametrize("change", ["permission", "session", "account", "readiness"])
def test_correction_refreshes_authority_after_initial_authentication(
    postgres_engine, test_settings, monkeypatch, change
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id):
        reached, release = Event(), Event()
        original = AssetService.update

        def pause_before_mutation(self, *args, **kwargs):
            reached.set()
            assert release.wait(15), "correction test did not release service boundary"
            return original(self, *args, **kwargs)

        monkeypatch.setattr(AssetService, "update", pause_before_mutation)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(correction_request, client, asset_id)
            try:
                assert reached.wait(15), "correction did not reach service"
                with Session(postgres_engine) as revoker:
                    if change == "permission":
                        revoker.execute(
                            delete(UserRole).where(UserRole.user_id == context.account.id)
                        )
                    elif change == "session":
                        revoker.execute(
                            update(AuthSession)
                            .where(AuthSession.id == context.session.id)
                            .values(revoked_at=datetime.now(UTC))
                        )
                    elif change == "account":
                        revoker.execute(
                            update(UserAccount)
                            .where(UserAccount.id == context.account.id)
                            .values(status="disabled")
                        )
                    else:
                        revoker.add(
                            StudentProfile(
                                person_id=context.person.id,
                                student_number=f"COR-{uuid4().hex[:12]}",
                            )
                        )
                        revoker.execute(
                            update(Person)
                            .where(Person.id == context.person.id)
                            .values(date_of_birth=date(2015, 1, 1))
                        )
                    revoker.commit()
            finally:
                release.set()
            response = pending.result(timeout=20)
        assert response.status_code == (403 if change == "permission" else 401), response.text
        with Session(postgres_engine) as observer:
            assert observer.get(Asset, asset_id).rental_revision == 0


def test_correction_waits_identity_before_asset_and_rechecks_revoked_grant(
    postgres_engine, test_settings
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id):
        reached_asset = Event()
        statements = []

        def observe(_connection, _cursor, statement, _parameters, _context, _many):
            if "FOR UPDATE" in statement or "FOR NO KEY UPDATE" in statement:
                statements.append(statement)
                if "FROM assets" in statement:
                    reached_asset.set()

        def observe_commit(_connection):
            statements.append("COMMIT")

        with Session(postgres_engine) as blocker:
            blocker.scalar(select(Asset).where(Asset.id == asset_id).with_for_update())
            event.listen(postgres_engine, "before_cursor_execute", observe)
            event.listen(postgres_engine, "commit", observe_commit)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(correction_request, client, asset_id)
                    try:
                        assert reached_asset.wait(15), "correction did not reach asset lock"
                        # Raw grant mutation intentionally does not take identity locks:
                        # the post-wait decision must not reuse the earlier allow result.
                        with Session(postgres_engine) as revoker:
                            revoker.execute(
                                delete(UserRole).where(UserRole.user_id == context.account.id)
                            )
                            revoker.commit()
                    finally:
                        blocker.rollback()
                    response = pending.result(timeout=20)
            finally:
                event.remove(postgres_engine, "before_cursor_execute", observe)
                event.remove(postgres_engine, "commit", observe_commit)
        assert response.status_code == 403, response.text
        first_asset = next(index for index, sql in enumerate(statements) if "FROM assets" in sql)
        last_commit = max(
            (index for index, sql in enumerate(statements[:first_asset]) if sql == "COMMIT"),
            default=-1,
        )
        held_identity_locks = statements[last_commit + 1 : first_asset]
        assert any("FROM people" in sql for sql in held_identity_locks)
        assert any("FROM user_accounts" in sql for sql in held_identity_locks)
        assert any("FROM auth_sessions" in sql for sql in held_identity_locks)
        with Session(postgres_engine) as observer:
            assert observer.get(Asset, asset_id).rental_revision == 0


@pytest.mark.parametrize("change", ["account", "person", "session", "grant_lock"])
def test_rental_identity_locks_still_block_authorization_mutations(
    postgres_engine, test_settings, change
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id):
        reached_asset = Event()

        def observe(_connection, _cursor, statement, _parameters, _context, _many):
            if "FROM assets" in statement and "FOR UPDATE" in statement:
                reached_asset.set()

        with Session(postgres_engine) as blocker:
            blocker.scalar(select(Asset).where(Asset.id == asset_id).with_for_update())
            event.listen(postgres_engine, "before_cursor_execute", observe)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(correction_request, client, asset_id)
                    try:
                        assert reached_asset.wait(15)
                        with Session(postgres_engine) as revoker:
                            revoker.execute(text("SET LOCAL lock_timeout = '200ms'"))
                            if change == "account":
                                statement = (
                                    update(UserAccount)
                                    .where(UserAccount.id == context.account.id)
                                    .values(status="disabled")
                                )
                            elif change == "person":
                                statement = (
                                    update(Person)
                                    .where(Person.id == context.person.id)
                                    .values(date_of_birth=date(2015, 1, 1))
                                )
                            elif change == "session":
                                statement = (
                                    update(AuthSession)
                                    .where(AuthSession.id == context.session.id)
                                    .values(revoked_at=datetime.now(UTC))
                                )
                            else:
                                # RBAC management serializes grant writes on this account.
                                statement = (
                                    select(UserAccount)
                                    .where(UserAccount.id == context.account.id)
                                    .with_for_update()
                                )
                            with pytest.raises(OperationalError) as raised:
                                revoker.execute(statement)
                            assert raised.value.orig.sqlstate == "55P03"
                            revoker.rollback()
                    finally:
                        blocker.rollback()
                    assert pending.result(timeout=20).status_code == 200
            finally:
                event.remove(postgres_engine, "before_cursor_execute", observe)


def test_versioned_unconfirmed_and_owned_source_corrections_remain_supported(
    postgres_engine, test_settings
):
    with correction_http_scene(postgres_engine, test_settings) as (client, _context, _asset_id):
        created = client.post("/api/v1/assets", json={"name": "历史待确认来源"})
        assert created.status_code == 201
        asset_id = created.json()["id"]
        owned = client.patch(
            f"/api/v1/assets/{asset_id}",
            json={
                "ownership_type": "owned",
                "acquisition_method": "purchase",
                "acquisition_channel": "online",
                "expected_rental_revision": 0,
            },
        )
        assert owned.status_code == 200, owned.text
        assert owned.json()["rental_revision"] == 1
        changed = client.patch(
            f"/api/v1/assets/{asset_id}",
            json={"acquisition_channel": "offline", "expected_rental_revision": 1},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["acquisition_channel"] == "offline"
        assert changed.json()["rental_revision"] == 2
        with Session(postgres_engine) as observer:
            audits = list(
                observer.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.resource_id == asset_id, AuditEvent.action == "asset.update")
                    .order_by(AuditEvent.created_at, AuditEvent.id)
                )
            )
            assert len(audits) == 2
            assert audits[0].event_metadata["previous_rental_facts"]["ownership_type"] is None
            assert audits[0].event_metadata["rental_facts"]["ownership_type"] == "owned"
            assert audits[0].event_metadata["rental_facts"]["acquisition_channel"] == "online"
            assert (
                audits[1].event_metadata["previous_rental_facts"]["acquisition_channel"] == "online"
            )
            assert audits[1].event_metadata["rental_facts"]["acquisition_channel"] == "offline"
        other = client.patch(
            f"/api/v1/assets/{asset_id}",
            json={
                "acquisition_method": "other",
                "acquisition_channel": None,
                "acquisition_note": "private@example.test",
                "expected_rental_revision": 2,
            },
        )
        assert other.status_code == 200, other.text
        with Session(postgres_engine) as observer:
            audit = observer.scalar(
                select(AuditEvent)
                .where(AuditEvent.resource_id == asset_id, AuditEvent.action == "asset.update")
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            )
            assert audit.event_metadata["acquisition_note_changed"] is True
            assert "private@example.test" not in str(audit.event_metadata)
