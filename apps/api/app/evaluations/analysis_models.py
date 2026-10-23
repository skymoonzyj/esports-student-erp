from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class ProjectAnalysisProfile(TimestampMixin, Base):
    __tablename__ = "project_analysis_profiles"
    __table_args__ = (
        CheckConstraint(
            "version >= 1",
            name="ck_project_analysis_profiles_version",
        ),
        CheckConstraint(
            "mode IN ('manual_only', 'automatic')",
            name="ck_project_analysis_profiles_mode",
        ),
        CheckConstraint(
            "jsonb_typeof(required_fields) = 'array'",
            name="ck_project_analysis_profiles_required_fields",
        ),
        CheckConstraint(
            "jsonb_typeof(media_slots) = 'array'",
            name="ck_project_analysis_profiles_media_slots",
        ),
        CheckConstraint(
            "btrim(result_schema_version) <> ''",
            name="ck_project_analysis_profiles_result_schema_version",
        ),
        CheckConstraint(
            "jsonb_typeof(confidence_thresholds) = 'object'",
            name="ck_project_analysis_profiles_confidence_thresholds",
        ),
        CheckConstraint(
            "jsonb_typeof(auto_publish_warning_levels) = 'array'",
            name="ck_project_analysis_profiles_warning_levels",
        ),
        CheckConstraint(
            "publish_mode IN ('auto_publish', 'review_required')",
            name="ck_project_analysis_profiles_publish_mode",
        ),
        CheckConstraint(
            "mode <> 'manual_only' OR "
            "(adapter_id IS NULL AND adapter_version IS NULL "
            "AND scoring_rule_version IS NULL "
            "AND confidence_thresholds = '{}'::jsonb "
            "AND auto_publish_warning_levels = '[]'::jsonb "
            "AND publish_mode = 'review_required')",
            name="ck_project_analysis_profiles_manual_only",
        ),
        CheckConstraint(
            "configured_by_user_id IS NOT NULL OR "
            "(version = 1 AND mode = 'manual_only' "
            "AND change_reason = 'migration backfill')",
            name="ck_project_analysis_profiles_configured_by",
        ),
        CheckConstraint(
            "btrim(change_reason) <> ''",
            name="ck_project_analysis_profiles_change_reason",
        ),
        UniqueConstraint(
            "project_id",
            "version",
            name="uq_project_analysis_profiles_project_version",
        ),
        UniqueConstraint(
            "id",
            "project_id",
            "version",
            name="uq_project_analysis_profiles_system_evaluation_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    adapter_id: Mapped[str | None] = mapped_column(String(80))
    adapter_version: Mapped[str | None] = mapped_column(String(40))
    scoring_rule_version: Mapped[str | None] = mapped_column(String(40))
    required_fields: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sql_text("'[]'::jsonb"),
    )
    media_slots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sql_text("'[]'::jsonb"),
    )
    result_schema_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="1",
        server_default="1",
    )
    confidence_thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
    )
    auto_publish_warning_levels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sql_text("'[]'::jsonb"),
    )
    publish_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    configured_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    change_reason: Mapped[str] = mapped_column(String(300), nullable=False)
