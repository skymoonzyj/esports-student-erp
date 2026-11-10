from uuid import uuid4

from tests.api.test_people import sign_in
from tests.services.test_personnel_intake import intake


def payload(users, **changes):
    value = intake(users)
    body = value.model_dump(mode="json")
    body["identity_number"] = value.identity_number.get_secret_value()
    body["phone"] = value.phone.get_secret_value()
    body["email"] = value.email.get_secret_value()
    body.update(idempotency_key=str(uuid4()), activation_role_keys=["coach"])
    body.update(changes)
    return body


def test_api_role_options_strict_staff_and_detail_capability(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "admin")
    options = db_client.get("/api/v1/personnel-intake/activation-role-options")
    assert options.status_code == 200
    assert len(options.json()["items"]) == 13
    assert all(i["assignable"] for i in options.json()["items"])
    result = db_client.post(
        "/api/v1/personnel-intake/direct-create",
        json=payload(platform_users, kind="staff", coach_person_ids=[]),
        headers={"X-CSRF-Token": csrf},
    )
    assert result.status_code == 201, result.text
    person_id = result.json()["person_id"]
    detail = db_client.get(f"/api/v1/people/{person_id}")
    assert detail.status_code == 200
    assert detail.json()["activation_roles_available"] is True
    view = db_client.get(f"/api/v1/personnel-intake/people/{person_id}/activation-roles")
    assert view.json()["role_keys"] == ["coach"]
    update = db_client.put(
        f"/api/v1/personnel-intake/people/{person_id}/activation-roles",
        json={"role_keys": [], "expected_version": 1, "idempotency_key": str(uuid4())},
        headers={"X-CSRF-Token": csrf},
    )
    assert update.status_code == 200, update.text
    assert update.json()["version"] == 2 and update.json()["role_keys"] == []


def test_ordinary_submitter_cannot_read_role_directory(db_client, platform_users):
    sign_in(db_client, platform_users, "coach")
    response = db_client.get("/api/v1/personnel-intake/activation-role-options")
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store, private"


def test_ordinary_submission_rejects_role_injection(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "coach")
    body = payload(platform_users)
    body.pop("idempotency_key")
    result = db_client.post(
        "/api/v1/personnel-intake/requests", json=body, headers={"X-CSRF-Token": csrf}
    )
    assert result.status_code == 422
