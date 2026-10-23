from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.housing.models import HousingBuilding, HousingFloor
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.portal.context_service import PortalContextService
from app.portal.schemas import PortalContextResponse
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES

NOW = datetime(2026, 8, 5, 8, tzinfo=UTC)


@dataclass
class PortalContextScenario:
    db: Session
    people: dict[str, Person]
    accounts: dict[str, UserAccount]
    building_id: UUID

    @property
    def multi_account(self) -> UserAccount:
        return self.accounts["multi"]

    @property
    def multi_person(self) -> Person:
        return self.people["multi"]

    @property
    def admin_account(self) -> UserAccount:
        return self.accounts["admin"]

    @property
    def admin_person(self) -> Person:
        return self.people["admin"]

    @property
    def staff_account(self) -> UserAccount:
        return self.accounts["staff"]

    @property
    def staff_person(self) -> Person:
        return self.people["staff"]

    def context_for(self, key: str) -> PortalContextResponse:
        return PortalContextService(self.db).get_context(
            user_id=self.accounts[key].id,
            actor_person_id=self.people[key].id,
            occurred_at=NOW,
        )

    def allow(
        self,
        key: str,
        permission: str,
        scope_kind: str,
        scope_id: UUID | None = None,
    ) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=self.accounts[key].id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind=scope_kind,
                scope_id=scope_id,
            )
        )
        self.db.flush()

    def deny(self, key: str, permission: str, scope_kind: str) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=self.accounts[key].id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind=scope_kind,
                scope_id=None,
            )
        )
        self.db.flush()


