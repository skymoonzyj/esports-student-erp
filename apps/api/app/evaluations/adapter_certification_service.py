from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.access import (
    EvaluationAccessService,
    EvaluationResource,
    EvaluationSqlResource,
)
from app.evaluations.adapter_registry import AdapterNotSupported, require_adapter
from app.evaluations.errors import (
    EvaluationAdapterCertificationConflict,
    EvaluationAdapterNotSupported,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.processing_models import EvaluationAdapterCertification
from app.evaluations.review_schemas import EvaluationAdapterCertificationInput
from app.teaching.models import GameProject

_CERTIFY_PERMISSION = "evaluation_adapters.certify"
_CENTER_PERMISSION = "evaluation_center.access"


@dataclass(frozen=True)
class EvaluationCertificationProjectPage:
    items: tuple[GameProject, ...]
    total: int
    page: int
    page_size: int


class EvaluationAdapterCertificationService:
    def __init__(
        self,
        db: Session,
        *,
        idempotency_service: EvaluationIdempotencyService,
        access_service: EvaluationAccessService | None = None,
    ) -> None:
        self.db = db
        self.idempotency = idempotency_service
        self.access = access_service or EvaluationAccessService(db)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    def _require_global_capability(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> None:
        decision = self.access.decide(
            account_id=actor_user_id,
            permission=_CERTIFY_PERMISSION,
            resource=EvaluationResource(),
            occurred_at=occurred_at,
        )
        if not decision.allowed:
            raise EvaluationOperationDenied("evaluation operation is not allowed")

    def _require_project_scope(
        self,
        *,
        actor_user_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
    ) -> None:
        decision = self.access.decide(
            account_id=actor_user_id,
            permission=_CERTIFY_PERMISSION,
            resource=EvaluationResource(project_id=project_id),
            occurred_at=occurred_at,
        )
        if not decision.allowed:
            raise EvaluationResourceNotFound("evaluation resource not found")
        center_decision = self.access.decide(
            account_id=actor_user_id,
            permission=_CENTER_PERMISSION,
            resource=EvaluationResource(project_id=project_id),
            occurred_at=occurred_at,
        )
        if not center_decision.allowed:
            raise EvaluationResourceNotFound("evaluation resource not found")

    @staticmethod
    def _require_supported_tuple(input: EvaluationAdapterCertificationInput) -> None:
        try:
            adapter = require_adapter(input.adapter_id, input.adapter_version)
        except AdapterNotSupported as exc:
            raise EvaluationAdapterNotSupported(
                "evaluation adapter is not supported"
            ) from exc
        if (
            adapter.scoring_rule_version != input.scoring_rule_version
            or adapter.result_schema_version != input.result_schema_version
        ):
            raise EvaluationAdapterNotSupported(
                "evaluation adapter is not supported"
            )

    @staticmethod
    def _matches_input(
        certification: EvaluationAdapterCertification,
        input: EvaluationAdapterCertificationInput,
    ) -> bool:
        return bool(
            certification.project_id == input.project_id
            and certification.adapter_id == input.adapter_id
            and certification.adapter_version == input.adapter_version
            and certification.scoring_rule_version == input.scoring_rule_version
            and certification.result_schema_version == input.result_schema_version
            and certification.manifest_digest == input.manifest_digest
        )

    def list_certifications(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> tuple[EvaluationAdapterCertification, ...]:
        self._require_aware(occurred_at)
        self._require_global_capability(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        authorization = self.access.center_scoped_sql_context(
            account_id=actor_user_id,
            permission=_CERTIFY_PERMISSION,
            occurred_at=occurred_at,
        )
        return tuple(
            self.db.scalars(
                select(EvaluationAdapterCertification)
                .where(
                    authorization.allowed(
                        EvaluationSqlResource(
                            project_id=EvaluationAdapterCertification.project_id,
                        )
                    )
                )
                .order_by(
                    EvaluationAdapterCertification.certified_at.desc(),
                    EvaluationAdapterCertification.id.desc(),
                )
            )
        )

    def list_projects(
        self,
        *,
        actor_user_id: UUID,
        query: str,
        page: int,
        page_size: int,
        occurred_at: datetime,
    ) -> EvaluationCertificationProjectPage:
        self._require_aware(occurred_at)
        self._require_global_capability(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        authorization = self.access.center_scoped_sql_context(
            account_id=actor_user_id,
            permission=_CERTIFY_PERMISSION,
            occurred_at=occurred_at,
        )
        statement = select(GameProject).where(
            GameProject.is_active.is_(True),
            authorization.allowed(
                EvaluationSqlResource(project_id=GameProject.id),
            ),
        )
        normalized_query = query.strip()
        if normalized_query:
            escaped = (
                normalized_query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            statement = statement.where(
                GameProject.name.ilike(f"%{escaped}%", escape="\\")
            )
        total = int(
            self.db.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        items = tuple(
            self.db.scalars(
                statement.order_by(GameProject.sort_order, GameProject.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return EvaluationCertificationProjectPage(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    def require_capability(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self._require_aware(occurred_at)
        self._require_global_capability(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )

    def create(
        self,
        input: EvaluationAdapterCertificationInput,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationAdapterCertification:
        self._require_aware(occurred_at)
        digests = self.idempotency.prepare(
            scope="evaluation.adapter.certification.create",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload=input.model_dump(mode="json"),
        )
        with write_transaction(self.db):
            self._require_global_capability(
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
            )
            project = self.db.scalar(
                select(GameProject)
                .where(
                    GameProject.id == input.project_id,
                    GameProject.is_active.is_(True),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if project is None:
                raise EvaluationResourceNotFound("evaluation resource not found")
            self._require_project_scope(
                actor_user_id=actor_user_id,
                project_id=project.id,
                occurred_at=occurred_at,
            )
            self._require_supported_tuple(input)
            replay_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=occurred_at,
            )
            if replay_id is not None:
                replay = self.db.scalar(
                    select(EvaluationAdapterCertification)
                    .where(EvaluationAdapterCertification.id == replay_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if replay is None or not self._matches_input(replay, input):
                    raise RuntimeError("evaluation certification replay is invalid")
                return replay

            existing = self.db.scalar(
                select(EvaluationAdapterCertification)
                .where(
                    EvaluationAdapterCertification.project_id == project.id,
                    EvaluationAdapterCertification.adapter_id == input.adapter_id,
                    EvaluationAdapterCertification.adapter_version
                    == input.adapter_version,
                    EvaluationAdapterCertification.scoring_rule_version
                    == input.scoring_rule_version,
                    EvaluationAdapterCertification.result_schema_version
                    == input.result_schema_version,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if existing is not None:
                if existing.manifest_digest != input.manifest_digest:
                    raise EvaluationAdapterCertificationConflict(
                        "evaluation adapter certification changed"
                    )
                self.idempotency.record(
                    digests,
                    resource_id=existing.id,
                    occurred_at=occurred_at,
                )
                return existing

            certification = EvaluationAdapterCertification(
                project_id=project.id,
                adapter_id=input.adapter_id,
                adapter_version=input.adapter_version,
                scoring_rule_version=input.scoring_rule_version,
                result_schema_version=input.result_schema_version,
                manifest_digest=input.manifest_digest,
                certified_by_user_id=actor_user_id,
                certified_at=occurred_at,
            )
            self.db.add(certification)
            self.db.flush()
            self.idempotency.record(
                digests,
                resource_id=certification.id,
                occurred_at=occurred_at,
            )
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="evaluation.adapter.certified",
                resource_type="evaluation_adapter_certification",
                resource_id=certification.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "adapter_certification_id": str(certification.id),
                    "project_id": str(project.id),
                    "adapter_version": certification.adapter_version,
                    "scoring_rule_version": certification.scoring_rule_version,
                    "result_schema_version": certification.result_schema_version,
                    "evaluation_action": "certify",
                },
            )
            self.db.flush()
        return certification
