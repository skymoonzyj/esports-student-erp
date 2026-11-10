"""HKID checks use literal, independently weighted examples, never generated check digits."""

from datetime import date

import pytest

from app.identity.document_identity import document_identity_keys, normalize_document_identity
from app.identity.normalization import IdentityValidationError
from app.personnel_intake.codec import normalize_payload, normalize_pending_payload
from app.personnel_intake.schemas import IntakeInput, PendingIntakeInput
from app.security.encryption import LookupDigest


def document(number, **kwargs):
    return normalize_document_identity(
        "hk_id",
        number,
        issuing_country=kwargs.pop("country", None),
        sex=kwargs.pop("sex", None),
        date_of_birth=kwargs.pop("birth", None),
        allow_pending=kwargs.pop("pending", True),
        **kwargs,
    )


@pytest.mark.parametrize(
    "number,want",
    [
        ("A123456(3)", "A1234563"),
        (" a 123456 (3) ", "A1234563"),
        ("AB123456(9)", "AB1234569"),
        ("Z123456(1)", "Z1234561"),
        ("A000002(A)", "A000002A"),
        ("a123456（3）", "A1234563"),
    ],
)
def test_hkid_canonicalizes_checksum_valid_literals(number, want):
    # A1234563: 36*9 + 10*8 + 1*7+2*6+3*5+4*4+5*3+6*2 + 3 = 484.
    value = document(number, country="HK")
    assert value.number == want
    assert value.issuing_country is None
    assert value.sex is value.date_of_birth is None


@pytest.mark.parametrize(
    "number",
    [
        "A123456(4)",
        "A123456(3",
        "A1234563)",
        "AAA1234563",
        "1234563",
        "A123456（3)",
        "A123456(3）",
        "ß123456(8)",
    ],
)
def test_hkid_rejects_bad_checksum_and_malformed_format(number):
    with pytest.raises(IdentityValidationError):
        document(number)


def test_hkid_requires_explicit_strict_facts_and_fixed_issuer():
    for fields in ({}, {"sex": "male"}, {"birth": date(2000, 1, 1)}):
        with pytest.raises(IdentityValidationError):
            document("A1234563", pending=False, **fields)
    with pytest.raises(IdentityValidationError):
        document("A1234563", country="US")
    assert document("A1234563", pending=False, sex="female", birth=date(2000, 1, 1)).sex == "female"


def test_hkid_digest_is_separate_from_cn_and_passport():
    lookup = LookupDigest(bytes(range(32)))
    keys = document_identity_keys(document("A123456(3)"), lookup)
    passport = normalize_document_identity(
        "passport",
        "A1234563",
        issuing_country="HK",
        sex=None,
        date_of_birth=None,
        allow_pending=True,
    )
    assert keys.identity_digest == lookup.digest_domain("hk-identity", "A1234563")
    assert keys.identity_digest != lookup.digest("A1234563")
    assert keys.identity_digest != document_identity_keys(passport, lookup).identity_digest
    assert keys.passport_number_digest is keys.issuing_country is None


def test_strict_and_pending_hkid_intake_preserve_explicit_facts_and_guardian_type():
    guardian = {
        "full_name": "监护人",
        "document_type": "hk_id",
        "identity_number": "AB123456(9)",
        "phone": "13900000002",
        "relationship": "父亲",
        "sex": "male",
        "date_of_birth": "1980-01-01",
    }
    strict = normalize_payload(
        IntakeInput(
            kind="student",
            full_name="学员",
            document_type="hk_id",
            identity_number="A123456(3)",
            phone="13900000001",
            sex="female",
            date_of_birth="2015-01-01",
            guardians=[guardian],
        )
    )
    assert strict.identity_number == "A1234563"
    assert strict.document_type == "hk_id"
    assert strict.guardians[0].document_type == "hk_id"
    pending = normalize_pending_payload(
        PendingIntakeInput(
            kind="staff", full_name="职员", document_type="hk_id", identity_number="A123456(3)"
        )
    )
    assert pending.identity_number == "A1234563"
    assert pending.date_of_birth is pending.sex is None


def test_hkid_claim_proof_preserves_typed_guardian_and_fixed_issuer():
    from app.personnel_intake.claims.proof import submission_from_input
    from app.personnel_intake.claims.schemas import ClaimStartInput

    source = ClaimStartInput(
        full_name="申请人",
        identity_number="a123456(3)",
        email="a@example.com",
        document_type="hk_id",
        issuing_country="HK",
        completion={
            "guardians": [
                {
                    "full_name": "监护人",
                    "identity_number": "AB123456(9)",
                    "document_type": "hk_id",
                    "phone": "13900000002",
                    "sex": "male",
                    "date_of_birth": "1980-01-01",
                    "relationship": "父亲",
                }
            ]
        },
    )
    proof = submission_from_input(source)
    assert proof.identity_number == "A1234563"
    assert proof.issuing_country is None
    assert '"document_type":"hk_id"' in proof.canonical()
    assert proof.identity_keys(LookupDigest(bytes(range(32)))).passport_number_digest is None
    with pytest.raises(ValueError):
        ClaimStartInput(
            full_name="申请人",
            identity_number="A1234563",
            email="a@example.com",
            document_type="hk_id",
            issuing_country="US",
        )


