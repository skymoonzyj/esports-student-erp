from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.identity.service as identity_service_module
from app.audit.models import AuditEvent
from app.identity.models import GuardianRelationship, Person
from app.identity.schemas import GuardianCreate


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post("/api/v1/auth/login", json={"identifier": phone, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_customer_service_list_is_masked(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "customer_service")
    response = db_client.get("/api/v1/people")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    person = platform_users["people"]["own_student"]
    row = next(item for item in response.json()["items"] if item["id"] == str(person.id))
    assert row["phone"] is None
    assert row["identity_number"] == "110105********1064"
    assert "110105201201021064" not in response.text


def test_admin_reads_personnel_counts_by_profile(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/people/counts")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {
        "total": 9,
        "student": 5,
        "guardian": 1,
        "staff": 3,
        "coach": 1,
        "life_teacher": 0,
        "customer_service": 1,
        "administration": 1,
        "other_staff": 0,
        "visitor": 0,
        "team_member": 0,
        "team_manager": 0,
        "in_team": 0,
    }


def test_guardian_reads_only_bound_students(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    own_student = platform_users["people"]["own_student"]
    other_student = platform_users["people"]["other_student"]

    assert db_client.get(f"/api/v1/people/{own_student.id}").status_code == 200
    assert db_client.get(f"/api/v1/people/{other_student.id}").status_code == 403


def test_guardian_cannot_read_student_after_relationship_ends(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    student = platform_users["people"]["own_student"]
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id
            == platform_users["people"]["guardian"].id,
            GuardianRelationship.student_person_id == student.id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    relationship.ended_at = datetime(2026, 7, 27, 2, tzinfo=UTC)
    db.commit()

    response = db_client.get(f"/api/v1/people/{student.id}")

    assert response.status_code == 403


def test_student_reads_only_self(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "self_student")
    own_person = platform_users["people"]["self_student"]
    other_person = platform_users["people"]["own_student"]

    own = db_client.get(f"/api/v1/people/{own_person.id}")
    assert own.status_code == 200
    assert own.json()["id"] == str(own_person.id)
    assert own.json()["phone"] == "138****0004"
    assert db_client.get(f"/api/v1/people/{other_person.id}").status_code == 403


def test_standard_account_reads_and_updates_own_profile(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    csrf = sign_in(db_client, platform_users, "self_student")
    person = platform_users["people"]["self_student"]

    profile = db_client.get("/api/v1/people/me/profile")

    assert profile.status_code == 200
    assert profile.headers["cache-control"] == "no-store, private"
    assert profile.json() == {
        "person_id": str(person.id),
        "full_name": "自助账号学员",
        "nickname": None,
        "person_types": ["student"],
        "staff_type": None,
        "sex": "female",
        "date_of_birth": "1995-01-02",
        "age_status": "adult",
        "masked_identity": "110105********112X",
        "masked_phone": "138****0004",
        "masked_email": "s***@example.com",
    }

    updated = db_client.patch(
        "/api/v1/people/me/profile",
        headers={"X-CSRF-Token": csrf},
        json={"nickname": "  小宇  "},
    )

    assert updated.status_code == 200
    assert updated.json()["nickname"] == "小宇"
    db.expire_all()
    stored = db.get(Person, person.id)
    assert stored is not None
    assert stored.nickname == "小宇"
    event = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "person.nickname.update")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event is not None
    assert event.actor_user_id == platform_users["accounts"]["self_student"].id
    assert event.resource_id == person.id
    assert event.event_metadata == {"person_id": str(person.id)}


def test_self_profile_can_clear_nickname_and_rejects_other_fields(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    person = platform_users["people"]["self_student"]
    person.nickname = "原昵称"
    db.commit()
    csrf = sign_in(db_client, platform_users, "self_student")

    rejected = db_client.patch(
        "/api/v1/people/me/profile",
        headers={"X-CSRF-Token": csrf},
        json={"nickname": "新昵称", "full_name": "不能修改实名"},
    )
    cleared = db_client.patch(
        "/api/v1/people/me/profile",
        headers={"X-CSRF-Token": csrf},
        json={"nickname": "   "},
    )

    assert rejected.status_code == 422
    assert cleared.status_code == 200
    assert cleared.json()["nickname"] is None
    db.expire_all()
    stored = db.get(Person, person.id)
    assert stored is not None
    assert stored.full_name == "自助账号学员"
    assert stored.nickname is None


def test_people_name_search_matches_and_returns_nickname(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    coach_summary = platform_users["people"]["coach"]
    coach = db.get(Person, coach_summary.id)
    assert coach is not None
    coach.nickname = "闪电教头"
    db.commit()
    sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/people", params={"name": "闪电"})

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == str(coach.id)
    assert response.json()["items"][0]["nickname"] == "闪电教头"


def test_admin_creates_staff_and_searches_exact_phone(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/people/staff",
        headers={"X-CSRF-Token": csrf},
        json={
            "full_name": "新增生活老师",
            "identity_number": "11010519491231002X",
            "sex": "female",
            "date_of_birth": "1949-12-31",
            "phone": "13900139009",
            "email": "life@example.com",
            "staff_type": "life_teacher",
        },
    )

    assert created.status_code == 201
    assert created.json()["phone"] == "139****9009"
    assert created.json()["identity_number"] == "110105********002X"
    assert "masked_phone" not in created.json()
    assert "masked_identity" not in created.json()
    searched = db_client.get("/api/v1/people", params={"phone": "13900139009"})
    assert searched.status_code == 200
    assert [item["full_name"] for item in searched.json()["items"]] == ["新增生活老师"]


def test_admin_updates_person_and_checks_readiness(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    person = platform_users["people"]["adult_without_account"]

    updated = db_client.patch(
        f"/api/v1/people/{person.id}",
        headers={"X-CSRF-Token": csrf},
        json={"full_name": "已更新学员"},
    )
    readiness = db_client.get(
        f"/api/v1/people/{person.id}/readiness",
        params={"reference_date": "2026-07-26"},
    )

    assert updated.status_code == 200
    assert updated.json()["full_name"] == "已更新学员"
    assert readiness.status_code == 200
    assert readiness.json() == {
        "ready": True,
        "missing": [],
        "age_status": "adult",
        "guardian_status": "not_applicable",
        "guardian_issues": [],
    }


def test_create_minor_without_guardian_returns_pending_draft(
    db_client: TestClient,
    platform_users,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        identity_service_module,
        "business_today",
        lambda: date(2026, 7, 27),
    )
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        "/api/v1/people/students",
        headers={"X-CSRF-Token": csrf},
        json={
            "full_name": "待绑定新学员",
            "identity_number": make_cn_id("20140102", 172),
            "sex": "female",
            "date_of_birth": "2014-01-02",
            "student_number": "PENDING-NEW-001",
        },
    )

    assert response.status_code == 201
    assert response.json()["age_status"] == "minor"
    assert response.json()["guardian_status"] == "pending"


def test_people_list_exposes_age_and_guardian_status(
    db_client: TestClient,
    platform_users,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        identity_service_module,
        "business_today",
        lambda: date(2026, 7, 27),
    )
    sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/people", params={"type": "student"})

    assert response.status_code == 200
    rows = {item["full_name"]: item for item in response.json()["items"]}
    assert rows["已绑定学员"]["age_status"] == "minor"
    assert rows["已绑定学员"]["guardian_status"] == "ready"
    assert rows["其他学员"]["guardian_status"] == "pending"
    assert rows["待开通账号人员"]["age_status"] == "adult"
    assert rows["待开通账号人员"]["guardian_status"] == "not_applicable"


def test_people_list_can_filter_staff_subtype(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        "/api/v1/people",
        params={"type": "staff", "staff_type": "coach"},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["full_name"] for item in response.json()["items"]] == ["平台教练"]
    assert response.json()["items"][0]["staff_type"] == "coach"


def test_only_admin_receives_guardian_issue_diagnostics(
    db_client: TestClient,
    platform_users,
) -> None:
    student_id = platform_users["people"]["other_student"].id
    guardian = platform_users["identity_service"].create_guardian(
        GuardianCreate(
            full_name="待开通账号监护人",
            identity_number=make_cn_id("19800102", 174),
            sex="female",
            date_of_birth=date(1980, 1, 2),
            phone="13900139174",
            email="guardian-174@example.com",
        )
    )
    relationship = platform_users["identity_service"].add_guardian(
        student_id,
        guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    sign_in(db_client, platform_users, "admin")
    admin = db_client.get(
        f"/api/v1/people/{student_id}/readiness",
        params={"reference_date": "2026-07-27"},
    )

    assert admin.status_code == 200
    assert admin.json()["guardian_issues"] == [
        {
            "relationship_id": str(relationship.relationship_id),
            "guardian_person_id": str(guardian.id),
            "account_id": None,
            "reason_codes": ["guardian_account_missing"],
        }
    ]

    db_client.cookies.clear()
    sign_in(db_client, platform_users, "customer_service")
    support = db_client.get(
        f"/api/v1/people/{student_id}/readiness",
        params={"reference_date": "2026-07-27"},
    )

    assert support.status_code == 200
    assert "guardian_issues" not in support.json()
