from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.auth.models import UserAccount
from app.identity.models import Person
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from tests.api.conftest import make_cn_id
from tests.api.test_people import sign_in
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor, intake
from tests.services.test_team_access import teams


def approved(db, users, team, kind="player", sequence=321):
    return approvals(db).direct_create(
        actor(users, "admin"),
        intake(
            users,
            kind=kind,
            team_id=team.id if kind != "student" else None,
            coach_person_ids=[] if kind == "player" else [users["people"]["coach"].id],
            identity_number=make_cn_id("20000102", sequence),
            email=f"projection-{sequence}@example.com",
        ),
        uuid4(),
    )


@pytest.mark.parametrize("kind", ["player", "both"])
def test_accountless_approved_player_kind_filter_counts_and_detail(
    db, db_client, platform_users, kind
):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    result = approved(db, platform_users, first, kind)
    sign_in(db_client, platform_users, "admin")
    response = db_client.get("/api/v1/people", params={"role": "team_member", "page_size": 1})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["id"] == str(result.person_id)
    assert (item["assisted_kind"], item["account_status"], item["roles"]) == (
        kind,
        "not_activated",
        [],
    )
    assert item["person_types"] == (["student"] if kind == "both" else [])
    counts = db_client.get("/api/v1/people/counts").json()
    assert counts["team_member"] == 1 and counts["team_manager"] == 0
    detail = db_client.get(f"/api/v1/people/{result.person_id}").json()
    assert (detail["assisted_kind"], detail["account_status"]) == (kind, "not_activated")


def test_role_overlap_deduplicates_and_deleted_person_is_excluded(db, db_client, platform_users):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    result = approved(db, platform_users, first, "both")
    account = UserAccount(
        person_id=result.person_id,
        password_hash="synthetic",
        status="disabled",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    db.add(UserRole(user_id=account.id, role_id=ROLES["team_member"].id))
    db.flush()
    sign_in(db_client, platform_users, "admin")
    page = db_client.get("/api/v1/people", params={"role": "team_member"}).json()
    assert page["total"] == 1 and len(page["items"]) == 1
    assert page["items"][0]["account_status"] == "disabled"
    assert db_client.get("/api/v1/people/counts").json()["team_member"] == 1
    db.get(Person, result.person_id).deleted_at = datetime.now(UTC)
    db.flush()
    assert db_client.get("/api/v1/people", params={"role": "team_member"}).json()["total"] == 0
    assert db_client.get("/api/v1/people/counts").json()["team_member"] == 0
    roster = db_client.get(f"/api/v1/team-management/teams/{first.id}/memberships").json()
    assert (roster["total"], roster["items"]) == (0, [])
    listed = db_client.get("/api/v1/team-management/teams").json()
    assert next(t for t in listed["items"] if t["id"] == str(first.id))["member_count"] == 0


def test_metadata_batch_is_bounded_safe_and_legacy_is_unchanged(db, platform_users, monkeypatch):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    ids = [approved(db, platform_users, first, sequence=n).person_id for n in (321, 323, 325)]
    identity = platform_users["identity_service"]
    statements = []

    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    def no_decrypt(*args, **kwargs):
        pytest.fail("display metadata must not decrypt facts")

    monkeypatch.setattr(identity.cipher, "decrypt", no_decrypt)
    # A dedicated batch boundary lets us assert its SQL budget without counting
    # existing authorized directory masks/readiness projections.
    assert hasattr(identity, "_metadata_for"), "bounded metadata batch is missing"
    db.connection()  # Establish the fixture SAVEPOINT outside the SELECT budget.
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        metadata = identity._metadata_for([*ids, platform_users["people"]["admin"].id])
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert all(metadata[pid] == ("player", "not_activated") for pid in ids)
    assert metadata[platform_users["people"]["admin"].id] == (None, "active")
    assert not any(word in statements[0] for word in ("snapshot_ciphertext", "payload_ciphertext"))
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == ids[0])) is None


def test_current_roster_and_count_omit_deleted_people_but_keep_accountless(
    db, db_client, platform_users
):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    gone = approved(db, platform_users, first, sequence=321)
    remaining = approved(db, platform_users, first, sequence=323)
    db.get(Person, gone.person_id).deleted_at = datetime.now(UTC)
    db.flush()
    sign_in(db_client, platform_users, "admin")
    roster = db_client.get(f"/api/v1/team-management/teams/{first.id}/memberships").json()
    assert roster["total"] == 1
    assert [row["person_id"] for row in roster["items"]] == [str(remaining.person_id)]
    assert roster["items"][0]["account_status"] == "not_activated"
    listed = db_client.get("/api/v1/team-management/teams").json()
    assert next(t for t in listed["items"] if t["id"] == str(first.id))["member_count"] == 1


def test_assisted_label_never_grants_team_visibility(db, db_client, platform_users):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    first, _ = teams(db, actor(platform_users, "admin").account.id)
    result = approved(db, platform_users, first)
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    sign_in(db_client, platform_users, "admin")
    item = db_client.get("/api/v1/people", params={"role": "team_member"}).json()["items"][0]
    assert item["id"] == str(result.person_id) and item["assisted_kind"] == "player"
    assert item["team"] == {"visibility": "denied", "current": None}


def test_combined_legacy_profile_batch_preserves_order_and_unique_types(db, platform_users):
    from app.identity.models import GuardianProfile, StaffProfile, VisitorProfile

    person_id = platform_users["people"]["self_student"].id
    db.add_all(
        [
            GuardianProfile(person_id=person_id),
            StaffProfile(person_id=person_id, staff_type="other"),
            VisitorProfile(person_id=person_id, visit_reason="隔离投影核验"),
        ]
    )
    db.flush()
    identity = platform_users["identity_service"]
    statements = []

    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    staff_types = {}
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        types = identity._types_for([person_id, person_id], staff_types=staff_types)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert types == {person_id: ["student", "guardian", "staff", "visitor"]}
    assert staff_types == {person_id: "other"}
    assert len(statements) == 3
    assert identity.get_person(person_id).person_types == [
        "student",
        "guardian",
        "staff",
        "visitor",
    ]


@pytest.mark.parametrize("status", ["active", "disabled"])
def test_legacy_account_status_is_additive_and_does_not_synthesize_kind(
    db, db_client, platform_users, status
):
    account = platform_users["accounts"]["self_student"]
    account.status = status
    db.flush()
    sign_in(db_client, platform_users, "admin")
    response = db_client.get(f"/api/v1/people/{account.person_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["assisted_kind"] is None and data["account_status"] == status
    assert data["person_types"] == ["student"]
