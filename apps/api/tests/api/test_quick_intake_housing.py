from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.auth.models import UserAccount
from app.housing.models import HousingStay
from app.identity.models import Person
from tests.api.conftest import make_cn_id
from tests.api.test_housing_occupancy import occupancy_room as _occupancy_room
from tests.api.test_housing_occupancy import verified_housing_emails  # noqa: F401
from tests.api.test_intake_role_intents import administration
from tests.api.test_people import sign_in

BASE = "/api/v1/personnel-intake/quick-import"


@pytest.fixture
def occupancy_room(db, request):
    yield from _occupancy_room.__wrapped__(db, request.getfixturevalue("verified_housing_emails"))


def line(room, *, sequence=721, job="助教", arrival="", departure="", phone="13800000721"):
    return "\t".join([room.room_number, "测试导入甲", arrival, job, departure,
                       make_cn_id("19900101", sequence), phone])


def setup_actor(db, client, users):
    administration(db, users)
    return {"X-CSRF-Token": sign_in(client, users, "coach")}


def preview(client, headers, text):
    return client.post(BASE + "/preview", json={"text": text}, headers=headers)


def counts(db):
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Person, HousingStay))


def test_preview_is_private_readonly_then_atomic_adult_without_email(db, db_client, platform_users, occupancy_room):
    headers = setup_actor(db, db_client, platform_users)
    before = counts(db)
    response = preview(db_client, headers, line(occupancy_room))
    assert response.status_code == 200, response.text
    assert counts(db) == before
    assert "no-store" in response.headers["cache-control"]
    row = response.json()["rows"][0]
    assert row["errors"] == []
    assert row["identity_label"] == "助教"
    assert make_cn_id("19900101", 721) not in response.text
    assert "13800000721" not in response.text
    key = str(uuid4())
    result = db_client.post(BASE + "/commit", headers=headers, json={"ticket": row["ticket"], "idempotency_key": key})
    assert result.status_code == 201, result.text
    data = result.json()
    assert data["stay_status"] == "checked_in"
    assert data["role_pending"] is True
    assert counts(db) == (before[0] + 1, before[1] + 1)
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == UUID(data["person_id"]))) is None
    replay = db_client.post(BASE + "/commit", headers=headers, json={"ticket": row["ticket"], "idempotency_key": key})
    assert replay.status_code == 201
    assert replay.json() == data
    assert counts(db) == (before[0] + 1, before[1] + 1)


@pytest.mark.parametrize("key", ["coach", "self_student", "guardian"])
def test_other_roles_cannot_preview(db, db_client, platform_users, occupancy_room, key):
    headers = {"X-CSRF-Token": sign_in(db_client, platform_users, key)}
    response = preview(db_client, headers, line(occupancy_room))
    assert response.status_code == 403


def test_room_denial_and_unverified_room_get_no_ticket(db, db_client, platform_users, occupancy_room):
    headers = setup_actor(db, db_client, platform_users)
    occupancy_room.verification_status = "pending_verification"
    db.commit()
    result = preview(db_client, headers, line(occupancy_room))
    assert result.status_code == 200, result.text
    assert result.json()["rows"][0]["ticket"] is None
    assert result.json()["rows"][0]["errors"]


def test_changed_room_rolls_back_profile_and_repeat_can_recover(db, db_client, platform_users, occupancy_room):
    headers = setup_actor(db, db_client, platform_users)
    row = preview(db_client, headers, line(occupancy_room)).json()["rows"][0]
    before = counts(db)
    occupancy_room.service_status = "maintenance"
    db.commit()
    body = {"ticket": row["ticket"], "idempotency_key": str(uuid4())}
    failed = db_client.post(BASE + "/commit", headers=headers, json=body)
    assert failed.status_code == 409, failed.text
    assert counts(db) == before
    occupancy_room.service_status = "available"
    db.commit()
    assert db_client.post(BASE + "/commit", headers=headers, json=body).status_code == 201


