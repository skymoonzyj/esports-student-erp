from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.housing.api_support as housing_api_support
import app.housing.occupancy_router as housing_occupancy_router
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.housing.access import HousingAccessService
from app.housing.errors import (
    HousingPersistenceFailure,
    HousingServiceError,
    HousingStaleVersion,
)
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingRoomIssue,
    HousingStay,
    HousingTransfer,
)
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.seed_inventory import FLOOR_SEEDS, INITIAL_BUILDINGS, INITIAL_ROOMS
from app.identity.models import Person, StudentProfile
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.teaching.models import Cohort

_BUILDING = INITIAL_BUILDINGS[0]
_FLOOR = next(floor for floor in FLOOR_SEEDS if floor.building_id == _BUILDING.id)
_MOVED_FLOOR = next(
    floor for floor in FLOOR_SEEDS if floor.building_id == _BUILDING.id and floor.id != _FLOOR.id
)
_ROOM = next(
    room for room in INITIAL_ROOMS if room.floor_id == _FLOOR.id and room.room_type == "double"
)
_TARGET_ROOM = next(
    room
    for room in INITIAL_ROOMS
    if room.floor_id == _FLOOR.id and room.room_type == "double" and room.id != _ROOM.id
)
_HIDDEN_TARGET_ROOM = next(
    room
    for room in INITIAL_ROOMS
    if room.building_id != _BUILDING.id and room.room_type == "double"
)


def _sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _assert_private(response) -> None:
    assert response.headers["cache-control"] == "no-store, private"


def test_void_transfer_source_preserves_current_destination(
    db_client,
    db,
    platform_users,
    occupancy_rooms,
    monkeypatch,
):
    from app.security.encryption import EnvelopeCipher

    source_room, destination_room = occupancy_rooms
    actor_id = platform_users["accounts"]["admin"].id
    person_id = platform_users["people"]["self_student"].id
    now = datetime.now(UTC)
    source = HousingStay(
        person_id=person_id,
        room_id=source_room.id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=now - timedelta(days=10),
        actual_check_in_at=now - timedelta(days=10),
        actual_check_out_at=now - timedelta(days=5),
        status="checked_out",
        room_name_snapshot=source_room.room_number,
        floor_name_snapshot="1层",
        building_name_snapshot="测试楼",
        created_by_user_id=actor_id,
        checked_in_by_user_id=actor_id,
        checked_out_by_user_id=actor_id,
    )
    destination = HousingStay(
        person_id=person_id,
        room_id=destination_room.id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=now - timedelta(days=5),
        actual_check_in_at=now - timedelta(days=5),
        status="checked_in",
        room_name_snapshot=destination_room.room_number,
        floor_name_snapshot="1层",
        building_name_snapshot="测试楼",
        created_by_user_id=actor_id,
        checked_in_by_user_id=actor_id,
    )
    db.add_all([source, destination])
    db.flush()
    transfer = HousingTransfer(
        person_id=person_id,
        from_stay_id=source.id,
        to_stay_id=destination.id,
        transferred_at=source.actual_check_out_at,
        reason="调房",
        transferred_by_user_id=actor_id,
    )
    db.add(transfer)
    db.add(
        UserRole(
            user_id=platform_users["accounts"]["customer_service"].id,
            role_id=ROLES["admin"].id,
        )
    )
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")
    before = db_client.get(f"/api/v1/housing/rooms/{destination_room.id}/detail").json()
    current_room_before = before["current_stays"]
    eligible_history = db_client.get(f"/api/v1/housing/rooms/{source_room.id}/detail")
    assert eligible_history.json()["history"][0]["allowed_actions"]["void_history"] is True
    transfer_fk_pair_before = (transfer.from_stay_id, transfer.to_stay_id)
    source.voided_at = now
    source.voided_by_user_id = actor_id
    source.void_reason_ciphertext = EnvelopeCipher(bytes(range(32))).encrypt(
        "历史错误入住",
        field_name="housing_stay_void_reason",
    )
    source.version += 1
    db.commit()

    original_decrypt = EnvelopeCipher.decrypt

    def reject_reason_decrypt(self, ciphertext, *, field_name):
        assert field_name != "housing_stay_void_reason", "default GET decrypted void reason"
        return original_decrypt(self, ciphertext, field_name=field_name)

    monkeypatch.setattr(EnvelopeCipher, "decrypt", reject_reason_decrypt)

    after = db_client.get(f"/api/v1/housing/rooms/{destination_room.id}/detail")
    assert after.status_code == 200
    current_room_after = after.json()["current_stays"]
    db.refresh(transfer)
    transfer_fk_pair_after = (transfer.from_stay_id, transfer.to_stay_id)
    assert current_room_after == current_room_before
    assert transfer_fk_pair_after == transfer_fk_pair_before
    detail = db_client.get(f"/api/v1/housing/rooms/{source_room.id}/detail")
    effective_history = {item["id"] for item in detail.json()["history"]}
    assert str(source.id) not in effective_history
    assert detail.json()["voided_history"] == []
    assert "历史错误入住" not in detail.text
    assert after.json()["transfers"][0]["from_stay_voided"] is True
    assert after.json()["transfers"][0]["to_stay_voided"] is False

    exported = db_client.post(
        "/api/v1/housing/exports",
        headers={"x-csrf-token": csrf},
        json={"format": "csv", "purpose": "有效历史导出", "floor_id": str(source_room.floor_id)},
    )
    assert exported.status_code == 200
    assert source_room.room_number not in exported.content.decode("utf-8-sig")
    assert destination_room.room_number in exported.content.decode("utf-8-sig")
    monkeypatch.setattr(EnvelopeCipher, "decrypt", original_decrypt)

    restricted = db_client.get(
        f"/api/v1/housing/rooms/{source_room.id}/detail",
        params={"include_voided": True},
    )
    assert restricted.status_code == 200
    assert restricted.json()["can_view_voided_history"] is True
    tombstone = restricted.json()["voided_history"][0]
    assert tombstone["id"] == str(source.id)
    assert tombstone["void_info"]["reason"] == "历史错误入住"
    assert not any(tombstone["allowed_actions"].values())
    _assert_private(restricted)

    _sign_in(db_client, platform_users, "customer_service")
    ordinary = db_client.get(f"/api/v1/housing/rooms/{destination_room.id}/detail")
    assert ordinary.status_code == 200
    assert ordinary.json()["transfers"][0]["from_stay_voided"] is True
    assert "历史错误入住" not in ordinary.text
    assert "voided_by_user_id" not in ordinary.text
    denied = db_client.get(
        f"/api/v1/housing/rooms/{source_room.id}/detail",
        params={"include_voided": True},
    )
    assert denied.status_code == 403
    assert "历史错误入住" not in denied.text
    _assert_private(denied)

    destination.status = "checked_out"
    destination.actual_check_out_at = now - timedelta(days=1)
    destination.checked_out_by_user_id = actor_id
    destination.voided_at = now
    destination.voided_by_user_id = actor_id
    destination.void_reason_ciphertext = EnvelopeCipher(bytes(range(32))).encrypt(
        "后段历史错误",
        field_name="housing_stay_void_reason",
    )
    db.commit()
    both_voided = db_client.get(f"/api/v1/housing/rooms/{destination_room.id}/detail")
    assert both_voided.status_code == 200
    chain = both_voided.json()["transfers"][0]
    assert (chain["from_stay_id"], chain["to_stay_id"]) == (
        str(source.id),
        str(destination.id),
    )
    assert chain["from_stay_voided"] is chain["to_stay_voided"] is True
    assert "void_info" not in chain
    assert both_voided.json()["current_stays"] == []
    assert both_voided.json()["history"] == []


def test_room_detail_history_void_capability_authority_queries_are_bounded(
    db_client,
    db,
    platform_users,
    occupancy_room,
):
    from tests.invited_housing_helpers import NOW
    from tests.invited_housing_helpers import stay as make_history

    actor = platform_users["accounts"]["admin"]
    resident = db.get(Person, platform_users["people"]["self_student"].id)

    def add_history():
        history = make_history(
            db,
            actor,
            resident,
            status="checked_out",
            start=NOW - timedelta(days=3),
            actual_end=NOW - timedelta(days=1),
        )
        history.room_id = occupancy_room.id
        db.commit()

    add_history()
    _sign_in(db_client, platform_users, "admin")
    statements = []

    def count_selects(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db.get_bind().engine
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        first = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")
        assert first.status_code == 200
        before_count = len(statements)
        for _ in range(5):
            add_history()
        statements.clear()
        after = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")
        assert after.status_code == 200
        assert len(after.json()["history"]) == 6
        assert all(item["allowed_actions"]["void_history"] for item in after.json()["history"])
        print(f"history capability SELECTs: one={before_count}, six={len(statements)}")
        assert len(statements) <= before_count + 2
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)


def _switch_to_email_only_account(
    db: Session,
    platform_users,
    key: str,
) -> Person:
    person = db.get(Person, platform_users["people"][key].id)
    assert person is not None
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == person.id))
    assert account is not None
    identifier = db.scalar(
        select(AccountLoginIdentifier).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "phone",
        )
    )
    assert identifier is not None
    db.delete(identifier)
    person.phone_ciphertext = None
    person.phone_digest = None
    account.login_phone_digest = None
    db.commit()
    return person


@pytest.fixture
def verified_housing_emails(db: Session, platform_users) -> None:
    for key, account in platform_users["accounts"].items():
        existing = db.scalar(
            select(AccountLoginIdentifier.id).where(
                AccountLoginIdentifier.account_id == account.id,
                AccountLoginIdentifier.kind == "email",
            )
        )
        if existing is not None:
            continue
        person_summary = platform_users["people"][key]
        person = db.get(Person, person_summary.id)
        assert person is not None
        email = platform_users["identity_service"].decrypt_email(person)
        assert email is not None
        db.add(
            AccountLoginIdentifier(
                account_id=account.id,
                kind="email",
                identifier_digest=login_identifier_digest(
                    platform_users["identity_service"].lookup,
                    normalize_login_identifier(email),
                ),
                verified_at=datetime.now(UTC),
                verification_source="test_verified",
                is_primary=False,
            )
        )
    db.commit()


@dataclass(frozen=True)
class ScopedReaders:
    building_key: str
    floor_key: str
    denied_key: str


