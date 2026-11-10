"""Append-only activation role approvals, separate from identity evidence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class ActivationRolePlan(Base):
    __tablename__ = "intake_activation_role_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["origin_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            ondelete="RESTRICT",
            name="fk_activation_role_plan_approval_person",
        ),
        UniqueConstraint("person_id", "version", name="uq_activation_role_plan_version"),
        UniqueConstraint(
            "authorizer_user_id", "operation_key", name="uq_activation_role_plan_operation"
        ),
        CheckConstraint("version >= 1", name="ck_activation_role_plan_version"),
        CheckConstraint(
            "operation_digest ~ '^[0-9a-f]{64}$' AND plan_digest ~ '^[0-9a-f]{64}$'",
            name="ck_activation_role_plan_digests",
        ),
        CheckConstraint(
            "NOT (role_keys && ARRAY['admin', 'super_admin']::varchar[])",
            name="ck_activation_role_plan_non_admin",
        ),
        Index("ix_activation_role_plan_authorizer", "authorizer_user_id", "person_id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"))
    origin_approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    authorizer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    role_keys: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    version: Mapped[int]
    operation_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    operation_digest: Mapped[str] = mapped_column(String(64))
    plan_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