def test_duplicate_batch_and_missing_guardian_are_deferred(db, db_client, platform_users, occupancy_room):
    headers = setup_actor(db, db_client, platform_users)
    text = line(occupancy_room)
    response = preview(db_client, headers, text + "\n" + text)
    assert response.status_code == 200, response.text
    assert all(row["ticket"] is None and row["errors"] for row in response.json()["rows"])
    minor = text.replace(make_cn_id("19900101", 721), make_cn_id("20150101", 721))
    row = preview(db_client, headers, minor).json()["rows"][0]
    assert row["ticket"] is None
    assert any("监护" in error for error in row["errors"])


def test_invalid_payload_never_echoes_sensitive_input(db, db_client, platform_users):
    headers = setup_actor(db, db_client, platform_users)
    result = db_client.post(BASE + "/preview", headers=headers, json={"text": {"secret": "sensitive-value"}})
    assert result.status_code == 422
    assert "sensitive-value" not in result.text


def test_tampered_ticket_rejected_and_no_profile_written(db, db_client, platform_users):
    headers = setup_actor(db, db_client, platform_users)
    before = counts(db)
    response = db_client.post(BASE + "/commit", headers=headers,
                             json={"ticket": "not-a-ticket", "idempotency_key": str(uuid4())})
    assert response.status_code == 422
    assert counts(db) == before


def test_invalid_id_and_phone_are_safe_row_errors(db, db_client, platform_users, occupancy_room):
    headers = setup_actor(db, db_client, platform_users)
    response = preview(db_client, headers, line(occupancy_room).replace(make_cn_id("19900101", 721), "wrong-id"))
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["ticket"] is None
    response = preview(db_client, headers, line(occupancy_room, phone="13800000721 后附说明"))
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["errors"]
    assert response.json()["rows"][0]["ticket"] is None


def test_parse_error_does_not_add_misleading_business_errors(db, db_client, platform_users):
    headers = setup_actor(db, db_client, platform_users)
    before = counts(db)
    response = preview(db_client, headers, "仅有一列，无法确定人员字段")
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["ticket"] is None
    assert len(row["errors"]) == 1
    assert "姓名或岗位文字长度" not in row["errors"][0]
    assert counts(db) == before


@pytest.mark.parametrize("job", ["经理", "无畏契约 经理", "无畏契约 队伍经理"])
def test_explicit_manager_alias_previews_as_team_manager(db, db_client, platform_users, occupancy_room, job):
    headers = setup_actor(db, db_client, platform_users)
    before = counts(db)
    response = preview(db_client, headers, line(occupancy_room, job=job))
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["errors"] == []
    assert row["ticket"]
    assert row["identity_label"] == "队伍经理"
    assert counts(db) == before


@pytest.mark.parametrize("job", ["产品经理", "经理助理", "经理 教练", "经理兼教练"])
def test_manager_substrings_or_multiple_roles_do_not_grant_manager(db, db_client, platform_users, occupancy_room, job):
    headers = setup_actor(db, db_client, platform_users)
    response = preview(db_client, headers, line(occupancy_room, job=job))
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["ticket"] is None
    assert row["errors"]


def test_plain_space_manager_row_previews_then_commits_without_activation(db, db_client, platform_users, occupancy_room):
    from app.core.time import business_today
    from app.personnel_intake.quick_import_models import QuickIntakeReceipt

    headers = setup_actor(db, db_client, platform_users)
    before = counts(db)
    text = " ".join([
        occupancy_room.room_number, "测试空格导入", business_today().isoformat(),
        "无畏契约 经理", make_cn_id("19900101", 721), "13800000721", "常驻",
    ])
    response = preview(db_client, headers, text)
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["errors"] == []
    assert row["full_name"] == "测试空格导入"
    assert row["identity_label"] == "队伍经理"
    assert row["departure_date"] is None
    assert row["ticket"]
    assert counts(db) == before
    result = db_client.post(BASE + "/commit", headers=headers, json={
        "ticket": row["ticket"], "idempotency_key": str(uuid4()),
    })
    assert result.status_code == 201, result.text
    person_id = UUID(result.json()["person_id"])
    stay = db.get(HousingStay, UUID(result.json()["stay_id"]))
    assert stay.person_id == person_id
    assert stay.room_id == occupancy_room.id
    assert stay.planned_check_out_at is None
    assert stay.duration_mode == "long_term"
    assert result.json()["role_pending"] is True
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None
    assert db.scalar(select(QuickIntakeReceipt.person_id).where(QuickIntakeReceipt.person_id == person_id)) == person_id
    assert counts(db) == (before[0] + 1, before[1] + 1)


