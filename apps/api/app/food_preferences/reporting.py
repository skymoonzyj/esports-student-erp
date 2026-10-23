from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import CTE

from app.food_preferences.access import FoodPreferenceAccess
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceCuisine,
    PersonFoodPreferenceDish,
)
from app.food_preferences.schemas import FoodPersonStatus, FoodPersonType, FoodPopulation
from app.identity.models import (
    GuardianProfile,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)


@dataclass(frozen=True, slots=True)
class FoodPreferencePersonRow:
    person_id: UUID
    full_name: str
    person_types: tuple[FoodPersonType, ...]
    status: FoodPersonStatus
    cuisines: tuple[str, ...]
    dishes: tuple[str, ...]
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class FoodPreferenceRankRow:
    label: str
    count: int
    ratio: float


@dataclass(frozen=True, slots=True)
class FoodPreferenceReportSnapshot:
    population: FoodPopulation
    include_inactive: bool
    total_people: int
    filled_people: int
    people: tuple[FoodPreferencePersonRow, ...]
    cuisines: tuple[FoodPreferenceRankRow, ...]
    dishes: tuple[FoodPreferenceRankRow, ...]
    access_revision: str


@dataclass(frozen=True, slots=True)
class FoodPreferenceStatisticsProjection:
    population: FoodPopulation
    include_inactive: bool
    total_people: int
    filled_people: int
    unfilled_people: int
    filled_ratio: float
    cuisines: tuple[FoodPreferenceRankRow, ...]
    dishes: tuple[FoodPreferenceRankRow, ...]
    people: tuple[FoodPreferencePersonRow, ...]
    page: int
    page_size: int
    access_revision: str


@dataclass(frozen=True, slots=True)
class _CuisineRow:
    person_id: UUID
    position: int
    option_id: UUID
    is_other: bool
    snapshot: str
    custom_name: str | None
    custom_normalized_name: str | None


@dataclass(frozen=True, slots=True)
class _DishRow:
    person_id: UUID
    position: int
    display_name: str
    normalized_name: str


@dataclass(frozen=True, slots=True)
class _PopulationRow:
    person_id: UUID
    full_name: str
    deleted_at: datetime | None
    is_student: bool
    student_status: str | None
    is_guardian: bool
    is_staff: bool
    is_visitor: bool
    updated_at: datetime | None


def _normalized_sort_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


