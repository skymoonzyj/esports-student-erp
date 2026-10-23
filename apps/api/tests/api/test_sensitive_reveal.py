from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.core.database import get_db


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post("/api/v1/auth/login", json={"identifier": phone, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_sensitive_reveal_requires_permission_and_purpose(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "customer_service")
    person = platform_users["people"]["own_student"]
    denied = db_client.post(
        f"/api/v1/people/{person.id}/sensitive-reveal",
        headers={"X-CSRF-Token": csrf},
        json={"field": "identity_number", "purpose": "入住资料核对"},
    )

    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "no-store, private"
    assert denied.json()["code"] == "permission_denied"
    assert "identity" not in denied.json()["detail"].lower()
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "person.identity.reveal",
            AuditEvent.outcome == "denied",
        )
    ) == 1


def test_sensitive_reveal_validates_field_and_purpose(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    person = platform_users["people"]["own_student"]

    bad_field = db_client.post(
        f"/api/v1/people/{person.id}/sensitive-reveal",
        headers={"X-CSRF-Token": csrf},
        json={"field": "email", "purpose": "入住资料核对"},
    )
    short_purpose = db_client.post(
        f"/api/v1/people/{person.id}/sensitive-reveal",
        headers={"X-CSRF-Token": csrf},
        json={"field": "identity_number", "purpose": "核对"},
    )

    assert bad_field.status_code == 422
    assert short_purpose.status_code == 422
    assert bad_field.headers["cache-control"] == "no-store, private"
    assert short_purpose.headers["cache-control"] == "no-store, private"


def test_sensitive_reveal_auth_failure_is_no_store(
    db_client: TestClient,
    platform_users,
) -> None:
    person = platform_users["people"]["own_student"]

    response = db_client.post(
        f"/api/v1/people/{person.id}/sensitive-reveal",
        json={"field": "identity_number", "purpose": "入住资料核对"},
    )

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"


def test_sensitive_reveal_unexpected_failure_is_no_store(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    person = platform_users["people"]["own_student"]

    def broken_db():  # type: ignore[no-untyped-def]
        raise RuntimeError("database unavailable")
        yield

    db_client.app.dependency_overrides[get_db] = broken_db
    try:
        response = db_client.post(
            f"/api/v1/people/{person.id}/sensitive-reveal",
            headers={"X-CSRF-Token": csrf},
            json={"field": "identity_number", "purpose": "入住资料核对"},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "internal_error"


def test_reveal_decryption_failure_is_generic_no_store_and_audited(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    person_summary = platform_users["people"]["own_student"]
    from app.identity.models import Person

    person = db.get(Person, person_summary.id)
    assert person is not None
    person.identity_ciphertext = "invalid-envelope"
    db.commit()

    response = db_client.post(
        f"/api/v1/people/{person.id}/sensitive-reveal",
        headers={"X-CSRF-Token": csrf},
        json={"field": "identity_number", "purpose": "入住资料核对"},
    )

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "sensitive_reveal_failed"
    assert "identity" not in response.json()["detail"].lower()
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "person.identity.reveal",
            AuditEvent.outcome == "failed",
        )
    ) == 1


def test_authorized_reveal_is_no_store_and_audited(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    person = platform_users["people"]["own_student"]
    response = db_client.post(
        f"/api/v1/people/{person.id}/sensitive-reveal",
        headers={"X-CSRF-Token": csrf},
        json={"field": "identity_number", "purpose": "入住资料核对"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["value"] == "110105201201021064"
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "person.identity.reveal",
            AuditEvent.outcome == "allowed",
        )
    ) == 1