@pytest.mark.parametrize("separator", ["\t", "|"])
def test_empty_departure_and_trailing_resident_status_batch_is_long_term(
    db, db_client, platform_users, occupancy_room, separator,
):
    headers = setup_actor(db, db_client, platform_users)
    occupancy_room.room_type, occupancy_room.capacity = "double", 2
    db.commit()
    before = counts(db)
    raw = "\n".join(separator.join([
        occupancy_room.room_number, "双人间", name, "2026/11/5", job, "",
        make_cn_id("19900101", sequence), phone, "常驻",
    ]) for name, job, sequence, phone in [
        ("测试经理甲", "无畏契约 经理", 723, "13800000723"),
        (" 测试教练乙", "无畏契约 教练", 725, "13800000725"),
    ])
    response = preview(db_client, headers, raw)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert len(rows) == 2
    assert [row["full_name"] for row in rows] == ["测试经理甲", "测试教练乙"]
    assert [row["identity_label"] for row in rows] == ["队伍经理", "教练"]
    assert all(row["errors"] == [] and row["ticket"] for row in rows)
    assert all(row["departure_date"] is None for row in rows)
    assert counts(db) == before
    for row in rows:
        result = db_client.post(BASE + "/commit", headers=headers, json={
            "ticket": row["ticket"], "idempotency_key": str(uuid4()),
        })
        assert result.status_code == 201, result.text
        person_id = UUID(result.json()["person_id"])
        stay = db.get(HousingStay, UUID(result.json()["stay_id"]))
        assert stay.person_id == person_id and stay.room_id == occupancy_room.id
        assert stay.planned_check_out_at is None and stay.duration_mode == "long_term"
        assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None
    assert counts(db) == (before[0] + 2, before[1] + 2)


def test_full_room_second_row_rollback_keeps_first_and_existing_id_defers(db, db_client, platform_users, occupancy_room):
    headers = setup_actor(db, db_client, platform_users)
    occupancy_room.room_type, occupancy_room.capacity = "single", 1
    db.commit()
    before = counts(db)
    rows = preview(db_client, headers, line(occupancy_room) + "\n" + line(occupancy_room, sequence=723)).json()["rows"]
    assert all(row["ticket"] for row in rows)
    first = db_client.post(BASE + "/commit", headers=headers, json={"ticket": rows[0]["ticket"], "idempotency_key": str(uuid4())})
    assert first.status_code == 201, first.text
    second = db_client.post(BASE + "/commit", headers=headers, json={"ticket": rows[1]["ticket"], "idempotency_key": str(uuid4())})
    assert second.status_code == 409
    assert "床位" in second.json()["detail"]
    assert counts(db) == (before[0] + 1, before[1] + 1)
    existing = preview(db_client, headers, line(occupancy_room)).json()["rows"][0]
    assert existing["ticket"] is None and any("已有档案" in e for e in existing["errors"])


def test_different_preview_same_key_conflicts_and_role_revocation_blocks_replay(db, db_client, platform_users, occupancy_room):
    from app.rbac.models import UserRole
    headers = setup_actor(db, db_client, platform_users)
    row = preview(db_client, headers, line(occupancy_room)).json()["rows"][0]
    other = preview(db_client, headers, line(occupancy_room, sequence=723)).json()["rows"][0]
    body = {"ticket": row["ticket"], "idempotency_key": str(uuid4())}
    assert db_client.post(BASE + "/commit", headers=headers, json=body).status_code == 201
    response = db_client.post(BASE + "/commit", headers=headers, json={**body, "ticket": other["ticket"]})
    assert response.status_code == 409
    assert response.json()["code"] == "quick_intake_key_conflict"
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == platform_users["accounts"]["coach"].id))
    db.commit()
    assert db_client.post(BASE + "/commit", headers=headers, json=body).status_code == 403


