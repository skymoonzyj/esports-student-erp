from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.housing.errors import HousingResourceNotFound, HousingServiceError
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingRoomIssue,
    HousingStay,
)
from app.housing.portal_service import (
    HousingPortalAuthorizedSubject,
    HousingPortalService,
)
from app.housing.schemas import HousingPortalResponse
from app.housing.seed_inventory import INITIAL_ROOMS
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService

NOW = datetime(2026, 8, 5, 8, tzinfo=UTC)
CHINA_BUSINESS_DATE = date(2026, 8, 5)

_PERSON_IDS = {
    "guardian": UUID("b1000000-0000-4000-8000-000000000001"),
    "minor": UUID("b1000000-0000-4000-8000-000000000002"),
    "adult": UUID("b1000000-0000-4000-8000-000000000003"),
    "admin": UUID("b1000000-0000-4000-8000-000000000004"),
    "roommate": UUID("b1000000-0000-4000-8000-000000000005"),
    "deleted_roommate": UUID("b1000000-0000-4000-8000-000000000006"),
}
_ACCOUNT_IDS = {
    "guardian": UUID("b2000000-0000-4000-8000-000000000001"),
    "adult": UUID("b2000000-0000-4000-8000-000000000002"),
    "admin": UUID("b2000000-0000-4000-8000-000000000003"),
}
_STAY_IDS = {
    "minor_current": UUID("b4000000-0000-4000-8000-000000000001"),
    "roommate_pending": UUID("b4000000-0000-4000-8000-000000000002"),
    "roommate_current": UUID("b4000000-0000-4000-8000-000000000003"),
    "deleted_roommate_current": UUID("b4000000-0000-4000-8000-000000000004"),
    "adult_overdue": UUID("b4000000-0000-4000-8000-000000000005"),
    "adult_future": UUID("b4000000-0000-4000-8000-000000000006"),
    "adult_checked_out": UUID("b4000000-0000-4000-8000-000000000007"),
    "minor_cancelled": UUID("b4000000-0000-4000-8000-000000000008"),
    "duplicate_current": UUID("b4000000-0000-4000-8000-000000000009"),
    "duplicate_pending": UUID("b4000000-0000-4000-8000-000000000010"),
}
_PORTAL_ROOM_SEED = next(room for room in INITIAL_ROOMS if room.room_type == "double")
_NOISE_ROOM_SEEDS = tuple(
    room
    for room in INITIAL_ROOMS
    if room.id != _PORTAL_ROOM_SEED.id and room.room_type != "functional"
)[:2]
_ROOM_NOTE = "housing-portal-room-note-secret"
_STAY_NOTE = "housing-portal-stay-note-secret"
_PROBLEM_DESCRIPTION = "housing-portal-room-problem-secret"


@dataclass
class HousingPortalScenario:
    db: Session
    people: dict[str, Person]
    accounts: dict[str, UserAccount]
    relationships: dict[str, GuardianRelationship]

    def list_subjects(
        self,
        account_key: str,
    ) -> list[HousingPortalAuthorizedSubject]:
        return HousingPortalService(self.db).list_authorized_subjects(
            user_id=self.accounts[account_key].id,
            actor_person_id=self.people[account_key].id,
            as_of=NOW,
        )

    def has_subjects(self, account_key: str) -> bool:
        return HousingPortalService(self.db).has_authorized_subjects(
            user_id=self.accounts[account_key].id,
            actor_person_id=self.people[account_key].id,
            as_of=NOW,
        )

    def get_housing(self, account_key: str) -> HousingPortalResponse:
        return HousingPortalService(self.db).get_housing(
            user_id=self.accounts[account_key].id,
            actor_person_id=self.people[account_key].id,
            as_of=NOW,
        )

    def deny_person(self, account_key: str, person_id: UUID) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=self.accounts[account_key].id,
                permission_id=PERMISSIONS["housing.read"].id,
                effect="deny",
                scope_kind="person",
                scope_id=person_id,
            )
        )
        self.db.flush()


