"""Private host runner RPC; never register this interface on an HTTP router."""

from __future__ import annotations

import json
import logging
import sys
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    field_validator,
)

from app.operations.cloud_schemas import validate_backup_id


class RequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncRequest(RequestBase):
    action: Literal["sync"]
    backup_id: str | None = None

    @field_validator("backup_id")
    @classmethod
    def safe_id(cls, value: str | None) -> str | None:
        return validate_backup_id(value) if value is not None else None


class ClaimRequest(RequestBase):
    action: Literal["claim"]


class HeartbeatRequest(RequestBase):
    action: Literal["heartbeat"]
    job_id: UUID
    revision: StrictInt = Field(ge=1)


class FinishRequest(RequestBase):
    action: Literal["finish"]
    job_id: UUID
    revision: StrictInt = Field(ge=1)
    success: StrictBool
    error_code: str | None = Field(default=None, max_length=128)
    backup_id: str | None = None

    @field_validator("backup_id")
    @classmethod
    def safe_id(cls, value: str | None) -> str | None:
        return validate_backup_id(value) if value is not None else None


REQUEST_ADAPTER: TypeAdapter[SyncRequest | ClaimRequest | HeartbeatRequest | FinishRequest] = (
    TypeAdapter(
        Annotated[
            SyncRequest | ClaimRequest | HeartbeatRequest | FinishRequest,
            Field(discriminator="action"),
        ]
    )
)


def main() -> int:
    logging.disable(logging.CRITICAL)
    result: dict[str, Any] = {"ok": False, "error_code": "cloud_bridge_failed"}
    code = 1
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized request")
        request = REQUEST_ADAPTER.validate_json(raw)
        # Import after validation so invalid requests do not even open a DB.
        from app.auth.dependencies import _secret_bytes
        from app.core.config import get_settings
        from app.core.database import SessionLocal
        from app.operations.cloud_service import CloudBackupService
        from app.security.encryption import EnvelopeCipher

        cipher = EnvelopeCipher(_secret_bytes(get_settings(), "field_encryption_master_key"))
        with SessionLocal() as db:
            service = CloudBackupService(db, cipher=cipher)
            if isinstance(request, SyncRequest):
                result = service.sync(request.backup_id)
            elif isinstance(request, ClaimRequest):
                result = service.claim()
            elif isinstance(request, FinishRequest):
                result = service.finish(
                    job_id=str(request.job_id),
                    revision=request.revision,
                    success=request.success,
                    error_code=request.error_code,
                    backup_id=request.backup_id,
                )
            else:
                result = service.heartbeat(job_id=str(request.job_id), revision=request.revision)
        code = 0
    except Exception:  # noqa: BLE001 - private RPC must never expose secret-bearing errors
        # No exception text, environment, credentials, traceback or request echo.
        result = {"ok": False, "error_code": "cloud_bridge_failed"}
    output = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
    if len(output) > 65536:
        output, code = '{"ok":false,"error_code":"cloud_bridge_failed"}', 1
    sys.stdout.write(output + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
