from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.identity.models import Person
from app.personnel_intake.models import CoachPersonResponsibility
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.security.encryption import EnvelopeCipher
from tests.api.test_people import sign_in
from tests.api.test_people_assisted_projection import approved
from tests.services.test_personnel_intake import actor, intake
from tests.services.test_team_access import teams


def test_editor_context_requires_standard_account_and_private_errors(client):
    response = client.get(f"/api/v1/personnel-intake/people/{uuid4()}/editor-context")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store, private"


def override(db, users, permission, scope="global", scope_id=None):
    db.add(
        UserPermissionOverride(
            user_id=users["accounts"]["admin"].id,
            permission_id=PERMISSIONS[permission].id,
            effect="deny",
            scope_kind=scope,
            scope_id=scope_id,
        )
    )
    db.flush()


def test_update_only_admin_gets_whitelisted_live_metadata_without_decryption(
    db, db_client, platform_users, monkeypatch
):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    result = approved(db, platform_users, first, "both")
    override(db, platform_users, "personnel_intake.submit")
    override(db, platform_users, "personnel_intake.review")
    sign_in(db_client, platform_users, "admin")

    def no_decrypt(*args, **kwargs):
        pytest.fail("editor metadata must not decrypt facts")

    monkeypatch.setattr(EnvelopeCipher, "decrypt", no_decrypt)
    response = db_client.get(f"/api/v1/personnel-intake/people/{result.person_id}/editor-context")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.json() == {
        "person_id": str(result.person_id),
        "kind": "both",
        "profile_version": 1,
        "current_coach_person_ids": [str(platform_users["people"]["coach"].id)],
        "eligible_coaches": [
            {"id": str(platform_users["people"]["coach"].id), "label": "平台教练"}
        ],
        "allowed_actions": ["correct_profile", "replace_coaches"],
    }
    # Ended responsibilities and changed profile version must override request metadata.
    from sqlalchemy import select

    from app.personnel_intake.models import AssistedPersonProfile

    row = db.scalar(
        select(CoachPersonResponsibility).where(
            CoachPersonResponsibility.person_id == result.person_id
        )
    )
    row.ended_at, row.ended_by_user_id = datetime.now(UTC), platform_users["accounts"]["admin"].id
    db.get(AssistedPersonProfile, result.person_id).version = 3
    db.flush()
    refreshed = db_client.get(
        f"/api/v1/personnel-intake/people/{result.person_id}/editor-context"
    ).json()
    assert refreshed["profile_version"] == 3 and refreshed["current_coach_person_ids"] == []


@pytest.mark.parametrize(
    "denial", ["nonadmin", "revoked", "target", "deleted", "missing", "legacy"]
)
def test_editor_context_private_failures_never_decrypt(
    db, db_client, platform_users, monkeypatch, denial
):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    person_id = approved(db, platform_users, first, "both").person_id
    key = "admin"
    if denial == "nonadmin":
        key = "coach"
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["coach"].id,
                permission_id=PERMISSIONS["people.update"].id,
                effect="allow",
                scope_kind="global",
            )
        )
    elif denial == "revoked":
        db.execute(
            UserRole.__table__.delete().where(
                UserRole.user_id == platform_users["accounts"]["admin"].id
            )
        )
    elif denial == "target":
        override(db, platform_users, "people.update", "person", person_id)
    elif denial == "deleted":
        db.get(Person, person_id).deleted_at = datetime.now(UTC)
    elif denial == "missing":
        person_id = uuid4()
    elif denial == "legacy":
        person_id = platform_users["people"]["adult_without_account"].id
    db.flush()
    sign_in(db_client, platform_users, key)

    def no_decrypt(*args, **kwargs):
        pytest.fail("private denial decrypted facts")

    monkeypatch.setattr(EnvelopeCipher, "decrypt", no_decrypt)
    response = db_client.get(f"/api/v1/personnel-intake/people/{person_id}/editor-context")
    assert response.status_code in (403, 404)
    assert response.headers["Cache-Control"] == "no-store, private"
    assert "kind" not in response.json() and "person_id" not in response.json()


def test_affiliation_deny_hides_only_correction_action(db, db_client, platform_users):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    person_id = approved(db, platform_users, first, "both").person_id
    override(db, platform_users, "people.update", "assigned_team", first.id)
    sign_in(db_client, platform_users, "admin")
    response = db_client.get(f"/api/v1/personnel-intake/people/{person_id}/editor-context")
    assert response.status_code == 200
    assert response.json()["allowed_actions"] == ["replace_coaches"]


def test_coach_write_uses_profile_version_and_stale_correction_conflicts(
    db, db_client, platform_users
):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    result = approved(db, platform_users, first, "both")
    csrf = sign_in(db_client, platform_users, "admin")
    root = f"/api/v1/personnel-intake/people/{result.person_id}"
    changed = db_client.put(
        root + "/coaches",
        json={"coach_person_ids": [], "expected_version": 1, "reason": "核验负责人变更"},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 204
    view = db_client.get(root + "/editor-context")
    assert view.status_code == 200
    assert view.json()["profile_version"] == 2
    assert view.json()["current_coach_person_ids"] == []
    source = intake(platform_users)
    payload = {
        "full_name": source.full_name,
        "identity_number": source.identity_number.get_secret_value(),
        "phone": source.phone.get_secret_value(),
        "email": "projection-321@example.com",
        "sex": source.sex,
        "date_of_birth": source.date_of_birth.isoformat(),
        "guardians": [],
        "expected_version": 1,
        "reason": "核验资料更正",
        "idempotency_key": str(uuid4()),
    }
    stale = db_client.post(root + "/corrections", json=payload, headers={"X-CSRF-Token": csrf})
    assert stale.status_code == 409


def test_editor_query_budget_and_missing_current_lineage_fail_closed(
    db, platform_users, monkeypatch
):
    from importlib.util import find_spec

    from sqlalchemy import event

    assert find_spec("app.personnel_intake.editor_context") is not None, "metadata query is missing"

    from app.personnel_intake import editor_context
    from app.personnel_intake.models import AssistedApproval, PersonnelIntakeRequest

    first, _ = teams(db, actor(platform_users, "admin").account.id)
    result = approved(db, platform_users, first, "both")
    who = actor(platform_users, "admin")
    statements = []

    def no_decrypt(*args, **kwargs):
        pytest.fail("editor metadata must not decrypt facts")

    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    monkeypatch.setattr(EnvelopeCipher, "decrypt", no_decrypt)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        view = editor_context.AssistedEditorContextQuery(db).get(who, result.person_id)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert view.profile_version == 1 and len(statements) <= 10
    assert not any("snapshot_ciphertext" in q or "payload_ciphertext" in q for q in statements)
    # Invalid source linkage must not fall back to an invented student/player kind.
    approval = db.get(AssistedApproval, result.approval_id)
    db.get(PersonnelIntakeRequest, approval.request_id).state = "withdrawn"
    db.flush()
    from app.personnel_intake.errors import PersonnelIntakeError

    with pytest.raises(PersonnelIntakeError) as error:
        editor_context.AssistedEditorContextQuery(db).get(who, result.person_id)
    assert error.value.status == 404
