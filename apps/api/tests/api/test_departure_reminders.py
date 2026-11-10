from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.auth.dependencies import require_standard_account
from app.core.database import get_db
from app.housing.models import HousingStay
from app.main import create_app
from app.rbac.models import UserRole
from tests.departure_helpers import business_snapshot, committed_scene

PATH = "/api/v1/portal/departure-reminders"


def assert_private(response):
    assert "no-store" in response.headers["cache-control"]
    assert "private" in response.headers["cache-control"]


def assert_minimal(value):
    if isinstance(value, dict):
        assert not set(value) & {
            "identity_ciphertext",
            "identity_digest",
            "phone",
            "phone_ciphertext",
            "email",
            "email_ciphertext",
            "roommates",
            "photos",
            "notes",
            "agreed_tuition_cents",
            "guardian_identity",
            "password_hash",
        }
        for child in value.values():
            assert_minimal(child)
    elif isinstance(value, list):
        for child in value:
            assert_minimal(child)


def test_endpoint_default_pagination_is_private_minimal_and_read_only(
    postgres_engine, test_settings
):
    with committed_scene(postgres_engine) as (db, context, _subject, source):
        # HTTP chooses current now, not a test-frozen clock.
        source.planned_check_out_at = datetime.now(UTC)
        db.commit()
        app = create_app(test_settings)
        app.dependency_overrides[require_standard_account] = lambda: context
        app.dependency_overrides[get_db] = lambda: db
        before = business_snapshot(db)
        with TestClient(app) as client:
            response = client.get(PATH)
        assert response.status_code == 200
        assert_private(response)
        data = response.json()
        assert (data["window"], data["page"], data["page_size"], data["total"]) == (
            "three_days",
            1,
            20,
            1,
        )
        assert data["items"][0]["events"][0]["detail_path"].startswith("/housing/rooms/")
        assert_minimal(data)
        assert business_snapshot(db) == before


@pytest.mark.parametrize("query", ["window=week", "page=0", "page_size=0", "page_size=51"])
def test_invalid_parameters_return_private_422(postgres_engine, test_settings, query):
    with committed_scene(postgres_engine) as (db, context, _subject, _source):
        app = create_app(test_settings)
        app.dependency_overrides[require_standard_account] = lambda: context
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            response = client.get(f"{PATH}?{query}")
        assert response.status_code == 422
        assert_private(response)


def test_unauthenticated_is_private_401(client):
    response = client.get(PATH)
    assert response.status_code == 401
    assert_private(response)


def test_no_scope_returns_private_unavailable_empty_response(postgres_engine, test_settings):
    with committed_scene(postgres_engine) as (db, context, _subject, _source):
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == context.account.id))
        db.commit()
        app = create_app(test_settings)
        app.dependency_overrides[require_standard_account] = lambda: context
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            response = client.get(PATH)
        assert response.status_code == 200
        assert_private(response)
        data = response.json()
        assert data["available"] is False
        assert data["items"] == []
        assert data["total"] == data["today_count"] == data["three_day_count"] == 0


def test_real_query_failure_is_sanitized_not_partial_success(postgres_engine, test_settings):
    fact_queries = 0

    def fail_after_counts(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal fact_queries
        if "departure_facts" not in statement:
            return
        fact_queries += 1
        if fact_queries == 2:
            raise OperationalError("private SQL", {}, RuntimeError("secret-person"))

    with committed_scene(postgres_engine) as (db, context, _subject, _source):
        app = create_app(test_settings)
        app.dependency_overrides[require_standard_account] = lambda: context
        app.dependency_overrides[get_db] = lambda: db
        event.listen(postgres_engine, "before_cursor_execute", fail_after_counts)
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get(PATH)
        finally:
            event.remove(postgres_engine, "before_cursor_execute", fail_after_counts)
        assert fact_queries == 2
        assert response.status_code == 500
        assert_private(response)
        assert "secret-person" not in response.text
        assert "private SQL" not in response.text


def test_duplicate_current_stays_use_existing_sanitized_private_error(
    postgres_engine, test_settings
):
    with committed_scene(postgres_engine) as (db, context, subject, source):
        now = datetime.now(UTC)
        source.planned_check_out_at = now
        duplicate = HousingStay(
            person_id=subject.id,
            room_id=source.room_id,
            stay_purpose="permanent",
            duration_mode="fixed_term",
            planned_check_in_at=now + timedelta(days=1),
            planned_check_out_at=now + timedelta(days=7),
            actual_check_in_at=now + timedelta(days=1),
            status="checked_in",
            room_name_snapshot="测试房间",
            floor_name_snapshot="测试楼层",
            building_name_snapshot="测试楼栋",
            created_by_user_id=context.account.id,
            checked_in_by_user_id=context.account.id,
        )
        db.add(duplicate)
        db.commit()
        try:
            app = create_app(test_settings)
            app.dependency_overrides[require_standard_account] = lambda: context
            app.dependency_overrides[get_db] = lambda: db
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get(PATH)
            assert response.status_code == 500
            assert response.json()["code"] == "housing_service_failure"
            assert_private(response)
            assert "multiple current stays" not in response.text
            assert "items" not in response.json()
        finally:
            db.delete(duplicate)
            db.commit()
