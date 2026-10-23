from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import app.food_preferences.preference_service as preference_service_module
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.models import UserAccount
from app.food_preferences.errors import (
    FoodPreferenceOnboardingIncomplete,
    FoodPreferenceValidationFailed,
    FoodPreferenceVersionChanged,
)
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceVersion,
)
from app.food_preferences.preference_service import (
    FoodPreferenceService,
    ReplaceFoodPreferenceCommand,
)
from app.food_preferences.schemas import (
    CuisineSelectionInput,
    FoodPreferenceWriteRequest,
)
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.rbac.models import UserRole
from app.rbac.seed import ROLES

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


@dataclass
class PreferenceScenario:
    db: Session
    people: dict[str, Person]
    accounts: dict[str, UserAccount]
    options: dict[str, FoodCuisineOption]

    def command(
        self,
        *,
        expected_version: int,
        cuisines: tuple[CuisineSelectionInput, ...] = (),
        dishes: tuple[str, ...] = (),
        actor: str = "admin",
        source: str = "profile_admin",
        request_id: str | None = None,
    ) -> ReplaceFoodPreferenceCommand:
        return ReplaceFoodPreferenceCommand(
            expected_version=expected_version,
            cuisines=cuisines,
            dishes=dishes,
            actor_user_id=self.accounts[actor].id,
            source=source,
            request_id=request_id or f"food-preference-{uuid4()}",
        )

    def selection(
        self,
        key: str,
        *,
        custom_name: str | None = None,
    ) -> CuisineSelectionInput:
        return CuisineSelectionInput(
            cuisine_option_id=self.options[key].id,
            custom_name=custom_name,
        )


@pytest.fixture
def preference_scenario(db: Session) -> PreferenceScenario:
    marker = uuid4().hex[:12]
    definitions = {
        "admin": "staff",
        "student": "student",
        "staff": "staff",
        "visitor": "visitor",
        "guardian": "guardian",
        "guardian_staff": "staff",
        "target": "none",
    }
    people: dict[str, Person] = {}
    accounts: dict[str, UserAccount] = {}
    for index, (key, profile_kind) in enumerate(definitions.items(), start=1):
        person = Person(
            full_name=f"饮食偏好{key}",
            identity_ciphertext=f"food-service-cipher-{marker}-{key}",
            identity_digest=f"food-service-id-{marker}-{key}",
            sex="female" if index % 2 else "male",
            date_of_birth=date(1990, 1, min(index, 28)),
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=f"food-service-phone-{marker}-{key}",
            email_normalized=f"food-service-{marker}-{key}@example.test",
            password_hash="test-password",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        if profile_kind == "student":
            db.add(
                StudentProfile(
                    person_id=person.id,
                    student_number=f"FOOD-SERVICE-{marker[:6]}-{index:02d}",
                    status="active",
                )
            )
            db.add(UserRole(user_id=account.id, role_id=ROLES["student"].id))
        elif profile_kind == "staff":
            db.add(StaffProfile(person_id=person.id, staff_type="other"))
            db.add(UserRole(user_id=account.id, role_id=ROLES["staff"].id))
        elif profile_kind == "visitor":
            db.add(
                VisitorProfile(
                    person_id=person.id,
                    linked_student_id=None,
                    visit_reason="临时入住",
                )
            )
        elif profile_kind == "guardian":
            db.add(UserRole(user_id=account.id, role_id=ROLES["guardian"].id))
        people[key] = person
        accounts[key] = account

    db.add(UserRole(user_id=accounts["admin"].id, role_id=ROLES["super_admin"].id))
    db.add(UserRole(user_id=accounts["guardian_staff"].id, role_id=ROLES["guardian"].id))
    db.add(
        GuardianRelationship(
            guardian_person_id=people["guardian"].id,
            student_person_id=people["student"].id,
            relationship="mother",
            is_primary=True,
            valid_from=date(2026, 1, 1),
        )
    )
    db.add(
        GuardianRelationship(
            guardian_person_id=people["guardian_staff"].id,
            student_person_id=people["student"].id,
            relationship="father",
            is_primary=False,
            valid_from=date(2026, 1, 1),
        )
    )
    active = FoodCuisineOption(
        name=f"测试川菜{marker[:4]}",
        normalized_name=f"测试川菜{marker[:4]}".casefold(),
        sort_order=10,
        is_active=True,
        is_other=False,
        created_by_user_id=accounts["admin"].id,
        updated_by_user_id=accounts["admin"].id,
    )
    inactive = FoodCuisineOption(
        name=f"停用菜系{marker[:4]}",
        normalized_name=f"停用菜系{marker[:4]}".casefold(),
        sort_order=20,
        is_active=False,
        is_other=False,
        created_by_user_id=accounts["admin"].id,
        updated_by_user_id=accounts["admin"].id,
    )
    db.add_all([active, inactive])
    db.flush()
    other = db.scalar(select(FoodCuisineOption).where(FoodCuisineOption.is_other.is_(True)))
    assert other is not None
    db.commit()
    return PreferenceScenario(
        db=db,
        people=people,
        accounts=accounts,
        options={"active": active, "inactive": inactive, "other": other},
    )


def _counts(db: Session, person_id: UUID) -> tuple[int, int, int, int]:
    return (
        db.scalar(
            select(func.count(PersonFoodPreference.person_id)).where(
                PersonFoodPreference.person_id == person_id
            )
        )
        or 0,
        db.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == person_id
            )
        )
        or 0,
        db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.resource_id == person_id)
        )
        or 0,
        db.scalar(
            select(func.count(UserAccount.id)).where(
                UserAccount.person_id == person_id,
                UserAccount.food_preferences_onboarding_completed_at.is_not(None),
            )
        )
        or 0,
    )


