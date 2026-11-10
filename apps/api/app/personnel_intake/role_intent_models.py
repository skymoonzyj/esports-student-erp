"""Immutable role requests are evidence, never an account authorization."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class IntakeRoleIntent(Base):
    __tablename__ = "intake_role_intents"
    __table_args__ = (
        ForeignKeyConstraint(["origin_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"], ondelete="RESTRICT",
            name="fk_role_intent_approval_person"),
        UniqueConstraint("person_id", name="uq_role_intent_person"),
        CheckConstraint("version = 1", name="ck_role_intent_version"),
        CheckConstraint("cardinality(role_keys) > 0 AND NOT (role_keys && ARRAY['admin','super_admin','finance']::varchar[])", name="ck_role_intent_roles"),
        CheckConstraint("intent_digest ~ '^[0-9a-f]{64}$'", name="ck_role_intent_digest"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"))
    origin_approval_id: Mapped[UUID]
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_accounts.id", ondelete="RESTRICT"))
    role_keys: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    position_evidence: Mapped[dict[str, str]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(default=1)
    intent_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntakeRoleDecision(Base):
    __tablename__ = "intake_role_decisions"
    __table_args__ = (
        UniqueConstraint("intent_id", name="uq_role_decision_intent"),
        UniqueConstraint("approved_by_user_id", "operation_key", name="uq_role_decision_operation"),
        CheckConstraint("operation_digest ~ '^[0-9a-f]{64}$'", name="ck_role_decision_digest"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    intent_id: Mapped[UUID] = mapped_column(ForeignKey("intake_role_intents.id", ondelete="RESTRICT"))
    approved_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_accounts.id", ondelete="RESTRICT"))
    role_plan_id: Mapped[UUID] = mapped_column(ForeignKey("intake_activation_role_plans.id", ondelete="RESTRICT"))
    operation_key: Mapped[UUID]
    operation_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
