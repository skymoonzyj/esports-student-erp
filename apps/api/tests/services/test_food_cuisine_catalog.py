from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.food_preferences.catalog_service as catalog_service_module
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.models import UserAccount
from app.food_preferences.access import FoodPreferenceAccess
from app.food_preferences.catalog_service import CuisineCatalogService
from app.food_preferences.errors import (
    FoodCuisineConflict,
    FoodCuisineInvalid,
    FoodCuisineProtected,
    FoodPreferenceNotFound,
    FoodPreferencePermissionDenied,
)
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceCuisine,
    PersonFoodPreferenceDish,
)
from app.food_preferences.schemas import CuisineCreateRequest, CuisinePatchRequest
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES

NOW = datetime(2026, 8, 11, 8, tzinfo=UTC)


@dataclass
class CatalogScenario:
    db: Session
    people: dict[str, Person]
    accounts: dict[str, UserAccount]

    def service(self, key: str) -> CuisineCatalogService:
        return CuisineCatalogService(
            self.db,
            access=FoodPreferenceAccess(
                self.db,
                account_id=self.accounts[key].id,
                occurred_at=NOW,
            ),
        )

    def add_person(self, key: str) -> Person:
        person = Person(
            full_name=f"菜品联想{key}",
            identity_ciphertext=f"food-catalog-{uuid4()}",
            identity_digest=f"food-catalog-{uuid4()}",
            sex="female",
            date_of_birth=date(1990, 1, 1),
        )
        self.db.add(person)
        self.db.flush()
        self.people[key] = person
        return person


