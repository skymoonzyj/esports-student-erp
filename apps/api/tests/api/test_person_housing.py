from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.housing.models import HousingRoom, HousingStay
from app.housing.seed_inventory import INITIAL_ROOMS
from app.identity.models import Person, StaffProfile, StudentProfile
from app.personnel_intake.models import CoachPersonResponsibility
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.api.test_housing import sign_in
from tests.api.test_housing_occupancy import verified_housing_emails  # noqa: F401


def pending_passport_person(db, platform_users):
    marker = uuid4().hex
    person = Person(
        full_name="待补护照住宿人员",
        nickname="保留昵称",
        profile_notes="保留原备注",
        identity_ciphertext=f"pending-passport-{marker}",
        identity_digest=f"pending-passport-{marker}",
        passport_number_digest=f"passport-number-{marker}",
        document_type="passport",
        intake_mode="pending",
        sex=None,
        date_of_birth=None,
    )
    db.add(person)
    db.flush()
    db.add(StaffProfile(person_id=person.id, staff_type="other"))
    db.commit()
    platform_users["people"]["pending_passport"] = person
    return person


def test_pending_passport_unknown_sex_preserves_authorized_existing_stay(
    db, db_client, platform_users
):
    person = pending_passport_person(db, platform_users)
    target = stay(db, platform_users, "pending_passport")

    response = get(db_client, platform_users, person_key="pending_passport")

    assert response.status_code == 200
    data = response.json()
    assert data["current"]["room_name"] == "本人房间"
    assert data["management_stay"]["id"] == str(target.id)
    assert data["management_stay"]["sex"] is None
    assert data["management_stay"]["allowed_actions"]["transfer"] is False
    assert data["management_stay"]["allowed_actions"]["request_checkout"] is True
    assert data["allowed_actions"]["create_stay"] is False
    db.refresh(person)
    assert person.nickname == "保留昵称"
    assert person.profile_notes == "保留原备注"


def test_unknown_sex_existing_resident_prevents_room_option_for_known_sex_person(
    db, platform_users
):
    from app.housing.access import HousingAccessService
    from app.housing.person_router import _rooms

    pending_passport_person(db, platform_users)
    target = stay(db, platform_users, "pending_passport")
    room = db.get(HousingRoom, target.room_id)
    room.verification_status = "verified"
    db.commit()
    access = HousingAccessService(db, platform_users["accounts"]["admin"].id, datetime.now(UTC))

    options = _rooms(db, access, platform_users["people"]["self_student"])

    assert room.id not in {option[0].id for option in options}


def grant(db, platform_users, key, permission, *, effect="allow", scope="global", scope_id=None):
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"][key].id,
            permission_id=PERMISSIONS[permission].id,
            effect=effect,
            scope_kind=scope,
            scope_id=scope_id,
        )
    )
    db.commit()


def stay(db, platform_users, person_key="self_student", *, status="checked_in", room_id=None):
    room_id = room_id or INITIAL_ROOMS[0].id
    now = datetime.now(UTC)
    row = HousingStay(
        person_id=platform_users["people"][person_key].id,
        room_id=room_id,
        stay_purpose="permanent",
        duration_mode="fixed_term",
        planned_check_in_at=now + timedelta(days=2)
        if status == "planned"
        else now - timedelta(days=3),
        planned_check_out_at=now + timedelta(days=7),
        actual_check_in_at=None if status == "planned" else now - timedelta(days=3),
        status=status,
        room_name_snapshot="本人房间",
        floor_name_snapshot="测试楼层",
        building_name_snapshot="测试楼栋",
        notes="PRIVATE_STAY_NOTE",
        created_by_user_id=platform_users["accounts"]["admin"].id,
        checked_in_by_user_id=None
        if status == "planned"
        else platform_users["accounts"]["admin"].id,
        version=1,
    )
    db.add(row)
    db.commit()
    return row


def get(client, platform_users, key="admin", person_key="self_student", suffix=""):
    sign_in(client, platform_users, key)
    return client.get(f"/api/v1/people/{platform_users['people'][person_key].id}/housing{suffix}")