@pytest.fixture
def occupancy_room(
    db: Session,
    verified_housing_emails: None,
) -> Iterator[HousingRoom]:
    room = db.get(HousingRoom, _ROOM.id)
    assert room is not None
    room.verification_status = "verified"
    room.service_status = "available"
    db.commit()
    yield room


@pytest.fixture
def occupancy_rooms(
    db: Session,
    verified_housing_emails: None,
) -> tuple[HousingRoom, HousingRoom]:
    source = db.get(HousingRoom, _ROOM.id)
    target = db.get(HousingRoom, _TARGET_ROOM.id)
    assert source is not None and target is not None
    for room in (source, target):
        room.verification_status = "verified"
        room.service_status = "available"
    db.commit()
    return source, target


@pytest.fixture
def scoped_readers(db: Session, platform_users) -> ScopedReaders:
    accounts = platform_users["accounts"]
    read = PERMISSIONS["housing.read"].id
    db.add_all(
        [
            UserPermissionOverride(
                user_id=accounts["self_student"].id,
                permission_id=read,
                effect="allow",
                scope_kind="building",
                scope_id=_BUILDING.id,
            ),
            UserPermissionOverride(
                user_id=accounts["customer_service"].id,
                permission_id=read,
                effect="allow",
                scope_kind="floor",
                scope_id=_FLOOR.id,
            ),
            UserPermissionOverride(
                user_id=accounts["minor_student"].id,
                permission_id=read,
                effect="allow",
                scope_kind="building",
                scope_id=_BUILDING.id,
            ),
            UserPermissionOverride(
                user_id=accounts["minor_student"].id,
                permission_id=read,
                effect="deny",
                scope_kind="floor",
                scope_id=_FLOOR.id,
            ),
        ]
    )
    db.commit()
    return ScopedReaders("self_student", "customer_service", "minor_student")


def test_housing_occupancy_routes_expose_the_private_internal_contract() -> None:
    expected = {
        ("GET", "/api/v1/housing/rooms/{room_id}/detail"),
        ("GET", "/api/v1/housing/rooms/{room_id}/resident-options"),
        ("GET", "/api/v1/housing/rooms/{room_id}/cohort-options"),
        ("GET", "/api/v1/housing/stays/{stay_id}/transfer-options"),
        ("POST", "/api/v1/housing/rooms/{room_id}/stays"),
        ("POST", "/api/v1/housing/rooms/{room_id}/stays/backfill"),
        ("PATCH", "/api/v1/housing/stays/{stay_id}"),
        ("PATCH", "/api/v1/housing/stays/{stay_id}/planned-departure"),
        ("POST", "/api/v1/housing/stays/{stay_id}/confirm-check-in"),
        ("POST", "/api/v1/housing/stays/{stay_id}/cancel"),
        ("POST", "/api/v1/housing/stays/{stay_id}/transfer"),
        ("POST", "/api/v1/housing/stays/{stay_id}/request-checkout"),
        ("POST", "/api/v1/housing/stays/{stay_id}/confirm-checkout"),
        ("POST", "/api/v1/housing/rooms/{room_id}/issues"),
        ("POST", "/api/v1/housing/issues/{issue_id}/resolve"),
    }
    actual = {
        (method, f"/api/v1{route.path}")
        for route in housing_occupancy_router.router.routes
        for method in route.methods or set()
    }

    assert actual == expected


def test_wrong_method_on_lifecycle_route_is_private(
    db_client: TestClient,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")

    response = db_client.put(
        f"/api/v1/housing/rooms/{occupancy_room.id}/detail",
        headers={"X-CSRF-Token": csrf},
        json={},
    )

    assert response.status_code == 405
    assert response.json()["code"] == "http_error"
    _assert_private(response)


def test_room_detail_uses_one_action_projection_and_status_only_sections(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    actor = platform_users["accounts"]["admin"]
    people = platform_users["people"]
    floor = db.get(HousingFloor, occupancy_room.floor_id)
    building = db.get(HousingBuilding, occupancy_room.building_id)
    assert floor is not None and building is not None
    now = datetime.now(UTC)
    current = HousingStay(
        person_id=people["admin"].id,
        room_id=occupancy_room.id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=now - timedelta(days=3),
        actual_check_in_at=now - timedelta(days=3),
        status="checked_in",
        room_name_snapshot=occupancy_room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        created_by_user_id=actor.id,
        checked_in_by_user_id=actor.id,
        version=1,
    )
    overdue_planned = HousingStay(
        person_id=people["coach"].id,
        room_id=occupancy_room.id,
        stay_purpose="permanent",
        duration_mode="fixed_term",
        planned_check_in_at=now - timedelta(days=1),
        planned_check_out_at=now + timedelta(days=7),
        status="planned",
        room_name_snapshot=occupancy_room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        created_by_user_id=actor.id,
        version=1,
    )
    checked_out = HousingStay(
        person_id=people["customer_service"].id,
        room_id=occupancy_room.id,
        stay_purpose="permanent",
        duration_mode="fixed_term",
        planned_check_in_at=now - timedelta(days=12),
        planned_check_out_at=now - timedelta(days=8),
        actual_check_in_at=now - timedelta(days=12),
        actual_check_out_at=now - timedelta(days=9),
        status="checked_out",
        room_name_snapshot=occupancy_room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        created_by_user_id=actor.id,
        checked_in_by_user_id=actor.id,
        checked_out_by_user_id=actor.id,
        version=2,
    )
    cancelled = HousingStay(
        person_id=people["self_student"].id,
        room_id=occupancy_room.id,
        stay_purpose="permanent",
        duration_mode="fixed_term",
        planned_check_in_at=now + timedelta(days=10),
        planned_check_out_at=now + timedelta(days=15),
        status="cancelled",
        room_name_snapshot=occupancy_room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        cancellation_reason="测试取消",
        created_by_user_id=actor.id,
        cancelled_by_user_id=actor.id,
        version=2,
    )
    db.add_all([current, overdue_planned, checked_out, cancelled])
    db.commit()
    _sign_in(db_client, platform_users, "admin")

    response = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")

    assert response.status_code == 200
    payload = response.json()
    current_item = next(item for item in payload["current_stays"] if item["id"] == str(current.id))
    occupant = next(item for item in payload["room"]["occupants"] if item["id"] == str(current.id))
    assert occupant["allowed_actions"] == current_item["allowed_actions"]
    assert {item["id"] for item in payload["future_reservations"]} == {str(overdue_planned.id)}
    assert {item["id"] for item in payload["history"]} == {
        str(checked_out.id),
        str(cancelled.id),
    }
    assert all(
        item["status"] not in {"planned", "checked_in", "checkout_pending"}
        for item in payload["history"]
    )
    _assert_private(response)


def test_detail_and_candidate_responses_share_the_current_access_revision(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    now = datetime.now(UTC)
    cohort = Cohort(
        name="API 住宿候选营期",
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=24),
        default_check_in_at=now + timedelta(days=2),
        default_check_out_at=now + timedelta(days=23),
        status="upcoming",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=platform_users["accounts"]["admin"].id,
    )
    db.add(cohort)
    db.commit()
    _sign_in(db_client, platform_users, "admin")

    catalog = db_client.get("/api/v1/housing/catalog")
    overview = db_client.get("/api/v1/housing/overview")
    detail = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")
    residents = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "平台"},
    )
    cohorts = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/cohort-options")

    assert {
        response.status_code for response in (catalog, overview, detail, residents, cohorts)
    } == {200}
    revision = catalog.json()["access_revision"]
    assert len(revision) == 64
    assert overview.json()["access_revision"] == revision
    assert detail.json()["access_revision"] == revision
    assert residents.json()["access_revision"] == revision
    assert cohorts.json()["access_revision"] == revision
    assert detail.json()["room"]["id"] == str(occupancy_room.id)
    assert detail.json()["room"]["allowed_actions"] == {
        "edit": True,
        "delete": True,
        "verify": False,
        "create_stay": True,
        "report_issue": True,
        "view_detail": True,
        "manage_care": True,
        "complete_cleaning": False,
        "manage_photos": True,
    }
    assert set(detail.json()) == {
        "access_revision",
        "as_of",
        "room",
        "photos",
        "current_stays",
        "future_reservations",
        "history",
        "voided_history",
        "can_view_voided_history",
        "transfers",
        "issues",
    }
    assert detail.json()["photos"] == []
    assert all("phone" not in item for item in residents.json()["items"])
    assert all(
        set(item)
        == {
            "person_id",
            "full_name",
            "person_type",
            "sex",
            "masked_phone",
            "account_ready",
            "housing_ready",
            "account_status",
            "profile_ready",
            "guardian_ready",
        }
        for item in residents.json()["items"]
    )
    assert set(residents.json()) == {"access_revision", "items"}
    assert set(cohorts.json()) == {"access_revision", "items"}
    assert {item["id"] for item in cohorts.json()["items"]}.issuperset({str(cohort.id)})
    assert all(
        set(item)
        == {
            "id",
            "name",
            "status",
            "default_check_in_at",
            "default_check_out_at",
        }
        for item in cohorts.json()["items"]
    )
    for response in (catalog, overview, detail, residents, cohorts):
        _assert_private(response)


@pytest.mark.parametrize("resident_state", ["disabled", "deleted"])
def test_resident_options_hide_disabled_and_deleted_candidates(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    resident_state: str,
) -> None:
    person = db.get(Person, platform_users["people"]["coach"].id)
    assert person is not None
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == person.id))
    assert account is not None
    if resident_state == "disabled":
        account.status = "disabled"
    else:
        person.deleted_at = datetime.now(UTC)
    db.commit()
    _sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "平台教练"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    _assert_private(response)


def test_resident_options_include_email_only_accounts_without_phone(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    person = _switch_to_email_only_account(db, platform_users, "coach")
    _sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": person.full_name},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["person_id"] == str(person.id)
    assert items[0]["masked_phone"] is None
    assert items[0]["account_ready"] is True
    assert items[0]["profile_ready"] is True
    _assert_private(response)


def test_resident_options_use_china_business_date_for_guardian_readiness(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary_now = datetime(2026, 8, 4, 16, 30, tzinfo=UTC)

    class BoundaryDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return boundary_now.replace(tzinfo=None)
            return boundary_now.astimezone(tz)

    candidate = db.get(Person, platform_users["people"]["self_student"].id)
    assert candidate is not None
    candidate.full_name = "跨日成年候选"
    candidate.date_of_birth = date(2008, 8, 5)
    student = db.get(StudentProfile, candidate.id)
    assert student is not None
    student.status = "active"
    db.commit()
    monkeypatch.setattr(housing_occupancy_router, "datetime", BoundaryDateTime)
    _sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "跨日成年"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "person_id": str(candidate.id),
            "full_name": "跨日成年候选",
            "person_type": "student",
            "sex": "female",
            "masked_phone": "138****0004",
            "account_ready": True,
            "housing_ready": True,
            "account_status": "active",
            "profile_ready": True,
            "guardian_ready": True,
        }
    ]
    _assert_private(response)


