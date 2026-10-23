from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class StudentGameAccount(TimestampMixin, Base):
    __tablename__ = "student_game_accounts"
    __table_args__ = (
        CheckConstraint(
            "btrim(identifier_ciphertext) <> ''",
            name="ck_student_game_accounts_ciphertext",
        ),
        CheckConstraint(
            "identifier_digest ~ '^[0-9a-f]{64}$'",
            name="ck_student_game_accounts_digest",
        ),
        CheckConstraint(
            "unbound_at IS NULL OR unbound_at > valid_from",
            name="ck_student_game_accounts_unbound_at",
        ),
        CheckConstraint(
            "(unbound_at IS NULL AND unbound_by_user_id IS NULL "
            "AND unbind_reason IS NULL) OR "
            "(unbound_at IS NOT NULL AND unbound_by_user_id IS NOT NULL "
            "AND unbind_reason IS NOT NULL AND btrim(unbind_reason) <> '')",
            name="ck_student_game_accounts_unbound_fields",
        ),
        Index(
            "uq_student_game_accounts_active_project_digest",
            "project_id",
            "identifier_digest",
            unique=True,
            postgresql_where=text("unbound_at IS NULL"),
        ),
        ExcludeConstraint(
            ("project_id", "="),
            ("identifier_digest", "="),
            (
                text(
                    "tstzrange(valid_from, "
                    "COALESCE(unbound_at, 'infinity'::timestamptz), '[)')"
                ),
                "&&",
            ),
            name="excl_student_game_accounts_project_digest_overlap",
            using="gist",
        ),
        Index(
            "ix_student_game_accounts_student_current",
            "student_person_id",
            "project_id",
            postgresql_where=text("unbound_at IS NULL"),
        ),
        Index(
            "ix_student_game_accounts_history_match",
            "project_id",
            "identifier_digest",
            "valid_from",
            "unbound_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    student_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_profiles.person_id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    identifier_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    identifier_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unbound_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    unbind_reason: Mapped[str | None] = mapped_column(String(200))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )


class StudentAuxiliaryPhone(TimestampMixin, Base):
    __tablename__ = "student_auxiliary_phones"
    __table_args__ = (
        CheckConstraint(
            "btrim(phone_ciphertext) <> ''",
            name="ck_student_auxiliary_phones_ciphertext",
        ),
        CheckConstraint(
            "phone_digest ~ '^[0-9a-f]{64}$'",
            name="ck_student_auxiliary_phones_digest",
        ),
        CheckConstraint(
            "disabled_at IS NULL OR disabled_at > valid_from",
            name="ck_student_auxiliary_phones_disabled_at",
        ),
        CheckConstraint(
            "(disabled_at IS NULL AND disabled_by_user_id IS NULL "
            "AND disable_reason IS NULL) OR "
            "(disabled_at IS NOT NULL AND disabled_by_user_id IS NOT NULL "
            "AND disable_reason IS NOT NULL AND btrim(disable_reason) <> '')",
            name="ck_student_auxiliary_phones_disabled_fields",
        ),
        Index(
            "uq_student_auxiliary_phones_active_student_digest",
            "student_person_id",
            "phone_digest",
            unique=True,
            postgresql_where=text("disabled_at IS NULL"),
        ),
        Index(
            "ix_student_auxiliary_phones_active_lookup",
            "phone_digest",
            "student_person_id",
            postgresql_where=text("disabled_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    student_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_profiles.person_id", ondelete="RESTRICT"),
        nullable=False,
    )
    phone_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    phone_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    disable_reason: Mapped[str | None] = mapped_column(String(200))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )


_student_game_account_history_index = Index(
    "ix_student_game_accounts_student_history",
    StudentGameAccount.student_person_id,
    StudentGameAccount.valid_from.desc(),
    StudentGameAccount.id.desc(),
)
_student_auxiliary_phone_history_index = Index(
    "ix_student_auxiliary_phones_student_history",
    StudentAuxiliaryPhone.student_person_id,
    StudentAuxiliaryPhone.valid_from.desc(),
    StudentAuxiliaryPhone.id.desc(),
)
