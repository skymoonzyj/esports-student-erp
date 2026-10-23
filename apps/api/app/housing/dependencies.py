from __future__ import annotations

import base64
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.housing.portal_service import HousingPortalService
from app.security.encryption import EnvelopeCipher, LookupDigest


def _decode_secret(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)


def get_housing_cipher(request: Request) -> EnvelopeCipher:
    settings = request.app.state.settings
    return EnvelopeCipher(
        _decode_secret(settings.field_encryption_master_key.get_secret_value())
    )


def get_housing_lookup(request: Request) -> LookupDigest:
    settings = request.app.state.settings
    return LookupDigest(_decode_secret(settings.field_lookup_key.get_secret_value()))


def get_housing_portal_service(
    db: Annotated[Session, Depends(get_db)],
) -> HousingPortalService:
    return HousingPortalService(db)


def get_housing_reporting_db(
    request_db: Annotated[Session, Depends(get_db)],
) -> Iterator[Session]:
    bind = request_db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    with Session(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
        yield session