@pytest.mark.parametrize("key", ["building_key", "floor_key"])
def test_detail_allows_building_and_floor_read_scope(
    db_client: TestClient,
    platform_users,
    occupancy_room: HousingRoom,
    scoped_readers: ScopedReaders,
    key: str,
) -> None:
    _sign_in(db_client, platform_users, getattr(scoped_readers, key))

    response = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")

    assert response.status_code == 200
    assert response.json()["room"]["id"] == str(occupancy_room.id)
    _assert_private(response)


def test_more_specific_floor_deny_hides_detail_under_building_allow(
    db_client: TestClient,
    platform_users,
    occupancy_room: HousingRoom,
    scoped_readers: ScopedReaders,
) -> None:
    _sign_in(db_client, platform_users, scoped_readers.denied_key)

    denied = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")
    missing = db_client.get(f"/api/v1/housing/rooms/{uuid4()}/detail")

    assert denied.status_code == missing.status_code == 404
    assert denied.json()["code"] == missing.json()["code"] == "housing_resource_not_found"
    assert denied.json()["detail"] == missing.json()["detail"]
    _assert_private(denied)
    _assert_private(missing)


def test_housing_read_alone_cannot_access_candidate_directories(
    db_client: TestClient,
    platform_users,
    occupancy_room: HousingRoom,
    scoped_readers: ScopedReaders,
) -> None:
    _sign_in(db_client, platform_users, scoped_readers.building_key)

    residents = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "平台"},
    )
    cohorts = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/cohort-options")

    assert residents.status_code == cohorts.status_code == 403
    assert residents.json()["code"] == cohorts.json()["code"] == "permission_denied"
    assert "items" not in residents.json()
    assert "items" not in cohorts.json()
    _assert_private(residents)
    _assert_private(cohorts)


@pytest.mark.parametrize(
    ("service_method", "endpoint", "params"),
    [
        ("list_resident_options", "resident-options", {"query": "平台"}),
        ("list_cohort_options", "cohort-options", None),
    ],
)
@pytest.mark.parametrize(
    ("revoked_permission", "expected_status", "expected_code"),
    [
        ("housing.read", 404, "housing_resource_not_found"),
        ("housing.stays.manage", 403, "permission_denied"),
    ],
)
def test_candidate_directories_reauthorize_after_precheck_before_listing(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
    service_method: str,
    endpoint: str,
    params: dict[str, str] | None,
    revoked_permission: str,
    expected_status: int,
    expected_code: str,
) -> None:
    account = platform_users["accounts"]["customer_service"]
    overrides = {
        permission: UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS[permission].id,
            effect="allow",
            scope_kind="floor",
            scope_id=_FLOOR.id,
        )
        for permission in ("housing.read", "housing.stays.manage")
    }
    db.add_all(overrides.values())
    db.commit()
    original = getattr(HousingOccupancyService, service_method)

    def revoke_after_precheck(self, *args, **kwargs):
        db.delete(overrides[revoked_permission])
        db.flush()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(HousingOccupancyService, service_method, revoke_after_precheck)
    _sign_in(db_client, platform_users, "customer_service")

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/{endpoint}",
        params=params,
    )

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert "items" not in response.json()
    _assert_private(response)


@pytest.mark.parametrize(
    ("service_method", "endpoint", "params"),
    [
        ("list_resident_options", "resident-options", {"query": "平台"}),
        ("list_cohort_options", "cohort-options", None),
    ],
)
def test_candidate_directories_refresh_exact_room_scope_before_listing(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
    service_method: str,
    endpoint: str,
    params: dict[str, str] | None,
) -> None:
    account = platform_users["accounts"]["customer_service"]
    db.add_all(
        [
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="floor",
                scope_id=_FLOOR.id,
            )
            for permission in ("housing.read", "housing.stays.manage")
        ]
    )
    db.commit()
    original = getattr(HousingOccupancyService, service_method)

    def move_room_after_precheck(self, *args, **kwargs):
        stale_room = db.get(HousingRoom, occupancy_room.id)
        assert stale_room is not None and stale_room.floor_id == _FLOOR.id
        db.execute(
            update(HousingRoom)
            .where(HousingRoom.id == occupancy_room.id)
            .values(floor_id=_MOVED_FLOOR.id)
            .execution_options(synchronize_session=False)
        )
        assert stale_room.floor_id == _FLOOR.id
        return original(self, *args, **kwargs)

    monkeypatch.setattr(HousingOccupancyService, service_method, move_room_after_precheck)
    _sign_in(db_client, platform_users, "customer_service")

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/{endpoint}",
        params=params,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    assert "items" not in response.json()
    _assert_private(response)


@pytest.mark.parametrize(
    ("service_method", "endpoint", "params"),
    [
        ("list_resident_options", "resident-options", {"query": "平台"}),
        ("list_cohort_options", "cohort-options", None),
    ],
)
def test_candidate_directories_return_revision_from_locked_reauthorization(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
    service_method: str,
    endpoint: str,
    params: dict[str, str] | None,
) -> None:
    account = platform_users["accounts"]["customer_service"]
    db.add_all(
        [
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="floor",
                scope_id=_FLOOR.id,
            )
            for permission in ("housing.read", "housing.stays.manage")
        ]
    )
    db.commit()
    initial_revision = HousingAccessService(
        db,
        account.id,
        datetime.now(UTC),
    ).access_revision
    original = getattr(HousingOccupancyService, service_method)

    def change_revision_after_precheck(self, *args, **kwargs):
        db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS["housing.issues.manage"].id,
                effect="allow",
                scope_kind="floor",
                scope_id=_FLOOR.id,
            )
        )
        db.commit()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        HousingOccupancyService,
        service_method,
        change_revision_after_precheck,
    )
    _sign_in(db_client, platform_users, "customer_service")

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/{endpoint}",
        params=params,
    )

    current_revision = HousingAccessService(
        db,
        account.id,
        datetime.now(UTC),
    ).access_revision
    assert response.status_code == 200
    assert current_revision != initial_revision
    assert response.json()["access_revision"] == current_revision
    _assert_private(response)


@pytest.mark.parametrize("query", ["  "])
def test_short_or_blank_resident_query_is_a_private_validation_error(
    db_client: TestClient,
    platform_users,
    occupancy_room: HousingRoom,
    query: str,
) -> None:
    _sign_in(db_client, platform_users, "admin")

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": query},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    _assert_private(response)


def test_resident_options_match_single_name_characters_and_initials(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    candidate = db.get(Person, platform_users["people"]["self_student"].id)
    assert candidate is not None
    candidate.full_name = "张三"
    db.commit()
    _sign_in(db_client, platform_users, "admin")

    for query in ("张", "三", "zs"):
        response = db_client.get(
            f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
            params={"query": query},
        )

        assert response.status_code == 200
        assert [item["person_id"] for item in response.json()["items"]] == [str(candidate.id)]
        _assert_private(response)


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/v1/housing/rooms/not-a-uuid/detail", {}),
        ("get", "/api/v1/housing/rooms/not-a-uuid/resident-options", {"params": {"query": "平台"}}),
        ("get", "/api/v1/housing/rooms/not-a-uuid/cohort-options", {}),
        ("get", "/api/v1/housing/stays/not-a-uuid/transfer-options", {}),
    ],
)
def test_all_read_validation_errors_are_private(
    db_client: TestClient,
    platform_users,
    method: str,
    path: str,
    kwargs: dict[str, object],
) -> None:
    _sign_in(db_client, platform_users, "admin")

    response = getattr(db_client, method)(path, **kwargs)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    _assert_private(response)


def _write(
    client: TestClient,
    method: str,
    path: str,
    *,
    csrf: str,
    request_id: str,
    body: dict[str, object],
):
    return getattr(client, method)(
        path,
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_id},
        json=body,
    )


def _assert_single_audit(
    db: Session,
    request_id: str,
    *,
    outcome: str,
) -> AuditEvent:
    db.expire_all()
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id)))
    assert len(events) == 1
    assert events[0].outcome == outcome
    return events[0]


def _assert_sensitive_read_audit(
    db: Session,
    request_id: str,
    *,
    action: str,
    room_id,
    outcome: str,
    metadata: dict[str, object],
) -> AuditEvent:
    event = _assert_single_audit(db, request_id, outcome=outcome)
    assert event.action == action
    assert event.resource_type == "housing_room"
    assert event.resource_id == room_id
    assert event.purpose is None
    assert event.event_metadata == metadata
    return event