@pytest.fixture
def catalog_scenario(db: Session) -> CatalogScenario:
    marker = uuid4().hex[:12]
    people: dict[str, Person] = {}
    accounts: dict[str, UserAccount] = {}
    for index, (key, role_key) in enumerate(
        (("admin", "admin"), ("coach", "coach")), start=1
    ):
        person = Person(
            full_name=f"菜系字典{key}",
            identity_ciphertext=f"food-catalog-cipher-{marker}-{key}",
            identity_digest=f"food-catalog-id-{marker}-{key}",
            sex="female" if index % 2 else "male",
            date_of_birth=date(1990, 1, index),
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=f"food-catalog-phone-{marker}-{key}",
            email_normalized=f"food-catalog-{marker}-{key}@example.test",
            password_hash="test-password",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        db.add(UserRole(user_id=account.id, role_id=ROLES[role_key].id))
        people[key] = person
        accounts[key] = account
    db.commit()
    return CatalogScenario(db=db, people=people, accounts=accounts)


def test_catalog_lists_active_by_default_and_inactive_only_for_global_manager(
    catalog_scenario: CatalogScenario,
) -> None:
    scenario = catalog_scenario
    admin = scenario.service("admin")
    active = admin.create(
        CuisineCreateRequest(name="川菜", sort_order=10),
        request_id="catalog-create-active",
    )
    inactive = admin.create(
        CuisineCreateRequest(name="粤菜", sort_order=20),
        request_id="catalog-create-inactive",
    )
    admin.patch(
        inactive.id,
        CuisinePatchRequest(is_active=False),
        request_id="catalog-disable-inactive",
    )

    coach_projection = scenario.service("coach").list(include_inactive=False)
    assert active.id in {item.id for item in coach_projection.items}
    assert inactive.id not in {item.id for item in coach_projection.items}
    assert coach_projection.allowed_actions == ()
    assert len(coach_projection.access_revision) == 64
    assert [
        (item.sort_order, item.normalized_name, str(item.id))
        for item in coach_projection.items
    ] == sorted(
        (item.sort_order, item.normalized_name, str(item.id))
        for item in coach_projection.items
    )
    with pytest.raises(FoodPreferencePermissionDenied):
        scenario.service("coach").list(include_inactive=True)

    admin_projection = admin.list(include_inactive=True)
    assert {active.id, inactive.id} <= {item.id for item in admin_projection.items}
    assert admin_projection.allowed_actions == ("create", "update", "disable")
    assert any(item.is_other and item.name == "其他" for item in admin_projection.items)
    assert [
        (
            not item.is_active,
            item.sort_order,
            item.normalized_name,
            str(item.id),
        )
        for item in admin_projection.items
    ] == sorted(
        (
            not item.is_active,
            item.sort_order,
            item.normalized_name,
            str(item.id),
        )
        for item in admin_projection.items
    )


def test_catalog_create_normalizes_and_rejects_nfkc_duplicate(
    catalog_scenario: CatalogScenario,
) -> None:
    service = catalog_scenario.service("admin")
    created = service.create(
        CuisineCreateRequest(name="  ＳｉＣｈｕａｎ  ", sort_order=3),
        request_id="catalog-normalized-create",
    )

    assert (created.name, created.normalized_name, created.is_active) == (
        "SiChuan",
        "sichuan",
        True,
    )
    with pytest.raises(FoodCuisineConflict):
        service.create(
            CuisineCreateRequest(name=" sichuan ", sort_order=4),
            request_id="catalog-duplicate-create",
        )


def test_catalog_audit_failure_rolls_back_create(
    catalog_scenario: CatalogScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = catalog_scenario
    before_options = scenario.db.scalar(select(func.count(FoodCuisineOption.id)))
    before_audits = scenario.db.scalar(select(func.count(AuditEvent.id)))

    def reject_audit(*args, **kwargs) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated food catalog audit failure")

    monkeypatch.setattr(catalog_service_module.AuditService, "append", reject_audit)
    with pytest.raises(AuditMetadataRejected):
        scenario.service("admin").create(
            CuisineCreateRequest(name="审计失败菜系", sort_order=9),
            request_id="catalog-audit-failure",
        )

    assert scenario.db.scalar(select(func.count(FoodCuisineOption.id))) == before_options
    assert scenario.db.scalar(select(func.count(AuditEvent.id))) == before_audits
    assert scenario.db.scalar(select(1)) == 1


def test_catalog_patch_preserves_snapshots_and_supports_lifecycle(
    catalog_scenario: CatalogScenario,
) -> None:
    scenario = catalog_scenario
    service = scenario.service("admin")
    option = service.create(
        CuisineCreateRequest(name="江浙菜", sort_order=30),
        request_id="catalog-snapshot-create",
    )
    target = scenario.add_person("snapshot-target")
    scenario.db.add(
        PersonFoodPreference(
            person_id=target.id,
            version=1,
            source="profile_admin",
            updated_by_user_id=scenario.accounts["admin"].id,
        )
    )
    scenario.db.flush()
    current = PersonFoodPreferenceCuisine(
        person_id=target.id,
        position=1,
        cuisine_option_id=option.id,
        cuisine_name_snapshot=option.name,
    )
    scenario.db.add(current)
    scenario.db.flush()

    renamed = service.patch(
        option.id,
        CuisinePatchRequest(name="江南菜", sort_order=5),
        request_id="catalog-rename",
    )
    disabled_state = service.patch(
        option.id,
        CuisinePatchRequest(is_active=False),
        request_id="catalog-disable",
    ).is_active
    reactivated = service.patch(
        option.id,
        CuisinePatchRequest(is_active=True),
        request_id="catalog-reactivate",
    )

    assert (renamed.name, renamed.normalized_name, renamed.sort_order) == (
        "江南菜",
        "江南菜",
        5,
    )
    assert disabled_state is False
    assert reactivated.is_active is True
    scenario.db.refresh(current)
    assert current.cuisine_name_snapshot == "江浙菜"
    events = list(
        scenario.db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_id == option.id,
                AuditEvent.action.like("food_preferences.cuisine.%"),
            )
        )
    )
    assert {event.action for event in events} == {
        "food_preferences.cuisine.create",
        "food_preferences.cuisine.update",
    }
    assert all(event.event_metadata == {} for event in events)


