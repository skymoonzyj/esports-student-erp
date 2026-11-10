"""Management identity input exercises the real atomic intake boundary."""

import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.auth.models import UserAccount
from app.identity.models import Person, StaffProfile, StudentProfile
from app.personnel_intake.identity_models import IntakeIdentitySelection
from app.personnel_intake.models import AssistedApproval
from app.personnel_intake.role_grants import latest_plan
from app.team_management.person_staff_models import PersonTeamStaffPosition
from tests.api.test_intake_activation_roles import payload
from tests.api.test_people import sign_in
from tests.services.test_team_access import teams


def identity_body(users, **changes):
    body = payload(users)
    body.pop("kind")
    body.pop("activation_role_keys")
    body["identity_selection"] = {
        "primary_identity": "student", "concurrent_identities": ["coach"]
    }
    body["staff_teams"] = []
    body.update(changes)
    return body


@pytest.mark.parametrize("mode", ["direct-create", "direct-create-pending"])
def test_management_identity_creates_both_profiles_and_real_staff_positions(
    db, db_client, platform_users, test_settings, mode
):
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    body = identity_body(platform_users, staff_teams=[
        {"staff_role": "coach", "team_ids": [str(second.id), str(first.id)]}
    ])
    result = db_client.post(f"/api/v1/personnel-intake/{mode}", json=body,
                            headers={"X-CSRF-Token": csrf})
    assert result.status_code == 201, result.text
    person_id = UUID(result.json()["person_id"])
    db.expire_all()
    assert db.get(StudentProfile, person_id) is not None
    assert db.get(StaffProfile, person_id).staff_type == "coach"
    evidence = db.get(IntakeIdentitySelection, person_id)
    assert evidence.primary_identity == "student"
    expected_summary = {
        "identity_selection": {"primary_identity": "student", "concurrent_identities": ["coach"],
                               "primary_staff_identity": None},
        "staff_teams": [{"staff_role": "coach", "team_ids": sorted([str(first.id), str(second.id)])}],
    }
    assert evidence.operation_summary == expected_summary
    summary_bytes = json.dumps(expected_summary, ensure_ascii=True, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
    expected_digest = hmac.new(
        base64.urlsafe_b64decode(test_settings.field_lookup_key.get_secret_value()),
        b"intake-identity-selection\x00" + summary_bytes,
        hashlib.sha256,
    ).hexdigest()
    assert evidence.operation_digest == expected_digest
    approval = db.get(AssistedApproval, evidence.origin_approval_id)
    plan = latest_plan(db, person_id)
    assert plan.role_keys == ["coach"]
    assert plan.operation_digest == approval.operation_digest
    assert evidence.operation_digest != approval.operation_digest
    assert db.get(Person, person_id).team_allocation_status == "assigned"
    positions = list(db.scalars(select(PersonTeamStaffPosition).where(
        PersonTeamStaffPosition.person_id == person_id)))
    assert {(p.team_id, p.staff_role) for p in positions} == {
        (first.id, "coach"), (second.id, "coach")}
    assert all(p.account_assignment_id is None for p in positions)
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None
    body["staff_teams"][0]["team_ids"].reverse()
    replay = db_client.post(f"/api/v1/personnel-intake/{mode}", json=body,
                            headers={"X-CSRF-Token": csrf})
    assert replay.status_code == 201 and replay.json() == result.json()
    db.expire_all()
    replayed_evidence = db.get(IntakeIdentitySelection, person_id)
    assert replayed_evidence.operation_digest == expected_digest
    assert replayed_evidence.operation_summary == expected_summary
    body["staff_teams"][0]["team_ids"] = [str(first.id)]
    conflict = db_client.post(f"/api/v1/personnel-intake/{mode}", json=body,
                              headers={"X-CSRF-Token": csrf})
    assert conflict.status_code == 409


@pytest.mark.parametrize("change", [
    {"kind": "player"}, {"activation_role_keys": []},
    {"staff_teams": [{"staff_role": "manager", "team_ids": [str(uuid4())]}]},
])
def test_management_rejects_conflicting_legacy_or_unselected_identity(
    db_client, platform_users, change
):
    csrf = sign_in(db_client, platform_users, "admin")
    result = db_client.post("/api/v1/personnel-intake/direct-create",
        json=identity_body(platform_users, **change), headers={"X-CSRF-Token": csrf})
    assert result.status_code == 422


def test_illegal_staff_team_rolls_back_whole_intake(db, db_client, platform_users):
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    before = db.scalar(select(func.count()).select_from(AssistedApproval))
    body = identity_body(platform_users, staff_teams=[
        {"staff_role": "coach", "team_ids": [str(first.id), str(uuid4())]}
    ])
    result = db_client.post("/api/v1/personnel-intake/direct-create", json=body,
                            headers={"X-CSRF-Token": csrf})
    assert result.status_code in {403, 404, 409}
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(AssistedApproval)) == before
    assert db.scalar(select(func.count()).select_from(IntakeIdentitySelection)) == 0
    assert db.scalar(select(func.count()).select_from(PersonTeamStaffPosition)) == 0


def test_management_context_has_pinned_contract(db_client, platform_users):
    sign_in(db_client, platform_users, "admin")
    response = db_client.get("/api/v1/personnel-intake/management-identity-context")
    assert response.status_code == 200
    assert set(response.json()) == {
        "role_options", "can_assign_staff_teams", "staff_team_reason", "staff_team_options"}
    assert len(response.json()["role_options"]) == 13
    assert response.json()["can_assign_staff_teams"] is True