def test_sensitive_read_audit_records_allowed_room_detail_without_payload_data(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    actor = platform_users["accounts"]["admin"]
    resident = platform_users["people"]["admin"]
    floor = db.get(HousingFloor, occupancy_room.floor_id)
    building = db.get(HousingBuilding, occupancy_room.building_id)
    assert floor is not None and building is not None
    now = datetime.now(UTC)
    visit_reason = "敏感访客事由-不得审计"
    notes = "敏感住宿备注-不得审计"
    issue_description = "敏感房间问题-不得审计"
    db.add_all(
        [
            HousingStay(
                person_id=resident.id,
                room_id=occupancy_room.id,
                stay_purpose="temporary",
                duration_mode="fixed_term",
                visit_reason=visit_reason,
                planned_check_in_at=now - timedelta(days=1),
                planned_check_out_at=now + timedelta(days=1),
                actual_check_in_at=now - timedelta(days=1),
                status="checked_in",
                room_name_snapshot=occupancy_room.room_number,
                floor_name_snapshot=floor.name,
                building_name_snapshot=building.name,
                notes=notes,
                created_by_user_id=actor.id,
                checked_in_by_user_id=actor.id,
                version=1,
            ),
            HousingRoomIssue(
                room_id=occupancy_room.id,
                description=issue_description,
                previous_service_status="available",
                reported_at=now,
                reported_by_user_id=actor.id,
                status="open",
            ),
        ]
    )
    occupancy_room.service_status = "problem"
    db.commit()
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-sensitive-detail-allow"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/detail",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 200
    assert resident.full_name in response.text
    assert visit_reason in response.text
    assert notes in response.text
    assert issue_description in response.text
    event = _assert_sensitive_read_audit(
        db,
        request_id,
        action="housing.room.detail.read",
        room_id=occupancy_room.id,
        outcome="allowed",
        metadata={"room_id": str(occupancy_room.id)},
    )
    assert all(
        value not in str(event.event_metadata)
        for value in (
            resident.full_name,
            "19990000001",
            "138****0001",
            visit_reason,
            notes,
            issue_description,
        )
    )
    _assert_private(response)


def test_sensitive_read_audit_records_allowed_resident_options_without_directory_data(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-sensitive-residents-allow"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "平台"},
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    event = _assert_sensitive_read_audit(
        db,
        request_id,
        action="housing.resident_options.read",
        room_id=occupancy_room.id,
        outcome="allowed",
        metadata={"room_id": str(occupancy_room.id), "result_count": len(items)},
    )
    forbidden = ["19990000001", "19990000004", "19990000003"]
    forbidden.extend(item["full_name"] for item in items)
    forbidden.extend(item["masked_phone"] for item in items if item["masked_phone"] is not None)
    assert all(value not in str(event.event_metadata) for value in forbidden)
    _assert_private(response)


@pytest.mark.parametrize(
    ("endpoint", "action", "params"),
    [
        pytest.param("detail", "housing.room.detail.read", None, id="detail"),
        pytest.param(
            "resident-options",
            "housing.resident_options.read",
            {"query": "平台"},
            id="resident-options",
        ),
    ],
)
def test_sensitive_read_audit_keeps_hidden_and_missing_rooms_equivalent(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    scoped_readers: ScopedReaders,
    endpoint: str,
    action: str,
    params: dict[str, str] | None,
) -> None:
    _sign_in(db_client, platform_users, scoped_readers.denied_key)
    missing_room_id = uuid4()
    hidden_request_id = f"housing-sensitive-{endpoint}-hidden"
    missing_request_id = f"housing-sensitive-{endpoint}-missing"

    hidden = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/{endpoint}",
        params=params,
        headers={"X-Request-ID": hidden_request_id},
    )
    missing = db_client.get(
        f"/api/v1/housing/rooms/{missing_room_id}/{endpoint}",
        params=params,
        headers={"X-Request-ID": missing_request_id},
    )

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["code"] == missing.json()["code"] == "housing_resource_not_found"
    assert hidden.json()["detail"] == missing.json()["detail"]
    _assert_sensitive_read_audit(
        db,
        hidden_request_id,
        action=action,
        room_id=occupancy_room.id,
        outcome="denied",
        metadata={
            "room_id": str(occupancy_room.id),
            "error_code": "housing_resource_not_found",
        },
    )
    _assert_sensitive_read_audit(
        db,
        missing_request_id,
        action=action,
        room_id=missing_room_id,
        outcome="denied",
        metadata={
            "room_id": str(missing_room_id),
            "error_code": "housing_resource_not_found",
        },
    )
    _assert_private(hidden)
    _assert_private(missing)


def test_sensitive_read_audit_records_resident_directory_permission_denial(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    scoped_readers: ScopedReaders,
) -> None:
    _sign_in(db_client, platform_users, scoped_readers.building_key)
    request_id = "housing-sensitive-residents-permission-denied"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "平台"},
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    _assert_sensitive_read_audit(
        db,
        request_id,
        action="housing.resident_options.read",
        room_id=occupancy_room.id,
        outcome="denied",
        metadata={"room_id": str(occupancy_room.id), "error_code": "permission_denied"},
    )
    _assert_private(response)


def test_sensitive_read_audit_records_mapped_resident_service_failure(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "敏感候选服务异常-不得审计"

    def fail(*args, **kwargs):
        del args, kwargs
        raise HousingStaleVersion(secret)

    monkeypatch.setattr(HousingOccupancyService, "list_resident_options", fail)
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-sensitive-residents-service-failed"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "平台"},
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "housing_stale_version"
    assert secret not in response.text
    event = _assert_sensitive_read_audit(
        db,
        request_id,
        action="housing.resident_options.read",
        room_id=occupancy_room.id,
        outcome="failed",
        metadata={
            "room_id": str(occupancy_room.id),
            "error_code": "housing_stale_version",
        },
    )
    assert secret not in str(event.event_metadata)
    _assert_private(response)


def test_sensitive_read_audit_records_unexpected_resident_failure_safely(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "敏感候选运行时异常-不得审计-19990000001"

    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(secret)

    monkeypatch.setattr(HousingOccupancyService, "list_resident_options", fail)
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-sensitive-residents-runtime-failed"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "平台"},
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 500
    assert response.json()["code"] == "housing_service_failure"
    assert secret not in response.text
    event = _assert_sensitive_read_audit(
        db,
        request_id,
        action="housing.resident_options.read",
        room_id=occupancy_room.id,
        outcome="failed",
        metadata={
            "room_id": str(occupancy_room.id),
            "error_code": "housing_service_failure",
        },
    )
    assert secret not in str(event.event_metadata)
    _assert_private(response)


def test_resident_query_validation_audit_is_failed_and_request_scoped(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-sensitive-residents-validation-failed"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": " "},
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    event = _assert_single_audit(db, request_id, outcome="failed")
    assert event.action == "housing.resident_options.read"
    assert event.resource_type == "housing_request"
    assert event.resource_id is None
    assert event.purpose is None
    assert event.event_metadata == {"error_code": "validation_error"}
    _assert_private(response)


def test_sensitive_read_audit_waits_for_resident_response_serialization(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        housing_occupancy_router,
        "ResidentOptionsResponse",
        lambda **kwargs: {},
    )
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-sensitive-residents-serialization-failed"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/resident-options",
        params={"query": "平台"},
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 500
    assert response.json()["code"] == "housing_service_failure"
    _assert_sensitive_read_audit(
        db,
        request_id,
        action="housing.resident_options.read",
        room_id=occupancy_room.id,
        outcome="failed",
        metadata={
            "room_id": str(occupancy_room.id),
            "error_code": "housing_service_failure",
        },
    )
    _assert_private(response)


def test_sensitive_read_audit_failure_fails_closed_without_retry(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def reject(*args, **kwargs) -> None:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise AuditMetadataRejected("simulated sensitive read audit rejection")

    monkeypatch.setattr(housing_api_support.AuditService, "append", reject)
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-sensitive-detail-audit-failed"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/detail",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 500
    assert response.json()["code"] == "housing_audit_failed"
    assert attempts == 1
    db.expire_all()
    assert list(db.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id))) == []
    _assert_private(response)


def _housing_domain_counts(db: Session) -> tuple[int, int, int]:
    return (
        db.scalar(select(func.count()).select_from(HousingStay)) or 0,
        db.scalar(select(func.count()).select_from(HousingTransfer)) or 0,
        db.scalar(select(func.count()).select_from(HousingRoomIssue)) or 0,
    )


@pytest.mark.parametrize(
    (
        "operator_key",
        "allowed_permission",
        "denied_permission",
        "expected_actions",
        "expected_status",
        "expected_outcome",
    ),
    [
        pytest.param(
            "customer_service",
            "housing.stays.manage",
            "housing.checkouts.manage",
            {
                "void_history": False,
                "edit_planned_departure": False,
                "edit_reservation": True,
                "confirm_check_in": True,
                "cancel_reservation": False,
                "transfer": False,
                "request_checkout": False,
                "confirm_checkout": False,
            },
            403,
            "denied",
            id="stays-only-denied",
        ),
        pytest.param(
            "customer_service",
            "housing.checkouts.manage",
            "housing.stays.manage",
            {
                "void_history": False,
                "edit_planned_departure": False,
                "edit_reservation": False,
                "confirm_check_in": False,
                "cancel_reservation": True,
                "transfer": False,
                "request_checkout": False,
                "confirm_checkout": False,
            },
            200,
            "allowed",
            id="checkouts-only-allowed",
        ),
    ],
)
def test_cancellation_uses_checkout_permission_without_granting_stay_actions(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    operator_key: str,
    allowed_permission: str,
    denied_permission: str,
    expected_actions: dict[str, bool],
    expected_status: int,
    expected_outcome: str,
) -> None:
    admin_csrf = _sign_in(db_client, platform_users, "admin")
    created = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=admin_csrf,
        request_id=f"housing-cancel-permission-setup-{operator_key}",
        body=_future_stay_body(platform_users["people"][operator_key].id),
    )
    assert created.status_code == 201
    stay_id = created.json()["id"]
    account = platform_users["accounts"][operator_key]
    db.add_all(
        [
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect=effect,
                scope_kind="floor",
                scope_id=_FLOOR.id,
            )
            for permission, effect in (
                ("housing.read", "allow"),
                (allowed_permission, "allow"),
                (denied_permission, "deny"),
            )
        ]
    )
    db.commit()
    operator_csrf = _sign_in(db_client, platform_users, operator_key)

    detail = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")
    assert detail.status_code == 200
    reservation = next(
        item for item in detail.json()["future_reservations"] if item["id"] == stay_id
    )
    assert reservation["allowed_actions"] == expected_actions
    _assert_private(detail)

    request_id = f"housing-cancel-permission-{operator_key}"
    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{stay_id}/cancel",
        csrf=operator_csrf,
        request_id=request_id,
        body={"version": 1, "reason": "权限边界取消"},
    )

    assert response.status_code == expected_status
    persisted = db.get(HousingStay, stay_id)
    assert persisted is not None
    if expected_status == 200:
        assert response.json()["status"] == "cancelled"
        assert persisted.status == "cancelled"
    else:
        assert response.json()["code"] == "permission_denied"
        assert persisted.status == "planned"
        assert persisted.version == 1
    _assert_single_audit(db, request_id, outcome=expected_outcome)
    _assert_private(response)


