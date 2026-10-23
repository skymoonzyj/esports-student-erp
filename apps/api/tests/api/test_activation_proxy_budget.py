"""Exercise real activation/registration routes, limiter and stored delivery attribution."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.registration.models import RegistrationVerificationChallenge
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor, intake


def start(client, index, headers, *, registration=False):
    email = f"proxy-{index}-{uuid4().hex}@example.com"
    return client.post(
        "/api/v1/registration/applications"
        if registration
        else "/api/v1/auth/profile-activation/start",
        json={"primary_purpose": "student", "identifier_kind": "email", "identifier": email}
        if registration
        else {"email": email},
        headers=headers,
    )


def test_trusted_proxy_clients_have_separate_activation_source_budgets(db_client):
    db_client.app.state.settings.trusted_proxy_cidrs = "10.0.0.0/24"
    with TestClient(db_client.app, client=("10.0.0.2", 43123)) as client:
        for index in range(10):
            assert (
                start(client, index, {"X-Forwarded-For": "203.0.113.11, 10.0.0.3"}).status_code
                == 200
            )
        assert start(client, 10, {"X-Forwarded-For": "203.0.113.11, 10.0.0.3"}).status_code == 429
        assert start(client, 11, {"X-Forwarded-For": "203.0.113.12, 10.0.0.3"}).status_code == 200


def test_same_client_registration_and_activation_share_the_minute_budget(db_client):
    db_client.app.state.settings.trusted_proxy_cidrs = "10.0.0.0/24"
    headers = {"X-Forwarded-For": "198.51.100.77, 203.0.113.11, 10.0.0.3"}
    with TestClient(db_client.app, client=("10.0.0.2", 43123)) as client:
        for index in range(9):
            assert start(client, index, headers, registration=True).status_code == 201
        assert start(client, 9, headers).status_code == 200
        # Client-controlled leftmost values cannot reset the real rightmost external peer.
        response = start(client, 10, {"X-Forwarded-For": "198.51.100.78, 203.0.113.11, 10.0.0.3"})
        assert response.status_code == 429
        assert response.headers["Cache-Control"] == "no-store, private"


@pytest.mark.parametrize(
    "peer,first,second",
    [
        ("198.51.100.9", {"X-Forwarded-For": "203.0.113.11"}, {"X-Forwarded-For": "203.0.113.12"}),
        (
            "10.0.0.2",
            {"X-Forwarded-For": "bad, 203.0.113.11"},
            {"X-Forwarded-For": "bad, 203.0.113.12"},
        ),
        (
            "10.0.0.2",
            [("X-Forwarded-For", "203.0.113.11"), ("X-Forwarded-For", "203.0.113.12")],
            {"X-Forwarded-For": ""},
        ),
    ],
)
def test_untrusted_or_malformed_forwarding_cannot_split_shared_budget(
    db_client, peer, first, second
):
    db_client.app.state.settings.trusted_proxy_cidrs = "10.0.0.0/24"
    with TestClient(db_client.app, client=(peer, 43123)) as client:
        for index in range(9):
            assert start(client, index, first, registration=True).status_code == 201
        assert start(client, 9, first).status_code == 200
        assert start(client, 10, second).status_code == 429


def test_activation_delivery_and_registration_challenge_use_the_same_trusted_source(
    db, db_client, platform_users, captured_sender
):
    approvals(db).direct_create(actor(platform_users, "admin"), intake(platform_users), uuid4())
    db_client.app.state.settings.trusted_proxy_cidrs = "10.0.0.0/24"
    with TestClient(db_client.app, client=("10.0.0.2", 43123)) as client:
        headers = {"X-Forwarded-For": "203.0.113.11"}
        assert (
            client.post(
                "/api/v1/auth/profile-activation/start",
                json={"email": "intake@example.com"},
                headers=headers,
            ).status_code
            == 200
        )
        response = start(client, 0, headers, registration=True)
        assert response.status_code == 201
        app_id = response.json()["application_id"]
        assert (
            client.post(
                f"/api/v1/registration/applications/{app_id}/challenge", headers=headers
            ).status_code
            == 201
        )
    rows = list(db.scalars(select(RegistrationVerificationChallenge)))
    assert len(rows) == len(captured_sender.messages) == 2
    assert rows[0].request_source_digest == rows[1].request_source_digest
