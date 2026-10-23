import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.models import UserAccount
from app.core.database import get_db
from app.main import create_app
from app.personnel_intake.claims.models import ProfileClaim
from tests.services.test_profile_claims import (
    approved_target,
    claim_input,
    claims,
)
from tests.services.test_profile_claims import (
    claim_db as _claim_db,
)


@pytest.fixture
def claim_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


@pytest.fixture
def claim_client(claim_db, test_settings):
    from app.personnel_intake.claims.dependencies import get_profile_claim_service

    service, sender = claims(claim_db)
    app = create_app(test_settings)
    app.dependency_overrides[get_profile_claim_service] = lambda: service
    app.dependency_overrides[get_db] = lambda: claim_db
    with TestClient(app, base_url="https://testserver") as client:
        yield client, service, sender


@pytest.mark.parametrize("scenario", ["matched", "unmatched", "unknown_issuer"])
def test_incomplete_profile_can_submit_completion_without_enumeration(
    claim_client, claim_db, scenario
):
    from tests.services.test_profile_claims import incomplete_pending_target

    client, _, sender = claim_client
    incomplete_pending_target(claim_db)
    body = {
        "full_name": "隔离护照本人",
        "identity_number": "PENDING-CLAIM-851",
        "document_type": "passport",
        "issuing_country": "US",
        "email": "claim@example.com",
        "completion": {"phone": "+1 202 555 0100", "sex": "female", "date_of_birth": "2000-01-02"},
    }
    if scenario == "unmatched":
        body["identity_number"] = "OTHER-851"
    elif scenario == "unknown_issuer":
        body["issuing_country"] = None
    started = client.post("/api/v1/auth/profile-claims/start", json=body)
    assert started.status_code == 200
    assert set(started.json()) == {"accepted", "resend_available_at", "expires_at"}
    assert len(sender.messages) == 1 and sender.messages[0].purpose == "profile_claim"
    verified = client.post(
        "/api/v1/auth/profile-claims/verify", json={"code": sender.messages[0].code}
    )
    assert verified.status_code == 200
    assert set(verified.json()) <= {"status", "reason"}
    assert verified.json() == {"status": "waiting_review", "reason": None}
    row = claim_db.scalar(select(ProfileClaim))
    assert (row.private_match_reason is None) == (scenario == "matched")


@pytest.mark.parametrize(
    "field", ["person_id", "kind", "staff_type", "roles", "coach", "team", "assignment"]
)
def test_completion_rejects_authority_fields(claim_client, field):
    client, _, sender = claim_client
    body = {
        "full_name": "本人",
        "identity_number": "P851",
        "document_type": "passport",
        "email": "claim@example.com",
        "completion": {field: "untrusted"},
    }
    response = client.post("/api/v1/auth/profile-claims/start", json=body)
    assert response.status_code == 422 and not sender.messages


@pytest.mark.parametrize("scenario", ["valid", "no_match", "wrong_name", "account", "out_of_scope"])
def test_public_claim_matching_is_not_an_identity_oracle(claim_client, claim_db, scenario):
    client, service, sender = claim_client
    target, users = approved_target(claim_db)
    value = claim_input()
    if scenario == "no_match":
        from tests.api.conftest import make_cn_id

        value = claim_input(identity_number=make_cn_id("20000102", 323))
    elif scenario == "wrong_name":
        value = claim_input(full_name="另一个名字")
    elif scenario == "account":
        from app.security.passwords import PasswordService

        claim_db.add(
            UserAccount(
                person_id=target.person_id,
                status="disabled",
                password_hash=PasswordService().hash("SyntheticPass1"),
            )
        )
        claim_db.commit()
    elif scenario == "out_of_scope":
        from app.identity.models import Person

        person = claim_db.get(Person, users["people"]["admin"].id)
        value = claim_input(
            full_name=person.full_name,
            identity_number=service.cipher.decrypt(
                person.identity_ciphertext, field_name="identity_number"
            ),
        )
    body = {
        key: getattr(value, key).get_secret_value()
        for key in ("full_name", "identity_number", "email")
    }
    start = client.post("/api/v1/auth/profile-claims/start", json=body)
    assert start.status_code == 200
    assert set(start.json()) == {"accepted", "resend_available_at", "expires_at"}
    assert len(sender.messages) == 1 and sender.messages[0].purpose == "profile_claim"
    assert start.headers["cache-control"] == "no-store, private"
    assert "HttpOnly" in start.headers["set-cookie"] and "Secure" in start.headers["set-cookie"]
    verify = client.post(
        "/api/v1/auth/profile-claims/verify", json={"code": sender.messages[0].code}
    )
    assert verify.status_code == 200 and verify.json() == {
        "status": "waiting_review",
        "reason": None,
    }
    assert all(value not in verify.text for value in ("person", "name", "internal_reason"))
    row = claim_db.scalar(select(ProfileClaim))
    assert (row.private_match_reason is None) == (scenario == "valid")
    assert client.post("/api/v1/auth/profile-claims/status", json={}).json() == verify.json()