def test_internal_mutation_endpoints_execute_the_complete_lifecycle_and_audit_once(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_rooms: tuple[HousingRoom, HousingRoom],
) -> None:
    source_room, target_room = occupancy_rooms
    people = platform_users["people"]
    csrf = _sign_in(db_client, platform_users, "admin")
    now = datetime.now(UTC)

    create = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{source_room.id}/stays",
        csrf=csrf,
        request_id="housing-api-create-stay",
        body={
            "person_id": str(people["admin"].id),
            "stay_purpose": "permanent",
            "duration_mode": "fixed_term",
            "planned_check_in_at": (now + timedelta(days=2)).isoformat(),
            "planned_check_out_at": (now + timedelta(days=20)).isoformat(),
            "notes": "API 预约",
        },
    )
    assert create.status_code == 201, create.json()["code"]
    created = create.json()
    assert created["room_id"] == str(source_room.id)
    assert created["status"] == "planned"
    assert created["version"] == 1
    assert created["allowed_actions"] == {
        "void_history": False,
        "edit_planned_departure": False,
        "edit_reservation": True,
        "confirm_check_in": True,
        "cancel_reservation": True,
        "transfer": False,
        "request_checkout": False,
        "confirm_checkout": False,
    }
    stay_id = created["id"]
    _assert_private(create)
    create_audit = _assert_single_audit(db, "housing-api-create-stay", outcome="allowed")
    assert create_audit.resource_type == "housing_stay"
    assert create_audit.resource_id == UUID(stay_id)
    assert create_audit.event_metadata == {
        "room_id": str(source_room.id),
        "resident_person_id": str(people["admin"].id),
        "stay_id": stay_id,
    }

    update = _write(
        db_client,
        "patch",
        f"/api/v1/housing/stays/{stay_id}",
        csrf=csrf,
        request_id="housing-api-update-stay",
        body={
            "version": 1,
            "planned_check_in_at": (now + timedelta(days=3)).isoformat(),
            "planned_check_out_at": (now + timedelta(days=21)).isoformat(),
            "notes": "API 调整预约",
        },
    )
    assert update.status_code == 200
    assert update.json()["version"] == 2
    _assert_private(update)
    _assert_single_audit(db, "housing-api-update-stay", outcome="allowed")

    check_in = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{stay_id}/confirm-check-in",
        csrf=csrf,
        request_id="housing-api-confirm-check-in",
        body={
            "version": 2,
            "actual_check_in_at": (now - timedelta(hours=3)).isoformat(),
        },
    )
    assert check_in.status_code == 200
    assert check_in.json()["status"] == "checked_in"
    assert check_in.json()["version"] == 3
    assert check_in.json()["allowed_actions"]["transfer"] is True
    assert check_in.json()["allowed_actions"]["request_checkout"] is True
    _assert_private(check_in)
    _assert_single_audit(db, "housing-api-confirm-check-in", outcome="allowed")

    options = db_client.get(f"/api/v1/housing/stays/{stay_id}/transfer-options")
    assert options.status_code == 200
    assert (
        options.json()["access_revision"]
        == db_client.get("/api/v1/housing/catalog").json()["access_revision"]
    )
    assert set(options.json()) == {"access_revision", "items"}
    assert all(
        set(item)
        == {
            "room_id",
            "building_label",
            "floor_label",
            "room_label",
            "capacity",
            "current_occupants",
            "current_gender",
            "advisory_compatible",
            "status_text",
        }
        for item in options.json()["items"]
    )
    assert str(target_room.id) in {item["room_id"] for item in options.json()["items"]}
    _assert_private(options)

    transfer = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{stay_id}/transfer",
        csrf=csrf,
        request_id="housing-api-transfer-stay",
        body={
            "version": 3,
            "target_room_id": str(target_room.id),
            "transferred_at": (now - timedelta(hours=2)).isoformat(),
            "reason": "API 调房",
        },
    )
    assert transfer.status_code == 200
    destination = transfer.json()
    assert destination["room_id"] == str(target_room.id)
    assert destination["status"] == "checked_in"
    assert destination["version"] == 1
    _assert_private(transfer)
    db.expire_all()
    transfer_record = db.scalar(
        select(HousingTransfer).where(HousingTransfer.from_stay_id == UUID(stay_id))
    )
    assert transfer_record is not None
    transfer_audit = _assert_single_audit(db, "housing-api-transfer-stay", outcome="allowed")
    assert transfer_audit.resource_type == "housing_transfer"
    assert transfer_audit.resource_id == transfer_record.id
    assert transfer_audit.event_metadata == {
        "stay_id": stay_id,
        "transfer_id": str(transfer_record.id),
        "source_room_id": str(source_room.id),
        "target_room_id": str(target_room.id),
    }

    destination_id = destination["id"]
    request_checkout = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{destination_id}/request-checkout",
        csrf=csrf,
        request_id="housing-api-request-checkout",
        body={
            "version": 1,
            "actual_check_out_at": (now - timedelta(hours=1)).isoformat(),
        },
    )
    assert request_checkout.status_code == 200
    assert request_checkout.json()["status"] == "checkout_pending"
    assert request_checkout.json()["allowed_actions"]["confirm_checkout"] is True
    assert request_checkout.json()["allowed_actions"]["transfer"] is False
    _assert_private(request_checkout)
    _assert_single_audit(db, "housing-api-request-checkout", outcome="allowed")

    confirm_checkout = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{destination_id}/confirm-checkout",
        csrf=csrf,
        request_id="housing-api-confirm-checkout",
        body={"version": 2},
    )
    assert confirm_checkout.status_code == 200
    assert confirm_checkout.json()["status"] == "checked_out"
    checkout_actions = confirm_checkout.json()["allowed_actions"]
    assert checkout_actions["void_history"] is True
    assert not any(value for key, value in checkout_actions.items() if key != "void_history")
    _assert_private(confirm_checkout)
    _assert_single_audit(db, "housing-api-confirm-checkout", outcome="allowed")

    # Transfer-out now blocks any new admission until explicit room cleaning.
    dirty_source = db_client.get(f"/api/v1/housing/rooms/{source_room.id}")
    assert dirty_source.status_code == 200
    assert dirty_source.json()["cleaning_status"] == "pending"
    cleaned_source = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{source_room.id}/cleaning/complete",
        csrf=csrf,
        request_id="housing-api-clean-source",
        body={"version": dirty_source.json()["version"]},
    )
    assert cleaned_source.status_code == 200
    assert cleaned_source.json()["cleaning_status"] == "ready"
    _assert_private(cleaned_source)
    cleaning_audit = _assert_single_audit(db, "housing-api-clean-source", outcome="allowed")
    assert cleaning_audit.action == "housing.room.cleaning.complete"

    cancellable = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{source_room.id}/stays",
        csrf=csrf,
        request_id="housing-api-create-cancellable",
        body={
            "person_id": str(people["coach"].id),
            "stay_purpose": "permanent",
            "duration_mode": "fixed_term",
            "planned_check_in_at": (now + timedelta(days=30)).isoformat(),
            "planned_check_out_at": (now + timedelta(days=40)).isoformat(),
        },
    )
    assert cancellable.status_code == 201
    _assert_single_audit(db, "housing-api-create-cancellable", outcome="allowed")
    cancel = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{cancellable.json()['id']}/cancel",
        csrf=csrf,
        request_id="housing-api-cancel-stay",
        body={"version": 1, "reason": "API 取消"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    _assert_private(cancel)
    _assert_single_audit(db, "housing-api-cancel-stay", outcome="allowed")

    backfill = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{source_room.id}/stays/backfill",
        csrf=csrf,
        request_id="housing-api-backfill-stay",
        body={
            "person_id": str(people["customer_service"].id),
            "stay_purpose": "permanent",
            "duration_mode": "long_term",
            "planned_check_in_at": (now - timedelta(days=2)).isoformat(),
        },
    )
    assert backfill.status_code == 201
    assert backfill.json()["status"] == "checked_in"
    _assert_private(backfill)
    backfill_audit = _assert_single_audit(db, "housing-api-backfill-stay", outcome="allowed")
    assert backfill_audit.resource_type == "housing_stay"
    assert backfill_audit.resource_id == UUID(backfill.json()["id"])
    assert backfill_audit.event_metadata == {
        "room_id": str(source_room.id),
        "resident_person_id": str(people["customer_service"].id),
        "stay_id": backfill.json()["id"],
    }

    db.expire_all()
    current_room = db.get(HousingRoom, source_room.id)
    assert current_room is not None
    report = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{source_room.id}/issues",
        csrf=csrf,
        request_id="housing-api-report-issue",
        body={"room_version": current_room.version, "description": "空调故障"},
    )
    assert report.status_code == 201
    assert report.json()["status"] == "open"
    assert report.json()["allowed_actions"] == {"resolve": True}
    detail_with_issue = db_client.get(f"/api/v1/housing/rooms/{source_room.id}/detail")
    assert detail_with_issue.status_code == 200
    assert detail_with_issue.json()["room"]["open_issue_summary"]["allowed_actions"] == {
        "resolve": True
    }
    _assert_private(report)
    report_audit = _assert_single_audit(db, "housing-api-report-issue", outcome="allowed")
    assert report_audit.resource_type == "housing_room_issue"
    assert report_audit.resource_id == UUID(report.json()["id"])
    assert report_audit.event_metadata == {
        "room_id": str(source_room.id),
        "issue_id": report.json()["id"],
    }

    db.expire_all()
    current_room = db.get(HousingRoom, source_room.id)
    assert current_room is not None
    resolve = _write(
        db_client,
        "post",
        f"/api/v1/housing/issues/{report.json()['id']}/resolve",
        csrf=csrf,
        request_id="housing-api-resolve-issue",
        body={"room_version": current_room.version, "resolution_note": "已维修"},
    )
    assert resolve.status_code == 200
    assert resolve.json()["status"] == "resolved"
    assert resolve.json()["allowed_actions"] == {"resolve": False}
    _assert_private(resolve)
    _assert_single_audit(db, "housing-api-resolve-issue", outcome="allowed")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/housing/rooms/not-a-uuid/stays", {}),
        ("post", "/api/v1/housing/rooms/not-a-uuid/stays/backfill", {}),
        ("patch", "/api/v1/housing/stays/not-a-uuid", {}),
        ("post", "/api/v1/housing/stays/not-a-uuid/confirm-check-in", {}),
        ("post", "/api/v1/housing/stays/not-a-uuid/cancel", {}),
        ("post", "/api/v1/housing/stays/not-a-uuid/transfer", {}),
        ("post", "/api/v1/housing/stays/not-a-uuid/request-checkout", {}),
        ("post", "/api/v1/housing/stays/not-a-uuid/confirm-checkout", {}),
        ("post", "/api/v1/housing/rooms/not-a-uuid/issues", {}),
        ("post", "/api/v1/housing/issues/not-a-uuid/resolve", {}),
    ],
)
def test_all_mutation_validation_errors_are_private(
    db_client: TestClient,
    db: Session,
    platform_users,
    method: str,
    path: str,
    body: dict[str, object],
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    before = _housing_domain_counts(db)

    response = _write(
        db_client,
        method,
        path,
        csrf=csrf,
        request_id=f"housing-validation-{method}-{path.rsplit('/', 1)[-1]}",
        body=body,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert _housing_domain_counts(db) == before
    event = _assert_single_audit(
        db,
        f"housing-validation-{method}-{path.rsplit('/', 1)[-1]}",
        outcome="failed",
    )
    assert event.event_metadata == {"error_code": "validation_error"}
    _assert_private(response)


def _future_stay_body(person_id, now: datetime | None = None) -> dict[str, object]:
    reference = now or datetime.now(UTC)
    return {
        "person_id": str(person_id),
        "stay_purpose": "permanent",
        "duration_mode": "fixed_term",
        "planned_check_in_at": (reference + timedelta(days=2)).isoformat(),
        "planned_check_out_at": (reference + timedelta(days=10)).isoformat(),
    }


@pytest.mark.parametrize(
    "role,allowed",
    [("administration", True), ("admin", True), ("super_admin", True), ("customer_service", False)],
)
def test_planned_departure_uses_existing_housing_permission_and_actual_actor(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    role: str,
    allowed: bool,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    now = datetime.now(UTC)
    previous_end = now + timedelta(days=10)
    created = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id="departure-setup",
        body={
            "person_id": str(platform_users["people"]["admin"].id),
            "stay_purpose": "permanent",
            "duration_mode": "fixed_term",
            "planned_check_in_at": (now - timedelta(days=2)).isoformat(),
            "planned_check_out_at": previous_end.isoformat(),
            "notes": "保留入住备注",
        },
    )
    assert created.status_code == 201
    stay_id = created.json()["id"]
    actor = platform_users["accounts"]["customer_service"]
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES[role].id))
    if not allowed:
        db.add(
            UserPermissionOverride(
                user_id=actor.id,
                permission_id=PERMISSIONS["housing.read"].id,
                effect="allow",
                scope_kind="global",
            )
        )
    db.commit()
    csrf = _sign_in(db_client, platform_users, "customer_service")
    detail = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")
    assert detail.status_code == 200
    assert detail.json()["current_stays"][0]["allowed_actions"]["edit_planned_departure"] is allowed
    room_version = detail.json()["room"]["version"]
    request_id = f"planned-departure-{role}"
    response = _write(
        db_client,
        "patch",
        f"/api/v1/housing/stays/{stay_id}/planned-departure",
        csrf=csrf,
        request_id=request_id,
        body={"version": 1, "duration_mode": "long_term", "planned_check_out_at": None},
    )
    assert response.status_code == (200 if allowed else 403), response.text
    _assert_private(response)
    event = _assert_single_audit(db, request_id, outcome="allowed" if allowed else "denied")
    assert event.actor_user_id == actor.id
    assert event.action == "housing.stay.update_planned_departure"
    persisted = db.get(HousingStay, UUID(stay_id))
    assert persisted is not None
    assert persisted.status == "checked_in"
    assert persisted.notes == "保留入住备注"
    assert persisted.actual_check_out_at is None
    if allowed:
        assert response.json()["duration_mode"] == "long_term"
        assert response.json()["planned_check_out_at"] is None
        assert event.event_metadata == {
            "stay_id": stay_id,
            "room_id": str(occupancy_room.id),
            "previous_duration_mode": "fixed_term",
            "duration_mode": "long_term",
            "previous_planned_check_out_at": previous_end.isoformat(),
            "planned_check_out_at": None,
        }
        db.refresh(occupancy_room)
        assert occupancy_room.version == room_version + 1
        stale = _write(
            db_client,
            "patch",
            f"/api/v1/housing/stays/{stay_id}/planned-departure",
            csrf=csrf,
            request_id=f"departure-stale-{role}",
            body={"version": 1, "duration_mode": "long_term", "planned_check_out_at": None},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "housing_stale_version"
    else:
        assert persisted.version == 1
        assert persisted.planned_check_out_at == previous_end


def _direct_stay_body(
    person_id,
    *,
    backfill: bool,
    cohort_id=None,
) -> dict[str, object]:
    now = datetime.now(UTC)
    starts_at = now - timedelta(days=2) if backfill else now + timedelta(days=2)
    body: dict[str, object] = {
        "person_id": str(person_id),
        "stay_purpose": "camp" if cohort_id is not None else "permanent",
        "duration_mode": "fixed_term",
        "planned_check_in_at": starts_at.isoformat(),
        "planned_check_out_at": (now + timedelta(days=10)).isoformat(),
    }
    if cohort_id is not None:
        body["cohort_id"] = str(cohort_id)
    return body


def test_direct_email_only_resident_can_receive_stay_without_phone(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    person = _switch_to_email_only_account(db, platform_users, "coach")
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = "housing-email-only-stay"
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id=request_id,
        body=_direct_stay_body(person.id, backfill=False),
    )

    assert response.status_code == 201, response.json()
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before + 1
    event = _assert_single_audit(db, request_id, outcome="allowed")
    assert event.event_metadata["resident_person_id"] == str(person.id)
    _assert_private(response)


@pytest.mark.parametrize(
    ("endpoint", "backfill"),
    [("stays", False), ("stays/backfill", True)],
)
@pytest.mark.parametrize(
    "resident_state",
    ["missing", "deleted", "disabled_account", "no_resident_profile"],
)
def test_direct_resident_selector_hides_non_directory_candidates(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    endpoint: str,
    backfill: bool,
    resident_state: str,
) -> None:
    if resident_state == "missing":
        person_id = uuid4()
    elif resident_state == "no_resident_profile":
        person_id = platform_users["people"]["guardian"].id
    else:
        person = db.get(Person, platform_users["people"]["coach"].id)
        assert person is not None
        account = db.scalar(select(UserAccount).where(UserAccount.person_id == person.id))
        assert account is not None
        person_id = person.id
        if resident_state == "deleted":
            person.deleted_at = datetime.now(UTC)
        else:
            account.status = "disabled"
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = f"housing-hidden-resident-{resident_state}-{endpoint.replace('/', '-')}"
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/{endpoint}",
        csrf=csrf,
        request_id=request_id,
        body=_direct_stay_body(person_id, backfill=backfill),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    event = _assert_single_audit(db, request_id, outcome="denied")
    assert event.event_metadata["error_code"] == "housing_resource_not_found"
    _assert_private(response)


@pytest.mark.parametrize(
    ("endpoint", "backfill"),
    [("stays", False), ("stays/backfill", True)],
)
@pytest.mark.parametrize(
    "cohort_state",
    ["missing", "unselectable_status", "invalid_default_interval"],
)
def test_direct_cohort_selector_hides_non_directory_candidates(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    endpoint: str,
    backfill: bool,
    cohort_state: str,
) -> None:
    now = datetime.now(UTC)
    if cohort_state == "missing":
        cohort_id = uuid4()
    else:
        starts_at = now - timedelta(days=3) if backfill else now + timedelta(days=1)
        default_out = (
            starts_at if cohort_state == "invalid_default_interval" else now + timedelta(days=12)
        )
        cohort = Cohort(
            name=f"住宿不可选营期-{cohort_state}-{endpoint}",
            starts_at=now - timedelta(days=5),
            ends_at=now + timedelta(days=20),
            default_check_in_at=starts_at,
            default_check_out_at=default_out,
            status="draft" if cohort_state == "unselectable_status" else "open",
            auto_start_enabled=False,
            auto_complete_enabled=False,
            created_by_user_id=platform_users["accounts"]["admin"].id,
        )
        db.add(cohort)
        db.commit()
        cohort_id = cohort.id
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = f"housing-hidden-cohort-{cohort_state}-{endpoint.replace('/', '-')}"
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/{endpoint}",
        csrf=csrf,
        request_id=request_id,
        body=_direct_stay_body(
            platform_users["people"]["admin"].id,
            backfill=backfill,
            cohort_id=cohort_id,
        ),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    event = _assert_single_audit(db, request_id, outcome="denied")
    assert event.event_metadata["error_code"] == "housing_resource_not_found"
    _assert_private(response)


@pytest.mark.parametrize(
    ("account_key", "scope_kind", "scope_id"),
    [
        ("customer_service", "building", _BUILDING.id),
        ("customer_service", "floor", _FLOOR.id),
    ],
)
def test_create_stay_allows_building_and_floor_manage_scopes(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    account_key: str,
    scope_kind: str,
    scope_id,
) -> None:
    account = platform_users["accounts"][account_key]
    for permission in ("housing.read", "housing.stays.manage"):
        db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind=scope_kind,
                scope_id=scope_id,
            )
        )
    db.commit()
    csrf = _sign_in(db_client, platform_users, account_key)

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id=f"housing-scope-{scope_kind}-allow",
        body=_future_stay_body(platform_users["people"][account_key].id),
    )

    assert response.status_code == 201
    _assert_private(response)
    _assert_single_audit(db, f"housing-scope-{scope_kind}-allow", outcome="allowed")