def test_missing_projection_is_version_zero_and_first_save_is_ordered(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    service = FoodPreferenceService(scenario.db)
    target_id = scenario.people["target"].id
    empty = service.get(target_id)
    assert (empty.version, empty.cuisines, empty.dishes, empty.updated_at) == (
        0,
        (),
        (),
        None,
    )

    result = service.replace(
        target_id,
        scenario.command(
            expected_version=0,
            cuisines=(
                scenario.selection("other", custom_name="  川 渝  "),
                scenario.selection("active"),
            ),
            dishes=("  宫保  鸡丁 ", "清蒸鱼"),
        ),
    )

    assert result.version == 1
    assert [item.cuisine_option_id for item in result.cuisines] == [
        scenario.options["other"].id,
        scenario.options["active"].id,
    ]
    assert result.cuisines[0].custom_name == "川 渝"
    assert result.dishes == ("宫保 鸡丁", "清蒸鱼")
    assert result.updated_at is not None and result.updated_at.tzinfo is not None
    history = scenario.db.scalar(
        select(PersonFoodPreferenceVersion).where(
            PersonFoodPreferenceVersion.person_id == target_id,
            PersonFoodPreferenceVersion.version == 1,
        )
    )
    assert history is not None
    assert history.cuisines == [
        {
            "cuisine_option_id": str(scenario.options["other"].id),
            "name": "其他",
            "custom_name": "川 渝",
        },
        {
            "cuisine_option_id": str(scenario.options["active"].id),
            "name": scenario.options["active"].name,
            "custom_name": None,
        },
    ]
    assert history.dishes == ["宫保 鸡丁", "清蒸鱼"]
    event = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == target_id,
            AuditEvent.action == "food_preferences.profile.update",
        )
    )
    assert event is not None
    assert event.event_metadata == {
        "person_id": str(target_id),
        "old_version": 0,
        "new_version": 1,
        "cuisine_count": 2,
        "dish_count": 2,
        "source": "profile_admin",
    }


