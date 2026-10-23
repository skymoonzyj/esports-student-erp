from __future__ import annotations

import base64
import hmac
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import ProblemError
from app.evaluations.analysis_profile_service import AnalysisProfileService
from app.evaluations.bridge_service import EvaluationBridgeService
from app.evaluations.config import EvaluationBridgeConfig
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.game_account_service import GameAccountService
from app.evaluations.media_service import EvaluationMediaService
from app.evaluations.media_store import PrivateMediaStore
from app.evaluations.staff_service import CohortStaffService
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
    store = PrivateMediaStore(
        root=settings.evaluation_media_root,
        quarantine_root=settings.evaluation_media_quarantine_root,
        max_bytes=settings.evaluation_media_max_bytes,
        max_pixels=settings.evaluation_media_max_pixels,
        preview_max_edge=settings.evaluation_media_preview_max_edge,
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
