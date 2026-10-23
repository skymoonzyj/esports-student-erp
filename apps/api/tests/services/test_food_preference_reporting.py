from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.food_preferences.dependencies import get_food_reporting_db
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceCuisine,
    PersonFoodPreferenceDish,
    PersonFoodPreferenceVersion,
)
from app.food_preferences.reporting import FoodPreferenceReportingService
from app.identity.models import (
    GuardianProfile,
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.rbac.models import Role, UserRole

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


@dataclass(frozen=True)
class ReportingScenario:
    db: Session
    actor_account_id: UUID
    people: dict[str, Person]
    standard_option_id: UUID
    inactive_option_id: UUID
    other_option_id: UUID

    def service(self) -> FoodPreferenceReportingService:
        return FoodPreferenceReportingService(
            self.db,
            account_id=self.actor_account_id,
            occurred_at=NOW,
        )


def _person(label: str, *, deleted: bool = False) -> Person:
    person_id = uuid4()
    return Person(
        id=person_id,
        full_name=label,
        identity_ciphertext=f"food-report-cipher-{person_id.hex}",
        identity_digest=f"food-report-digest-{person_id.hex}",
        sex="female",
        date_of_birth=date(1990, 1, 1),
        deleted_at=NOW if deleted else None,
    )


def _add_current_preference(
    scenario: ReportingScenario,
    *,
    person_key: str,
    cuisines: tuple[tuple[UUID, str, str | None, str | None], ...] = (),
    dishes: tuple[tuple[str, str], ...] = (),
    version: int = 1,
) -> None:
    person_id = scenario.people[person_key].id
    scenario.db.add(
        PersonFoodPreference(
            person_id=person_id,
            version=version,
            source="profile_admin",
            updated_by_user_id=scenario.actor_account_id,
            updated_at=NOW,
        )
    )
    scenario.db.flush()
    scenario.db.add_all(
        [
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=position,
                cuisine_option_id=option_id,
                cuisine_name_snapshot=snapshot,
                custom_name=custom_name,
                custom_normalized_name=custom_normalized_name,
            )
            for position, (
                option_id,
                snapshot,
                custom_name,
                custom_normalized_name,
            ) in enumerate(cuisines, start=1)
        ]
        + [
            PersonFoodPreferenceDish(
                person_id=person_id,
                position=position,
                display_name=display_name,
                normalized_name=normalized_name,
            )
            for position, (display_name, normalized_name) in enumerate(
                dishes,
                start=1,
            )
        ]
    )
    for history_version in range(1, version + 1):
        scenario.db.add(
            PersonFoodPreferenceVersion(
                person_id=person_id,
                version=history_version,
                cuisines=[
                    {
                        "cuisine_option_id": str(option_id),
                        "name": snapshot,
                        "custom_name": custom_name,
                    }
                    for option_id, snapshot, custom_name, _ in cuisines
                ],
                dishes=[display_name for display_name, _ in dishes],
                source="profile_admin",
                updated_by_user_id=scenario.actor_account_id,
                created_at=NOW,
            )
        )


@pytest.fixture
def reporting_scenario(db: Session) -> ReportingScenario:
    people = {
        "admin": _person("00 管理员"),
        "active_student": _person("01 在营学员"),
        "draft_student": _person("02 草稿学员"),
        "inactive_student": _person("03 停用学员"),
        "deleted_student": _person("04 删除学员", deleted=True),
        "staff": _person("05 员工"),
        "deleted_visitor": _person("06 删除访客", deleted=True),
        "pure_guardian": _person("07 监护人"),
        "without_account": _person("08 无账号人员"),
    }
    db.add_all(people.values())
    db.flush()

    actor_account_id = uuid4()
    db.add(
        UserAccount(
            id=actor_account_id,
            person_id=people["admin"].id,
            login_phone_digest=f"food-report-phone-{actor_account_id.hex}",
            email_normalized=f"food-report-{actor_account_id.hex}@example.test",
            password_hash="test-password",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
    )
    admin_role_id = db.scalar(select(Role.id).where(Role.key == "admin"))
    assert admin_role_id is not None
    db.add(UserRole(user_id=actor_account_id, role_id=admin_role_id))
    db.add_all(
        [
            StaffProfile(person_id=people["admin"].id, staff_type="administration"),
            StudentProfile(
                person_id=people["active_student"].id,
                student_number=f"FOOD-R-A-{uuid4().hex[:10]}",
                status="active",
            ),
            StudentProfile(
                person_id=people["draft_student"].id,
                student_number=f"FOOD-R-D-{uuid4().hex[:10]}",
                status="draft",
            ),
            StudentProfile(
                person_id=people["inactive_student"].id,
                student_number=f"FOOD-R-I-{uuid4().hex[:10]}",
                status="inactive",
            ),
            StudentProfile(
                person_id=people["deleted_student"].id,
                student_number=f"FOOD-R-X-{uuid4().hex[:10]}",
                status="active",
            ),
            StaffProfile(person_id=people["staff"].id, staff_type="coach"),
            VisitorProfile(
                person_id=people["deleted_visitor"].id,
                linked_student_id=None,
                visit_reason="餐饮报表测试",
            ),
            GuardianRelationship(
                guardian_person_id=people["active_student"].id,
                student_person_id=people["draft_student"].id,
                relationship="other",
                is_primary=False,
                valid_from=date(2026, 1, 1),
            ),
            GuardianProfile(person_id=people["active_student"].id),
            GuardianProfile(person_id=people["pure_guardian"].id),
        ]
    )
    standard = FoodCuisineOption(
        name="标准菜系",
        normalized_name=f"标准菜系-{uuid4().hex}",
        sort_order=10,
        is_active=True,
        is_other=False,
        created_by_user_id=actor_account_id,
        updated_by_user_id=actor_account_id,
    )
    inactive = FoodCuisineOption(
        name="停用菜系",
        normalized_name=f"停用菜系-{uuid4().hex}",
        sort_order=20,
        is_active=False,
        is_other=False,
        created_by_user_id=actor_account_id,
        updated_by_user_id=actor_account_id,
    )
    db.add_all([standard, inactive])
    db.flush()
    other = db.scalar(
        select(FoodCuisineOption).where(FoodCuisineOption.is_other.is_(True))
    )
    assert other is not None

    scenario = ReportingScenario(
        db=db,
        actor_account_id=actor_account_id,
        people=people,
        standard_option_id=standard.id,
        inactive_option_id=inactive.id,
        other_option_id=other.id,
    )
    _add_current_preference(
        scenario,
        person_key="active_student",
        cuisines=(
            (standard.id, "Zulu", None, None),
            (other.id, "其他", "湘菜", "湘菜"),
        ),
        dishes=(("Mapo", "mapo"),),
        version=2,
    )
    _add_current_preference(
        scenario,
        person_key="draft_student",
        cuisines=((inactive.id, "停用菜系旧名", None, None),),
        dishes=(("ＭＡＰＯ", "mapo"),),
        version=2,
    )
    _add_current_preference(
        scenario,
        person_key="deleted_student",
        cuisines=((standard.id, "Alpha", None, None),),
        dishes=(("寿司", "寿司"),),
    )
    _add_current_preference(
        scenario,
        person_key="staff",
        cuisines=((standard.id, "Beta", None, None),),
        dishes=(("MAPO", "mapo"),),
    )
    _add_current_preference(
        scenario,
        person_key="deleted_visitor",
        cuisines=((other.id, "其他", "湘 菜", "湘菜"),),
    )
    _add_current_preference(scenario, person_key="pure_guardian")
    db.flush()
    return scenario


def _person_keys(
    scenario: ReportingScenario,
    people: tuple,
) -> tuple[str, ...]:
    key_by_id = {person.id: key for key, person in scenario.people.items()}
    return tuple(key_by_id[row.person_id] for row in people)


def test_population_person_rows_use_current_state_and_stable_types(
    reporting_scenario: ReportingScenario,
) -> None:
    scenario = reporting_scenario

    active_students = scenario.service().build_snapshot(
        population="students",
        include_inactive=False,
    )
    all_students = scenario.service().build_snapshot(
        population="students",
        include_inactive=True,
    )
    current_people = scenario.service().build_snapshot(
        population="all_people",
        include_inactive=False,
    )
    all_people = scenario.service().build_snapshot(
        population="all_people",
        include_inactive=True,
    )

    assert _person_keys(scenario, active_students.people) == ("active_student",)
    assert active_students.total_people == 1
    assert active_students.filled_people == 1
    assert _person_keys(scenario, all_students.people) == (
        "active_student",
        "draft_student",
        "inactive_student",
        "deleted_student",
    )
    assert (all_students.total_people, all_students.filled_people) == (4, 3)
    assert _person_keys(scenario, current_people.people) == (
        "admin",
        "active_student",
        "draft_student",
        "inactive_student",
        "staff",
        "pure_guardian",
        "without_account",
    )
    assert (current_people.total_people, current_people.filled_people) == (7, 3)
    assert _person_keys(scenario, all_people.people) == tuple(scenario.people)
    assert (all_people.total_people, all_people.filled_people) == (9, 5)

    rows = {row.person_id: row for row in all_people.people}
    assert rows[scenario.people["active_student"].id].person_types == (
        "student",
        "guardian",
    )
    assert rows[scenario.people["staff"].id].person_types == ("staff",)
    assert rows[scenario.people["pure_guardian"].id].person_types == ("guardian",)
    assert rows[scenario.people["without_account"].id].person_types == ()
    assert rows[scenario.people["draft_student"].id].status == "draft"
    assert rows[scenario.people["inactive_student"].id].status == "inactive"
    assert rows[scenario.people["deleted_student"].id].status == "deleted"
    assert rows[scenario.people["deleted_visitor"].id].status == "deleted"
    assert rows[scenario.people["staff"].id].status == "active"
    assert rows[scenario.people["active_student"].id].cuisines == (
        "Zulu",
        "湘菜",
    )
    assert rows[scenario.people["draft_student"].id].cuisines == (
        "停用菜系旧名",
    )
    assert rows[scenario.people["pure_guardian"].id].cuisines == ()
    assert rows[scenario.people["pure_guardian"].id].dishes == ()
    assert rows[scenario.people["pure_guardian"].id].updated_at == NOW


def test_aggregates_group_current_normalized_keys_before_pagination(
    reporting_scenario: ReportingScenario,
) -> None:
    scenario = reporting_scenario

    snapshot = scenario.service().build_snapshot(
        population="all_people",
        include_inactive=True,
    )
    page = scenario.service().statistics(
        population="all_people",
        include_inactive=True,
        page=2,
        page_size=3,
    )

    assert [(row.label, row.count) for row in snapshot.cuisines] == [
        ("Alpha", 3),
        ("湘 菜", 2),
        ("停用菜系旧名", 1),
    ]
    assert [(row.label, row.count) for row in snapshot.dishes] == [
        ("MAPO", 3),
        ("寿司", 1),
    ]
    assert snapshot.cuisines[0].ratio == pytest.approx(3 / 9)
    assert snapshot.cuisines[1].ratio == pytest.approx(2 / 9)
    assert snapshot.dishes[0].ratio == pytest.approx(3 / 9)
    assert page.total_people == 9
    assert page.filled_people == 5
    assert page.unfilled_people == 4
    assert page.filled_ratio == pytest.approx(5 / 9)
    assert page.cuisines == snapshot.cuisines
    assert page.dishes == snapshot.dishes
    assert page.people == snapshot.people[3:6]
    assert page.page == 2
    assert page.page_size == 3
    assert len(page.access_revision) == 64


def test_multiple_custom_cuisines_sharing_other_option_are_counted_separately(
    reporting_scenario: ReportingScenario,
) -> None:
    scenario = reporting_scenario
    _add_current_preference(
        scenario,
        person_key="without_account",
        cuisines=(
            (scenario.other_option_id, "其他", "川菜", "川菜"),
            (scenario.other_option_id, "其他", "粤菜", "粤菜"),
        ),
        dishes=(("宫保鸡丁", "宫保鸡丁"),),
    )
    scenario.db.flush()

    snapshot = scenario.service().build_snapshot(
        population="all_people",
        include_inactive=False,
    )

    counts = {row.label: row.count for row in snapshot.cuisines}
    assert counts["川菜"] == 1
    assert counts["粤菜"] == 1
    person = next(
        row
        for row in snapshot.people
        if row.person_id == scenario.people["without_account"].id
    )
    assert person.cuisines == ("川菜", "粤菜")


def test_empty_student_population_has_zero_ratios(
    reporting_scenario: ReportingScenario,
) -> None:
    scenario = reporting_scenario
    for key in (
        "active_student",
        "draft_student",
        "inactive_student",
        "deleted_student",
    ):
        profile = scenario.db.get(StudentProfile, scenario.people[key].id)
        assert profile is not None
        profile.status = "inactive"

    snapshot = scenario.service().build_snapshot(
        population="students",
        include_inactive=False,
    )
    page = scenario.service().statistics(
        population="students",
        include_inactive=False,
        page=1,
        page_size=100,
    )

    assert snapshot.total_people == snapshot.filled_people == 0
    assert snapshot.people == snapshot.cuisines == snapshot.dishes == ()
    assert page.total_people == page.filled_people == page.unfilled_people == 0
    assert page.filled_ratio == 0.0


def test_reporting_dependency_uses_a_fresh_repeatable_read_session(
    db: Session,
) -> None:
    dependency = get_food_reporting_db(db)
    reporting_db = next(dependency)
    try:
        assert reporting_db is not db
        assert reporting_db.connection().get_isolation_level().upper() == (
            "REPEATABLE READ"
        )
    finally:
        dependency.close()
