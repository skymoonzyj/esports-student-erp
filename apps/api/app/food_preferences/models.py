from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin

FOOD_PREFERENCE_SOURCES = (
    "profile_self",
    "profile_guardian",
    "profile_admin",
    "camp_survey",
)

_SOURCE_CHECK = (
    "source IN ('profile_self', 'profile_guardian', 'profile_admin', 'camp_survey')"
)


class FoodCuisineOption(TimestampMixin, Base):
    __tablename__ = "food_cuisine_options"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 40 AND name = btrim(name)",
            name="ck_food_cuisine_options_name",
        ),
        CheckConstraint(
            "char_length(normalized_name) BETWEEN 1 AND 160 "
            "AND normalized_name = btrim(normalized_name)",
            name="ck_food_cuisine_options_normalized_name",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_food_cuisine_options_sort_order",
        ),
        UniqueConstraint(
            "normalized_name",
            name="uq_food_cuisine_options_normalized_name",
        ),
        Index(
            "uq_food_cuisine_options_single_other",
            "is_other",
            unique=True,
            postgresql_where=text("is_other"),
        ),
        Index(
            "ix_food_cuisine_options_catalog",
            "is_active",
            "sort_order",
            "normalized_name",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_other: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class PersonFoodPreference(Base):
    __tablename__ = "person_food_preferences"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_person_food_preferences_version"),
        CheckConstraint(_SOURCE_CHECK, name="ck_person_food_preferences_source"),
        Index("ix_person_food_preferences_updated_at", "updated_at", "person_id"),
    )

    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PersonFoodPreferenceCuisine(Base):
    __tablename__ = "person_food_preference_cuisines"
    __table_args__ = (
        CheckConstraint(
            "position BETWEEN 1 AND 3",
            name="ck_person_food_preference_cuisines_position",
        ),
        CheckConstraint(
            "char_length(cuisine_name_snapshot) BETWEEN 1 AND 40 "
            "AND cuisine_name_snapshot = btrim(cuisine_name_snapshot)",
            name="ck_person_food_preference_cuisines_snapshot",
        ),
        CheckConstraint(
            "(custom_name IS NULL) = (custom_normalized_name IS NULL)",
            name="ck_person_food_preference_cuisines_custom_pair",
        ),
        CheckConstraint(
            "custom_name IS NULL OR "
            "(char_length(custom_name) BETWEEN 1 AND 40 "
            "AND custom_name = btrim(custom_name))",
            name="ck_person_food_preference_cuisines_custom_name",
        ),
        CheckConstraint(
            "custom_normalized_name IS NULL OR "
            "(char_length(custom_normalized_name) BETWEEN 1 AND 160 "
            "AND custom_normalized_name = btrim(custom_normalized_name))",
            name="ck_person_food_preference_cuisines_custom_normalized_name",
        ),
        Index(
            "uq_person_food_preference_cuisines_standard_option",
            "person_id",
            "cuisine_option_id",
            unique=True,
            postgresql_where=text("custom_name IS NULL"),
        ),
        UniqueConstraint(
            "person_id",
            "custom_normalized_name",
            name="uq_person_food_preference_cuisines_custom_name",
        ),
        Index(
            "ix_person_food_preference_cuisines_option",
            "cuisine_option_id",
            "person_id",
        ),
    )

    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("person_food_preferences.person_id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    cuisine_option_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("food_cuisine_options.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cuisine_name_snapshot: Mapped[str] = mapped_column(String(40), nullable=False)
    custom_name: Mapped[str | None] = mapped_column(String(40))
    custom_normalized_name: Mapped[str | None] = mapped_column(String(160))


class PersonFoodPreferenceDish(Base):
    __tablename__ = "person_food_preference_dishes"
    __table_args__ = (
        CheckConstraint(
            "position BETWEEN 1 AND 3",
            name="ck_person_food_preference_dishes_position",
        ),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 40 "
            "AND display_name = btrim(display_name)",
            name="ck_person_food_preference_dishes_display_name",
        ),
        CheckConstraint(
            "char_length(normalized_name) BETWEEN 1 AND 160 "
            "AND normalized_name = btrim(normalized_name)",
            name="ck_person_food_preference_dishes_normalized_name",
        ),
        UniqueConstraint(
            "person_id",
            "normalized_name",
            name="uq_person_food_preference_dishes_normalized_name",
        ),
        Index(
            "ix_person_food_preference_dishes_suggestions",
            "normalized_name",
            "person_id",
        ),
    )

    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("person_food_preferences.person_id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(40), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)


class PersonFoodPreferenceVersion(Base):
    __tablename__ = "person_food_preference_versions"
    __table_args__ = (
        CheckConstraint(
            "version >= 1",
            name="ck_person_food_preference_versions_version",
        ),
        CheckConstraint(
            "jsonb_typeof(cuisines) = 'array' "
            "AND jsonb_array_length(cuisines) <= 3",
            name="ck_person_food_preference_versions_cuisines",
        ),
        CheckConstraint(
            "jsonb_typeof(dishes) = 'array' AND jsonb_array_length(dishes) <= 3",
            name="ck_person_food_preference_versions_dishes",
        ),
        CheckConstraint(
            _SOURCE_CHECK,
            name="ck_person_food_preference_versions_source",
        ),
        UniqueConstraint(
            "person_id",
            "version",
            name="uq_person_food_preference_versions_person_version",
        ),
        Index(
            "ix_person_food_preference_versions_history",
            "person_id",
            "version",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    cuisines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    dishes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
