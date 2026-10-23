from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.food_preferences.access import FoodPreferenceAccess
from app.food_preferences.errors import (
    FoodPreferenceNotFound,
    FoodPreferencePermissionDenied,
)
from app.food_preferences.models import (
    PersonFoodPreference,
    PersonFoodPreferenceDish,
    PersonFoodPreferenceVersion,
)
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES

NOW = datetime(2026, 8, 11, 8, tzinfo=UTC)


@dataclass
class AccessScenario:
    db: Session
    people: dict[str, Person]
    accounts: dict[str, UserAccount]
    relationships: dict[str, GuardianRelationship]

    def access(self, key: str) -> FoodPreferenceAccess:
        return FoodPreferenceAccess(
            self.db,
            account_id=self.accounts[key].id,
            occurred_at=NOW,
        )

    def override(
        self,
        key: str,
        permission: str,
        effect: str,
        scope_kind: str,
        scope_id: UUID | None = None,
    ) -> UserPermissionOverride:
        row = UserPermissionOverride(
            user_id=self.accounts[key].id,
            permission_id=PERMISSIONS[permission].id,
            effect=effect,
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
        self.db.add(row)
        self.db.flush()
        return row


@pytest.fixture
def access_scenario(db: Session) -> AccessScenario:
    marker = uuid4().hex[:12]
    definitions = {
        "admin": (("admin",), False),
        "student": (("student",), True),
        "other_student": (("student",), True),
        "guardian": (("guardian",), False),
        "ended_guardian": (("guardian",), False),
        "coach": (("coach",), False),
        "disabled": (("student",), True),
        "deleted": ((), False),
        "roleless": ((), False),
    }
    people: dict[str, Person] = {}
    accounts: dict[str, UserAccount] = {}
    for index, (key, (role_keys, is_student)) in enumerate(
        definitions.items(), start=1
    ):
        person = Person(
            full_name=f"饮食授权{key}",
            identity_ciphertext=f"food-access-cipher-{marker}-{key}",
            identity_digest=f"food-access-id-{marker}-{key}",
            sex="female" if index % 2 else "male",
            date_of_birth=date(1990, 1, min(index, 28)),
            deleted_at=NOW if key == "deleted" else None,
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=f"food-access-phone-{marker}-{key}",
            email_normalized=f"food-access-{marker}-{key}@example.test",
            password_hash="test-password",
            status="disabled" if key == "disabled" else "active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        for role_key in role_keys:
            db.add(UserRole(user_id=account.id, role_id=ROLES[role_key].id))
        if is_student:
            db.add(
                StudentProfile(
                    person_id=person.id,
                    student_number=f"FOOD-ACCESS-{marker[:6]}-{index:02d}",
                    status="active",
                )
            )
        people[key] = person
        accounts[key] = account

    active = GuardianRelationship(
        guardian_person_id=people["guardian"].id,
        student_person_id=people["student"].id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
    )
    ended = GuardianRelationship(
        guardian_person_id=people["ended_guardian"].id,
        student_person_id=people["student"].id,
        relationship="father",
        is_primary=False,
        valid_from=date(2026, 1, 1),
        ended_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
    )
    db.add_all([active, ended])
    db.flush()
    return AccessScenario(
        db=db,
        people=people,
        accounts=accounts,
        relationships={"active": active, "ended": ended},
    )


def _write_counts(db: Session) -> tuple[int, int, int, int]:
    return (
        db.scalar(select(func.count(PersonFoodPreference.person_id))) or 0,
        db.scalar(select(func.count(PersonFoodPreferenceVersion.id))) or 0,
        db.scalar(
            select(func.count(UserAccount.id)).where(
                UserAccount.food_preferences_onboarding_completed_at.is_not(None)
            )
        )
        or 0,
        db.scalar(select(func.count(AuditEvent.id))) or 0,
    )


def test_access_allows_admin_student_family_exact_person_and_self_baseline(
    access_scenario: AccessScenario,
) -> None:
    scenario = access_scenario
    student_id = scenario.people["student"].id
    other_id = scenario.people["other_student"].id

    assert scenario.access("admin").require_target(
        "food_preferences.read", other_id
    ).id == other_id
    assert scenario.access("student").require_target(
        "food_preferences.read", student_id
    ).id == student_id
    assert scenario.access("guardian").require_target(
        "food_preferences.update", student_id
    ).id == student_id
    assert scenario.access("coach").require_self("food_preferences.read").id == (
        scenario.people["coach"].id
    )
    assert scenario.access("roleless").require_self("food_preferences.update").id == (
        scenario.people["roleless"].id
    )

    scenario.override(
        "coach",
        "food_preferences.read",
        "allow",
        "person",
        other_id,
    )
    assert scenario.access("coach").require_target(
        "food_preferences.read", other_id
    ).id == other_id


def test_any_capability_includes_self_baseline_but_not_other_targets(
    access_scenario: AccessScenario,
) -> None:
    coach_access = access_scenario.access("coach")
    coach_access.require_any_capability("food_preferences.read")

    with pytest.raises(FoodPreferenceNotFound):
        coach_access.require_target(
            "food_preferences.read",
            access_scenario.people["student"].id,
        )


@pytest.mark.parametrize(
    "target_kind",
    ["missing", "deleted", "unrelated", "ended_family", "explicit_deny"],
)
def test_target_failures_are_private_equivalent_and_write_nothing(
    access_scenario: AccessScenario,
    target_kind: str,
) -> None:
    scenario = access_scenario
    actor_key = "guardian"
    target_id = scenario.people["other_student"].id
    if target_kind == "missing":
        target_id = uuid4()
    elif target_kind == "deleted":
        target_id = scenario.people["deleted"].id
    elif target_kind == "ended_family":
        actor_key = "ended_guardian"
        target_id = scenario.people["student"].id
    elif target_kind == "explicit_deny":
        target_id = scenario.people["student"].id
        scenario.override(
            "guardian",
            "food_preferences.read",
            "deny",
            "person",
            target_id,
        )
    before = _write_counts(scenario.db)

    with pytest.raises(FoodPreferenceNotFound, match="food preference target not found"):
        scenario.access(actor_key).require_target("food_preferences.read", target_id)

    assert _write_counts(scenario.db) == before


def test_global_deny_and_disabled_account_have_no_operation_capability(
    access_scenario: AccessScenario,
) -> None:
    scenario = access_scenario
    scenario.override("coach", "food_preferences.read", "deny", "global")

    with pytest.raises(FoodPreferencePermissionDenied):
        scenario.access("coach").require_any_capability("food_preferences.read")
    with pytest.raises(FoodPreferencePermissionDenied):
        scenario.access("disabled").require_self("food_preferences.read")


def test_self_deny_revokes_only_the_self_baseline(access_scenario: AccessScenario) -> None:
    scenario = access_scenario
    scenario.override("coach", "food_preferences.update", "deny", "self")
    before = _write_counts(scenario.db)

    with pytest.raises(FoodPreferencePermissionDenied):
        scenario.access("coach").require_self("food_preferences.update")

    assert _write_counts(scenario.db) == before


def test_student_self_deny_revokes_role_and_baseline_capability(
    access_scenario: AccessScenario,
) -> None:
    scenario = access_scenario
    scenario.override("student", "food_preferences.update", "deny", "self")

    with pytest.raises(FoodPreferencePermissionDenied):
        scenario.access("student").require_any_capability("food_preferences.update")


def test_can_target_is_false_for_private_or_denied_targets(
    access_scenario: AccessScenario,
) -> None:
    access = access_scenario.access("coach")
    assert access.can_target(
        "food_preferences.read", access_scenario.people["coach"].id
    ) is False
    assert access.can_target("food_preferences.read", uuid4()) is False


def test_access_revision_changes_only_for_relevant_authorization_state(
    access_scenario: AccessScenario,
) -> None:
    scenario = access_scenario
    initial = scenario.access("guardian").access_revision

    irrelevant = Person(
        full_name="不相关人员",
        identity_ciphertext=f"irrelevant-{uuid4()}",
        identity_digest=f"irrelevant-{uuid4()}",
        sex="female",
        date_of_birth=date(1990, 1, 1),
    )
    scenario.db.add(irrelevant)
    scenario.db.flush()
    assert scenario.access("guardian").access_revision == initial

    scenario.relationships["active"].ended_at = NOW
    scenario.db.flush()
    relationship_revision = scenario.access("guardian").access_revision
    assert relationship_revision != initial

    scenario.override(
        "guardian",
        "food_preferences.read",
        "deny",
        "self",
    )
    assert scenario.access("guardian").access_revision != relationship_revision


def test_access_revision_changes_when_scheduled_family_relationship_starts(
    access_scenario: AccessScenario,
) -> None:
    scenario = access_scenario
    scenario.db.add(
        GuardianRelationship(
            guardian_person_id=scenario.people["guardian"].id,
            student_person_id=scenario.people["other_student"].id,
            relationship="mother",
            is_primary=False,
            valid_from=(NOW + timedelta(days=1)).date(),
        )
    )
    scenario.db.flush()

    before = scenario.access("guardian").access_revision
    after = FoodPreferenceAccess(
        scenario.db,
        account_id=scenario.accounts["guardian"].id,
        occurred_at=NOW + timedelta(days=1),
    ).access_revision

    assert after != before


def test_access_revision_ignores_raw_food_text(access_scenario: AccessScenario) -> None:
    scenario = access_scenario
    initial = scenario.access("guardian").access_revision
    target = scenario.people["other_student"]
    root = PersonFoodPreference(
        person_id=target.id,
        version=1,
        source="profile_admin",
        updated_by_user_id=scenario.accounts["admin"].id,
    )
    scenario.db.add(root)
    scenario.db.flush()
    scenario.db.add(
        PersonFoodPreferenceDish(
            person_id=target.id,
            position=1,
            display_name="绝不能进入授权摘要的菜名",
            normalized_name="绝不能进入授权摘要的菜名",
        )
    )
    scenario.db.flush()

    assert scenario.access("guardian").access_revision == initial