@dataclass
class HousingPortalProjectionScenario:
    authorization: HousingPortalScenario
    room: HousingRoom
    noise_rooms: tuple[HousingRoom, HousingRoom]
    stays: dict[str, HousingStay]


def _person(
    db: Session,
    *,
    key: str,
    name: str,
    birth_date: date,
    student_number: str | None = None,
) -> Person:
    person = Person(
        id=_PERSON_IDS[key],
        full_name=name,
        identity_ciphertext=f"housing-portal-identity-ciphertext-{key}",
        identity_digest=f"housing-portal-identity-digest-{key}",
        phone_ciphertext=f"housing-portal-phone-ciphertext-{key}",
        phone_digest=f"housing-portal-phone-digest-{key}",
        email=None,
        sex="female",
        date_of_birth=birth_date,
    )
    db.add(person)
    db.flush()
    if student_number is not None:
        db.add(
            StudentProfile(
                person_id=person.id,
                student_number=student_number,
                status="active",
            )
        )
        db.flush()
    return person


def _account(
    db: Session,
    *,
    key: str,
    person: Person,
    roles: tuple[str, ...],
) -> UserAccount:
    account = UserAccount(
        id=_ACCOUNT_IDS[key],
        person_id=person.id,
        login_phone_digest=f"housing-portal-login-digest-{key}",
        email_normalized=f"housing-portal-{key}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    for role in roles:
        db.add(UserRole(user_id=account.id, role_id=ROLES[role].id))
    db.flush()
    return account


def _housing_stay(
    db: Session,
    *,
    stay_id: UUID,
    person_id: UUID,
    room: HousingRoom,
    actor_id: UUID,
    planned_check_in_at: datetime,
    planned_check_out_at: datetime | None,
    status: str,
    actual_check_in_at: datetime | None = None,
    actual_check_out_at: datetime | None = None,
    notes: str | None = None,
) -> HousingStay:
    floor = db.get(HousingFloor, room.floor_id)
    building = db.get(HousingBuilding, room.building_id)
    assert floor is not None and building is not None
    stay = HousingStay(
        id=stay_id,
        person_id=person_id,
        room_id=room.id,
        stay_purpose="permanent",
        duration_mode=("fixed_term" if planned_check_out_at is not None else "long_term"),
        planned_check_in_at=planned_check_in_at,
        planned_check_out_at=planned_check_out_at,
        actual_check_in_at=actual_check_in_at,
        actual_check_out_at=actual_check_out_at,
        status=status,
        room_name_snapshot=room.room_number,
        floor_name_snapshot=floor.name,
        building_name_snapshot=building.name,
        notes=notes,
        cancellation_reason="housing portal cancelled noise" if status == "cancelled" else None,
        created_by_user_id=actor_id,
        checked_in_by_user_id=(actor_id if actual_check_in_at is not None else None),
        checked_out_by_user_id=(actor_id if actual_check_out_at is not None else None),
        cancelled_by_user_id=actor_id if status == "cancelled" else None,
        version=1,
    )
    db.add(stay)
    db.flush()
    return stay


@pytest.fixture
def housing_portal_scenario(db: Session) -> HousingPortalScenario:
    people = {
        "guardian": _person(
            db,
            key="guardian",
            name="门户监护人",
            birth_date=date(1980, 1, 2),
        ),
        "minor": _person(
            db,
            key="minor",
            name="未成年学员",
            birth_date=date(2012, 1, 2),
            student_number="HOUSING-PORTAL-MINOR",
        ),
        "adult": _person(
            db,
            key="adult",
            name="成年学员",
            birth_date=date(1995, 1, 2),
            student_number="HOUSING-PORTAL-ADULT",
        ),
        "admin": _person(
            db,
            key="admin",
            name="纯住宿管理员",
            birth_date=date(1985, 1, 2),
        ),
    }
    accounts = {
        "guardian": _account(
            db,
            key="guardian",
            person=people["guardian"],
            roles=("guardian",),
        ),
        "adult": _account(
            db,
            key="adult",
            person=people["adult"],
            roles=("student", "guardian"),
        ),
        "admin": _account(
            db,
            key="admin",
            person=people["admin"],
            roles=("admin",),
        ),
    }
    relationships = {
        "guardian_to_adult": GuardianRelationship(
            id=UUID("b3000000-0000-4000-8000-000000000001"),
            guardian_person_id=people["guardian"].id,
            student_person_id=people["adult"].id,
            relationship="father",
            is_primary=False,
            valid_from=date(2010, 1, 1),
        ),
        "guardian_to_minor": GuardianRelationship(
            id=UUID("b3000000-0000-4000-8000-000000000002"),
            guardian_person_id=people["guardian"].id,
            student_person_id=people["minor"].id,
            relationship="father",
            is_primary=True,
            valid_from=date(2020, 1, 1),
        ),
        "adult_to_minor": GuardianRelationship(
            id=UUID("b3000000-0000-4000-8000-000000000003"),
            guardian_person_id=people["adult"].id,
            student_person_id=people["minor"].id,
            relationship="mentor",
            is_primary=False,
            valid_from=date(2026, 1, 1),
        ),
    }
    db.add_all(relationships.values())
    db.flush()
    return HousingPortalScenario(
        db=db,
        people=people,
        accounts=accounts,
        relationships=relationships,
    )


@pytest.fixture
def housing_portal_projection(
    housing_portal_scenario: HousingPortalScenario,
) -> HousingPortalProjectionScenario:
    scenario = housing_portal_scenario
    db = scenario.db
    roommate = _person(
        db,
        key="roommate",
        name="同住房同学",
        birth_date=date(2011, 2, 3),
    )
    deleted_roommate = _person(
        db,
        key="deleted_roommate",
        name="已删除室友",
        birth_date=date(2010, 3, 4),
    )
    deleted_roommate.deleted_at = NOW - timedelta(days=1)
    scenario.people["roommate"] = roommate
    scenario.people["deleted_roommate"] = deleted_roommate

    room = db.get(HousingRoom, _PORTAL_ROOM_SEED.id)
    noise_rooms = tuple(db.get(HousingRoom, seed.id) for seed in _NOISE_ROOM_SEEDS)
    assert room is not None
    assert len(noise_rooms) == 2 and all(item is not None for item in noise_rooms)
    checked_out_room, cancelled_room = noise_rooms
    assert checked_out_room is not None and cancelled_room is not None
    room.notes = _ROOM_NOTE
    room.service_status = "problem"
    db.add(
        HousingRoomIssue(
            room_id=room.id,
            description=_PROBLEM_DESCRIPTION,
            previous_service_status="available",
            reported_at=NOW - timedelta(days=1),
            reported_by_user_id=scenario.accounts["guardian"].id,
            status="open",
        )
    )
    db.flush()

    actor_id = scenario.accounts["guardian"].id
    stays = {
        "minor_current": _housing_stay(
            db,
            stay_id=_STAY_IDS["minor_current"],
            person_id=scenario.people["minor"].id,
            room=room,
            actor_id=actor_id,
            planned_check_in_at=NOW - timedelta(days=30),
            planned_check_out_at=None,
            actual_check_in_at=NOW - timedelta(days=30),
            status="checked_in",
            notes=_STAY_NOTE,
        ),
        "roommate_pending": _housing_stay(
            db,
            stay_id=_STAY_IDS["roommate_pending"],
            person_id=roommate.id,
            room=room,
            actor_id=actor_id,
            planned_check_in_at=NOW - timedelta(days=30),
            planned_check_out_at=NOW - timedelta(days=20),
            actual_check_in_at=NOW - timedelta(days=30),
            status="checkout_pending",
        ),
        "roommate_current": _housing_stay(
            db,
            stay_id=_STAY_IDS["roommate_current"],
            person_id=roommate.id,
            room=room,
            actor_id=actor_id,
            planned_check_in_at=NOW - timedelta(days=19),
            planned_check_out_at=None,
            actual_check_in_at=NOW - timedelta(days=19),
            status="checked_in",
        ),
        "deleted_roommate_current": _housing_stay(
            db,
            stay_id=_STAY_IDS["deleted_roommate_current"],
            person_id=deleted_roommate.id,
            room=room,
            actor_id=actor_id,
            planned_check_in_at=NOW - timedelta(days=10),
            planned_check_out_at=None,
            actual_check_in_at=NOW - timedelta(days=10),
            status="checked_in",
        ),
        "adult_overdue": _housing_stay(
            db,
            stay_id=_STAY_IDS["adult_overdue"],
            person_id=scenario.people["adult"].id,
            room=room,
            actor_id=actor_id,
            planned_check_in_at=NOW - timedelta(days=2),
            planned_check_out_at=NOW + timedelta(days=2),
            status="planned",
        ),
        "adult_future": _housing_stay(
            db,
            stay_id=_STAY_IDS["adult_future"],
            person_id=scenario.people["adult"].id,
            room=room,
            actor_id=actor_id,
            planned_check_in_at=NOW + timedelta(days=7),
            planned_check_out_at=NOW + timedelta(days=10),
            status="planned",
        ),
        "adult_checked_out": _housing_stay(
            db,
            stay_id=_STAY_IDS["adult_checked_out"],
            person_id=scenario.people["adult"].id,
            room=checked_out_room,
            actor_id=actor_id,
            planned_check_in_at=NOW - timedelta(days=50),
            planned_check_out_at=NOW - timedelta(days=40),
            actual_check_in_at=NOW - timedelta(days=50),
            actual_check_out_at=NOW - timedelta(days=41),
            status="checked_out",
        ),
        "minor_cancelled": _housing_stay(
            db,
            stay_id=_STAY_IDS["minor_cancelled"],
            person_id=scenario.people["minor"].id,
            room=cancelled_room,
            actor_id=actor_id,
            planned_check_in_at=NOW + timedelta(days=20),
            planned_check_out_at=NOW + timedelta(days=25),
            status="cancelled",
        ),
    }
    return HousingPortalProjectionScenario(
        authorization=scenario,
        room=room,
        noise_rooms=(checked_out_room, cancelled_room),
        stays=stays,
    )


def test_guardian_lists_current_adult_and_minor_students_in_stable_order(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    subjects = housing_portal_scenario.list_subjects("guardian")

    assert [(subject.person_id, subject.name, subject.is_self) for subject in subjects] == [
        (housing_portal_scenario.people["adult"].id, "成年学员", False),
        (housing_portal_scenario.people["minor"].id, "未成年学员", False),
    ]


def test_student_guardian_lists_self_first_and_person_ids_are_unique(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    subjects = housing_portal_scenario.list_subjects("adult")
    person_ids = [subject.person_id for subject in subjects]

    assert [(subject.person_id, subject.name, subject.is_self) for subject in subjects] == [
        (housing_portal_scenario.people["adult"].id, "成年学员", True),
        (housing_portal_scenario.people["minor"].id, "未成年学员", False),
    ]
    assert len(person_ids) == len(set(person_ids))


def test_valid_from_after_china_business_date_filters_family_target(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    housing_portal_scenario.relationships["guardian_to_adult"].valid_from = date(
        2026,
        8,
        6,
    )
    housing_portal_scenario.db.flush()

    subjects = HousingPortalService(housing_portal_scenario.db).list_authorized_subjects(
        user_id=housing_portal_scenario.accounts["guardian"].id,
        actor_person_id=housing_portal_scenario.people["guardian"].id,
        as_of=NOW,
    )

    assert [subject.person_id for subject in subjects] == [
        housing_portal_scenario.people["minor"].id
    ]


def test_valid_until_before_china_business_date_filters_family_target(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    housing_portal_scenario.relationships["guardian_to_adult"].valid_until = date(
        2026,
        8,
        4,
    )
    housing_portal_scenario.db.flush()

    subjects = HousingPortalService(housing_portal_scenario.db).list_authorized_subjects(
        user_id=housing_portal_scenario.accounts["guardian"].id,
        actor_person_id=housing_portal_scenario.people["guardian"].id,
        as_of=NOW,
    )

    assert [subject.person_id for subject in subjects] == [
        housing_portal_scenario.people["minor"].id
    ]


def test_valid_until_on_china_business_date_includes_family_target(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    housing_portal_scenario.relationships["guardian_to_adult"].valid_until = CHINA_BUSINESS_DATE
    housing_portal_scenario.db.flush()

    subjects = HousingPortalService(housing_portal_scenario.db).list_authorized_subjects(
        user_id=housing_portal_scenario.accounts["guardian"].id,
        actor_person_id=housing_portal_scenario.people["guardian"].id,
        as_of=NOW,
    )

    assert [subject.person_id for subject in subjects] == [
        housing_portal_scenario.people["adult"].id,
        housing_portal_scenario.people["minor"].id,
    ]


def test_shanghai_business_date_excludes_target_expired_on_prior_utc_day(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    housing_portal_scenario.relationships["guardian_to_adult"].valid_until = date(
        2026,
        8,
        4,
    )
    housing_portal_scenario.db.flush()

    subjects = HousingPortalService(housing_portal_scenario.db).list_authorized_subjects(
        user_id=housing_portal_scenario.accounts["guardian"].id,
        actor_person_id=housing_portal_scenario.people["guardian"].id,
        as_of=datetime(2026, 8, 4, 16, tzinfo=UTC),
    )

    assert [subject.person_id for subject in subjects] == [
        housing_portal_scenario.people["minor"].id
    ]


def test_valid_from_on_china_business_date_includes_family_target(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    housing_portal_scenario.relationships["guardian_to_adult"].valid_from = CHINA_BUSINESS_DATE
    housing_portal_scenario.db.flush()

    subjects = housing_portal_scenario.list_subjects("guardian")

    assert [subject.person_id for subject in subjects] == [
        housing_portal_scenario.people["adult"].id,
        housing_portal_scenario.people["minor"].id,
    ]


def test_same_name_non_self_subjects_sort_by_uuid(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    housing_portal_scenario.people["adult"].full_name = "同名住宿学员"
    housing_portal_scenario.people["minor"].full_name = "同名住宿学员"
    housing_portal_scenario.db.flush()

    subjects = housing_portal_scenario.list_subjects("guardian")

    assert [subject.person_id for subject in subjects] == sorted(
        [
            housing_portal_scenario.people["adult"].id,
            housing_portal_scenario.people["minor"].id,
        ],
        key=lambda person_id: person_id.int,
    )


def test_candidate_without_student_profile_is_filtered(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    assert (
        housing_portal_scenario.db.get(
            StudentProfile,
            housing_portal_scenario.people["admin"].id,
        )
        is None
    )
    housing_portal_scenario.db.add(
        GuardianRelationship(
            id=UUID("b3000000-0000-4000-8000-000000000004"),
            guardian_person_id=housing_portal_scenario.people["guardian"].id,
            student_person_id=housing_portal_scenario.people["admin"].id,
            relationship="guardian",
            is_primary=False,
            valid_from=CHINA_BUSINESS_DATE,
        )
    )
    housing_portal_scenario.db.flush()

    subjects = HousingPortalService(housing_portal_scenario.db).list_authorized_subjects(
        user_id=housing_portal_scenario.accounts["guardian"].id,
        actor_person_id=housing_portal_scenario.people["guardian"].id,
        as_of=NOW,
    )

    assert [subject.person_id for subject in subjects] == [
        housing_portal_scenario.people["adult"].id,
        housing_portal_scenario.people["minor"].id,
    ]


@pytest.mark.parametrize("filtered_by", ["ended", "deleted", "person_deny"])
def test_ended_deleted_and_explicitly_denied_subjects_are_filtered(
    housing_portal_scenario: HousingPortalScenario,
    filtered_by: str,
) -> None:
    if filtered_by == "ended":
        housing_portal_scenario.relationships["guardian_to_adult"].ended_at = NOW
        housing_portal_scenario.relationships["guardian_to_minor"].ended_at = NOW
    elif filtered_by == "deleted":
        housing_portal_scenario.people["adult"].deleted_at = NOW
        housing_portal_scenario.people["minor"].deleted_at = NOW
    else:
        housing_portal_scenario.deny_person(
            "guardian",
            housing_portal_scenario.people["adult"].id,
        )
        housing_portal_scenario.deny_person(
            "guardian",
            housing_portal_scenario.people["minor"].id,
        )
    housing_portal_scenario.db.flush()

    assert housing_portal_scenario.list_subjects("guardian") == []


def test_global_explicit_deny_filters_guardian_subjects(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    housing_portal_scenario.db.add(
        UserPermissionOverride(
            user_id=housing_portal_scenario.accounts["guardian"].id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    housing_portal_scenario.db.flush()

    assert housing_portal_scenario.list_subjects("guardian") == []


def test_global_admin_has_no_personal_housing_subjects(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    assert housing_portal_scenario.list_subjects("admin") == []
    assert housing_portal_scenario.has_subjects("admin") is False


def test_global_admin_student_profile_has_no_personal_housing_subject(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    admin = housing_portal_scenario.people["admin"]
    housing_portal_scenario.db.add(
        StudentProfile(
            person_id=admin.id,
            student_number="HOUSING-PORTAL-ADMIN-SELF",
            status="active",
        )
    )
    housing_portal_scenario.db.flush()

    assert housing_portal_scenario.list_subjects("admin") == []


def test_global_admin_guardian_relationship_has_no_personal_housing_subject(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    housing_portal_scenario.db.add(
        GuardianRelationship(
            id=UUID("b3000000-0000-4000-8000-000000000005"),
            guardian_person_id=housing_portal_scenario.people["admin"].id,
            student_person_id=housing_portal_scenario.people["minor"].id,
            relationship="guardian",
            is_primary=False,
            valid_from=CHINA_BUSINESS_DATE,
        )
    )
    housing_portal_scenario.db.flush()

    assert housing_portal_scenario.list_subjects("admin") == []


def test_person_scoped_allow_preserves_personal_housing_access(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    minor = housing_portal_scenario.people["minor"]
    housing_portal_scenario.db.add_all(
        [
            GuardianRelationship(
                id=UUID("b3000000-0000-4000-8000-000000000007"),
                guardian_person_id=housing_portal_scenario.people["admin"].id,
                student_person_id=minor.id,
                relationship="guardian",
                is_primary=False,
                valid_from=CHINA_BUSINESS_DATE,
            ),
            UserPermissionOverride(
                user_id=housing_portal_scenario.accounts["admin"].id,
                permission_id=PERMISSIONS["housing.read"].id,
                effect="allow",
                scope_kind="person",
                scope_id=minor.id,
            ),
        ]
    )
    housing_portal_scenario.db.flush()

    assert housing_portal_scenario.list_subjects("admin") == [
        HousingPortalAuthorizedSubject(
            person_id=minor.id,
            name=minor.full_name,
            is_self=False,
        )
    ]


def test_global_explicit_deny_overrides_admin_person_allow(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    minor = housing_portal_scenario.people["minor"]
    housing_portal_scenario.db.add_all(
        [
            GuardianRelationship(
                id=UUID("b3000000-0000-4000-8000-000000000008"),
                guardian_person_id=housing_portal_scenario.people["admin"].id,
                student_person_id=minor.id,
                relationship="guardian",
                is_primary=False,
                valid_from=CHINA_BUSINESS_DATE,
            ),
            UserPermissionOverride(
                user_id=housing_portal_scenario.accounts["admin"].id,
                permission_id=PERMISSIONS["housing.read"].id,
                effect="allow",
                scope_kind="person",
                scope_id=minor.id,
            ),
            UserPermissionOverride(
                user_id=housing_portal_scenario.accounts["admin"].id,
                permission_id=PERMISSIONS["housing.read"].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            ),
        ]
    )
    housing_portal_scenario.db.flush()

    assert housing_portal_scenario.list_subjects("admin") == []


def test_relationship_missing_during_token_regeneration_denies_candidate(
    housing_portal_scenario: HousingPortalScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = housing_portal_scenario.people["admin"]
    minor = housing_portal_scenario.people["minor"]
    housing_portal_scenario.db.add_all(
        [
            UserRole(
                user_id=housing_portal_scenario.accounts["admin"].id,
                role_id=ROLES["guardian"].id,
            ),
            GuardianRelationship(
                id=UUID("b3000000-0000-4000-8000-000000000006"),
                guardian_person_id=admin.id,
                student_person_id=minor.id,
                relationship="guardian",
                is_primary=False,
                valid_from=CHINA_BUSINESS_DATE,
            ),
        ]
    )
    housing_portal_scenario.db.flush()

    def tokens_without_relationship(
        _self: RbacService,
        *,
        actor_person_id: UUID,
        target_person_ids: set[UUID],
        reference_date: date | None = None,
    ) -> dict[UUID, set[str]]:
        del actor_person_id, reference_date
        return {person_id: {"global", f"person:{person_id}"} for person_id in target_person_ids}

    monkeypatch.setattr(
        RbacService,
        "person_resource_tokens_many",
        tokens_without_relationship,
    )

    assert housing_portal_scenario.list_subjects("admin") == []


def test_admin_student_role_allows_student_profile_self_subject(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    admin = housing_portal_scenario.people["admin"]
    housing_portal_scenario.db.add_all(
        [
            StudentProfile(
                person_id=admin.id,
                student_number="HOUSING-PORTAL-ADMIN-STUDENT",
                status="active",
            ),
            UserRole(
                user_id=housing_portal_scenario.accounts["admin"].id,
                role_id=ROLES["student"].id,
            ),
        ]
    )
    housing_portal_scenario.db.flush()

    assert housing_portal_scenario.list_subjects("admin") == [
        HousingPortalAuthorizedSubject(
            person_id=admin.id,
            name=admin.full_name,
            is_self=True,
        )
    ]


def _nested_mapping_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        mapping_keys = {key for key in value if isinstance(key, str)}
        for nested in value.values():
            mapping_keys.update(_nested_mapping_keys(nested))
        return mapping_keys
    if isinstance(value, list):
        sequence_keys: set[str] = set()
        for nested in value:
            sequence_keys.update(_nested_mapping_keys(nested))
        return sequence_keys
    return set()


def test_get_housing_projects_current_planned_and_current_roommates_without_secrets(
    housing_portal_projection: HousingPortalProjectionScenario,
) -> None:
    projection = housing_portal_projection

    response = projection.authorization.get_housing("guardian")

    assert response.as_of == NOW
    assert [subject.subject_name for subject in response.subjects] == [
        "成年学员",
        "未成年学员",
    ]
    subjects = {subject.subject_person_id: subject for subject in response.subjects}
    adult = subjects[projection.authorization.people["adult"].id]
    minor = subjects[projection.authorization.people["minor"].id]

    assert adult.current_stay is None
    assert [stay.planned_check_in_at for stay in adult.planned_stays] == [
        projection.stays["adult_overdue"].planned_check_in_at,
        projection.stays["adult_future"].planned_check_in_at,
    ]
    assert all(stay.status == "planned" for stay in adult.planned_stays)
    assert all(stay.roommates is None for stay in adult.planned_stays)
    assert minor.current_stay is not None
    assert minor.current_stay.status == "checked_in"
    assert minor.current_stay.roommates == [projection.authorization.people["roommate"].full_name]
    assert minor.planned_stays == []

    serialized = response.model_dump(mode="json")
    serialized_text = json.dumps(serialized, ensure_ascii=False, sort_keys=True)
    for noise_room in projection.noise_rooms:
        assert noise_room.room_number not in serialized_text

    forbidden_keys = {
        "access_source",
        "account_status",
        "birthday",
        "date_of_birth",
        "description",
        "email",
        "email_normalized",
        "id",
        "identity",
        "identity_ciphertext",
        "identity_digest",
        "identity_number",
        "login_phone_digest",
        "masked_phone",
        "notes",
        "phone",
        "phone_ciphertext",
        "phone_digest",
        "problem_description",
        "room_id",
        "roommate_id",
        "roommate_person_id",
        "sex",
        "stay_id",
        "student_number",
        "user_id",
    }
    assert forbidden_keys.isdisjoint(_nested_mapping_keys(serialized))

    forbidden_values = {
        "active",
        "family",
        "female",
        _PROBLEM_DESCRIPTION,
        _ROOM_NOTE,
        _STAY_NOTE,
        str(projection.room.id),
        str(projection.authorization.people["roommate"].id),
        str(projection.authorization.people["deleted_roommate"].id),
        *(str(room.id) for room in projection.noise_rooms),
        *(str(stay.id) for stay in projection.stays.values()),
    }
    for person in projection.authorization.people.values():
        forbidden_values.update(
            {
                person.identity_ciphertext,
                person.identity_digest,
                str(person.date_of_birth),
            }
        )
        if person.phone_ciphertext is not None:
            forbidden_values.add(person.phone_ciphertext)
        if person.phone_digest is not None:
            forbidden_values.add(person.phone_digest)
        if person.email is not None:
            forbidden_values.add(person.email)
    for account in projection.authorization.accounts.values():
        forbidden_values.update(
            {
                account.email_normalized,
                account.login_phone_digest,
                account.status,
            }
        )
    for person_key in ("adult", "minor"):
        profile = projection.authorization.db.get(
            StudentProfile,
            projection.authorization.people[person_key].id,
        )
        assert profile is not None
        forbidden_values.add(profile.student_number)

    for value in forbidden_values:
        assert value not in serialized_text


def test_get_housing_returns_every_authorized_subject_without_stays(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    response = housing_portal_scenario.get_housing("guardian")

    assert [
        (
            subject.subject_person_id,
            subject.current_stay,
            subject.planned_stays,
        )
        for subject in response.subjects
    ] == [
        (housing_portal_scenario.people["adult"].id, None, []),
        (housing_portal_scenario.people["minor"].id, None, []),
    ]


def test_get_housing_without_an_authorized_subject_is_not_found(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    with pytest.raises(HousingResourceNotFound):
        housing_portal_scenario.get_housing("admin")


def test_void_history_preserves_personal_current_and_planned_projection(
    housing_portal_projection: HousingPortalProjectionScenario,
) -> None:
    projection = housing_portal_projection
    scenario = projection.authorization
    before = scenario.get_housing("guardian").model_dump(mode="json")
    history = projection.stays["adult_checked_out"]
    history.voided_at = NOW
    history.voided_by_user_id = scenario.accounts["guardian"].id
    history.void_reason_ciphertext = "opaque-void-reason"
    scenario.db.flush()
    after = scenario.get_housing("guardian").model_dump(mode="json")
    assert after == before
    assert "void_info" not in _nested_mapping_keys(after)
    assert "void_reason_ciphertext" not in _nested_mapping_keys(after)


def test_get_housing_rejects_multiple_logically_current_stays_atomically(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    scenario = housing_portal_scenario
    room = scenario.db.get(HousingRoom, _PORTAL_ROOM_SEED.id)
    assert room is not None
    actor_id = scenario.accounts["guardian"].id
    _housing_stay(
        scenario.db,
        stay_id=_STAY_IDS["duplicate_current"],
        person_id=scenario.people["adult"].id,
        room=room,
        actor_id=actor_id,
        planned_check_in_at=NOW - timedelta(days=20),
        planned_check_out_at=NOW - timedelta(days=10),
        actual_check_in_at=NOW - timedelta(days=20),
        status="checked_in",
    )
    _housing_stay(
        scenario.db,
        stay_id=_STAY_IDS["duplicate_pending"],
        person_id=scenario.people["adult"].id,
        room=room,
        actor_id=actor_id,
        planned_check_in_at=NOW - timedelta(days=9),
        planned_check_out_at=None,
        actual_check_in_at=NOW - timedelta(days=9),
        status="checkout_pending",
    )

    with pytest.raises(HousingServiceError):
        scenario.get_housing("guardian")
