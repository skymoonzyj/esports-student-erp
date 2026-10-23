"""Bounded private stdin RPC. Never mount this interface on the HTTP router."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, BinaryIO, Literal, TextIO
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from app.operations.cloud_auth_requests import CloudDirectoryEntry
from app.operations.cloud_auth_schemas import (
    AuthContract,
    AuthFence,
    AuthMode,
    AuthSource,
    CandidateAccess,
    DeviceGrant,
    ImportedTokenGrant,
    PollResult,
    ProbeEvidence,
)


class WorkSource(AuthContract):
    protocol_version: Literal[1, 2] = 1
    mode: AuthMode = "own_app"
    auth_config_id: str | None = None
    auth_config_revision: int | None = Field(default=None, ge=1, strict=True)
    app_revision: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def source_version(self) -> WorkSource:
        if self.protocol_version == 1:
            if (
                self.mode != "own_app"
                or self.auth_config_id is not None
                or self.auth_config_revision is not None
                or self.app_revision is not None
            ):
                raise ValueError("invalid legacy work source")
        else:
            if self.auth_config_id is None or self.auth_config_revision is None:
                raise ValueError("source required for protocol v2")
            AuthSource(
                mode=self.mode,
                auth_config_id=self.auth_config_id,
                auth_config_revision=self.auth_config_revision,
                app_revision=self.app_revision,
            )
        return self


class ImportIdentity(AuthContract):
    provider: Literal["baidu", "115"]
    account_id: str = Field(pattern=r"^[1-9][0-9]{0,31}$", repr=False)
    display_name: str | None = Field(default=None, max_length=128, repr=False)


class ImportWorkResult(WorkSource):
    action: Literal["verify_import"]
    error_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def import_source(self) -> ImportWorkResult:
        if self.protocol_version != 2 or self.mode != "openlist_builtin_import":
            raise ValueError("builtin source required")
        return self


class QrResult(WorkSource):
    action: Literal["create_qr"]
    grant: DeviceGrant | None = None
    error_code: str | None = Field(default=None, max_length=64)


class PollWorkResult(WorkSource):
    action: Literal["poll"]
    poll: PollResult | None = None
    error_code: str | None = Field(default=None, max_length=64)


class CandidateResult(WorkSource):
    action: Literal["prepare", "select_directory"]
    access: CandidateAccess | None = None
    error_code: str | None = Field(default=None, max_length=64)


class DirectoryResult(WorkSource):
    action: Literal["list_directories"]
    entries: list[CloudDirectoryEntry] = Field(default_factory=list, max_length=500)
    error_code: str | None = Field(default=None, max_length=64)


class ProbeResult(WorkSource):
    action: Literal["probe"]
    evidence: ProbeEvidence | None = None
    error_code: str | None = Field(default=None, max_length=64)


class ApplyResult(WorkSource):
    action: Literal["apply", "unbind"]
    host_drain_receipt: UUID | None = None
    error_code: str | None = Field(default=None, max_length=64)


class CleanupResult(WorkSource):
    action: Literal["cleanup"]
    cleaned: bool = False
    error_code: str | None = Field(default=None, max_length=64)


AuthWorkResult = Annotated[
    ImportWorkResult
    | QrResult
    | PollWorkResult
    | CandidateResult
    | DirectoryResult
    | ProbeResult
    | ApplyResult
    | CleanupResult,
    Field(discriminator="action"),
]
RESULT_ADAPTER: TypeAdapter[Any] = TypeAdapter(AuthWorkResult)


class ClaimRequest(AuthContract):
    action: Literal["claim"]


class HeartbeatRequest(AuthContract):
    action: Literal["heartbeat"]
    connector_id: UUID | None = None


class RenewRequest(AuthContract):
    action: Literal["renew"]
    fence: AuthFence


class CompleteRequest(AuthContract):
    action: Literal["complete"]
    fence: AuthFence
    result: AuthWorkResult


class OwnershipRecord(WorkSource):
    operation_id: UUID
    connector_id: UUID
    candidate_id: UUID
    config_revision: int = Field(ge=1)
    provider: Literal["baidu", "115"]
    mount_path: str = Field(max_length=1024)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=4096, repr=False)
    marker: str = Field(min_length=1, max_length=255, repr=False)
    account_fingerprint: str = Field(min_length=1, max_length=255)
    raw_account_id: str = Field(min_length=1, max_length=255, repr=False)
    storage_id: str | None = Field(default=None, max_length=255)
    webdav_user_id: str | None = Field(default=None, max_length=255)
    meta_id: str | None = Field(default=None, max_length=255)
    directory: str | None = Field(default=None, max_length=1024)
    delivery_started: bool = False
    delivered: bool = False
    creation_unknown: bool = False
    scope_previous: str | None = Field(default=None, max_length=1024)
    scope_pending: bool = False


class CheckpointRequest(AuthContract):
    action: Literal["checkpoint"]
    fence: AuthFence
    kind: Literal["read", "reserve", "save", "reserve_probe", "finish_probe", "recover_probe"]
    operation_id: UUID
    record: OwnershipRecord | None = None
    directory: str | None = Field(default=None, max_length=1024)
    path: str | None = Field(default=None, max_length=128)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    error_code: str | None = Field(default=None, max_length=64)
    cleanup_pending: bool = False


class RefreshCheckpointRequest(AuthContract):
    action: Literal["refresh_checkpoint"]
    fence: AuthFence
    operation_id: UUID
    phase: Literal["not_started", "sent", "refreshed", "verified", "unknown"]
    identity: ImportIdentity | None = None
    tokens: ImportedTokenGrant | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def builtin_refresh(self) -> RefreshCheckpointRequest:
        if self.fence.protocol_version != 2 or self.fence.mode != "openlist_builtin_import":
            raise ValueError("builtin source required")
        if (self.phase in {"sent", "verified"}) != (self.identity is not None) or (
            self.phase == "refreshed"
        ) != (self.tokens is not None):
            raise ValueError("invalid refresh checkpoint fields")
        return self


class SnapshotFence(AuthContract):
    connector_id: UUID
    operation_id: UUID
    revision: Literal[1]
    expires_at: AwareDatetime


class BeginSnapshot(AuthContract):
    action: Literal["begin_snapshot"]
    connector_id: UUID
    operation_id: UUID


class EndSnapshot(AuthContract):
    action: Literal["end_snapshot"]
    fence: SnapshotFence


class RestoreSnapshot(AuthContract):
    action: Literal["restore_snapshot"]
    fence: SnapshotFence


REQUEST_ADAPTER: TypeAdapter[Any] = TypeAdapter(
    Annotated[
        ClaimRequest
        | HeartbeatRequest
        | RenewRequest
        | CompleteRequest
        | CheckpointRequest
        | RefreshCheckpointRequest
        | BeginSnapshot
        | EndSnapshot
        | RestoreSnapshot,
        Field(discriminator="action"),
    ]
)


def dispatch(request: Any) -> dict[str, Any]:
    # No DB configuration/import until untrusted input has passed strict validation.
    from app.core.database import SessionLocal
    from app.operations import cloud_auth_worker as worker

    now = datetime.now(UTC)
    with SessionLocal() as db:
        if request.action == "claim":
            work = worker.claim_auth_operation(db, now=now)
            return {"ok": True, "work": work}
        if request.action == "heartbeat":
            worker.heartbeat(db, now=now, connector_id=request.connector_id)
            return {"ok": True}
        if request.action == "renew":
            return {"ok": worker.renew_auth_operation(db, request.fence, now=now)}
        if request.action == "complete":
            return {
                "ok": worker.complete_auth_operation(db, request.fence, request.result, now=now)
            }
        if request.action == "checkpoint":
            return worker.checkpoint(db, request, now=now)
        if request.action == "refresh_checkpoint":
            from app.operations.cloud_auth_import_worker import refresh_checkpoint

            return refresh_checkpoint(db, request, now=now)
        if request.action == "begin_snapshot":
            return worker.begin_snapshot(db, request.connector_id, request.operation_id, now=now)
        if request.action == "restore_snapshot":
            return worker.restore_snapshot(db, request.fence, now=now)
        return worker.end_snapshot(db, request.fence, now=now)


def serve(
    source: BinaryIO, destination: TextIO, *, dispatch: Callable[[Any], dict[str, Any]] = dispatch
) -> int:
    failure = {"ok": False, "error_code": "cloud_auth_bridge_failed"}
    code = 0
    try:
        raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("request too large")
        request = REQUEST_ADAPTER.validate_json(raw)
        result = dispatch(request)
        output = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
        if not isinstance(result, dict) or len(output.encode()) > 65536:
            raise ValueError("response too large")
    except Exception:  # noqa: BLE001 - never expose private RPC payloads or exception text
        output, code = json.dumps(failure, separators=(",", ":")), 1
    destination.write(output + "\n")
    return code


def main() -> int:
    logging.disable(logging.CRITICAL)
    return serve(sys.stdin.buffer, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