@pytest.fixture
def portal_context_scenario(db: Session) -> PortalContextScenario:
    role_sets = {
        "admin": ("admin",),
        "coach": ("coach",),
        "guardian": ("guardian",),
        "student": ("student",),
        "multi": ("coach", "guardian"),
        "student_guardian": ("student", "guardian"),
        "staff": ("staff",),
    }
    people: dict[str, Person] = {}
    accounts: dict[str, UserAccount] = {}
    marker = uuid4().hex[:12]
    for index, (key, roles) in enumerate(role_sets.items(), start=1):
        person = Person(
            full_name=f"门户上下文{key}",
            identity_ciphertext=f"portal-context-{marker}-{key}",
            identity_digest=f"ctx-id-{marker}-{key}",
            sex="female" if index % 2 else "male",
            date_of_birth=date(1990, 1, min(index, 28)),
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=f"ctx-phone-{marker}-{key}",
            email_normalized=f"portal-context-{marker}-{key}@example.test",
            password_hash="test-password-hash",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        for role in roles:
            db.add(UserRole(user_id=account.id, role_id=ROLES[role].id))
        if "student" in roles:
            db.add(
                StudentProfile(
                    person_id=person.id,
                    student_number=f"PORTAL-{index:03d}-{marker[:6]}",
                    status="active",
                )
            )
        people[key] = person
        accounts[key] = account
    db.add(
        GuardianRelationship(
            guardian_person_id=people["guardian"].id,
            student_person_id=people["student"].id,
            relationship="mother",
            is_primary=True,
            valid_from=date(2026, 1, 1),
        )
    )
    db.flush()
    building_id = db.scalar(
        select(HousingBuilding.id).order_by(
            HousingBuilding.display_order,
            HousingBuilding.id,
        )
    )
    assert building_id is not None
    return PortalContextScenario(
        db=db,
        people=people,
        accounts=accounts,
        building_id=building_id,
    )


def test_context_combines_coach_and_family_without_switching_sessions(
    portal_context_scenario: PortalContextScenario,
) -> None:
    result = portal_context_scenario.context_for("multi")

    assert result == PortalContextResponse(
        coach_available=True,
        learning_available=True,
        payments_available=True,
        housing_management_available=False,
        housing_available=False,
        learning_mode="family",
    )


def test_context_keeps_pure_back_office_account_out_of_portal(
    portal_context_scenario: PortalContextScenario,
) -> None:
    result = portal_context_scenario.context_for("admin")

    assert result == PortalContextResponse(
        coach_available=False,
        learning_available=False,
        payments_available=False,
        housing_management_available=True,
        housing_available=False,
        learning_mode=None,
    )


def test_context_exposes_only_coach_capability_for_coach_account(
    portal_context_scenario: PortalContextScenario,
) -> None:
    result = portal_context_scenario.context_for("coach")

    assert result == PortalContextResponse(
        coach_available=True,
        learning_available=False,
        payments_available=False,
        housing_management_available=False,
        housing_available=False,
        learning_mode=None,
    )


def test_context_honors_generic_explicit_deny(
    portal_context_scenario: PortalContextScenario,
) -> None:
    portal_context_scenario.deny("multi", "coach_roster.read", "global")

    result = portal_context_scenario.context_for("multi")

    assert result.coach_available is False
    assert result.learning_available is True


def test_context_reports_all_learning_modes(
    portal_context_scenario: PortalContextScenario,
) -> None:
    assert portal_context_scenario.context_for("student").learning_mode == "self"
    assert portal_context_scenario.context_for("guardian").learning_mode == "family"
    assert portal_context_scenario.context_for("student_guardian").learning_mode == (
        "self_and_family"
    )


def test_staff_account_level_self_payment_allow_is_visible(
    portal_context_scenario: PortalContextScenario,
) -> None:
    portal_context_scenario.allow("staff", "payments.read", "self")

    result = portal_context_scenario.context_for("staff")

    assert result.payments_available is True
    assert result.learning_available is False


def test_self_payment_deny_overrides_guardian_default(
    portal_context_scenario: PortalContextScenario,
) -> None:
    portal_context_scenario.deny("guardian", "payments.read", "self")

    result = portal_context_scenario.context_for("guardian")

    assert result.learning_available is True
    assert result.payments_available is False


def test_guardian_has_personal_housing_without_management_catalog(
    portal_context_scenario: PortalContextScenario,
) -> None:
    result = portal_context_scenario.context_for("guardian")

    assert result.housing_available is True
    assert result.housing_management_available is False


def test_guardian_global_housing_deny_revokes_personal_and_management_housing(
    portal_context_scenario: PortalContextScenario,
) -> None:
    portal_context_scenario.db.add(
        UserRole(
            user_id=portal_context_scenario.accounts["guardian"].id,
            role_id=ROLES["admin"].id,
        )
    )
    portal_context_scenario.db.flush()

    before = portal_context_scenario.context_for("guardian")

    assert before.housing_available is True
    assert before.housing_management_available is True

    portal_context_scenario.deny("guardian", "housing.read", "global")

    after = portal_context_scenario.context_for("guardian")

    assert after.housing_available is False
    assert after.housing_management_available is False


def test_student_has_personal_housing(
    portal_context_scenario: PortalContextScenario,
) -> None:
    result = portal_context_scenario.context_for("student")

    assert result.housing_available is True
    assert result.housing_management_available is False


def test_admin_has_housing_management_without_personal_subjects(
    portal_context_scenario: PortalContextScenario,
) -> None:
    result = portal_context_scenario.context_for("admin")

    assert result.housing_available is False
    assert result.housing_management_available is True


def test_building_scoped_staff_has_management_without_personal_subjects(
    portal_context_scenario: PortalContextScenario,
) -> None:
    before = portal_context_scenario.context_for("staff")
    portal_context_scenario.allow(
        "staff",
        "housing.read",
        "building",
        portal_context_scenario.building_id,
    )

    result = portal_context_scenario.context_for("staff")

    assert before.housing_management_available is False
    assert before.housing_available is False
    assert result.housing_management_available is True
    assert result.housing_available is False


def test_floor_scoped_staff_has_management_without_personal_subjects(
    portal_context_scenario: PortalContextScenario,
) -> None:
    floor_id = portal_context_scenario.db.scalar(
        select(HousingFloor.id).order_by(
            HousingFloor.building_id,
            HousingFloor.number,
            HousingFloor.id,
        )
    )
    assert floor_id is not None
    portal_context_scenario.allow("staff", "housing.read", "floor", floor_id)

    result = portal_context_scenario.context_for("staff")

    assert result.housing_management_available is True
    assert result.housing_available is False