def test_system_other_is_protected_but_can_be_reordered(
    catalog_scenario: CatalogScenario,
) -> None:
    scenario = catalog_scenario
    service = scenario.service("admin")
    other = scenario.db.scalar(
        select(FoodCuisineOption).where(FoodCuisineOption.is_other.is_(True))
    )
    assert other is not None

    with pytest.raises(FoodCuisineProtected):
        service.patch(
            other.id,
            CuisinePatchRequest(is_active=False),
            request_id="catalog-other-disable",
        )
    with pytest.raises(FoodCuisineProtected):
        service.patch(
            other.id,
            CuisinePatchRequest(name="别的名称"),
            request_id="catalog-other-rename",
        )
    reordered = service.patch(
        other.id,
        CuisinePatchRequest(sort_order=7),
        request_id="catalog-other-reorder",
    )
    assert (reordered.name, reordered.is_active, reordered.sort_order) == (
        "其他",
        True,
        7,
    )


def test_catalog_patch_missing_is_private_not_found(
    catalog_scenario: CatalogScenario,
) -> None:
    with pytest.raises(FoodPreferenceNotFound):
        catalog_scenario.service("admin").patch(
            uuid4(),
            CuisinePatchRequest(sort_order=1),
            request_id="catalog-missing",
        )


def _add_dish(
    scenario: CatalogScenario,
    *,
    key: str,
    position: int,
    display_name: str,
    normalized_name: str,
) -> None:
    person = scenario.add_person(key)
    scenario.db.add(
        PersonFoodPreference(
            person_id=person.id,
            version=1,
            source="profile_admin",
            updated_by_user_id=scenario.accounts["admin"].id,
        )
    )
    scenario.db.flush()
    scenario.db.add(
        PersonFoodPreferenceDish(
            person_id=person.id,
            position=position,
            display_name=display_name,
            normalized_name=normalized_name,
        )
    )
    scenario.db.flush()


def test_dish_suggestions_are_normalized_ranked_and_identity_free(
    catalog_scenario: CatalogScenario,
) -> None:
    scenario = catalog_scenario
    _add_dish(
        scenario,
        key="dish-a",
        position=1,
        display_name="SiChuan Noodles",
        normalized_name="sichuan noodles",
    )
    _add_dish(
        scenario,
        key="dish-b",
        position=1,
        display_name="SICHUAN NOODLES",
        normalized_name="sichuan noodles",
    )
    _add_dish(
        scenario,
        key="dish-c",
        position=1,
        display_name="Sichuan Fish",
        normalized_name="sichuan fish",
    )
    _add_dish(
        scenario,
        key="dish-deleted",
        position=1,
        display_name="Deleted Secret Dish",
        normalized_name="deleted secret dish",
    )
    scenario.people["dish-deleted"].deleted_at = NOW
    scenario.db.flush()
    service = scenario.service("coach")

    assert service.dish_suggestions("  ＳＩ  ", limit=20) == (
        "SICHUAN NOODLES",
        "Sichuan Fish",
    )
    assert service.dish_suggestions("  ", limit=20) == ()
    assert service.dish_suggestions("secret", limit=20) == ()
    assert all(isinstance(item, str) for item in service.dish_suggestions("si", limit=1))


@pytest.mark.parametrize("limit", [0, 21, True, 1.5, "1"])
def test_dish_suggestions_reject_invalid_limit(
    catalog_scenario: CatalogScenario,
    limit: object,
) -> None:
    with pytest.raises(FoodCuisineInvalid):
        catalog_scenario.service("coach").dish_suggestions(  # type: ignore[arg-type]
            "川", limit=limit
        )


def test_dish_suggestions_honor_global_update_deny(
    catalog_scenario: CatalogScenario,
) -> None:
    scenario = catalog_scenario
    scenario.db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["coach"].id,
            permission_id=PERMISSIONS["food_preferences.update"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    scenario.db.flush()

    with pytest.raises(FoodPreferencePermissionDenied):
        scenario.service("coach").dish_suggestions("川", limit=10)
