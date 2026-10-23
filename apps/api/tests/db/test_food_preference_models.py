from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceCuisine,
    PersonFoodPreferenceDish,
    PersonFoodPreferenceVersion,
)

FOOD_OTHER_CUISINE_ID = UUID("60000000-0000-4000-8000-000000000001")


def _insert_person_and_account(db: Session) -> tuple[UUID, UUID]:
    person_id = uuid4()
    account_id = uuid4()
    db.execute(
        text(
            "INSERT INTO people ("
            "id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth"
            ") VALUES ("
            ":person_id, '饮食偏好测试人员', 'food-test-ciphertext', "
            ":identity_digest, 'female', DATE '1990-01-02')"
        ),
        {"person_id": person_id, "identity_digest": f"food-person-{person_id}"},
    )
    db.execute(
        text(
            "INSERT INTO user_accounts ("
            "id, person_id, login_phone_digest, email_normalized, password_hash, "
            "status, force_password_change, failed_login_count"
            ") VALUES ("
            ":account_id, :person_id, :phone_digest, :email, 'test-password', "
            "'active', false, 0)"
        ),
        {
            "account_id": account_id,
            "person_id": person_id,
            "phone_digest": f"food-phone-{account_id}",
            "email": f"food-{account_id}@example.test",
        },
    )
    return person_id, account_id


def _add_current_preference(
    db: Session,
    *,
    person_id: UUID,
    account_id: UUID,
    version: int = 1,
    source: str = "profile_self",
) -> PersonFoodPreference:
    preference = PersonFoodPreference(
        person_id=person_id,
        version=version,
        source=source,
        updated_by_user_id=account_id,
    )
    db.add(preference)
    db.flush()
    return preference


def _add_cuisine_option(
    db: Session,
    *,
    account_id: UUID,
    name: str | None = None,
    normalized_name: str | None = None,
) -> FoodCuisineOption:
    suffix = uuid4().hex[:8]
    option = FoodCuisineOption(
        name=name or f"测试菜系{suffix}",
        normalized_name=normalized_name or f"测试菜系{suffix}",
        sort_order=10,
        is_active=True,
        is_other=False,
        created_by_user_id=account_id,
        updated_by_user_id=account_id,
    )
    db.add(option)
    db.flush()
    return option


def _add_version(
    db: Session,
    *,
    person_id: UUID,
    account_id: UUID,
    version: int = 1,
    source: str = "profile_self",
    cuisines: list[dict[str, object]] | None = None,
    dishes: list[str] | None = None,
) -> PersonFoodPreferenceVersion:
    row = PersonFoodPreferenceVersion(
        person_id=person_id,
        version=version,
        cuisines=cuisines or [],
        dishes=dishes or [],
        source=source,
        updated_by_user_id=account_id,
    )
    db.add(row)
    db.flush()
    return row


def test_account_food_onboarding_completion_time_round_trips(db: Session) -> None:
    _, account_id = _insert_person_and_account(db)
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert "food_preferences_onboarding_completed_at" in UserAccount.__table__.columns
    assert account.food_preferences_onboarding_completed_at is None

    completed_at = datetime(2026, 8, 11, 12, tzinfo=UTC)
    account.food_preferences_onboarding_completed_at = completed_at
    db.flush()
    db.expire(account)

    assert account.food_preferences_onboarding_completed_at == completed_at


@pytest.mark.parametrize("position", [0, 4])
@pytest.mark.parametrize("record_kind", ["cuisine", "dish"])
def test_current_food_positions_are_limited_to_one_through_three(
    db: Session,
    position: int,
    record_kind: str,
) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _add_current_preference(db, person_id=person_id, account_id=account_id)
    row: PersonFoodPreferenceCuisine | PersonFoodPreferenceDish
    if record_kind == "cuisine":
        option = _add_cuisine_option(db, account_id=account_id)
        row = PersonFoodPreferenceCuisine(
            person_id=person_id,
            position=position,
            cuisine_option_id=option.id,
            cuisine_name_snapshot=option.name,
        )
    else:
        row = PersonFoodPreferenceDish(
            person_id=person_id,
            position=position,
            display_name="宫保鸡丁",
            normalized_name="宫保鸡丁",
        )

    with pytest.raises(IntegrityError):
        db.add(row)
        db.flush()