def test_new_account_cannot_use_ticket_and_room_deny_prevents_write(db, db_client, platform_users, occupancy_room):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    headers = setup_actor(db, db_client, platform_users)
    row = preview(db_client, headers, line(occupancy_room)).json()["rows"][0]
    body = {"ticket": row["ticket"], "idempotency_key": str(uuid4())}
    other_headers = {"X-CSRF-Token": sign_in(db_client, platform_users, "admin")}
    assert db_client.post(BASE + "/commit", headers=other_headers, json=body).status_code == 403
    headers = {"X-CSRF-Token": sign_in(db_client, platform_users, "coach")}
    db.add(UserPermissionOverride(user_id=platform_users["accounts"]["coach"].id,
        permission_id=PERMISSIONS["housing.stays.manage"].id, effect="deny", scope_kind="global"))
    db.commit()
    before = counts(db)
    assert db_client.post(BASE + "/commit", headers=headers, json=body).status_code == 403
    assert counts(db) == before
    assert preview(db_client, headers, line(occupancy_room)).json()["rows"][0]["ticket"] is None


@pytest.mark.parametrize("job", ["超级管理员", "管理员", "财务", "未知身份", "教练兼学员"])
def test_privileged_or_ambiguous_jobs_not_inferred(db, db_client, platform_users, occupancy_room, job):
    headers = setup_actor(db, db_client, platform_users)
    row = preview(db_client, headers, line(occupancy_room, job=job)).json()["rows"][0]
    assert row["ticket"] is None and row["errors"]


def test_future_arrival_is_reservation_and_past_departure_deferred(db, db_client, platform_users, occupancy_room):
    from datetime import timedelta

    from app.core.time import business_today
    headers = setup_actor(db, db_client, platform_users)
    tomorrow = business_today() + timedelta(days=1)
    row = preview(db_client, headers, line(occupancy_room, arrival=tomorrow.isoformat())).json()["rows"][0]
    assert row["ticket"], row
    response = db_client.post(BASE + "/commit", headers=headers, json={"ticket": row["ticket"], "idempotency_key": str(uuid4())})
    assert response.status_code == 201, response.text
    assert response.json()["stay_status"] == "planned"
    old = preview(db_client, headers, line(occupancy_room, sequence=725, arrival="2025/1/1", departure="2025/2/1")).json()["rows"][0]
    assert old["ticket"] is None and old["errors"]


def test_ambiguous_team_requires_choice_and_selected_staff_position_is_pending(db, db_client, platform_users, occupancy_room):
    from app.teaching.models import GameProject, ProfessionalTeam
    from app.team_management.person_staff_models import PersonTeamStaffPosition
    headers = setup_actor(db, db_client, platform_users)
    project = db.scalar(select(GameProject).where(GameProject.is_active.is_(True), GameProject.is_team_division.is_(True)))
    assert project
    teams = [ProfessionalTeam(project_id=project.id, name=f"导入隔离队{i}", is_active=True,
                              created_by_user_id=platform_users["accounts"]["admin"].id) for i in (1, 2)]
    db.add_all(teams); db.commit()
    text = line(occupancy_room, job=f"{project.name} 助教")
    row = preview(db_client, headers, text).json()["rows"][0]
    assert row["needs_team_choice"] is True and row["ticket"] is None
    chosen = db_client.post(BASE + "/preview", headers=headers, json={"text": text, "team_choices": {"1": str(teams[0].id)}}).json()["rows"][0]
    assert chosen["ticket"], chosen
    response = db_client.post(BASE + "/commit", headers=headers, json={"ticket": chosen["ticket"], "idempotency_key": str(uuid4())})
    assert response.status_code == 201, response.text
    position = db.scalar(select(PersonTeamStaffPosition).where(PersonTeamStaffPosition.person_id == UUID(response.json()["person_id"])))
    assert position is not None and position.team_id == teams[0].id
    assert response.json()["role_pending"] is True


def test_property_cannot_import_and_unauthenticated_requests_are_denied(db, db_client, platform_users, occupancy_room):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    assert preview(db_client, {}, line(occupancy_room)).status_code in {401, 403}
    uid = platform_users["accounts"]["coach"].id
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == uid))
    db.add(UserRole(user_id=uid, role_id=ROLES["property"].id)); db.commit()
    headers = {"X-CSRF-Token": sign_in(db_client, platform_users, "coach")}
    assert preview(db_client, headers, line(occupancy_room)).status_code == 403