def test_three_distinct_custom_cuisines_share_other_option_and_keep_history(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    target_id = scenario.people["target"].id

    result = FoodPreferenceService(scenario.db).replace(
        target_id,
        scenario.command(
            expected_version=0,
            cuisines=tuple(
                scenario.selection("other", custom_name=name)
                for name in ("川菜", "粤菜", "东北菜")
            ),
        ),
    )

    assert [item.cuisine_option_id for item in result.cuisines] == [
        scenario.options["other"].id,
        scenario.options["other"].id,
        scenario.options["other"].id,
    ]
    assert [item.custom_name for item in result.cuisines] == [
        "川菜",
        "粤菜",
        "东北菜",
    ]
    history = scenario.db.scalar(
        select(PersonFoodPreferenceVersion).where(
            PersonFoodPreferenceVersion.person_id == target_id,
            PersonFoodPreferenceVersion.version == 1,
        )
    )
    assert history is not None
    assert [item["custom_name"] for item in history.cuisines] == [
        "川菜",
        "粤菜",
        "东北菜",
    ]


def test_custom_cuisine_cannot_duplicate_selected_standard_name(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    target_id = scenario.people["target"].id
    before = _counts(scenario.db, target_id)

    with pytest.raises(FoodPreferenceValidationFailed):
        FoodPreferenceService(scenario.db).replace(
            target_id,
            scenario.command(
                expected_version=0,
                cuisines=(
                    scenario.selection("active"),
                    scenario.selection(
                        "other",
                        custom_name=scenario.options["active"].name,
                    ),
                ),
            ),
        )

    assert _counts(scenario.db, target_id) == before


def test_first_empty_save_is_noop_exact_save_reuses_and_clear_versions(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    service = FoodPreferenceService(scenario.db)
    target_id = scenario.people["target"].id
    empty = service.replace(target_id, scenario.command(expected_version=0))
    assert empty.version == 0
    assert _counts(scenario.db, target_id)[:2] == (0, 0)

    first_command = scenario.command(
        expected_version=0,
        cuisines=(scenario.selection("active"),),
        dishes=("宫保鸡丁",),
    )
    first = service.replace(target_id, first_command)
    counts_after_first = _counts(scenario.db, target_id)
    noop = service.replace(
        target_id,
        scenario.command(
            expected_version=1,
            cuisines=(scenario.selection("active"),),
            dishes=("宫保鸡丁",),
        ),
    )
    assert noop == first
    assert _counts(scenario.db, target_id) == counts_after_first

    cleared = service.replace(target_id, scenario.command(expected_version=1))
    assert (cleared.version, cleared.cuisines, cleared.dishes) == (2, (), ())
    assert scenario.db.scalar(
        select(func.count(PersonFoodPreferenceVersion.id)).where(
            PersonFoodPreferenceVersion.person_id == target_id
        )
    ) == 2


@pytest.mark.parametrize(
    "invalid_kind",
    [
        "inactive_new",
        "missing_option",
        "other_without_custom",
        "normal_with_custom",
        "duplicate_option",
        "duplicate_custom",
        "duplicate_dish",
        "four_cuisines",
        "four_dishes",
    ],
)
def test_replace_rejects_invalid_selection_without_writes(
    preference_scenario: PreferenceScenario,
    invalid_kind: str,
) -> None:
    scenario = preference_scenario
    target_id = scenario.people["target"].id
    cuisines: tuple[CuisineSelectionInput, ...] = (scenario.selection("active"),)
    dishes = ("宫保鸡丁",)
    if invalid_kind == "inactive_new":
        cuisines = (scenario.selection("inactive"),)
    elif invalid_kind == "missing_option":
        cuisines = (CuisineSelectionInput(cuisine_option_id=uuid4()),)
    elif invalid_kind == "other_without_custom":
        cuisines = (scenario.selection("other"),)
    elif invalid_kind == "normal_with_custom":
        cuisines = (scenario.selection("active", custom_name="川渝"),)
    elif invalid_kind == "duplicate_option":
        cuisines = (scenario.selection("active"), scenario.selection("active"))
    elif invalid_kind == "duplicate_custom":
        cuisines = (
            scenario.selection("other", custom_name="ＳｉＣｈｕａｎ"),
            CuisineSelectionInput(cuisine_option_id=uuid4(), custom_name=" sichuan "),
        )
    elif invalid_kind == "duplicate_dish":
        dishes = ("ＳｉＣｈｕａｎ", " sichuan ")
    elif invalid_kind == "four_cuisines":
        cuisines = tuple(scenario.selection("active") for _ in range(4))
    elif invalid_kind == "four_dishes":
        dishes = ("一", "二", "三", "四")
    before = _counts(scenario.db, target_id)

    with pytest.raises(FoodPreferenceValidationFailed):
        FoodPreferenceService(scenario.db).replace(
            target_id,
            scenario.command(
                expected_version=0,
                cuisines=cuisines,
                dishes=dishes,
            ),
        )

    assert _counts(scenario.db, target_id) == before


def test_inactive_existing_selection_keeps_snapshot_while_other_content_changes(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    service = FoodPreferenceService(scenario.db)
    target_id = scenario.people["target"].id
    scenario.options["inactive"].is_active = True
    scenario.db.commit()
    service.replace(
        target_id,
        scenario.command(
            expected_version=0,
            cuisines=(scenario.selection("inactive"),),
            dishes=("第一道菜",),
        ),
    )
    original_name = scenario.options["inactive"].name
    renamed = f"重命名停用菜系{uuid4().hex[:4]}"
    scenario.options["inactive"].name = renamed
    scenario.options["inactive"].normalized_name = renamed.casefold()
    scenario.options["inactive"].is_active = False
    scenario.db.commit()

    changed = service.replace(
        target_id,
        scenario.command(
            expected_version=1,
            cuisines=(scenario.selection("inactive"),),
            dishes=("第二道菜",),
        ),
    )

    assert changed.version == 2
    assert changed.cuisines[0].cuisine_option_id == scenario.options["inactive"].id
    assert changed.cuisines[0].name == original_name
    history = scenario.db.scalar(
        select(PersonFoodPreferenceVersion).where(
            PersonFoodPreferenceVersion.person_id == target_id,
            PersonFoodPreferenceVersion.version == 2,
        )
    )
    assert history is not None
    assert history.cuisines[0]["name"] == original_name


def test_stale_version_rolls_back_and_session_remains_usable(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    service = FoodPreferenceService(scenario.db)
    target_id = scenario.people["target"].id
    service.replace(
        target_id,
        scenario.command(
            expected_version=0,
            cuisines=(scenario.selection("active"),),
            dishes=("第一版",),
        ),
    )
    before = _counts(scenario.db, target_id)

    with pytest.raises(FoodPreferenceVersionChanged):
        service.replace(
            target_id,
            scenario.command(
                expected_version=0,
                cuisines=(scenario.selection("active"),),
                dishes=("过期写入",),
            ),
        )

    assert _counts(scenario.db, target_id) == before
    assert scenario.db.scalar(select(1)) == 1
    assert service.get(target_id).dishes == ("第一版",)


@pytest.mark.parametrize("account_key", ["student", "staff", "visitor", "guardian_staff"])
def test_eligible_self_onboarding_requires_one_plus_one_and_completes_atomically(
    preference_scenario: PreferenceScenario,
    account_key: str,
) -> None:
    scenario = preference_scenario
    service = FoodPreferenceService(scenario.db)
    account = scenario.accounts[account_key]
    before = _counts(scenario.db, account.person_id)
    with pytest.raises(FoodPreferenceOnboardingIncomplete):
        service.replace_self(
            account_id=account.id,
            input=FoodPreferenceWriteRequest(expected_version=0),
            request_id=f"onboarding-incomplete-{account_key}",
            occurred_at=NOW,
        )
    assert _counts(scenario.db, account.person_id) == before

    completed = service.replace_self(
        account_id=account.id,
        input=FoodPreferenceWriteRequest(
            expected_version=0,
            cuisines=[scenario.selection("active")],
            dishes=["宫保鸡丁"],
        ),
        request_id=f"onboarding-complete-{account_key}",
        occurred_at=NOW,
    )

    assert completed.onboarding_required is False
    assert completed.onboarding_completed_at == NOW
    assert completed.profile.version == 1
    scenario.db.refresh(account)
    assert account.food_preferences_onboarding_completed_at == NOW


def test_pure_guardian_is_exempt_and_may_keep_optional_empty_profile(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    account = scenario.accounts["guardian"]

    projection = FoodPreferenceService(scenario.db).replace_self(
        account_id=account.id,
        input=FoodPreferenceWriteRequest(expected_version=0),
        request_id="guardian-optional-empty",
        occurred_at=NOW,
    )

    assert projection.onboarding_required is False
    assert projection.onboarding_completed_at is None
    assert projection.profile.version == 0
    assert _counts(scenario.db, account.person_id) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("account_key", "onboarding_required"),
    [
        ("student", True),
        ("staff", True),
        ("visitor", True),
        ("guardian_staff", True),
        ("guardian", False),
    ],
)
def test_get_self_projects_profile_and_server_owned_onboarding_eligibility(
    preference_scenario: PreferenceScenario,
    account_key: str,
    onboarding_required: bool,
) -> None:
    scenario = preference_scenario
    account = scenario.accounts[account_key]

    projection = FoodPreferenceService(scenario.db).get_self(account_id=account.id)

    assert projection.profile.person_id == account.person_id
    assert projection.profile.version == 0
    assert projection.onboarding_required is onboarding_required
    assert projection.onboarding_completed_at is None


def test_admin_prefill_does_not_complete_but_self_confirmation_does_without_new_version(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    service = FoodPreferenceService(scenario.db)
    student = scenario.accounts["student"]
    service.replace(
        student.person_id,
        scenario.command(
            expected_version=0,
            cuisines=(scenario.selection("active"),),
            dishes=("预填菜品",),
        ),
    )
    scenario.db.refresh(student)
    assert student.food_preferences_onboarding_completed_at is None
    history_before = scenario.db.scalar(
        select(func.count(PersonFoodPreferenceVersion.id)).where(
            PersonFoodPreferenceVersion.person_id == student.person_id
        )
    )

    confirmed = service.replace_self(
        account_id=student.id,
        input=FoodPreferenceWriteRequest(
            expected_version=1,
            cuisines=[scenario.selection("active")],
            dishes=["预填菜品"],
        ),
        request_id="student-confirms-prefill",
        occurred_at=NOW,
    )

    assert confirmed.profile.version == 1
    assert confirmed.onboarding_completed_at == NOW
    assert scenario.db.scalar(
        select(func.count(PersonFoodPreferenceVersion.id)).where(
            PersonFoodPreferenceVersion.person_id == student.person_id
        )
    ) == history_before


def test_self_replace_refreshes_completion_after_reauthorization(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    student = scenario.accounts["student"]
    completed_at = NOW.replace(hour=11)

    def complete_during_reauthorization() -> None:
        scenario.db.execute(
            update(UserAccount)
            .where(UserAccount.id == student.id)
            .values(food_preferences_onboarding_completed_at=completed_at)
            .execution_options(synchronize_session=False)
        )

    result = FoodPreferenceService(scenario.db).replace_self(
        account_id=student.id,
        input=FoodPreferenceWriteRequest(
            expected_version=0,
            cuisines=[scenario.selection("active")],
            dishes=["宫保鸡丁"],
        ),
        request_id="student-completed-during-reauthorization",
        occurred_at=NOW,
        reauthorize=complete_during_reauthorization,
    )

    assert result.onboarding_required is False
    assert result.onboarding_completed_at == completed_at
    scenario.db.refresh(student)
    assert student.food_preferences_onboarding_completed_at == completed_at
    assert scenario.db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == student.person_id,
            AuditEvent.action == "food_preferences.onboarding.complete",
        )
    ) == 0


def test_clearing_after_onboarding_does_not_reset_completion(
    preference_scenario: PreferenceScenario,
) -> None:
    scenario = preference_scenario
    service = FoodPreferenceService(scenario.db)
    account = scenario.accounts["student"]
    service.replace_self(
        account_id=account.id,
        input=FoodPreferenceWriteRequest(
            expected_version=0,
            cuisines=[scenario.selection("active")],
            dishes=["宫保鸡丁"],
        ),
        request_id="student-onboarding-before-clear",
        occurred_at=NOW,
    )

    cleared = service.replace_self(
        account_id=account.id,
        input=FoodPreferenceWriteRequest(expected_version=1),
        request_id="student-clears-later",
        occurred_at=NOW.replace(hour=13),
    )

    assert cleared.profile.version == 2
    assert cleared.onboarding_completed_at == NOW
    assert cleared.onboarding_required is False


def test_onboarding_audit_failure_rolls_back_completion_and_keeps_session_usable(
    preference_scenario: PreferenceScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = preference_scenario
    service = FoodPreferenceService(scenario.db)
    student = scenario.accounts["student"]
    service.replace(
        student.person_id,
        scenario.command(
            expected_version=0,
            cuisines=(scenario.selection("active"),),
            dishes=("预填菜品",),
        ),
    )
    before = _counts(scenario.db, student.person_id)

    def reject_audit(*args, **kwargs) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated onboarding audit failure")

    monkeypatch.setattr(preference_service_module.AuditService, "append", reject_audit)
    with pytest.raises(AuditMetadataRejected):
        service.replace_self(
            account_id=student.id,
            input=FoodPreferenceWriteRequest(
                expected_version=1,
                cuisines=[scenario.selection("active")],
                dishes=["预填菜品"],
            ),
            request_id="student-onboarding-audit-failure",
            occurred_at=NOW,
        )

    scenario.db.expire_all()
    assert _counts(scenario.db, student.person_id) == before
    assert scenario.db.get(UserAccount, student.id).food_preferences_onboarding_completed_at is None  # type: ignore[union-attr]
    assert scenario.db.scalar(select(1)) == 1


def test_profile_audit_failure_rolls_back_current_and_history(
    preference_scenario: PreferenceScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = preference_scenario
    target_id = scenario.people["target"].id
    before = _counts(scenario.db, target_id)

    def reject_audit(*args, **kwargs) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated profile audit failure")

    monkeypatch.setattr(preference_service_module.AuditService, "append", reject_audit)
    with pytest.raises(AuditMetadataRejected):
        FoodPreferenceService(scenario.db).replace(
            target_id,
            scenario.command(
                expected_version=0,
                cuisines=(scenario.selection("active"),),
                dishes=("审计失败菜品",),
            ),
        )

    scenario.db.expire_all()
    assert _counts(scenario.db, target_id) == before
    assert scenario.db.scalar(select(1)) == 1
    assert FoodPreferenceService(scenario.db).get(target_id).version == 0