def test_exact_person_summary_does_not_read_roommates(db, db_client, platform_users):
    target = stay(db, platform_users)
    stay(db, platform_users, "coach")
    response = get(db_client, platform_users)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    data = response.json()
    assert data["current"]["room_name"] == "本人房间"
    assert data["current"]["status"] == "checked_in"
    assert data["planned"] is None
    assert data["management_stay"]["id"] == str(target.id)
    assert data["management_stay"]["allowed_actions"]["transfer"] is True
    assert data["allowed_actions"]["create_stay"] is False
    assert "平台教练" not in response.text
    assert "notes" not in data["current"]


@pytest.mark.parametrize("status", ["checked_in", "checkout_pending", "planned"])
def test_reader_narrow_summary_has_no_management_fields(db, db_client, platform_users, status):
    stay(db, platform_users, status=status)
    grant(db, platform_users, "customer_service", "housing.read")
    response = get(db_client, platform_users, "customer_service")
    assert response.status_code == 200
    data = response.json()
    assert data["planned" if status == "planned" else "current"]["status"] == status
    assert data["current" if status == "planned" else "planned"] is None
    assert data["management_stay"] is None
    assert data["allowed_actions"]["create_stay"] is False
    assert "PRIVATE_STAY_NOTE" not in response.text
    assert "person_id" not in response.text


def test_people_read_alone_does_not_grant_housing(db, db_client, platform_users):
    stay(db, platform_users)
    response = get(db_client, platform_users, "customer_service")
    assert response.status_code in {403, 404}
    assert response.headers["cache-control"] == "no-store, private"


def test_housing_read_cannot_bypass_exact_people_deny(db, db_client, platform_users):
    stay(db, platform_users)
    grant(db, platform_users, "customer_service", "housing.read")
    grant(
        db,
        platform_users,
        "customer_service",
        "people.read",
        effect="deny",
        scope="person",
        scope_id=platform_users["people"]["self_student"].id,
    )
    assert get(db_client, platform_users, "customer_service").status_code in {403, 404}


def test_housing_scope_and_explicit_deny_are_enforced(db, db_client, platform_users):
    target = stay(db, platform_users)
    room = db.get(HousingRoom, target.room_id)
    grant(
        db,
        platform_users,
        "customer_service",
        "housing.read",
        scope="building",
        scope_id=room.building_id,
    )
    assert get(db_client, platform_users, "customer_service").status_code == 200
    grant(
        db,
        platform_users,
        "customer_service",
        "housing.read",
        effect="deny",
        scope="floor",
        scope_id=room.floor_id,
    )
    response = get(db_client, platform_users, "customer_service")
    assert response.status_code in {403, 404}
    assert "本人房间" not in response.text


def test_current_coach_responsibility_is_readonly_even_with_global_housing_grants(
    db, db_client, platform_users
):
    stay(db, platform_users)
    coach = platform_users["accounts"]["coach"]
    person = platform_users["people"]["self_student"]
    responsibility = CoachPersonResponsibility(
        coach_person_id=coach.person_id, person_id=person.id, created_by_user_id=coach.id
    )
    db.add(responsibility)
    db.commit()
    grant(db, platform_users, "coach", "people.read", scope="person", scope_id=person.id)
    grant(db, platform_users, "coach", "housing.read")
    grant(db, platform_users, "coach", "housing.stays.manage")
    response = get(db_client, platform_users, "coach")
    assert response.status_code == 200
    data = response.json()
    assert data["current"]["room_name"] == "本人房间"
    assert data["management_stay"] is None
    assert data["allowed_actions"]["create_stay"] is False
    assert get(db_client, platform_users, "coach", suffix="/check-in-options").status_code in {
        403,
        404,
    }
    grant(
        db,
        platform_users,
        "coach",
        "personnel_intake.housing.read",
        effect="deny",
        scope="person",
        scope_id=person.id,
    )
    assert get(db_client, platform_users, "coach").status_code in {403, 404}


def test_ended_coach_responsibility_cannot_read(db, db_client, platform_users):
    coach = platform_users["accounts"]["coach"]
    person = platform_users["people"]["self_student"]
    db.add(
        CoachPersonResponsibility(
            coach_person_id=coach.person_id,
            person_id=person.id,
            created_by_user_id=coach.id,
            ended_at=datetime.now(UTC),
            ended_by_user_id=coach.id,
        )
    )
    db.commit()
    grant(db, platform_users, "coach", "people.read", scope="person", scope_id=person.id)
    assert get(db_client, platform_users, "coach").status_code in {403, 404}


