from uuid import uuid4

import pytest

from tests.api.test_people import sign_in
from tests.services.test_personnel_intake import intake


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/context"),
        ("get", "/requests"),
        ("get", f"/requests/{uuid4()}"),
        ("post", "/requests"),
    ],
)
def test_private_intake_errors_are_never_cached(client, method, path):
    response = getattr(client, method)("/api/v1/personnel-intake" + path)
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store, private"


def test_context_choices_submit_and_safe_validation(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "coach")
    context = db_client.get("/api/v1/personnel-intake/context")
    assert context.status_code == 200
    assert context.json()["eligible_coaches"] == [
        {"id": str(platform_users["people"]["coach"].id), "label": "平台教练"}
    ]
    assert context.json()["can_review"] is False
    url = "/api/v1/personnel-intake/requests"
    value = intake(platform_users)
    payload = value.model_dump(mode="json")
    payload.update(
        identity_number=value.identity_number.get_secret_value(),
        phone=value.phone.get_secret_value(),
        email=value.email.get_secret_value(),
    )
    assert db_client.post(url, json=payload).status_code == 403
    submitted = db_client.post(url, json=payload, headers={"X-CSRF-Token": csrf})
    assert submitted.status_code == 201
    assert submitted.headers["Cache-Control"] == "no-store, private"
    listed = db_client.get(url).json()
    assert listed["total"] == 1
    assert "scoped_payload" not in listed["items"][0]
    invalid = db_client.post(
        url,
        json={"identity_number": payload["identity_number"], "unexpected": payload["email"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422
    assert payload["identity_number"] not in invalid.text and payload["email"] not in invalid.text
    assert invalid.headers["Cache-Control"] == "no-store, private"
    malicious_key = "sensitive-key@example.com"
    invalid_key = db_client.post(
        url, json={malicious_key: "ignored"}, headers={"X-CSRF-Token": csrf}
    )
    assert invalid_key.status_code == 422
    assert malicious_key not in invalid_key.text
