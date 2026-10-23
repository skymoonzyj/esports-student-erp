from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base

_ROLE_SCOPE_CHECK = (
    "scope_kind IN ("
    "'global', 'self', 'family', 'person', 'assigned_class', 'assigned_team', "
    "'building', 'floor'"
    ")"
)
_OVERRIDE_SCOPE_CHECK = (
    "scope_kind IN ("
    "'global', 'self', 'family', 'person', 'assigned_class', 'assigned_team', "
    "'cohort', 'project', 'enrollment', 'building', 'floor'"
    ")"
)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        CheckConstraint(_ROLE_SCOPE_CHECK, name="ck_role_permissions_scope_kind"),
    )

    role_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    scope_kind: Mapped[str] = mapped_column(String(32), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class UserPermissionOverride(Base):
    __tablename__ = "user_permission_overrides"
    __table_args__ = (
        CheckConstraint("effect IN ('allow', 'deny')", name="ck_permission_overrides_effect"),
        CheckConstraint(
            _OVERRIDE_SCOPE_CHECK,
            name="ck_permission_overrides_scope_kind",
        ),
        CheckConstraint(
            "(scope_kind IN ('global', 'self', 'family') AND scope_id IS NULL) OR "
            "(scope_kind IN ("
            "'person', 'assigned_class', 'assigned_team', 'cohort', 'project', 'enrollment', "
            "'building', 'floor'"
            ") AND scope_id IS NOT NULL)",
            name="ck_permission_overrides_scope_id",
        ),
        CheckConstraint(
            "valid_until IS NULL OR "
            "(valid_from IS NOT NULL AND valid_until > valid_from)",
            name="ck_permission_overrides_validity",
        ),
        UniqueConstraint(
            "user_id",
            "permission_id",
            "effect",
            "scope_kind",
            "scope_id",
            name="uq_permission_overrides_rule",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    grant_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
