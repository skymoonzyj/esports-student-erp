from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, text, update

from app.asset_management.models import Asset, AssetAssignment
from app.audit.models import AuditEvent
from app.auth.dependencies import require_standard_account
from app.auth.models import AuthSession, UserAccount
from app.core.database import get_db
from app.core.errors import ProblemError
from app.identity.models import StudentProfile
from app.main import create_app
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from tests.departure_helpers import committed_scene, deny

PATH = "/api/v1/portal/rental-reminders"
NOW = datetime(2026, 10, 24, 15, 59, 59, tzinfo=UTC)


@contextmanager
def scene(engine):
    with committed_scene(engine) as (db, actor, subject, _source):
        ids = []

        def asset(code="RENT-A", expires=date(2026, 10, 31), **extra):
            row = Asset(
                asset_code=f"{code}-{uuid4().hex[:8]}", name="测试租赁电脑",
                location="合成测试室", ownership_type="rented",
                rental_started_on=date(2026, 1, 1), rental_expires_on=expires,
                rental_revision=0, created_by_user_id=actor.account.id,
                **extra,
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
            return row

        try:
            yield db, actor, subject, asset
        finally:
            db.rollback()
            db.execute(delete(AssetAssignment).where(AssetAssignment.asset_id.in_(ids)))
            db.execute(delete(Asset).where(Asset.id.in_(ids)))
            db.commit()


def service(db):
    from app.portal.rental_reminders import PortalRentalReminderService
    from app.security.encryption import EnvelopeCipher, LookupDigest

    return PortalRentalReminderService(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )


def read(db, actor, now=NOW, page=1, page_size=20):
    return service(db).list_reminders(actor=actor, now=now, page=page, page_size=page_size)


def test_inclusive_day_seven_overdue_maintenance_unassigned_excludes_ineligible(postgres_engine):
    with scene(postgres_engine) as (db, actor, subject, asset):
        seventh = asset("SEVEN")
        overdue = asset("OVERDUE", date(2026, 10, 20), status="maintenance")
        today = asset("TODAY", date(2026, 10, 24), status="assigned")
        asset("EIGHT", date(2026, 11, 1))
        asset("RETIRED", status="retired")
        asset("RETURNED", rental_returned_on=date(2026, 10, 23))
        future = asset("FUTURE")
        future.rental_started_on = date(2026, 10, 25)
        owned = asset("OWNED")
        owned.ownership_type = "owned"
        owned.acquisition_method = "purchase"
        owned.acquisition_channel = "online"
        owned.rental_started_on = owned.rental_expires_on = None
        db.add(AssetAssignment(asset_id=today.id, person_id=subject.id, assigned_at=NOW, assigned_by_user_id=actor.account.id))
        db.commit()
        result = read(db, actor)
        assert result.available is True and result.can_manage is True
        assert result.total == 3
        assert [item.asset_id for item in result.items] == [overdue.id, today.id, seventh.id]
        assert [item.days_remaining for item in result.items] == [-4, 0, 7]
        assert [item.person_name for item in result.items] == [None, subject.full_name, None]
        assert result.business_date == date(2026, 10, 24)
        assert result.next_refresh_at == datetime(2026, 10, 24, 16, tzinfo=UTC)


def test_china_midnight_changes_window_and_days(postgres_engine):
    with scene(postgres_engine) as (db, actor, _subject, asset):
        asset(expires=date(2026, 11, 1))
        db.commit()
        assert read(db, actor).total == 0
        result = read(db, actor, now=datetime(2026, 10, 24, 16, tzinfo=UTC))
        assert result.total == 1
        assert result.business_date == date(2026, 10, 25)
        assert result.items[0].days_remaining == 7


def test_order_pagination_and_count_use_same_snapshot_without_photos_or_n_plus_one(postgres_engine):
    with scene(postgres_engine) as (db, actor, _subject, asset):
        rows = [asset(code) for code in ["Z", "A", "M"]]
        db.commit()
        statements = []

        def record(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(postgres_engine, "before_cursor_execute", record)
        try:
            result = read(db, actor, page=2, page_size=1)
        finally:
            event.remove(postgres_engine, "before_cursor_execute", record)
        assert result.total == 3 and result.page == 2 and result.page_size == 1
        assert result.items[0].asset_id == rows[2].id
        assert not any("asset_photos" in sql for sql in statements)
        assert len([sql for sql in statements if "FROM assets" in sql]) == 2
        assert read(db, actor, page=8).items == []


def test_global_denial_returns_empty_capability_even_with_manage_or_self(postgres_engine):
    with scene(postgres_engine) as (db, actor, _subject, asset):
        asset()
        deny(db, actor, "assets.read", "global", None)
        db.commit()
        result = read(db, actor)
        assert result.available is False and result.can_manage is False
        assert result.total == 0 and result.items == []


def test_manage_is_separate_and_roles_are_rechecked_freshly(postgres_engine):
    with scene(postgres_engine) as (db, actor, _subject, asset):
        asset()
        deny(db, actor, "assets.manage", "global", None)
        db.commit()
        result = read(db, actor)
        assert result.available is True and result.can_manage is False
        db.execute(delete(UserRole).where(UserRole.user_id == actor.account.id))
        db.commit()
        assert read(db, actor).available is False


def test_self_only_permission_never_exposes_supplier_rental_projection(postgres_engine):
    with scene(postgres_engine) as (db, actor, _subject, asset):
        item = asset()
        db.add(AssetAssignment(asset_id=item.id, person_id=actor.person.id, assigned_at=NOW, assigned_by_user_id=actor.account.id))
        db.execute(delete(UserRole).where(UserRole.user_id == actor.account.id))
        db.add(UserPermissionOverride(user_id=actor.account.id, permission_id=PERMISSIONS["assets.read_self"].id, effect="allow", scope_kind="self", scope_id=None))
        db.commit()
        result = read(db, actor)
        assert (result.available, result.can_manage, result.total, result.items) == (False, False, 0, [])


@pytest.mark.parametrize("change", ["renewal", "permission"])
def test_count_page_and_permissions_share_readonly_snapshot_under_concurrent_change(postgres_engine, change):
    with scene(postgres_engine) as (db, actor, _subject, asset):
        item = asset()
        db.commit()
        changed = False

        def change_after_count(connection, _cursor, statement, _parameters, _context, _many):
            nonlocal changed
            if changed or "FROM assets" not in statement:
                return
            changed = True
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
            assert connection.exec_driver_sql("SHOW transaction_isolation").scalar_one() == "repeatable read"
            with postgres_engine.begin() as writer:
                if change == "renewal":
                    writer.execute(update(Asset).where(Asset.id == item.id).values(rental_expires_on=date(2026, 12, 1)))
                else:
                    writer.execute(delete(UserRole).where(UserRole.user_id == actor.account.id))

        event.listen(postgres_engine, "after_cursor_execute", change_after_count)
        try:
            result = read(db, actor)
        finally:
            event.remove(postgres_engine, "after_cursor_execute", change_after_count)
        assert changed
        assert result.total == 1 and result.items[0].asset_id == item.id
        assert result.items[0].rental_expires_on == date(2026, 10, 31)
        assert read(db, actor).total == 0


def test_caller_transaction_is_not_flushed_or_committed_and_read_does_not_audit(postgres_engine):
    with scene(postgres_engine) as (db, actor, _subject, asset):
        item = asset()
        db.commit()
        db.execute(text("SELECT 1"))
        transaction = db.get_transaction()
        original_name = item.name
        before = db.scalar(select(func.count()).select_from(AuditEvent))
        item.name = "未提交的名字"
        result = read(db, actor)
        assert result.items[0].name == original_name
        assert db.get_transaction() is transaction and transaction.is_active
        db.rollback()
        assert item.name == original_name
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == before


@pytest.mark.parametrize("change", ["revoked", "expired", "disabled", "force_password", "readiness"])
def test_session_and_readiness_rechecked_without_auth_side_effects(postgres_engine, change):
    with scene(postgres_engine) as (db, actor, _subject, _asset):
        if change == "revoked":
            actor.session.revoked_at = NOW - timedelta(seconds=1)
        elif change == "expired":
            actor.session.expires_at = NOW
        elif change == "disabled":
            actor.account.status = "disabled"
        elif change == "force_password":
            actor.account.force_password_change = True
        else:
            actor.person.date_of_birth = date(2015, 1, 1)
            db.add(StudentProfile(person_id=actor.person.id, student_number="rental-" + actor.person.id.hex[:16], status="active"))
        db.commit()
        with pytest.raises(ProblemError) as error:
            read(db, actor)
        assert error.value.status == (403 if change == "force_password" else 401)
        assert db.scalar(select(UserAccount.status).where(UserAccount.id == actor.account.id)) == ("disabled" if change == "disabled" else "active")
        if change == "readiness":
            assert db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == actor.session.id)) is None


def test_endpoint_is_private_read_only_minimal_and_validates_parameters(postgres_engine, test_settings):
    with scene(postgres_engine) as (db, actor, _subject, asset):
        asset(expires=datetime.now(UTC).date())
        db.commit()
        app = create_app(test_settings)
        app.dependency_overrides[require_standard_account] = lambda: actor
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            response = client.get(PATH)
            assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"]
            assert "private" in response.headers["cache-control"]
            data = response.json()
            assert data["total"] == 1
            assert set(data["items"][0]) == {"asset_id", "asset_code", "name", "location", "person_name", "rental_expires_on", "days_remaining", "rental_revision"}
            for query in ["page=0", "page_size=0", "page_size=101"]:
                invalid = client.get(f"{PATH}?{query}")
                assert invalid.status_code == 422
                assert "no-store" in invalid.headers["cache-control"]


def test_unauthenticated_is_401(client):
    response = client.get(PATH)
    assert response.status_code == 401
    assert "no-store" in response.headers["cache-control"]
