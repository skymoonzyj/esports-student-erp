from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.transactions import write_transaction
from app.food_preferences.errors import (
    FoodPreferenceNotFound,
    FoodPreferenceOnboardingIncomplete,
    FoodPreferenceValidationFailed,
    FoodPreferenceVersionChanged,
)
from app.food_preferences.models import (
    FOOD_PREFERENCE_SOURCES,
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceCuisine,
    PersonFoodPreferenceDish,
    PersonFoodPreferenceVersion,
)
from app.food_preferences.normalization import (
    FoodTextValidationError,
    normalize_food_display,
    normalize_food_key,
)
from app.food_preferences.schemas import (
    CuisineSelectionInput,
    FoodPreferenceWriteRequest,
)
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile


@dataclass(frozen=True)
class ReplaceFoodPreferenceCommand:
    expected_version: int
    cuisines: tuple[CuisineSelectionInput, ...]
    dishes: tuple[str, ...]
    actor_user_id: UUID
    source: str
    request_id: str


@dataclass(frozen=True)
class FoodPreferenceCuisineProjection:
    cuisine_option_id: UUID
    name: str
    custom_name: str | None


@dataclass(frozen=True)
class FoodPreferenceProjection:
    person_id: UUID
    version: int
    cuisines: tuple[FoodPreferenceCuisineProjection, ...]
    dishes: tuple[str, ...]
    updated_at: datetime | None


@dataclass(frozen=True)
class FoodPreferenceSelfProjection:
    profile: FoodPreferenceProjection
    onboarding_required: bool
    onboarding_completed_at: datetime | None


@dataclass(frozen=True)
class _NormalizedCuisine:
    cuisine_option_id: UUID
    custom_name: str | None
    custom_normalized_name: str | None


@dataclass(frozen=True)
class _ResolvedCuisine:
    cuisine_option_id: UUID
    name: str
    custom_name: str | None
    custom_normalized_name: str | None


@dataclass(frozen=True)
class _NormalizedWrite:
    cuisines: tuple[_NormalizedCuisine, ...]
    dishes: tuple[tuple[str, str], ...]


class FoodPreferenceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _validation_error(message: str) -> FoodPreferenceValidationFailed:
        return FoodPreferenceValidationFailed(message)

    @staticmethod
    def _translate_integrity_error(
        exc: IntegrityError,
    ) -> FoodPreferenceVersionChanged | None:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in {
            "person_food_preferences_pkey",
            "uq_person_food_preference_versions_person_version",
        }:
            return FoodPreferenceVersionChanged("food preference version changed")
        return None

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise FoodPreferenceValidationFailed(
                "onboarding completion time must include an offset"
            )

    def _normalize_command(
        self,
        command: ReplaceFoodPreferenceCommand,
    ) -> _NormalizedWrite:
        if (
            not isinstance(command.expected_version, int)
            or isinstance(command.expected_version, bool)
            or command.expected_version < 0
        ):
            raise self._validation_error("expected version must be non-negative")
        if command.source not in FOOD_PREFERENCE_SOURCES:
            raise self._validation_error("food preference source is invalid")
        if not isinstance(command.cuisines, tuple) or len(command.cuisines) > 3:
            raise self._validation_error("at most three cuisines may be selected")
        if not isinstance(command.dishes, tuple) or len(command.dishes) > 3:
            raise self._validation_error("at most three dishes may be selected")

        cuisines: list[_NormalizedCuisine] = []
        custom_keys: set[str] = set()
        try:
            for selection in command.cuisines:
                if not isinstance(selection, CuisineSelectionInput):
                    raise self._validation_error("cuisine selection is invalid")

                custom_name = selection.custom_name
                if custom_name is not None:
                    if not isinstance(custom_name, str):
                        raise self._validation_error("custom cuisine name is invalid")
                    custom_name = normalize_food_display(custom_name)
                    custom_key = normalize_food_key(custom_name)
                    if custom_key in custom_keys:
                        raise self._validation_error(
                            "custom cuisine names must be unique"
                        )
                    custom_keys.add(custom_key)
                else:
                    custom_key = None
                cuisines.append(
                    _NormalizedCuisine(
                        cuisine_option_id=selection.cuisine_option_id,
                        custom_name=custom_name,
                        custom_normalized_name=custom_key,
                    )
                )

            dishes: list[tuple[str, str]] = []
            dish_keys: set[str] = set()
            for dish in command.dishes:
                if not isinstance(dish, str):
                    raise self._validation_error("dish name is invalid")
                display_name = normalize_food_display(dish)
                normalized_name = normalize_food_key(display_name)
                if normalized_name in dish_keys:
                    raise self._validation_error("dish names must be unique")
                dish_keys.add(normalized_name)
                dishes.append((display_name, normalized_name))
        except FoodTextValidationError as exc:
            raise self._validation_error("food preference text is invalid") from exc

        return _NormalizedWrite(cuisines=tuple(cuisines), dishes=tuple(dishes))

    def _current_rows(
        self,
        person_id: UUID,
    ) -> tuple[
        tuple[PersonFoodPreferenceCuisine, ...],
        tuple[PersonFoodPreferenceDish, ...],
    ]:
        cuisines = tuple(
            self.db.scalars(
                select(PersonFoodPreferenceCuisine)
                .where(PersonFoodPreferenceCuisine.person_id == person_id)
                .order_by(PersonFoodPreferenceCuisine.position)
                .execution_options(populate_existing=True)
            )
        )
        dishes = tuple(
            self.db.scalars(
                select(PersonFoodPreferenceDish)
                .where(PersonFoodPreferenceDish.person_id == person_id)
                .order_by(PersonFoodPreferenceDish.position)
                .execution_options(populate_existing=True)
            )
        )
        return cuisines, dishes

    @staticmethod
    def _projection(
        root: PersonFoodPreference,
        cuisine_rows: tuple[PersonFoodPreferenceCuisine, ...],
        dish_rows: tuple[PersonFoodPreferenceDish, ...],
    ) -> FoodPreferenceProjection:
        return FoodPreferenceProjection(
            person_id=root.person_id,
            version=root.version,
            cuisines=tuple(
                FoodPreferenceCuisineProjection(
                    cuisine_option_id=row.cuisine_option_id,
                    name=row.cuisine_name_snapshot,
                    custom_name=row.custom_name,
                )
                for row in cuisine_rows
            ),
            dishes=tuple(row.display_name for row in dish_rows),
            updated_at=root.updated_at,
        )

    def get(
        self,
        person_id: UUID,
        *,
        reauthorize: Callable[[], None] | None = None,
    ) -> FoodPreferenceProjection:
        self.db.scalar(
            select(Person.id)
            .where(Person.id == person_id)
            .with_for_update(read=True)
        )
        if reauthorize is not None:
            reauthorize()
        root = self.db.scalar(
            select(PersonFoodPreference)
            .where(PersonFoodPreference.person_id == person_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if root is None:
            return FoodPreferenceProjection(
                person_id=person_id,
                version=0,
                cuisines=(),
                dishes=(),
                updated_at=None,
            )
        cuisine_rows, dish_rows = self._current_rows(person_id)
        return self._projection(root, cuisine_rows, dish_rows)

    def _resolve_cuisines(
        self,
        normalized: _NormalizedWrite,
        *,
        existing_rows: dict[UUID, PersonFoodPreferenceCuisine],
    ) -> tuple[_ResolvedCuisine, ...]:
        option_ids = [item.cuisine_option_id for item in normalized.cuisines]
        if not option_ids:
            return ()
        options = tuple(
            self.db.scalars(
                select(FoodCuisineOption)
                .where(FoodCuisineOption.id.in_(option_ids))
                .order_by(FoodCuisineOption.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        by_id = {option.id: option for option in options}
        if set(by_id) != set(option_ids):
            raise self._validation_error("cuisine option does not exist")

        resolved: list[_ResolvedCuisine] = []
        standard_option_ids: set[UUID] = set()
        display_keys: set[str] = set()
        for selection in normalized.cuisines:
            option = by_id[selection.cuisine_option_id]
            existing = existing_rows.get(option.id)
            if not option.is_active and existing is None:
                raise self._validation_error("inactive cuisine option cannot be added")
            option_name = (
                existing.cuisine_name_snapshot
                if not option.is_active and existing is not None
                else option.name
            )
            if option.is_other:
                if selection.custom_name is None:
                    raise self._validation_error(
                        "other cuisine requires a custom name"
                    )
                display_name = selection.custom_name
            else:
                if option.id in standard_option_ids:
                    raise self._validation_error(
                        "standard cuisine options must be unique"
                    )
                standard_option_ids.add(option.id)
                if selection.custom_name is not None:
                    raise self._validation_error(
                        "custom name is only allowed for other cuisine"
                    )
                display_name = option_name
            display_key = normalize_food_key(display_name)
            if display_key in display_keys:
                raise self._validation_error("cuisine names must be unique")
            display_keys.add(display_key)
            resolved.append(
                _ResolvedCuisine(
                    cuisine_option_id=option.id,
                    name=option_name,
                    custom_name=selection.custom_name,
                    custom_normalized_name=selection.custom_normalized_name,
                )
            )
        return tuple(resolved)

    @staticmethod
    def _same_content(
        normalized: _NormalizedWrite,
        cuisine_rows: tuple[PersonFoodPreferenceCuisine, ...],
        dish_rows: tuple[PersonFoodPreferenceDish, ...],
    ) -> bool:
        requested_cuisines = tuple(
            (item.cuisine_option_id, item.custom_name) for item in normalized.cuisines
        )
        current_cuisines = tuple(
            (row.cuisine_option_id, row.custom_name) for row in cuisine_rows
        )
        requested_dishes = tuple(display for display, _ in normalized.dishes)
        current_dishes = tuple(row.display_name for row in dish_rows)
        return (
            requested_cuisines == current_cuisines
            and requested_dishes == current_dishes
        )

    def replace(
        self,
        person_id: UUID,
        command: ReplaceFoodPreferenceCommand,
        *,
        reauthorize: Callable[[], None] | None = None,
        resolve_source: Callable[[], str] | None = None,
    ) -> FoodPreferenceProjection:
        try:
            with write_transaction(self.db):
                return self.replace_locked(
                    person_id,
                    command,
                    reauthorize=reauthorize,
                    resolve_source=resolve_source,
                )
        except IntegrityError as exc:
            translated = self._translate_integrity_error(exc)
            if translated is None:
                raise
            raise translated from exc

    def replace_locked(
        self,
        person_id: UUID,
        command: ReplaceFoodPreferenceCommand,
        *,
        reauthorize: Callable[[], None] | None = None,
        resolve_source: Callable[[], str] | None = None,
    ) -> FoodPreferenceProjection:
        """Replace one current profile without committing the caller's transaction."""

        person = self.db.scalar(
            select(Person)
            .where(Person.id == person_id, Person.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if person is None:
            raise FoodPreferenceNotFound("person not found")

        if reauthorize is not None:
            reauthorize()
        if resolve_source is not None:
            command = dataclass_replace(command, source=resolve_source())

        root = self.db.scalar(
            select(PersonFoodPreference)
            .where(PersonFoodPreference.person_id == person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        current_version = root.version if root is not None else 0
        if command.expected_version != current_version:
            raise FoodPreferenceVersionChanged("food preference version changed")

        normalized = self._normalize_command(command)
        if root is None:
            cuisine_rows: tuple[PersonFoodPreferenceCuisine, ...] = ()
            dish_rows: tuple[PersonFoodPreferenceDish, ...] = ()
        else:
            cuisine_rows, dish_rows = self._current_rows(person_id)

        if self._same_content(normalized, cuisine_rows, dish_rows):
            if root is None:
                return FoodPreferenceProjection(
                    person_id=person_id,
                    version=0,
                    cuisines=(),
                    dishes=(),
                    updated_at=None,
                )
            return self._projection(root, cuisine_rows, dish_rows)

        resolved_cuisines = self._resolve_cuisines(
            normalized,
            existing_rows={row.cuisine_option_id: row for row in cuisine_rows},
        )
        new_version = current_version + 1
        if root is None:
            root = PersonFoodPreference(
                person_id=person_id,
                version=new_version,
                source=command.source,
                updated_by_user_id=command.actor_user_id,
            )
            self.db.add(root)
            self.db.flush()
        else:
            root.version = new_version
            root.source = command.source
            root.updated_by_user_id = command.actor_user_id
            self.db.execute(
                delete(PersonFoodPreferenceCuisine).where(
                    PersonFoodPreferenceCuisine.person_id == person_id
                )
            )
            self.db.execute(
                delete(PersonFoodPreferenceDish).where(
                    PersonFoodPreferenceDish.person_id == person_id
                )
            )

        new_cuisine_rows = tuple(
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=position,
                cuisine_option_id=item.cuisine_option_id,
                cuisine_name_snapshot=item.name,
                custom_name=item.custom_name,
                custom_normalized_name=item.custom_normalized_name,
            )
            for position, item in enumerate(resolved_cuisines, start=1)
        )
        new_dish_rows = tuple(
            PersonFoodPreferenceDish(
                person_id=person_id,
                position=position,
                display_name=display_name,
                normalized_name=normalized_name,
            )
            for position, (display_name, normalized_name) in enumerate(
                normalized.dishes,
                start=1,
            )
        )
        self.db.add_all([*new_cuisine_rows, *new_dish_rows])
        self.db.add(
            PersonFoodPreferenceVersion(
                person_id=person_id,
                version=new_version,
                cuisines=[
                    {
                        "cuisine_option_id": str(item.cuisine_option_id),
                        "name": item.name,
                        "custom_name": item.custom_name,
                    }
                    for item in resolved_cuisines
                ],
                dishes=[display_name for display_name, _ in normalized.dishes],
                source=command.source,
                updated_by_user_id=command.actor_user_id,
            )
        )
        self.db.flush()
        AuditService(self.db).append(
            actor_user_id=command.actor_user_id,
            action="food_preferences.profile.update",
            resource_type="person_food_preference",
            resource_id=person_id,
            purpose=None,
            outcome="allowed",
            request_id=command.request_id,
            metadata={
                "person_id": str(person_id),
                "old_version": current_version,
                "new_version": new_version,
                "cuisine_count": len(resolved_cuisines),
                "dish_count": len(normalized.dishes),
                "source": command.source,
            },
        )
        self.db.refresh(root, attribute_names=["updated_at"])
        return self._projection(root, new_cuisine_rows, new_dish_rows)

    def _onboarding_eligible(self, person_id: UUID) -> bool:
        return bool(
            self.db.scalar(
                select(
                    or_(
                        exists().where(StudentProfile.person_id == person_id),
                        exists().where(StaffProfile.person_id == person_id),
                        exists().where(VisitorProfile.person_id == person_id),
                    )
                )
            )
        )

    def get_self(
        self,
        *,
        account_id: UUID,
        reauthorize: Callable[[], None] | None = None,
    ) -> FoodPreferenceSelfProjection:
        account = self.db.scalar(
            select(UserAccount)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == account_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise FoodPreferenceNotFound("active account not found")
        profile = self.get(account.person_id, reauthorize=reauthorize)
        self.db.refresh(
            account,
            attribute_names=["food_preferences_onboarding_completed_at"],
        )
        completed_at = account.food_preferences_onboarding_completed_at
        return FoodPreferenceSelfProjection(
            profile=profile,
            onboarding_required=(
                completed_at is None and self._onboarding_eligible(account.person_id)
            ),
            onboarding_completed_at=completed_at,
        )

    def replace_self(
        self,
        *,
        account_id: UUID,
        input: FoodPreferenceWriteRequest,
        request_id: str,
        occurred_at: datetime,
        reauthorize: Callable[[], None] | None = None,
    ) -> FoodPreferenceSelfProjection:
        self._require_aware(occurred_at)
        command = ReplaceFoodPreferenceCommand(
            expected_version=input.expected_version,
            cuisines=input.cuisines,
            dishes=input.dishes,
            actor_user_id=account_id,
            source="profile_self",
            request_id=request_id,
        )
        try:
            with write_transaction(self.db):
                account = self.db.scalar(
                    select(UserAccount)
                    .where(
                        UserAccount.id == account_id,
                        UserAccount.status == "active",
                    )
                    .with_for_update(key_share=True)
                    .execution_options(populate_existing=True)
                )
                if account is None:
                    raise FoodPreferenceNotFound("active account not found")
                onboarding_state: tuple[bool, bool] | None = None

                def reauthorize_and_validate_onboarding() -> None:
                    nonlocal onboarding_state
                    if reauthorize is not None:
                        reauthorize()
                    self.db.refresh(
                        account,
                        attribute_names=[
                            "food_preferences_onboarding_completed_at"
                        ],
                    )
                    eligible = self._onboarding_eligible(account.person_id)
                    completing = (
                        eligible
                        and account.food_preferences_onboarding_completed_at is None
                    )
                    onboarding_state = eligible, completing
                    if completing and (not input.cuisines or not input.dishes):
                        raise FoodPreferenceOnboardingIncomplete(
                            "onboarding requires at least one cuisine and one dish"
                        )

                profile = self.replace_locked(
                    account.person_id,
                    command,
                    reauthorize=reauthorize_and_validate_onboarding,
                )
                assert onboarding_state is not None
                eligible, completing = onboarding_state
                if completing:
                    account.food_preferences_onboarding_completed_at = occurred_at
                    AuditService(self.db).append(
                        actor_user_id=account.id,
                        action="food_preferences.onboarding.complete",
                        resource_type="person_food_preference",
                        resource_id=account.person_id,
                        purpose=None,
                        outcome="allowed",
                        request_id=request_id,
                        metadata={
                            "person_id": str(account.person_id),
                            "source": "profile_self",
                        },
                    )
                return FoodPreferenceSelfProjection(
                    profile=profile,
                    onboarding_required=(
                        eligible
                        and account.food_preferences_onboarding_completed_at is None
                    ),
                    onboarding_completed_at=(
                        account.food_preferences_onboarding_completed_at
                    ),
                )
        except IntegrityError as exc:
            translated = self._translate_integrity_error(exc)
            if translated is None:
                raise
            raise translated from exc