def test_strict_hkid_snapshot_includes_metadata_but_cn_legacy_stays_unchanged():
    from uuid import uuid4

    from app.identity.models import Person
    from app.personnel_intake.guardian_records import person_facts
    from app.security.encryption import EnvelopeCipher

    cipher = EnvelopeCipher(bytes(range(32)))
    person = Person(
        id=uuid4(),
        full_name="测试",
        document_type="hk_id",
        intake_mode="strict",
        identity_ciphertext=cipher.encrypt("A1234563", field_name="identity_number"),
        identity_digest="a" * 64,
        sex="female",
        date_of_birth=date(2000, 1, 1),
    )
    facts = person_facts(person, cipher)
    assert facts["document_type"] == "hk_id"
    assert facts["document_issuing_country"] is None
    person.document_type = "cn_id"
    assert "document_type" not in person_facts(person, cipher)


def test_strict_hkid_reservation_uses_typed_digest():
    from app.personnel_intake.service import PersonnelIntakeService
    from app.security.encryption import EnvelopeCipher

    lookup = LookupDigest(bytes(range(32)))
    s = PersonnelIntakeService(None, cipher=EnvelopeCipher(bytes(range(32))), lookup=lookup)
    payload = normalize_payload(
        IntakeInput(
            kind="student",
            full_name="学员",
            document_type="hk_id",
            identity_number="A1234563",
            phone="13900000001",
            sex="female",
            date_of_birth="2000-01-01",
        )
    )
    assert s._digests(payload)[0] == lookup.digest_domain("hk-identity", "A1234563")


def test_hkid_claim_mask_never_reveals_all_but_one_character():
    from app.personnel_intake.claims.queries import mask_identity

    assert mask_identity("A1234563", document_type="hk_id") == "****63"


def test_ordinary_housing_export_labels_hkid_without_implying_passport():
    from datetime import UTC, datetime

    from app.housing.occupancy_export_service import OccupancyExportRow, row_values

    row = OccupancyExportRow(
        room_number="101",
        person_name="测试",
        sex=None,
        identity_number="A1234563",
        phone=None,
        check_in_at=datetime(2026, 1, 1, tzinfo=UTC),
        check_out_at=None,
        document_type="hk_id",
    )
    cells = row_values(row)
    assert len(cells) == 10
    assert "香港身份证" in cells[-1]
    assert "护照" not in cells[-1]


def test_cn_legacy_operation_fingerprint_survives_new_optional_metadata():
    import json

    from app.personnel_intake.approval_service import PersonnelApprovalService
    from app.personnel_intake.schemas import IntakePayloadView
    from app.security.encryption import EnvelopeCipher

    legacy = {
        "kind": "student",
        "full_name": "旧档案",
        "identity_number": "11010519491231002X",
        "phone": "13900000001",
        "email": None,
        "sex": "female",
        "date_of_birth": "1949-12-31",
        "guardians": [],
        "coach_person_ids": [],
        "team_id": None,
    }
    lookup = LookupDigest(bytes(range(32)))
    expected = lookup.digest_domain(
        "assisted-operation",
        json.dumps(
            {
                "action": "direct_create",
                "person_id": None,
                "expected_version": None,
                "payload": legacy,
                "reason": None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    s = PersonnelApprovalService(None, cipher=EnvelopeCipher(bytes(range(32))), lookup=lookup)
    assert s._fingerprint("direct_create", IntakePayloadView.model_validate(legacy)) == expected


def test_pre_hkid_signed_cn_snapshot_authenticates_without_rewriting_ciphertext():
    import json
    from uuid import UUID

    from app.identity.models import Person
    from app.personnel_intake.guardian_records import authenticated_approval_facts, person_facts
    from app.personnel_intake.models import AssistedApproval
    from app.security.encryption import EnvelopeCipher

    lookup = LookupDigest(bytes(range(32)))
    cipher = EnvelopeCipher(bytes(range(32)))
    identifier = UUID("00000000-0000-4000-8000-000000000001")
    legacy = {
        "person_id": str(identifier),
        "full_name": "旧人员",
        "identity_number": "11010519491231002X",
        "identity_digest": lookup.digest("11010519491231002X"),
        "sex": "female",
        "date_of_birth": "1949-12-31",
        "phone": None,
        "email": None,
        "deleted_at": None,
    }
    raw = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encrypted = cipher.encrypt(raw, field_name="assisted_person_snapshot")
    approval = AssistedApproval(
        person_id=identifier,
        guardian_relationship_ids=[],
        snapshot_ciphertext=encrypted,
        snapshot_digest=lookup.digest_domain("assisted-person-snapshot", raw),
        guardian_snapshot_ciphertext=cipher.encrypt("[]", field_name="assisted_guardian_snapshot"),
        guardian_snapshot_digest=lookup.digest_domain("assisted-guardian-snapshot", "[]"),
    )
    own, guardians = authenticated_approval_facts(approval, cipher=cipher, lookup=lookup)
    assert own == legacy and guardians == []
    person = Person(
        id=identifier,
        full_name="旧人员",
        identity_ciphertext=cipher.encrypt(legacy["identity_number"], field_name="identity_number"),
        identity_digest=legacy["identity_digest"],
        sex="female",
        date_of_birth=date(1949, 12, 31),
        document_type="cn_id",
        intake_mode="strict",
    )
    assert person_facts(person, cipher) == legacy
    assert approval.snapshot_ciphertext == encrypted
