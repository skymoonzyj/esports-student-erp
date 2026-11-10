"""Synthetic return-permit contracts; expectations are independent literal values."""

from datetime import UTC, date, datetime

import pytest

from app.identity import document_identity
from app.identity.document_identity import document_identity_keys, normalize_document_identity
from app.identity.normalization import IdentityValidationError
from app.personnel_intake.codec import normalize_payload, normalize_pending_payload
from app.personnel_intake.schemas import IntakeInput, PendingIntakeInput
from app.security.encryption import LookupDigest


def document(number, **fields):
    return normalize_document_identity(
        "hk_mo_permit",
        number,
        issuing_country=fields.get("country"),
        sex=fields.get("sex"),
        date_of_birth=fields.get("birth"),
        allow_pending=fields.get("pending", True),
    )


@pytest.mark.parametrize(
    "raw,want",
    [
        ("H87654321", "H87654321"),
        (" m1234567802 ", "M12345678"),
        ("h8765432100", "H87654321"),
        ("M00000000", "M00000000"),
    ],
)
def test_permit_normalizes_lifetime_number_without_inventing_facts(raw, want):
    value = document(raw)
    assert value.number == want
    assert value.sex is value.date_of_birth is value.issuing_country is None
    assert hasattr(document_identity, "normalize_hk_mo_permit")
    assert document_identity.normalize_hk_mo_permit(raw) == want


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "H1234567",
        "H123456789",
        "H12345678901X",
        "H123456789012",
        "A12345678",
        "HM12345678",
        "H 12345678",
        "H1234\t5678",
        "Ｈ１２３４５６７８",
        "H１２３４５６７８",
        "H12345678\x00",
        "M12345678(02)",
    ],
)
def test_permit_rejects_non_ascii_or_non_contract_shapes(raw):
    with pytest.raises(IdentityValidationError):
        document(raw)


@pytest.mark.parametrize("country", ["HK", "MO", "CN", "US", ""])
def test_permit_rejects_any_supplied_issuer(country):
    with pytest.raises(IdentityValidationError):
        document("H87654321", country=country)


def test_permit_requires_manual_strict_facts_and_domain_separates_digest():
    for fields in ({}, {"sex": "male"}, {"birth": date(2000, 1, 1)}):
        with pytest.raises(IdentityValidationError):
            document("H87654321", pending=False, **fields)
    assert (
        document("H87654321", pending=False, sex="female", birth=date(2000, 1, 1)).sex == "female"
    )
    lookup = LookupDigest(bytes(range(32)))
    keys = document_identity_keys(document("h8765432101"), lookup)
    assert keys.identity_digest == lookup.digest_domain("hk-mo-permit-identity", "H87654321")
    assert keys.identity_digest not in {
        lookup.digest("H87654321"),
        lookup.digest_domain("hk-identity", "H87654321"),
        lookup.digest_domain("passport-identity", '[null,"H87654321"]'),
    }
    assert keys.passport_number_digest is keys.issuing_country is None


def guardian(**changes):
    return {
        "full_name": "Synthetic guardian",
        "document_type": "hk_mo_permit",
        "identity_number": "m1234567802",
        "phone": "13900000002",
        "relationship": "父亲",
        "sex": "male",
        "date_of_birth": "1980-01-01",
    } | changes


def test_permit_strict_pending_and_guardian_preserve_manual_facts():
    strict = normalize_payload(
        IntakeInput(
            kind="student",
            full_name="Synthetic minor",
            document_type="hk_mo_permit",
            identity_number="h8765432100",
            phone="13900000001",
            sex="female",
            date_of_birth="2015-01-01",
            guardians=[guardian()],
        )
    )
    assert strict.identity_number == "H87654321"
    assert strict.guardians[0].identity_number == "M12345678"
    assert strict.guardians[0].document_type == "hk_mo_permit"
    assert strict.sex == "female" and strict.date_of_birth == date(2015, 1, 1)
    pending = normalize_pending_payload(
        PendingIntakeInput(
            kind="staff",
            full_name="Synthetic pending",
            document_type="hk_mo_permit",
            identity_number="H87654321",
        )
    )
    assert pending.sex is pending.date_of_birth is None
    with pytest.raises(ValueError):
        IntakeInput(
            kind="student",
            full_name="Synthetic",
            document_type="hk_mo_permit",
            identity_number="H87654321",
            phone="13900000001",
            sex="female",
            date_of_birth="2015-01-01",
            guardians=[guardian(sex=None)],
        )


