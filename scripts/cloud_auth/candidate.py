"""Host-only candidate data. Secrets never participate in representations."""

import hashlib
import http.client
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote
from uuid import UUID

from erp_cloud_transport import CloudError, Transport

SOURCE_FIELDS = (
    "protocol_version", "mode", "auth_config_id", "auth_config_revision", "app_revision"
)


def validate_auth_source(provider: str, source: dict) -> dict:
    """Only the identified v1 wire may omit frozen source configuration."""
    if not isinstance(source, dict) or set(source) != set(SOURCE_FIELDS):
        raise CloudError("cloud_ownership_mismatch")
    version, mode = source["protocol_version"], source["mode"]
    revision, app_revision = source["auth_config_revision"], source["app_revision"]
    valid = provider in ("baidu", "115") and type(version) is int
    if version == 1:
        valid = valid and mode == "own_app" and all(
            source[key] is None for key in SOURCE_FIELDS[2:]
        )
    elif version == 2 and mode == "own_app":
        valid = valid and (type(app_revision) is int and app_revision >= 1
                          and type(revision) is int and revision == app_revision
                          and source["auth_config_id"] == "own-app:" + provider)
    elif version == 2 and mode == "openlist_builtin_import":
        valid = valid and (app_revision is None and type(revision) is int and revision == 1
                          and source["auth_config_id"] == "openlist-builtin-import-v1:" + provider)
    else:
        valid = False
    if not valid:
        raise CloudError("cloud_ownership_mismatch")
    return dict(source)


def logical_directory(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or len(value) > 1024
        or any(c in value for c in "?#\\")
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or (
            value != "/"
            and any(part in ("", ".", "..") for part in value[1:].split("/"))
        )
    ):
        raise CloudError("cloud_invalid_path")
    # Check hidden traversal, never replace the logical name by a decoded value.
    # Thus literal %20/%25 remain literal, but %2e%2e/%252f are refused.
    decoded = value
    for _ in range(8):
        try:
            next_value = unquote(decoded, errors="strict")
        except UnicodeError:
            raise CloudError("cloud_invalid_path") from None
        if next_value == decoded:
            return value
        if (
            next_value.count("/") != decoded.count("/")
            or any(c in next_value for c in "?#\\")
            or any(ord(c) < 32 or ord(c) == 127 for c in next_value)
            or any(part in (".", "..") for part in next_value.split("/"))
        ):
            raise CloudError("cloud_invalid_path")
        decoded = next_value
    raise CloudError("cloud_invalid_path")


@dataclass(frozen=True)
class CandidateRef:
    operation_id: UUID
    storage_id: str
    webdav_user_id: str
    mount_path: str
    account_fingerprint: str


@dataclass(frozen=True)
class CandidateAccess:
    ref: CandidateRef
    username: str
    password: str = field(repr=False)
    origin: str
    directory: str | None
    candidate_id: UUID
    config_revision: int


@dataclass(frozen=True)
class DirectoryEntry:
    path: str
    name: str
    selectable: bool


@dataclass(frozen=True)
class CandidateOwnership:
    operation_id: UUID
    connector_id: UUID
    candidate_id: UUID
    config_revision: int
    provider: str
    mount_path: str
    username: str
    password: str = field(repr=False)
    marker: str = field(repr=False)
    account_fingerprint: str
    raw_account_id: str = field(repr=False)
    storage_id: str | None = None
    webdav_user_id: str | None = None
    meta_id: str | None = None
    directory: str | None = None
    delivery_started: bool = False
    delivered: bool = False
    creation_unknown: bool = False
    scope_previous: str | None = None
    scope_pending: bool = False
    protocol_version: int = 1
    mode: str = "own_app"
    auth_config_id: str | None = None
    auth_config_revision: int | None = None
    app_revision: int | None = None

    def __post_init__(self):
        validate_auth_source(self.provider, self.auth_source())
        if (not all(isinstance(value, UUID) for value in (
            self.operation_id, self.connector_id, self.candidate_id
        )) or type(self.config_revision) is not int or self.config_revision < 1):
            raise CloudError("cloud_ownership_mismatch")

    def auth_source(self):
        return {key: getattr(self, key) for key in SOURCE_FIELDS}


