from __future__ import annotations

import base64
import hmac
from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import ProblemError
from app.evaluations.adapter_certification_service import (
    EvaluationAdapterCertificationService,
)
from app.evaluations.analysis_profile_service import AnalysisProfileService
from app.evaluations.bridge_service import EvaluationBridgeService
from app.evaluations.config import EvaluationBridgeConfig
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.errors import EvaluationAccessDenied, EvaluationBridgeError
from app.evaluations.game_account_service import GameAccountService
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.matching_service import EvaluationMatchingService
from app.evaluations.media_service import EvaluationMediaService
from app.evaluations.media_store import PrivateMediaStore
from app.evaluations.processing_service import EvaluationProcessingService
from app.evaluations.query_service import EvaluationQueryService
from app.evaluations.report_query_service import TeachingReportQueryService
from app.evaluations.report_service import TeachingReportService
from app.evaluations.report_workspace_query_service import (
    TeachingReportWorkspaceQueryService,
)
from app.evaluations.result_service import EvaluationResultService
from app.evaluations.review_service import EvaluationReviewService
from app.evaluations.schemas import EvaluationToolSessionContext
from app.evaluations.staff_service import CohortStaffService
from app.evaluations.survey_access import CampSurveyAccessService
from app.evaluations.survey_query_service import CampSurveyQueryService
from app.evaluations.survey_service import CampSurveyService
from app.evaluations.task_service import EvaluationTaskService
from app.evaluations.upload_service import EvaluationUploadService
from app.evaluations.worker_ticket import WorkerMediaTicketService
from app.security.encryption import EnvelopeCipher, LookupDigest


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def require_evaluation_tool_service(request: Request) -> None:
    authorization_values = request.headers.getlist("Authorization")
    provided = authorization_values[0].encode("utf-8") if len(authorization_values) == 1 else b""
    expected = (
        "Bearer " + request.app.state.settings.evaluation_tool_service_token.get_secret_value()
    ).encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        raise ProblemError(
            status=401,
            code="integration_auth_invalid",
            detail="集成服务身份无效",
        )


def require_evaluation_tool_web_service(request: Request) -> None:
    authorization_values = request.headers.getlist("Authorization")
    provided = authorization_values[0].encode("utf-8") if len(authorization_values) == 1 else b""
    expected = (
        "Bearer "
        + request.app.state.settings.evaluation_tool_web_service_token.get_secret_value()
    ).encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        raise ProblemError(
            status=401,
            code="integration_auth_invalid",
            detail="集成服务身份无效",
        )


def require_evaluation_tool_worker_service(request: Request) -> None:
    authorization_values = request.headers.getlist("Authorization")
    provided = (
        authorization_values[0].encode("utf-8")
        if len(authorization_values) == 1
        else b""
    )
    expected = (
        "Bearer "
        + request.app.state.settings.evaluation_tool_worker_service_token.get_secret_value()
    ).encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        raise ProblemError(
            status=401,
            code="integration_auth_invalid",
            detail="集成服务身份无效",
        )


def require_evaluation_tool_v2_service(request: Request) -> None:
    authorization_values = request.headers.getlist("Authorization")
    provided = (
        authorization_values[0].encode("utf-8")
        if len(authorization_values) == 1
        else b""
    )
    settings = request.app.state.settings
    expected_web = (
        "Bearer " + settings.evaluation_tool_web_service_token.get_secret_value()
    ).encode("utf-8")
    expected_worker = (
        "Bearer " + settings.evaluation_tool_worker_service_token.get_secret_value()
    ).encode("utf-8")
    if not (
        hmac.compare_digest(provided, expected_web)
        or hmac.compare_digest(provided, expected_worker)
    ):
        raise ProblemError(
            status=401,
            code="integration_auth_invalid",
            detail="集成服务身份无效",
        )


def get_evaluation_bridge_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationBridgeService:
    settings: Settings = request.app.state.settings
    return EvaluationBridgeService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        token_pepper=_secret_bytes(settings, "session_token_pepper"),
        config=EvaluationBridgeConfig.from_settings(settings),
    )


def require_evaluation_delegated_session(
    request: Request,
    service: Annotated[
        EvaluationBridgeService,
        Depends(get_evaluation_bridge_service),
    ],
) -> EvaluationToolSessionContext:
    values = request.headers.getlist("X-Evaluation-Delegated-Session")
    if len(values) != 1:
        raise ProblemError(
            status=401,
            code="delegated_session_invalid",
            detail="ERP 登录已失效，请重新连接",
        )
    raw = values[0]
    try:
        session_id = UUID(raw)
    except (TypeError, ValueError, AttributeError):
        session_id = None
    if session_id is None or str(session_id) != raw:
        raise ProblemError(
            status=401,
            code="delegated_session_invalid",
            detail="ERP 登录已失效，请重新连接",
        )
    try:
        return service.session_context(session_id)
    except EvaluationAccessDenied as exc:
        raise ProblemError(
            status=403,
            code="evaluation_access_denied",
            detail="评价中心权限已失效",
        ) from exc
    except EvaluationBridgeError as exc:
        raise ProblemError(
            status=401,
            code="delegated_session_invalid",
            detail="ERP 登录已失效，请重新连接",
        ) from exc