def test_permit_claim_evidence_is_typed_and_rejects_issuer_metadata():
    from app.personnel_intake.claims.proof import submission_from_input
    from app.personnel_intake.claims.schemas import ClaimStartInput

    fields = {
        "full_name": "Synthetic applicant",
        "identity_number": "h8765432102",
        "email": "synthetic@example.com",
        "document_type": "hk_mo_permit",
    }
    proof = submission_from_input(ClaimStartInput(**fields, completion={"guardians": [guardian()]}))
    assert proof.identity_number == "H87654321" and proof.issuing_country is None
    assert '"document_type":"hk_mo_permit"' in proof.canonical()
    assert '"identity_number":"M12345678"' in proof.canonical()
    assert proof.identity_keys(LookupDigest(bytes(range(32)))).passport_number_digest is None
    for extra in (
        {"issuing_country": "HK"},
        {"completion": {"issuing_country": "MO"}},
        {"completion": {"guardians": [guardian(document_issuing_country="MO")]}},
    ):
        with pytest.raises(ValueError):
            ClaimStartInput(**fields, **extra)


def test_permit_mask_and_ordinary_housing_export_label():
    from app.housing.occupancy_export_service import OccupancyExportRow, row_values
    from app.personnel_intake.claims.queries import mask_identity

    assert mask_identity("H87654321", document_type="hk_mo_permit") == "****21"
    cells = row_values(
        OccupancyExportRow(
            room_number="101",
            person_name="Synthetic",
            sex=None,
            identity_number="H87654321",
            phone=None,
            check_in_at=datetime(2026, 1, 1, tzinfo=UTC),
            check_out_at=None,
            document_type="hk_mo_permit",
        )
    )
    assert len(cells) == 10
    assert "回乡证（港澳居民来往内地通行证）" in cells[-1]
    assert "护照" not in cells[-1]


@pytest.mark.parametrize("guardians", [[], [guardian(date_of_birth="2016-01-01")]])
def test_permit_minor_requires_an_adult_guardian(guardians):
    from app.personnel_intake.errors import PersonnelIntakeError

    with pytest.raises(PersonnelIntakeError):
        normalize_payload(
            IntakeInput(
                kind="student",
                full_name="Synthetic minor",
                document_type="hk_mo_permit",
                identity_number="H87654321",
                phone="13900000001",
                sex="female",
                date_of_birth="2015-01-01",
                guardians=guardians,
            )
        )


@pytest.mark.parametrize("invalid_guardian", [False, True])
def test_permit_invalid_api_input_does_not_echo_the_document_or_log_it(caplog, invalid_guardian):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.errors import install_exception_handlers
    from app.personnel_intake.claims.schemas import ClaimStartInput

    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/probe")
    def probe(body: ClaimStartInput):
        return {"accepted": True}

    raw = "Ｈ８７６５４３２１"
    with TestClient(app) as client:
        data = {
            "full_name": "Synthetic",
            "identity_number": "H87654321" if invalid_guardian else raw,
            "document_type": "hk_mo_permit",
            "email": "synthetic@example.com",
        }
        if invalid_guardian:
            data["completion"] = {"guardians": [guardian(identity_number=raw)]}
        response = client.post("/probe", json=data)
    assert response.status_code == 422
    assert raw not in response.text and raw not in caplog.text
