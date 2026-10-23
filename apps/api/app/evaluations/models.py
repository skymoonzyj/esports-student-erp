from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.game_models import StudentAuxiliaryPhone, StudentGameAccount
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.processing_models import (
    EvaluationAdapterCertification,
    SystemEvaluationAttempt,
)
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportReview,
    StudentReportVersion,
    StudentReportWindowGrant,
)
from app.evaluations.review_models import (
    SystemEvaluationManualDraft,
    SystemEvaluationResultVersion,
)
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
    EvaluationStaffRole,
)
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffRating,
    CampSurveyStaffSnapshot,
    CampSurveySubmission,
    CampSurveySupplement,
    CampSurveySupplementRating,
    CampSurveyVersion,
)
from app.evaluations.task_models import (
    EvaluationIdempotencyKey,
    EvaluationQuarantineCleanup,
    SystemEvaluationEvent,
    SystemEvaluationTask,
    SystemEvaluationTaskMedia,
    SystemEvaluationUploadSession,
    SystemEvaluationUploadSessionMedia,
)

__all__ = [
    "CampSurveyCycle",
    "CampSurveySnapshotVersion",
    "CampSurveyStaffRating",
    "CampSurveyStaffSnapshot",
    "CampSurveySubmission",
    "CampSurveySupplement",
    "CampSurveySupplementRating",
    "CampSurveyVersion",
    "CohortStaffAssignment",
    "CohortStaffAssignmentPermission",
    "EvaluationAdapterCertification",
    "EvaluationBridgeRequest",
    "EvaluationBridgeTicket",
    "EvaluationIdempotencyKey",
    "EvaluationMediaAsset",
    "EvaluationMediaBlob",
    "EvaluationQuarantineCleanup",
    "EvaluationStaffRole",
    "ProjectAnalysisProfile",
    "StudentAuxiliaryPhone",
    "StudentGameAccount",
    "StudentReport",
    "StudentReportDraft",
    "StudentReportReview",
    "StudentReportVersion",
    "StudentReportWindowGrant",
    "SystemEvaluationAttempt",
    "SystemEvaluationEvent",
    "SystemEvaluationManualDraft",
    "SystemEvaluationResultVersion",
    "SystemEvaluationTask",
    "SystemEvaluationTaskMedia",
    "SystemEvaluationUploadSession",
    "SystemEvaluationUploadSessionMedia",
]


class EvaluationBridgeRequest(TimestampMixin, Base):
    __tablename__ = "evaluation_bridge_requests"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('standalone', 'embedded')",
            name="ck_evaluation_bridge_requests_mode",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_evaluation_bridge_requests_expiry",
        ),
        Index("ix_evaluation_bridge_requests_expiry", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationBridgeTicket(TimestampMixin, Base):
    __tablename__ = "evaluation_bridge_tickets"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_evaluation_bridge_tickets_expiry",
        ),
        Index("ix_evaluation_bridge_tickets_expiry", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    login_request_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_bridge_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    auth_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