def test_specific_floor_deny_overrides_building_manage_allow_for_write(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    account = platform_users["accounts"]["customer_service"]
    for permission in ("housing.read", "housing.stays.manage"):
        db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="building",
                scope_id=_BUILDING.id,
            )
        )
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["housing.stays.manage"].id,
            effect="deny",
            scope_kind="floor",
            scope_id=_FLOOR.id,
        )
    )
    db.commit()
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0
    csrf = _sign_in(db_client, platform_users, "customer_service")

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id="housing-specific-deny-write",
        body=_future_stay_body(platform_users["people"]["coach"].id),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    event = _assert_single_audit(db, "housing-specific-deny-write", outcome="denied")
    assert event.event_metadata["error_code"] == "permission_denied"
    _assert_private(response)


@pytest.mark.parametrize(
    "missing_permission",
    ["housing.rooms.manage", "housing.stays.manage"],
)
def test_backfill_requires_both_room_and_stay_management_permissions(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    missing_permission: str,
) -> None:
    account = platform_users["accounts"]["customer_service"]
    granted_permissions = {
        "housing.read",
        "housing.rooms.manage",
        "housing.stays.manage",
    } - {missing_permission}
    for permission in granted_permissions:
        db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="floor",
                scope_id=_FLOOR.id,
            )
        )
    db.commit()
    csrf = _sign_in(db_client, platform_users, "customer_service")
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0
    request_id = f"housing-backfill-missing-{missing_permission}"

    backfill = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays/backfill",
        csrf=csrf,
        request_id=request_id,
        body={
            "person_id": str(platform_users["people"]["coach"].id),
            "stay_purpose": "permanent",
            "duration_mode": "long_term",
            "planned_check_in_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )

    assert backfill.status_code == 403
    assert backfill.json()["code"] == "permission_denied"
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    event = _assert_single_audit(db, request_id, outcome="denied")
    assert event.event_metadata["error_code"] == "permission_denied"
    _assert_private(backfill)


@pytest.mark.parametrize(
    ("state", "expected_code"),
    [
        ("disabled", "housing_room_unavailable"),
        ("problem", "housing_room_unavailable"),
        ("unverified", "housing_room_not_verified"),
        ("inactive_floor", "housing_parent_inactive"),
        ("inactive_building", "housing_parent_inactive"),
    ],
)
def test_noneligible_room_writes_have_stable_failures_and_zero_domain_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    state: str,
    expected_code: str,
) -> None:
    if state in {"disabled", "problem"}:
        occupancy_room.service_status = state
    elif state == "unverified":
        occupancy_room.verification_status = "pending_verification"
    elif state == "inactive_floor":
        floor = db.get(HousingFloor, occupancy_room.floor_id)
        assert floor is not None
        floor.is_active = False
    else:
        building = db.get(HousingBuilding, occupancy_room.building_id)
        assert building is not None
        building.is_active = False
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0
    request_id = f"housing-noneligible-{state}"

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id=request_id,
        body=_future_stay_body(platform_users["people"]["admin"].id),
    )

    assert response.status_code == 409
    assert response.json()["code"] == expected_code
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    event = _assert_single_audit(db, request_id, outcome="failed")
    assert event.event_metadata["error_code"] == expected_code
    _assert_private(response)


