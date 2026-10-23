from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount


def add_test_primary_login_identifier(
    db: Session,
    account: UserAccount,
    *,
    kind: str = "phone",
) -> AccountLoginIdentifier:
    if kind not in {"phone", "email"}:
        raise ValueError("test login identifier kind is invalid")
    db.flush()
    digest = hashlib.sha256(
        f"test-primary-login:{kind}:{account.id}".encode()
    ).hexdigest()
    identifier = AccountLoginIdentifier(
        account_id=account.id,
        kind=kind,
        identifier_digest=digest,
        verified_at=datetime.now(UTC),
        verification_source="test_fixture",
        is_primary=True,
    )
    db.add(identifier)
    return identifier