@pytest.mark.usefixtures("verified_housing_emails")
def test_empty_eligible_person_can_arrange_using_only_scoped_room_options(
    db, db_client, platform_users
):
    seed = INITIAL_ROOMS[0]
    room = db.get(HousingRoom, seed.id)
    room.verification_status = "verified"
    db.get(StudentProfile, platform_users["people"]["self_student"].id).status = "active"
    db.commit()
    grant(
        db,
        platform_users,
        "customer_service",
        "housing.read",
        scope="floor",
        scope_id=room.floor_id,
    )
    grant(
        db,
        platform_users,
        "customer_service",
        "housing.stays.manage",
        scope="floor",
        scope_id=room.floor_id,
    )
    response = get(db_client, platform_users, "customer_service")
    assert response.status_code == 200
    assert response.json()["allowed_actions"]["create_stay"] is True
    options = get(db_client, platform_users, "customer_service", suffix="/check-in-options")
    assert options.status_code == 200
    data = options.json()
    assert data["resident"]["person_id"] == str(platform_users["people"]["self_student"].id)
    assert data["resident"]["housing_ready"] is True
    assert {item["id"] for item in data["items"]} == {str(room.id)}
    assert set(data["items"][0]) == {
        "id",
        "room_number",
        "building_name",
        "floor_name",
        "allowed_actions",
    }
    assert "occupants" not in options.text and "notes" not in options.text
    stay(db, platform_users, status="planned")
    assert (
        get(db_client, platform_users, "customer_service").json()["allowed_actions"]["create_stay"]
        is False
    )
    assert get(
        db_client, platform_users, "customer_service", suffix="/check-in-options"
    ).status_code in {403, 404, 409}


def test_ineligible_person_cannot_arrange(db, db_client, platform_users):
    response = get(db_client, platform_users, person_key="adult_without_account")
    assert response.status_code == 200
    assert response.json()["allowed_actions"]["create_stay"] is False
    assert get(
        db_client, platform_users, person_key="adult_without_account", suffix="/check-in-options"
    ).status_code in {403, 404, 409}


def test_deleted_and_missing_people_are_private_not_found(db, db_client, platform_users):
    person = db.scalar(
        select(Person).where(Person.id == platform_users["people"]["self_student"].id)
    )
    person.deleted_at = datetime.now(UTC)
    db.commit()
    sign_in(db_client, platform_users, "admin")
    for person_id in (person.id, uuid4()):
        response = db_client.get(f"/api/v1/people/{person_id}/housing")
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store, private"


def test_history_is_not_current_housing(db, db_client, platform_users):
    ended = stay(db, platform_users)
    admin_id = platform_users["accounts"]["admin"].id
    ended.status = "checked_out"
    ended.actual_check_out_at = datetime.now(UTC) - timedelta(days=1)
    ended.checked_out_by_user_id = admin_id
    db.commit()
    response = get(db_client, platform_users)
    assert response.status_code == 200
    data = response.json()
    assert data["current"] is None and data["planned"] is None
    assert data["management_stay"] is None
    assert "本人房间" not in response.text and "PRIVATE_STAY_NOTE" not in response.text


def test_void_history_does_not_expand_person_housing_dto(db, db_client, platform_users):
    ended = stay(db, platform_users)
    admin_id = platform_users["accounts"]["admin"].id
    ended.status = "checked_out"
    ended.actual_check_out_at = datetime.now(UTC) - timedelta(days=1)
    ended.checked_out_by_user_id = admin_id
    db.commit()
    before = get(db_client, platform_users).json()
    ended.voided_at = datetime.now(UTC)
    ended.voided_by_user_id = admin_id
    ended.void_reason_ciphertext = "opaque-void-reason"
    db.commit()
    response = get(db_client, platform_users)
    assert response.status_code == 200
    after = response.json()
    assert after["current"] == before["current"] is None
    assert after["planned"] == before["planned"] is None
    assert after["management_stay"] == before["management_stay"] is None
    assert "void_info" not in response.text
    assert "void_reason" not in response.text
    assert "本人房间" not in response.text


def test_hidden_current_does_not_become_arrangeable(db, db_client, platform_users):
    target = stay(db, platform_users)
    room = db.get(HousingRoom, target.room_id)
    grant(db, platform_users, "customer_service", "housing.read")
    grant(db, platform_users, "customer_service", "housing.stays.manage")
    grant(
        db,
        platform_users,
        "customer_service",
        "housing.read",
        effect="deny",
        scope="building",
        scope_id=room.building_id,
    )
    assert get(db_client, platform_users, "customer_service").status_code in {403, 404}
    assert get(
        db_client, platform_users, "customer_service", suffix="/check-in-options"
    ).status_code in {403, 404}


def test_read_permission_without_write_never_exposes_check_in_options(
    db, db_client, platform_users
):
    grant(db, platform_users, "customer_service", "housing.read")
    response = get(db_client, platform_users, "customer_service", suffix="/check-in-options")
    assert response.status_code in {403, 404}
    assert response.headers["cache-control"] == "no-store, private"
    assert "resident" not in response.text and "room_number" not in response.text


def test_exact_person_queries_never_select_other_stays(db, db_client, platform_users):
    target = stay(db, platform_users)
    stay(db, platform_users, "coach")
    statements = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany):
        if "housing_stays" in statement and statement.lstrip().startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        response = get(db_client, platform_users)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert statements
    for statement, parameters in statements:
        assert "housing_stays.person_id =" in statement
        assert target.person_id in parameters.values()


def test_approved_unactivated_person_can_arrange_without_account(db, db_client, platform_users):
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    result = approvals(db).direct_create(
        actor(platform_users, "admin"), intake(platform_users, email=None), uuid4()
    )
    room = db.get(HousingRoom, INITIAL_ROOMS[0].id)
    room.verification_status = "verified"
    db.commit()
    sign_in(db_client, platform_users, "admin")
    base = f"/api/v1/people/{result.person_id}/housing"
    response = db_client.get(base)
    assert response.status_code == 200
    assert response.json()["allowed_actions"]["create_stay"] is True
    options = db_client.get(base + "/check-in-options")
    assert options.status_code == 200
    resident = options.json()["resident"]
    assert resident["person_id"] == str(result.person_id)
    assert resident["housing_ready"] is True
    assert resident["account_ready"] is False
    assert resident["account_status"] == "not_activated"


@pytest.mark.parametrize(
    "unavailable",
    ["inactive_floor", "inactive_building", "functional", "maintenance", "unverified"],
)
def test_check_in_rooms_must_be_currently_eligible(db, db_client, platform_users, unavailable):
    from app.housing.models import HousingBuilding, HousingFloor
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    result = approvals(db).direct_create(
        actor(platform_users, "admin"), intake(platform_users, email=None), uuid4()
    )
    room = db.get(HousingRoom, INITIAL_ROOMS[0].id)
    room.verification_status = "verified"
    if unavailable == "inactive_floor":
        db.get(HousingFloor, room.floor_id).is_active = False
    elif unavailable == "inactive_building":
        db.get(HousingBuilding, room.building_id).is_active = False
    elif unavailable == "functional":
        room.room_type, room.capacity = "functional", 0
    elif unavailable == "maintenance":
        room.service_status = "maintenance"
    else:
        room.verification_status = "pending_verification"
    db.commit()
    sign_in(db_client, platform_users, "admin")
    base = f"/api/v1/people/{result.person_id}/housing"
    response = db_client.get(base)
    assert response.status_code == 200
    assert response.json()["allowed_actions"]["create_stay"] is False
    assert db_client.get(base + "/check-in-options").status_code in {403, 404}


@pytest.mark.parametrize("conflict", ["full", "gender"])
def test_check_in_options_exclude_current_capacity_and_gender_conflicts(
    db, db_client, platform_users, conflict
):
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    result = approvals(db).direct_create(
        actor(platform_users, "admin"), intake(platform_users, email=None), uuid4()
    )
    seed = next(
        room
        for room in INITIAL_ROOMS
        if room.room_type == ("single" if conflict == "full" else "double")
    )
    room = db.get(HousingRoom, seed.id)
    room.verification_status = "verified"
    db.commit()
    # Approved target is male. The full case uses a same-sex occupant so only
    # capacity can exclude it; gender case leaves spare capacity.
    if conflict == "full":
        db.get(Person, platform_users["people"]["coach"].id).sex = "male"
        db.commit()
    stay(db, platform_users, "coach", room_id=room.id)
    sign_in(db_client, platform_users, "admin")
    base = f"/api/v1/people/{result.person_id}/housing"
    response = db_client.get(base)
    assert response.status_code == 200
    assert response.json()["allowed_actions"]["create_stay"] is False
    assert db_client.get(base + "/check-in-options").status_code in {403, 404}


def test_manager_coach_role_cannot_use_owner_fallback_to_bypass_room_deny(
    db, db_client, platform_users
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES

    target = stay(db, platform_users)
    coach = platform_users["accounts"]["coach"]
    db.add(UserRole(user_id=coach.id, role_id=ROLES["admin"].id))
    db.add(
        CoachPersonResponsibility(
            coach_person_id=coach.person_id, person_id=target.person_id, created_by_user_id=coach.id
        )
    )
    db.commit()
    grant(db, platform_users, "coach", "people.read")
    grant(db, platform_users, "coach", "housing.read")
    grant(
        db,
        platform_users,
        "coach",
        "housing.read",
        effect="deny",
        scope="building",
        scope_id=db.get(HousingRoom, target.room_id).building_id,
    )
    response = get(db_client, platform_users, "coach")
    assert response.status_code in {403, 404}
    assert "本人房间" not in response.text


@pytest.mark.parametrize("ended", [False, True])
def test_team_manager_requires_current_ownership_and_remains_readonly(
    db, db_client, platform_users, ended
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.models import TeamManagerAssignment, TeamMembership
    from tests.services.test_team_access import teams

    target = stay(db, platform_users)
    user = platform_users["accounts"]["customer_service"]
    admin_id = platform_users["accounts"]["admin"].id
    team, _ = teams(db, admin_id)
    db.add(UserRole(user_id=user.id, role_id=ROLES["team_manager"].id))
    assignment = TeamManagerAssignment(
        account_id=user.id, team_id=team.id, created_by_user_id=admin_id
    )
    db.add(assignment)
    db.add(
        TeamMembership(
            person_id=target.person_id,
            team_id=team.id,
            joined_on=datetime.now(UTC).date() - timedelta(days=2),
            created_by_user_id=admin_id,
        )
    )
    db.flush()
    if ended:
        assignment.ended_at, assignment.end_kind = datetime.now(UTC), "unassigned"
    db.commit()
    response = get(db_client, platform_users, "customer_service")
    if ended:
        assert response.status_code in {403, 404}
    else:
        assert response.status_code == 200
        assert response.json()["current"]["room_name"] == "本人房间"
        assert response.json()["management_stay"] is None
        assert response.json()["allowed_actions"]["create_stay"] is False
        assert "PRIVATE_STAY_NOTE" not in response.text


@pytest.mark.parametrize(
    "role_key,staff_role",
    [("team_manager", "manager"), ("coach", "coach"), ("assistant_coach", "assistant_coach")],
)
def test_new_staff_binding_exact_housing_summary_is_readonly_and_revocable(
    db, db_client, platform_users, role_key, staff_role
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.models import TeamMembership, TeamStaffAssignment
    from tests.services.test_team_access import teams

    target = stay(db, platform_users)
    user = platform_users["accounts"]["customer_service"]
    creator = platform_users["accounts"]["admin"].id
    team, _ = teams(db, creator)
    db.add(UserRole(user_id=user.id, role_id=ROLES[role_key].id))
    assignment = TeamStaffAssignment(
        account_id=user.id, team_id=team.id, staff_role=staff_role, created_by_user_id=creator
    )
    db.add_all(
        [
            assignment,
            TeamMembership(
                person_id=target.person_id,
                team_id=team.id,
                joined_on=datetime.now(UTC).date(),
                created_by_user_id=creator,
            ),
        ]
    )
    db.commit()
    for permission in (
        "housing.read",
        "housing.stays.manage",
        "housing.transfers.manage",
        "housing.checkouts.manage",
    ):
        grant(db, platform_users, "customer_service", permission)
    response = get(db_client, platform_users, "customer_service")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    body = response.json()
    assert body["current"]["room_name"] == "本人房间"
    assert body["management_stay"] is None
    assert body["allowed_actions"]["create_stay"] is False
    assert "PRIVATE_STAY_NOTE" not in response.text
    assert "occupants" not in response.text and "phone" not in response.text
    assert get(
        db_client, platform_users, "customer_service", suffix="/check-in-options"
    ).status_code in {403, 404}
    assignment.ended_at, assignment.end_kind = datetime.now(UTC), "unassigned"
    db.commit()
    assert get(db_client, platform_users, "customer_service").status_code in {403, 404}


def test_permission_revocation_changes_revision_and_management_actions(
    db, db_client, platform_users
):
    stay(db, platform_users)
    grant(db, platform_users, "customer_service", "housing.read")
    before = get(db_client, platform_users, "customer_service").json()
    grant(db, platform_users, "customer_service", "housing.transfers.manage")
    allowed = get(db_client, platform_users, "customer_service").json()
    assert allowed["management_stay"]["allowed_actions"]["transfer"] is True
    assert allowed["access_revision"] != before["access_revision"]
    override = db.scalar(
        select(UserPermissionOverride).where(
            UserPermissionOverride.user_id == platform_users["accounts"]["customer_service"].id,
            UserPermissionOverride.permission_id == PERMISSIONS["housing.transfers.manage"].id,
        )
    )
    db.delete(override)
    db.commit()
    revoked = get(db_client, platform_users, "customer_service").json()
    assert revoked["management_stay"] is None
    assert revoked["access_revision"] != allowed["access_revision"]


def test_housing_person_reads_audit_success_and_denial_without_payload(
    db, db_client, platform_users
):
    from app.audit.models import AuditEvent

    stay(db, platform_users)
    assert get(db_client, platform_users).status_code == 200
    assert get(db_client, platform_users, "customer_service").status_code in {403, 404}
    rows = list(db.scalars(select(AuditEvent).where(AuditEvent.action == "housing.person.read")))
    assert {row.outcome for row in rows} == {"allowed", "denied"}
    assert "本人房间" not in str([row.event_metadata for row in rows])
    assert "PRIVATE_STAY_NOTE" not in str([row.event_metadata for row in rows])


def test_dialog_revision_matches_real_existing_housing_options(db, db_client, platform_users):
    target = stay(db, platform_users)
    sign_in(db_client, platform_users, "admin")
    summary = db_client.get(f"/api/v1/people/{target.person_id}/housing")
    assert summary.status_code == 200
    transfer = db_client.get(f"/api/v1/housing/stays/{target.id}/transfer-options")
    assert transfer.status_code == 200
    cohort = db_client.get(f"/api/v1/housing/rooms/{target.room_id}/cohort-options")
    assert cohort.status_code == 200
    data = summary.json()
    assert data["housing_access_revision"] == transfer.json()["access_revision"]
    assert data["housing_access_revision"] == cohort.json()["access_revision"]
    assert data["access_revision"] != data["housing_access_revision"]


def test_person_housing_audit_failure_is_private_and_fails_closed(
    db_client, platform_users, monkeypatch
):
    from app.audit.service import AuditMetadataRejected
    from app.housing import api_support

    sign_in(db_client, platform_users, "admin")

    def reject(*_args, **_kwargs):
        raise AuditMetadataRejected("simulated audit failure")

    monkeypatch.setattr(api_support.AuditService, "append", reject)
    response = db_client.get(
        f"/api/v1/people/{platform_users['people']['self_student'].id}/housing"
    )
    assert response.status_code == 500
    assert response.json()["code"] == "housing_audit_failed"
    assert response.headers["cache-control"] == "no-store, private"
    assert "current" not in response.text


def test_summary_availability_query_is_bounded(db, db_client, platform_users):
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    result = approvals(db).direct_create(
        actor(platform_users, "admin"), intake(platform_users, email=None), uuid4()
    )
    db.get(HousingRoom, INITIAL_ROOMS[0].id).verification_status = "verified"
    db.commit()
    statements = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany):
        if "FROM housing_rooms JOIN housing_buildings" in statement:
            statements.append((statement, parameters))

    sign_in(db_client, platform_users, "admin")
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        response = db_client.get(f"/api/v1/people/{result.person_id}/housing")
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert response.json()["allowed_actions"]["create_stay"] is True
    assert len(statements) == 1
    statement, parameters = statements[0]
    assert "LIMIT" in statement
    assert parameters["param_1"] == 1
