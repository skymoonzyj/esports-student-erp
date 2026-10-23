from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from pydantic import ValidationError

from app.housing.reporting_types import HousingReportProjectionV1
from app.security.encryption import EncryptedFieldError, EnvelopeCipher

SNAPSHOT_FIELD_NAME = "housing_report_snapshot"
SUPPORTED_SNAPSHOT_KEY_VERSIONS = frozenset({1})


class SnapshotError(ValueError):
    pass


class SnapshotIntegrityError(SnapshotError):
    pass


class SnapshotVersionError(SnapshotError):
    pass


@dataclass(frozen=True, slots=True)
class SealedSnapshot:
    ciphertext: str
    key_version: int
    digest: str


def canonical_projection_json(projection: HousingReportProjectionV1) -> str:
    return json.dumps(
        projection.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _ciphertext_digest(ciphertext: str) -> str:
    return hashlib.sha256(ciphertext.encode("utf-8")).hexdigest()


def _require_supported_key_version(key_version: int) -> None:
    if key_version not in SUPPORTED_SNAPSHOT_KEY_VERSIONS:
        raise SnapshotVersionError("unsupported snapshot key version")


def seal_projection(
    projection: HousingReportProjectionV1,
    *,
    cipher: EnvelopeCipher,
    key_version: int = 1,
) -> SealedSnapshot:
    _require_supported_key_version(key_version)
    ciphertext = cipher.encrypt(
        canonical_projection_json(projection),
        field_name=SNAPSHOT_FIELD_NAME,
    )
    return SealedSnapshot(
        ciphertext=ciphertext,
        key_version=key_version,
        digest=_ciphertext_digest(ciphertext),
    )


def open_projection(
    sealed: SealedSnapshot,
    *,
    cipher: EnvelopeCipher,
) -> HousingReportProjectionV1:
    _require_supported_key_version(sealed.key_version)
    expected_digest = _ciphertext_digest(sealed.ciphertext)
    if not hmac.compare_digest(sealed.digest, expected_digest):
        raise SnapshotIntegrityError("snapshot integrity check failed")
    try:
        plaintext = cipher.decrypt(
            sealed.ciphertext,
            field_name=SNAPSHOT_FIELD_NAME,
        )
    except EncryptedFieldError:
        raise SnapshotIntegrityError("snapshot decryption failed") from None
    try:
        payload = json.loads(plaintext)
    except (TypeError, json.JSONDecodeError):
        raise SnapshotIntegrityError("snapshot JSON is invalid") from None
    if not isinstance(payload, dict):
        raise SnapshotIntegrityError("snapshot payload is invalid")
    if payload.get("schema_version") != 1:
        raise SnapshotVersionError("unsupported projection schema")
    try:
        return HousingReportProjectionV1.model_validate(payload)
    except ValidationError:
        raise SnapshotIntegrityError("snapshot payload validation failed") from None
