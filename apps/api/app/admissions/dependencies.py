from __future__ import annotations

import base64
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.admissions.case_service import AdmissionCaseService
from app.admissions.credential_service import CredentialService
from app.admissions.enrollment_service import EnrollmentService
from app.admissions.payment_service import PaymentService
from app.admissions.withdrawal_service import WithdrawalService
from app.core.config import Settings
from app.core.database import get_db
from app.security.encryption import EnvelopeCipher, LookupDigest


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def get_credential_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> CredentialService:
    settings: Settings = request.app.state.settings
    return CredentialService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        signature_tolerance_seconds=settings.payment_signature_tolerance_seconds,
        nonce_ttl_hours=settings.payment_nonce_ttl_hours,
        requests_per_minute=settings.payment_import_requests_per_minute,
    )


def get_payment_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> PaymentService:
    settings: Settings = request.app.state.settings
    return PaymentService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
    )


def get_admission_case_service(
    db: Annotated[Session, Depends(get_db)],
) -> AdmissionCaseService:
    return AdmissionCaseService(db)


def get_enrollment_service(
    db: Annotated[Session, Depends(get_db)],
) -> EnrollmentService:
    return EnrollmentService(db)


def get_withdrawal_service(
    db: Annotated[Session, Depends(get_db)],
) -> WithdrawalService:
    return WithdrawalService(db)


def get_lookup_digest(request: Request) -> LookupDigest:
    settings: Settings = request.app.state.settings
    return LookupDigest(_secret_bytes(settings, "field_lookup_key"))


def get_envelope_cipher(request: Request) -> EnvelopeCipher:
    settings: Settings = request.app.state.settings
    return EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key"))