def test_missing_write_is_private_and_audited_once_without_domain_write(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    missing_id = uuid4()
    csrf = _sign_in(db_client, platform_users, "admin")
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{missing_id}/stays",
        csrf=csrf,
        request_id="housing-missing-write",
        body=_future_stay_body(platform_users["people"]["admin"].id),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    _assert_single_audit(db, "housing-missing-write", outcome="denied")
    _assert_private(response)


def test_stale_write_is_failed_once_and_preserves_the_stay(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    csrf = _sign_in(db_client, platform_users, "admin")
    created = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id="housing-stale-setup",
        body=_future_stay_body(platform_users["people"]["admin"].id),
    )
    assert created.status_code == 201
    stay_id = created.json()["id"]

    stale = _write(
        db_client,
        "patch",
        f"/api/v1/housing/stays/{stay_id}",
        csrf=csrf,
        request_id="housing-stale-write",
        body={
            "version": 99,
            "planned_check_in_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
            "planned_check_out_at": (datetime.now(UTC) + timedelta(days=11)).isoformat(),
        },
    )

    assert stale.status_code == 409
    assert stale.json()["code"] == "housing_stale_version"
    db.expire_all()
    persisted = db.get(HousingStay, stay_id)
    assert persisted is not None and persisted.version == 1
    _assert_single_audit(db, "housing-stale-write", outcome="failed")
    _assert_private(stale)


@pytest.mark.parametrize(
    ("revoked_permission", "expected_status", "expected_code"),
    [
        ("housing.read", 404, "housing_resource_not_found"),
        ("housing.stays.manage", 403, "permission_denied"),
    ],
)
def test_create_reauthorizes_read_and_manage_permissions_under_lock(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
    revoked_permission: str,
    expected_status: int,
    expected_code: str,
) -> None:
    account = platform_users["accounts"]["customer_service"]
    overrides = {
        permission: UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS[permission].id,
            effect="allow",
            scope_kind="floor",
            scope_id=_FLOOR.id,
        )
        for permission in ("housing.read", "housing.stays.manage")
    }
    db.add_all(overrides.values())
    db.commit()
    original = HousingOccupancyService.create_stay

    def revoke_before_lock(self, *args, **kwargs):
        db.delete(overrides[revoked_permission])
        db.flush()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(HousingOccupancyService, "create_stay", revoke_before_lock)
    csrf = _sign_in(db_client, platform_users, "customer_service")
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0
    request_id = f"housing-revoked-before-lock-{revoked_permission}"

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id=request_id,
        body=_future_stay_body(platform_users["people"]["coach"].id),
    )

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    event = _assert_single_audit(db, request_id, outcome="denied")
    assert event.event_metadata["error_code"] == expected_code
    _assert_private(response)


def test_direct_hidden_transfer_target_is_private_and_has_zero_side_effects(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    hidden_target = db.get(HousingRoom, _HIDDEN_TARGET_ROOM.id)
    assert hidden_target is not None
    hidden_target.verification_status = "verified"
    hidden_target.service_status = "available"
    db.commit()
    admin_csrf = _sign_in(db_client, platform_users, "admin")
    now = datetime.now(UTC)
    created = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=admin_csrf,
        request_id="housing-hidden-target-setup",
        body=_future_stay_body(platform_users["people"]["admin"].id, now),
    )
    assert created.status_code == 201
    stay_id = created.json()["id"]
    checked_in = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{stay_id}/confirm-check-in",
        csrf=admin_csrf,
        request_id="housing-hidden-target-check-in",
        body={
            "version": 1,
            "actual_check_in_at": (now - timedelta(hours=2)).isoformat(),
        },
    )
    assert checked_in.status_code == 200

    scoped_actor = platform_users["accounts"]["customer_service"]
    hidden_floor = db.get(HousingFloor, hidden_target.floor_id)
    assert hidden_floor is not None
    db.add_all(
        [
            UserPermissionOverride(
                user_id=scoped_actor.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="floor",
                scope_id=scope_id,
            )
            for permission, scope_id in (
                ("housing.read", occupancy_room.floor_id),
                ("housing.transfers.manage", occupancy_room.floor_id),
                ("housing.transfers.manage", hidden_floor.id),
            )
        ]
    )
    db.commit()
    scoped_actor_csrf = _sign_in(db_client, platform_users, "customer_service")
    before_stays = db.scalar(select(func.count()).select_from(HousingStay)) or 0

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{stay_id}/transfer",
        csrf=scoped_actor_csrf,
        request_id="housing-hidden-transfer-target",
        body={
            "version": 2,
            "target_room_id": str(hidden_target.id),
            "transferred_at": (now - timedelta(hours=1)).isoformat(),
            "reason": "不得写入审计的自由文本原因",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before_stays
    db.expire_all()
    source = db.get(HousingStay, stay_id)
    assert source is not None and source.status == "checked_in" and source.version == 2
    event = _assert_single_audit(db, "housing-hidden-transfer-target", outcome="denied")
    assert event.event_metadata["error_code"] == "housing_resource_not_found"
    assert "自由文本" not in str(event.event_metadata)
    _assert_private(response)


def test_expired_fixed_term_detail_and_backdated_transfer_share_request_time_boundary(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_rooms: tuple[HousingRoom, HousingRoom],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_room, target_room = occupancy_rooms
    floor = db.get(HousingFloor, source_room.floor_id)
    building = db.get(HousingBuilding, source_room.building_id)
    assert floor is not None and building is not None
    actor = platform_users["accounts"]["admin"]
    resident = platform_users["people"]["admin"]
    now = datetime(2026, 8, 5, 4, 0, tzinfo=UTC)

    class BoundaryDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    monkeypatch.setattr(housing_occupancy_router, "datetime", BoundaryDateTime)
    source = HousingStay(
        person_id=resident.id,
        room_id=source_room.id,
        stay_purpose="permanent",
        duration_mode="fixed_term",
        cohort_id=None,
        visit_reason=None,
        planned_check_in_at=now - timedelta(hours=2),
        planned_check_out_at=now,
        actual_check_in_at=now - timedelta(hours=2),
        actual_check_out_at=None,
        status="checked_in",
        room_name_snapshot=source_room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        notes=None,
        checkout_requested_actual_at=None,
        checkout_requested_at=None,
        checkout_requested_by_user_id=None,
        created_by_user_id=actor.id,
        checked_in_by_user_id=actor.id,
        version=1,
    )
    db.add(source)
    db.commit()
    source_id = source.id
    source_state = (source.status, source.actual_check_out_at, source.version)
    domain_counts_before = _housing_domain_counts(db)
    destination_count_before = (
        db.scalar(
            select(func.count())
            .select_from(HousingStay)
            .where(
                HousingStay.person_id == resident.id,
                HousingStay.room_id == target_room.id,
            )
        )
        or 0
    )
    assert destination_count_before == 0
    csrf = _sign_in(db_client, platform_users, "admin")

    detail = db_client.get(
        f"/api/v1/housing/rooms/{source_room.id}/detail",
        headers={"X-Request-ID": "housing-expired-fixed-term-detail"},
    )
    assert detail.status_code == 200
    returned_source = next(
        item for item in detail.json()["current_stays"] if item["id"] == str(source_id)
    )
    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{source_id}/transfer",
        csrf=csrf,
        request_id="housing-expired-fixed-term-backdated-transfer",
        body={
            "version": 1,
            "target_room_id": str(target_room.id),
            "transferred_at": (now - timedelta(minutes=30)).isoformat(),
            "reason": "不得通过回填时间绕过到期限制",
        },
    )

    assert (
        returned_source["allowed_actions"]["transfer"],
        response.status_code,
    ) == (False, 409)
    assert response.json()["code"] == "housing_invalid_stay_transition"
    _assert_private(detail)
    _assert_private(response)
    event = _assert_single_audit(
        db,
        "housing-expired-fixed-term-backdated-transfer",
        outcome="failed",
    )
    assert event.action == "housing.stay.transfer"
    assert event.resource_type == "housing_stay"
    assert event.resource_id == source_id
    assert event.event_metadata == {
        "stay_id": str(source_id),
        "source_room_id": str(source_room.id),
        "target_room_id": str(target_room.id),
        "error_code": "housing_invalid_stay_transition",
    }
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (preserved.status, preserved.actual_check_out_at, preserved.version) == source_state
    assert _housing_domain_counts(db) == domain_counts_before
    assert (
        db.scalar(
            select(func.count())
            .select_from(HousingStay)
            .where(
                HousingStay.person_id == resident.id,
                HousingStay.room_id == target_room.id,
            )
        )
        == destination_count_before
    )


@pytest.mark.parametrize(
    ("raised", "expected_status", "expected_code", "expected_detail"),
    [
        (
            HousingPersistenceFailure("secret database constraint and phone 19990000001"),
            503,
            "housing_persistence_failure",
            "房态服务暂时不可用，请稍后重试",
        ),
        (
            HousingServiceError("secret internal lifecycle detail"),
            500,
            "housing_service_failure",
            "房态服务暂时不可用",
        ),
    ],
)
def test_internal_service_failures_are_safe_retryable_and_audited_once(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
    raised: HousingServiceError,
    expected_status: int,
    expected_code: str,
    expected_detail: str,
) -> None:
    def fail(*args, **kwargs):
        del args, kwargs
        raise raised

    monkeypatch.setattr(HousingOccupancyService, "create_stay", fail)
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = f"housing-safe-failure-{expected_status}"
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id=request_id,
        body=_future_stay_body(platform_users["people"]["admin"].id),
    )

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert response.json()["detail"] == expected_detail
    assert "secret" not in response.text
    assert "19990000001" not in response.text
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    event = _assert_single_audit(db, request_id, outcome="failed")
    assert event.event_metadata == {"error_code": expected_code}
    _assert_private(response)


