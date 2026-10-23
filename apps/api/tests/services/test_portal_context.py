from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.food_preferences.models import PersonFoodPreference
from app.housing.models import HousingBuilding, HousingFloor
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
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
        "visitor": (),
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
        if key in {"admin", "coach", "multi", "staff"}:
            staff_type = {
                "admin": "administration",
                "staff": "life_teacher",
            }.get(key, "coach")
            db.add(
                StaffProfile(
                    person_id=person.id,
                    staff_type=staff_type,
                )
            )
        if key == "visitor":
            db.add(
                VisitorProfile(
                    person_id=person.id,
                    linked_student_id=None,
                    visit_reason="餐饮偏好门户测试访客",
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
        invitations_available=False,
        adult_guardian_invitations_available=False,
        food_profile_available=True,
        food_preferences_onboarding_required=True,
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
        invitations_available=False,
        adult_guardian_invitations_available=False,
        food_profile_available=True,
        food_preferences_onboarding_required=True,
        learning_mode=None,
    )


def test_context_exposes_coach_without_global_housing_management(
    portal_context_scenario: PortalContextScenario,
) -> None:
    result = portal_context_scenario.context_for("coach")

    assert result == PortalContextResponse(
        coach_available=True,
        learning_available=False,
        payments_available=False,
        housing_management_available=False,
        housing_available=False,
        invitations_available=False,
        adult_guardian_invitations_available=False,
        food_profile_available=True,
        food_preferences_onboarding_required=True,
        learning_mode=None,
    )


def test_staff_applicant_without_staff_profile_still_requires_food_onboarding(
    portal_context_scenario: PortalContextScenario,
) -> None:
    account = portal_context_scenario.accounts["visitor"]
    person = portal_context_scenario.people["visitor"]
    visitor_profile = portal_context_scenario.db.get(VisitorProfile, person.id)
    assert visitor_profile is not None
    portal_context_scenario.db.delete(visitor_profile)
    account.self_service_primary_purpose = "staff_applicant"
    portal_context_scenario.db.flush()

    result = portal_context_scenario.context_for("visitor")

    assert result.food_profile_available is True
    assert result.food_preferences_onboarding_required is True


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


def test_adult_student_guardian_invitation_capability_is_independent_of_food_access(
    portal_context_scenario: PortalContextScenario,
) -> None:
    portal_context_scenario.deny("student", "food_preferences.read", "self")

    result = portal_context_scenario.context_for("student")

    assert result.food_profile_available is False
    assert result.adult_guardian_invitations_available is True


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
            role_id=ROLES["super_admin"].id,
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


@pytest.mark.parametrize(
    ("account_key", "profile_available", "onboarding_required"),
    [
        ("student", True, True),
        ("student_guardian", True, True),
        ("coach", True, True),
        ("multi", True, True),
        ("staff", True, True),
        ("visitor", True, True),
        ("guardian", True, False),
        ("admin", True, True),
    ],
)
def test_food_profile_and_onboarding_capabilities_follow_person_profiles(
    portal_context_scenario: PortalContextScenario,
    account_key: str,
    profile_available: bool,
    onboarding_required: bool,
) -> None:
    result = portal_context_scenario.context_for(account_key)

    assert result.food_profile_available is profile_available
    assert result.food_preferences_onboarding_required is onboarding_required


def test_prefilled_preferences_do_not_complete_onboarding(
    portal_context_scenario: PortalContextScenario,
) -> None:
    staff = portal_context_scenario.accounts["staff"]
    portal_context_scenario.db.add(
        PersonFoodPreference(
            person_id=staff.person_id,
            version=1,
            source="profile_admin",
            updated_by_user_id=portal_context_scenario.admin_account.id,
            updated_at=NOW,
        )
    )
    portal_context_scenario.db.flush()

    prefilled = portal_context_scenario.context_for("staff")

    assert prefilled.food_preferences_onboarding_required is True

    staff.food_preferences_onboarding_completed_at = NOW
    portal_context_scenario.db.flush()

    completed = portal_context_scenario.context_for("staff")

    assert completed.food_profile_available is True
    assert completed.food_preferences_onboarding_required is False


def test_food_capabilities_honor_read_and_update_self_denies(
    portal_context_scenario: PortalContextScenario,
) -> None:
    portal_context_scenario.deny(
        "staff",
        "food_preferences.read",
        "self",
    )
    portal_context_scenario.deny(
        "coach",
        "food_preferences.update",
        "self",
    )

    read_denied = portal_context_scenario.context_for("staff")
    update_denied = portal_context_scenario.context_for("coach")

    assert read_denied.food_profile_available is False
    assert read_denied.food_preferences_onboarding_required is False
    assert update_denied.food_profile_available is True
    assert update_denied.food_preferences_onboarding_required is False


@pytest.mark.parametrize("disabled_kind", ["account", "person"])
def test_disabled_food_profile_subject_has_no_portal_projection(
    portal_context_scenario: PortalContextScenario,
    disabled_kind: str,
) -> None:
    if disabled_kind == "account":
        portal_context_scenario.accounts["visitor"].status = "disabled"
    else:
        portal_context_scenario.people["visitor"].deleted_at = NOW
    portal_context_scenario.db.flush()

    result = portal_context_scenario.context_for("visitor")

    assert result.food_profile_available is False
    assert result.food_preferences_onboarding_required is False


def test_food_capabilities_require_the_account_actor_person(
    portal_context_scenario: PortalContextScenario,
) -> None:
    staff = portal_context_scenario.accounts["staff"]

    result = PortalContextService(portal_context_scenario.db).get_context(
        user_id=staff.id,
        actor_person_id=portal_context_scenario.people["visitor"].id,
        occurred_at=NOW,
    )

    assert result.food_profile_available is False
    assert result.food_preferences_onboarding_required is False
