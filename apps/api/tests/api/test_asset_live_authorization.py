"""Synthetic HTTP regressions for asset write confidentiality and lock boundaries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from threading import Event

import pytest
from PIL import Image
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.asset_management import router as asset_router
from app.asset_management.models import Asset, AssetAssignment, AssetPhoto
from app.asset_management.service import AssetService
from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.rbac.models import UserRole
from tests.api.test_asset_rental_corrections import correction_http_scene
from tests.departure_helpers import deny


def mutation_request(client, asset_id, person_id, operation):
    path = f"/api/v1/assets/{asset_id}"
    if operation == "create":
        return client.post("/api/v1/assets", json={"name": "不应创建"})
    if operation == "update":
        return client.patch(path, json={"note": "不应写入"})
    if operation == "noop":
        return client.patch(path, json={"name": "合成租约更正"})
    if operation == "assign":
        return client.post(
            path + "/assign",
            json={
                "person_id": str(person_id),
                "assigned_at": "2026-01-01T00:00:00Z",
            },
        )
    if operation == "return":
        return client.post(path + "/return", json={"returned_at": "2026-01-02T00:00:00Z"})
    if operation == "rental-return":
        return client.post(
            path + "/rental-return",
            json={
                "returned_on": "2026-01-02",
                "expected_rental_revision": 0,
            },
        )
    if operation == "photo-delete":
        return client.delete(path + "/photo")
    photo = BytesIO()
    Image.new("RGB", (2, 2), color=(20, 40, 80)).save(photo, format="PNG")
    return client.put(path + "/photo", files={"file": ("test.png", photo.getvalue(), "image/png")})


def prepare_custody(client, asset_id, person_id, operation):
    if operation == "return":
        response = mutation_request(client, asset_id, person_id, "assign")
        assert response.status_code == 200, response.text


def remove_custody(engine, asset_id):
    with Session(engine) as db:
        db.execute(delete(AssetAssignment).where(AssetAssignment.asset_id == asset_id))
        db.commit()


@pytest.mark.parametrize(
    "operation",
    [
        "create",
        "noop",
        "assign",
        "return",
        "rental-return",
        "photo-upload",
        "photo-delete",
    ],
)
def test_read_denied_asset_mutations_do_not_return_details_or_write(
    postgres_engine,
    test_settings,
    operation,
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id):
        try:
            prepare_custody(client, asset_id, context.person.id, operation)
            with Session(postgres_engine) as db:
                asset = db.get(Asset, asset_id)
                asset.note = "PRIVATE-NOTE"
                asset.serial_number = "PRIVATE-SERIAL"
                deny(db, context, "assets.read", "global", None)
                db.commit()
                before_audits = list(
                    db.scalars(
                        select(AuditEvent.id).where(
                            AuditEvent.actor_user_id == context.account.id,
                        )
                    )
                )
                before_assets = list(
                    db.scalars(
                        select(Asset.id).where(
                            Asset.created_by_user_id == context.account.id,
                        )
                    )
                )
            assert client.get(f"/api/v1/assets/{asset_id}").status_code == 403
            response = mutation_request(client, asset_id, context.person.id, operation)
            assert response.status_code == 403, response.text
            assert "PRIVATE-NOTE" not in response.text
            assert "PRIVATE-SERIAL" not in response.text
            with Session(postgres_engine) as db:
                asset = db.get(Asset, asset_id)
                assert asset.note == "PRIVATE-NOTE"
                assert asset.rental_returned_on is None
                assert asset.photo is None
                assert asset.status == ("assigned" if operation == "return" else "available")
                assert set(
                    db.scalars(
                        select(Asset.id).where(
                            Asset.created_by_user_id == context.account.id,
                        )
                    )
                ) == set(before_assets)
                assert set(
                    db.scalars(
                        select(AuditEvent.id).where(
                            AuditEvent.actor_user_id == context.account.id,
                        )
                    )
                ) == set(before_audits)
        finally:
            remove_custody(postgres_engine, asset_id)


@pytest.mark.parametrize("change", ["session", "account", "grant"])
def test_asset_export_rechecks_authority_after_render_before_returning_bytes(
    postgres_engine,
    test_settings,
    monkeypatch,
    change,
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, _asset_id):
        render = asset_router.render_asset_export

        def render_then_revoke(*args, **kwargs):
            result = render(*args, **kwargs)
            with Session(postgres_engine) as db:
                if change == "session":
                    db.execute(
                        update(AuthSession)
                        .where(AuthSession.id == context.session.id)
                        .values(revoked_at=datetime.now(UTC))
                    )
                elif change == "account":
                    db.execute(
                        update(UserAccount)
                        .where(UserAccount.id == context.account.id)
                        .values(status="disabled")
                    )
                else:
                    db.execute(delete(UserRole).where(UserRole.user_id == context.account.id))
                db.commit()
            return result

        monkeypatch.setattr(asset_router, "render_asset_export", render_then_revoke)
        response = client.post(
            "/api/v1/assets/exports",
            json={
                "format": "csv",
                "purpose": "合成授权边界验证",
            },
        )
        assert response.status_code == (403 if change == "grant" else 401), response.text
        assert "attachment" not in response.headers.get("content-disposition", "")
        with Session(postgres_engine) as db:
            assert (
                db.scalar(
                    select(AuditEvent.id).where(
                        AuditEvent.actor_user_id == context.account.id,
                        AuditEvent.action == "asset.export",
                    )
                )
                is None
            )


@pytest.mark.parametrize("operation", ["update", "assign", "return"])
@pytest.mark.parametrize("change", ["session", "account", "grant"])
def test_ordinary_asset_write_rechecks_authority_before_asset_lock(
    postgres_engine,
    test_settings,
    monkeypatch,
    operation,
    change,
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id):
        try:
            prepare_custody(client, asset_id, context.person.id, operation)
            reached, release = Event(), Event()
            method = "return_asset" if operation == "return" else operation
            original = getattr(AssetService, method)

            def pause(self, *args, **kwargs):
                reached.set()
                assert release.wait(15), "mutation boundary was not released"
                return original(self, *args, **kwargs)

            monkeypatch.setattr(AssetService, method, pause)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    mutation_request, client, asset_id, context.person.id, operation
                )
                try:
                    assert reached.wait(15), "request did not reach service"
                    with Session(postgres_engine) as db:
                        if change == "session":
                            db.execute(
                                update(AuthSession)
                                .where(AuthSession.id == context.session.id)
                                .values(revoked_at=datetime.now(UTC))
                            )
                        elif change == "account":
                            db.execute(
                                update(UserAccount)
                                .where(UserAccount.id == context.account.id)
                                .values(status="disabled")
                            )
                        else:
                            db.execute(
                                delete(UserRole).where(UserRole.user_id == context.account.id)
                            )
                        db.commit()
                finally:
                    release.set()
                response = pending.result(timeout=20)
            assert response.status_code == (403 if change == "grant" else 401), response.text
            assert_no_mutation(postgres_engine, context.account.id, asset_id, operation)
        finally:
            remove_custody(postgres_engine, asset_id)


def assert_no_mutation(engine, actor_id, asset_id, operation):
    with Session(engine) as db:
        asset = db.get(Asset, asset_id)
        assert asset.note is None
        assert asset.status == ("assigned" if operation == "return" else "available")
        assignments = list(
            db.scalars(select(AssetAssignment).where(AssetAssignment.asset_id == asset_id))
        )
        assert len(assignments) == (1 if operation == "return" else 0)
        assert all(assignment.returned_at is None for assignment in assignments)
        assert (
            db.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.actor_user_id == actor_id,
                    AuditEvent.action
                    == {
                        "update": "asset.update",
                        "assign": "asset.assign",
                        "return": "asset.return",
                    }[operation],
                )
            )
            is None
        )


@pytest.mark.parametrize("operation", ["update", "assign", "return"])
@pytest.mark.parametrize("change", ["session", "account", "grant", "read"])
def test_ordinary_asset_wait_retains_identity_locks_and_rechecks_grants(
    postgres_engine,
    test_settings,
    operation,
    change,
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id):
        try:
            prepare_custody(client, asset_id, context.person.id, operation)
            reached = Event()

            def observe(_connection, _cursor, statement, _parameters, _context, _many):
                if "FROM assets" in statement and "FOR UPDATE" in statement:
                    reached.set()

            with Session(postgres_engine) as blocker:
                blocker.scalar(select(Asset).where(Asset.id == asset_id).with_for_update())
                event.listen(postgres_engine, "before_cursor_execute", observe)
                try:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pending = pool.submit(
                            mutation_request, client, asset_id, context.person.id, operation
                        )
                        try:
                            assert reached.wait(15), "request did not reach asset lock"
                            with Session(postgres_engine) as db:
                                db.execute(text("SET LOCAL lock_timeout = '200ms'"))
                                if change in {"grant", "read"}:
                                    # Direct grant removal can bypass account locking; never
                                    # reuse the earlier allow decision after the asset wait.
                                    if change == "read":
                                        deny(db, context, "assets.read", "global", None)
                                    else:
                                        db.execute(
                                            delete(UserRole).where(
                                                UserRole.user_id == context.account.id
                                            )
                                        )
                                    db.commit()
                                else:
                                    statement = (
                                        update(AuthSession)
                                        .where(AuthSession.id == context.session.id)
                                        .values(revoked_at=datetime.now(UTC))
                                        if change == "session"
                                        else update(UserAccount)
                                        .where(UserAccount.id == context.account.id)
                                        .values(status="disabled")
                                    )
                                    with pytest.raises(OperationalError) as raised:
                                        db.execute(statement)
                                    assert raised.value.orig.sqlstate == "55P03"
                                    db.rollback()
                        finally:
                            blocker.rollback()
                        response = pending.result(timeout=20)
                finally:
                    event.remove(postgres_engine, "before_cursor_execute", observe)
            assert response.status_code == (403 if change in {"grant", "read"} else 200), (
                response.text
            )
            if change in {"grant", "read"}:
                assert_no_mutation(postgres_engine, context.account.id, asset_id, operation)
        finally:
            remove_custody(postgres_engine, asset_id)


@pytest.mark.parametrize("change", ["session", "read", "manage", "unchanged"])
def test_photo_upload_rechecks_authority_after_file_processing(
    postgres_engine,
    test_settings,
    monkeypatch,
    change,
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id):
        processed, release = Event(), Event()
        validate = asset_router.validate_asset_photo

        def validate_then_pause(*args, **kwargs):
            validated = validate(*args, **kwargs)
            processed.set()
            assert release.wait(15), "photo processing boundary was not released"
            return validated

        monkeypatch.setattr(asset_router, "validate_asset_photo", validate_then_pause)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                mutation_request, client, asset_id, context.person.id, "photo-upload"
            )
            try:
                assert processed.wait(15), "upload did not process the photo"
                with Session(postgres_engine) as db:
                    if change == "session":
                        db.execute(
                            update(AuthSession)
                            .where(AuthSession.id == context.session.id)
                            .values(revoked_at=datetime.now(UTC))
                        )
                    elif change != "unchanged":
                        deny(db, context, f"assets.{change}", "global", None)
                    db.commit()
            finally:
                release.set()
            response = pending.result(timeout=20)
        expected_status = 200 if change == "unchanged" else (401 if change == "session" else 403)
        assert response.status_code == expected_status, response.text
        with Session(postgres_engine) as db:
            photo = db.scalar(select(AssetPhoto).where(AssetPhoto.asset_id == asset_id))
            audit = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.actor_user_id == context.account.id,
                    AuditEvent.action == "asset.photo.upload",
                )
            )
            assert (photo is not None) == (change == "unchanged")
            assert (audit is not None) == (change == "unchanged")
            if photo is not None:
                assert response.json()["photo"]["id"] == str(photo.id)
                assert photo.content.startswith(b"\x89PNG")


@pytest.mark.parametrize("change", ["session", "read", "manage", "unchanged"])
def test_photo_delete_wait_retains_identity_and_rechecks_permissions(
    postgres_engine,
    test_settings,
    change,
):
    with correction_http_scene(postgres_engine, test_settings) as (client, context, asset_id):
        uploaded = mutation_request(client, asset_id, context.person.id, "photo-upload")
        assert uploaded.status_code == 200, uploaded.text
        reached = Event()

        def observe(_connection, _cursor, statement, _parameters, _context, _many):
            if "FROM asset_photos" in statement and "FOR UPDATE" in statement:
                reached.set()

        with Session(postgres_engine) as blocker:
            photo = blocker.scalar(
                select(AssetPhoto).where(AssetPhoto.asset_id == asset_id).with_for_update()
            )
            original_content = photo.content
            event.listen(postgres_engine, "before_cursor_execute", observe)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(
                        mutation_request, client, asset_id, context.person.id, "photo-delete"
                    )
                    try:
                        assert reached.wait(15), "delete did not reach photo lock"
                        with Session(postgres_engine) as db:
                            db.execute(text("SET LOCAL lock_timeout = '200ms'"))
                            if change == "session":
                                with pytest.raises(OperationalError) as raised:
                                    db.execute(
                                        update(AuthSession)
                                        .where(AuthSession.id == context.session.id)
                                        .values(revoked_at=datetime.now(UTC))
                                    )
                                assert raised.value.orig.sqlstate == "55P03"
                                db.rollback()
                            elif change != "unchanged":
                                deny(db, context, f"assets.{change}", "global", None)
                                db.commit()
                    finally:
                        blocker.rollback()
                    response = pending.result(timeout=20)
            finally:
                event.remove(postgres_engine, "before_cursor_execute", observe)
        allowed = change in {"session", "unchanged"}
        assert response.status_code == (200 if allowed else 403), response.text
        with Session(postgres_engine) as db:
            photo = db.scalar(select(AssetPhoto).where(AssetPhoto.asset_id == asset_id))
            audit = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.actor_user_id == context.account.id,
                    AuditEvent.action == "asset.photo.remove",
                )
            )
            assert (photo is None) == allowed
            assert (audit is not None) == allowed
            if not allowed:
                assert photo.content == original_content