def test_raw_database_failure_maps_to_safe_503_and_one_failed_audit(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_raw(*args, **kwargs):
        del args, kwargs
        raise IntegrityError(
            "INSERT secret_housing_table",
            {"phone": "19990000001"},
            RuntimeError("secret database constraint"),
        )

    monkeypatch.setattr(HousingOccupancyService, "create_stay", fail_raw)
    csrf = _sign_in(db_client, platform_users, "admin")

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id="housing-raw-database-failure",
        body=_future_stay_body(platform_users["people"]["admin"].id),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "housing_persistence_failure"
    assert response.json()["detail"] == "房态服务暂时不可用，请稍后重试"
    assert "secret" not in response.text
    assert "19990000001" not in response.text
    event = _assert_single_audit(db, "housing-raw-database-failure", outcome="failed")
    assert event.event_metadata == {"error_code": "housing_persistence_failure"}
    _assert_private(response)


@pytest.mark.parametrize("failure_site", ["access", "precheck"])
def test_database_failure_before_mutation_execution_is_safe_and_audited_once(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    def fail_boundary(*args, **kwargs):
        del args, kwargs
        raise IntegrityError(
            f"SELECT secret_housing_{failure_site}",
            {"phone": "19990000001", "identity": "110105198001021020"},
            RuntimeError(f"secret {failure_site} database failure"),
        )

    monkeypatch.setattr(
        housing_occupancy_router,
        "_access" if failure_site == "access" else "_precheck_room_mutation",
        fail_boundary,
    )
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = f"housing-{failure_site}-database-failure"
    before = _housing_domain_counts(db)

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id=request_id,
        body={
            **_future_stay_body(platform_users["people"]["admin"].id),
            "notes": "不得进入审计的自由文本备注",
        },
    )

    assert response.status_code == 503
    assert response.json()["code"] == "housing_persistence_failure"
    assert response.json()["detail"] == "房态服务暂时不可用，请稍后重试"
    assert "secret" not in response.text
    assert "19990000001" not in response.text
    assert _housing_domain_counts(db) == before
    event = _assert_single_audit(db, request_id, outcome="failed")
    assert event.event_metadata == {"error_code": "housing_persistence_failure"}
    assert all(
        value not in str(event.event_metadata)
        for value in (
            "自由文本备注",
            "平台管理员",
            "19990000001",
            "110105198001021020",
        )
    )
    _assert_private(response)


def test_unexpected_mutation_precheck_failure_is_safe_and_audited_once(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_precheck(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("secret precheck reason for 平台管理员 19990000001")

    monkeypatch.setattr(housing_occupancy_router, "_precheck_room_mutation", fail_precheck)
    csrf = _sign_in(db_client, platform_users, "admin")
    request_id = "housing-precheck-runtime-failure"
    before = _housing_domain_counts(db)

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/issues",
        csrf=csrf,
        request_id=request_id,
        body={
            "room_version": occupancy_room.version,
            "description": "不得进入审计的问题描述",
        },
    )

    assert response.status_code == 500
    assert response.json()["code"] == "housing_service_failure"
    assert response.json()["detail"] == "房态服务暂时不可用"
    assert "secret" not in response.text
    assert "19990000001" not in response.text
    assert _housing_domain_counts(db) == before
    event = _assert_single_audit(db, request_id, outcome="failed")
    assert event.event_metadata == {"error_code": "housing_service_failure"}
    assert all(
        value not in str(event.event_metadata)
        for value in (
            "问题描述",
            "secret precheck reason",
            "平台管理员",
            "19990000001",
        )
    )
    _assert_private(response)


def test_sensitive_read_audit_records_raw_database_detail_failure(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_read(*args, **kwargs):
        del args, kwargs
        raise IntegrityError(
            "SELECT secret_housing_read",
            {"phone": "19990000001"},
            RuntimeError("secret read database failure"),
        )

    monkeypatch.setattr(HousingOccupancyService, "room_projections", fail_read)
    _sign_in(db_client, platform_users, "admin")
    request_id = "housing-sensitive-detail-database-failed"

    response = db_client.get(
        f"/api/v1/housing/rooms/{occupancy_room.id}/detail",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "housing_persistence_failure"
    assert response.json()["detail"] == "房态服务暂时不可用，请稍后重试"
    assert "secret" not in response.text
    assert "19990000001" not in response.text
    event = _assert_sensitive_read_audit(
        db,
        request_id,
        action="housing.room.detail.read",
        room_id=occupancy_room.id,
        outcome="failed",
        metadata={
            "room_id": str(occupancy_room.id),
            "error_code": "housing_persistence_failure",
        },
    )
    assert "secret" not in str(event.event_metadata)
    assert "19990000001" not in str(event.event_metadata)
    _assert_private(response)


def test_automatic_checkout_pending_actions_follow_the_service_state_machine(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
) -> None:
    actor = platform_users["accounts"]["admin"]
    person = platform_users["people"]["admin"]
    floor = db.get(HousingFloor, occupancy_room.floor_id)
    building = db.get(HousingBuilding, occupancy_room.building_id)
    assert floor is not None and building is not None
    now = datetime.now(UTC)
    stay = HousingStay(
        person_id=person.id,
        room_id=occupancy_room.id,
        stay_purpose="permanent",
        duration_mode="long_term",
        cohort_id=None,
        visit_reason=None,
        planned_check_in_at=now - timedelta(days=2),
        planned_check_out_at=None,
        actual_check_in_at=now - timedelta(days=2),
        actual_check_out_at=None,
        status="checkout_pending",
        room_name_snapshot=occupancy_room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        notes=None,
        checkout_requested_actual_at=None,
        checkout_requested_at=None,
        checkout_requested_by_user_id=None,
        created_by_user_id=actor.id,
        checked_in_by_user_id=actor.id,
        version=1,
    )
    db.add(stay)
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")

    detail = db_client.get(f"/api/v1/housing/rooms/{occupancy_room.id}/detail")

    assert detail.status_code == 200
    returned = next(item for item in detail.json()["current_stays"] if item["id"] == str(stay.id))
    assert returned["checkout_requested_actual_at"] is None
    assert returned["checkout_requested_at"] is None
    db.refresh(stay)
    assert (
        stay.checkout_requested_actual_at,
        stay.checkout_requested_at,
        stay.checkout_requested_by_user_id,
    ) == (None, None, None)
    assert returned["allowed_actions"] == {
        "void_history": False,
        "edit_planned_departure": False,
        "edit_reservation": False,
        "confirm_check_in": False,
        "cancel_reservation": False,
        "transfer": True,
        "request_checkout": True,
        "confirm_checkout": False,
    }

    requested = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{stay.id}/request-checkout",
        csrf=csrf,
        request_id="housing-automatic-pending-request",
        body={
            "version": 1,
            "actual_check_out_at": (now - timedelta(hours=1)).isoformat(),
        },
    )

    assert requested.status_code == 200
    assert requested.json()["status"] == "checkout_pending"
    assert requested.json()["checkout_requested_actual_at"] is not None
    assert requested.json()["checkout_requested_at"] is not None
    assert requested.json()["allowed_actions"]["request_checkout"] is False
    assert requested.json()["allowed_actions"]["confirm_checkout"] is True
    db.refresh(stay)
    assert all(
        value is not None
        for value in (
            stay.checkout_requested_actual_at,
            stay.checkout_requested_at,
            stay.checkout_requested_by_user_id,
        )
    )
    _assert_single_audit(db, "housing-automatic-pending-request", outcome="allowed")
    _assert_private(requested)

    confirmed = _write(
        db_client,
        "post",
        f"/api/v1/housing/stays/{stay.id}/confirm-checkout",
        csrf=csrf,
        request_id="housing-automatic-pending-confirm",
        body={"version": 2},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "checked_out"
    assert datetime.fromisoformat(
        confirmed.json()["actual_check_out_at"]
    ) == datetime.fromisoformat(requested.json()["checkout_requested_actual_at"])
    confirmed_actions = confirmed.json()["allowed_actions"]
    assert confirmed_actions["void_history"] is True
    assert not any(value for key, value in confirmed_actions.items() if key != "void_history")
    _assert_single_audit(db, "housing-automatic-pending-confirm", outcome="allowed")
    _assert_private(confirmed)


def test_audit_rejection_rolls_back_lifecycle_mutation(
    db_client: TestClient,
    db: Session,
    platform_users,
    occupancy_room: HousingRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(*args, **kwargs) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated housing lifecycle audit rejection")

    monkeypatch.setattr(housing_api_support.AuditService, "append", reject)
    csrf = _sign_in(db_client, platform_users, "admin")
    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0

    response = _write(
        db_client,
        "post",
        f"/api/v1/housing/rooms/{occupancy_room.id}/stays",
        csrf=csrf,
        request_id="housing-lifecycle-audit-rollback",
        body=_future_stay_body(platform_users["people"]["admin"].id),
    )

    assert response.status_code == 500
    assert response.json()["code"] == "housing_audit_failed"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before
    _assert_private(response)
