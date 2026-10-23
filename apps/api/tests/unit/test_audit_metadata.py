from hashlib import sha256

import pytest

from app.audit.service import AuditMetadataRejected, AuditService
from app.core.privacy import contains_phone_or_identity

PHONE_SHAPED_DIGEST = sha256(b"food-export-diagnostic-191").hexdigest()


def test_assisted_evidence_failure_preserves_only_controlled_audit_error_code() -> None:
    metadata = {"error_code": "assisted_evidence_unavailable"}
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata

    sensitive_metadata = {**metadata, "error_detail": "cipher authentication failed"}
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(sensitive_metadata)
    assert AuditService.public_metadata(sensitive_metadata) == metadata


def test_organization_manager_audit_preserves_typed_identifiers_only() -> None:
    metadata = {
        "organization_id": "00000000-0000-4000-8000-000000000001",
        "target_account_id": "00000000-0000-4000-8000-000000000002",
        "assignment_id": "00000000-0000-4000-8000-000000000003",
    }
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize("key", ["organization_id", "target_account_id", "assignment_id"])
@pytest.mark.parametrize(
    "value", ["李明", "13900000000", "test@example.com", {"full_name": "李明"}]
)
def test_organization_manager_audit_rejects_names_and_contacts_in_identifier_keys(
    key, value
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata({key: value})
    assert AuditService.public_metadata({key: value}) == {}


@pytest.mark.parametrize("key", ["full_name", "nickname", "phone", "email", "organization_name"])
def test_organization_manager_audit_rejects_extra_personal_metadata(key) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata({key: "李明"})


def test_canonical_file_sha256_is_not_rejected_for_phone_shaped_digits() -> None:
    assert contains_phone_or_identity(PHONE_SHAPED_DIGEST)
    AuditService.validate_metadata({"file_sha256": PHONE_SHAPED_DIGEST})


def test_public_metadata_preserves_canonical_file_sha256_and_other_fields() -> None:
    metadata = {"file_sha256": PHONE_SHAPED_DIGEST, "format": "csv_zip", "person_count": 1}
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"file_sha256": "13900000000"},
        {"file_sha256": "110105199001010000"},
        {"file_sha256": "password=NotAnActualPassword123"},
        {"file_sha256": PHONE_SHAPED_DIGEST.upper()},
        {"file_sha256": PHONE_SHAPED_DIGEST[:-1]},
        {"file_sha256": PHONE_SHAPED_DIGEST + "0"},
        {"file_sha256": "g" * 64},
        {"file_sha256": None},
        {"file_sha256": 123},
        {"file_sha256": {"file_sha256": PHONE_SHAPED_DIGEST}},
        {"FILE_SHA256": PHONE_SHAPED_DIGEST},
        {"file_sha256_extra": PHONE_SHAPED_DIGEST},
        {"unknown": PHONE_SHAPED_DIGEST},
    ],
)
def test_file_sha256_exemption_requires_exact_key_and_canonical_shape(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == {}


def test_canonical_hash_does_not_allow_sensitive_unknown_metadata() -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(
            {"file_sha256": PHONE_SHAPED_DIGEST, "unknown": "13900000000"}
        )


def test_phone_shaped_hex_outside_file_sha256_remains_sensitive() -> None:
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_purpose(PHONE_SHAPED_DIGEST)
    assert AuditService.public_purpose(PHONE_SHAPED_DIGEST) == "敏感内容已隐藏"


@pytest.mark.parametrize("version", [1, 2, 3])
def test_talent_session_audit_accepts_and_preserves_known_catalog_and_scoring_versions(
    version: int,
) -> None:
    metadata = {
        "session_id": "00000000-0000-4000-8000-000000000001",
        "person_id": "00000000-0000-4000-8000-000000000002",
        "operation_key": "00000000-0000-4000-8000-000000000003",
        "catalog_version": f"talent-training-v{version}",
        "scoring_version": f"talent-score-v{version}",
        "result_count": 0,
    }
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize(
    "field,value",
    [
        ("catalog_version", "talent-training-v4"),
        ("catalog_version", "talent-training-v3-unreviewed"),
        ("scoring_version", "talent-score-v4"),
        ("scoring_version", "talent-score-v3-unreviewed"),
        ("catalog_version", "13900000000"),
        ("scoring_version", "password=NotAnActualPassword123"),
    ],
)
def test_talent_version_allowlist_still_rejects_and_hides_unknown_or_sensitive_values(
    field: str,
    value: str,
) -> None:
    metadata = {"result_count": 0, field: value}
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == {"result_count": 0}
