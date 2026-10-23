from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import _secret_bytes
from app.core.database import get_db
from app.core.errors import ProblemError
from app.personnel_intake.claims.public_service import ProfileClaimService
from app.registration.dependencies import (
    RegistrationSecretMaterial,
    get_registration_verification_service,
    require_registration_secret_material,
)
from app.registration.verification_service import RegistrationVerificationService
from app.security.encryption import EnvelopeCipher, LookupDigest

COOKIE_NAME = "erp_profile_claim"
COOKIE_PATH = "/api/v1/auth/profile-claims"


def require_claim_cookie(request: Request) -> str:
    values = [
        value
        for header in request.headers.getlist("cookie")
        for part in header.split(";")
        for name, separator, value in [part.strip().partition("=")]
        if separator and name == COOKIE_NAME
    ]
    if len(values) != 1 or not values[0]:
        raise ProblemError(
            status=401,
            code="profile_claim_session_invalid",
            detail="认领会话已失效，请重新验证邮箱",
        )
    return values[0]


def get_profile_claim_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    verification: Annotated[
        RegistrationVerificationService, Depends(get_registration_verification_service)
    ],
    material: Annotated[RegistrationSecretMaterial, Depends(require_registration_secret_material)],
) -> ProfileClaimService:
    settings = request.app.state.settings
    return ProfileClaimService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
        verification=verification,
        token_pepper=material.session_token_pepper,
        email_pending_limit=settings.profile_claim_email_pending_limit,
        identity_pending_limit=settings.profile_claim_identity_pending_limit,
        global_pending_limit=settings.profile_claim_global_pending_limit,
    )
