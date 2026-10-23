from __future__ import annotations

import math
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.adapter_registry import AdapterNotSupported, require_adapter
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.errors import (
    AnalysisAdapterNotAccepted,
    AnalysisProfileError,
    AnalysisProfileVersionConflict,
    EvaluationResourceNotFound,
    InvalidAnalysisProfile,
)
from app.evaluations.schemas import AnalysisProfileCreate
from app.teaching.models import GameProject


class AnalysisProfileService:
    """Appends immutable project analysis profile versions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _invalid() -> InvalidAnalysisProfile:
        return InvalidAnalysisProfile("analysis profile configuration is invalid")

    @classmethod
    def _validate_input(cls, input: AnalysisProfileCreate) -> None:
        required_fields = input.required_fields
        if (
            any(not field.strip() for field in required_fields)
            or len(required_fields) != len(set(required_fields))
        ):
            raise cls._invalid()
        if input.mode == "manual_only":
            if (
                input.adapter_id is not None
                or input.adapter_version is not None
                or input.scoring_rule_version is not None
                or input.confidence_thresholds
                or input.auto_publish_warning_levels
                or input.publish_mode != "review_required"
            ):
                raise cls._invalid()
            return

        if (
            input.adapter_id is None
            or not input.adapter_id.strip()
            or input.adapter_version is None
            or not input.adapter_version.strip()
            or input.scoring_rule_version is None
            or not input.scoring_rule_version.strip()
            or not required_fields
            or set(input.confidence_thresholds) != set(required_fields)
            or any(
                not math.isfinite(threshold) or not 0 <= threshold <= 1
                for threshold in input.confidence_thresholds.values()
            )
        ):
            raise cls._invalid()
        try:
            adapter = require_adapter(input.adapter_id, input.adapter_version)
        except AdapterNotSupported as exc:
            raise AnalysisAdapterNotAccepted(
                "analysis adapter is not accepted"
            ) from exc
        if (
            input.scoring_rule_version != adapter.scoring_rule_version
            or input.result_schema_version != adapter.result_schema_version
        ):
            raise AnalysisAdapterNotAccepted("analysis adapter is not accepted")

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> AnalysisProfileError:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_project_analysis_profiles_project_version":
            return AnalysisProfileVersionConflict(
                "analysis profile current version changed"
            )
        if isinstance(constraint, str) and constraint.startswith(
            "ck_project_analysis_profiles_"
        ):
            return InvalidAnalysisProfile(
                "analysis profile configuration is invalid"
            )
        return AnalysisProfileError("analysis profile transaction failed")

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidAnalysisProfile("analysis profile timestamp is invalid")

    def current_at(
        self,
        project_id: UUID,
        *,
        occurred_at: datetime,
    ) -> ProjectAnalysisProfile | None:
        self._require_aware(occurred_at)
        return self.db.scalar(
            select(ProjectAnalysisProfile)
            .where(
                ProjectAnalysisProfile.project_id == project_id,
                ProjectAnalysisProfile.effective_from <= occurred_at,
            )
            .order_by(
                ProjectAnalysisProfile.effective_from.desc(),
                ProjectAnalysisProfile.version.desc(),
            )
            .limit(1)
        )

    def create_version(
        self,
        project_id: UUID,
        input: AnalysisProfileCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> ProjectAnalysisProfile:
        if actor_user_id is None:
            raise self._invalid()
        self._validate_input(input)
        try:
            with write_transaction(self.db):
                project = self.db.scalar(
                    select(GameProject)
                    .where(GameProject.id == project_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if project is None:
                    raise EvaluationResourceNotFound("project not found")
                latest = self.db.scalar(
                    select(ProjectAnalysisProfile)
                    .where(ProjectAnalysisProfile.project_id == project.id)
                    .order_by(ProjectAnalysisProfile.version.desc())
                    .limit(1)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if latest is None:
                    raise AnalysisProfileError("analysis profile history is unavailable")
                if latest.version != input.expected_current_version:
                    raise AnalysisProfileVersionConflict(
                        "analysis profile current version changed"
                    )
                profile = ProjectAnalysisProfile(
                    project_id=project.id,
                    version=latest.version + 1,
                    mode=input.mode,
                    adapter_id=input.adapter_id,
                    adapter_version=input.adapter_version,
                    scoring_rule_version=input.scoring_rule_version,
                    required_fields=list(input.required_fields),
                    media_slots=[slot.model_dump() for slot in input.media_slots],
                    result_schema_version=input.result_schema_version,
                    confidence_thresholds=dict(input.confidence_thresholds),
                    auto_publish_warning_levels=list(
                        input.auto_publish_warning_levels
                    ),
                    publish_mode=input.publish_mode,
                    effective_from=input.effective_from,
                    configured_by_user_id=actor_user_id,
                    change_reason=input.change_reason,
                )
                self.db.add(profile)
                self.db.flush()
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="evaluation.analysis_profile.create",
                    resource_type="project_analysis_profile",
                    resource_id=profile.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={
                        "project_id": str(project.id),
                        "analysis_profile_id": str(profile.id),
                        "profile_version": profile.version,
                        "analysis_mode": profile.mode,
                    },
                )
            return profile
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc
