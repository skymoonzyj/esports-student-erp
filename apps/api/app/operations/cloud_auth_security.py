"""Live account/session authorization; never trust a queued actor's old role."""

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import _secret_bytes
from app.auth.models import AuthSession, UserAccount
from app.core.config import Settings, get_settings
from app.core.errors import ProblemError
from app.identity.models import Person
from app.operations.cloud_auth_errors import auth_problem
from app.operations.cloud_auth_models import (
    CloudAuthOperation,
    CloudAuthSession,
    CloudConnectorState,
)
from app.operations.cloud_auth_schemas import AuthActor, AuthMode, AuthProvider
from app.operations.cloud_auth_state import aad
from app.rbac.service import RbacService
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest


def import_payload_fingerprint(
    settings: Settings, row: CloudAuthSession, access_token: str, refresh_token: str
) -> str:
    canonical = json.dumps(
        {
            "provider": row.provider,
            "session_id": str(row.id),
            "mode": row.mode,
            "auth_config_id": row.auth_config_id,
            "auth_config_revision": row.auth_config_revision,
            "app_revision": row.app_revision,
            "access_token": access_token,
            "refresh_token": refresh_token,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return LookupDigest(_secret_bytes(settings, "field_lookup_key")).digest_domain(
        "cloud-auth-import-v1", canonical
    )


def import_token_digests(
    settings: Settings, provider: str, access_token: str, refresh_token: str
) -> list[str]:
    if provider not in ("baidu", "115"):
        raise auth_problem("cloud_provider_unsupported", 422)
    lookup = LookupDigest(_secret_bytes(settings, "field_lookup_key"))
    return sorted(
        {
            lookup.digest_domain(f"cloud-auth-import-token-v1:{provider}", token)
            for token in (access_token, refresh_token)
        }
    )


def _digest_aad(operation: CloudAuthOperation) -> str:
    return aad(
        cast(AuthProvider, operation.provider),
        operation.id,
        "import_token_digests",
        mode=cast(AuthMode, operation.mode),
        protocol_version=operation.protocol_version,
    )


def store_import_token_digests(
    operation: CloudAuthOperation, digests: list[str], cipher: EnvelopeCipher
) -> None:
    operation.import_token_digests_ciphertext = cipher.encrypt(
        json.dumps(sorted(set(digests)), separators=(",", ":")), field_name=_digest_aad(operation)
    )


def load_import_token_digests(operation: CloudAuthOperation, cipher: EnvelopeCipher) -> list[str]:
    if not operation.import_token_digests_ciphertext:
        return []
    try:
        values = json.loads(
            cipher.decrypt(
                operation.import_token_digests_ciphertext, field_name=_digest_aad(operation)
            )
        )
        if (
            not isinstance(values, list)
            or not values
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
                for value in values
            )
        ):
            raise ValueError("invalid digest record")
        return values
    except (EncryptedFieldError, ValueError, TypeError):
        raise auth_problem("cloud_import_checkpoint_invalid") from None


def known_import_token_reused(
    db: Session,
    provider: str,
    digests: list[str],
    exclude_operation_id: UUID | None = None,
    *,
    cipher: EnvelopeCipher | None = None,
) -> bool:
    # Terminal history remains non-reversible but encrypted. Provider locks held by callers
    # serialize submit and durable-send checks; queued requests are not yet consumed.
    cipher = cipher or EnvelopeCipher(_secret_bytes(get_settings(), "field_encryption_master_key"))
    query = select(CloudAuthOperation).where(
        CloudAuthOperation.provider == provider,
        CloudAuthOperation.import_token_digests_ciphertext.is_not(None),
        or_(
            CloudAuthOperation.action != "verify_import",
            CloudAuthOperation.refresh_phase != "not_started",
        ),
    )
    if exclude_operation_id is not None:
        query = query.where(CloudAuthOperation.id != exclude_operation_id)
    return any(
        set(digests).intersection(load_import_token_digests(operation, cipher))
        for operation in db.scalars(query)
    )


def require_auth_actor(db: Session, actor: AuthActor, *, lock: bool = False) -> None:
    account = db.scalar(
        select(UserAccount)
        .where(UserAccount.id == actor.account_id)
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise ProblemError(status=401, code="invalid_session", detail="登录已失效，请重新登录")
    # Same person -> account -> login-session ordering as account lifecycle writes.
    person_query = select(Person).where(Person.id == account.person_id)
    account_query = select(UserAccount).where(UserAccount.id == actor.account_id)
    session_query = select(AuthSession).where(
        AuthSession.id == actor.login_session_id, AuthSession.user_id == actor.account_id
    )
    if lock:
        person_query, account_query, session_query = (
            person_query.with_for_update(),
            account_query.with_for_update(),
            session_query.with_for_update(),
        )
    person = db.scalar(person_query.execution_options(populate_existing=True))
    account = db.scalar(account_query.execution_options(populate_existing=True))
    login = db.scalar(session_query.execution_options(populate_existing=True))
    if (
        account is None
        or account.status != "active"
        or person is None
        or person.deleted_at is not None
        or login is None
        or login.revoked_at is not None
        or login.expires_at <= datetime.now(UTC)
    ):
        raise ProblemError(status=401, code="invalid_session", detail="登录已失效，请重新登录")
    if account.force_password_change:
        raise ProblemError(
            status=403, code="force_password_change_required", detail="请先修改临时密码"
        )
    rbac = RbacService(db)
    if (
        not rbac.account_has_role(actor.account_id, "super_admin")
        or not rbac.decide(
            user_id=actor.account_id,
            permission="operations.monitor.read",
            resource_tokens={"global"},
        ).allowed
    ):
        raise ProblemError(
            status=403, code="permission_denied", detail="只有超级管理员可以管理网盘授权"
        )


def require_no_maintenance(db: Session) -> None:
    # Do not infer that an expired DB lease means host I/O has stopped.
    if (
        db.scalar(
            select(CloudConnectorState.id)
            .where(CloudConnectorState.maintenance_operation_id.is_not(None))
            .limit(1)
        )
        is not None
    ):
        raise auth_problem("cloud_maintenance_active")
