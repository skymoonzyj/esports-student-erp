from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class Person(TimestampMixin, Base):
    __tablename__ = "people"
    __table_args__ = (
        CheckConstraint("sex IN ('male', 'female')", name="ck_people_sex"),
        CheckConstraint(
            "nickname IS NULL OR btrim(nickname) <> ''",
            name="ck_people_nickname_not_blank",
        ),
        CheckConstraint(
            "email IS NULL",
            name="ck_people_legacy_email_cleared",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    full_name: Mapped[str] = mapped_column(String(80), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(40))
    identity_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    phone_ciphertext: Mapped[str | None] = mapped_column(Text)
    phone_digest: Mapped[str | None] = mapped_column(String(64), index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    email_ciphertext: Mapped[str | None] = mapped_column(Text)
    email_digest: Mapped[str | None] = mapped_column(String(64), index=True)
    sex: Mapped[str] = mapped_column(String(16), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentProfile(Base):
    __tablename__ = "student_profiles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'inactive')",
            name="ck_student_profiles_status",
        ),
        CheckConstraint(
            "game_account_limit >= 3",
            name="ck_student_profiles_game_account_limit",
        ),
    )

    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    student_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    game_account_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default="3",
    )


class StaffProfile(Base):
    __tablename__ = "staff_profiles"
    __table_args__ = (
        CheckConstraint(
            "staff_type IN ('coach', 'life_teacher', 'customer_service', "
            "'administration', 'other')",
            name="ck_staff_profiles_staff_type",
        ),
    )

    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    staff_type: Mapped[str] = mapped_column(String(32), nullable=False)


class VisitorProfile(Base):
    __tablename__ = "visitor_profiles"

    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    linked_student_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    visit_reason: Mapped[str] = mapped_column(String(200), nullable=False)


class GuardianProfile(Base):
    __tablename__ = "guardian_profiles"

    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    legacy_backfilled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )


class GuardianRelationship(TimestampMixin, Base):
    __tablename__ = "guardian_relationships"
    __table_args__ = (
        Index(
            "ix_guardian_relationships_pair",
            "guardian_person_id",
            "student_person_id",
        ),
        Index(
            "ix_guardian_relationships_student_history",
            "student_person_id",
            "id",
        ),
        CheckConstraint(
            "guardian_person_id <> student_person_id",
            name="ck_guardian_relationships_distinct_people",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_guardian_relationships_valid_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    guardian_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    relationship: Mapped[str] = mapped_column(String(40), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    end_reason: Mapped[str | None] = mapped_column(String(200))
