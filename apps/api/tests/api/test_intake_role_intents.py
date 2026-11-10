"""Direct profiles must not implicitly grant their requested account roles."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.auth.models import UserAccount
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from tests.api.test_intake_identity_teams import identity_body
from tests.api.test_people import sign_in


def administration(db, users):
    uid = users["accounts"]["coach"].id
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == uid))
    db.add(UserRole(user_id=uid, role_id=ROLES["administration"].id))
    db.commit()


@pytest.mark.parametrize("identity", ["coach", "life_teacher", "staff", "team_manager", "assistant_coach"])
@pytest.mark.parametrize("mode", ["direct-create", "direct-create-pending"])
def test_administration_creates_profile_without_granting_requested_role(
    db, db_client, platform_users, identity, mode
):
    administration(db, platform_users)
    csrf = sign_in(db_client, platform_users, "coach")
    body = identity_body(platform_users, coach_person_ids=[],
                         identity_selection={"primary_identity": identity})
    response = db_client.post(f"/api/v1/personnel-intake/{mode}", json=body,
                              headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    person_id = response.json()["person_id"]
    db.expire_all()
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == UUID(person_id))) is None
    intent = db_client.get(f"/api/v1/personnel-intake/people/{person_id}/role-intent")
    assert intent.status_code == 200, intent.text
    assert intent.json()["role_keys"] == [identity]
    assert intent.json()["status"] == "pending"
    assert intent.json()["can_approve"] is False
    forbidden = db_client.post(f"/api/v1/personnel-intake/people/{person_id}/role-intent/approve",
        json={"expected_version": 1, "idempotency_key": str(uuid4())},
        headers={"X-CSRF-Token": csrf})
    assert forbidden.status_code == 403


def test_administration_identity_options_distinguish_request_and_grant(db, db_client, platform_users):
    administration(db, platform_users)
    sign_in(db_client, platform_users, "coach")
    result = db_client.get("/api/v1/personnel-intake/management-identity-context")
    assert result.status_code == 200, result.text
    options = {item["key"]: item for item in result.json()["role_options"]}
    assert options["coach"]["requestable"] is True
    assert options["coach"]["assignable"] is False
    assert options["finance"]["requestable"] is False
    assert options["finance"]["assignable"] is False
    assert "admin" not in options and "super_admin" not in options


@pytest.mark.parametrize("identity", ["finance", "admin", "super_admin"])
def test_administration_cannot_smuggle_privileged_identity(db, db_client, platform_users, identity):
    from sqlalchemy import func

    from app.identity.models import Person
    administration(db, platform_users)
    csrf = sign_in(db_client, platform_users, "coach")
    before = db.scalar(select(func.count()).select_from(Person))
    result = db_client.post("/api/v1/personnel-intake/direct-create",
        json=identity_body(platform_users, coach_person_ids=[], identity_selection={"primary_identity": identity}),
        headers={"X-CSRF-Token": csrf})
    assert result.status_code in {403, 422}
    assert db.scalar(select(func.count()).select_from(Person)) == before


def test_administration_direct_access_does_not_open_other_approval_or_account_permissions(db, db_client, platform_users):
    administration(db, platform_users)
    sign_in(db_client, platform_users, "coach")
    context = db_client.get("/api/v1/personnel-intake/context").json()
    assert context["can_direct_create"] is True
    assert context["can_direct_create_pending"] is True
    assert context["can_review"] is False
    assert db_client.get("/api/v1/personnel-intake/role-intents").status_code == 403
    assert db_client.get("/api/v1/personnel-intake/activation-role-options").status_code == 403


def test_explicit_intake_deny_blocks_direct_profiles(db, db_client, platform_users):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    administration(db, platform_users)
    db.add(UserPermissionOverride(user_id=platform_users["accounts"]["coach"].id,
        permission_id=PERMISSIONS["personnel_intake.review"].id, effect="deny", scope_kind="global"))
    db.commit()
    csrf = sign_in(db_client, platform_users, "coach")
    response = db_client.post("/api/v1/personnel-intake/direct-create",
        json=identity_body(platform_users, coach_person_ids=[], identity_selection={"primary_identity": "life_teacher"}),
        headers={"X-CSRF-Token": csrf})
    assert response.status_code == 403


def test_administration_with_delegable_bundle_still_creates_profile(db, db_client, platform_users):
    from tests.services.test_intake_activation_roles import property_delegation
    administration(db, platform_users)
    property_delegation(db, platform_users["accounts"]["coach"].id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "coach")
    response = db_client.post("/api/v1/personnel-intake/direct-create",
        json=identity_body(platform_users, coach_person_ids=[], identity_selection={"primary_identity": "property"}),
        headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    view = db_client.get(f"/api/v1/personnel-intake/people/{response.json()['person_id']}/role-intent")
    assert view.json()["status"] == "pending"


@pytest.mark.parametrize("identity", ["student", "team_member"])
@pytest.mark.parametrize("mode", ["direct-create", "direct-create-pending"])
def test_administration_directly_creates_base_identity_with_business_affiliation(
    db, db_client, platform_users, identity, mode
):
    from app.identity.models import StudentProfile
    from app.personnel_intake.models import CoachPersonResponsibility
    from app.personnel_intake.role_intent_models import IntakeRoleIntent
    from app.team_management.models import TeamMembership
    from tests.services.test_team_access import teams

    # Preserve the real coach's eligibility while making the operator purely administrative.
    uid = platform_users["accounts"]["admin"].id
    first, _ = teams(db, uid)
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == uid))
    db.add(UserRole(user_id=uid, role_id=ROLES["administration"].id))
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    body = identity_body(platform_users, identity_selection={"primary_identity": identity})
    if identity == "team_member":
        body.update(coach_person_ids=[], team_id=str(first.id))
    response = db_client.post(f"/api/v1/personnel-intake/{mode}", json=body,
        headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    person_id = UUID(response.json()["person_id"])
    db.expire_all()
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None
    assert db.scalar(select(IntakeRoleIntent.id).where(IntakeRoleIntent.person_id == person_id)) is None
    if identity == "student":
        assert db.get(StudentProfile, person_id) is not None
        assert db.scalar(select(CoachPersonResponsibility.id).where(
            CoachPersonResponsibility.person_id == person_id)) is not None
    else:
        assert db.scalar(select(TeamMembership.id).where(
            TeamMembership.person_id == person_id, TeamMembership.team_id == first.id)) is not None