@pytest.mark.parametrize("path", ["start", "verify", "status"])
def test_profile_claim_errors_are_private_before_service_dependency(client, path):
    response = client.post(
        f"/api/v1/auth/profile-claims/{path}",
        json={},
        headers={"origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store, private"


def test_profile_claim_cookie_and_strict_status_request(claim_client):
    client, _, sender = claim_client
    value = claim_input()
    started = client.post(
        "/api/v1/auth/profile-claims/start",
        json={
            key: getattr(value, key).get_secret_value()
            for key in ("full_name", "identity_number", "email")
        },
    )
    cookie = started.headers["set-cookie"]
    assert "Path=/api/v1/auth/profile-claims" in cookie and "SameSite=lax" in cookie
    assert "erp_session=" not in cookie
    seconds = int(cookie.split("Max-Age=", 1)[1].split(";", 1)[0])
    assert 0 < seconds <= 600
    assert client.cookies.get("erp_profile_claim") not in started.text
    unverified = client.post("/api/v1/auth/profile-claims/status", json={})
    assert (
        unverified.status_code == 400 and unverified.headers["cache-control"] == "no-store, private"
    )
    client.post("/api/v1/auth/profile-claims/verify", json={"code": sender.messages[-1].code})
    for body in ({"person_id": "guess"}, {"email": "other@example.com"}):
        response = client.post("/api/v1/auth/profile-claims/status", json=body)
        assert (
            response.status_code == 422 and response.headers["cache-control"] == "no-store, private"
        )


@pytest.mark.parametrize("path", ["start", "verify", "status"])
@pytest.mark.parametrize(
    "headers,expected",
    [
        ([("content-type", "text/plain")], 415),
        ([("content-type", "application/json"), ("content-type", "application/json")], 415),
        ([("origin", "null")], 403),
        ([("origin", "http://127.0.0.1:5173"), ("origin", "http://127.0.0.1:5173")], 403),
        ([("sec-fetch-site", "cross-site")], 403),
        ([("sec-fetch-mode", "navigate")], 403),
        ([("sec-fetch-dest", "document")], 403),
        ([("sec-fetch-site", "same-origin"), ("sec-fetch-site", "same-origin")], 403),
    ],
)
def test_profile_claim_metadata_guards_cover_status_and_private_errors(
    client, path, headers, expected
):
    response = client.post(
        f"/api/v1/auth/profile-claims/{path}",
        content="{}",
        headers=[("content-type", "application/json"), *headers]
        if not any(k == "content-type" for k, _ in headers)
        else headers,
    )
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store, private"


def test_profile_claim_quota_http_error_has_retry_after_and_no_internal_target(claim_client):
    client, service, _sender = claim_client
    from app.personnel_intake.claims.errors import ProfileClaimError

    def limited(token, code):
        raise ProfileClaimError(status=429, retry_after_seconds=60)

    service.verify = limited
    client.cookies.set("erp_profile_claim", "a" * 43)
    response = client.post("/api/v1/auth/profile-claims/verify", json={"code": "123456"})
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    assert (
        "target" not in response.text and response.headers["cache-control"] == "no-store, private"
    )


def test_real_profile_claim_dependency_uses_capturing_sender_and_private_cookie(
    claim_db, test_settings
):
    from app.registration.delivery import CapturingVerificationSender

    app = create_app(test_settings)
    sender = CapturingVerificationSender(messages=[])
    app.state.registration_verification_sender = sender
    app.dependency_overrides[get_db] = lambda: claim_db
    value = claim_input()
    body = {
        key: getattr(value, key).get_secret_value()
        for key in ("full_name", "identity_number", "email")
    }
    with TestClient(app, base_url="https://testserver") as client:
        start = client.post("/api/v1/auth/profile-claims/start", json=body)
        assert start.status_code == 200 and len(sender.messages) == 1
        verified = client.post(
            "/api/v1/auth/profile-claims/verify", json={"code": sender.messages[-1].code}
        )
        assert verified.json() == {"status": "waiting_review", "reason": None}
        assert client.post("/api/v1/auth/profile-claims/status", json={}).json() == verified.json()


def test_global_mail_unavailable_is_uniform_and_does_not_query_target(test_settings, monkeypatch):
    from app.mail.settings_service import MailSettingsNotConfigured, MailSettingsService

    class NoTargetQueries:
        def scalar(self, *args, **kwargs):
            raise AssertionError("target must not be queried without delivery configuration")

    def unavailable(self):
        raise MailSettingsNotConfigured("synthetic unavailable configuration")

    monkeypatch.setattr(MailSettingsService, "credentials", unavailable)
    app = create_app(test_settings)
    app.dependency_overrides[get_db] = lambda: NoTargetQueries()
    with TestClient(app) as client:
        responses = []
        for full_name in ("已有档案姓名", "未知输入姓名"):
            value = claim_input(full_name=full_name)
            response = client.post(
                "/api/v1/auth/profile-claims/start",
                json={
                    key: getattr(value, key).get_secret_value()
                    for key in ("full_name", "identity_number", "email")
                },
            )
            assert response.status_code == 503 and "set-cookie" not in response.headers
            assert response.headers["cache-control"] == "no-store, private"
            body = response.json()
            body.pop("request_id")
            responses.append(body)
        assert responses[0] == responses[1]