def test_current_cuisines_and_dishes_are_unique_per_person(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _add_current_preference(db, person_id=person_id, account_id=account_id)
    option = _add_cuisine_option(db, account_id=account_id)
    db.add_all(
        [
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=1,
                cuisine_option_id=option.id,
                cuisine_name_snapshot=option.name,
            ),
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=2,
                cuisine_option_id=option.id,
                cuisine_name_snapshot=option.name,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db.flush()


def test_multiple_custom_cuisines_may_share_system_other_option(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _add_current_preference(db, person_id=person_id, account_id=account_id)
    db.add_all(
        [
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=position,
                cuisine_option_id=FOOD_OTHER_CUISINE_ID,
                cuisine_name_snapshot="其他",
                custom_name=name,
                custom_normalized_name=name,
            )
            for position, name in enumerate(("川菜", "粤菜"), start=1)
        ]
    )

    db.flush()

    rows = tuple(
        db.scalars(
            select(PersonFoodPreferenceCuisine)
            .where(PersonFoodPreferenceCuisine.person_id == person_id)
            .order_by(PersonFoodPreferenceCuisine.position)
        )
    )
    assert [row.custom_name for row in rows] == ["川菜", "粤菜"]


def test_current_dish_normalized_name_is_unique_per_person(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _add_current_preference(db, person_id=person_id, account_id=account_id)
    db.add_all(
        [
            PersonFoodPreferenceDish(
                person_id=person_id,
                position=1,
                display_name="SiChuan",
                normalized_name="sichuan",
            ),
            PersonFoodPreferenceDish(
                person_id=person_id,
                position=2,
                display_name="ＳｉＣｈｕａｎ",
                normalized_name="sichuan",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db.flush()


def test_cuisine_normalized_name_is_globally_unique(db: Session) -> None:
    _, account_id = _insert_person_and_account(db)
    _add_cuisine_option(
        db,
        account_id=account_id,
        name="SiChuan",
        normalized_name="sichuan",
    )

    with pytest.raises(IntegrityError):
        _add_cuisine_option(
            db,
            account_id=account_id,
            name="ＳｉＣｈｕａｎ",
            normalized_name="sichuan",
        )


def test_system_other_cuisine_is_unique_active_and_protected(db: Session) -> None:
    other = db.get(FoodCuisineOption, FOOD_OTHER_CUISINE_ID)
    assert other is not None
    assert (other.name, other.normalized_name, other.is_active, other.is_other) == (
        "其他",
        "其他",
        True,
        True,
    )
    _, account_id = _insert_person_and_account(db)
    with pytest.raises(IntegrityError):
        db.add(
            FoodCuisineOption(
                name="另一个其他",
                normalized_name="另一个其他",
                sort_order=999,
                is_active=True,
                is_other=True,
                created_by_user_id=account_id,
                updated_by_user_id=account_id,
            )
        )
        db.flush()


@pytest.mark.parametrize("operation", ["disable", "delete"])
def test_system_other_cuisine_cannot_be_disabled_or_deleted(
    db: Session,
    operation: str,
) -> None:
    statement = (
        update(FoodCuisineOption)
        .where(FoodCuisineOption.id == FOOD_OTHER_CUISINE_ID)
        .values(is_active=False)
        if operation == "disable"
        else delete(FoodCuisineOption).where(
            FoodCuisineOption.id == FOOD_OTHER_CUISINE_ID
        )
    )

    with pytest.raises(DBAPIError, match="system other cuisine is protected"):
        db.execute(statement)
        db.flush()


@pytest.mark.parametrize(
    ("option_id", "custom_name", "custom_normalized_name"),
    [
        (FOOD_OTHER_CUISINE_ID, None, None),
        (FOOD_OTHER_CUISINE_ID, "川渝", None),
    ],
)
def test_other_cuisine_requires_complete_custom_name_pair(
    db: Session,
    option_id: UUID,
    custom_name: str | None,
    custom_normalized_name: str | None,
) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _add_current_preference(db, person_id=person_id, account_id=account_id)
    with pytest.raises(DBAPIError, match="custom cuisine pairing is invalid"):
        db.add(
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=1,
                cuisine_option_id=option_id,
                cuisine_name_snapshot="其他",
                custom_name=custom_name,
                custom_normalized_name=custom_normalized_name,
            )
        )
        db.flush()


def test_regular_cuisine_rejects_custom_name(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _add_current_preference(db, person_id=person_id, account_id=account_id)
    option = _add_cuisine_option(db, account_id=account_id)
    with pytest.raises(DBAPIError, match="custom cuisine pairing is invalid"):
        db.add(
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=1,
                cuisine_option_id=option.id,
                cuisine_name_snapshot=option.name,
                custom_name="川渝",
                custom_normalized_name="川渝",
            )
        )
        db.flush()


@pytest.mark.parametrize("record_kind", ["current", "history"])
def test_person_with_food_history_cannot_be_deleted(
    db: Session,
    record_kind: str,
) -> None:
    person_id, own_account_id = _insert_person_and_account(db)
    _, actor_account_id = _insert_person_and_account(db)
    db.execute(
        text("DELETE FROM user_accounts WHERE id = :id"),
        {"id": own_account_id},
    )
    if record_kind == "current":
        _add_current_preference(
            db,
            person_id=person_id,
            account_id=actor_account_id,
        )
    else:
        _add_version(
            db,
            person_id=person_id,
            account_id=actor_account_id,
        )

    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})
        db.flush()


@pytest.mark.parametrize(
    ("version", "source"),
    [(0, "profile_self"), (1, "invalid_source")],
)
def test_current_preference_rejects_invalid_version_or_source(
    db: Session,
    version: int,
    source: str,
) -> None:
    person_id, account_id = _insert_person_and_account(db)
    with pytest.raises(IntegrityError):
        _add_current_preference(
            db,
            person_id=person_id,
            account_id=account_id,
            version=version,
            source=source,
        )


@pytest.mark.parametrize(
    ("version", "source", "cuisines", "dishes"),
    [
        (0, "profile_self", [], []),
        (1, "invalid_source", [], []),
        (1, "profile_self", [{}, {}, {}, {}], []),
        (1, "profile_self", [], ["一", "二", "三", "四"]),
    ],
)
def test_preference_version_rejects_invalid_bounds(
    db: Session,
    version: int,
    source: str,
    cuisines: list[dict[str, object]],
    dishes: list[str],
) -> None:
    person_id, account_id = _insert_person_and_account(db)
    with pytest.raises(IntegrityError):
        _add_version(
            db,
            person_id=person_id,
            account_id=account_id,
            version=version,
            source=source,
            cuisines=cuisines,
            dishes=dishes,
        )


@pytest.mark.parametrize("field", ["cuisines", "dishes"])
def test_preference_version_requires_json_arrays(db: Session, field: str) -> None:
    person_id, account_id = _insert_person_and_account(db)
    values: dict[str, object] = {
        "person_id": person_id,
        "version": 1,
        "cuisines": [],
        "dishes": [],
        "source": "profile_self",
        "updated_by_user_id": account_id,
    }
    values[field] = {}
    with pytest.raises(IntegrityError):
        db.add(PersonFoodPreferenceVersion(**values))
        db.flush()


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_preference_versions_are_immutable(db: Session, operation: str) -> None:
    person_id, account_id = _insert_person_and_account(db)
    version = _add_version(db, person_id=person_id, account_id=account_id)
    statement = (
        update(PersonFoodPreferenceVersion)
        .where(PersonFoodPreferenceVersion.id == version.id)
        .values(source="profile_admin")
        if operation == "update"
        else delete(PersonFoodPreferenceVersion).where(
            PersonFoodPreferenceVersion.id == version.id
        )
    )

    with pytest.raises(DBAPIError, match="food preference versions are immutable"):
        db.execute(statement)
        db.flush()


def test_preference_versions_are_unique_per_person(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _add_version(db, person_id=person_id, account_id=account_id, version=1)
    with pytest.raises(IntegrityError):
        _add_version(db, person_id=person_id, account_id=account_id, version=1)


def test_model_rows_round_trip(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    current = _add_current_preference(
        db,
        person_id=person_id,
        account_id=account_id,
        source="profile_guardian",
    )
    option = _add_cuisine_option(db, account_id=account_id)
    db.add_all(
        [
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=1,
                cuisine_option_id=option.id,
                cuisine_name_snapshot=option.name,
            ),
            PersonFoodPreferenceDish(
                person_id=person_id,
                position=1,
                display_name="宫保 鸡丁",
                normalized_name="宫保 鸡丁",
            ),
        ]
    )
    history = _add_version(
        db,
        person_id=person_id,
        account_id=account_id,
        source="profile_guardian",
        cuisines=[
            {
                "cuisine_option_id": str(option.id),
                "name": option.name,
                "custom_name": None,
            }
        ],
        dishes=["宫保 鸡丁"],
    )
    db.flush()

    assert db.scalar(
        select(PersonFoodPreference).where(
            PersonFoodPreference.person_id == person_id
        )
    ) is current
    assert history.cuisines[0]["name"] == option.name
