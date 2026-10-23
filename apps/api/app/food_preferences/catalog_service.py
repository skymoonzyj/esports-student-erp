from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.food_preferences.access import FoodPreferenceAccess
from app.food_preferences.errors import (
    FoodCuisineConflict,
    FoodCuisineInvalid,
    FoodCuisineProtected,
    FoodPreferenceNotFound,
    FoodPreferencePermissionDenied,
)
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreferenceDish,
)
from app.food_preferences.normalization import (
    FoodTextValidationError,
    normalize_food_display,
    normalize_food_key,
)
from app.food_preferences.schemas import (
    CatalogAllowedAction,
    CuisineCreateRequest,
    CuisinePatchRequest,
)
from app.identity.models import Person


@dataclass(frozen=True)
class CuisineCatalogProjection:
    items: tuple[FoodCuisineOption, ...]
    allowed_actions: tuple[CatalogAllowedAction, ...]
    access_revision: str


class CuisineCatalogService:
    def __init__(
        self,
        db: Session,
        *,
        access: FoodPreferenceAccess,
    ) -> None:
        self.db = db
        self.access = access

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> FoodCuisineConflict:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_food_cuisine_options_normalized_name":
            return FoodCuisineConflict("cuisine name already exists")
        return FoodCuisineConflict("cuisine catalog transaction conflicted")

    def _can_manage(self) -> bool:
        try:
            self.access.require_global("food_preferences.catalog.manage")
        except FoodPreferencePermissionDenied:
            return False
        return True

    def list(self, *, include_inactive: bool) -> CuisineCatalogProjection:
        self.access.require_any_capability("food_preferences.read")
        can_manage = self._can_manage()
        if include_inactive and not can_manage:
            raise FoodPreferencePermissionDenied(
                "inactive cuisine catalog requires global management"
            )
        query = select(FoodCuisineOption)
        if not include_inactive:
            query = query.where(FoodCuisineOption.is_active.is_(True))
        items = tuple(
            self.db.scalars(
                query.order_by(
                    FoodCuisineOption.is_active.desc(),
                    FoodCuisineOption.sort_order,
                    FoodCuisineOption.normalized_name,
                    FoodCuisineOption.id,
                )
            )
        )
        return CuisineCatalogProjection(
            items=items,
            allowed_actions=("create", "update", "disable") if can_manage else (),
            access_revision=self.access.access_revision,
        )

    def create(
        self,
        input: CuisineCreateRequest,
        *,
        request_id: str,
    ) -> FoodCuisineOption:
        self.access.require_global("food_preferences.catalog.manage")
        display_name = normalize_food_display(input.name)
        normalized_name = normalize_food_key(input.name)
        try:
            with write_transaction(self.db):
                option = FoodCuisineOption(
                    name=display_name,
                    normalized_name=normalized_name,
                    sort_order=input.sort_order,
                    is_active=True,
                    is_other=False,
                    created_by_user_id=self.access.account_id,
                    updated_by_user_id=self.access.account_id,
                )
                self.db.add(option)
                self.db.flush()
                AuditService(self.db).append(
                    actor_user_id=self.access.account_id,
                    action="food_preferences.cuisine.create",
                    resource_type="food_cuisine_option",
                    resource_id=option.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={},
                )
            return option
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def patch(
        self,
        option_id: UUID,
        input: CuisinePatchRequest,
        *,
        request_id: str,
    ) -> FoodCuisineOption:
        self.access.require_global("food_preferences.catalog.manage")
        try:
            with write_transaction(self.db):
                option = self.db.scalar(
                    select(FoodCuisineOption)
                    .where(FoodCuisineOption.id == option_id)
                    .with_for_update()
                )
                if option is None:
                    raise FoodPreferenceNotFound("cuisine option not found")
                if option.is_other:
                    if input.is_active is False:
                        raise FoodCuisineProtected("system other cuisine is protected")
                    if input.name is not None:
                        display_name = normalize_food_display(input.name)
                        if display_name != option.name:
                            raise FoodCuisineProtected(
                                "system other cuisine is protected"
                            )
                if input.name is not None:
                    option.name = normalize_food_display(input.name)
                    option.normalized_name = normalize_food_key(input.name)
                if input.sort_order is not None:
                    option.sort_order = input.sort_order
                if input.is_active is not None:
                    option.is_active = input.is_active
                option.updated_by_user_id = self.access.account_id
                self.db.flush()
                AuditService(self.db).append(
                    actor_user_id=self.access.account_id,
                    action="food_preferences.cuisine.update",
                    resource_type="food_cuisine_option",
                    resource_id=option.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={},
                )
            return option
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def dish_suggestions(self, query: str, *, limit: int) -> tuple[str, ...]:
        self.access.require_any_capability("food_preferences.update")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise FoodCuisineInvalid("suggestion limit must be between 1 and 20")
        if not isinstance(query, str) or not query.strip():
            return ()
        try:
            normalized_query = normalize_food_key(query)
        except FoodTextValidationError as exc:
            raise FoodCuisineInvalid("suggestion query is invalid") from exc
        # Do not delegate the display-name tie break to PostgreSQL's collation:
        # the product contract is the deterministic Unicode/code-point
        # ordering of the current display names, while database collations can
        # rank ``SiChuan`` before ``SICHUAN``.  Fetch the small suggestion set
        # and choose the representative in Python; the normalized key still
        # drives matching and the stable outer ordering.
        rows = self.db.execute(
            select(
                PersonFoodPreferenceDish.normalized_name,
                PersonFoodPreferenceDish.display_name,
                PersonFoodPreferenceDish.person_id,
            )
            .join(Person, Person.id == PersonFoodPreferenceDish.person_id)
            .where(
                Person.deleted_at.is_(None),
                PersonFoodPreferenceDish.normalized_name.contains(
                    normalized_query,
                    autoescape=True,
                )
            )
        )
        grouped: dict[str, tuple[set[UUID], set[str]]] = {}
        for normalized_name, display_name, person_id in rows:
            people, display_names = grouped.setdefault(
                normalized_name,
                (set(), set()),
            )
            people.add(person_id)
            display_names.add(display_name)
        ranked = sorted(
            (
                (-len(people), normalized_name, min(display_names))
                for normalized_name, (people, display_names) in grouped.items()
            ),
        )
        return tuple(display_name for _, _, display_name in ranked[:limit])