class CandidateJournal(Protocol):
    """Backend implementation must commit encrypted records and fence every write."""

    def reserve(self, record: CandidateOwnership) -> CandidateOwnership: ...
    def read(self, operation_id: UUID) -> CandidateOwnership | None: ...
    def save(self, record: CandidateOwnership) -> None: ...
    def reserve_probe(
        self,
        operation_id: UUID,
        ref: CandidateRef,
        directory: str,
        path: str,
        sha256: str,
    ) -> None: ...
    def finish_probe(
        self, operation_id: UUID, error_code: str | None, cleanup_pending: bool
    ) -> None: ...


@dataclass(frozen=True)
class ProbeEvidence:
    operation_id: UUID
    candidate_id: UUID
    config_revision: int
    directory: str
    sha256: str
    verified_at: datetime


def verify_candidate_scope(access: CandidateAccess, probe_path: str) -> None:
    """The pinned server must explicitly reject traversal for read and write.

    Use only the journaled random probe basename. No remote existing object is
    targeted. 404/405 aren't proof of scope denial; every method must give 403.
    """
    if access.origin != "http://127.0.0.1:5244":
        raise CloudError("cloud_scope_unverified")
    auth = Transport(access.origin, access.username, access.password).authorization
    name = probe_path.removeprefix("/")
    if not re.fullmatch(r"erp-probe-[a-f0-9]{32}-[a-f0-9]{32}\.bin", name):
        raise CloudError("cloud_scope_unverified")
    for method in ("GET", "PUT", "MKCOL"):
        connection = http.client.HTTPConnection("127.0.0.1", 5244, timeout=20)
        try:
            connection.request(
                method,
                f"/dav/%2e%2e/{name}",
                headers={"Authorization": auth, "Content-Length": "0"},
            )
            response = connection.getresponse()
            if response.status != 403 or len(response.read(65537)) > 65536:
                raise CloudError("cloud_scope_unverified")
        except (OSError, http.client.HTTPException):
            raise CloudError("cloud_scope_unverified") from None
        finally:
            connection.close()


def probe_candidate(
    access: CandidateAccess,
    directory: str,
    operation_id: UUID,
    *,
    journal: CandidateJournal,
    transport=None,
    scope_checker=verify_candidate_scope,
) -> ProbeEvidence:
    directory = logical_directory(directory)
    if directory != access.directory or not isinstance(operation_id, UUID):
        raise CloudError("cloud_probe_stale")
    # The dedicated user is rooted at selected directory, not the full mount.
    path = f"/erp-probe-{operation_id.hex}-{secrets.token_hex(16)}.bin"
    payload = secrets.token_bytes(64)
    digest = hashlib.sha256(payload).hexdigest()
    journal.reserve_probe(operation_id, access.ref, directory, path, digest)
    dav = transport or Transport(access.origin, access.username, access.password)
    error = None
    cleanup_pending = False
    try:
        try:
            scope_checker(access, path)
        except Exception:  # noqa: BLE001 -- private boundary: no external exception text
            raise CloudError("cloud_scope_unverified") from None
        with tempfile.TemporaryDirectory(prefix="erp-cloud-probe-") as temporary:
            source, destination = (
                Path(temporary) / "source",
                Path(temporary) / "readback",
            )
            descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            dav.put(path, source)
            dav.get(path, destination, 64)
            if destination.read_bytes() != payload:
                raise CloudError("cloud_readback_failed")
    except CloudError as exc:
        error = (
            str(exc)
            if str(exc)
            in (
                "cloud_scope_unverified",
                "cloud_readback_failed",
                "cloud_remote_corrupt",
                "cloud_redirect_rejected",
            )
            else "cloud_probe_failed"
        )
    except Exception:  # noqa: BLE001 -- redact remote and filesystem details
        error = "cloud_probe_failed"
    finally:
        try:
            dav.delete(path)
        except Exception:  # noqa: BLE001 -- cleanup never replaces the first failure
            cleanup_pending = True
        journal.finish_probe(operation_id, error, cleanup_pending)
    if error:
        raise CloudError(error)
    if cleanup_pending:
        raise CloudError("cloud_probe_cleanup_pending")
    return ProbeEvidence(
        operation_id,
        access.candidate_id,
        access.config_revision,
        directory,
        digest,
        datetime.now(UTC),
    )