def test_pending_not_required_rejects_real_staff_relationship(db, db_client, platform_users):
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    response = db_client.post("/api/v1/personnel-intake/direct-create-pending",
        json=identity_body(platform_users, team_allocation_status="not_required", staff_teams=[
            {"staff_role": "coach", "team_ids": [str(first.id)]}]),
        headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422


@pytest.mark.parametrize("mode", ["direct-create", "direct-create-pending"])
@pytest.mark.parametrize("primary,concurrent", [
    ("team_member", ["team_manager"]),
    ("coach", ["assistant_coach", "team_manager"]),
])
def test_player_manager_and_all_staff_roles_keep_separate_business_relationships(
    db, db_client, platform_users, mode, primary, concurrent
):
    from app.team_management.models import TeamMembership
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    groups = [{"staff_role": "manager", "team_ids": [str(first.id), str(second.id)]}]
    if primary == "coach":
        groups.extend({"staff_role": role, "team_ids": [str(first.id), str(second.id)]}
                      for role in ("coach", "assistant_coach"))
    body = identity_body(platform_users, coach_person_ids=[],
        identity_selection={"primary_identity": primary, "concurrent_identities": concurrent},
        staff_teams=groups, team_id=str(first.id) if primary == "team_member" else None)
    response = db_client.post(f"/api/v1/personnel-intake/{mode}", json=body,
                             headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    person_id = UUID(response.json()["person_id"])
    db.expire_all()
    assert db.get(StaffProfile, person_id).staff_type == ("team_manager" if primary == "team_member" else "coach")
    assert db.scalar(select(func.count()).select_from(TeamMembership)
                     .where(TeamMembership.person_id == person_id)) == (1 if primary == "team_member" else 0)
    assert db.scalar(select(func.count()).select_from(PersonTeamStaffPosition)
                     .where(PersonTeamStaffPosition.person_id == person_id)) == (2 if primary == "team_member" else 6)


@pytest.mark.parametrize("identity", ["student", "team_member"])
def test_strict_required_affiliations_remain_required_but_pending_can_omit(
    db_client, platform_users, identity
):
    csrf = sign_in(db_client, platform_users, "admin")
    body = identity_body(platform_users, coach_person_ids=[],
                         identity_selection={"primary_identity": identity})
    strict = db_client.post("/api/v1/personnel-intake/direct-create", json=body,
                            headers={"X-CSRF-Token": csrf})
    assert strict.status_code == 409  # Existing missing-affiliation business conflict.
    pending = db_client.post("/api/v1/personnel-intake/direct-create-pending", json=body,
                             headers={"X-CSRF-Token": csrf})
    assert pending.status_code == 201, pending.text


def test_ordinary_submissions_reject_management_identity_fields(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "coach")
    body = identity_body(platform_users, kind="student")
    body.pop("idempotency_key")
    response = db_client.post("/api/v1/personnel-intake/requests", json=body,
                              headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422
    context = db_client.get("/api/v1/personnel-intake/management-identity-context")
    assert context.status_code == 403


def test_empty_management_staff_teams_preserves_legacy_replay_and_no_identity_evidence(
    db, db_client, platform_users
):
    csrf = sign_in(db_client, platform_users, "admin")
    body = payload(platform_users)
    first = db_client.post("/api/v1/personnel-intake/direct-create", json=body,
                           headers={"X-CSRF-Token": csrf})
    assert first.status_code == 201
    body["staff_teams"] = []
    replay = db_client.post("/api/v1/personnel-intake/direct-create", json=body,
                            headers={"X-CSRF-Token": csrf})
    assert replay.status_code == 201 and replay.json() == first.json()
    assert db.get(IntakeIdentitySelection, UUID(first.json()["person_id"])) is None
    assert db.scalar(select(func.count()).select_from(PersonTeamStaffPosition)) == 0


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("extra", ["identity_selection", "staff_teams", "activation_role_keys"])
def test_corrections_reject_all_management_authority_fields(db_client, platform_users, pending, extra):
    csrf = sign_in(db_client, platform_users, "admin")
    body = identity_body(platform_users)
    route = "direct-create-pending" if pending else "direct-create"
    created = db_client.post(f"/api/v1/personnel-intake/{route}", json=body,
                             headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201
    for field in ("identity_selection", "staff_teams", "coach_person_ids", "team_id"):
        body.pop(field, None)
    body.update(expected_version=1, reason="合成资料核实", idempotency_key=str(uuid4()))
    body[extra] = {"primary_identity": "coach"} if extra == "identity_selection" else []
    suffix = "pending-corrections" if pending else "corrections"
    response = db_client.post(f"/api/v1/personnel-intake/people/{created.json()['person_id']}/{suffix}",
                             json=body, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422


@pytest.mark.parametrize("extra_coach", [False, True])
def test_new_baseline_identity_does_not_require_extra_role_delegation(db, db_client, platform_users, extra_coach):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    db.add(UserPermissionOverride(user_id=platform_users["accounts"]["admin"].id,
        permission_id=PERMISSIONS["permissions.manage"].id, effect="deny", scope_kind="global"))
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    body = identity_body(platform_users, identity_selection={"primary_identity": "student",
                         "concurrent_identities": ["coach"] if extra_coach else []})
    response = db_client.post("/api/v1/personnel-intake/direct-create", json=body,
                              headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    person_id = UUID(response.json()["person_id"])
    assert latest_plan(db, person_id) is None
    if extra_coach:
        intent = db_client.get(f"/api/v1/personnel-intake/people/{person_id}/role-intent")
        assert intent.json()["role_keys"] == ["coach"]
        assert intent.json()["status"] == "pending"
    if extra_coach:
        assert db.scalar(select(func.count()).select_from(IntakeIdentitySelection)) == 1
        assert db.scalar(select(func.count()).select_from(AssistedApproval)) == 1
