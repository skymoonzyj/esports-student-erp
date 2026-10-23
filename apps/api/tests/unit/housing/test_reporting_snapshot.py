from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from uuid import UUID

import pytest

from app.housing.reporting_snapshot import (
    SnapshotIntegrityError,
    SnapshotVersionError,
    canonical_projection_json,
    open_projection,
    seal_projection,
)
from app.housing.reporting_types import HousingReportProjectionV1, ResidentReportRow
from app.security.encryption import EnvelopeCipher


def projection() -> HousingReportProjectionV1:
    return HousingReportProjectionV1(
        generated_at=datetime.fromisoformat("2026-08-06T12:00:00+08:00"),
        range_start=datetime.fromisoformat("2026-08-01T00:00:00+08:00"),
        range_end=datetime.fromisoformat("2026-08-22T00:00:00+08:00"),
        residents=(
            ResidentReportRow(
                row_id="opaque-row-1",
                person_id=UUID("10000000-0000-4000-8000-000000000001"),
                full_name="测试学员",
                phone="13800138000",
                identity_number="11010519491231002X",
                sex="male",
                date_of_birth=date(2000, 1, 2),
                person_type="student",
                building_id=UUID("20000000-0000-4000-8000-000000000001"),
                building_name="1号楼",
                building_order=1,
                floor_id=UUID("30000000-0000-4000-8000-000000000001"),
                floor_name="二层",
                floor_order=2,
                room_id=UUID("40000000-0000-4000-8000-000000000001"),
                room_number="0201",
                stay_id=UUID("50000000-0000-4000-8000-000000000001"),
                stay_purpose="permanent",
                actual_check_in_at=datetime.fromisoformat("2026-08-01T00:00:00+08:00"),
                actual_check_out_at=None,
                stay_status="checked_in",
                cohort_id=None,
                cohort_name=None,
                visit_reason=None,
                is_minor_at_check_in=False,
                guardian_count=0,
            ),
        ),
        guardians=(),
    )


def test_canonical_json_is_repeatable_and_unicode_preserving() -> None:
    first = canonical_projection_json(projection())
    second = canonical_projection_json(projection())

    assert first == second
    assert "测试学员" in first
    assert "\\u6d4b" not in first
    assert " " not in first


def test_snapshot_round_trip_and_digest_covers_stored_ciphertext() -> None:
    cipher = EnvelopeCipher(b"s" * 32)

    sealed = seal_projection(projection(), cipher=cipher, key_version=1)

    assert sealed.digest == hashlib.sha256(sealed.ciphertext.encode("utf-8")).hexdigest()
    assert open_projection(sealed, cipher=cipher) == projection()


def test_old_snapshot_without_phone_tags_remains_readable():
    cipher = EnvelopeCipher(b"s" * 32)
    old = projection().model_dump(mode="json")
    for row in old["residents"]:
        row.pop("phone_verified", None)
        row.pop("phone_evidence", None)
    ciphertext = cipher.encrypt(json.dumps(old), field_name="housing_report_snapshot")
    sealed = seal_projection(projection(), cipher=cipher, key_version=1).__class__(
        ciphertext=ciphertext,
        key_version=1,
        digest=hashlib.sha256(ciphertext.encode("utf-8")).hexdigest(),
    )
    row = open_projection(sealed, cipher=cipher).residents[0]
    assert row.phone_verified is True
    assert row.phone_evidence == "verified_identifier"


@pytest.mark.parametrize("person_type", ["player", "student_player"])
def test_new_source_snapshot_keeps_unverified_phone_tags(person_type):
    original = projection()
    original = original.model_copy(
        update={
            "residents": (
                original.residents[0].model_copy(
                    update={
                        "person_type": person_type,
                        "phone_verified": False,
                        "phone_evidence": "approved_profile",
                    }
                ),
            )
        }
    )
    cipher = EnvelopeCipher(b"s" * 32)
    assert (
        open_projection(seal_projection(original, cipher=cipher, key_version=1), cipher=cipher)
        == original
    )


def test_snapshot_rejects_ciphertext_digest_mismatch_before_decryption() -> None:
    cipher = EnvelopeCipher(b"s" * 32)
    sealed = seal_projection(projection(), cipher=cipher, key_version=1)
    damaged = sealed.__class__(
        ciphertext=sealed.ciphertext + "x",
        key_version=sealed.key_version,
        digest=sealed.digest,
    )

    with pytest.raises(SnapshotIntegrityError, match="snapshot integrity check failed"):
        open_projection(damaged, cipher=cipher)


@pytest.mark.parametrize("key_version", [0, 2, 999])
def test_snapshot_rejects_unsupported_key_version(key_version: int) -> None:
    cipher = EnvelopeCipher(b"s" * 32)

    with pytest.raises(SnapshotVersionError, match="unsupported snapshot key version"):
        seal_projection(projection(), cipher=cipher, key_version=key_version)


def test_snapshot_rejects_authenticated_but_unsupported_projection_schema() -> None:
    cipher = EnvelopeCipher(b"s" * 32)
    plaintext = canonical_projection_json(projection()).replace(
        '"schema_version":1',
        '"schema_version":2',
    )
    ciphertext = cipher.encrypt(plaintext, field_name="housing_report_snapshot")
    sealed = seal_projection(projection(), cipher=cipher, key_version=1).__class__(
        ciphertext=ciphertext,
        key_version=1,
        digest=hashlib.sha256(ciphertext.encode("utf-8")).hexdigest(),
    )

    with pytest.raises(SnapshotVersionError, match="unsupported projection schema"):
        open_projection(sealed, cipher=cipher)
