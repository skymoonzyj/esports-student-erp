"""Durable v1 ownership and evidence; service layers enforce state transitions."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class ReactionxConnector(TimestampMixin, Base):
    __tablename__ = "reactionx_connectors"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','revoked')", name="ck_reactionx_connector_status"
        ),
        CheckConstraint(
            "credential_digest ~ '^[0-9a-f]{64}$'", name="ck_reactionx_connector_digest"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"), index=True
    )
    browser_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="RESTRICT")
    )
    credential_digest: Mapped[str] = mapped_column(String(64), unique=True)
    origin: Mapped[str] = mapped_column(String(255))
    client_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    control_tab_id: Mapped[UUID | None] = mapped_column()
    last_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    instance_id: Mapped[UUID | None] = mapped_column()
    connector_version: Mapped[str | None] = mapped_column(String(32))
    protocol_version: Mapped[int | None] = mapped_column(Integer)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clock_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    devices: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")


class ReactionxPairing(TimestampMixin, Base):
    __tablename__ = "reactionx_pairings"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    origin: Mapped[str] = mapped_column(String(255))
    actor_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"), index=True
    )
    browser_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="RESTRICT")
    )
    challenge_digest: Mapped[str] = mapped_column(String(64), unique=True)
    verification_phrase: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    connector_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reactionx_connectors.id", ondelete="RESTRICT"), unique=True
    )
    browser_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    browser_tab_id: Mapped[UUID | None] = mapped_column()


class ReactionxRun(TimestampMixin, Base):
    __tablename__ = "reactionx_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared','ready','starting','running','stopping','unconfirmed',"
            "'awaiting_sync','completed','cancelled','expired','interrupted','uncertain_closed')",
            name="ck_reactionx_run_status",
        ),
        CheckConstraint("last_sequence >= 0", name="ck_reactionx_run_sequence"),
        Index(
            "uq_reactionx_connector_active_run",
            "connector_id",
            unique=True,
            postgresql_where=text(
                "status IN ('prepared','ready','starting','running','stopping','unconfirmed')"
            ),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"), index=True
    )
    subject_person_id: Mapped[UUID] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), index=True
    )
    connector_id: Mapped[UUID] = mapped_column(
        ForeignKey("reactionx_connectors.id", ondelete="RESTRICT")
    )
    browser_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="RESTRICT")
    )
    browser_tab_id: Mapped[UUID] = mapped_column()
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    manifest_digest: Mapped[str] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="prepared")
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    upload_permit_digest: Mapped[str | None] = mapped_column(String(64))
    upload_permit_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_authorization: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    uncertainty_record: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ReactionxCommand(TimestampMixin, Base):
    __tablename__ = "reactionx_commands"
    __table_args__ = (
        UniqueConstraint("connector_id", "sequence", name="uq_reactionx_command_sequence"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reactionx_runs.id", ondelete="RESTRICT"), index=True
    )
    connector_id: Mapped[UUID] = mapped_column(
        ForeignKey("reactionx_connectors.id", ondelete="RESTRICT")
    )
    actor_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    subject_person_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_fingerprint: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delivery_envelope: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    browser_tab_id: Mapped[UUID | None] = mapped_column()


class ReactionxEvent(Base):
    __tablename__ = "reactionx_events"
    __table_args__ = (CheckConstraint("seq > 0", name="ck_reactionx_event_seq"),)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("reactionx_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ReactionxResultReceipt(TimestampMixin, Base):
    __tablename__ = "reactionx_result_receipts"
    __table_args__ = (
        CheckConstraint(
            "(NOT eligible) OR (rank_n1 IS NOT NULL AND rank_d1 IS NOT NULL "
            "AND rank_sign1 IS NOT NULL AND rank_n2 IS NOT NULL AND rank_d2 IS NOT NULL "
            "AND rank_sign2 IS NOT NULL AND comparison_key IS NOT NULL)",
            name="ck_reactionx_rank_complete",
        ),
        *(
            CheckConstraint(
                f"rank_n{i} BETWEEN 0 AND 9007199254740991 AND "
                f"rank_d{i} BETWEEN 1 AND 10000 AND rank_sign{i} IN (-1,1)",
                name=f"ck_reactionx_rank_domain_{i}",
            )
            for i in (1, 2)
        ),
        Index(
            "ix_reactionx_rank_group",
            "comparison_key",
            "rank_key1",
            "rank_key2",
            "saved_at",
            "result_id",
            postgresql_where=text("eligible"),
        ),
        Index("ix_reactionx_result_page", "saved_at", "result_id"),
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("reactionx_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    result_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    digest: Mapped[str] = mapped_column(String(64))
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    eligible: Mapped[bool] = mapped_column(Boolean)
    ranking_reason: Mapped[str | None] = mapped_column(String(40))
    rank_n1: Mapped[int | None] = mapped_column(BigInteger)
    rank_d1: Mapped[int | None] = mapped_column(Integer)
    rank_sign1: Mapped[int | None] = mapped_column(Integer)
    rank_n2: Mapped[int | None] = mapped_column(BigInteger)
    rank_d2: Mapped[int | None] = mapped_column(Integer)
    rank_sign2: Mapped[int | None] = mapped_column(Integer)
    rank_key1: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 0),
        Computed(
            "rank_sign1 * div(CAST(rank_n1 AS numeric) * 100000000, CAST(rank_d1 AS numeric))",
            persisted=True,
        ),
    )
    rank_key2: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 0),
        Computed(
            "rank_sign2 * div(CAST(rank_n2 AS numeric) * 100000000, CAST(rank_d2 AS numeric))",
            persisted=True,
        ),
    )
    comparison_key: Mapped[str | None] = mapped_column(String(128), index=True)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