def get_evaluation_context_service(
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationContextService:
    return EvaluationContextService(db)


def get_game_account_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> GameAccountService:
    settings: Settings = request.app.state.settings
    return GameAccountService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
    )


def get_staff_service(
    db: Annotated[Session, Depends(get_db)],
) -> CohortStaffService:
    return CohortStaffService(db)


def get_analysis_profile_service(
    db: Annotated[Session, Depends(get_db)],
) -> AnalysisProfileService:
    return AnalysisProfileService(db)


def get_private_media_store(request: Request) -> Iterator[PrivateMediaStore]:
    settings: Settings = request.app.state.settings
    media_root, quarantine_root = settings.evaluation_media_paths()
    store = PrivateMediaStore(
        root=media_root,
        quarantine_root=quarantine_root,
        max_bytes=settings.evaluation_media_max_bytes,
        max_pixels=settings.evaluation_media_max_pixels,
        preview_max_edge=settings.evaluation_media_preview_max_edge,
        expected_identity=settings.evaluation_media_store_id,
    )
    try:
        yield store
    finally:
        store.close()


def get_evaluation_media_service(
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[PrivateMediaStore, Depends(get_private_media_store)],
) -> EvaluationMediaService:
    return EvaluationMediaService(db, store=store)


def get_evaluation_upload_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[PrivateMediaStore, Depends(get_private_media_store)],
) -> EvaluationUploadService:
    settings: Settings = request.app.state.settings
    return EvaluationUploadService(
        db, store=store,
        max_open_sessions_per_account=settings.evaluation_upload_max_open_sessions_per_account,
        max_open_sessions_total=settings.evaluation_upload_max_open_sessions_total,
    )


def get_evaluation_task_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[PrivateMediaStore, Depends(get_private_media_store)],
) -> EvaluationTaskService:
    settings: Settings = request.app.state.settings
    return EvaluationTaskService(
        db,
        store=store,
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=_secret_bytes(settings, "field_lookup_key"),
        ),
    )


def get_evaluation_matching_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationMatchingService:
    settings: Settings = request.app.state.settings
    digest_key = _secret_bytes(settings, "field_lookup_key")
    return EvaluationMatchingService(
        db,
        lookup=LookupDigest(digest_key),
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=digest_key,
        ),
    )


def get_evaluation_processing_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationProcessingService:
    settings: Settings = request.app.state.settings
    return EvaluationProcessingService(
        db,
        token_pepper=_secret_bytes(settings, "session_token_pepper"),
        include_test_adapters=settings.app_env == "test",
    )


def get_evaluation_query_service(
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationQueryService:
    return EvaluationQueryService(db)


def get_camp_survey_access_service(
    db: Annotated[Session, Depends(get_db)],
) -> CampSurveyAccessService:
    return CampSurveyAccessService(db)


def get_camp_survey_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> CampSurveyService:
    settings: Settings = request.app.state.settings
    return CampSurveyService(
        db,
        idempotency=EvaluationIdempotencyService(
            db,
            digest_key=_secret_bytes(settings, "field_lookup_key"),
        ),
    )


def get_camp_survey_query_service(
    db: Annotated[Session, Depends(get_db)],
) -> CampSurveyQueryService:
    return CampSurveyQueryService(db)


def get_teaching_report_query_service(
    db: Annotated[Session, Depends(get_db)],
) -> TeachingReportQueryService:
    return TeachingReportQueryService(db)


def get_teaching_report_workspace_query_service(
    db: Annotated[Session, Depends(get_db)],
) -> TeachingReportWorkspaceQueryService:
    return TeachingReportWorkspaceQueryService(db)


def get_teaching_report_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TeachingReportService:
    settings: Settings = request.app.state.settings
    return TeachingReportService(
        db,
        idempotency=EvaluationIdempotencyService(
            db,
            digest_key=_secret_bytes(settings, "field_lookup_key"),
        ),
    )


def get_evaluation_result_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationResultService:
    settings: Settings = request.app.state.settings
    return EvaluationResultService(
        db,
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=_secret_bytes(settings, "field_lookup_key"),
        ),
    )


def get_evaluation_review_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationReviewService:
    settings: Settings = request.app.state.settings
    return EvaluationReviewService(
        db,
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=_secret_bytes(settings, "field_lookup_key"),
        ),
    )


def get_evaluation_adapter_certification_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationAdapterCertificationService:
    settings: Settings = request.app.state.settings
    return EvaluationAdapterCertificationService(
        db,
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=_secret_bytes(settings, "field_lookup_key"),
        ),
    )


def get_worker_media_ticket_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[PrivateMediaStore, Depends(get_private_media_store)],
) -> WorkerMediaTicketService:
    settings: Settings = request.app.state.settings
    return WorkerMediaTicketService(
        db,
        store=store,
        secret=_secret_bytes(settings, "session_token_pepper"),
    )