class FoodPreferenceReportingService:
    def __init__(
        self,
        db: Session,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> None:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        self.db = db
        self.account_id = account_id
        self.occurred_at = occurred_at

    @staticmethod
    def _population_cte(
        population: FoodPopulation,
        *,
        include_inactive: bool,
    ) -> CTE:
        statement = (
            select(
                Person.id.label("person_id"),
                Person.full_name.label("full_name"),
                Person.deleted_at.label("deleted_at"),
                StudentProfile.person_id.is_not(None).label("is_student"),
                StudentProfile.status.label("student_status"),
                GuardianProfile.person_id.is_not(None).label("is_guardian"),
                StaffProfile.person_id.is_not(None).label("is_staff"),
                VisitorProfile.person_id.is_not(None).label("is_visitor"),
            )
            .select_from(Person)
            .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
            .outerjoin(GuardianProfile, GuardianProfile.person_id == Person.id)
            .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
            .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
        )
        if population == "students":
            statement = statement.where(StudentProfile.person_id.is_not(None))
            if not include_inactive:
                statement = statement.where(
                    Person.deleted_at.is_(None),
                    StudentProfile.status == "active",
                )
        elif population == "all_people":
            if not include_inactive:
                statement = statement.where(Person.deleted_at.is_(None))
        else:
            raise ValueError("unsupported food preference population")
        return statement.cte("food_preference_population")

    @staticmethod
    def _person_types(row: _PopulationRow) -> tuple[FoodPersonType, ...]:
        types: list[FoodPersonType] = []
        if row.is_student:
            types.append("student")
        if row.is_guardian:
            types.append("guardian")
        if row.is_staff:
            types.append("staff")
        if row.is_visitor:
            types.append("visitor")
        return tuple(types)

    @staticmethod
    def _person_status(row: _PopulationRow) -> FoodPersonStatus:
        if row.deleted_at is not None:
            return "deleted"
        if row.is_student:
            return cast(FoodPersonStatus, row.student_status)
        return "active"

    @staticmethod
    def _cuisine_label(row: _CuisineRow) -> str:
        if row.is_other:
            assert row.custom_name is not None
            return row.custom_name
        return row.snapshot

    @staticmethod
    def _rank_rows(
        groups: dict[tuple[str, str], tuple[set[UUID], set[str]]],
        *,
        total_people: int,
    ) -> tuple[FoodPreferenceRankRow, ...]:
        ranked = [
            (
                min(labels),
                len(person_ids),
                stable_key,
            )
            for stable_key, (person_ids, labels) in groups.items()
        ]
        ranked.sort(
            key=lambda item: (
                -item[1],
                _normalized_sort_text(item[0]),
                item[2],
            )
        )
        return tuple(
            FoodPreferenceRankRow(
                label=label,
                count=count,
                ratio=count / total_people if total_people else 0.0,
            )
            for label, count, _ in ranked
        )

    def _authorize(self) -> str:
        access = FoodPreferenceAccess(
            self.db,
            account_id=self.account_id,
            occurred_at=self.occurred_at,
        )
        access.require_global("food_preferences.export")
        return access.access_revision

    def build_snapshot(
        self,
        *,
        population: FoodPopulation,
        include_inactive: bool,
    ) -> FoodPreferenceReportSnapshot:
        access_revision = self._authorize()
        population_cte = self._population_cte(
            population,
            include_inactive=include_inactive,
        )
        population_rows = tuple(
            _PopulationRow(*row)
            for row in self.db.execute(
                select(
                    population_cte,
                    PersonFoodPreference.updated_at.label("updated_at"),
                )
                .outerjoin(
                    PersonFoodPreference,
                    PersonFoodPreference.person_id == population_cte.c.person_id,
                )
                .order_by(
                    population_cte.c.full_name,
                    population_cte.c.person_id,
                )
            )
        )
        cuisine_rows = tuple(
            _CuisineRow(*row)
            for row in self.db.execute(
                select(
                    PersonFoodPreferenceCuisine.person_id,
                    PersonFoodPreferenceCuisine.position,
                    PersonFoodPreferenceCuisine.cuisine_option_id,
                    FoodCuisineOption.is_other,
                    PersonFoodPreferenceCuisine.cuisine_name_snapshot,
                    PersonFoodPreferenceCuisine.custom_name,
                    PersonFoodPreferenceCuisine.custom_normalized_name,
                )
                .join(
                    population_cte,
                    population_cte.c.person_id
                    == PersonFoodPreferenceCuisine.person_id,
                )
                .join(
                    FoodCuisineOption,
                    FoodCuisineOption.id
                    == PersonFoodPreferenceCuisine.cuisine_option_id,
                )
                .order_by(
                    PersonFoodPreferenceCuisine.person_id,
                    PersonFoodPreferenceCuisine.position,
                )
            )
        )
        dish_rows = tuple(
            _DishRow(*row)
            for row in self.db.execute(
                select(
                    PersonFoodPreferenceDish.person_id,
                    PersonFoodPreferenceDish.position,
                    PersonFoodPreferenceDish.display_name,
                    PersonFoodPreferenceDish.normalized_name,
                )
                .join(
                    population_cte,
                    population_cte.c.person_id == PersonFoodPreferenceDish.person_id,
                )
                .order_by(
                    PersonFoodPreferenceDish.person_id,
                    PersonFoodPreferenceDish.position,
                )
            )
        )

        cuisines_by_person: dict[UUID, list[str]] = {}
        dishes_by_person: dict[UUID, list[str]] = {}
        cuisine_groups: dict[tuple[str, str], tuple[set[UUID], set[str]]] = {}
        dish_groups: dict[tuple[str, str], tuple[set[UUID], set[str]]] = {}
        for cuisine_row in cuisine_rows:
            label = self._cuisine_label(cuisine_row)
            cuisines_by_person.setdefault(cuisine_row.person_id, []).append(label)
            key = (
                ("other", cuisine_row.custom_normalized_name or "")
                if cuisine_row.is_other
                else ("standard", str(cuisine_row.option_id))
            )
            member_ids, labels = cuisine_groups.setdefault(key, (set(), set()))
            member_ids.add(cuisine_row.person_id)
            labels.add(label)
        for dish_row in dish_rows:
            dishes_by_person.setdefault(dish_row.person_id, []).append(
                dish_row.display_name
            )
            key = ("dish", dish_row.normalized_name)
            member_ids, labels = dish_groups.setdefault(key, (set(), set()))
            member_ids.add(dish_row.person_id)
            labels.add(dish_row.display_name)

        person_projections = tuple(
            FoodPreferencePersonRow(
                person_id=population_row.person_id,
                full_name=population_row.full_name,
                person_types=self._person_types(population_row),
                status=self._person_status(population_row),
                cuisines=tuple(
                    cuisines_by_person.get(population_row.person_id, ())
                ),
                dishes=tuple(dishes_by_person.get(population_row.person_id, ())),
                updated_at=population_row.updated_at,
            )
            for population_row in population_rows
        )
        total_people = len(person_projections)
        filled_people = sum(
            bool(person.cuisines or person.dishes) for person in person_projections
        )
        return FoodPreferenceReportSnapshot(
            population=population,
            include_inactive=include_inactive,
            total_people=total_people,
            filled_people=filled_people,
            people=person_projections,
            cuisines=self._rank_rows(
                cuisine_groups,
                total_people=total_people,
            ),
            dishes=self._rank_rows(
                dish_groups,
                total_people=total_people,
            ),
            access_revision=access_revision,
        )

    def statistics(
        self,
        *,
        population: FoodPopulation,
        include_inactive: bool,
        page: int,
        page_size: int,
    ) -> FoodPreferenceStatisticsProjection:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        snapshot = self.build_snapshot(
            population=population,
            include_inactive=include_inactive,
        )
        start = (page - 1) * page_size
        return FoodPreferenceStatisticsProjection(
            population=snapshot.population,
            include_inactive=snapshot.include_inactive,
            total_people=snapshot.total_people,
            filled_people=snapshot.filled_people,
            unfilled_people=snapshot.total_people - snapshot.filled_people,
            filled_ratio=(
                snapshot.filled_people / snapshot.total_people
                if snapshot.total_people
                else 0.0
            ),
            cuisines=snapshot.cuisines,
            dishes=snapshot.dishes,
            people=snapshot.people[start : start + page_size],
            page=page,
            page_size=page_size,
            access_revision=snapshot.access_revision,
        )
